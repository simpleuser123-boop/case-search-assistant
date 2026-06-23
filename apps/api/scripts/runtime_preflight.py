"""Compare live /health with the current source TestClient /health."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPT_PATH = Path(__file__).resolve()
API_ROOT = SCRIPT_PATH.parents[1]
PROJECT_ROOT = SCRIPT_PATH.parents[3]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

DEFAULT_LIVE_URL = os.getenv("RUNTIME_PREFLIGHT_URL", "http://127.0.0.1:8000/health")
DEFAULT_TIMEOUT_SECONDS = float(os.getenv("RUNTIME_PREFLIGHT_TIMEOUT_SECONDS", "8"))

REQUIRED_PATHS = (
    ("feature_flags",),
    ("runtime",),
    ("runtime", "app_version"),
    ("runtime", "source_root"),
    ("runtime", "process_id"),
    ("runtime", "started_at"),
    ("runtime", "config_digest"),
    ("dependencies",),
    ("dependencies", "db"),
    ("dependencies", "db", "reachable"),
    ("dependencies", "db", "degraded_reason"),
    ("dependencies", "ollama"),
    ("dependencies", "ollama", "reachable"),
    ("dependencies", "ollama", "degraded_reason"),
    ("dependencies", "chroma"),
    ("dependencies", "chroma", "reachable"),
    ("dependencies", "chroma", "degraded_reason"),
)

COMPARABLE_PATHS = (
    ("feature_flags",),
    ("runtime", "app_version"),
    ("runtime", "config_digest"),
    ("embedding_provider",),
    ("embedding_model",),
    ("embedding_dimension",),
    ("embedding_distance_metric",),
    ("chroma_collection",),
)


@dataclass
class PreflightResult:
    ok: bool
    errors: list[str]
    live_health: dict[str, Any]
    source_health: dict[str, Any]


_MISSING = object()


def _path_label(path: tuple[str, ...]) -> str:
    return ".".join(path)


def _get_path(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return _MISSING
        current = current[key]
    return current


def _normalize_path(value: Any) -> str:
    return os.path.normcase(str(Path(str(value)).resolve()))


def fetch_live_health(url: str = DEFAULT_LIVE_URL, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"live /health is not reachable at {url}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"live /health at {url} did not return JSON") from exc


def fetch_source_health() -> dict[str, Any]:
    from fastapi.testclient import TestClient

    from app.main import app

    response = TestClient(app).get("/health")
    if response.status_code != 200:
        raise RuntimeError(f"source TestClient /health returned HTTP {response.status_code}")
    return response.json()


def compare_health(
    live_health: dict[str, Any],
    source_health: dict[str, Any],
    *,
    current_source_root: str | Path | None = None,
) -> PreflightResult:
    errors: list[str] = []

    for path in REQUIRED_PATHS:
        if _get_path(live_health, path) is _MISSING:
            errors.append(f"live /health missing field: {_path_label(path)}")
        if _get_path(source_health, path) is _MISSING:
            errors.append(f"source TestClient /health missing field: {_path_label(path)}")

    live_source_root = _get_path(live_health, ("runtime", "source_root"))
    expected_source_root = _get_path(source_health, ("runtime", "source_root"))
    current_root = current_source_root or expected_source_root or PROJECT_ROOT
    if live_source_root is not _MISSING:
        if _normalize_path(live_source_root) != _normalize_path(current_root):
            errors.append(
                "live /health runtime.source_root does not match current source root; "
                f"live={live_source_root!r}, current={str(current_root)!r}. "
                "Restart the API process or switch port 8000 to the current project."
            )
    if expected_source_root is not _MISSING:
        if _normalize_path(expected_source_root) != _normalize_path(current_root):
            errors.append(
                "source TestClient runtime.source_root does not match current project; "
                f"source={expected_source_root!r}, current={str(current_root)!r}."
            )

    for path in COMPARABLE_PATHS:
        live_value = _get_path(live_health, path)
        source_value = _get_path(source_health, path)
        if live_value is _MISSING or source_value is _MISSING:
            continue
        if live_value != source_value:
            errors.append(
                f"health field mismatch: {_path_label(path)} "
                f"live={live_value!r}, source={source_value!r}"
            )

    return PreflightResult(
        ok=not errors,
        errors=errors,
        live_health=live_health,
        source_health=source_health,
    )


def run_preflight(url: str = DEFAULT_LIVE_URL, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> PreflightResult:
    live = fetch_live_health(url, timeout_seconds)
    source = fetch_source_health()
    return compare_health(live, source)


def main() -> int:
    url = DEFAULT_LIVE_URL
    try:
        result = run_preflight(url, DEFAULT_TIMEOUT_SECONDS)
    except RuntimeError as exc:
        print(f"FAIL runtime preflight: {exc}")
        print("Start or restart the current API process, then rerun this preflight.")
        return 1

    if not result.ok:
        print("FAIL runtime preflight:")
        for error in result.errors:
            print(f"- {error}")
        return 1

    runtime = result.live_health["runtime"]
    print("PASS runtime preflight")
    print(f"live_url={url}")
    print(f"process_id={runtime['process_id']}")
    print(f"source_root={runtime['source_root']}")
    print(f"started_at={runtime['started_at']}")
    print(f"app_version={runtime['app_version']}")
    print(f"config_digest={runtime['config_digest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
