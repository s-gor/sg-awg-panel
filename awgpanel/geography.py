from __future__ import annotations

import ipaddress
import json
import re
import time
import urllib.error
import urllib.request
from typing import Any

_COUNTRY_CACHE: dict[str, tuple[str, float]] = {}
_CACHE_SECONDS = 24 * 60 * 60
_CODE_RE = re.compile(r"^[A-Z]{2}$")

# Names are intentionally concise. Unknown valid ISO codes are still shown as
# the code itself, so manual overrides never depend on this display table.
COUNTRY_NAMES_RU: dict[str, str] = {
    "AE": "ОАЭ", "AM": "Армения", "AR": "Аргентина", "AT": "Австрия",
    "AU": "Австралия", "AZ": "Азербайджан", "BE": "Бельгия", "BG": "Болгария",
    "BR": "Бразилия", "BY": "Беларусь", "CA": "Канада", "CH": "Швейцария",
    "CL": "Чили", "CN": "Китай", "CY": "Кипр", "CZ": "Чехия",
    "DE": "Германия", "DK": "Дания", "EE": "Эстония", "ES": "Испания",
    "FI": "Финляндия", "FR": "Франция", "GB": "Великобритания", "GE": "Грузия",
    "GR": "Греция", "HK": "Гонконг", "HR": "Хорватия", "HU": "Венгрия",
    "ID": "Индонезия", "IE": "Ирландия", "IL": "Израиль", "IN": "Индия",
    "IS": "Исландия", "IT": "Италия", "JP": "Япония", "KZ": "Казахстан",
    "KR": "Южная Корея", "LT": "Литва", "LU": "Люксембург", "LV": "Латвия",
    "MD": "Молдова", "ME": "Черногория", "MX": "Мексика", "NL": "Нидерланды",
    "NO": "Норвегия", "NZ": "Новая Зеландия", "PL": "Польша", "PT": "Португалия",
    "RO": "Румыния", "RS": "Сербия", "RU": "Россия", "SE": "Швеция",
    "SG": "Сингапур", "SI": "Словения", "SK": "Словакия", "TH": "Таиланд",
    "TR": "Турция", "TW": "Тайвань", "UA": "Украина", "US": "США",
    "UZ": "Узбекистан", "VN": "Вьетнам", "ZA": "ЮАР",
}


def normalize_country_code(value: object) -> str:
    code = str(value or "").strip().upper()
    return code if _CODE_RE.fullmatch(code) else ""


def country_flag(value: object) -> str:
    code = normalize_country_code(value)
    if not code:
        return "🌐"
    return "".join(chr(0x1F1E6 + ord(char) - ord("A")) for char in code)




def country_flag_asset(value: object) -> str:
    """Return the bundled SVG path for a country code.

    The browser receives a real image instead of a Unicode regional-indicator
    pair, so Windows cannot collapse the flag back to letters such as DE/US.
    """
    code = normalize_country_code(value)
    return f"flags/{code.lower()}.svg" if code in COUNTRY_NAMES_RU else "flags/unknown.svg"

def country_name(value: object) -> str:
    code = normalize_country_code(value)
    if not code:
        return "Страна не определена"
    return COUNTRY_NAMES_RU.get(code, code)


def country_display(value: object) -> str:
    code = normalize_country_code(value)
    return f"{country_flag(code)} {country_name(code)}" if code else "🌐 Страна не определена"


def _public_ipv4(value: object) -> str:
    text = str(value or "").strip()
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return ""
    return str(address) if address.version == 4 and address.is_global else ""


def _json_request(url: str, timeout: float) -> dict[str, Any]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "SG-AWG-Panel/0.7.0-RC4"},
    )
    with opener.open(request, timeout=timeout) as response:  # nosec B310
        payload = response.read(4096).decode("utf-8", "replace")
    parsed = json.loads(payload)
    return parsed if isinstance(parsed, dict) else {}


def detect_country_code(public_ipv4: object, *, force: bool = False, timeout: float = 2.5) -> str:
    """Best-effort country lookup for a public IPv4.

    Country lookup is never a hard dependency. Results are cached for a day;
    failures return an empty string and the UI uses a neutral globe.
    """
    address = _public_ipv4(public_ipv4)
    if not address:
        return ""
    cached = _COUNTRY_CACHE.get(address)
    if cached and not force and time.time() - cached[1] < _CACHE_SECONDS:
        return cached[0]

    lookups = (
        (f"https://api.country.is/{address}", lambda data: data.get("country")),
        (f"https://ipwho.is/{address}?fields=success,country_code", lambda data: data.get("country_code") if data.get("success", True) else ""),
    )
    for url, extract in lookups:
        try:
            code = normalize_country_code(extract(_json_request(url, timeout)))
        except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError, ValueError):
            continue
        if code:
            _COUNTRY_CACHE[address] = (code, time.time())
            return code
    _COUNTRY_CACHE[address] = ("", time.time())
    return ""
