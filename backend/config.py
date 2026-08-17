"""Runtime settings and CU/BC contract rollover rules."""

from __future__ import annotations

import os
from datetime import date, datetime
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

TQ_USER = os.getenv("TQ_USER") or os.getenv("TQ_ACCOUNT", "")
TQ_PASS = os.getenv("TQ_PASS") or os.getenv("TQ_PASSWORD", "")
PORT = int(os.getenv("PORT", "8000"))

WAIT_UPDATE_DEADLINE_SEC = float(os.getenv("WAIT_UPDATE_DEADLINE_SEC", "2"))
STALE_AFTER_SEC = int(os.getenv("STALE_AFTER_SEC", "120"))


def add_months(year: int, month: int, offset: int) -> tuple[int, int]:
    """Return ``(year, month)`` after adding calendar months."""
    total = year * 12 + month - 1 + offset
    result_year, zero_based_month = divmod(total, 12)
    return result_year, zero_based_month + 1


def _symbol(exchange: str, product: str, year: int, month: int) -> str:
    return f"{exchange}.{product}{year % 100:02d}{month:02d}"


def get_contracts(now: date | datetime | None = None) -> dict[str, dict[str, str]]:
    """Build calendar-month M+1 and M+2 contracts in Asia/Shanghai time."""
    current = now or datetime.now(SHANGHAI_TZ)
    m1_year, m1_month = add_months(current.year, current.month, 1)
    m2_year, m2_month = add_months(current.year, current.month, 2)

    return {
        "cu": {
            "m_plus_1": _symbol("SHFE", "cu", m1_year, m1_month),
            "m_plus_2": _symbol("SHFE", "cu", m2_year, m2_month),
        },
        "bc": {
            "m_plus_1": _symbol("INE", "bc", m1_year, m1_month),
            "m_plus_2": _symbol("INE", "bc", m2_year, m2_month),
        },
    }


def flatten_contracts(contracts: dict[str, dict[str, str]]) -> list[str]:
    return [
        contracts["cu"]["m_plus_1"],
        contracts["cu"]["m_plus_2"],
        contracts["bc"]["m_plus_1"],
        contracts["bc"]["m_plus_2"],
    ]
