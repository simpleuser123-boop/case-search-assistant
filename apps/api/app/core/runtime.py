"""Runtime metadata for local source/process consistency checks."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.core.config import PROJECT_ROOT, Settings, settings

APP_VERSION_ENV = "CASE_SEARCH_APP_VERSION"
APP_VERSION = os.getenv(APP_VERSION_ENV, "0.0.0")
SOURCE_ROOT = PROJECT_ROOT.resolve()
PROCESS_ID = os.getpid()
STARTED_AT = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

_SENSITIVE_MARKERS = ("KEY", "PASSWORD", "SECRET", "TOKEN")


def _is_sensitive_field(name: str) -> bool:
    upper = name.upper()
    return any(marker in upper for marker in _SENSITIVE_MARKERS)


def _sanitize_url(value: str) -> str:
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        return value

    hostname = parsed.hostname or ""
    netloc = hostname
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _safe_config_value(name: str, value: Any) -> Any:
    if _is_sensitive_field(name):
        return {"present": bool(str(value).strip())}
    if name.upper().endswith("_URL"):
        return _sanitize_url(str(value))
    if isinstance(value, Path):
        return str(value)
    return value


def safe_config_snapshot(config: Settings = settings) -> dict[str, Any]:
    """Return a deterministic, non-secret view of runtime configuration."""

    return {
        name: _safe_config_value(name, getattr(config, name))
        for name in sorted(type(config).model_fields)
    }


def config_digest(config: Settings = settings) -> str:
    payload = json.dumps(
        safe_config_snapshot(config),
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def runtime_metadata(config: Settings = settings) -> dict[str, Any]:
    return {
        "app_version": APP_VERSION,
        "source_root": str(SOURCE_ROOT),
        "process_id": PROCESS_ID,
        "started_at": STARTED_AT,
        "config_digest": config_digest(config),
    }
