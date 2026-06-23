"""数据库引擎（SQLModel + SQLAlchemy）。

Day 0 只需可连接性；表结构留待 Day 1 数据层落地。
"""
from __future__ import annotations

from sqlmodel import create_engine

from app.core.config import settings

# pool_pre_ping：避免连接被数据库回收后报错。
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True, echo=False)
