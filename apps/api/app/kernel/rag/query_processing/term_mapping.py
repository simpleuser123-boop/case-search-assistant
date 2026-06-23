"""Local legal term mapping for pre-retrieval query understanding.

The mapping catalog is intentionally conservative:
- high-confidence mappings may populate weighted legal elements/case-cause hints;
- low-confidence mappings only create recall variants.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_TERM_MAPPING_PATH = PROJECT_ROOT / "data/eval/term_mappings.json"
TERM_MAPPING_VERSION = "m1_2_query_understanding_v1"
HIGH_CONFIDENCE_THRESHOLD = 0.85
MAX_LOCAL_QUERY_VARIANTS = 3


DEFAULT_TERM_MAPPINGS: list[dict[str, Any]] = [
    {
        "id": "drug_trade_meth",
        "colloquial": "卖冰毒",
        "triggers": ["卖冰毒", "贩卖毒品", "毒品交易"],
        "required_terms": ["甲基苯丙胺"],
        "legal_term": "走私、贩卖、运输、制造毒品",
        "case_cause_hint": "走私、贩卖、运输、制造毒品罪",
        "weighted_terms": ["走私、贩卖、运输、制造毒品", "甲基苯丙胺毒品交易"],
        "expansion_terms": ["甲基苯丙胺", "毒品数量", "毒品交易"],
        "confidence": 0.96,
        "confidence_level": "high",
        "default_enabled": False,
        "experimental": True,
    },
    {
        "id": "dangerous_driving_alcohol",
        "colloquial": "醉驾血检",
        "triggers": ["醉驾", "醉酒驾驶"],
        "required_terms": ["血液酒精含量"],
        "legal_term": "危险驾驶",
        "case_cause_hint": "危险驾驶罪",
        "weighted_terms": ["危险驾驶", "醉酒驾驶", "血液酒精含量"],
        "expansion_terms": ["认罪认罚", "道路交通安全"],
        "confidence": 0.94,
        "confidence_level": "high",
        "default_enabled": True,
    },
    {
        "id": "traffic_death_escape",
        "colloquial": "醉驾撞人逃跑",
        "triggers": ["酒驾撞死人", "醉驾撞人逃跑", "交通肇事", "逃逸"],
        "required_terms": ["致人死亡"],
        "legal_term": "交通肇事后逃逸",
        "case_cause_hint": "交通肇事罪",
        "weighted_terms": ["交通肇事", "致人死亡", "逃逸"],
        "expansion_terms": ["交通事故责任", "赔偿"],
        "confidence": 0.93,
        "confidence_level": "high",
        "default_enabled": False,
        "experimental": True,
    },
    {
        "id": "rape_violence_coercion",
        "colloquial": "违背意愿强行发生关系",
        "triggers": ["违背妇女意愿", "暴力胁迫"],
        "required_terms": ["强奸"],
        "legal_term": "强奸",
        "case_cause_hint": "强奸罪",
        "weighted_terms": ["强奸", "违背妇女意愿", "暴力胁迫"],
        "expansion_terms": ["性侵害", "被害人陈述"],
        "confidence": 0.93,
        "confidence_level": "high",
        "default_enabled": False,
        "experimental": True,
    },
    {
        "id": "theft_household",
        "colloquial": "入室偷东西",
        "triggers": ["入室偷东西", "入户盗窃", "入室盗窃"],
        "legal_term": "入户盗窃",
        "case_cause_hint": "盗窃罪",
        "weighted_terms": ["盗窃", "入户盗窃"],
        "expansion_terms": ["财物价值", "退赃"],
        "confidence": 0.92,
        "confidence_level": "high",
        "default_enabled": True,
    },
    {
        "id": "harbor_drug_use",
        "colloquial": "提供地方给别人吸毒",
        "triggers": ["提供地方给别人吸毒", "提供场所"],
        "required_terms": ["吸毒"],
        "legal_term": "容留他人吸毒",
        "case_cause_hint": "容留他人吸毒罪",
        "weighted_terms": ["容留他人吸毒", "提供场所", "多人吸毒"],
        "expansion_terms": ["毒品吸食", "场所"],
        "confidence": 0.96,
        "confidence_level": "high",
        "default_enabled": True,
    },
    {
        "id": "intentional_injury_minor",
        "colloquial": "打人轻伤",
        "triggers": ["打人轻伤", "轻伤", "持械殴打"],
        "required_terms": ["殴打"],
        "legal_term": "故意伤害",
        "case_cause_hint": "故意伤害罪",
        "weighted_terms": ["故意伤害", "轻伤", "持械殴打"],
        "expansion_terms": ["赔偿谅解", "伤情鉴定"],
        "confidence": 0.9,
        "confidence_level": "high",
        "default_enabled": True,
    },
    {
        "id": "robbery_knife_money",
        "colloquial": "拿刀抢钱",
        "triggers": ["拿刀抢钱", "持刀", "抢钱"],
        "required_terms": ["暴力"],
        "legal_term": "以暴力、胁迫方法抢劫",
        "case_cause_hint": "抢劫罪",
        "weighted_terms": ["抢劫", "持刀", "暴力胁迫"],
        "expansion_terms": ["索取财物", "当场劫取"],
        "confidence": 0.95,
        "confidence_level": "high",
        "default_enabled": True,
        "recall_only_variant_mode": "case_cause_plus_expansion",
    },
    {
        "id": "gun_ammo_possession",
        "colloquial": "私藏枪支弹药",
        "triggers": ["私藏枪支", "非法持有枪支", "非法持有私藏枪支"],
        "required_terms": ["弹药"],
        "legal_term": "非法持有、私藏枪支、弹药",
        "case_cause_hint": "非法持有、私藏枪支、弹药罪",
        "weighted_terms": ["非法持有、私藏枪支、弹药", "枪支弹药"],
        "expansion_terms": ["鉴定意见", "枪支弹药数量"],
        "confidence": 0.93,
        "confidence_level": "high",
        "default_enabled": False,
        "experimental": True,
    },
    {
        "id": "drug_possession_meth",
        "colloquial": "非法持有冰毒",
        "triggers": ["非法持有毒品", "毒品数量"],
        "required_terms": ["甲基苯丙胺"],
        "legal_term": "非法持有毒品",
        "case_cause_hint": "非法持有毒品罪",
        "weighted_terms": ["非法持有毒品", "甲基苯丙胺", "毒品数量"],
        "expansion_terms": ["毒品称量", "毒品鉴定"],
        "confidence": 0.94,
        "confidence_level": "high",
        "default_enabled": True,
        "recall_only_variant_mode": "legal_term",
    },
    {
        "id": "casino_online_profit",
        "colloquial": "网络赌博抽水",
        "triggers": ["抽水", "抽头渔利"],
        "required_terms": ["网络赌博"],
        "legal_term": "开设赌场",
        "case_cause_hint": "开设赌场罪",
        "weighted_terms": ["开设赌场", "网络赌博", "抽头渔利"],
        "expansion_terms": ["赌博平台", "赌资流水"],
        "confidence": 0.9,
        "confidence_level": "high",
        "default_enabled": True,
    },
    {
        "id": "gambling_group",
        "colloquial": "聚众赌博赌资",
        "triggers": ["聚众赌博"],
        "required_terms": ["赌资"],
        "legal_term": "赌博",
        "case_cause_hint": "赌博罪",
        "weighted_terms": ["赌博", "聚众赌博", "赌资"],
        "expansion_terms": ["赌博活动", "参赌人员"],
        "confidence": 0.89,
        "confidence_level": "high",
        "default_enabled": False,
        "experimental": True,
    },
    {
        "id": "job_occupation_company_funds",
        "colloquial": "利用职务便利占公司钱",
        "triggers": ["利用职务便利", "占有公司资金", "公司货款"],
        "legal_term": "职务侵占",
        "case_cause_hint": "职务侵占罪",
        "weighted_terms": ["职务侵占", "利用职务便利", "占有公司资金"],
        "expansion_terms": ["单位财物", "非法占有"],
        "confidence": 0.92,
        "confidence_level": "high",
        "default_enabled": True,
    },
    {
        "id": "illegal_business_unlicensed",
        "colloquial": "未经许可经营",
        "triggers": ["未经许可"],
        "required_terms": ["经营"],
        "legal_term": "非法经营",
        "case_cause_hint": "非法经营罪",
        "weighted_terms": ["非法经营", "未经许可经营"],
        "expansion_terms": ["行政许可", "经营数额"],
        "confidence": 0.88,
        "confidence_level": "high",
        "default_enabled": False,
        "experimental": True,
    },
    {
        "id": "illegal_logging_license",
        "colloquial": "没证砍树",
        "triggers": ["采伐许可证", "未办采伐许可证"],
        "required_terms": ["林木"],
        "legal_term": "滥伐林木",
        "case_cause_hint": "滥伐林木罪",
        "weighted_terms": ["滥伐林木", "采伐许可证", "林木"],
        "expansion_terms": ["林木蓄积", "采伐数量"],
        "confidence": 0.9,
        "confidence_level": "high",
        "default_enabled": True,
    },
    {
        "id": "extortion_threat_money",
        "colloquial": "威胁别人写借条",
        "triggers": ["威胁别人写借条", "威胁", "恐吓"],
        "required_terms": ["索要财物"],
        "legal_term": "敲诈勒索",
        "case_cause_hint": "敲诈勒索罪",
        "weighted_terms": ["敲诈勒索", "威胁恐吓", "索要财物"],
        "expansion_terms": ["被害人恐惧", "交付财物"],
        "confidence": 0.86,
        "confidence_level": "high",
        "default_enabled": True,
    },
    {
        "id": "copyright_copy_distribution",
        "colloquial": "未经许可复制发行",
        "triggers": ["复制发行"],
        "required_terms": ["未经许可"],
        "legal_term": "侵犯著作权",
        "case_cause_hint": "侵犯著作权罪",
        "weighted_terms": ["侵犯著作权", "未经许可", "复制发行"],
        "expansion_terms": ["非法出版物", "著作权许可"],
        "confidence": 0.91,
        "confidence_level": "high",
        "default_enabled": False,
        "experimental": True,
    },
    {
        "id": "obstruct_official_police",
        "colloquial": "拦民警执法",
        "triggers": ["阻碍民警", "暴力阻碍"],
        "required_terms": ["执法"],
        "legal_term": "妨害公务",
        "case_cause_hint": "妨害公务罪",
        "weighted_terms": ["妨害公务", "阻碍民警执法", "暴力"],
        "expansion_terms": ["依法执行职务", "警察"],
        "confidence": 0.9,
        "confidence_level": "high",
        "default_enabled": True,
    },
    {
        "id": "contract_fraud_signing",
        "colloquial": "签合同骗钱",
        "triggers": ["签订合同"],
        "required_terms": ["骗取财物"],
        "legal_term": "合同诈骗",
        "case_cause_hint": "合同诈骗罪",
        "weighted_terms": ["合同诈骗", "签订合同", "骗取财物"],
        "expansion_terms": ["非法占有目的", "合同履行"],
        "confidence": 0.89,
        "confidence_level": "high",
        "default_enabled": True,
    },
    {
        "id": "farmland_land_use_change",
        "colloquial": "占用林地种庄稼",
        "triggers": ["占用林地种庄稼", "改变土地用途"],
        "required_terms": ["林地"],
        "legal_term": "非法占用农用地",
        "case_cause_hint": "非法占用农用地罪",
        "weighted_terms": ["非法占用农用地", "改变土地用途", "林地"],
        "expansion_terms": ["农用地", "林业鉴定"],
        "confidence": 0.94,
        "confidence_level": "high",
        "default_enabled": True,
    },
    {
        "id": "public_deposit_high_interest",
        "colloquial": "高息集资",
        "triggers": ["高息集资", "返本付息"],
        "legal_term": "非法吸收公众存款",
        "case_cause_hint": "非法吸收公众存款罪",
        "weighted_terms": ["非法吸收公众存款", "高息集资", "返本付息"],
        "expansion_terms": ["不特定公众", "吸收资金"],
        "confidence": 0.88,
        "confidence_level": "high",
        "default_enabled": True,
    },
    {
        "id": "pyramid_scheme_rebate",
        "colloquial": "组织传销拉人头",
        "triggers": ["组织传销拉人头", "拉人头", "层级返利"],
        "legal_term": "组织、领导传销活动",
        "case_cause_hint": "组织、领导传销活动罪",
        "weighted_terms": ["组织、领导传销活动", "拉人头", "层级返利"],
        "expansion_terms": ["发展下线", "传销层级"],
        "confidence": 0.93,
        "confidence_level": "high",
        "default_enabled": True,
    },
    {
        "id": "pollution_hazardous_waste",
        "colloquial": "把危险废物倒掉",
        "triggers": ["把危险废物倒掉", "非法倾倒", "危险废物"],
        "legal_term": "非法处置危险废物",
        "case_cause_hint": "污染环境罪",
        "weighted_terms": ["污染环境", "非法处置危险废物", "危险废物"],
        "expansion_terms": ["非法倾倒", "环境污染"],
        "confidence": 0.9,
        "confidence_level": "high",
        "default_enabled": False,
        "experimental": True,
    },
    {
        "id": "ordinary_theft_colloquial",
        "colloquial": "偷东西",
        "triggers": ["偷东西"],
        "legal_term": "盗窃",
        "case_cause_hint": "盗窃罪",
        "weighted_terms": ["盗窃", "财物价值"],
        "expansion_terms": ["退赃", "被盗财物"],
        "confidence": 0.9,
        "confidence_level": "high",
        "default_enabled": True,
        "use_for_weighting": True,
    },
    {
        "id": "property_damage_vehicle",
        "colloquial": "把别人车砸了",
        "triggers": ["把别人车砸了", "砸车"],
        "legal_term": "故意毁坏财物",
        "case_cause_hint": "故意毁坏财物罪",
        "weighted_terms": ["故意毁坏财物", "车辆损失"],
        "expansion_terms": ["财物损失", "价格鉴定"],
        "confidence": 0.93,
        "confidence_level": "high",
        "default_enabled": True,
    },
    {
        "id": "public_deposit_borrow_many",
        "colloquial": "借钱不还还骗很多人",
        "triggers": ["借钱不还", "很多人"],
        "legal_term": "非法吸收公众存款",
        "case_cause_hint": "非法吸收公众存款罪",
        "expansion_terms": ["高息集资", "不特定公众", "返本付息"],
        "confidence": 0.82,
        "confidence_level": "low",
        "default_enabled": True,
    },
    {
        "id": "casino_payment_help",
        "colloquial": "帮赌博平台收钱",
        "triggers": ["帮赌博平台收钱", "赌博平台收钱"],
        "legal_term": "开设赌场帮助行为",
        "case_cause_hint": "开设赌场罪",
        "expansion_terms": ["资金结算", "赌博平台", "抽头渔利"],
        "confidence": 0.78,
        "confidence_level": "low",
        "default_enabled": True,
    },
    {
        "id": "identity_card_fraud",
        "colloquial": "假冒别人身份办卡",
        "triggers": ["假冒别人身份办卡", "冒用身份办卡"],
        "legal_term": "侵犯公民个人信息或诈骗相关行为",
        "case_cause_hint": "侵犯公民个人信息罪",
        "expansion_terms": ["冒用身份", "信用卡", "个人信息"],
        "confidence": 0.62,
        "confidence_level": "low",
        "default_enabled": False,
        "experimental": True,
    },
    {
        "id": "transfer_crime_proceeds",
        "colloquial": "帮别人转账洗钱",
        "triggers": ["帮别人转账洗钱", "帮人转账"],
        "legal_term": "掩饰、隐瞒犯罪所得",
        "case_cause_hint": "掩饰、隐瞒犯罪所得罪",
        "expansion_terms": ["转移资金", "犯罪所得", "代收代付"],
        "confidence": 0.76,
        "confidence_level": "low",
        "default_enabled": True,
    },
    {
        "id": "medical_insurance_fraud",
        "colloquial": "骗医保报销",
        "triggers": ["骗医保报销", "医保报销"],
        "legal_term": "诈骗医疗保障基金",
        "case_cause_hint": "诈骗罪",
        "expansion_terms": ["虚假报销", "医保基金", "诈骗"],
        "confidence": 0.72,
        "confidence_level": "low",
        "default_enabled": True,
    },
]


@dataclass(frozen=True)
class TermMappingMatch:
    mapping_id: str
    legal_term: str
    case_cause_hint: str
    confidence: float
    confidence_level: str
    weighting_allowed: bool
    use_case_cause_hint: bool
    experimental: bool
    expansion_terms: tuple[str, ...]
    weighted_terms: tuple[str, ...]
    recall_only_variant_mode: str


@dataclass(frozen=True)
class TermMappingApplication:
    version: str
    matches: tuple[TermMappingMatch, ...]
    query_variants: tuple[str, ...]
    recall_only_query_variants: tuple[str, ...]
    legal_elements: tuple[str, ...]
    case_cause_hint: str
    high_confidence_labels: tuple[str, ...]
    low_confidence_labels: tuple[str, ...]
    weighted_confidence: float | None

    @property
    def used(self) -> bool:
        return bool(self.matches)

    @property
    def experimental_used(self) -> bool:
        return any(match.experimental for match in self.matches)


def apply_term_mappings(cleaned_query: str, *, include_experimental: bool = False) -> TermMappingApplication:
    catalog = load_term_mapping_catalog()
    matches = _match_mappings(cleaned_query, catalog["mappings"], include_experimental=include_experimental)
    return _build_application(cleaned_query, catalog["version"], matches)


@lru_cache(maxsize=1)
def load_term_mapping_catalog() -> dict[str, Any]:
    if DEFAULT_TERM_MAPPING_PATH.is_file():
        with DEFAULT_TERM_MAPPING_PATH.open("r", encoding="utf-8") as file:
            payload = json.load(file)
        mappings = payload.get("mappings") if isinstance(payload, dict) else None
        if isinstance(mappings, list):
            return {
                "version": str(payload.get("version") or TERM_MAPPING_VERSION),
                "mappings": mappings,
            }
    return {"version": TERM_MAPPING_VERSION, "mappings": DEFAULT_TERM_MAPPINGS}


def _match_mappings(
    cleaned_query: str,
    mappings: list[dict[str, Any]],
    *,
    include_experimental: bool,
) -> tuple[TermMappingMatch, ...]:
    compact_query = _compact(cleaned_query)
    matches: list[TermMappingMatch] = []
    for mapping in mappings:
        if not bool(mapping.get("default_enabled", True)) and not include_experimental:
            continue
        if bool(mapping.get("experimental", False)) and not include_experimental:
            continue
        if not _mapping_matches(compact_query, mapping):
            continue
        confidence = _confidence(mapping)
        confidence_level = str(mapping.get("confidence_level") or "").strip().lower()
        if confidence_level not in {"high", "low"}:
            confidence_level = "high" if confidence >= HIGH_CONFIDENCE_THRESHOLD else "low"
        legal_term = str(mapping.get("legal_term") or "").strip()
        case_cause_hint = str(mapping.get("case_cause_hint") or "").strip()
        matches.append(
            TermMappingMatch(
                mapping_id=str(mapping.get("id") or mapping.get("colloquial") or legal_term).strip(),
                legal_term=legal_term,
                case_cause_hint=case_cause_hint,
                confidence=confidence,
                confidence_level=confidence_level,
                weighting_allowed=(
                    confidence_level == "high"
                    and confidence >= HIGH_CONFIDENCE_THRESHOLD
                    and bool(mapping.get("use_for_weighting", False))
                ),
                use_case_cause_hint=bool(mapping.get("use_case_cause_hint", False)),
                experimental=bool(mapping.get("experimental", False)),
                expansion_terms=tuple(_clean_terms(mapping.get("expansion_terms", []))),
                weighted_terms=tuple(_clean_terms(mapping.get("weighted_terms", []))),
                recall_only_variant_mode=str(mapping.get("recall_only_variant_mode") or "").strip(),
            )
        )
    return tuple(sorted(matches, key=lambda item: (item.weighting_allowed, item.confidence), reverse=True))


def _mapping_matches(compact_query: str, mapping: dict[str, Any]) -> bool:
    triggers = _clean_terms(mapping.get("triggers") or [mapping.get("colloquial")])
    if triggers and not any(_compact(term) in compact_query for term in triggers):
        return False
    required_terms = _clean_terms(mapping.get("required_terms", []))
    return all(_compact(term) in compact_query for term in required_terms)


def _build_application(
    cleaned_query: str,
    version: str,
    matches: tuple[TermMappingMatch, ...],
) -> TermMappingApplication:
    weighted_matches = [match for match in matches if match.weighting_allowed]
    legal_elements = _dedupe(
        term
        for match in weighted_matches
        for term in [match.legal_term, *match.weighted_terms]
    )
    high_case_hints = _dedupe(
        match.case_cause_hint for match in weighted_matches if match.case_cause_hint and match.use_case_cause_hint
    )
    case_cause_hint = high_case_hints[0] if len(high_case_hints) == 1 else ""
    query_variants = _dedupe(
        _variant_for_match(cleaned_query, match)
        for match in matches
    )[:MAX_LOCAL_QUERY_VARIANTS]
    recall_only_query_variants = _dedupe(
        _recall_only_variant_for_match(match)
        for match in matches
    )[:MAX_LOCAL_QUERY_VARIANTS]
    high_labels = tuple(match.mapping_id for match in matches if match.confidence_level == "high")
    low_labels = tuple(match.mapping_id for match in matches if match.confidence_level != "high")
    weighted_confidence = max((match.confidence for match in weighted_matches), default=None)
    return TermMappingApplication(
        version=version,
        matches=matches,
        query_variants=tuple(query_variants),
        recall_only_query_variants=tuple(recall_only_query_variants),
        legal_elements=tuple(legal_elements),
        case_cause_hint=case_cause_hint,
        high_confidence_labels=high_labels,
        low_confidence_labels=low_labels,
        weighted_confidence=weighted_confidence,
    )


def _variant_for_match(cleaned_query: str, match: TermMappingMatch) -> str:
    parts = [
        cleaned_query,
        match.legal_term,
        *match.expansion_terms,
    ]
    if match.weighting_allowed and match.use_case_cause_hint and match.case_cause_hint:
        parts.append(match.case_cause_hint)
    return " ".join(_dedupe(parts))


def _recall_only_variant_for_match(match: TermMappingMatch) -> str:
    if match.recall_only_variant_mode == "case_cause_plus_expansion":
        return " ".join(_dedupe([match.case_cause_hint, *match.expansion_terms]))
    if match.recall_only_variant_mode == "legal_term":
        return match.legal_term
    return ""


def _clean_terms(value: object) -> list[str]:
    if not isinstance(value, list | tuple):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _confidence(mapping: dict[str, Any]) -> float:
    try:
        value = float(mapping.get("confidence", 0.0))
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, value))


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def _dedupe(values) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if item and item not in seen:
            unique.append(item)
            seen.add(item)
    return unique
