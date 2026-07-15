#!/usr/bin/env python3
from __future__ import annotations

import ipaddress
import json
import os
import re
import platform
import shutil
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

AGENT_VERSION = "0.7.0-RC4"
ENV_FILE = Path(os.environ.get("SG_AWG_NODE_ENV", "/etc/sg-awg-node/agent.env"))
AWG_CONFIG_PATH = Path("/etc/amnezia/amneziawg/awg0.conf")
NODE_TRAFFIC_PATH = Path("/etc/sg-awg-node/traffic.nft")
CASCADE_STATE_PATH = Path("/etc/sg-awg-node/cascade.json")
CASCADE_CONFIG_PATH = Path("/etc/amnezia/amneziawg/sgcascade.conf")
CASCADE_INTERFACE = "sgcascade"
CASCADE_TABLE = 23000
CASCADE_MARK = 0x6200
CASCADE_PRIORITY = 13050
_COUNTRY_CACHE: tuple[str, float] = ("", 0.0)

KNOWN_UNITS = {
    "awg": "sg-awg-server.service",
    "traffic": "sg-awg-traffic.service",
    "nginx": "nginx.service",
}
JOB_TO_UNIT = {
    "restart_awg": KNOWN_UNITS["awg"],
    "restart_traffic": KNOWN_UNITS["traffic"],
    "restart_nginx": KNOWN_UNITS["nginx"],
}


def _read_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        result[key.strip()] = value
    return result


def _run(command: list[str], timeout: int = 12) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={**os.environ, "LC_ALL": "C.UTF-8"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    output = (completed.stdout + completed.stderr).strip()
    return completed.returncode, output[:16000]


def _service_state(unit: str) -> str:
    rc, text = _run(["systemctl", "is-active", unit], timeout=5)
    state = text.splitlines()[0].strip() if text else "unknown"
    return state if rc == 0 or state in {"inactive", "failed", "activating", "deactivating"} else "unknown"


def _os_release() -> dict[str, str]:
    values: dict[str, str] = {}
    path = Path("/etc/os-release")
    if path.exists():
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            values[key] = value.strip().strip('"')
    return values


def _cpu_percent() -> float:
    def sample() -> tuple[int, int]:
        fields = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
        numbers = [int(value) for value in fields]
        idle = numbers[3] + (numbers[4] if len(numbers) > 4 else 0)
        return sum(numbers), idle
    try:
        total1, idle1 = sample()
        time.sleep(0.15)
        total2, idle2 = sample()
        delta = max(1, total2 - total1)
        return round(100.0 * (1.0 - (idle2 - idle1) / delta), 1)
    except Exception:
        return 0.0


def _memory_percent() -> float:
    try:
        values: dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, raw = line.split(":", 1)
            values[key] = int(raw.strip().split()[0])
        total = values.get("MemTotal", 0)
        available = values.get("MemAvailable", values.get("MemFree", 0))
        return round(100.0 * (total - available) / total, 1) if total else 0.0
    except Exception:
        return 0.0


def _private_ipv4() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("1.1.1.1", 53))
            return str(sock.getsockname()[0])
        finally:
            sock.close()
    except OSError:
        return ""


def _public_ipv4() -> str:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for url in ("https://checkip.amazonaws.com", "https://api.ipify.org"):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "SG-AWG-Node-Agent/1"})
            with opener.open(req, timeout=3) as response:
                return response.read().decode("ascii", "ignore").strip()[:64]
        except Exception:
            continue
    return ""


def _country_code(public_ipv4: str) -> str:
    global _COUNTRY_CACHE
    cached, cached_at = _COUNTRY_CACHE
    if cached and time.time() - cached_at < 21600:
        return cached
    try:
        address = ipaddress.ip_address(str(public_ipv4 or "").strip())
        if address.version != 4 or not address.is_global:
            return cached
    except ValueError:
        return cached
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    urls = (
        f"https://api.country.is/{address}",
        f"https://ipwho.is/{address}?fields=success,country_code",
    )
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "SG-AWG-Node-Agent/0.7.0-RC4", "Accept": "application/json"})
            with opener.open(req, timeout=3) as response:
                data = json.loads(response.read(4096).decode("utf-8", "replace"))
            value = (data.get("country") or data.get("country_code")) if isinstance(data, dict) else ""
            code = str(value or "").strip().upper()
            if re.fullmatch(r"[A-Z]{2}", code):
                _COUNTRY_CACHE = (code, time.time())
                return code
        except Exception:
            continue
    return cached


def _version(command: list[str]) -> str:
    rc, output = _run(command, timeout=5)
    return output.splitlines()[0][:96] if rc == 0 and output else ""


def _awg_interface_values(config_text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    in_interface = False
    for raw in config_text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            in_interface = line.casefold() == "[interface]"
            continue
        if not in_interface or not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip().casefold()] = value.strip()
    return values


def _awg_peer_metadata(config_text: str) -> dict[str, dict[str, Any]]:
    """Read peer routes and ownership markers from the persisted awg0.conf.

    Peers created by Controller always have the SG-AWG-CLIENT marker. Peers
    without that marker belong to the local server configuration and must be
    preserved. Their VPN addresses are reserved so Controller cannot assign
    the same /32 to a different managed client.
    """
    result: dict[str, dict[str, Any]] = {}
    block: list[str] = []
    in_peer = False

    def commit(lines: list[str]) -> None:
        if not lines:
            return
        values: dict[str, str] = {}
        marker = ""
        for raw in lines:
            line = raw.strip()
            if line.startswith("# SG-AWG-CLIENT id="):
                marker = line
            elif line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip().casefold()] = value.strip()
        public_key = values.get("publickey", "").strip()
        if not public_key:
            return
        managed_id = 0
        managed_name = ""
        match = re.search(r"# SG-AWG-CLIENT id=(\d+)\s+name=(.*?)\s+role=", marker)
        if match:
            managed_id = int(match.group(1))
            managed_name = match.group(2).strip()
        result[public_key] = {
            "allowed_ips": values.get("allowedips", "").strip(),
            "managed": bool(marker),
            "managed_id": managed_id,
            "name": managed_name,
        }

    for raw in config_text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            if in_peer:
                commit(block)
            in_peer = line.casefold() == "[peer]"
            block = [raw] if in_peer else []
            continue
        if in_peer:
            block.append(raw)
    if in_peer:
        commit(block)
    return result


def _awg_runtime() -> dict[str, Any]:
    config_text = ""
    try:
        config_text = AWG_CONFIG_PATH.read_text(encoding="utf-8")
    except OSError:
        pass
    interface = _awg_interface_values(config_text)
    address_text = str(interface.get("address") or "").split(",", 1)[0].strip()
    interface_address = ""
    server_network = ""
    try:
        parsed = ipaddress.ip_interface(address_text)
        interface_address = str(parsed)
        server_network = str(parsed.network)
    except ValueError:
        pass
    try:
        configured_port = int(interface.get("listenport") or 0)
    except ValueError:
        configured_port = 0

    rc_key, public_key = _run(["awg", "show", "awg0", "public-key"], timeout=5)
    rc_port, runtime_port_text = _run(["awg", "show", "awg0", "listen-port"], timeout=5)
    try:
        runtime_port = int(runtime_port_text.splitlines()[0]) if rc_port == 0 else 0
    except (ValueError, IndexError):
        runtime_port = 0

    peer_metadata = _awg_peer_metadata(config_text)
    peers: dict[str, dict[str, Any]] = {}
    rc_dump, dump = _run(["awg", "show", "awg0", "dump"], timeout=8)
    if rc_dump == 0:
        for index, line in enumerate(dump.splitlines()):
            fields = line.split("\t")
            if index == 0 or len(fields) < 8:
                continue
            key = fields[0].strip()
            metadata = peer_metadata.get(key, {})
            try:
                latest_handshake = int(fields[4] or 0)
                rx = int(fields[5] or 0)
                tx = int(fields[6] or 0)
            except ValueError:
                latest_handshake = rx = tx = 0
            peers[key] = {
                "latest_handshake": latest_handshake,
                "rx": rx,
                "tx": tx,
                "allowed_ips": fields[3].strip() or str(metadata.get("allowed_ips") or ""),
                "managed": bool(metadata.get("managed")),
                "managed_id": int(metadata.get("managed_id") or 0),
                "name": str(metadata.get("name") or ""),
            }
            if len(peers) >= 1000:
                break

    address_claims: list[dict[str, Any]] = []
    try:
        runtime_network = ipaddress.ip_network(server_network, strict=True)
    except ValueError:
        runtime_network = None
    if runtime_network is not None:
        for public_key, details in peers.items():
            for raw in str(details.get("allowed_ips") or "").split(","):
                try:
                    route = ipaddress.ip_network(raw.strip(), strict=False)
                except ValueError:
                    continue
                if route.version != 4 or route.prefixlen != 32 or route.network_address not in runtime_network:
                    continue
                address_claims.append({
                    "address": str(route.network_address),
                    "key": public_key,
                    "managed": bool(details.get("managed")),
                    "name": str(details.get("name") or ""),
                })

    masking: dict[str, Any] = {}
    for key in ("jc", "jmin", "jmax", "s1", "s2", "s3", "s4", "h1", "h2", "h3", "h4", "i1", "i2", "i3", "i4", "i5"):
        value = str(interface.get(key) or "").strip()
        if value:
            masking[key] = value
    try:
        mtu = int(interface.get("mtu") or 1280)
    except ValueError:
        mtu = 1280
    return {
        "configured": bool(config_text and interface_address and configured_port),
        "interface": "awg0",
        "interface_address": interface_address,
        "server_network": server_network,
        "listen_port": runtime_port or configured_port,
        "configured_listen_port": configured_port,
        "public_key": public_key.strip() if rc_key == 0 else "",
        "mtu": mtu,
        "masking": masking,
        "address_claims": address_claims,
        "peers": peers,
    }


def collect_metadata(*, include_public_ip: bool = False) -> dict[str, Any]:
    os_release = _os_release()
    usage = shutil.disk_usage("/")
    public_ipv4 = _public_ipv4() if include_public_ip else ""
    return {
        "agent_version": AGENT_VERSION,
        "os_name": os_release.get("NAME", platform.system()),
        "os_version": os_release.get("VERSION_ID", platform.release()),
        "kernel": platform.release(),
        "public_ipv4": public_ipv4,
        "private_ipv4": _private_ipv4(),
        "country_code": _country_code(public_ipv4) if public_ipv4 else _COUNTRY_CACHE[0],
        "awg_version": _version(["awg", "--version"]) or _version(["awg-quick", "--version"]),
        "panel_version": "",
        "cpu_percent": _cpu_percent(),
        "memory_percent": _memory_percent(),
        "disk_percent": round(100.0 * usage.used / usage.total, 1) if usage.total else 0.0,
        "load1": round(os.getloadavg()[0], 2) if hasattr(os, "getloadavg") else 0.0,
        "services": {name: _service_state(unit) for name, unit in KNOWN_UNITS.items()},
        "awg_runtime": _awg_runtime(),
        "capabilities": {
            "amneziawg": True,
            "metrics": True,
            "diagnostics": True,
            "safe_service_restart": True,
            "managed_clients": True,
            "arbitrary_shell": False,
        },
    }


def _ssl_context(insecure: bool) -> ssl.SSLContext | None:
    if not insecure:
        return None
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def api_request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    token: str,
    payload: dict[str, Any] | None = None,
    insecure: bool = False,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": f"SG-AWG-Node-Agent/{AGENT_VERSION}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20, context=_ssl_context(insecure)) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", "replace")[:1000]
        raise RuntimeError(f"Controller HTTP {exc.code}: {message}") from exc


def _validate_managed_peers(
    payload: dict[str, Any],
    runtime: dict[str, Any],
    *,
    reserved_addresses: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    expected = payload.get("expected") if isinstance(payload.get("expected"), dict) else {}
    try:
        expected_port = int(expected.get("listen_port") or 585)
    except (TypeError, ValueError) as exc:
        raise ValueError("Некорректный UDP-порт SG-Node") from exc
    if expected_port != 585:
        raise ValueError("SG-Node должна использовать стандартный UDP-порт 585")
    if int(runtime.get("listen_port") or 0) != expected_port:
        raise ValueError(
            f"Реальный ListenPort SG-Node: {runtime.get('listen_port') or 'не определён'}, ожидается 585"
        )
    # The authenticated SG-Node is authoritative for its current awg0 key.
    # A key may legitimately change after a clean reinstall or an awg0 rebuild
    # between the last heartbeat and this job. Rejecting the whole client sync
    # leaves the Node unusable. The actual key is returned in the verified job
    # result and Controller updates its runtime before exporting the profile.
    actual_key = str(runtime.get("public_key") or "").strip()
    if not actual_key:
        raise ValueError("Не удалось определить публичный ключ работающего awg0")
    expected_network = str(expected.get("server_network") or "").strip()
    if not expected_network or str(runtime.get("server_network") or "") != expected_network:
        raise ValueError("Сеть работающего awg0 не совпадает с данными Controller")

    raw_peers = payload.get("peers")
    if not isinstance(raw_peers, list) or len(raw_peers) > 1000:
        raise ValueError("Некорректный список клиентов SG-Node")
    network = ipaddress.ip_network(expected_network, strict=True)
    result: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    seen_keys: set[str] = set()
    seen_addresses: set[str] = set()
    for item in raw_peers:
        if not isinstance(item, dict):
            raise ValueError("Некорректная запись клиента SG-Node")
        client_id = int(item.get("id") or 0)
        public_key = str(item.get("public_key") or "").strip()
        preshared_key = str(item.get("preshared_key") or "").strip()
        name = re.sub(r"[\r\n#]+", " ", str(item.get("name") or "Client")).strip()[:64]
        try:
            address = ipaddress.ip_interface(str(item.get("address") or "").strip())
        except ValueError as exc:
            raise ValueError(f"Некорректный адрес клиента #{client_id}") from exc
        if client_id <= 0 or client_id in seen_ids:
            raise ValueError("Идентификаторы клиентов SG-Node должны быть уникальны")
        if not public_key or public_key in seen_keys or not preshared_key:
            raise ValueError(f"Некорректные ключи клиента #{client_id}")
        system_role = re.sub(r"[^a-z0-9_-]+", "", str(item.get("system_role") or "").strip().lower())[:48]
        is_cascade_service = system_role.startswith("cascade_exit_")
        if address.version != 4 or address.network.prefixlen != 32:
            raise ValueError(f"Адрес клиента #{client_id} должен быть IPv4 /32")
        if is_cascade_service:
            if not address.ip.is_private or address.ip in network:
                raise ValueError(f"Служебный адрес Cascade #{client_id} должен быть отдельным частным /32 вне сети SG-Node")
        elif address.ip not in network:
            raise ValueError(f"Адрес клиента #{client_id} должен быть /32 внутри сети SG-Node")
        address_key = str(address.ip)
        if address_key in seen_addresses:
            raise ValueError("Адреса клиентов SG-Node должны быть уникальны")
        owner = (reserved_addresses or {}).get(address_key, "")
        if owner and not is_cascade_service:
            raise ValueError(
                f"Адрес {address_key}/32 уже занят локальным peer SG-Node"
                + (f" ({owner})" if owner else "")
            )
        routes = [str(address)]
        advertised = str(item.get("advertised_networks") or "").strip()
        if advertised:
            for raw in advertised.split(","):
                value = str(ipaddress.ip_network(raw.strip(), strict=False))
                routes.append(value)
        seen_ids.add(client_id)
        seen_keys.add(public_key)
        seen_addresses.add(str(address.ip))
        result.append({
            "id": client_id,
            "name": name or f"Client {client_id}",
            "public_key": public_key,
            "preshared_key": preshared_key,
            "allowed_ips": ", ".join(routes),
            "system_role": system_role,
        })
    return result


def _replace_managed_peers(config_text: str, peers: list[dict[str, Any]]) -> str:
    lines = config_text.rstrip().splitlines()
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.strip().casefold() in {"[interface]", "[peer]"}:
            if current:
                blocks.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append(current)
    interface_blocks = [block for block in blocks if block and block[0].strip().casefold() == "[interface]"]
    if len(interface_blocks) != 1:
        raise ValueError("В awg0.conf должна быть ровно одна секция [Interface]")
    preserved = [interface_blocks[0]]
    for block in blocks:
        if not block or block is interface_blocks[0]:
            continue
        if block[0].strip().casefold() != "[peer]":
            continue
        if any(line.strip().startswith("# SG-AWG-CLIENT id=") for line in block):
            continue
        preserved.append(block)
    rendered = ["\n".join(block).rstrip() for block in preserved]
    for peer in peers:
        rendered.append("\n".join([
            "[Peer]",
            f"# SG-AWG-CLIENT id={peer['id']} name={peer['name']} role={peer.get('system_role') or 'client'}",
            f"PublicKey = {peer['public_key']}",
            f"PresharedKey = {peer['preshared_key']}",
            f"AllowedIPs = {peer['allowed_ips']}",
        ]))
    return "\n\n".join(rendered).rstrip() + "\n"


def _external_interface() -> str:
    rc, output = _run(["ip", "route", "show", "default"], timeout=5)
    if rc == 0:
        match = re.search(r"\bdev\s+(\S+)", output)
        if match and re.fullmatch(r"[A-Za-z0-9_.:-]{1,32}", match.group(1)):
            return match.group(1)
    raise ValueError("Не удалось определить внешний интерфейс SG-Node")


def _cascade_state() -> dict[str, Any]:
    try:
        value = json.loads(CASCADE_STATE_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _write_base_traffic(runtime: dict[str, Any], peers: list[dict[str, Any]]) -> None:
    network = str(runtime.get("server_network") or "").strip()
    if not network:
        raise ValueError("Не определена сеть работающего awg0")
    ipaddress.ip_network(network, strict=True)
    external = _external_interface()
    service_sources = sorted({
        str(ipaddress.ip_interface(peer["allowed_ips"].split(",", 1)[0].strip()).ip) + "/32"
        for peer in peers
        if str(peer.get("system_role") or "").startswith("cascade_exit_")
    })
    cascade = _cascade_state()
    cascade_interface = CASCADE_INTERFACE if cascade.get("active") else ""
    forward_lines = [f'    iifname "awg0" oifname "{external}" accept']
    if cascade_interface:
        forward_lines.append(f'    iifname "awg0" oifname "{cascade_interface}" accept')
    sources = [network, *service_sources]
    source_set = ", ".join(sources)
    text = "\n".join([
        "table inet sg_awg_node_filter {",
        "  chain forward {",
        "    type filter hook forward priority filter; policy drop;",
        *forward_lines,
        f'    iifname "{external}" oifname "awg0" ct state established,related accept',
        f'    iifname "{cascade_interface}" oifname "awg0" ct state established,related accept' if cascade_interface else "",
        '    iifname "awg0" oifname "awg0" drop',
        "  }",
        "}",
        "table ip sg_awg_node_nat {",
        "  chain postrouting {",
        "    type nat hook postrouting priority srcnat; policy accept;",
        f'    oifname "{external}" ip saddr {{ {source_set} }} masquerade',
        "  }",
        "}",
        "",
    ]).replace("\n\n\n", "\n\n")
    NODE_TRAFFIC_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = NODE_TRAFFIC_PATH.with_suffix(".nft.new")
    temporary.write_text(text, encoding="utf-8")
    temporary.chmod(0o600)
    rc, output = _run(["nft", "-c", "-f", str(temporary)], timeout=15)
    if rc != 0:
        temporary.unlink(missing_ok=True)
        raise ValueError(output or "nft отклонил правила SG-Node")
    temporary.replace(NODE_TRAFFIC_PATH)
    rc, output = _run(["systemctl", "reload-or-restart", KNOWN_UNITS["traffic"]], timeout=30)
    if rc != 0 or _service_state(KNOWN_UNITS["traffic"]) != "active":
        raise RuntimeError(output or "Не удалось применить Traffic runtime SG-Node")


def _delete_cascade_transport() -> None:
    while _run(["ip", "rule", "del", "priority", str(CASCADE_PRIORITY)], timeout=5)[0] == 0:
        pass
    _run(["ip", "route", "flush", "table", str(CASCADE_TABLE)], timeout=5)
    if CASCADE_CONFIG_PATH.exists():
        _run(["awg-quick", "down", str(CASCADE_CONFIG_PATH)], timeout=45)


def _delete_cascade_runtime() -> None:
    _run(["nft", "delete", "table", "inet", "sg_awg_node_cascade"], timeout=5)
    _run(["nft", "delete", "table", "ip", "sg_awg_node_cascade_nat"], timeout=5)
    _delete_cascade_transport()


def _cascade_nft(server_network: str) -> str:
    return "\n".join([
        "table inet sg_awg_node_cascade {",
        "  chain classify {",
        "    type filter hook prerouting priority -160; policy accept;",
        f'    iifname "awg0" ip saddr {server_network} ct mark != 0 meta mark set ct mark',
        f'    iifname "awg0" ip saddr {server_network} ct state new meta mark set 0x{CASCADE_MARK:x} ct mark set meta mark',
        "  }",
        "  chain guard {",
        "    type filter hook forward priority -60; policy accept;",
        f'    iifname "awg0" ip saddr {server_network} meta mark 0x{CASCADE_MARK:x} oifname != "{CASCADE_INTERFACE}" drop',
        "  }",
        "}",
        "table ip sg_awg_node_cascade_nat {",
        "  chain postrouting {",
        "    type nat hook postrouting priority srcnat; policy accept;",
        f'    ip saddr {server_network} oifname "{CASCADE_INTERFACE}" masquerade',
        "  }",
        "}",
        "",
    ])


def _apply_cascade_routes(server_network: str) -> None:
    for command in (
        ["ip", "route", "replace", "default", "dev", CASCADE_INTERFACE, "table", str(CASCADE_TABLE)],
        ["ip", "route", "replace", server_network, "dev", "awg0", "table", str(CASCADE_TABLE)],
        ["ip", "rule", "add", "priority", str(CASCADE_PRIORITY), "fwmark", hex(CASCADE_MARK), "lookup", str(CASCADE_TABLE)],
    ):
        rc, output = _run(command, timeout=10)
        if rc != 0:
            raise RuntimeError(output or "Не удалось применить маршрут Cascade")


def _write_cascade_firewall(server_network: str) -> Path:
    nft_text = _cascade_nft(server_network)
    nft_path = CASCADE_STATE_PATH.with_suffix(".nft")
    nft_path.write_text(nft_text, encoding="utf-8")
    nft_path.chmod(0o600)
    rc, output = _run(["nft", "-c", "-f", str(nft_path)], timeout=15)
    if rc != 0:
        raise RuntimeError(output or "nft отклонил маршрут Cascade")
    _run(["nft", "delete", "table", "inet", "sg_awg_node_cascade"], timeout=5)
    _run(["nft", "delete", "table", "ip", "sg_awg_node_cascade_nat"], timeout=5)
    rc, output = _run(["nft", "-f", str(nft_path)], timeout=15)
    if rc != 0:
        raise RuntimeError(output or "Не удалось включить маршрут Cascade")
    return nft_path


def restore_cascade_runtime() -> dict[str, Any]:
    """Restore an active managed Cascade after a Node reboot.

    sg-awg-traffic loads the persisted Cascade guard first, so client traffic
    stays fail-closed until the service tunnel and policy route are restored.
    """
    state = _cascade_state()
    if not state.get("active"):
        return {"restored": False}
    if not CASCADE_CONFIG_PATH.is_file():
        raise RuntimeError("Cascade отмечен активным, но служебная конфигурация отсутствует")
    runtime = _awg_runtime()
    server_network = str(runtime.get("server_network") or state.get("entry_network") or "").strip()
    if int(runtime.get("listen_port") or 0) != 585 or not server_network:
        raise RuntimeError("Не удалось восстановить Cascade: awg0 на UDP 585 ещё не готов")
    ipaddress.ip_network(server_network, strict=True)

    # Keep the already loaded nft guard in place while transport state is reset.
    nft_path = CASCADE_STATE_PATH.with_suffix(".nft")
    if not nft_path.is_file():
        _write_cascade_firewall(server_network)
    else:
        rc, output = _run(["nft", "-c", "-f", str(nft_path)], timeout=15)
        if rc != 0:
            raise RuntimeError(output or "Сохранённые правила Cascade повреждены")
        filter_ok = _run(["nft", "list", "table", "inet", "sg_awg_node_cascade"], timeout=5)[0] == 0
        nat_ok = _run(["nft", "list", "table", "ip", "sg_awg_node_cascade_nat"], timeout=5)[0] == 0
        if not (filter_ok and nat_ok):
            _write_cascade_firewall(server_network)

    _delete_cascade_transport()
    try:
        rc, output = _run(["awg-quick", "up", str(CASCADE_CONFIG_PATH)], timeout=45)
        if rc != 0:
            raise RuntimeError(output or "Не удалось восстановить туннель Cascade")
        _apply_cascade_routes(server_network)
        rc, link_output = _run(["ip", "link", "show", "dev", CASCADE_INTERFACE], timeout=5)
        if rc != 0 or "UP" not in link_output:
            raise RuntimeError("Интерфейс Cascade не восстановлен")
    except Exception:
        _delete_cascade_transport()
        # Do not remove cascade.nft: it is the fail-closed guard until the
        # Controller repairs or disables this route.
        raise
    return {
        "restored": True,
        "link_id": int(state.get("link_id") or 0),
        "interface": CASCADE_INTERFACE,
        "entry_network": server_network,
    }


def _flush_managed_peer_connections(peers: list[dict[str, Any]]) -> None:
    """Reset only VPN clients affected by a route change."""
    addresses: list[str] = []
    for peer in peers:
        try:
            address = str(ipaddress.ip_interface(str(peer.get("address") or "")).ip)
        except ValueError:
            continue
        if address not in addresses:
            addresses.append(address)
    for address in addresses:
        for selector in ("--orig-src", "--orig-dst", "--reply-src", "--reply-dst"):
            _run(["conntrack", "-D", "-f", "ipv4", selector, address], timeout=15)
    _run(["ip", "route", "flush", "cache"], timeout=10)


def configure_cascade_runtime(payload: dict[str, Any]) -> dict[str, Any]:
    config_text = str(payload.get("config_text") or "").strip() + "\n"
    link_id = int(payload.get("link_id") or 0)
    expected_exit_ip = str(payload.get("exit_public_ip") or "").strip()
    if link_id <= 0 or "[Interface]" not in config_text or "[Peer]" not in config_text:
        raise ValueError("Некорректное задание Cascade")
    runtime = _awg_runtime()
    server_network = str(runtime.get("server_network") or "").strip()
    if int(runtime.get("listen_port") or 0) != 585 or not server_network:
        raise ValueError("Сервер подключения должен иметь работающий awg0 на UDP 585")
    CASCADE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    # awg-quick validates the interface name from the filename. Use a valid
    # temporary .conf name instead of a `.conf.new` suffix.
    temporary = CASCADE_CONFIG_PATH.with_name(f"{CASCADE_INTERFACE}-check.conf")
    temporary.write_text(config_text, encoding="utf-8")
    temporary.chmod(0o600)
    rc, output = _run(["awg-quick", "strip", str(temporary)], timeout=20)
    if rc != 0:
        temporary.unlink(missing_ok=True)
        raise ValueError(output or "AWG-конфигурация Cascade отклонена")
    _delete_cascade_runtime()
    temporary.replace(CASCADE_CONFIG_PATH)
    try:
        rc, output = _run(["awg-quick", "up", str(CASCADE_CONFIG_PATH)], timeout=45)
        if rc != 0:
            raise RuntimeError(output or "Не удалось поднять туннель Cascade")
        _apply_cascade_routes(server_network)
        _write_cascade_firewall(server_network)
        state = {
            "active": True,
            "link_id": link_id,
            "entry_network": server_network,
            "exit_public_ip": expected_exit_ip,
            "interface": CASCADE_INTERFACE,
        }
        CASCADE_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        CASCADE_STATE_PATH.chmod(0o600)
        peers_payload = payload.get("managed_peers") if isinstance(payload.get("managed_peers"), list) else []
        _write_base_traffic(runtime, peers_payload)
        _flush_managed_peer_connections(peers_payload)
        rc, link_output = _run(["ip", "link", "show", "dev", CASCADE_INTERFACE], timeout=5)
        if rc != 0 or "UP" not in link_output:
            raise RuntimeError("Интерфейс Cascade не перешёл в состояние UP")
        return {
            "message": "Cascade включён на сервере подключения",
            "cascade_active": True,
            "link_id": link_id,
            "interface": CASCADE_INTERFACE,
            "entry_network": server_network,
            "exit_public_ip": expected_exit_ip,
        }
    except Exception:
        _delete_cascade_runtime()
        CASCADE_STATE_PATH.unlink(missing_ok=True)
        _write_base_traffic(runtime, payload.get("managed_peers") if isinstance(payload.get("managed_peers"), list) else [])
        raise


def test_cascade_runtime(payload: dict[str, Any]) -> dict[str, Any]:
    link_id = int(payload.get("link_id") or 0)
    state = _cascade_state()
    if not state.get("active") or int(state.get("link_id") or 0) != link_id:
        raise ValueError("Cascade на SG-Node не активен")
    rc, output = _run(["ip", "link", "show", "dev", CASCADE_INTERFACE], timeout=5)
    if rc != 0 or "UP" not in output:
        raise ValueError("Интерфейс Cascade не работает")
    rc, rules = _run(["ip", "rule", "show"], timeout=5)
    if rc != 0 or str(CASCADE_TABLE) not in rules:
        raise ValueError("Policy route Cascade не найден")
    exit_ip = ""
    for url in ("https://checkip.amazonaws.com", "https://api.ipify.org"):
        rc, text = _run([
            "curl", "-4fsS", "--noproxy", "*", "--interface", CASCADE_INTERFACE,
            "--connect-timeout", "4", "--max-time", "8", url,
        ], timeout=12)
        value = text.strip().splitlines()[0] if text.strip() else ""
        try:
            if rc == 0 and ipaddress.ip_address(value).is_global:
                exit_ip = value
                break
        except ValueError:
            continue
    if not exit_ip:
        raise ValueError("Туннель поднят, но выходной IP не подтверждён")
    return {
        "message": f"Cascade работает. Видимый IP: {exit_ip}",
        "cascade_active": True,
        "link_id": link_id,
        "exit_ip": exit_ip,
    }


def disable_cascade_runtime(payload: dict[str, Any]) -> dict[str, Any]:
    link_id = int(payload.get("link_id") or 0)
    runtime = _awg_runtime()
    _delete_cascade_runtime()
    CASCADE_STATE_PATH.unlink(missing_ok=True)
    CASCADE_STATE_PATH.with_suffix(".nft").unlink(missing_ok=True)
    CASCADE_CONFIG_PATH.unlink(missing_ok=True)
    peers_payload = payload.get("managed_peers") if isinstance(payload.get("managed_peers"), list) else []
    _write_base_traffic(runtime, peers_payload)
    _flush_managed_peer_connections(peers_payload)
    return {"message": "Прямой выход в интернет восстановлен", "cascade_active": False, "link_id": link_id}


def _unmanaged_reserved_addresses(config_text: str, server_network: str) -> dict[str, str]:
    network = ipaddress.ip_network(server_network, strict=True)
    reserved: dict[str, str] = {}
    for public_key, metadata in _awg_peer_metadata(config_text).items():
        if bool(metadata.get("managed")):
            continue
        raw_routes = str(metadata.get("allowed_ips") or "")
        for raw in raw_routes.split(","):
            try:
                route = ipaddress.ip_network(raw.strip(), strict=False)
            except ValueError:
                continue
            if route.version != 4 or route.prefixlen != 32 or route.network_address not in network:
                continue
            label = str(metadata.get("name") or "").strip()
            reserved[str(route.network_address)] = label or public_key[:12]
    return reserved


def sync_awg_clients(payload: dict[str, Any]) -> dict[str, Any]:
    if not AWG_CONFIG_PATH.is_file():
        raise ValueError("На SG-Node не найден рабочий awg0.conf")
    previous = AWG_CONFIG_PATH.read_bytes()
    previous_text = previous.decode("utf-8")
    runtime_before = _awg_runtime()
    reserved_addresses = _unmanaged_reserved_addresses(
        previous_text, str(runtime_before.get("server_network") or "")
    )
    peers = _validate_managed_peers(
        payload, runtime_before, reserved_addresses=reserved_addresses
    )
    previous_traffic = NODE_TRAFFIC_PATH.read_bytes() if NODE_TRAFFIC_PATH.exists() else b""
    new_text = _replace_managed_peers(previous_text, peers)
    temporary = AWG_CONFIG_PATH.with_name("awg0-sync.conf")
    temporary.write_text(new_text, encoding="utf-8")
    temporary.chmod(0o600)
    rc, output = _run(["awg-quick", "strip", str(temporary)], timeout=20)
    if rc != 0:
        temporary.unlink(missing_ok=True)
        raise ValueError(output or "awg-quick отклонил список клиентов")
    try:
        temporary.replace(AWG_CONFIG_PATH)
        rc, output = _run(["systemctl", "restart", "sg-awg-server.service"], timeout=45)
        if rc != 0 or _service_state("sg-awg-server.service") != "active":
            raise RuntimeError(output or "AmneziaWG не запустился после изменения клиентов")
        runtime_after = _awg_runtime()
        _validate_managed_peers(
            payload, runtime_after, reserved_addresses=reserved_addresses
        )
        _write_base_traffic(runtime_after, peers)
        actual_keys = set((runtime_after.get("peers") or {}).keys())
        missing = [peer["id"] for peer in peers if peer["public_key"] not in actual_keys]
        if missing:
            raise RuntimeError("После перезапуска не найдены peers клиентов: " + ", ".join(map(str, missing)))
    except Exception:
        rollback = AWG_CONFIG_PATH.with_name("awg0-rollback.conf")
        rollback.write_bytes(previous)
        rollback.chmod(0o600)
        rollback.replace(AWG_CONFIG_PATH)
        _run(["systemctl", "restart", "sg-awg-server.service"], timeout=45)
        if previous_traffic:
            NODE_TRAFFIC_PATH.write_bytes(previous_traffic)
            NODE_TRAFFIC_PATH.chmod(0o600)
            _run(["systemctl", "reload-or-restart", KNOWN_UNITS["traffic"]], timeout=30)
        raise
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "message": "Список клиентов SG-Node применён и проверен",
        "verified_client_ids": [peer["id"] for peer in peers],
        "listen_port": int(runtime_after.get("listen_port") or 0),
        "server_network": str(runtime_after.get("server_network") or ""),
        "server_public_key": str(runtime_after.get("public_key") or ""),
        "managed_peers": len(peers),
        "runtime": runtime_after,
    }


def diagnostics() -> dict[str, Any]:
    commands = {
        "uname": ["uname", "-a"],
        "addresses": ["ip", "-br", "address"],
        "routes": ["ip", "route"],
        "awg": ["awg", "show", "all"],
        "services": ["systemctl", "--no-pager", "--full", "status", *KNOWN_UNITS.values()],
        "logs": [
            "journalctl", "--no-pager", "-n", "80",
            "-u", KNOWN_UNITS["awg"], "-u", KNOWN_UNITS["traffic"], "-u", "sg-awg-node-agent.service",
        ],
    }
    result: dict[str, Any] = {}
    for name, command in commands.items():
        rc, output = _run(command, timeout=20)
        # Keys are never read from files by the agent. Still redact common labels.
        for secret_label in ("PrivateKey", "PresharedKey", "AGENT_TOKEN"):
            output = "\n".join(
                "[redacted]" if secret_label.lower() in line.lower() else line
                for line in output.splitlines()
            )
        result[name] = {"exit_code": rc, "output": output[:12000]}
    result["metadata"] = collect_metadata(include_public_ip=True)
    return result


def execute_job(job: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    kind = str(job.get("kind") or "")
    if kind == "refresh":
        return True, {"message": "Состояние SG-Node обновлено", "metadata": collect_metadata(include_public_ip=True)}
    if kind == "diagnostics":
        return True, {"message": "Диагностика SG-Node собрана", "diagnostics": diagnostics()}
    if kind == "apply_awg_config":
        try:
            payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
            mode = str(payload.get("mode") or "")
            if mode == "sync_clients":
                return True, sync_awg_clients(payload)
            if mode == "configure_cascade":
                return True, configure_cascade_runtime(payload)
            if mode == "disable_cascade":
                return True, disable_cascade_runtime(payload)
            if mode == "test_cascade":
                return True, test_cascade_runtime(payload)
            raise ValueError("Неподдерживаемое задание AmneziaWG")
        except Exception as exc:
            return False, {"message": str(exc)}
    unit = JOB_TO_UNIT.get(kind)
    if unit:
        rc, output = _run(["systemctl", "restart", unit], timeout=45)
        state = _service_state(unit)
        ok = rc == 0 and state in {"active", "activating"}
        return ok, {
            "message": f"{unit}: {'перезапущена' if ok else 'ошибка перезапуска'}",
            "state": state,
            "output": output,
        }
    return False, {"message": "Agent отклонил неизвестное действие"}


def main() -> int:
    if not ENV_FILE.exists():
        raise SystemExit(f"Missing {ENV_FILE}")
    config = _read_env(ENV_FILE)
    base_url = config.get("CONTROLLER_URL", "").rstrip("/")
    slug = config.get("NODE_SLUG", "")
    token = config.get("AGENT_TOKEN", "")
    insecure = config.get("INSECURE_TLS", "0") == "1"
    if not base_url or not slug or not token:
        raise SystemExit("Incomplete SG-AWG Node Agent configuration")

    try:
        restored = restore_cascade_runtime()
        if restored.get("restored"):
            print(
                f"SG-AWG Node Agent: Cascade {restored.get('link_id')} restored on {CASCADE_INTERFACE}",
                flush=True,
            )
    except Exception as exc:
        # The persisted nft guard remains active, so client traffic cannot
        # silently fall back to the direct Internet route.
        print(f"SG-AWG Node Agent: Cascade restore failed: {exc}", flush=True)

    delay = 5
    last_public_refresh = 0.0
    while True:
        try:
            now = time.monotonic()
            include_public = now - last_public_refresh > 600
            metadata = collect_metadata(include_public_ip=include_public)
            if include_public:
                last_public_refresh = now
            heartbeat = api_request(
                base_url,
                f"/api/cluster/v1/nodes/{slug}/heartbeat",
                method="POST",
                token=token,
                payload=metadata,
                insecure=insecure,
            )
            if not heartbeat.get("ok"):
                raise RuntimeError(str(heartbeat.get("message") or "Heartbeat rejected"))
            response = api_request(
                base_url,
                f"/api/cluster/v1/nodes/{slug}/jobs/next",
                token=token,
                insecure=insecure,
            )
            job = response.get("job")
            if isinstance(job, dict):
                ok, result = execute_job(job)
                api_request(
                    base_url,
                    f"/api/cluster/v1/nodes/{slug}/jobs/{int(job['id'])}/result",
                    method="POST",
                    token=token,
                    payload={"ok": ok, "result": result},
                    insecure=insecure,
                )
            delay = 30
        except Exception as exc:
            print(f"SG-AWG Node Agent: {exc}", flush=True)
            delay = min(max(delay * 2, 10), 120)
        time.sleep(delay)


if __name__ == "__main__":
    raise SystemExit(main())
