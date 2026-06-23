/**
 * E4-2 案情录入端脱敏纯函数（本地脱敏 + 要素抽取 -> SearchProfile）。
 *
 * 纯 TS，无任何网络调用 / 无浏览器存储 / 无副作用。规则口径与后端
 * apps/api/app/kernel/guardrails/contracts/intake_sanitize.py 逐条一致：
 * - 脱敏 = 移除 / 占位，不是「截断后保留」：识别到 PII 即整体替换为占位符。
 * - 结构化抽取尽力而为（规则 / 关键词，不依赖服务端 LLM）。
 * - query_text 只由已脱敏文本（或结构化要素）拼成，必属已脱敏。
 * - fail-closed：宁可少抽一个要素，也不放行未脱敏内容。
 *
 * 原始口语化案情 / PII 只在浏览器内存；本模块输出严格 = SearchProfile 白名单五字段。
 * 本步不接任何 UI / 端点（E4-3/E4-4 才做）；不依赖 AI 增强子开关 on 路径。
 */

// --- SearchProfile 白名单五字段（E-1 冻结，不得增删）---
export const SEARCH_PROFILE_FIELDS = [
  "case_cause",
  "region",
  "trial_level_preference",
  "dispute_focus_keywords",
  "query_text",
] as const;

export interface SearchProfileDraft {
  case_cause: string | null;
  region: string | null;
  trial_level_preference: string | null;
  dispute_focus_keywords: string[];
  query_text: string;
}

// --- 脱敏占位符（与后端逐字一致）---
export const PLACEHOLDER_NAME = "[姓名]";
export const PLACEHOLDER_ID_CARD = "[身份证]";
export const PLACEHOLDER_USCC = "[统一社会信用代码]";
export const PLACEHOLDER_PHONE = "[手机号]";
export const PLACEHOLDER_BANK_CARD = "[银行卡号]";
export const PLACEHOLDER_EMAIL = "[邮箱]";
export const PLACEHOLDER_PLATE = "[车牌]";
export const PLACEHOLDER_ADDRESS = "[住址]";

// query_text 最大长度（脱敏后短查询，避免承载长正文）。
export const QUERY_TEXT_MAX_LEN = 280;

// 当事人角色标签：用于「标签 + 姓名」结构化定位姓名（保留标签、占位姓名）。
const PARTY_ROLE_LABELS = [
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
];

// --- PII 识别正则（顺序敏感：先邮箱 / 统一社会信用代码，再证件 / 卡号 / 手机号）---
// 用显式 lookaround 而非 \b，规避中文边界差异（与后端一致）。
const RE_EMAIL = /[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}/g;
// 统一社会信用代码：18 位字母数字，且至少含一个大写字母。
const RE_USCC = /(?<![A-Za-z0-9])(?=[0-9A-Z]*[A-Z])[0-9A-Z]{18}(?![A-Za-z0-9])/g;
// 居民身份证：18 位，末位可为 X / x。
const RE_ID_CARD = /(?<!\d)\d{17}[\dXx](?!\d)/g;
// 银行卡号：16-19 位纯数字。
const RE_BANK_CARD = /(?<!\d)\d{16,19}(?!\d)/g;
// 手机号：1 开头 + 第二位 3-9 + 共 11 位。
const RE_PHONE = /(?<!\d)1[3-9]\d{9}(?!\d)/g;
// 车牌：省份简称 + 字母 + 5 位字母数字（含新能源 6 位）。
const RE_PLATE =
  /[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼][A-Z][A-HJ-NP-Z0-9]{4,5}/g;
// 住址：省/市/区/县 ... 路/街/号/室/栋/单元 等街道级要素。
const RE_ADDRESS =
  /[一-龥]{2,}(?:省|自治区)?[一-龥]{0,8}(?:市|自治州)?[一-龥]{0,8}(?:区|县)[一-龥0-9]{0,20}?(?:路|街|道|巷|村|号|室|栋|幢|单元|楼)[0-9A-Za-z\-]*(?:号|室|栋|幢|单元|楼)?/g;
// 姓名捕获边界（与后端逐条一致）：优先匹配 3 字姓名，但仅当其后紧跟「边界」（标点 /
// 空白 / 数字 / 拉丁字母 / 连接词 / 句尾）；否则回退到 2 字姓名。避免把「张三与」
// 「李四买」这类「姓名+连接词/案由用字」吞进来；不依赖姓氏黑名单。
const NAME_BOUNDARY = "(?=[，。、；：,.;:!?！？\\s0-9A-Za-z与和及的在系于至向]|$)";
const NAME_BODY = "(?:[\\u4e00-\\u9fa5]{3}" + NAME_BOUNDARY + "|[\\u4e00-\\u9fa5]{2})";
// 姓名：角色标签 + 姓名（保留标签，占位姓名）。
const RE_NAME_LABELED = new RegExp(
  "(" + PARTY_ROLE_LABELS.join("|") + ")(?:是|为|：|:)?(" + NAME_BODY + ")",
  "g",
);
// 姓名：「姓名/名字 是/为/： X」。
const RE_NAME_KEYED = new RegExp(
  "(姓名|名字)(?:是|为|：|:)(" + NAME_BODY + ")",
  "g",
);

// --- 结构化抽取词表（与后端逐项一致）---
const KNOWN_CASE_CAUSES = [
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
];
const RE_CHARGE = /[一-龥]{2,10}罪/;
const RE_GENERIC_DISPUTE = /[一-龥]{2,12}纠纷/;
// 罪名前缀（控告 / 指控类动词），抽取后剥离以得到干净罪名（与后端一致）。
const CHARGE_PREFIXES = ["涉嫌", "被控", "指控", "控告", "构成", "涉", "犯"];

const REGIONS = [
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
];

const TRIAL_LEVELS = ["再审", "二审", "一审"] as const;
const TRIAL_LEVEL_ALIASES: Record<string, string> = {
  再审: "再审",
  重审: "再审",
  二审: "二审",
  上诉: "二审",
  终审: "二审",
  一审: "一审",
  初审: "一审",
  起诉: "一审",
};

const DISPUTE_KEYWORDS = [
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
];
const MAX_DISPUTE_KEYWORDS = 8;

// --- 脱敏（移除 / 占位）---
export function redactPII(text: string): string {
  if (!text) {
    return "";
  }
  // 先从原始文本收集「被角色标签 / 关键词标识」的姓名 token（用于全文占位）。
  const namedTokens: string[] = [];
  for (const m of text.matchAll(RE_NAME_LABELED)) {
    if (m[2]) {
      namedTokens.push(m[2]);
    }
  }
  for (const m of text.matchAll(RE_NAME_KEYED)) {
    if (m[2]) {
      namedTokens.push(m[2]);
    }
  }

  let redacted = text;
  redacted = redacted.replace(RE_EMAIL, PLACEHOLDER_EMAIL);
  redacted = redacted.replace(RE_USCC, PLACEHOLDER_USCC);
  redacted = redacted.replace(RE_ID_CARD, PLACEHOLDER_ID_CARD);
  redacted = redacted.replace(RE_BANK_CARD, PLACEHOLDER_BANK_CARD);
  redacted = redacted.replace(RE_PHONE, PLACEHOLDER_PHONE);
  redacted = redacted.replace(RE_PLATE, PLACEHOLDER_PLATE);
  redacted = redacted.replace(RE_ADDRESS, PLACEHOLDER_ADDRESS);
  redacted = redacted.replace(
    RE_NAME_LABELED,
    (_m, label: string) => `${label}${PLACEHOLDER_NAME}`,
  );
  redacted = redacted.replace(
    RE_NAME_KEYED,
    (_m, key: string) => `${key}：${PLACEHOLDER_NAME}`,
  );
  // 已识别姓名的全文复述（无标签出现）一并占位（按长度降序，先长后短）。
  const unique = Array.from(new Set(namedTokens)).sort((a, b) => b.length - a.length);
  for (const token of unique) {
    if (token && token !== "姓名" && token !== "名字") {
      redacted = redacted.split(token).join(PLACEHOLDER_NAME);
    }
  }
  return redacted;
}

// --- 结构化抽取（尽力而为）---
export function extractCaseCause(text: string): string | null {
  if (!text) {
    return null;
  }
  for (const cause of KNOWN_CASE_CAUSES) {
    if (text.includes(cause)) {
      return cause;
    }
  }
  const charge = text.match(RE_CHARGE);
  if (charge) {
    let name = charge[0];
    for (const pref of CHARGE_PREFIXES) {
      if (name.startsWith(pref) && name.length - pref.length >= 2) {
        name = name.slice(pref.length);
        break;
      }
    }
    return name;
  }
  const dispute = text.match(RE_GENERIC_DISPUTE);
  if (dispute) {
    return dispute[0];
  }
  return null;
}

export function extractRegion(text: string): string | null {
  if (!text) {
    return null;
  }
  let bestPos = -1;
  let bestRegion: string | null = null;
  for (const region of REGIONS) {
    const pos = text.indexOf(region);
    if (pos !== -1 && (bestPos === -1 || pos < bestPos)) {
      bestPos = pos;
      bestRegion = region;
    }
  }
  return bestRegion;
}

export function extractTrialLevelPreference(text: string): string | null {
  if (!text) {
    return null;
  }
  for (const level of TRIAL_LEVELS) {
    for (const [alias, canonical] of Object.entries(TRIAL_LEVEL_ALIASES)) {
      if (canonical === level && text.includes(alias)) {
        return level;
      }
    }
  }
  return null;
}

export function extractDisputeFocusKeywords(text: string): string[] {
  if (!text) {
    return [];
  }
  const found: string[] = [];
  for (const kw of DISPUTE_KEYWORDS) {
    if (text.includes(kw) && !found.includes(kw)) {
      found.push(kw);
    }
    if (found.length >= MAX_DISPUTE_KEYWORDS) {
      break;
    }
  }
  return found;
}

function buildQueryText(
  redacted: string,
  profile: Omit<SearchProfileDraft, "query_text">,
): string {
  let normalized = (redacted || "").replace(/\s+/g, " ").trim();
  if (normalized.length > QUERY_TEXT_MAX_LEN) {
    normalized = normalized.slice(0, QUERY_TEXT_MAX_LEN);
  }
  if (normalized) {
    return normalized;
  }
  const parts: string[] = [];
  for (const key of ["case_cause", "region", "trial_level_preference"] as const) {
    const val = profile[key];
    if (val) {
      parts.push(String(val));
    }
  }
  for (const kw of profile.dispute_focus_keywords) {
    parts.push(String(kw));
  }
  return parts.join(" ").trim();
}

/**
 * 核心入口：原始案情文本 -> 已脱敏的 SearchProfile 白名单载荷（纯函数）。
 * 流程与后端 build_search_profile_from_raw 一致：先抽取、再整体脱敏、query_text 仅由
 * 已脱敏文本拼成、对所有字符串输出再脱敏一次（双保险），输出严格白名单五字段。
 */
export function buildSearchProfileFromRaw(rawCaseText: string): SearchProfileDraft {
  const text = rawCaseText || "";

  const caseCause = extractCaseCause(text);
  const region = extractRegion(text);
  const trialLevel = extractTrialLevelPreference(text);
  const keywords = extractDisputeFocusKeywords(text);

  const redacted = redactPII(text);

  const base: Omit<SearchProfileDraft, "query_text"> = {
    case_cause: caseCause,
    region,
    trial_level_preference: trialLevel,
    dispute_focus_keywords: keywords,
  };
  const queryText = buildQueryText(redacted, base);

  // 双保险：对所有字符串输出再脱敏一次，确保结构化字段也 0 PII 残留。
  return {
    case_cause: caseCause ? redactPII(caseCause) : null,
    region: region ? redactPII(region) : null,
    trial_level_preference: trialLevel ? redactPII(trialLevel) : null,
    dispute_focus_keywords: keywords.map((k) => redactPII(k)),
    query_text: redactPII(queryText),
  };
}
