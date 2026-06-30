from __future__ import annotations

AWG_GATEWAY = "awg_gateway"
BLOCK = "block"
OUTBOUND = "outbound"

VALID_EGRESS_MODES = frozenset({AWG_GATEWAY, BLOCK, OUTBOUND})

# Alpha 22 and earlier exported "direct". Keep import compatibility, but never
# store or export it again.
_LEGACY_ALIASES = {
    "direct": AWG_GATEWAY,
    "awg-gateway": AWG_GATEWAY,
    "awg gateway": AWG_GATEWAY,
    "gateway": AWG_GATEWAY,
}


def normalize_egress_mode(value: object, *, allow_legacy: bool = True) -> str:
    mode = str(value or "").strip().lower()
    if allow_legacy:
        mode = _LEGACY_ALIASES.get(mode, mode)
    if mode not in VALID_EGRESS_MODES:
        raise ValueError(
            "Режим должен быть awg_gateway, block или outbound"
        )
    return mode


def egress_mode_label(value: object) -> str:
    try:
        mode = normalize_egress_mode(value)
    except ValueError:
        return str(value or "")
    return {
        AWG_GATEWAY: "текущий сервер",
        BLOCK: "Block",
        OUTBOUND: "Outbound",
    }[mode]
