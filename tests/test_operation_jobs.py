import json
from pathlib import Path

import awgpanel.operation_jobs as jobs


def test_operation_worker_persists_live_log_and_success(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "OPERATION_JOBS_DIR", tmp_path)
    token = "a" * 43
    job_dir = tmp_path / token
    job_dir.mkdir()
    job = {
        "kind": "test",
        "title": "Тестовая операция",
        "payload": {"value": 7},
        "successPath": "/done",
        "errorPath": "/back",
        "createdAt": "2026-06-29T10:00:00+00:00",
    }
    (job_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")
    (job_dir / "operation.log").write_text("start\n", encoding="utf-8")

    def handler(reporter, payload):
        reporter.phase("Проверка")
        reporter.log("[OK] Значение принято")
        return {"value": payload["value"]}

    monkeypatch.setattr(jobs, "_HANDLERS", {"test": handler})
    assert jobs.run_operation_job(token) == 0
    status = jobs.get_operation_job(token)
    assert status["state"] == "success"
    assert status["result"] == {"value": 7}
    assert status["successPath"] == "/done"
    assert "[Этап] Проверка" in status["log"]
    assert "[OK] Значение принято" in status["log"]


def test_operation_worker_does_not_claim_restore_for_protection_error(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "OPERATION_JOBS_DIR", tmp_path)
    token = "b" * 43
    job_dir = tmp_path / token
    job_dir.mkdir()
    job = {
        "kind": "protection_check",
        "title": "Проверка защиты",
        "payload": {},
        "successPath": "/protection",
        "errorPath": "/protection",
        "createdAt": "2026-06-29T10:00:00+00:00",
    }
    (job_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")
    (job_dir / "operation.log").write_text("", encoding="utf-8")

    def fail(reporter, payload):
        raise RuntimeError("test failure")

    monkeypatch.setattr(jobs, "_HANDLERS", {"protection_check": fail})
    assert jobs.run_operation_job(token) == 1
    status = jobs.get_operation_job(token)
    assert status["state"] == "error"
    assert status.get("restored") is not True
    assert "test failure" in status["log"]


def test_backup_restore_reports_integrity_clients_and_service(monkeypatch):
    import sqlite3
    from pathlib import Path
    import awgpanel.core as core
    import awgpanel.db as db

    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE awg_clients (id INTEGER PRIMARY KEY)")
    connection.executemany("INSERT INTO awg_clients(id) VALUES (?)", [(1,), (2,)])

    monkeypatch.setattr(db, "connect", lambda: connection)
    monkeypatch.setattr(
        core,
        "verify_backup",
        lambda name: {
            "verified": True,
            "file_count": 3,
            "size_bytes": 4096,
            "verification_errors": [],
        },
    )
    monkeypatch.setattr(core, "restore_backup", lambda name: Path(name))
    monkeypatch.setattr(core, "get_awg_settings", lambda: {"configured": 1})
    monkeypatch.setattr(core, "awg_service_state", lambda: "active")

    class Reporter:
        def __init__(self):
            self.messages = []

        def phase(self, text):
            self.messages.append("PHASE: " + text)

        def log(self, text):
            self.messages.append(text)

    reporter = Reporter()
    result = jobs._backup_restore(reporter, {"name": "backup-test"})
    joined = "\n".join(reporter.messages)
    assert result == {
        "backup": "backup-test",
        "clients": 2,
        "serviceState": "active",
    }
    assert "SQLite после восстановления: ok" in joined
    assert "Клиентов восстановлено: 2" in joined
    assert "AWG Server: active" in joined
