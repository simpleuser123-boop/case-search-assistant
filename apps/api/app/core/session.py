"""Query session id helpers.

Day 1 5.1 only needs a stable id shape that can be logged and returned in
errors. It does not persist the session yet.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4


def generate_query_session_id() -> str:
    now = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"qs_{now}_{uuid4().hex[:12]}"
