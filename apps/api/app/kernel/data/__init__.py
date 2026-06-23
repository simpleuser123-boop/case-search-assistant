"""共享内核 · 数据组公开面（E-2a 逻辑边界，纯 re-export）。

内核成员（依据文档 17 §2.1）：pipeline / case_store。
本模块只把上述两包「现有可调用入口」收敛为稳定公开符号，**纯 re-export**：
不复制实现、不改签名、不改运行时语义、不新增逻辑。E-2a 阶段零文件移动。

> pipeline/ 为离线语料管道脚本入口；case_store/ 为案例存储读取面。
> 二者只读元数据/结构化字段，绝不返回正文型字段进入运行时持久层/报告/日志。
"""
from __future__ import annotations

# --- case_store 读取面 ---
from app.kernel.data.case_store.jsonl_store import CaseStoreNotReadyError, get_case_detail

# --- pipeline 语料管道入口 ---
from app.kernel.data.pipeline.index_chroma import (
    EmbeddingError,
    iter_chunks,
    load_cases_meta,
    validate_collection_model,
)

__all__ = [
    # case_store
    "CaseStoreNotReadyError", "get_case_detail",
    # pipeline
    "EmbeddingError", "iter_chunks", "load_cases_meta", "validate_collection_model",
]
