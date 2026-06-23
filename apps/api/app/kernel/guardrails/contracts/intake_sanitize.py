"""E4-2 案情录入端脱敏纯函数（本地脱敏 + 要素抽取 -> SearchProfile）。

本模块只做一件事：把「原始口语化案情」转成已脱敏、结构化、字段白名单的
SearchProfile 载荷，并提供后端防御性第二道闸。**纯函数，零 I/O、零网络、零持久化、
零运行时依赖（只依赖同组 whitelist / intake_contract 纯逻辑）**，不接任何端点 / 页面，
不依赖 ENABLE_INTAKE_AI_EXTRACTION 的 on 路径（该子开关本步仍 off、不接线）。

设计口径（与前端 apps/web/src/intake/sanitize.ts 逐规则一致）：
- 脱敏 = 移除 / 占位，不是「截断后保留」：识别到 PII token 即整体替换为占位符
  （[姓名]/[身份证]/[手机号]/...），绝不把原始 PII 子串保留进任何返回值。
- 结构化抽取尽力而为（规则 / 关键词，不依赖服务端 LLM）：case_cause / region /
  trial_level_preference / dispute_focus_keywords。
- query_text 由「已脱敏案情文本」拼成的短查询，必须已脱敏。
- fail-closed：宁可少抽一个要素，也不放行未脱敏内容。

后端防御层（第二道闸）：
- sanitize_intake_profile_payload：复用 E4-1 assert_no_raw_case_payload + 键级白名单，
  再对保留值做值级脱敏。出现 raw_case/raw_query/name/id_card/phone/address/email 等
  PII / 正文型键即抛错，异常消息只暴露键名、绝不回显键值。
- 红线：原始案情零上送是录入端最强约束；后端即便前端漏脱敏也必须拒绝 / 移除。
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from .intake_contract import (
    INTAKE_SEARCH_PROFILE_FIELDS,
    assert_no_raw_case_payload,
    sanitize_intake_search_profile,
)
from .whitelist import SEARCH_PROFILE_FIELDS

# --- 脱敏占位符（与前端逐字一致）-------------------------------------------------

PLACEHOLDER_NAME = "[姓名]"
PLACEHOLDER_ID_CARD = "[身份证]"
PLACEHOLDER_USCC = "[统一社会信用代码]"
PLACEHOLDER_PHONE = "[手机号]"
PLACEHOLDER_BANK_CARD = "[银行卡号]"
PLACEHOLDER_EMAIL = "[邮箱]"
PLACEHOLDER_PLATE = "[车牌]"
PLACEHOLDER_ADDRESS = "[住址]"

# query_text 最大长度（脱敏后短查询，避免承载长正文）。
QUERY_TEXT_MAX_LEN = 280

# 当事人角色标签：用于「标签 + 姓名」结构化定位姓名（保留标签、占位姓名）。
_PARTY_ROLE_LABELS = (
    "原告",
    "被告",
    "上诉人",
    "被上诉人",
    "申请人",
    "被申请人",
    "申请执行人",
    "被执行人",
    "第三人",
    "当事人",
    "委托诉讼代理人",
    "代理人",
    "罪犯",
    "犯罪嫌疑人",
)

# --- PII 识别正则（顺序敏感：先邮箱 / 统一社会信用代码，再证件 / 卡号 / 手机号）-----
# 说明：用显式 lookaround 而非 \b，避免 JS / Python 在中文边界上的差异。

# 邮箱
_RE_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# 统一社会信用代码：18 位字母数字，且至少含一个大写字母（与纯数字身份证区分）。
_RE_USCC = re.compile(r"(?<![A-Za-z0-9])(?=[0-9A-Z]*[A-Z])[0-9A-Z]{18}(?![A-Za-z0-9])")

# 居民身份证：18 位，末位可为 X / x。
_RE_ID_CARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")

# 银行卡号：16-19 位纯数字。
_RE_BANK_CARD = re.compile(r"(?<!\d)\d{16,19}(?!\d)")

# 手机号：1 开头 + 第二位 3-9 + 共 11 位。
_RE_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")

# 车牌：省份简称 + 字母 + 5 位字母数字（含新能源 6 位）。
_RE_PLATE = re.compile(
    r"[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼]"
    r"[A-Z][A-HJ-NP-Z0-9]{4,5}"
)

# 住址：省/市/区/县 ... 路/街/号/室/栋/单元 等街道级要素。
_RE_ADDRESS = re.compile(
    r"[一-龥]{2,}(?:省|自治区)?[一-龥]{0,8}(?:市|自治州)?"
    r"[一-龥]{0,8}(?:区|县)"
    r"[一-龥0-9]{0,20}?(?:路|街|道|巷|村|号|室|栋|幢|单元|楼)"
    r"[0-9A-Za-z\-]*(?:号|室|栋|幢|单元|楼)?"
)

# 姓名捕获边界：姓名通常 2-3 字。难点是判定姓名在哪结束（中文连接词本身也是中文）。
# 规则：优先匹配 3 字姓名，但仅当其后紧跟「边界」（标点 / 空白 / 数字 / 拉丁字母 /
# 连接词 / 句尾）；否则回退到 2 字姓名。这样既能覆盖 3 字姓名，又不会把「张三与」「李四买」
# 这类「姓名+连接词/案由用字」吞进来。不依赖姓氏黑名单（避免与「主张」等用字冲突）。
# 宁可少抽一个字，也不把连接词当姓名（fail-closed 由后端值级脱敏 + 键级红线兜底）。
_NAME_BOUNDARY = r"(?=[，。、；：,.;:!?！？\s0-9A-Za-z与和及的在系于至向]|$)"
# 2-3 位中文姓名（3 字需有右边界，否则取 2 字）。
_NAME_BODY = r"(?:[一-龥]{3}" + _NAME_BOUNDARY + r"|[一-龥]{2})"
# 姓名：角色标签 + 姓名（保留标签，占位姓名）。
_RE_NAME_LABELED = re.compile(
    r"(" + "|".join(_PARTY_ROLE_LABELS) + r")"
    r"(?:是|为|：|:)?"
    r"(" + _NAME_BODY + r")"
)
# 姓名：「姓名/名字 是/为/： X」。
_RE_NAME_KEYED = re.compile(r"(姓名|名字)(?:是|为|：|:)(" + _NAME_BODY + r")")


# --- 结构化抽取词表（与前端逐项一致）-------------------------------------------

# 常见民商事案由（出现即直接命中，优先于通用「XX纠纷」正则）。
_KNOWN_CASE_CAUSES = (
    "买卖合同纠纷",
    "借款合同纠纷",
    "民间借贷纠纷",
    "金融借款合同纠纷",
    "房屋租赁合同纠纷",
    "租赁合同纠纷",
    "劳动合同纠纷",
    "劳务合同纠纷",
    "建设工程施工合同纠纷",
    "服务合同纠纷",
    "股权转让纠纷",
    "股东出资纠纷",
    "婚姻家庭纠纷",
    "离婚纠纷",
    "继承纠纷",
    "机动车交通事故责任纠纷",
    "侵权责任纠纷",
    "名誉权纠纷",
    "知识产权权属纠纷",
    "著作权权属、侵权纠纷",
    "商标权纠纷",
    "不当得利纠纷",
    "合同纠纷",
)

# 通用案由兜底：以「罪」结尾的罪名、或「……纠纷 / ……合同纠纷」。
_RE_CHARGE = re.compile(r"[一-龥]{2,10}罪")
_RE_GENERIC_DISPUTE = re.compile(r"[一-龥]{2,12}纠纷")

# 罪名前缀（控告 / 指控类动词），抽取后剥离以得到干净罪名（与前端一致）。
_CHARGE_PREFIXES = ("涉嫌", "被控", "指控", "控告", "构成", "涉", "犯")

# 地域（省 / 自治区 / 直辖市，按列表顺序取首个命中）。
_REGIONS = (
    "北京",
    "天津",
    "上海",
    "重庆",
    "河北",
    "山西",
    "内蒙古",
    "辽宁",
    "吉林",
    "黑龙江",
    "江苏",
    "浙江",
    "安徽",
    "福建",
    "江西",
    "山东",
    "河南",
    "湖北",
    "湖南",
    "广东",
    "广西",
    "海南",
    "四川",
    "贵州",
    "云南",
    "西藏",
    "陕西",
    "甘肃",
    "青海",
    "宁夏",
    "新疆",
    "香港",
    "澳门",
    "台湾",
)

# 审级倾向（再审 > 二审 > 一审，按优先级取首个命中）。
_TRIAL_LEVELS = ("再审", "二审", "一审")
_TRIAL_LEVEL_ALIASES: dict[str, str] = {
    "再审": "再审",
    "重审": "再审",
    "二审": "二审",
    "上诉": "二审",
    "终审": "二审",
    "一审": "一审",
    "初审": "一审",
    "起诉": "一审",
}

# 争议焦点关键词词典（命中即收集，去重 + 截断）。
_DISPUTE_KEYWORDS = (
    "合同效力",
    "违约责任",
    "违约金",
    "解除合同",
    "合同解除",
    "履行期限",
    "付款义务",
    "货款",
    "欠款",
    "利息",
    "逾期利息",
    "担保",
    "抵押",
    "质押",
    "保证责任",
    "赔偿责任",
    "损害赔偿",
    "赔偿金",
    "证据不足",
    "举证责任",
    "诉讼时效",
    "管辖权",
    "责任划分",
    "过错责任",
    "工伤",
    "工资",
    "经济补偿",
    "抚养权",
    "抚养费",
    "财产分割",
    "继承份额",
    "侵权",
    "名誉",
    "知识产权",
    "股权",
    "出资",
    "不当得利",
)

# 抽取出的关键词上限。
_MAX_DISPUTE_KEYWORDS = 8


# --- 脱敏（移除 / 占位）---------------------------------------------------------

def redact_pii(text: str) -> str:
    """把文本中的 PII 整体替换为占位符（纯函数，不保留原始 PII 子串）。

    顺序敏感：先邮箱 / 统一社会信用代码，再身份证 / 银行卡 / 手机号，最后车牌 /
    住址 / 姓名。姓名一旦经「角色标签 / 关键词」识别出，其在全文的所有出现（含无
    标签的复述）都被占位，避免独立复述的姓名残留。返回值绝不含被识别的 PII token。
    """
    if not text:
        return ""

    # 先从原始文本收集「被角色标签 / 关键词标识」的姓名 token（用于全文占位）。
    named_tokens: list[str] = []
    for m in _RE_NAME_LABELED.finditer(text):
        if m.group(2):
            named_tokens.append(m.group(2))
    for m in _RE_NAME_KEYED.finditer(text):
        if m.group(2):
            named_tokens.append(m.group(2))

    redacted = text
    redacted = _RE_EMAIL.sub(PLACEHOLDER_EMAIL, redacted)
    redacted = _RE_USCC.sub(PLACEHOLDER_USCC, redacted)
    redacted = _RE_ID_CARD.sub(PLACEHOLDER_ID_CARD, redacted)
    redacted = _RE_BANK_CARD.sub(PLACEHOLDER_BANK_CARD, redacted)
    redacted = _RE_PHONE.sub(PLACEHOLDER_PHONE, redacted)
    redacted = _RE_PLATE.sub(PLACEHOLDER_PLATE, redacted)
    redacted = _RE_ADDRESS.sub(PLACEHOLDER_ADDRESS, redacted)
    # 姓名：保留角色标签 / 关键词，占位姓名本身。
    redacted = _RE_NAME_LABELED.sub(lambda m: f"{m.group(1)}{PLACEHOLDER_NAME}", redacted)
    redacted = _RE_NAME_KEYED.sub(lambda m: f"{m.group(1)}：{PLACEHOLDER_NAME}", redacted)
    # 已识别姓名的全文复述（无标签出现）一并占位（按长度降序，先长后短）。
    for token in sorted(set(named_tokens), key=len, reverse=True):
        if token and token not in ("姓名", "名字"):
            redacted = redacted.replace(token, PLACEHOLDER_NAME)
    return redacted


# --- 结构化抽取（尽力而为）------------------------------------------------------

def extract_case_cause(text: str) -> str | None:
    """抽取案由：先匹配已知案由表，再兜底「XX罪 / XX纠纷」。"""
    if not text:
        return None
    for cause in _KNOWN_CASE_CAUSES:
        if cause in text:
            return cause
    m = _RE_CHARGE.search(text)
    if m:
        charge = m.group(0)
        for pref in _CHARGE_PREFIXES:
            if charge.startswith(pref) and len(charge) - len(pref) >= 2:
                charge = charge[len(pref):]
                break
        return charge
    m = _RE_GENERIC_DISPUTE.search(text)
    if m:
        return m.group(0)
    return None


def extract_region(text: str) -> str | None:
    """抽取地域：取文本中最早出现的省 / 直辖市；并列时按列表顺序优先。"""
    if not text:
        return None
    best_pos = -1
    best_region: str | None = None
    for region in _REGIONS:
        pos = text.find(region)
        if pos != -1 and (best_pos == -1 or pos < best_pos):
            best_pos = pos
            best_region = region
    return best_region


def extract_trial_level_preference(text: str) -> str | None:
    """抽取审级倾向：再审 > 二审 > 一审（含别名归一）。"""
    if not text:
        return None
    for level in _TRIAL_LEVELS:
        for alias, canonical in _TRIAL_LEVEL_ALIASES.items():
            if canonical == level and alias in text:
                return level
    return None


def extract_dispute_focus_keywords(text: str) -> list[str]:
    """抽取争议焦点关键词：命中词典即收集，去重保序 + 截断上限。"""
    if not text:
        return []
    found: list[str] = []
    for kw in _DISPUTE_KEYWORDS:
        if kw in text and kw not in found:
            found.append(kw)
        if len(found) >= _MAX_DISPUTE_KEYWORDS:
            break
    return found


def _build_query_text(redacted: str, profile: Mapping[str, Any]) -> str:
    """由已脱敏文本拼成短查询；过短时用结构化要素兜底。返回值必属已脱敏。"""
    normalized = re.sub(r"\s+", " ", redacted or "").strip()
    if len(normalized) > QUERY_TEXT_MAX_LEN:
        normalized = normalized[:QUERY_TEXT_MAX_LEN]
    if normalized:
        return normalized
    # 兜底：用结构化要素（均已脱敏 / 词表来源）拼最短查询。
    parts: list[str] = []
    for key in ("case_cause", "region", "trial_level_preference"):
        val = profile.get(key)
        if val:
            parts.append(str(val))
    kws = profile.get("dispute_focus_keywords") or []
    parts.extend(str(k) for k in kws)
    return " ".join(parts).strip()


def build_search_profile_from_raw(raw_case_text: str) -> dict[str, Any]:
    """核心入口：原始案情文本 -> 已脱敏的 SearchProfile 白名单载荷（纯函数）。

    流程（fail-closed）：
    1. 先从原始文本做结构化抽取（case_cause/region/trial_level/keywords）——这些是
       受控词表 / 罪名 / 纠纷模式来源，不承载自由 PII。
    2. 对原始文本整体脱敏（redact_pii）得到已脱敏案情文本。
    3. query_text 只由已脱敏文本（或结构化要素）拼成。
    4. 对所有字符串型输出再跑一次 redact_pii，确保 0 PII 残留（双保险）。
    5. 输出严格 = SearchProfile 白名单五字段，其余一律不产生。

    **绝不**把原始案情 / PII 写入返回值；返回的 query_text / case_cause 等均已脱敏。
    """
    text = raw_case_text or ""

    case_cause = extract_case_cause(text)
    region = extract_region(text)
    trial_level = extract_trial_level_preference(text)
    keywords = extract_dispute_focus_keywords(text)

    redacted = redact_pii(text)

    profile: dict[str, Any] = {
        "case_cause": case_cause,
        "region": region,
        "trial_level_preference": trial_level,
        "dispute_focus_keywords": keywords,
    }
    profile["query_text"] = _build_query_text(redacted, profile)

    # 双保险：对所有字符串输出再脱敏一次，确保结构化字段也 0 PII 残留。
    for key in ("case_cause", "region", "trial_level_preference", "query_text"):
        if isinstance(profile.get(key), str) and profile[key]:
            profile[key] = redact_pii(profile[key])
    profile["dispute_focus_keywords"] = [
        redact_pii(k) if isinstance(k, str) else k
        for k in profile["dispute_focus_keywords"]
    ]

    # 只保留白名单五字段（其余一律丢弃）；None / 空值保留为白名单内字段。
    return {k: v for k, v in profile.items() if k in SEARCH_PROFILE_FIELDS}


# --- 后端防御性第二道闸 ---------------------------------------------------------

def sanitize_intake_profile_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """后端 intake 入口防御层：对「已是结构化白名单」的 payload 做 fail-closed 校验。

    1. sanitize_intake_search_profile（内含 assert_no_raw_case_payload）：出现任何正文型键
       （raw_case/raw_query/full_text/...）或 PII 型键（name/id_card/phone/address/email/...）
       立即抛 ContractViolationError，异常消息只暴露键名、绝不回显键值；再做键级白名单
       （只保留 SearchProfile 五字段）。
    2. 值级防御：对保留下来的字符串值再跑一次 redact_pii——即便键名合法，若值里仍
       夹带可识别 PII（前端漏脱敏），也在此移除 / 占位，绝不放行未脱敏内容。

    本函数是纯函数：不写库、不发请求、不记日志。
    """
    # 第一道：键级红线（正文型 / PII 型键 fail-closed）。
    cleaned = sanitize_intake_search_profile(payload)

    # 第二道：值级脱敏（字符串字段移除 / 占位残留 PII）。
    result: dict[str, Any] = {}
    for key, value in cleaned.items():
        if isinstance(value, str):
            result[key] = redact_pii(value)
        elif key == "dispute_focus_keywords" and isinstance(value, (list, tuple)):
            result[key] = [redact_pii(v) if isinstance(v, str) else v for v in value]
        else:
            result[key] = value
    return result


__all__ = [
    # 占位符
    "PLACEHOLDER_NAME",
    "PLACEHOLDER_ID_CARD",
    "PLACEHOLDER_USCC",
    "PLACEHOLDER_PHONE",
    "PLACEHOLDER_BANK_CARD",
    "PLACEHOLDER_EMAIL",
    "PLACEHOLDER_PLATE",
    "PLACEHOLDER_ADDRESS",
    "QUERY_TEXT_MAX_LEN",
    # 脱敏 + 抽取纯函数
    "redact_pii",
    "extract_case_cause",
    "extract_region",
    "extract_trial_level_preference",
    "extract_dispute_focus_keywords",
    "build_search_profile_from_raw",
    # 后端防御层
    "sanitize_intake_profile_payload",
]
