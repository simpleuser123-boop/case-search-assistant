"""M5-6 导入项校验与净化：白名单过滤 + 锚点完整性 + 去重判定。

红线（纯逻辑，不 import 检索/rerank/持久层，不触碰主排序）：
- 字段白名单：只保留 M4 元数据全量 + 来源锚点 + 用户自填短字段；
  任何非白名单键（含潜在正文/凭据键）一律**丢弃**，绝不进入导入对象。
- 锚点完整性：沿用 case_id + source_chunk_id；缺锚点/非法锚点的承载型项被拒绝，
  绝不伪造锚点。纯引用型（收藏）可不带锚点，但带了就必须合法。
- 去重：按 case_id 去重（批内重复 + 调用方传入的已存在 case_id 集合）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 导入项允许的白名单键（与 M5-3 SEDIMENT_WRITE_ALLOWED_KEYS 对齐，去掉 object_type 单列处理）。
IMPORT_ITEM_ALLOWED_KEYS = frozenset(
    {
        "case_id",
        "case_number",
        "court",
        "trial_level",
        "case_cause",
        "judgment_date",
        "source_anchors",
        "note",
        "tag",
        "label",
        "list_id",
        "list_title",
        "report_id",
    }
)

# 明确的正文/凭据黑名单：即便白名单被误扩，这些键也必被识别为「含正文」拒绝信号。
# （与 M5-3 SEDIMENT_FORBIDDEN_KEYS 对齐。）
IMPORT_ITEM_FORBIDDEN_KEYS = frozenset(
    {
        "raw_query",
        "query",
        "case_fact_body",
        "candidate_body",
        "chunk_body",
        "judgment_long_text",
        "summary_body",
        "holding_body",
        "compare_body",
        "user_free_long_text",
        "text",
        "content",
        "password",
        "token",
        "session_token",
    }
)

# 承载案例侧 AI 内容的导入对象类型：必须至少有一条合法来源锚点（无锚点即拒绝）。
AI_CONTENT_OBJECT_TYPES = frozenset({"case_list", "report_template"})

# 短字段长度上限（与 M5-3 模型一致，防止自由长文本经短字段混入）。
_NOTE_MAX = 200
_SHORT_MAX = 120
_TINY_MAX = 80
_TRIAL_MAX = 40

REASON_OK = "ok"
REASON_FORBIDDEN_BODY = "forbidden_body_field"
REASON_MISSING_ANCHOR = "missing_source_anchor"
REASON_INVALID_ANCHOR = "invalid_source_anchor"
REASON_MISSING_CASE_ID = "missing_case_id"
REASON_DUPLICATE = "duplicate_case_id"
REASON_UNKNOWN_TYPE = "unknown_object_type"


def is_valid_anchor(anchor: object) -> bool:
    """单条锚点是否合法：必须是 dict 且 case_id / source_chunk_id 均为非空字符串。"""
    if not isinstance(anchor, dict):
        return False
    case_id = anchor.get("case_id")
    chunk_id = anchor.get("source_chunk_id")
    return bool(case_id) and isinstance(case_id, str) and bool(chunk_id) and isinstance(chunk_id, str)


def sanitize_anchors(anchors: object) -> list[dict]:
    """只保留合法锚点，且每条锚点只取 case_id / source_chunk_id / anchor_type 三个键。

    任何非法锚点被丢弃（不伪造、不补全）；锚点内容里不携带正文。
    """
    if not isinstance(anchors, list):
        return []
    clean: list[dict] = []
    for anchor in anchors:
        if not is_valid_anchor(anchor):
            continue
        item = {
            "case_id": anchor["case_id"],
            "source_chunk_id": anchor["source_chunk_id"],
        }
        atype = anchor.get("anchor_type")
        if isinstance(atype, str) and atype:
            item["anchor_type"] = atype[:40]
        clean.append(item)
    return clean


def _truncate(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] if value else None


@dataclass
class ItemValidationResult:
    ok: bool
    reason_code: str = REASON_OK
    clean_payload: dict = field(default_factory=dict)
    case_id: str | None = None


def validate_and_clean_item(
    *,
    object_type: str,
    raw_item: dict,
) -> ItemValidationResult:
    """校验并净化单个导入项。

    步骤：
    1. 黑名单命中（含正文/凭据键）→ 拒绝（reason=forbidden_body_field）。
    2. 仅保留白名单键，丢弃其余所有键（含未知键 / 潜在正文）。
    3. 短字段截断；锚点净化（只留合法锚点的 case_id+chunk_id+anchor_type）。
    4. 必须有 case_id（导入对象的引用主键）。
    5. AI 内容承载型（case_list/report_template）必须至少一条合法锚点；
       任何出现的锚点都必须合法，否则拒绝。
    """
    if object_type not in ("case_favorite", "case_list", "report_template"):
        return ItemValidationResult(ok=False, reason_code=REASON_UNKNOWN_TYPE)

    if not isinstance(raw_item, dict):
        return ItemValidationResult(ok=False, reason_code=REASON_FORBIDDEN_BODY)

    keys = set(raw_item.keys())
    # 1. 黑名单（正文/凭据）键出现即拒绝——绝不静默吞掉正文后继续导入。
    if keys & IMPORT_ITEM_FORBIDDEN_KEYS:
        return ItemValidationResult(ok=False, reason_code=REASON_FORBIDDEN_BODY)

    # 5(a). 任何出现的锚点都必须合法（防止半截/脏锚点混入）。
    raw_anchors = raw_item.get("source_anchors")
    if raw_anchors is not None:
        if not isinstance(raw_anchors, list):
            return ItemValidationResult(ok=False, reason_code=REASON_INVALID_ANCHOR)
        for anchor in raw_anchors:
            if not is_valid_anchor(anchor):
                return ItemValidationResult(ok=False, reason_code=REASON_INVALID_ANCHOR)

    # 2~3. 仅保留白名单键并净化值（非白名单键一律丢弃，绝不入库）。
    clean: dict = {}
    case_id = _truncate(raw_item.get("case_id"), _SHORT_MAX)
    if case_id:
        clean["case_id"] = case_id
    for key, limit in (
        ("case_number", _SHORT_MAX),
        ("court", _SHORT_MAX),
        ("trial_level", _TRIAL_MAX),
        ("case_cause", _SHORT_MAX),
        ("judgment_date", _TRIAL_MAX),
        ("note", _NOTE_MAX),
        ("tag", _TINY_MAX),
        ("label", _TINY_MAX),
        ("list_id", _SHORT_MAX),
        ("list_title", _SHORT_MAX),
        ("report_id", _SHORT_MAX),
    ):
        val = _truncate(raw_item.get(key), limit)
        if val is not None:
            clean[key] = val
    anchors = sanitize_anchors(raw_anchors)
    if anchors:
        clean["source_anchors"] = anchors

    # 4. 必须有 case_id。
    if not case_id:
        return ItemValidationResult(ok=False, reason_code=REASON_MISSING_CASE_ID)

    # 5(b). AI 内容承载型：无合法锚点不进入导入（仍带回 case_id 供逐项结果引用）。
    if object_type in AI_CONTENT_OBJECT_TYPES and not anchors:
        return ItemValidationResult(ok=False, reason_code=REASON_MISSING_ANCHOR, case_id=case_id)

    return ItemValidationResult(ok=True, reason_code=REASON_OK, clean_payload=clean, case_id=case_id)
