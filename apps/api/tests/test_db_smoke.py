from __future__ import annotations

from sqlalchemy import create_engine, text

from scripts import db_smoke


def test_db_smoke_passes_with_key_tables_and_minimal_read_write():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('r2_smoke_test')"))
        conn.execute(
            text(
                """
                CREATE TABLE analytics_events (
                    id INTEGER PRIMARY KEY,
                    event_name VARCHAR(80) NOT NULL
                )
                """
            )
        )

    report = db_smoke.build_report(engine)

    assert report["status"] == "ok"
    assert report["db"]["driver"] == "sqlite"
    assert report["checks"]["connection"]["ok"] is True
    assert report["checks"]["migration"]["ok"] is True
    assert report["checks"]["key_tables"]["ok"] is True
    assert report["checks"]["minimal_read_write"]["ok"] is True
    assert report["checks"]["minimal_read_write"]["detail"]["cleanup_ok"] is True


def test_db_smoke_reports_missing_key_tables_but_still_checks_read_write():
    engine = create_engine("sqlite:///:memory:")

    report = db_smoke.build_report(engine)

    assert report["status"] == "degraded"
    assert report["checks"]["connection"]["ok"] is True
    assert report["checks"]["migration"]["ok"] is True
    assert report["checks"]["key_tables"]["ok"] is False
    assert report["checks"]["key_tables"]["detail"]["missing_tables"] == ["analytics_events"]
    assert report["checks"]["minimal_read_write"]["ok"] is True
