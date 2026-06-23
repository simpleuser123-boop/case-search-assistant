"""Minimal DB smoke for R2.

Checks:
- connection reachability
- migration/alembic version status
- key table existence without creating business tables
- minimal read/write on a smoke-owned temporary table
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError, SQLAlchemyError, TimeoutError

from app.core.db import engine

EXPECTED_BUSINESS_TABLES = (
    "analytics_events",
)
SMOKE_TABLE_NAME = "__db_smoke_probe"


@dataclass
class CheckResult:
    ok: bool
    detail: dict[str, Any]


def _db_error_reason(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return "connection_timeout"
    if isinstance(exc, OperationalError):
        return "connection_failed"
    if isinstance(exc, SQLAlchemyError):
        return "sqlalchemy_error"
    return exc.__class__.__name__


def _db_target(db_engine: Engine) -> dict[str, Any]:
    url = db_engine.url
    return {
        "driver": getattr(url, "drivername", None) or getattr(db_engine.dialect, "name", "unknown"),
        "host": getattr(url, "host", None),
        "database": getattr(url, "database", None),
    }


def check_connection(db_engine: Engine) -> CheckResult:
    try:
        with db_engine.connect() as conn:
            value = conn.execute(text("SELECT 1")).scalar()
        return CheckResult(ok=value == 1, detail={"reachable": value == 1})
    except Exception as exc:  # noqa: BLE001 - smoke must explain failure
        return CheckResult(
            ok=False,
            detail={
                "reachable": False,
                "reason": _db_error_reason(exc),
            },
        )


def check_migration_status(db_engine: Engine) -> CheckResult:
    versions_dir = API_ROOT / "alembic" / "versions"
    revision_files = sorted(path.name for path in versions_dir.glob("*.py") if path.name != "__init__.py")
    try:
        inspector = inspect(db_engine)
        tables = set(inspector.get_table_names())
        has_version_table = "alembic_version" in tables
        versions: list[str] = []
        if has_version_table:
            with db_engine.connect() as conn:
                rows = conn.execute(text("SELECT version_num FROM alembic_version")).fetchall()
            versions = [str(row[0]) for row in rows]
        ok = not revision_files or has_version_table
        return CheckResult(
            ok=ok,
            detail={
                "revision_file_count": len(revision_files),
                "revision_files": revision_files,
                "alembic_version_table_exists": has_version_table,
                "applied_versions": versions,
                "reason": None if ok else "missing_alembic_version_table",
            },
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            ok=False,
            detail={
                "reason": _db_error_reason(exc),
            },
        )


def check_key_tables(db_engine: Engine) -> CheckResult:
    try:
        inspector = inspect(db_engine)
        tables = set(inspector.get_table_names())
        existing = sorted(name for name in EXPECTED_BUSINESS_TABLES if name in tables)
        missing = sorted(name for name in EXPECTED_BUSINESS_TABLES if name not in tables)
        return CheckResult(
            ok=not missing,
            detail={
                "expected_tables": list(EXPECTED_BUSINESS_TABLES),
                "existing_tables": existing,
                "missing_tables": missing,
                "reason": None if not missing else "missing_key_tables",
            },
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            ok=False,
            detail={
                "reason": _db_error_reason(exc),
            },
        )


def check_minimal_read_write(db_engine: Engine) -> CheckResult:
    create_prefix = "CREATE TEMP TABLE" if db_engine.dialect.name == "sqlite" else "CREATE TEMPORARY TABLE"
    try:
        with db_engine.connect() as conn:
            created = False
            conn.execute(
                text(
                    f"""
                    {create_prefix} {SMOKE_TABLE_NAME} (
                        smoke_id VARCHAR(64) PRIMARY KEY,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )
            created = True
            conn.execute(
                text(f"INSERT INTO {SMOKE_TABLE_NAME} (smoke_id) VALUES (:smoke_id)"),
                {"smoke_id": "r2-smoke"},
            )
            row = conn.execute(
                text(f"SELECT smoke_id FROM {SMOKE_TABLE_NAME} WHERE smoke_id = :smoke_id"),
                {"smoke_id": "r2-smoke"},
            ).scalar()
            conn.execute(
                text(f"DELETE FROM {SMOKE_TABLE_NAME} WHERE smoke_id = :smoke_id"),
                {"smoke_id": "r2-smoke"},
            )
            remaining = conn.execute(
                text(f"SELECT COUNT(*) FROM {SMOKE_TABLE_NAME} WHERE smoke_id = :smoke_id"),
                {"smoke_id": "r2-smoke"},
            ).scalar()
            if created:
                conn.execute(text(f"DROP TABLE IF EXISTS {SMOKE_TABLE_NAME}"))
            conn.commit()
        ok = row == "r2-smoke" and remaining == 0
        return CheckResult(
            ok=ok,
            detail={
                "table": SMOKE_TABLE_NAME,
                "inserted": row == "r2-smoke",
                "cleanup_ok": remaining == 0,
                "reason": None if ok else "smoke_read_write_verification_failed",
            },
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            ok=False,
            detail={
                "table": SMOKE_TABLE_NAME,
                "reason": _db_error_reason(exc),
            },
        )


def build_report(db_engine: Engine = engine) -> dict[str, Any]:
    target = _db_target(db_engine)
    connection = check_connection(db_engine)
    migration = check_migration_status(db_engine) if connection.ok else CheckResult(False, {"reason": "connection_unavailable"})
    key_tables = check_key_tables(db_engine) if connection.ok else CheckResult(False, {"reason": "connection_unavailable"})
    read_write = check_minimal_read_write(db_engine) if connection.ok else CheckResult(False, {"reason": "connection_unavailable"})
    overall_ok = connection.ok and migration.ok and key_tables.ok and read_write.ok
    return {
        "status": "ok" if overall_ok else "degraded",
        "db": target,
        "checks": {
            "connection": asdict(connection),
            "migration": asdict(migration),
            "key_tables": asdict(key_tables),
            "minimal_read_write": asdict(read_write),
        },
    }


def main() -> int:
    report = build_report()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
