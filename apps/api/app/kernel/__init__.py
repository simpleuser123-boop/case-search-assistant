"""共享内核公开面（kernel surface · E-2a 逻辑边界冻结）。

E 系列多产品生态：把散落在 apps/api/app/ 下的 RAG 核心、身份与租户、契约与护栏、
数据层，显式收敛为「共享内核层」的稳定公开面。**消费方（检索链路、未来产品能力包）
只经本公开面消费内核**，不得绕过公开面深引内核内部私有子模块，不得互相 import。

E-2a 第一性约束（纯 re-export，零语义变化）：
- 纯 re-export：所有符号转发自现有真实包，不复制实现、不改签名、不改运行时语义。
- 零文件移动：真实实现仍在原 app.kernel.rag.retrieval / app.kernel.identity.account / app.kernel.guardrails.contracts / ... ；
  物理迁移进 app/kernel/{rag,identity,guardrails,data}/ 是 E-2b。
- 内核不得反向 import 任何产品包（intake/statute/drafting/casebook，本步均不存在）。
- 护栏（白名单 sanitize / 锚点校验 / 多租户过滤 / 对象级鉴权）只在内核实现一次。

四组公开面（文档 17 §2.1 冻结口径）：
- rag：retrieval / rerank / query_processing / summary
- identity：account / team / permission / sharing
- guardrails：contracts + 锚点校验 + 多租户过滤 + 对象级鉴权
- data：pipeline / case_store

子模块亦可单独消费（如 `from app.kernel.rag import VectorRetrievalService`），
以便消费方按需引入、避免不必要的导入副作用。
"""
from __future__ import annotations

from app.kernel import data, guardrails, identity, rag
from app.kernel.rag import *  # noqa: F401,F403
from app.kernel.identity import *  # noqa: F401,F403
from app.kernel.guardrails import *  # noqa: F401,F403
from app.kernel.data import *  # noqa: F401,F403

# 内核成员归属声明（仅文档/聚合层声明，E-2a 不挪文件；供守门测试与 E-2b 迁移引用）。
KERNEL_GROUPS = {
    "rag": ("retrieval", "rerank", "query_processing", "summary"),
    "identity": ("account", "team", "permission", "sharing"),
    "guardrails": ("contracts",),  # + 锚点校验/多租户过滤/对象级鉴权（共享自 identity 三包）
    "data": ("pipeline", "case_store"),
}

# 未来产品能力包命名空间（E-4~E-7 才建；本步预置规则，当前均不存在）。
PRODUCT_PACKAGES = ("intake", "statute", "drafting", "casebook")

__all__ = (
    ["KERNEL_GROUPS", "PRODUCT_PACKAGES", "data", "guardrails", "identity", "rag"]
    + list(rag.__all__)
    + list(identity.__all__)
    + list(guardrails.__all__)
    + list(data.__all__)
)
