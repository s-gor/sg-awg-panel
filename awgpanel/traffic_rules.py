from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import subprocess
import urllib.error
import urllib.request
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, Iterable

from .db import ACTIVE_CLIENT_SQL, connect, init_db
from .errors import AWGPanelError
from .outbounds import fwmark_for, interface_name_for
from .traffic_modes import AWG_GATEWAY, normalize_egress_mode

MAX_LIST_BYTES = 16 * 1024 * 1024
MAX_LIST_ITEMS = 800_000
MAX_RULES = 256
DNSMASQ_MAX_DIRECTIVE_BYTES = 900

DNSMASQ_CONFIG_PATH = Path(
    os.environ.get("AWGPANEL_DNSMASQ_CONFIG", "/etc/dnsmasq.d/sg-awg-traffic.conf")
)
TRAFFIC_SCHEDULE_STATE_PATH = Path(
    os.environ.get(
        "AWGPANEL_TRAFFIC_SCHEDULE_STATE",
        "/var/lib/sg-awg-panel/traffic-rules/schedule-state.json",
    )
)

_DOMAIN_RE = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z0-9-]{2,63}$")
_PORT_RE = re.compile(r"^(\d{1,5})(?:-(\d{1,5}))?$")
_SCHEDULE_RE = re.compile(
    r"^(?P<days>(?:mon|tue|wed|thu|fri|sat|sun)(?:,(?:mon|tue|wed|thu|fri|sat|sun))*)?"
    r"(?:@(?P<start>(?:[01]\d|2[0-3]):[0-5]\d)-(?P<end>(?:[01]\d|2[0-3]):[0-5]\d))?$",
    re.IGNORECASE,
)

BUILTIN_LISTS: tuple[dict[str, Any], ...] = ()
PRESETS: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class CompiledRule:
    id: int
    priority: int
    name: str
    client_addresses: tuple[str, ...]
    list_kind: str
    list_items: tuple[str, ...]
    inline_domains: tuple[str, ...]
    inline_cidrs: tuple[str, ...]
    protocol: str
    ports: tuple[str, ...]
    invert: bool
    action_mode: str
    outbound_id: int | None
    schedule: str


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _normalize_domain(value: str) -> str:
    raw = value.strip().lower().rstrip(".")
    if raw.startswith("*."):
        raw = raw[2:]
    try:
        raw = raw.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError(f"Некорректный домен: {value}") from exc
    if not _DOMAIN_RE.fullmatch(raw):
        raise ValueError(f"Некорректный домен: {value}")
    return raw


def parse_domain_list(text: str, source_format: str = "plain") -> list[str]:
    result: set[str] = set()
    for original in text.replace("\r\n", "\n").splitlines():
        line = original.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if source_format == "antifilter":
            match = re.search(r"(?:\*://)?(?:\*\.)?([A-Za-z0-9._-]+)(?:/\*)?", line)
            if not match:
                continue
            line = match.group(1)
        elif source_format == "v2fly":
            line = line.split("#", 1)[0].strip()
            lowered = line.lower()
            if not line or lowered.startswith(("include:", "regexp:", "keyword:")):
                continue
            if lowered.startswith(("domain:", "full:")):
                line = line.split(":", 1)[1]
            line = line.split()[0]
        else:
            line = line.split("#", 1)[0].strip().split()[0]
            if "://" in line:
                parsed = urllib.parse.urlsplit(line)
                line = parsed.hostname or ""
        with_context = line.strip().strip("/")
        try:
            result.add(_normalize_domain(with_context))
        except ValueError:
            continue
        if len(result) > MAX_LIST_ITEMS:
            raise ValueError(f"Список содержит более {MAX_LIST_ITEMS} доменов")
    return sorted(result)


def parse_cidr_list(text: str) -> list[str]:
    networks: list[ipaddress.IPv4Network] = []
    for original in text.replace("\r\n", "\n").splitlines():
        line = original.split("#", 1)[0].strip()
        if not line or line.startswith((";", "!")):
            continue
        token = line.split()[0].strip().strip(",")
        try:
            network = ipaddress.ip_network(token, strict=False)
        except ValueError:
            try:
                address = ipaddress.ip_address(token)
            except ValueError:
                continue
            network = ipaddress.ip_network(f"{address}/32", strict=False)
        if network.version != 4:
            continue
        networks.append(network)
        if len(networks) > MAX_LIST_ITEMS:
            raise ValueError(f"Список содержит более {MAX_LIST_ITEMS} сетей")
    return [str(item) for item in ipaddress.collapse_addresses(networks)]


def normalize_list_content(kind: str, text: str, source_format: str = "plain") -> tuple[str, int, str]:
    if len(text.encode("utf-8")) > MAX_LIST_BYTES:
        raise ValueError("Список превышает допустимый размер 16 MiB")
    if kind == "domains":
        items = parse_domain_list(text, source_format)
    elif kind == "cidrs":
        items = parse_cidr_list(text)
    else:
        raise ValueError("Тип списка должен быть domains или cidrs")
    normalized = "\n".join(items) + ("\n" if items else "")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return normalized, len(items), digest


def _normalize_slug(value: str) -> str:
    slug = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", slug):
        raise ValueError("Slug должен содержать латинские буквы, цифры и дефисы")
    return slug


def normalize_source_reference(value: object, *, kind: str) -> str:
    """Normalize HTTPS, GeoIP-country and ASN list source references."""
    raw = str(value or "").strip()
    geo = re.fullmatch(r"geo:([A-Za-z]{2})", raw)
    if geo:
        if kind != "cidrs":
            raise ValueError("GeoIP-источник можно использовать только для IPv4 CIDR")
        return f"geo:{geo.group(1).lower()}"
    asn = re.fullmatch(r"asn:(?:AS)?([1-9][0-9]{0,9})", raw, re.IGNORECASE)
    if asn:
        if kind != "cidrs":
            raise ValueError("ASN-источник можно использовать только для IPv4 CIDR")
        return f"asn:AS{int(asn.group(1))}"
    if re.fullmatch(r"https://[^\s]{3,2048}", raw):
        return raw
    raise ValueError("Источник должен быть HTTPS URL, geo:RU или asn:AS12345")


def source_display_type(value: object) -> str:
    raw = str(value or "")
    if raw.startswith("geo:"):
        return "geo"
    if raw.startswith("asn:"):
        return "asn"
    return "url"


def _normalize_ports(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    normalized: list[str] = []
    for part in [item.strip() for item in raw.split(",")]:
        match = _PORT_RE.fullmatch(part)
        if not match:
            raise ValueError(f"Некорректный порт или диапазон: {part}")
        start, end = int(match.group(1)), int(match.group(2) or match.group(1))
        if not 1 <= start <= end <= 65535:
            raise ValueError(f"Некорректный порт или диапазон: {part}")
        normalized.append(str(start) if start == end else f"{start}-{end}")
    if len(normalized) > 64:
        raise ValueError("Укажите не более 64 портов или диапазонов")
    return ", ".join(dict.fromkeys(normalized))


def _normalize_client_ids(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    ids: list[int] = []
    for part in raw.split(","):
        try:
            item = int(part.strip())
        except ValueError as exc:
            raise ValueError("Список клиентов должен содержать числовые id") from exc
        if item <= 0:
            raise ValueError("Некорректный id клиента")
        ids.append(item)
    return ",".join(str(item) for item in dict.fromkeys(ids))


def _normalize_inline_domains(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    items: list[str] = []
    for part in re.split(r"[,\n]", raw):
        if part.strip():
            items.append(_normalize_domain(part))
    if len(items) > 256:
        raise ValueError("Укажите не более 256 доменов в одном правиле")
    return "\n".join(dict.fromkeys(items))


def _normalize_inline_cidrs(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    items = parse_cidr_list(raw.replace(",", "\n"))
    if len(items) > 256:
        raise ValueError("Укажите не более 256 CIDR в одном правиле")
    return "\n".join(items)


def _normalize_schedule(value: object) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    match = _SCHEDULE_RE.fullmatch(raw)
    if not match:
        raise ValueError("Расписание: mon,tue@08:00-22:00 или пусто")
    if match.group("start"):
        start = time.fromisoformat(match.group("start"))
        end = time.fromisoformat(match.group("end"))
        if start == end:
            raise ValueError("Начало и конец расписания не должны совпадать")
    return raw


def schedule_is_active(value: str, now: datetime | None = None) -> bool:
    if not value:
        return True
    current = now or datetime.now().astimezone()
    match = _SCHEDULE_RE.fullmatch(value)
    if not match:
        return False
    days = match.group("days")
    if days:
        names = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
        if names[current.weekday()] not in days.lower().split(","):
            return False
    if not match.group("start"):
        return True
    start = time.fromisoformat(match.group("start"))
    end = time.fromisoformat(match.group("end"))
    current_time = current.timetz().replace(tzinfo=None)
    if start < end:
        return start <= current_time < end
    return current_time >= start or current_time < end


def ensure_builtin_lists() -> None:
    init_db()
    with connect() as con:
        for item in BUILTIN_LISTS:
            content, count, digest = normalize_list_content(
                str(item["kind"]), str(item["content"]), str(item["source_format"])
            )
            con.execute(
                """
                INSERT INTO traffic_lists (
                    slug, name, description, kind, source_type, source_url,
                    source_format, enabled, auto_update, builtin,
                    content_text, sha256, item_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET
                    name=excluded.name,
                    description=excluded.description,
                    source_type=CASE WHEN traffic_lists.builtin=1 THEN excluded.source_type ELSE traffic_lists.source_type END,
                    source_url=CASE WHEN traffic_lists.builtin=1 THEN excluded.source_url ELSE traffic_lists.source_url END,
                    source_format=CASE WHEN traffic_lists.builtin=1 THEN excluded.source_format ELSE traffic_lists.source_format END,
                    enabled=CASE WHEN traffic_lists.builtin=1 THEN excluded.enabled ELSE traffic_lists.enabled END,
                    auto_update=CASE WHEN traffic_lists.builtin=1 THEN excluded.auto_update ELSE traffic_lists.auto_update END,
                    builtin=CASE WHEN traffic_lists.builtin=1 THEN 1 ELSE traffic_lists.builtin END,
                    content_text=CASE
                        WHEN traffic_lists.builtin=1 AND traffic_lists.source_type='manual' THEN excluded.content_text
                        ELSE traffic_lists.content_text END,
                    sha256=CASE
                        WHEN traffic_lists.builtin=1 AND traffic_lists.source_type='manual' THEN excluded.sha256
                        ELSE traffic_lists.sha256 END,
                    item_count=CASE
                        WHEN traffic_lists.builtin=1 AND traffic_lists.source_type='manual' THEN excluded.item_count
                        ELSE traffic_lists.item_count END
                """,
                (
                    item["slug"], item["name"], item["description"], item["kind"],
                    item["source_type"], item["source_url"], item["source_format"],
                    int(item.get("enabled", 0)), int(item["auto_update"]), content, digest, count,
                ),
            )



def list_traffic_lists(*, enabled_only: bool = False):
    ensure_builtin_lists()
    query = "SELECT * FROM traffic_lists"
    if enabled_only:
        query += " WHERE enabled=1"
    query += " ORDER BY builtin DESC, name COLLATE NOCASE"
    with connect() as con:
        return con.execute(query).fetchall()


def find_traffic_list(list_id: int):
    ensure_builtin_lists()
    with connect() as con:
        row = con.execute("SELECT * FROM traffic_lists WHERE id=?", (int(list_id),)).fetchone()
    if row is None:
        raise AWGPanelError("Traffic List не найден")
    return row


def find_traffic_list_by_slug(slug: str):
    ensure_builtin_lists()
    with connect() as con:
        row = con.execute("SELECT * FROM traffic_lists WHERE slug=?", (_normalize_slug(slug),)).fetchone()
    if row is None:
        raise AWGPanelError(f"Traffic List {slug} не найден")
    return row


def save_traffic_list(
    list_id: int | None,
    *,
    slug: str,
    name: str,
    description: str,
    kind: str,
    source_type: str,
    source_url: str,
    source_format: str,
    content_text: str,
    enabled: bool,
    auto_update: bool,
):
    init_db()
    current = find_traffic_list(int(list_id)) if list_id is not None else None
    if current is not None and bool(current["builtin"]):
        # Presets depend on stable built-in identities. Users may enable/disable
        # them and edit the content of manual examples, but cannot silently
        # repoint a trusted built-in remote source or change its kind.
        slug = str(current["slug"])
        name = str(current["name"])
        kind = str(current["kind"])
        source_type = str(current["source_type"])
        source_url = str(current["source_url"])
        source_format = str(current["source_format"])
        if source_type == "url":
            content_text = str(current["content_text"] or "")

    normalized_slug = _normalize_slug(slug)
    normalized_name = str(name or "").strip()
    if not 1 <= len(normalized_name) <= 96:
        raise ValueError("Название списка должно содержать 1–96 символов")
    if kind not in {"domains", "cidrs"}:
        raise ValueError("Тип списка должен быть domains или cidrs")
    if source_type not in {"manual", "url"}:
        raise ValueError("Источник должен быть manual или url")
    if source_format not in {"plain", "antifilter", "v2fly"}:
        raise ValueError("Неизвестный формат источника")
    url = str(source_url or "").strip()
    if source_type == "url":
        url = normalize_source_reference(url, kind=kind)
        if url.startswith(("geo:", "asn:")):
            source_format = "plain"
    else:
        url = ""
    normalized, count, digest = normalize_list_content(kind, content_text, source_format)
    try:
        with connect() as con:
            if list_id is None:
                cursor = con.execute(
                    """
                    INSERT INTO traffic_lists (
                        slug, name, description, kind, source_type, source_url,
                        source_format, enabled, auto_update, builtin,
                        content_text, sha256, item_count, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (
                        normalized_slug, normalized_name, str(description or "").strip(), kind,
                        source_type, url, source_format, 1 if enabled else 0,
                        1 if auto_update else 0, normalized, digest, count,
                    ),
                )
                list_id = int(cursor.lastrowid)
            else:
                con.execute(
                    """
                    UPDATE traffic_lists SET slug=?, name=?, description=?, kind=?,
                        source_type=?, source_url=?, source_format=?, enabled=?,
                        auto_update=?, content_text=?, sha256=?, item_count=?,
                        updated_at=CURRENT_TIMESTAMP WHERE id=?
                    """,
                    (
                        normalized_slug, normalized_name, str(description or "").strip(), kind,
                        source_type, url, source_format, 1 if enabled else 0,
                        1 if auto_update else 0, normalized, digest, count, int(list_id),
                    ),
                )
                if bool(current["builtin"]):
                    con.execute("UPDATE traffic_lists SET builtin=1 WHERE id=?", (int(list_id),))
    except Exception as exc:
        if "UNIQUE" in str(exc).upper():
            raise ValueError("Traffic List с таким slug или названием уже существует") from exc
        raise
    return find_traffic_list(int(list_id))


def delete_traffic_list(list_id: int) -> None:
    current = find_traffic_list(list_id)
    if bool(current["builtin"]):
        raise ValueError("Встроенный список нельзя удалить; его можно отключить")
    with connect() as con:
        used = con.execute("SELECT COUNT(*) FROM traffic_rules WHERE list_id=?", (int(list_id),)).fetchone()[0]
        if used:
            raise ValueError("Сначала удалите правила, использующие этот список")
        con.execute("DELETE FROM traffic_lists WHERE id=?", (int(list_id),))


def _download_source_payload(source_reference: str, *, timeout: int) -> tuple[bytes, str]:
    reference = str(source_reference or "").strip()
    if reference.startswith("geo:"):
        country = reference.split(":", 1)[1].lower()
        url = (
            "https://raw.githubusercontent.com/ipverse/rir-ip/refs/heads/master/"
            f"country/{country}/ipv4-aggregated.txt"
        )
        response_format = "plain"
    elif reference.startswith("asn:"):
        resource = reference.split(":", 1)[1].upper()
        url = (
            "https://stat.ripe.net/data/announced-prefixes/data.json?resource="
            + urllib.parse.quote(resource)
        )
        response_format = "ripe-asn-json"
    else:
        url = reference
        response_format = "plain"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "SG-AWG-Panel/0.1 traffic-list"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        final_url = str(response.geturl() or "")
        if not final_url.lower().startswith("https://"):
            raise ValueError("Источник перенаправил запрос на небезопасный URL")
        raw = response.read(MAX_LIST_BYTES + 1)
    return raw, response_format


def _refresh_traffic_list_impl(list_id: int, *, timeout: int = 45):
    current = find_traffic_list(list_id)
    if str(current["source_type"]) != "url":
        raise ValueError("Ручной список не загружается из сети")
    try:
        raw, response_format = _download_source_payload(
            str(current["source_url"]), timeout=timeout
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        with connect() as con:
            con.execute(
                "UPDATE traffic_lists SET last_error=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (str(exc)[:500], int(list_id)),
            )
        raise AWGPanelError(f"Не удалось загрузить список {current['name']}: {exc}") from exc
    if len(raw) > MAX_LIST_BYTES:
        raise ValueError("Загруженный список превышает 16 MiB")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Список должен быть UTF-8 текстом") from exc

    if response_format == "ripe-asn-json":
        try:
            document = json.loads(text)
            prefixes = document["data"]["prefixes"]
            if not isinstance(prefixes, list):
                raise TypeError
            values = []
            for item in prefixes:
                if not isinstance(item, dict) or not isinstance(item.get("prefix"), str):
                    continue
                values.append(item["prefix"])
            text = "\n".join(values)
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("RIPEstat вернул некорректный список префиксов ASN") from exc

    normalized, count, digest = normalize_list_content(
        str(current["kind"]), text, str(current["source_format"])
    )
    if count == 0:
        raise ValueError("Загруженный список не содержит подходящих записей")
    changed = digest != str(current["sha256"] or "")
    with connect() as con:
        con.execute(
            """
            UPDATE traffic_lists SET content_text=?, sha256=?, item_count=?,
                last_updated_at=CURRENT_TIMESTAMP, last_error='', updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (normalized, digest, count, int(list_id)),
        )
    result = find_traffic_list(list_id)
    return result, changed


def refresh_traffic_list(list_id: int, *, timeout: int = 45):
    """Refresh one external list and retain the previous working content on error."""
    try:
        return _refresh_traffic_list_impl(list_id, timeout=timeout)
    except Exception as exc:
        with connect() as con:
            con.execute(
                "UPDATE traffic_lists SET last_error=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (str(exc)[:500], int(list_id)),
            )
        raise


def refresh_auto_lists() -> dict[str, int]:
    ok = 0
    failed = 0
    changed = 0
    for item in list_traffic_lists(enabled_only=True):
        if str(item["source_type"]) != "url" or not bool(item["auto_update"]):
            continue
        try:
            _row, item_changed = refresh_traffic_list(int(item["id"]))
            ok += 1
            changed += 1 if item_changed else 0
        except Exception:
            failed += 1
    return {"updated": ok, "failed": failed, "changed": changed}


def list_traffic_rules(*, enabled_only: bool = False, include_system: bool = True):
    ensure_builtin_lists()
    query = """
        SELECT r.*, l.name AS list_name, l.slug AS list_slug, l.kind AS list_kind,
               o.name AS outbound_name
        FROM traffic_rules r
        LEFT JOIN traffic_lists l ON l.id=r.list_id
        LEFT JOIN outbounds o ON o.id=r.outbound_id
    """
    filters: list[str] = []
    if enabled_only:
        filters.append("r.enabled=1")
    if not include_system:
        filters.append("r.system_key=''")
    if filters:
        query += " WHERE " + " AND ".join(filters)
    query += " ORDER BY r.priority, r.id"
    with connect() as con:
        return con.execute(query).fetchall()


def find_traffic_rule(rule_id: int):
    with connect() as con:
        row = con.execute("SELECT * FROM traffic_rules WHERE id=?", (int(rule_id),)).fetchone()
    if row is None:
        raise AWGPanelError("Traffic Rules Rule не найден")
    return row


def next_rule_priority() -> int:
    """Return a free user-facing priority before the reserved 9999 catch-all."""
    priorities = [int(row["priority"]) for row in list_traffic_rules(include_system=False)]
    regular = [value for value in priorities if value < 9999]
    candidate = ((max(regular) // 10) + 1) * 10 if regular else 10
    if candidate < 9999 and candidate not in priorities:
        return candidate
    for candidate in range(10, 9990, 10):
        if candidate not in priorities:
            return candidate
    raise ValueError("Не удалось назначить порядок правила: свободные позиции закончились")


def rule_supports_simple_editor(rule: object) -> bool:
    row = dict(rule)  # sqlite3.Row and dictionaries are both accepted.
    domains = bool(str(row.get("inline_domains") or "").strip())
    cidrs = bool(str(row.get("inline_cidrs") or "").strip())
    return (
        row.get("list_id") is None
        and domains != cidrs
        and str(row.get("protocol") or "any") == "any"
        and not str(row.get("ports") or "").strip()
        and not bool(row.get("invert_match"))
        and not str(row.get("schedule") or "").strip()
    )


def ensure_rule_dns_control(rule: object, *, force_redirect: bool = False) -> bool:
    """Enable the DNS path required by an enabled domain rule.

    Returns True when DNS settings were changed.
    """
    row = dict(rule)
    if not bool(row.get("enabled")):
        return False
    has_domains = bool(str(row.get("inline_domains") or "").strip())
    list_id = row.get("list_id")
    if list_id is not None:
        selected = find_traffic_list(int(list_id))
        has_domains = has_domains or str(selected["kind"]) == "domains"
    if not has_domains:
        return False
    current = get_dns_traffic_settings()
    current_mode = str(current["mode"])
    mode = "redirect" if force_redirect or current_mode == "off" else current_mode
    advertise = True
    if mode == current_mode and bool(current["advertise_to_clients"]) == advertise:
        return False
    save_dns_traffic_settings(
        mode=mode,
        upstreams=str(current["upstreams"]),
        advertise_to_clients=advertise,
        block_dot=bool(current["block_dot"]),
    )
    return True


def validate_rule_values(values: dict[str, object], *, rule_id: int | None = None) -> dict[str, object]:
    name = str(values.get("name", "")).strip()
    if not 1 <= len(name) <= 96:
        raise ValueError("Название правила должно содержать 1–96 символов")
    priority = int(values.get("priority", 100))
    if not 1 <= priority <= 9999:
        raise ValueError("Приоритет должен быть 1–9999")
    client_ids = _normalize_client_ids(values.get("client_ids", ""))
    if client_ids:
        ids = [int(item) for item in client_ids.split(",")]
        with connect() as con:
            known = {int(row[0]) for row in con.execute("SELECT id FROM awg_clients WHERE node_id IS NULL AND id IN (%s)" % ",".join("?" * len(ids)), ids)}
        missing = set(ids) - known
        if missing:
            raise ValueError("Неизвестные клиенты: " + ", ".join(map(str, sorted(missing))))
    list_id_value = values.get("list_id")
    list_id = int(list_id_value) if str(list_id_value or "").isdigit() else None
    if list_id is not None:
        selected = find_traffic_list(list_id)
        if not bool(selected["enabled"]):
            raise ValueError("Выбранный Traffic List отключён")
    inline_domains = _normalize_inline_domains(values.get("inline_domains", ""))
    inline_cidrs = _normalize_inline_cidrs(values.get("inline_cidrs", ""))
    if list_id is None and not inline_domains and not inline_cidrs and priority != 9999:
        # Any is allowed, but the explicit final priority makes accidental catch-all less likely.
        if not bool(values.get("allow_any", False)):
            raise ValueError("Для правила Any подтвердите условие 'Любое назначение'")
    protocol = str(values.get("protocol", "any")).strip().lower()
    if protocol not in {"any", "tcp", "udp"}:
        raise ValueError("Протокол должен быть any, tcp или udp")
    ports = _normalize_ports(values.get("ports", ""))
    if ports and protocol == "any":
        raise ValueError("Для фильтра по портам выберите TCP или UDP")
    action_mode = normalize_egress_mode(values.get("action_mode", AWG_GATEWAY))
    if action_mode == AWG_GATEWAY:
        raise ValueError("Traffic Rule может использовать только block или outbound")
    outbound_value = values.get("outbound_id")
    outbound_id = int(outbound_value) if str(outbound_value or "").isdigit() else None
    if action_mode == "outbound":
        if outbound_id is None:
            raise ValueError("Выберите Outbound")
        with connect() as con:
            outbound = con.execute("SELECT * FROM outbounds WHERE id=?", (outbound_id,)).fetchone()
        if outbound is None or not bool(outbound["enabled"]):
            raise ValueError("Выбранный Outbound не найден или отключён")
    else:
        outbound_id = None
    schedule = _normalize_schedule(values.get("schedule", ""))
    return {
        "name": name,
        "priority": priority,
        "enabled": 1 if bool(values.get("enabled", True)) else 0,
        "client_ids": client_ids,
        "list_id": list_id,
        "inline_domains": inline_domains,
        "inline_cidrs": inline_cidrs,
        "protocol": protocol,
        "ports": ports,
        "invert_match": 1 if bool(values.get("invert_match", False)) else 0,
        "schedule": schedule,
        "action_mode": action_mode,
        "outbound_id": outbound_id,
    }


def save_traffic_rule(rule_id: int | None, values: dict[str, object]):
    normalized = validate_rule_values(values, rule_id=rule_id)
    with connect() as con:
        if rule_id is None:
            count = int(con.execute("SELECT COUNT(*) FROM traffic_rules").fetchone()[0])
            if count >= MAX_RULES:
                raise ValueError(f"Можно создать не более {MAX_RULES} правил")
            cursor = con.execute(
                """
                INSERT INTO traffic_rules (
                    priority, name, enabled, client_ids, list_id, inline_domains,
                    inline_cidrs, protocol, ports, invert_match, schedule,
                    action_mode, outbound_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(normalized[key] for key in (
                    "priority", "name", "enabled", "client_ids", "list_id",
                    "inline_domains", "inline_cidrs", "protocol", "ports",
                    "invert_match", "schedule", "action_mode", "outbound_id",
                )),
            )
            rule_id = int(cursor.lastrowid)
        else:
            current = find_traffic_rule(rule_id)
            if str(current["system_key"] or ""):
                raise ValueError("Системная защита изменяется в разделе «Защита»")
            con.execute(
                """
                UPDATE traffic_rules SET priority=?, name=?, enabled=?, client_ids=?,
                    list_id=?, inline_domains=?, inline_cidrs=?, protocol=?, ports=?,
                    invert_match=?, schedule=?, action_mode=?, outbound_id=?,
                    updated_at=CURRENT_TIMESTAMP WHERE id=?
                """,
                tuple(normalized[key] for key in (
                    "priority", "name", "enabled", "client_ids", "list_id",
                    "inline_domains", "inline_cidrs", "protocol", "ports",
                    "invert_match", "schedule", "action_mode", "outbound_id",
                )) + (int(rule_id),),
            )
    return find_traffic_rule(int(rule_id))


def delete_traffic_rule(rule_id: int) -> None:
    current = find_traffic_rule(rule_id)
    if str(current["system_key"] or ""):
        raise ValueError("Системную защиту нельзя удалить как обычное правило")
    with connect() as con:
        con.execute("DELETE FROM traffic_rules WHERE id=?", (int(rule_id),))


def reorder_traffic_rules(rule_ids: Iterable[int]) -> None:
    ids = [int(item) for item in rule_ids]
    if not ids:
        return
    with connect() as con:
        known = {
            int(row[0])
            for row in con.execute(
                "SELECT id FROM traffic_rules WHERE system_key=''"
            ).fetchall()
        }
        if set(ids) != known or len(ids) != len(known):
            raise ValueError("Порядок должен содержать все пользовательские правила ровно один раз")
        for index, rule_id in enumerate(ids, start=1):
            con.execute(
                "UPDATE traffic_rules SET priority=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (index * 10, rule_id),
            )


def get_dns_traffic_settings():
    init_db()
    with connect() as con:
        row = con.execute("SELECT * FROM dns_settings WHERE id=1").fetchone()
    if row is None:
        raise AWGPanelError("Настройки DNS Control не найдены")
    return row


def save_dns_traffic_settings(
    *, mode: str, upstreams: str, advertise_to_clients: bool, block_dot: bool
):
    # DNS interception is part of domain traffic and is always enabled. Legacy
    # JSON values off/observe are accepted but normalized to redirect.
    normalized_mode = "redirect"
    servers: list[str] = []
    for part in str(upstreams or "").split(","):
        if not part.strip():
            continue
        try:
            servers.append(str(ipaddress.ip_address(part.strip())))
        except ValueError as exc:
            raise ValueError(f"Некорректный upstream DNS: {part.strip()}") from exc
    if not servers:
        raise ValueError("Укажите хотя бы один upstream DNS")
    with connect() as con:
        con.execute(
            """
            UPDATE dns_settings SET mode=?, upstreams=?, advertise_to_clients=?,
                block_dot=?, updated_at=CURRENT_TIMESTAMP WHERE id=1
            """,
            (
                normalized_mode, ", ".join(servers), 1,
                1 if block_dot else 0,
            ),
        )
    return get_dns_traffic_settings()




def _list_items(row: Any) -> tuple[str, ...]:
    return tuple(line.strip() for line in str(row["content_text"] or "").splitlines() if line.strip())


def compile_rule_values(
    rules: Iterable[object], now: datetime | None = None
) -> list[CompiledRule]:
    init_db()
    with connect() as con:
        clients = {int(row["id"]): str(row["address"]) for row in con.execute(f"SELECT id, address FROM awg_clients WHERE node_id IS NULL AND {ACTIVE_CLIENT_SQL}")}
        lists = {int(row["id"]): row for row in con.execute("SELECT * FROM traffic_lists WHERE enabled=1")}
    output: list[CompiledRule] = []
    for index, original in enumerate(rules, start=1):
        rule = dict(original)
        if not bool(rule.get("enabled", True)):
            continue
        if not schedule_is_active(str(rule.get("schedule") or ""), now):
            continue
        client_ids = [int(part) for part in str(rule.get("client_ids") or "").split(",") if part]
        addresses = tuple(clients[item] for item in client_ids if item in clients)
        if client_ids and not addresses:
            continue
        list_id = rule.get("list_id")
        selected = lists.get(int(list_id)) if list_id is not None else None
        if list_id is not None and selected is None:
            continue
        raw_id = rule.get("id")
        rule_id = int(raw_id) if isinstance(raw_id, int) and raw_id > 0 else 100000 + index
        output.append(
            CompiledRule(
                id=rule_id,
                priority=int(rule.get("priority", 100)),
                name=str(rule.get("name") or ""),
                client_addresses=addresses,
                list_kind=str(selected["kind"]) if selected else "",
                list_items=_list_items(selected) if selected else (),
                inline_domains=tuple(str(rule.get("inline_domains") or "").splitlines()),
                inline_cidrs=tuple(str(rule.get("inline_cidrs") or "").splitlines()),
                protocol=str(rule.get("protocol") or "any"),
                ports=tuple(part.strip() for part in str(rule.get("ports") or "").split(",") if part.strip()),
                invert=bool(rule.get("invert_match")),
                action_mode=str(rule.get("action_mode") or "block"),
                outbound_id=int(rule["outbound_id"]) if rule.get("outbound_id") is not None else None,
                schedule=str(rule.get("schedule") or ""),
            )
        )
    return output


def compile_rules(now: datetime | None = None) -> list[CompiledRule]:
    return compile_rule_values(list_traffic_rules(enabled_only=True), now=now)


def _set_name(rule_id: int, kind: str) -> str:
    return f"rr_{rule_id}_{'dom4' if kind == 'domains' else 'net4'}"


def _nft_port_expression(protocol: str, ports: tuple[str, ...]) -> str:
    if not ports:
        return ""
    return f" {protocol} dport {{ {', '.join(ports)} }}"


def _nft_comment(value: str) -> str:
    return value.replace("\\", "\\\\").replace(chr(34), chr(92) + chr(34)).replace("\n", " ")[:80]


def render_rule_nft(
    *, inbound_interface: str, rules: list[CompiledRule], block_mark: int = 0x5FFF
) -> tuple[list[str], list[str], list[str], dict[int, tuple[str, ...]]]:
    """Compile ordered policy rules to nftables declarations and statements.

    Domain and CIDR conditions are an OR. Inverted rules mean "not in either
    set". The first emitted matching statement returns from the base chain,
    which implements first-match-wins before per-client fallback traffic.
    """
    declarations: list[str] = []
    classification: list[str] = []
    guards: list[str] = []
    domain_map: dict[int, tuple[str, ...]] = {}

    for rule in rules:
        domains = tuple(
            dict.fromkeys(
                item for item in (
                    rule.inline_domains
                    + (rule.list_items if rule.list_kind == "domains" else ())
                ) if item
            )
        )
        cidrs = tuple(
            dict.fromkeys(
                item for item in (
                    rule.inline_cidrs
                    + (rule.list_items if rule.list_kind == "cidrs" else ())
                ) if item
            )
        )
        domain_set = ""
        cidr_set = ""
        if domains:
            domain_set = _set_name(rule.id, "domains")
            declarations.extend(
                [
                    f"  set {domain_set} {{",
                    "    type ipv4_addr;",
                    "    flags timeout;",
                    "    timeout 1h;",
                    "  }",
                ]
            )
            domain_map[rule.id] = domains
        if cidrs:
            cidr_set = _set_name(rule.id, "cidrs")
            declarations.extend(
                [
                    f"  set {cidr_set} {{",
                    "    type ipv4_addr;",
                    "    flags interval;",
                    "    auto-merge;",
                    f"    elements = {{ {', '.join(cidrs)} }}",
                    "  }",
                ]
            )

        sources = ""
        if rule.client_addresses:
            addresses = ", ".join(
                str(ipaddress.ip_interface(item).ip) for item in rule.client_addresses
            )
            sources = f" ip saddr {{ {addresses} }}"
        proto = "" if rule.protocol == "any" else f" meta l4proto {rule.protocol}"
        ports = _nft_port_expression(rule.protocol, rule.ports)
        base = f'iifname "{inbound_interface}"{sources}'

        if rule.action_mode == "outbound" and rule.outbound_id is not None:
            mark = fwmark_for(rule.outbound_id)
        elif rule.action_mode == "block":
            mark = block_mark
        else:
            mark = 0
        action = f"meta mark set 0x{mark:x} ct mark set meta mark return"
        comment = _nft_comment(f"{rule.priority}:{rule.name}")

        destinations: list[str]
        if rule.invert:
            parts: list[str] = []
            if domain_set:
                parts.append(f" ip daddr != @{domain_set}")
            if cidr_set:
                parts.append(f" ip daddr != @{cidr_set}")
            destinations = ["".join(parts)] if parts else [""]
        else:
            destinations = []
            if domain_set:
                destinations.append(f" ip daddr @{domain_set}")
            if cidr_set:
                destinations.append(f" ip daddr @{cidr_set}")
            if not destinations:
                destinations.append("")

        for destination in destinations:
            classification.append(
                f"    {base}{destination}{proto}{ports} {action} comment \"{comment}\""
            )

    guards.append(
        f'    iifname "{inbound_interface}" meta mark 0x{block_mark:x} drop'
    )
    seen_outbounds: set[int] = set()
    for rule in rules:
        if (
            rule.action_mode == "outbound"
            and rule.outbound_id is not None
            and rule.outbound_id not in seen_outbounds
        ):
            seen_outbounds.add(rule.outbound_id)
            mark = fwmark_for(rule.outbound_id)
            interface = interface_name_for(rule.outbound_id)
            guards.append(
                f'    iifname "{inbound_interface}" meta mark 0x{mark:x} '
                f'oifname != "{interface}" drop'
            )
    return declarations, classification, guards, domain_map


def _dnsmasq_nftset_lines(
    domains: tuple[str, ...], *, set_name: str, max_bytes: int = DNSMASQ_MAX_DIRECTIVE_BYTES
) -> list[str]:
    """Render nftset directives without exceeding dnsmasq parser line limits."""
    prefix = "nftset=/"
    suffix = f"/4#inet#sg_awg_traffic#{set_name}"
    lines: list[str] = []
    chunk: list[str] = []
    for domain in domains:
        candidate = prefix + "/".join((*chunk, domain)) + suffix
        if chunk and len(candidate.encode("utf-8")) > max_bytes:
            lines.append(prefix + "/".join(chunk) + suffix)
            chunk = [domain]
        else:
            chunk.append(domain)
    if chunk:
        lines.append(prefix + "/".join(chunk) + suffix)
    for line in lines:
        if len(line.encode("utf-8")) > max_bytes:
            raise AWGPanelError("Домен слишком длинный для конфигурации dnsmasq")
    return lines


def render_dnsmasq_config(
    *, server_address: str, upstreams: str, domain_map: dict[int, tuple[str, ...]]
) -> str:
    lines = [
        "# Managed by SG-AWG-Panel. Do not edit.",
        "port=53",
        f"listen-address={server_address}",
        "bind-dynamic",
        "no-resolv",
        "domain-needed",
        "bogus-priv",
        "cache-size=10000",
        "min-cache-ttl=60",
        "max-cache-ttl=3600",
    ]
    for server in [part.strip() for part in upstreams.split(",") if part.strip()]:
        lines.append(f"server={server}")
    for rule_id, domains in sorted(domain_map.items()):
        lines.extend(
            _dnsmasq_nftset_lines(
                domains, set_name=_set_name(rule_id, "domains")
            )
        )
    return "\n".join(lines) + "\n"


def apply_dnsmasq_runtime(
    *, server_address: str, domain_map: dict[int, tuple[str, ...]], validate_only: bool = False
) -> dict[str, object]:
    settings = get_dns_traffic_settings()
    mode = str(settings["mode"])
    if mode == "off":
        if not validate_only:
            DNSMASQ_CONFIG_PATH.unlink(missing_ok=True)
            subprocess.run(
                ["systemctl", "disable", "--now", "dnsmasq.service"],
                check=False, capture_output=True, text=True, timeout=30,
            )
        return {"enabled": False, "mode": mode, "domains": 0}

    text = render_dnsmasq_config(
        server_address=server_address,
        upstreams=str(settings["upstreams"]),
        domain_map=domain_map,
    )
    DNSMASQ_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = DNSMASQ_CONFIG_PATH.with_name(DNSMASQ_CONFIG_PATH.name + ".new")
    temporary.write_text(text, encoding="utf-8")
    os.chmod(temporary, 0o644)
    result = subprocess.run(
        ["dnsmasq", "--test", f"--conf-file={temporary}"],
        capture_output=True, text=True, timeout=20, check=False,
    )
    if result.returncode != 0:
        temporary.unlink(missing_ok=True)
        raise AWGPanelError(
            result.stderr.strip() or result.stdout.strip() or "dnsmasq не принял конфигурацию"
        )
    if validate_only:
        temporary.unlink(missing_ok=True)
        return {
            "enabled": True,
            "mode": mode,
            "domains": sum(len(value) for value in domain_map.values()),
        }

    previous = DNSMASQ_CONFIG_PATH.read_bytes() if DNSMASQ_CONFIG_PATH.exists() else None
    temporary.replace(DNSMASQ_CONFIG_PATH)
    try:
        subprocess.run(
            ["systemctl", "enable", "dnsmasq.service"],
            check=False, capture_output=True, text=True, timeout=30,
        )
        restart = subprocess.run(
            ["systemctl", "restart", "dnsmasq.service"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if restart.returncode != 0:
            raise AWGPanelError(restart.stderr.strip() or "dnsmasq не запустился")
        active = subprocess.run(
            ["systemctl", "is-active", "dnsmasq.service"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if active.stdout.strip() != "active":
            raise AWGPanelError("dnsmasq не находится в состоянии active")
    except Exception:
        if previous is None:
            DNSMASQ_CONFIG_PATH.unlink(missing_ok=True)
            subprocess.run(
                ["systemctl", "disable", "--now", "dnsmasq.service"],
                check=False, capture_output=True, text=True, timeout=30,
            )
        else:
            DNSMASQ_CONFIG_PATH.write_bytes(previous)
            os.chmod(DNSMASQ_CONFIG_PATH, 0o644)
            subprocess.run(
                ["systemctl", "restart", "dnsmasq.service"],
                check=False, capture_output=True, text=True, timeout=30,
            )
        raise
    return {
        "enabled": True,
        "mode": mode,
        "domains": sum(len(value) for value in domain_map.values()),
    }


def dns_runtime_status() -> dict[str, object]:
    settings = get_dns_traffic_settings()
    result = subprocess.run(["systemctl", "is-active", "dnsmasq.service"], capture_output=True, text=True, check=False)
    return {
        "mode": str(settings["mode"]),
        "active": result.stdout.strip() == "active",
        "advertise_to_clients": bool(settings["advertise_to_clients"]),
        "block_dot": bool(settings["block_dot"]),
        "upstreams": str(settings["upstreams"]),
    }


def _domain_matches(hostname: str, domain: str) -> bool:
    host = hostname.lower().rstrip(".")
    item = domain.lower().rstrip(".")
    return host == item or host.endswith("." + item)


def _ports_match(port: int | None, ports: tuple[str, ...]) -> bool:
    if not ports:
        return True
    if port is None:
        return False
    for item in ports:
        if "-" in item:
            start, end = map(int, item.split("-", 1))
            if start <= port <= end:
                return True
        elif int(item) == port:
            return True
    return False


def diagnose_route(
    *, client_id: int | None, destination: str, protocol: str, port: int | None
) -> dict[str, object]:
    host = destination.strip().lower().rstrip(".")
    resolved: list[str] = []
    try:
        address = ipaddress.ip_address(host)
        if address.version == 4:
            resolved = [str(address)]
    except ValueError:
        try:
            resolved = sorted({item[4][0] for item in socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)})
        except OSError:
            resolved = []
    client_address = ""
    if client_id:
        with connect() as con:
            row = con.execute("SELECT address FROM awg_clients WHERE id=? AND node_id IS NULL", (int(client_id),)).fetchone()
        if row is None:
            raise ValueError("Клиент не найден")
        client_address = str(row["address"])
    for rule in compile_rules():
        if rule.client_addresses and client_address not in rule.client_addresses:
            continue
        if rule.protocol != "any" and rule.protocol != protocol:
            continue
        if not _ports_match(port, rule.ports):
            continue
        destination_match = not (rule.list_items or rule.inline_domains or rule.inline_cidrs)
        domain_items = rule.inline_domains + (rule.list_items if rule.list_kind == "domains" else ())
        cidr_items = rule.inline_cidrs + (rule.list_items if rule.list_kind == "cidrs" else ())
        if domain_items and any(_domain_matches(host, item) for item in domain_items):
            destination_match = True
        if cidr_items and resolved:
            networks = [ipaddress.ip_network(item, strict=False) for item in cidr_items]
            destination_match = any(ipaddress.ip_address(address) in network for address in resolved for network in networks)
        if rule.invert:
            destination_match = not destination_match
        if not destination_match:
            continue
        return {
            "matched": True,
            "rule_id": rule.id,
            "priority": rule.priority,
            "rule_name": rule.name,
            "action_mode": rule.action_mode,
            "outbound_id": rule.outbound_id,
            "resolved": resolved,
        }
    return {"matched": False, "action_mode": "client-default", "resolved": resolved}


def apply_preset(key: str, *, outbound_id: int | None, replace_existing: bool = True) -> dict[str, object]:
    preset = next((item for item in PRESETS if item["key"] == key), None)
    if preset is None:
        raise ValueError("Неизвестный шаблон Traffic Rules")
    if preset["requires_outbound"] and outbound_id is None:
        raise ValueError("Для этого шаблона выберите Outbound")
    if outbound_id is not None:
        with connect() as con:
            outbound = con.execute("SELECT * FROM outbounds WHERE id=? AND enabled=1", (int(outbound_id),)).fetchone()
        if outbound is None:
            raise ValueError("Выбранный Outbound не найден или отключён")
    ensure_builtin_lists()
    used_slugs = [slug for _priority, _name, slug, _action in preset["rules"] if slug]
    domain_rules_present = False
    for slug in used_slugs:
        item = find_traffic_list_by_slug(str(slug))
        if str(item["kind"]) == "domains":
            domain_rules_present = True
        if str(item["source_type"]) == "url" and int(item["item_count"] or 0) == 0:
            refresh_traffic_list(int(item["id"]))
    if domain_rules_present:
        current_dns = get_dns_traffic_settings()
        save_dns_traffic_settings(
            mode="redirect",
            upstreams=str(current_dns["upstreams"]),
            advertise_to_clients=True,
            block_dot=bool(current_dns["block_dot"]),
        )
    with connect() as con:
        if replace_existing:
            con.execute("DELETE FROM traffic_rules WHERE system_key=''")
        for priority, name, slug, action in preset["rules"]:
            list_id = None
            if slug:
                row = con.execute("SELECT id FROM traffic_lists WHERE slug=?", (slug,)).fetchone()
                if row is None:
                    raise AWGPanelError(f"Встроенный список {slug} не найден")
                list_id = int(row["id"])
            con.execute(
                """
                INSERT INTO traffic_rules (
                    priority, name, enabled, client_ids, list_id, inline_domains,
                    inline_cidrs, protocol, ports, invert_match, schedule,
                    action_mode, outbound_id
                ) VALUES (?, ?, 1, '', ?, '', '', 'any', '', 0, '', ?, ?)
                """,
                (priority, name, list_id, action, int(outbound_id) if action == "outbound" else None),
            )
    return {"preset": preset["name"], "rules": len(preset["rules"])}


def preset_catalog() -> tuple[dict[str, Any], ...]:
    return PRESETS


def _rule_json_object(row: object) -> dict[str, object]:
    item = dict(row)
    return {
        "id": int(item["id"]) if item.get("id") is not None else None,
        "priority": int(item.get("priority", 100)),
        "name": str(item.get("name") or ""),
        "enabled": bool(item.get("enabled", True)),
        "clientIds": [
            int(value)
            for value in str(item.get("client_ids") or "").split(",")
            if value
        ],
        "listId": int(item["list_id"]) if item.get("list_id") is not None else None,
        "inlineDomains": str(item.get("inline_domains") or "").splitlines(),
        "inlineCIDRs": str(item.get("inline_cidrs") or "").splitlines(),
        "protocol": str(item.get("protocol") or "any"),
        "ports": str(item.get("ports") or ""),
        "invert": bool(item.get("invert_match", False)),
        "schedule": str(item.get("schedule") or ""),
        "action": str(item.get("action_mode") or "block"),
        "outboundId": int(item["outbound_id"]) if item.get("outbound_id") is not None else None,
    }


def traffic_rule_json_document(rule_id: int | None = None) -> str:
    if rule_id is None:
        rule: dict[str, object] = {
            "id": None,
            "priority": next_rule_priority(),
            "name": "Новое правило",
            "enabled": True,
            "clientIds": [],
            "listId": None,
            "inlineDomains": [],
            "inlineCIDRs": [],
            "protocol": "any",
            "ports": "",
            "invert": False,
            "schedule": "",
            "action": "block",
            "outboundId": None,
        }
    else:
        rule = _rule_json_object(find_traffic_rule(rule_id))
    return json.dumps(
        {
            "_sgAwgPanel": {"format": "traffic-rule-v1", "id": rule_id},
            "rule": rule,
        },
        ensure_ascii=False,
        indent=2,
    ) + "\n"


def rules_json_document() -> str:
    rules = [_rule_json_object(row) for row in list_traffic_rules(include_system=False)]
    return json.dumps({
        "_sgAwgPanel": {"format": "traffic-rules-v1"},
        "rules": rules,
    }, ensure_ascii=False, indent=2) + "\n"

def _reject_rule_unknown_keys(item: dict[str, object], path: str) -> None:
    allowed = {"id", "priority", "name", "enabled", "clientIds", "listId", "inlineDomains", "inlineCIDRs", "protocol", "ports", "invert", "schedule", "action", "outboundId"}
    unknown = sorted(set(item) - allowed)
    if unknown:
        raise ValueError(f"{path}: неизвестные поля: {', '.join(unknown)}")


def _parse_rule_json_item(item: object, path: str) -> dict[str, object]:
    if not isinstance(item, dict):
        raise ValueError(f"{path} должен быть объектом")
    _reject_rule_unknown_keys(item, path)
    client_ids = item.get("clientIds", [])
    if not isinstance(client_ids, list) or any(not isinstance(value, int) for value in client_ids):
        raise ValueError(f"{path}.clientIds должен быть массивом id")
    inline_domains = item.get("inlineDomains", [])
    inline_cidrs = item.get("inlineCIDRs", [])
    if not isinstance(inline_domains, list) or any(not isinstance(value, str) for value in inline_domains):
        raise ValueError(f"{path}.inlineDomains должен быть массивом строк")
    if not isinstance(inline_cidrs, list) or any(not isinstance(value, str) for value in inline_cidrs):
        raise ValueError(f"{path}.inlineCIDRs должен быть массивом строк")
    values = validate_rule_values({
        "name": item.get("name", ""),
        "priority": item.get("priority", 100),
        "enabled": item.get("enabled", True),
        "client_ids": ",".join(str(value) for value in client_ids),
        "list_id": item.get("listId"),
        "inline_domains": "\n".join(inline_domains),
        "inline_cidrs": "\n".join(inline_cidrs),
        "protocol": item.get("protocol", "any"),
        "ports": item.get("ports", ""),
        "invert_match": item.get("invert", False),
        "schedule": item.get("schedule", ""),
        "action_mode": item.get("action", "block"),
        "outbound_id": item.get("outboundId"),
        "allow_any": not item.get("listId") and not inline_domains and not inline_cidrs,
    })
    values["id"] = int(item["id"]) if isinstance(item.get("id"), int) else None
    return values


def parse_traffic_rule_json_document(
    text: str, *, expected_id: int | None = None
) -> dict[str, object]:
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON: строка {exc.lineno}, столбец {exc.colno}: {exc.msg}") from exc
    if not isinstance(document, dict):
        raise ValueError("JSON правила должен быть объектом")
    unknown = sorted(set(document) - {"_sgAwgPanel", "rule"})
    if unknown:
        raise ValueError("$: неизвестные поля: " + ", ".join(unknown))
    meta = document.get("_sgAwgPanel", {})
    if not isinstance(meta, dict):
        raise ValueError("_sgAwgPanel должен быть объектом")
    meta_unknown = sorted(set(meta) - {"format", "id", "note"})
    if meta_unknown:
        raise ValueError("_sgAwgPanel: неизвестные поля: " + ", ".join(meta_unknown))
    if meta.get("format") != "traffic-rule-v1":
        raise ValueError("_sgAwgPanel.format должен быть traffic-rule-v1")
    meta_id = meta.get("id")
    if expected_id is not None and meta_id not in (None, expected_id):
        raise ValueError("_sgAwgPanel.id не совпадает с редактируемым правилом")
    values = _parse_rule_json_item(document.get("rule"), "rule")
    item_id = values.get("id")
    if expected_id is not None and item_id not in (None, expected_id):
        raise ValueError("rule.id не совпадает с редактируемым правилом")
    return values


def parse_rules_json_document(text: str) -> list[dict[str, object]]:
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON: строка {exc.lineno}, столбец {exc.colno}: {exc.msg}") from exc
    if not isinstance(document, dict):
        raise ValueError("Traffic Rules JSON должен быть объектом")
    unknown = sorted(set(document) - {"_sgAwgPanel", "rules"})
    if unknown:
        raise ValueError("$: неизвестные поля: " + ", ".join(unknown))
    meta = document.get("_sgAwgPanel", {})
    if not isinstance(meta, dict):
        raise ValueError("_sgAwgPanel должен быть объектом")
    meta_unknown = sorted(set(meta) - {"format", "note"})
    if meta_unknown:
        raise ValueError("_sgAwgPanel: неизвестные поля: " + ", ".join(meta_unknown))
    if meta.get("format") != "traffic-rules-v1":
        raise ValueError("_sgAwgPanel.format должен быть traffic-rules-v1")
    items = document.get("rules")
    if not isinstance(items, list):
        raise ValueError("rules должен быть массивом")
    parsed = [_parse_rule_json_item(item, f"rules[{index}]") for index, item in enumerate(items)]
    seen_ids: dict[int, int] = {}
    seen_names: dict[str, int] = {}
    seen_priorities: dict[int, int] = {}
    for index, item in enumerate(parsed):
        item_id = item.get("id")
        if item_id is not None:
            item_id = int(item_id)
            if item_id in seen_ids:
                raise ValueError(f"rules[{index}].id: дубликат id {item_id}; впервые указан в rules[{seen_ids[item_id]}]")
            seen_ids[item_id] = index
        name = str(item.get("name") or "").strip().casefold()
        if name:
            if name in seen_names:
                raise ValueError(f"rules[{index}].name: дубликат имени; впервые указано в rules[{seen_names[name]}]")
            seen_names[name] = index
        priority = int(item.get("priority", 100))
        if priority in seen_priorities:
            raise ValueError(f"rules[{index}].priority: дубликат приоритета {priority}; впервые указан в rules[{seen_priorities[priority]}]")
        seen_priorities[priority] = index
    return parsed

def replace_rules_document(rules: list[dict[str, object]]) -> None:
    with connect() as con:
        con.execute("DELETE FROM traffic_rules WHERE system_key=''")
        for values in rules:
            con.execute(
                """
                INSERT INTO traffic_rules (
                    priority, name, enabled, client_ids, list_id, inline_domains,
                    inline_cidrs, protocol, ports, invert_match, schedule,
                    action_mode, outbound_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(values[key] for key in (
                    "priority", "name", "enabled", "client_ids", "list_id",
                    "inline_domains", "inline_cidrs", "protocol", "ports",
                    "invert_match", "schedule", "action_mode", "outbound_id",
                )),
            )


def active_schedule_signature(now: datetime | None = None) -> str:
    """Return a stable signature of the currently active ordered policy rules."""
    active = [
        {
            "id": rule.id,
            "priority": rule.priority,
            "schedule": rule.schedule,
        }
        for rule in compile_rules(now)
    ]
    return hashlib.sha256(
        json.dumps(active, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def traffic_schedule_tick() -> dict[str, object]:
    """Reapply traffic only when a schedule boundary changed active rules."""
    signature = active_schedule_signature()
    previous = ""
    try:
        previous_doc = json.loads(TRAFFIC_SCHEDULE_STATE_PATH.read_text(encoding="utf-8"))
        previous = str(previous_doc.get("signature", ""))
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        previous = ""
    if signature == previous:
        return {"changed": False, "signature": signature}

    # Imported lazily to avoid a module import cycle: egress imports the rule compiler.
    from .egress import apply_egress_runtime

    status = apply_egress_runtime()
    TRAFFIC_SCHEDULE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = TRAFFIC_SCHEDULE_STATE_PATH.with_suffix(".json.new")
    temporary.write_text(
        json.dumps(
            {
                "signature": signature,
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(TRAFFIC_SCHEDULE_STATE_PATH)
    return {"changed": True, "signature": signature, "status": status}


def refresh_auto_lists_and_apply() -> dict[str, object]:
    """Refresh remote lists with database/runtime rollback on apply failure."""
    # Imported lazily to avoid a module import cycle: egress imports this module.
    from .egress import mutate_traffic_and_apply

    return mutate_traffic_and_apply(refresh_auto_lists)
