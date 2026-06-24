"""应用配置与启动期校验。

安全红线（Day0 §4.1 / §7）：
- 仅校验密钥“是否存在”，绝不打印密钥值。
- query embedding 与文书 embedding 必须共用同一 provider / 模型 / 维度 / 距离度量。
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[4]
ENV_FILE = PROJECT_ROOT / ".env"


def _default_chroma_persist_dir() -> str:
    return (PROJECT_ROOT / "data" / "chroma").as_posix()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- 密钥（仅校验存在性，不打印）---
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_CHAT_COMPLETIONS_PATH: str = "/v1/chat/completions"
    DEEPSEEK_MODEL: str = "deepseek-chat"
    QUERY_REWRITE_TIMEOUT_SECONDS: int = 5
    ENABLE_QUERY_REWRITE: bool = False
    SUMMARY_TIMEOUT_SECONDS: int = 5
    ENABLE_SUMMARY: bool = False
    ENABLE_EXPANDED_SEARCH: bool = False
    FACT_ALIGNMENT_TIMEOUT_SECONDS: float = 2.0

    # --- query processing ---
    QUERY_MAX_LENGTH: int = 5000
    QUERY_MIN_SEMANTIC_LENGTH: int = 4

    # --- fact similarity rerank ---
    ENABLE_WEIGHTED_RERANK: bool = False
    RERANK_WEIGHT_VECTOR_SIMILARITY: float = 0.55
    RERANK_WEIGHT_LEGAL_ELEMENT_OVERLAP: float = 0.20
    RERANK_WEIGHT_CASE_CAUSE_MATCH: float = 0.10
    RERANK_WEIGHT_KEY_PARAGRAPH_MATCH: float = 0.10
    RERANK_WEIGHT_AUTHORITY_SIGNAL: float = 0.05

    # --- M4 工作流沉淀开关（M4-1 合同冻结：默认全部 false，安全态）---
    # 仅声明开关位，M4-1 不实现任何沉淀业务行为；关闭时回到 M3 末态，
    # 不改变标准搜索默认行为、排序、source selection 或 rerank 默认开关。
    ENABLE_SEARCH_HISTORY: bool = False
    ENABLE_CASE_FAVORITE: bool = False
    ENABLE_CASE_LIST: bool = False
    ENABLE_LIST_EXPORT: bool = False
    ENABLE_REPORT_TEMPLATE: bool = False
    ENABLE_TEAM_REUSE: bool = False

    # --- M5 商业化扩展开关（M5-1 合同冻结：默认全部 false，安全态）---
    # 仅声明开关位，M5-1 不实现任何账号/隔离/权限/共享/导入/计费业务行为；
    # 关闭时回到 M4 末态（单用户、纯前端沉淀），不改变标准搜索默认行为、排序、
    # source selection 或 rerank 默认开关。M5-2 至 M5-9 才逐步实现对应能力，
    # 且每个能力都必须可关闭/可降级、不默认开启跨用户可见性、不代管凭据明文。
    ENABLE_ACCOUNT_SYSTEM: bool = False
    ENABLE_TEAM_WORKSPACE: bool = False
    ENABLE_PERMISSION_TIERING: bool = False
    ENABLE_TEAM_SHARING: bool = False
    ENABLE_BULK_IMPORT: bool = False
    ENABLE_BILLING: bool = False

    # --- E 系列多产品生态开关（E-1 合同冻结：默认全部 false，安全态）---
    # 仅声明开关位，E-1 不实现任何生态/录入端/法条检索/文书工作台/案件协作台业务行为，
    # 不新建任何产品能力包、不抽取共享内核、不暴露检索服务接口、不注册任何端点、不接线前端入口。
    # 关闭时回到 M5-10 单产品末态，不改变标准搜索默认行为、排序、source selection 或 rerank 默认开关。
    # ENABLE_ECOSYSTEM 为生态总开关（是否暴露跨产品导航/枢纽），与 4 个产品子开关正交。
    # E-4 至 E-7 才逐个实现对应产品的 on 路径，且每个产品都必须可关闭/可降级、不默认开启跨用户可见性。
    ENABLE_ECOSYSTEM: bool = False
    ENABLE_INTAKE: bool = False
    ENABLE_STATUTE_SEARCH: bool = False
    ENABLE_DRAFTING: bool = False
    ENABLE_CASEBOOK: bool = False

    # --- E4 案情录入端二级 flag（E4-1 仅声明，不接线、无 on 路径）---
    # 录入端「服务端 AI 增强抽取」子开关，正交于 ENABLE_INTAKE。
    # 地基决策（用户 2026-06-16 拍板）：脱敏与要素抽取默认纯浏览器本地完成，原始案情零上送；
    # 服务端 AI 增强是后期可选项，本期只冻结不接线——E4 阶段无 on 路径、不接任何端点。
    # 其后期调用前置 = 用户显式确认 + 已完成本地脱敏，且只接收已脱敏文本、用完即丢、零持久化、日志不写正文。
    # 默认必须为 False；任一默认 True 即 E4 NO_GO。
    ENABLE_INTAKE_AI_EXTRACTION: bool = False

    # --- embedding / 向量库口径 ---
    # 向量模型本地部署：Ollama bge-m3（1024 维），无需 API 密钥。
    EMBEDDING_PROVIDER: str = "ollama"
    EMBEDDING_MODEL: str = "bge-m3"
    EMBEDDING_DIMENSION: int = 1024
    EMBEDDING_DISTANCE_METRIC: str = "cosine"
    EMBEDDING_TIMEOUT_SECONDS: float = 6.0
    EMBEDDING_WARMUP_TIMEOUT_SECONDS: float = 6.0
    EMBEDDING_CACHE_TTL_SECONDS: int = 300
    EMBEDDING_CACHE_MAX_ENTRIES: int = 256
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    CHROMA_COLLECTION: str = "case_chunks_bge_m3_v1"
    # Public default stays repo-relative. On Windows checkouts under non-ASCII paths,
    # set CHROMA_PERSIST_DIR explicitly to an ASCII directory if Chroma/HNSW misbehaves.
    CHROMA_PERSIST_DIR: str = _default_chroma_persist_dir()
    CHROMA_QUERY_TIMEOUT_SECONDS: int = 3

    # --- 基础设施 ---
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/case_search"
    REDIS_URL: str = "redis://localhost:6379/0"
    LOG_LEVEL: str = "INFO"


settings = Settings()

# 需要在启动期确认“存在”的密钥清单（只看是否非空，绝不读取/打印其值）。
# 向量模型走本地 Ollama，无需密钥；这里只校验 LLM 密钥。
REQUIRED_SECRET_KEYS = ("DEEPSEEK_API_KEY",)


def check_secrets_present(s: Settings = settings) -> dict[str, bool]:
    """返回每个必需密钥是否“存在”（非空）。值本身不出现在返回里。"""
    return {key: bool(getattr(s, key, "").strip()) for key in REQUIRED_SECRET_KEYS}


def missing_secrets(s: Settings = settings) -> list[str]:
    """返回缺失（未配置）的密钥名清单——只回名字，不回值。"""
    return [name for name, present in check_secrets_present(s).items() if not present]
