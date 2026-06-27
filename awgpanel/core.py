from __future__ import annotations

import ipaddress
import json
import os
import random
import re
import secrets
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .db import connect, init_db
from .errors import AWGPanelError

AWG_CONFIG_DIR = Path(os.environ.get("AWGPANEL_AWG_CONFIG_DIR", "/etc/amnezia/amneziawg"))
AWG_SERVICE = os.environ.get("AWGPANEL_AWG_SERVICE", "sg-awg-server")
AWG_CONFIG_PATH = AWG_CONFIG_DIR / "awg0.conf"
BACKUP_DIR = Path(os.environ.get("AWGPANEL_BACKUP_DIR", "/var/lib/sg-awg-panel/backups"))
BACKUP_KEEP = int(os.environ.get("AWGPANEL_BACKUP_KEEP", "20"))
_PUBLIC_IP_CACHE: tuple[str, float] = ("", 0.0)
AWG_NAME_RE = re.compile(r"^[A-Za-z0-9А-Яа-яЁё_. -]{1,64}$")


def _run(
    args: list[str], *, input_text: str | None = None, timeout: int = 20
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise AWGPanelError(f"Команда превысила тайм-аут {timeout} с: {' '.join(args)}") from exc


def _require_root() -> None:
    if os.geteuid() != 0:
        raise PermissionError("Для управления AmneziaWG нужны права root")


def _command_path(name: str) -> str | None:
    return shutil.which(name)


def _keypair() -> tuple[str, str]:
    awg = _command_path("awg")
    if not awg:
        raise AWGPanelError("Команда awg не найдена. Сначала выполните deploy/install-amneziawg.sh")
    private = _run([awg, "genkey"]).stdout.strip()
    if not private:
        raise AWGPanelError("awg genkey не вернул закрытый ключ")
    public_result = _run([awg, "pubkey"], input_text=private + "\n")
    public = public_result.stdout.strip()
    if public_result.returncode != 0 or not public:
        raise AWGPanelError(public_result.stderr.strip() or "Не удалось получить открытый ключ AWG")
    return private, public


def _psk() -> str:
    awg = _command_path("awg")
    if not awg:
        raise AWGPanelError("Команда awg не найдена")
    result = _run([awg, "genpsk"])
    value = result.stdout.strip()
    if result.returncode != 0 or not value:
        raise AWGPanelError(result.stderr.strip() or "Не удалось создать PresharedKey")
    return value


def _random_header_ranges() -> tuple[str, str, str, str]:
    # Four separated segments guarantee that H1-H4 ranges never overlap.
    segments = (
        (100_000_000, 850_000_000),
        (950_000_000, 1_700_000_000),
        (1_800_000_000, 2_650_000_000),
        (2_750_000_000, 3_900_000_000),
    )
    ranges: list[str] = []
    for low, high in segments:
        width = secrets.randbelow(8_000_001) + 2_000_000
        start = random.SystemRandom().randint(low, high - width)
        ranges.append(f"{start}-{start + width}")
    random.SystemRandom().shuffle(ranges)
    return tuple(ranges)  # type: ignore[return-value]


def _validate_header(value: str, field: str) -> str:
    value = value.strip()
    match = re.fullmatch(r"(\d+)(?:-(\d+))?", value)
    if not match:
        raise ValueError(f"{field}: укажите число или диапазон start-end")
    start = int(match.group(1))
    end = int(match.group(2) or start)
    if not (0 <= start <= end <= 4_294_967_295):
        raise ValueError(f"{field}: диапазон должен находиться в пределах uint32")
    return f"{start}-{end}" if start != end else str(start)


def _validate_signature(value: str, field: str) -> str:
    value = value.strip()
    if len(value) > 4096:
        raise ValueError(f"{field}: значение слишком длинное")
    if "\n" in value or "\r" in value:
        raise ValueError(f"{field}: перевод строки недопустим")
    return value


def _validate_endpoint_host(value: object) -> str:
    endpoint_host = str(value).strip()
    if not endpoint_host:
        raise ValueError("Укажите публичный IP или домен AmneziaWG-сервера")
    if any(ch.isspace() for ch in endpoint_host) or any(ch in endpoint_host for ch in "/?#@"):
        raise ValueError("Endpoint должен содержать только IP-адрес или доменное имя без порта")
    candidate = endpoint_host[1:-1] if endpoint_host.startswith("[") and endpoint_host.endswith("]") else endpoint_host
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        pass
    if ":" in candidate:
        raise ValueError("UDP-порт задаётся отдельным полем")
    try:
        ascii_name = candidate.encode("idna").decode("ascii").rstrip(".")
    except UnicodeError as exc:
        raise ValueError("Некорректное доменное имя AmneziaWG-сервера") from exc
    if len(ascii_name) > 253:
        raise ValueError("Доменное имя слишком длинное")
    labels = ascii_name.split(".")
    if any(
        not label
        or len(label) > 63
        or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
        for label in labels
    ):
        raise ValueError("Некорректное доменное имя AmneziaWG-сервера")
    return ascii_name.lower()


def _validate_dns_servers(value: object) -> str:
    raw = str(value).strip()
    if not raw:
        raise ValueError("Укажите хотя бы один DNS-сервер")
    items = [item.strip() for item in raw.split(",")]
    if not 1 <= len(items) <= 4 or any(not item for item in items):
        raise ValueError("Укажите от одного до четырёх DNS IP-адресов через запятую")
    normalized: list[str] = []
    for item in items:
        try:
            normalized.append(str(ipaddress.ip_address(item)))
        except ValueError as exc:
            raise ValueError(f"Некорректный DNS IP-адрес: {item}") from exc
    return ", ".join(normalized)


def _validate_allowed_ips(value: object) -> str:
    raw = str(value).strip()
    if not raw:
        raise ValueError("AllowedIPs не может быть пустым")
    items = [item.strip() for item in raw.split(",")]
    if not 1 <= len(items) <= 32 or any(not item for item in items):
        raise ValueError("Укажите от одной до 32 сетей CIDR через запятую")
    normalized: list[str] = []
    for item in items:
        try:
            normalized.append(str(ipaddress.ip_network(item, strict=False)))
        except ValueError as exc:
            raise ValueError(f"Некорректная сеть AllowedIPs: {item}") from exc
    return ", ".join(normalized)


def _validate_settings(values: dict[str, object]) -> dict[str, object]:
    interface_name = str(values.get("interface_name", "awg0")).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", interface_name):
        raise ValueError("Имя интерфейса должно содержать до 15 латинских символов, цифр, . _ или -")
    endpoint_host = _validate_endpoint_host(values.get("endpoint_host", ""))
    listen_port = int(values.get("listen_port", 585))
    if not 1 <= listen_port <= 65535:
        raise ValueError("UDP-порт должен быть от 1 до 65535")
    try:
        network = ipaddress.ip_network(str(values.get("server_network", "10.77.0.0/24")).strip(), strict=True)
    except ValueError as exc:
        raise ValueError("Сеть клиентов должна быть IPv4 CIDR, например 10.77.0.0/24") from exc
    if network.version != 4 or network.prefixlen < 16 or network.prefixlen > 30:
        raise ValueError("Для Alpha 1 используйте частную IPv4-сеть от /16 до /30")
    if not network.is_private:
        raise ValueError("Сеть клиентов должна быть частной IPv4-сетью")
    dns_servers = _validate_dns_servers(values.get("dns_servers", "1.1.1.1, 1.0.0.1"))
    mtu = int(values.get("mtu", 1280))
    if not 576 <= mtu <= 1500:
        raise ValueError("MTU должен быть от 576 до 1500")
    external_interface = str(values.get("external_interface", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,32}", external_interface):
        raise ValueError("Укажите внешний сетевой интерфейс, например eth0 или ens5")

    jc = int(values.get("jc", 6))
    jmin = int(values.get("jmin", 64))
    jmax = int(values.get("jmax", 128))
    s1 = int(values.get("s1", 48))
    s2 = int(values.get("s2", 48))
    s3 = int(values.get("s3", 32))
    s4 = int(values.get("s4", 16))
    if not 0 <= jc <= 10:
        raise ValueError("Jc должен быть от 0 до 10 для AWG 2.0")
    if not 64 <= jmin < jmax <= 1024:
        raise ValueError("Должно выполняться 64 ≤ Jmin < Jmax ≤ 1024")
    if not 0 <= s1 <= 64 or not 0 <= s2 <= 64 or not 0 <= s3 <= 64 or not 0 <= s4 <= 32:
        raise ValueError("Для AWG 2.0 S1-S3 должны быть 0-64, а S4 — 0-32")

    headers = [_validate_header(str(values.get(f"h{i}", "")), f"H{i}") for i in range(1, 5)]
    parsed: list[tuple[int, int]] = []
    for value in headers:
        parts = value.split("-", 1)
        parsed.append((int(parts[0]), int(parts[-1])))
    for index, current in enumerate(parsed):
        for other in parsed[index + 1 :]:
            if max(current[0], other[0]) <= min(current[1], other[1]):
                raise ValueError("Диапазоны H1-H4 не должны пересекаться")

    return {
        "interface_name": interface_name,
        "endpoint_host": endpoint_host,
        "listen_port": listen_port,
        "server_network": str(network),
        "dns_servers": dns_servers,
        "mtu": mtu,
        "external_interface": external_interface,
        "isolate_clients": 1 if str(values.get("isolate_clients", "1")).lower() in {"1", "true", "yes", "on"} else 0,
        "jc": jc,
        "jmin": jmin,
        "jmax": jmax,
        "s1": s1,
        "s2": s2,
        "s3": s3,
        "s4": s4,
        **{f"h{i}": headers[i - 1] for i in range(1, 5)},
        **{f"i{i}": _validate_signature(str(values.get(f"i{i}", "")), f"I{i}") for i in range(1, 6)},
    }


def get_awg_settings():
    init_db()
    with connect() as con:
        row = con.execute("SELECT * FROM awg_settings WHERE id=1").fetchone()
    if row is None:
        raise AWGPanelError("Настройки AmneziaWG не найдены")
    return row


def list_awg_clients(*, enabled_only: bool = False):
    init_db()
    query = "SELECT * FROM awg_clients"
    if enabled_only:
        query += " WHERE enabled=1"
    query += " ORDER BY id"
    with connect() as con:
        return con.execute(query).fetchall()


def find_awg_client(client_id: int):
    init_db()
    with connect() as con:
        row = con.execute("SELECT * FROM awg_clients WHERE id=?", (client_id,)).fetchone()
    if row is None:
        raise AWGPanelError("Клиент AmneziaWG не найден")
    return row


def detect_external_interface() -> str:
    ip = _command_path("ip")
    if not ip:
        return ""
    result = _run([ip, "route", "show", "default"])
    if result.returncode != 0:
        return ""
    match = re.search(r"\bdev\s+(\S+)", result.stdout)
    return match.group(1) if match else ""


def _url_text(request: urllib.request.Request | str, timeout: float = 2.5) -> str:
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
        return response.read(256).decode("ascii", errors="ignore").strip()


def detect_public_ipv4(*, force: bool = False) -> str:
    """Best-effort public IPv4 detection without making it a hard dependency."""
    global _PUBLIC_IP_CACHE
    cached, cached_at = _PUBLIC_IP_CACHE
    if not force and cached and time.time() - cached_at < 300:
        return cached

    def accept(candidate: str) -> str:
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            return ""
        if address.version != 4 or address.is_private:
            return ""
        value = str(address)
        _PUBLIC_IP_CACHE = (value, time.time())
        return value

    try:
        token_request = urllib.request.Request(
            "http://169.254.169.254/latest/api/token",
            method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
        )
        token = _url_text(token_request, timeout=1.0)
        if token:
            ip_request = urllib.request.Request(
                "http://169.254.169.254/latest/meta-data/public-ipv4",
                headers={"X-aws-ec2-metadata-token": token},
            )
            detected = accept(_url_text(ip_request, timeout=1.0))
            if detected:
                return detected
    except (OSError, urllib.error.URLError, ValueError):
        pass

    for url in ("https://checkip.amazonaws.com", "https://api.ipify.org"):
        try:
            detected = accept(_url_text(url))
        except (OSError, urllib.error.URLError, ValueError):
            continue
        if detected:
            return detected
    return cached if not force else ""


def _safe_reason(reason: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", reason).strip("-")
    return cleaned[:48] or "change"


def _backup_state(reason: str) -> Path:
    _require_root()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(BACKUP_DIR, 0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    target = BACKUP_DIR / f"{stamp}-{_safe_reason(reason)}"
    target.mkdir(mode=0o700)

    if db.DB_PATH.exists():
        destination = sqlite3.connect(target / "panel.db")
        try:
            with connect() as source:
                source.backup(destination)
        finally:
            destination.close()
        os.chmod(target / "panel.db", 0o600)

    config_existed = AWG_CONFIG_PATH.exists()
    if config_existed:
        shutil.copy2(AWG_CONFIG_PATH, target / "awg0.conf")
        os.chmod(target / "awg0.conf", 0o600)

    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "config_existed": config_existed,
        "service_state": awg_service_state(),
    }
    (target / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.chmod(target / "metadata.json", 0o600)

    backups = sorted((item for item in BACKUP_DIR.iterdir() if item.is_dir()), reverse=True)
    for old in backups[max(1, BACKUP_KEEP):]:
        shutil.rmtree(old, ignore_errors=True)
    return target


def _restore_backup(backup: Path) -> None:
    metadata_path = backup / "metadata.json"
    metadata = {}
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            metadata = {}

    backup_db = backup / "panel.db"
    if backup_db.exists():
        db.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            Path(str(db.DB_PATH) + suffix).unlink(missing_ok=True)
        shutil.copy2(backup_db, db.DB_PATH)
        os.chmod(db.DB_PATH, 0o600)

    backup_config = backup / "awg0.conf"
    AWG_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if backup_config.exists():
        shutil.copy2(backup_config, AWG_CONFIG_PATH)
        os.chmod(AWG_CONFIG_PATH, 0o600)
    elif not metadata.get("config_existed", False):
        AWG_CONFIG_PATH.unlink(missing_ok=True)

    previous_state = str(metadata.get("service_state", "inactive"))
    if previous_state == "active" and AWG_CONFIG_PATH.exists():
        _run(["systemctl", "restart", AWG_SERVICE], timeout=30)
    else:
        _run(["systemctl", "stop", AWG_SERVICE], timeout=30)


def list_backups(limit: int = 10) -> list[dict[str, object]]:
    if not BACKUP_DIR.exists():
        return []
    result: list[dict[str, object]] = []
    for item in sorted((path for path in BACKUP_DIR.iterdir() if path.is_dir()), reverse=True)[:limit]:
        metadata = {}
        try:
            metadata = json.loads((item / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
        result.append({"name": item.name, "path": str(item), **metadata})
    return result


def _server_address(settings) -> str:
    network = ipaddress.ip_network(settings["server_network"], strict=True)
    return f"{next(network.hosts())}/{network.prefixlen}"


def _next_client_address(settings) -> str:
    network = ipaddress.ip_network(settings["server_network"], strict=True)
    used = {str(row["address"]).split("/", 1)[0] for row in list_awg_clients()}
    hosts = network.hosts()
    next(hosts, None)  # server gets the first usable address
    for host in hosts:
        if str(host) not in used:
            return f"{host}/32"
    raise AWGPanelError("В сети AmneziaWG больше нет свободных адресов")


def _obfuscation_lines(settings) -> list[str]:
    lines = [
        f"Jc = {settings['jc']}",
        f"Jmin = {settings['jmin']}",
        f"Jmax = {settings['jmax']}",
        f"S1 = {settings['s1']}",
        f"S2 = {settings['s2']}",
        f"S3 = {settings['s3']}",
        f"S4 = {settings['s4']}",
        f"H1 = {settings['h1']}",
        f"H2 = {settings['h2']}",
        f"H3 = {settings['h3']}",
        f"H4 = {settings['h4']}",
    ]
    for number in range(1, 6):
        value = str(settings[f"i{number}"]).strip()
        if value:
            lines.append(f"I{number} = {value}")
    return lines


def render_awg_server_config() -> str:
    settings = get_awg_settings()
    if not settings["configured"]:
        raise AWGPanelError("AmneziaWG ещё не настроен")
    ext = settings["external_interface"]
    interface_name = settings["interface_name"]
    up_commands: list[str] = []
    down_commands: list[str] = []
    if bool(settings["isolate_clients"]):
        up_commands.append(f"iptables -I FORWARD 1 -i {interface_name} -o {interface_name} -j DROP")
        down_commands.append(f"iptables -D FORWARD -i {interface_name} -o {interface_name} -j DROP")
    up_commands.extend([
        f"iptables -A FORWARD -i {interface_name} -j ACCEPT",
        f"iptables -A FORWARD -o {interface_name} -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT",
        f"iptables -t nat -A POSTROUTING -s {settings['server_network']} -o {ext} -j MASQUERADE",
    ])
    down_commands.extend([
        f"iptables -D FORWARD -i {interface_name} -j ACCEPT",
        f"iptables -D FORWARD -o {interface_name} -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT",
        f"iptables -t nat -D POSTROUTING -s {settings['server_network']} -o {ext} -j MASQUERADE",
    ])
    lines = [
        "[Interface]",
        f"Address = {_server_address(settings)}",
        f"ListenPort = {settings['listen_port']}",
        f"PrivateKey = {settings['private_key']}",
        f"MTU = {settings['mtu']}",
        *_obfuscation_lines(settings),
        "",
        "PostUp = " + "; ".join(up_commands),
        "PostDown = " + "; ".join(down_commands),
    ]
    for client in list_awg_clients(enabled_only=True):
        lines.extend(
            [
                "",
                "[Peer]",
                f"# {client['name']}",
                f"PublicKey = {client['public_key']}",
                f"PresharedKey = {client['preshared_key']}",
                f"AllowedIPs = {client['address']}",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def render_awg_client_config(client_id: int) -> str:
    settings = get_awg_settings()
    client = find_awg_client(client_id)
    if not settings["configured"]:
        raise AWGPanelError("AmneziaWG ещё не настроен")
    endpoint_host = settings["endpoint_host"]
    if ":" in endpoint_host and not endpoint_host.startswith("["):
        endpoint_host = f"[{endpoint_host}]"
    lines = [
        "[Interface]",
        f"Address = {client['address']}",
        f"DNS = {settings['dns_servers']}",
        f"PrivateKey = {client['private_key']}",
        f"MTU = {settings['mtu']}",
        *_obfuscation_lines(settings),
        "",
        "[Peer]",
        f"PublicKey = {settings['public_key']}",
        f"PresharedKey = {client['preshared_key']}",
        f"AllowedIPs = {client['allowed_ips']}",
        f"Endpoint = {endpoint_host}:{settings['listen_port']}",
        "PersistentKeepalive = 25",
    ]
    return "\n".join(lines).rstrip() + "\n"


def _write_server_config() -> Path:
    _require_root()
    AWG_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(AWG_CONFIG_DIR, 0o700)
    # awg-quick accepts only <valid-interface-name>.conf. A suffix such as
    # awg0.conf.tmp is rejected before validation begins.
    temporary = AWG_CONFIG_DIR / "awgtest0.conf"
    temporary.write_text(render_awg_server_config(), encoding="utf-8")
    os.chmod(temporary, 0o600)
    try:
        awg_quick = _command_path("awg-quick")
        if awg_quick:
            validation = _run([awg_quick, "strip", str(temporary)], timeout=15)
            if validation.returncode != 0:
                raise AWGPanelError(
                    validation.stderr.strip()
                    or validation.stdout.strip()
                    or "awg-quick не принял сформированную конфигурацию"
                )
        temporary.replace(AWG_CONFIG_PATH)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    os.chmod(AWG_CONFIG_PATH, 0o600)
    return AWG_CONFIG_PATH


def _configure_awg_impl(**values):
    current = get_awg_settings()
    if not values.get("endpoint_host"):
        values["endpoint_host"] = detect_public_ipv4()
    if not values.get("external_interface"):
        values["external_interface"] = detect_external_interface()
    if current["configured"]:
        for key in ("h1", "h2", "h3", "h4"):
            if not str(values.get(key, "")).strip():
                values[key] = current[key]
    else:
        h1, h2, h3, h4 = _random_header_ranges()
        generated = {"h1": h1, "h2": h2, "h3": h3, "h4": h4}
        for key, value in generated.items():
            if not str(values.get(key, "")).strip():
                values[key] = value
    for key, default in {
        "interface_name": "awg0", "listen_port": 585,
        "server_network": "10.77.0.0/24", "dns_servers": "1.1.1.1, 1.0.0.1",
        "mtu": 1280, "jc": 6, "jmin": 64, "jmax": 128,
        "s1": 48, "s2": 48, "s3": 32, "s4": 16,
        "isolate_clients": 1,
        "i1": "", "i2": "", "i3": "", "i4": "", "i5": "",
    }.items():
        values.setdefault(key, current[key] if current["configured"] else default)
    validated = _validate_settings(values)
    private_key = current["private_key"]
    public_key = current["public_key"]
    if not private_key or not public_key:
        private_key, public_key = _keypair()
    with connect() as con:
        con.execute(
            """
            UPDATE awg_settings SET configured=1, interface_name=?, endpoint_host=?,
                listen_port=?, server_network=?, dns_servers=?, mtu=?, external_interface=?,
                private_key=?, public_key=?, jc=?, jmin=?, jmax=?, s1=?, s2=?, s3=?, s4=?,
                h1=?, h2=?, h3=?, h4=?, i1=?, i2=?, i3=?, i4=?, i5=?,
                isolate_clients=?, updated_at=CURRENT_TIMESTAMP WHERE id=1
            """,
            (
                validated["interface_name"], validated["endpoint_host"],
                validated["listen_port"], validated["server_network"],
                validated["dns_servers"], validated["mtu"], validated["external_interface"],
                private_key, public_key, validated["jc"], validated["jmin"],
                validated["jmax"], validated["s1"], validated["s2"],
                validated["s3"], validated["s4"], validated["h1"],
                validated["h2"], validated["h3"], validated["h4"],
                validated["i1"], validated["i2"], validated["i3"],
                validated["i4"], validated["i5"], validated["isolate_clients"],
            ),
        )
    _write_server_config()
    return get_awg_settings()


def configure_awg(**values):
    _require_root()
    backup = _backup_state("server-settings")
    try:
        return _configure_awg_impl(**values)
    except Exception:
        _restore_backup(backup)
        raise


def configure_and_start_awg(**values) -> tuple[object, str]:
    _require_root()
    backup = _backup_state("server-apply")
    previous_state = awg_service_state()
    try:
        settings = _configure_awg_impl(**values)
        state = _service_action("restart" if previous_state == "active" else "start")
        if state != "active":
            raise AWGPanelError(f"Служба AmneziaWG не запустилась: {state}")
        return settings, state
    except Exception:
        _restore_backup(backup)
        raise


def add_awg_client(name: str, comment: str = ""):
    _require_root()
    settings = get_awg_settings()
    if not settings["configured"]:
        raise AWGPanelError("Сначала настройте сервер AmneziaWG")
    name = name.strip()
    if not AWG_NAME_RE.fullmatch(name):
        raise ValueError("Имя клиента должно содержать от 1 до 64 обычных символов")
    backup = _backup_state("client-add")
    try:
        private_key, public_key = _keypair()
        preshared_key = _psk()
        address = _next_client_address(settings)
        try:
            with connect() as con:
                cursor = con.execute(
                    """
                    INSERT INTO awg_clients
                        (name, address, private_key, public_key, preshared_key, comment,
                         allowed_ips, access_token, access_enabled)
                    VALUES (?, ?, ?, ?, ?, ?, '0.0.0.0/0', ?, 1)
                    """,
                    (name, address, private_key, public_key, preshared_key, comment.strip(),
                     secrets.token_urlsafe(24)),
                )
                client_id = int(cursor.lastrowid)
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise AWGPanelError("Клиент с таким именем уже существует") from exc
            raise
        _write_server_config()
        _reload_if_active()
        return find_awg_client(client_id)
    except Exception:
        _restore_backup(backup)
        raise


def set_awg_client_enabled(client_id: int, enabled: bool):
    _require_root()
    find_awg_client(client_id)
    backup = _backup_state("client-toggle")
    try:
        with connect() as con:
            con.execute(
                "UPDATE awg_clients SET enabled=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (1 if enabled else 0, client_id),
            )
        _write_server_config()
        _reload_if_active()
        return find_awg_client(client_id)
    except Exception:
        _restore_backup(backup)
        raise


def regenerate_awg_client(client_id: int):
    _require_root()
    find_awg_client(client_id)
    backup = _backup_state("client-regenerate")
    try:
        private_key, public_key = _keypair()
        preshared_key = _psk()
        with connect() as con:
            con.execute(
                """
                UPDATE awg_clients
                SET private_key=?, public_key=?, preshared_key=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (private_key, public_key, preshared_key, client_id),
            )
        _write_server_config()
        _reload_if_active()
        return find_awg_client(client_id)
    except Exception:
        _restore_backup(backup)
        raise


def delete_awg_client(client_id: int):
    _require_root()
    client = find_awg_client(client_id)
    backup = _backup_state("client-delete")
    try:
        with connect() as con:
            con.execute("DELETE FROM awg_clients WHERE id=?", (client_id,))
        _write_server_config()
        _reload_if_active()
        return client
    except Exception:
        _restore_backup(backup)
        raise


def update_awg_client_routing(client_id: int, allowed_ips: str):
    _require_root()
    find_awg_client(client_id)
    normalized = _validate_allowed_ips(allowed_ips)
    backup = _backup_state("client-routing")
    try:
        with connect() as con:
            con.execute(
                "UPDATE awg_clients SET allowed_ips=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (normalized, client_id),
            )
        return find_awg_client(client_id)
    except Exception:
        _restore_backup(backup)
        raise


def update_routing_settings(*, isolate_clients: bool):
    _require_root()
    backup = _backup_state("routing-settings")
    try:
        with connect() as con:
            con.execute(
                "UPDATE awg_settings SET isolate_clients=?, updated_at=CURRENT_TIMESTAMP WHERE id=1",
                (1 if isolate_clients else 0,),
            )
        _write_server_config()
        _reload_if_active()
        return get_awg_settings()
    except Exception:
        _restore_backup(backup)
        raise


def update_dns_servers(dns_servers: str):
    _require_root()
    normalized = _validate_dns_servers(dns_servers)
    backup = _backup_state("dns-settings")
    try:
        with connect() as con:
            con.execute(
                "UPDATE awg_settings SET dns_servers=?, updated_at=CURRENT_TIMESTAMP WHERE id=1",
                (normalized,),
            )
        return get_awg_settings()
    except Exception:
        _restore_backup(backup)
        raise


def set_client_access_enabled(client_id: int, enabled: bool):
    _require_root()
    find_awg_client(client_id)
    with connect() as con:
        con.execute(
            "UPDATE awg_clients SET access_enabled=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (1 if enabled else 0, client_id),
        )
    return find_awg_client(client_id)


def regenerate_client_access_token(client_id: int):
    _require_root()
    find_awg_client(client_id)
    with connect() as con:
        con.execute(
            "UPDATE awg_clients SET access_token=?, access_downloads=0, access_last_at=NULL, "
            "access_enabled=1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (secrets.token_urlsafe(24), client_id),
        )
    return find_awg_client(client_id)


def find_client_by_access_token(token: str):
    init_db()
    if not token or len(token) > 256:
        raise AWGPanelError("Ссылка доступа недействительна")
    with connect() as con:
        row = con.execute(
            "SELECT * FROM awg_clients WHERE access_token=? AND access_enabled=1 AND enabled=1",
            (token,),
        ).fetchone()
    if row is None:
        raise AWGPanelError("Ссылка доступа недействительна или отключена")
    return row


def record_client_access(client_id: int) -> None:
    with connect() as con:
        con.execute(
            "UPDATE awg_clients SET access_downloads=access_downloads+1, "
            "access_last_at=CURRENT_TIMESTAMP WHERE id=?",
            (client_id,),
        )


def create_manual_backup() -> Path:
    return _backup_state("manual")


def restore_backup(name: str) -> Path:
    _require_root()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise AWGPanelError("Некорректное имя резервной копии")
    backup = (BACKUP_DIR / name).resolve()
    if backup.parent != BACKUP_DIR.resolve() or not backup.is_dir():
        raise AWGPanelError("Резервная копия не найдена")
    safety = _backup_state("before-restore")
    try:
        _restore_backup(backup)
        if get_awg_settings()["configured"]:
            _write_server_config()
            _reload_if_active()
        return backup
    except Exception:
        _restore_backup(safety)
        raise


def _service_action(action: str) -> str:
    _require_root()
    if action not in {"start", "stop", "restart"}:
        raise ValueError("Недопустимое действие службы")
    result = _run(["systemctl", action, AWG_SERVICE], timeout=30)
    if result.returncode != 0:
        raise AWGPanelError(result.stderr.strip() or result.stdout.strip() or f"systemctl {action} завершился ошибкой")
    return awg_service_state()


def start_awg() -> str:
    if not AWG_CONFIG_PATH.exists():
        _write_server_config()
    return _service_action("start")


def stop_awg() -> str:
    return _service_action("stop")


def restart_awg() -> str:
    _write_server_config()
    return _service_action("restart")


def _reload_if_active() -> None:
    if awg_service_state() == "active":
        restart_awg()


def awg_service_state() -> str:
    result = _run(["systemctl", "is-active", AWG_SERVICE])
    return result.stdout.strip() or "inactive"


def _peer_stats() -> dict[str, dict[str, int]]:
    settings = get_awg_settings()
    if not settings["configured"] or not _command_path("awg"):
        return {}
    result = _run(["awg", "show", settings["interface_name"], "dump"])
    if result.returncode != 0:
        return {}
    stats: dict[str, dict[str, int]] = {}
    lines = result.stdout.splitlines()[1:]
    for line in lines:
        parts = line.split("\t")
        if len(parts) < 8:
            continue
        try:
            stats[parts[0]] = {
                "latest_handshake": int(parts[4] or 0),
                "rx": int(parts[5] or 0),
                "tx": int(parts[6] or 0),
            }
        except ValueError:
            continue
    return stats


def _format_handshake(timestamp: int) -> str:
    if timestamp <= 0:
        return "—"
    moment = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    age = max(0, int(time.time()) - timestamp)
    if age < 60:
        age_text = f"{age} с назад"
    elif age < 3600:
        age_text = f"{age // 60} мин назад"
    elif age < 86400:
        age_text = f"{age // 3600} ч назад"
    else:
        age_text = f"{age // 86400} дн назад"
    return f"{moment:%Y-%m-%d %H:%M UTC} · {age_text}"


def _system_resources() -> dict[str, object]:
    try:
        load1, load5, load15 = os.getloadavg()
    except OSError:
        load1 = load5 = load15 = 0.0
    total_kib = available_kib = 0
    try:
        values: dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, raw = line.split(":", 1)
            values[key] = int(raw.strip().split()[0])
        total_kib = values.get("MemTotal", 0)
        available_kib = values.get("MemAvailable", 0)
    except (OSError, ValueError, IndexError):
        pass
    used_kib = max(0, total_kib - available_kib)
    percent = round((used_kib / total_kib * 100), 1) if total_kib else 0.0
    return {
        "load1": round(load1, 2),
        "load5": round(load5, 2),
        "load15": round(load15, 2),
        "memory_total": total_kib * 1024,
        "memory_used": used_kib * 1024,
        "memory_percent": percent,
    }


def _format_bytes(value: int) -> str:
    size = float(max(0, value))
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit in {"B", "KiB"} else f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


def _service_main_pid(service: str) -> int:
    result = _run(["systemctl", "show", "-p", "MainPID", "--value", service])
    try:
        return int(result.stdout.strip() or 0)
    except ValueError:
        return 0


def _process_rss(pid: int) -> int:
    if pid <= 0:
        return 0
    try:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def _udp_listener(listen_port: int) -> dict[str, object]:
    ss = _command_path("ss")
    if not ss:
        return {"listening": False, "lines": [], "error": "Команда ss не найдена"}
    result = _run([ss, "-H", "-lunp"], timeout=10)
    if result.returncode != 0:
        return {
            "listening": False,
            "lines": [],
            "error": result.stderr.strip() or "Не удалось проверить UDP-порт",
        }
    marker = f":{listen_port}"
    lines = [line for line in result.stdout.splitlines() if marker in line]
    return {"listening": bool(lines), "lines": lines, "error": ""}


def _service_logs(service: str, lines: int = 80) -> str:
    journalctl = _command_path("journalctl")
    if not journalctl:
        return "journalctl не найден"
    result = _run(
        [journalctl, "-u", service, "-n", str(lines), "--no-pager", "--output=short-iso"],
        timeout=15,
    )
    text = result.stdout.strip() or result.stderr.strip()
    return text[-24_000:] if text else "Журнал пока пуст."




def _systemctl_enabled(service: str) -> bool:
    result = _run(["systemctl", "is-enabled", service], timeout=10)
    return result.returncode == 0 and result.stdout.strip() in {"enabled", "static", "indirect"}


def _service_uptime(service: str) -> str:
    result = _run(
        ["systemctl", "show", service, "-p", "ActiveEnterTimestampMonotonic", "--value"],
        timeout=10,
    )
    try:
        started = int(result.stdout.strip() or 0) / 1_000_000
    except ValueError:
        return "—"
    if started <= 0:
        return "—"
    age = max(0, int(time.monotonic() - started))
    days, remainder = divmod(age, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)
    if days:
        return f"{days} д {hours} ч"
    if hours:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"


def _ip_forward_enabled() -> bool:
    try:
        return Path("/proc/sys/net/ipv4/ip_forward").read_text(encoding="ascii").strip() == "1"
    except OSError:
        return False


def _nat_rule_present(settings) -> bool:
    iptables = _command_path("iptables")
    if not iptables or not settings["configured"]:
        return False
    result = _run(
        [
            iptables, "-t", "nat", "-C", "POSTROUTING",
            "-s", str(settings["server_network"]),
            "-o", str(settings["external_interface"]),
            "-j", "MASQUERADE",
        ],
        timeout=10,
    )
    return result.returncode == 0


def _redact_diagnostic_text(text: str) -> str:
    redacted = re.sub(
        r"(?im)^(PrivateKey|PresharedKey|PublicKey)\s*=\s*.+$",
        r"\1 = [REDACTED]",
        text,
    )
    redacted = re.sub(r"(?i)(/a/)[A-Za-z0-9_-]{16,}", r"\1[REDACTED]", redacted)
    return redacted[-16_000:]


def build_diagnostic_report() -> str:
    diagnostics = get_awg_diagnostics()
    resources = diagnostics["resources"]
    lines = [
        "SG-AWG-Panel diagnostic report",
        f"Generated UTC: {datetime.now(timezone.utc).isoformat()}",
        "",
        f"AWG service: {diagnostics['service_state']}",
        f"AWG enabled at boot: {diagnostics['awg_enabled']}",
        f"AWG uptime: {diagnostics['awg_uptime']}",
        f"Panel service: {diagnostics['panel_state']}",
        f"Panel enabled at boot: {diagnostics['panel_enabled']}",
        f"Panel uptime: {diagnostics['panel_uptime']}",
        f"Panel RSS: {diagnostics['panel_rss_text']}",
        f"Kernel module loaded: {diagnostics['module_loaded']}",
        f"AWG tools installed: {diagnostics['installed']}",
        f"Interface present: {diagnostics['interface_present']}",
        f"UDP {diagnostics['listen_port']} listening: {diagnostics['udp']['listening']}",
        f"IPv4 forwarding: {diagnostics['ip_forward']}",
        f"NAT masquerade rule: {diagnostics['nat_rule']}",
        f"Boot persistence ready: {diagnostics['boot_ready']}",
        f"Public IPv4: {diagnostics['public_ipv4'] or 'unknown'}",
        f"External interface: {diagnostics['external_interface'] or 'unknown'}",
        f"System memory used: {resources['memory_percent']}%",
        f"Load average: {resources['load1']} / {resources['load5']} / {resources['load15']}",
        f"Config exists: {diagnostics['config_exists']}",
        f"Config path: {diagnostics['config_path']}",
        f"Backups: {len(diagnostics['backups'])}",
        "",
        "--- sg-awg-server journal ---",
        _redact_diagnostic_text(str(diagnostics['server_logs'])),
        "",
        "--- sg-awg-panel journal ---",
        _redact_diagnostic_text(str(diagnostics['panel_logs'])),
        "",
    ]
    return "\n".join(lines)


def get_awg_diagnostics() -> dict[str, object]:
    settings = get_awg_settings()
    state = awg_service_state()
    panel_state = _run(["systemctl", "is-active", "sg-awg-panel"]).stdout.strip() or "inactive"
    panel_pid = _service_main_pid("sg-awg-panel")
    panel_rss = _process_rss(panel_pid)
    listen_port = int(settings["listen_port"] or 585)
    panel_enabled = _systemctl_enabled("sg-awg-panel")
    awg_enabled = _systemctl_enabled(AWG_SERVICE)
    ip_forward = _ip_forward_enabled()
    nat_rule = _nat_rule_present(settings)
    config_exists = AWG_CONFIG_PATH.exists()
    interface_present = Path(f"/sys/class/net/{settings['interface_name']}").exists()
    udp = _udp_listener(listen_port)
    boot_ready = bool(panel_enabled and awg_enabled and config_exists and ip_forward)
    return {
        "service_state": state,
        "panel_state": panel_state,
        "panel_enabled": panel_enabled,
        "awg_enabled": awg_enabled,
        "panel_uptime": _service_uptime("sg-awg-panel"),
        "awg_uptime": _service_uptime(AWG_SERVICE),
        "module_loaded": Path("/sys/module/amneziawg").exists(),
        "installed": bool(_command_path("awg") and _command_path("awg-quick")),
        "interface_present": interface_present,
        "external_interface": detect_external_interface(),
        "public_ipv4": detect_public_ipv4(force=True),
        "udp": udp,
        "listen_port": listen_port,
        "config_path": str(AWG_CONFIG_PATH),
        "config_exists": config_exists,
        "ip_forward": ip_forward,
        "nat_rule": nat_rule,
        "boot_ready": boot_ready,
        "panel_pid": panel_pid,
        "panel_rss": panel_rss,
        "panel_rss_text": _format_bytes(panel_rss),
        "resources": _system_resources(),
        "server_logs": _service_logs(AWG_SERVICE),
        "panel_logs": _service_logs("sg-awg-panel"),
        "backups": list_backups(),
    }


def get_awg_overview() -> dict[str, object]:
    settings = get_awg_settings()
    installed = bool(_command_path("awg") and _command_path("awg-quick"))
    module_loaded = Path("/sys/module/amneziawg").exists()
    state = awg_service_state() if installed else "not-installed"
    stats = _peer_stats() if state == "active" else {}
    clients: list[dict[str, object]] = []
    for row in list_awg_clients():
        item = dict(row)
        item.update(stats.get(row["public_key"], {"latest_handshake": 0, "rx": 0, "tx": 0}))
        item["latest_handshake_text"] = _format_handshake(int(item["latest_handshake"]))
        item["rx_text"] = _format_bytes(int(item["rx"]))
        item["tx_text"] = _format_bytes(int(item["tx"]))
        clients.append(item)
    panel_pid = _service_main_pid("sg-awg-panel")
    panel_rss = _process_rss(panel_pid)
    endpoint_detected = "" if settings["endpoint_host"] else detect_public_ipv4()
    total_rx = sum(int(item["rx"]) for item in clients)
    total_tx = sum(int(item["tx"]) for item in clients)
    active_clients = sum(1 for item in clients if int(item["latest_handshake"]) > 0 and time.time() - int(item["latest_handshake"]) < 180)
    backups = list_backups(limit=1)
    return {
        "installed": installed,
        "module_loaded": module_loaded,
        "configured": bool(settings["configured"]),
        "service_state": state,
        "config_path": str(AWG_CONFIG_PATH),
        "settings": settings,
        "clients": clients,
        "external_interface_detected": detect_external_interface(),
        "public_ipv4_detected": endpoint_detected,
        "panel_rss": panel_rss,
        "panel_rss_text": _format_bytes(panel_rss),
        "resources": _system_resources(),
        "total_rx": total_rx,
        "total_tx": total_tx,
        "total_rx_text": _format_bytes(total_rx),
        "total_tx_text": _format_bytes(total_tx),
        "active_clients": active_clients,
        "latest_backup": backups[0] if backups else None,
    }

