"""E-2b re-export shim — 真实实现已物理迁入 app.kernel.rag.summary。
本文件仅转发，不留实现分叉；旧 import 路径 `app.summary` 继续可用且指向同一对象。
迁移基线：E-2a 末态（docs/development/e2a-release-gate-20260615-095700.json）。"""
from __future__ import annotations
import importlib as _il
_real = _il.import_module("app.kernel.rag.summary")
for _k in dir(_real):
    if not _k.startswith("__"):
        globals()[_k] = getattr(_real, _k)
__all__ = list(getattr(_real, "__all__", [k for k in dir(_real) if not k.startswith("_")]))
del _il, _real, _k
