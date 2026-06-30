from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .errors import AWGPanelError

OPERATION_JOBS_DIR = Path(
    os.environ.get(
        "AWGPANEL_OPERATION_JOBS_DIR",
        "/var/lib/sg-awg-panel/operation-jobs",
    )
)
PROJECT_DIR = Path(os.environ.get("AWGPANEL_PROJECT_DIR", "/opt/sg-awg-panel"))
ENV_FILE = Path(os.environ.get("AWGPANEL_ENV_FILE", "/etc/sg-awg-panel/web.env"))
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,80}$")
_ALLOWED_KINDS = {
    "server_config",
    "service_action",
    "backup_restore",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _require_root() -> None:
    if os.geteuid() != 0:
        raise PermissionError("Для запуска системной операции нужны права root")


def _safe_path(value: object, default: str = "/") -> str:
    raw = str(value or default)
    if not raw.startswith("/") or raw.startswith("//") or "\n" in raw or "\r" in raw:
        return default
    return raw[:1024]


def _atomic_json(path: Path, payload: dict[str, Any], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, mode)
    temporary.replace(path)


def _cleanup_jobs() -> None:
    if not OPERATION_JOBS_DIR.exists():
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=2)
    for child in OPERATION_JOBS_DIR.iterdir():
        if not child.is_dir() or not _TOKEN_RE.fullmatch(child.name):
            continue
        try:
            modified = datetime.fromtimestamp(child.stat().st_mtime, timezone.utc)
        except OSError:
            continue
        if modified < cutoff:
            shutil.rmtree(child, ignore_errors=True)


def start_operation_job(
    *,
    kind: str,
    title: str,
    payload: dict[str, Any] | None = None,
    success_path: str = "/",
    error_path: str = "/",
) -> dict[str, str]:
    _require_root()
    if kind not in _ALLOWED_KINDS:
        raise ValueError("Неизвестная системная операция")
    clean_title = str(title or "Системная операция").strip()[:160]
    if not clean_title:
        raise ValueError("Не указано название операции")

    OPERATION_JOBS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(OPERATION_JOBS_DIR, 0o700)
    _cleanup_jobs()
    token = secrets.token_urlsafe(32)
    job_dir = OPERATION_JOBS_DIR / token
    job_dir.mkdir(mode=0o700)
    data = {
        "kind": kind,
        "title": clean_title,
        "payload": payload or {},
        "successPath": _safe_path(success_path),
        "errorPath": _safe_path(error_path),
        "createdAt": _utc_now(),
    }
    _atomic_json(job_dir / "job.json", data)
    _atomic_json(
        job_dir / "status.json",
        {
            "state": "starting",
            "title": clean_title,
            "message": "Операция поставлена в очередь",
            "successPath": data["successPath"],
            "errorPath": data["errorPath"],
            "startedAt": data["createdAt"],
            "updatedAt": data["createdAt"],
        },
    )
    (job_dir / "operation.log").write_text(
        f"[SG-AWG-Panel] {clean_title}\n", encoding="utf-8"
    )
    os.chmod(job_dir / "operation.log", 0o600)

    systemd_run = shutil.which("systemd-run")
    python = PROJECT_DIR / ".venv" / "bin" / "python"
    if not systemd_run:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise AWGPanelError("Не найдена команда systemd-run")
    if not python.exists():
        shutil.rmtree(job_dir, ignore_errors=True)
        raise AWGPanelError("Python-окружение панели не найдено")
    args = [
        systemd_run,
        "--collect",
        f"--unit=sg-awg-operation-{uuid.uuid4().hex[:10]}",
        f"--working-directory={PROJECT_DIR}",
    ]
    if ENV_FILE.exists():
        args.append(f"--property=EnvironmentFile={ENV_FILE}")
    args += [str(python), "-m", "awgpanel", "operation-job", "--token", token]
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode != 0:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise AWGPanelError(
            result.stderr.strip()
            or result.stdout.strip()
            or "Не удалось запустить системную операцию"
        )
    return {"token": token, "title": clean_title}


def get_operation_job(token: str) -> dict[str, Any] | None:
    if not _TOKEN_RE.fullmatch(str(token or "")):
        return None
    job_dir = OPERATION_JOBS_DIR / token
    try:
        status = json.loads((job_dir / "status.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        status["log"] = (job_dir / "operation.log").read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        status["log"] = ""
    return status


class OperationReporter:
    def __init__(self, job_dir: Path, job: dict[str, Any]):
        self.job_dir = job_dir
        self.job = job
        self.status_path = job_dir / "status.json"
        self.log_path = job_dir / "operation.log"
        self.started_at = str(job.get("createdAt") or _utc_now())

    def log(self, text: str) -> None:
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(text.rstrip() + "\n")

    def status(
        self,
        state: str,
        message: str,
        *,
        result: dict[str, Any] | None = None,
        restored: bool = False,
    ) -> None:
        payload: dict[str, Any] = {
            "state": state,
            "title": str(self.job.get("title") or "Системная операция"),
            "message": message,
            "successPath": _safe_path(self.job.get("successPath")),
            "errorPath": _safe_path(self.job.get("errorPath")),
            "startedAt": self.started_at,
            "updatedAt": _utc_now(),
        }
        if result is not None:
            payload["result"] = result
        if restored:
            payload["restored"] = True
        _atomic_json(self.status_path, payload)

    def phase(self, text: str) -> None:
        self.log(f"[Этап] {text}")
        self.status("running", text)


OperationHandler = Callable[[OperationReporter, dict[str, Any]], dict[str, Any]]


def _server_config(reporter: OperationReporter, payload: dict[str, Any]) -> dict[str, Any]:
    from .core import configure_and_start_awg

    values = payload.get("values")
    if not isinstance(values, dict):
        raise ValueError("Параметры AWG Server отсутствуют")
    reporter.phase("Проверка параметров AWG Server")
    reporter.log("[Проверка] Создаётся страховочная резервная копия текущего состояния.")
    reporter.phase("Формирование конфигурации AmneziaWG")
    settings, state = configure_and_start_awg(**values)
    reporter.log(f"[Готово] Служба AmneziaWG: {state}")
    reporter.log(f"[Готово] Интерфейс: {settings['interface_name']}; UDP-порт: {settings['listen_port']}")
    return {"serviceState": state, "interface": str(settings["interface_name"])}


def _service_action(reporter: OperationReporter, payload: dict[str, Any]) -> dict[str, Any]:
    from .core import restart_awg, start_awg, stop_awg

    action = str(payload.get("action") or "")
    actions = {"start": start_awg, "stop": stop_awg, "restart": restart_awg}
    if action not in actions:
        raise ValueError("Недопустимое действие службы")
    labels = {"start": "Запуск", "stop": "Остановка", "restart": "Перезапуск"}
    reporter.phase(f"{labels[action]} службы AmneziaWG")
    state = actions[action]()
    reporter.log(f"[Готово] Состояние службы: {state}")
    return {"serviceState": state}


def _backup_restore(reporter: OperationReporter, payload: dict[str, Any]) -> dict[str, Any]:
    from .core import (
        awg_service_state,
        get_awg_settings,
        restore_backup,
        verify_backup,
    )
    from .db import connect

    name = str(payload.get("name") or "")
    reporter.phase("Проверка целостности резервной копии")
    reporter.log(f"[Копия] {name}")
    verification = verify_backup(name)
    if not verification.get("verified"):
        errors = verification.get("verification_errors") or ["неизвестная ошибка"]
        raise AWGPanelError("Проверка копии не пройдена: " + "; ".join(map(str, errors)))
    reporter.log(
        "[Проверка] SQLite, конфигурация и контрольные суммы: успешно; "
        f"файлов: {verification.get('file_count', 0)}; "
        f"размер: {verification.get('size_bytes', 0)} байт"
    )

    reporter.phase("Создание страховочной копии текущего состояния")
    reporter.log("[Защита] Перед восстановлением автоматически сохраняется текущее состояние.")
    reporter.phase("Восстановление базы и конфигурации AWG")
    restored = restore_backup(name)

    reporter.phase("Проверка восстановленного состояния")
    with connect() as con:
        integrity = con.execute("PRAGMA integrity_check").fetchall()
        client_count = int(con.execute("SELECT COUNT(*) FROM awg_clients").fetchone()[0])
    if integrity != [("ok",)]:
        raise AWGPanelError(f"SQLite после восстановления: {integrity}")
    settings = dict(get_awg_settings())
    service_state = awg_service_state()
    reporter.log("[Проверка] SQLite после восстановления: ok")
    reporter.log(f"[Проверка] Клиентов восстановлено: {client_count}")
    reporter.log(
        "[Проверка] AWG Server: "
        + (service_state if settings.get("configured") else "не был настроен в этой копии")
    )
    reporter.log(f"[Готово] Восстановлено из {restored.name}")
    return {
        "backup": restored.name,
        "clients": client_count,
        "serviceState": service_state,
    }




_HANDLERS: dict[str, OperationHandler] = {
    "server_config": _server_config,
    "service_action": _service_action,
    "backup_restore": _backup_restore,
}


def run_operation_job(token: str) -> int:
    if not _TOKEN_RE.fullmatch(str(token or "")):
        return 2
    job_dir = OPERATION_JOBS_DIR / token
    try:
        job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 2
    reporter = OperationReporter(job_dir, job)
    kind = str(job.get("kind") or "")
    payload = job.get("payload")
    if kind not in _HANDLERS or not isinstance(payload, dict):
        reporter.log("[Ошибка] Некорректное описание операции.")
        reporter.status("error", "Операция не распознана")
        return 2
    try:
        reporter.status("running", "Операция запущена")
        result = _HANDLERS[kind](reporter, payload)
        reporter.log("[Готово] Операция завершена.")
        reporter.status("success", "Операция завершена успешно", result=result)
        return 0
    except Exception as exc:
        reporter.log(f"[Ошибка] {exc}")
        reporter.log("[Диагностика]")
        reporter.log(traceback.format_exc(limit=8))
        reporter.status(
            "error",
            str(exc) or "Операция завершилась с ошибкой",
            restored=kind in {"server_config", "backup_restore"},
        )
        return 1
