"""Thread-safe in-memory quote store."""

from __future__ import annotations

import threading
from datetime import datetime

try:
    from .config import SHANGHAI_TZ, STALE_AFTER_SEC, flatten_contracts, get_contracts
except ImportError:  # pragma: no cover - direct ``uvicorn main:app`` execution
    from config import SHANGHAI_TZ, STALE_AFTER_SEC, flatten_contracts, get_contracts


def _market_age_seconds(value: str, now: datetime) -> int | None:
    if not value:
        return None
    try:
        market_dt = datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=SHANGHAI_TZ
        )
    except (TypeError, ValueError):
        return None
    return max(0, int((now - market_dt).total_seconds()))


def decorate_status(item: dict, now: datetime | None = None) -> dict:
    result = dict(item)
    current = now or datetime.now(SHANGHAI_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI_TZ)

    age = _market_age_seconds(result.get("datetime", ""), current)
    if result.get("expired"):
        status = "expired"
    elif not result.get("has_quote"):
        status = "no_quote"
    elif age is None or age > STALE_AFTER_SEC:
        status = "stale"
    else:
        status = "live"

    result["stale_seconds"] = age
    result["data_status"] = status
    result["is_fresh"] = status == "live"
    return result


class QuoteStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._contracts = get_contracts()
        self._latest: dict[str, dict] = {}
        self._previous: dict[str, float] = {}

    def set_contracts(self, contracts: dict[str, dict[str, str]]) -> None:
        active = set(flatten_contracts(contracts))
        with self._lock:
            self._contracts = {
                product: values.copy() for product, values in contracts.items()
            }
            self._latest = {
                symbol: quote
                for symbol, quote in self._latest.items()
                if symbol in active
            }
            self._previous = {
                symbol: price
                for symbol, price in self._previous.items()
                if symbol in active
            }

    def update(
        self,
        *,
        symbol: str,
        instrument_id: str,
        last_price: float,
        bid_price1: float | None,
        ask_price1: float | None,
        bid_volume1: float | None,
        ask_volume1: float | None,
        volume: float | None,
        open_interest: float | None,
        market_datetime: str,
        received_at: str,
        expired: bool,
    ) -> None:
        with self._lock:
            previous = self._previous.get(symbol)
            self._previous[symbol] = last_price
            self._latest[symbol] = {
                "symbol": symbol,
                "instrument_id": instrument_id,
                "last_price": last_price,
                "bid_price1": bid_price1,
                "ask_price1": ask_price1,
                "bid_volume1": bid_volume1,
                "ask_volume1": ask_volume1,
                "volume": volume,
                "open_interest": open_interest,
                "datetime": market_datetime,
                "received_at": received_at,
                "change": round(last_price - previous, 2) if previous is not None else 0,
                "has_quote": True,
                "expired": expired,
            }

    def mark_unavailable(
        self,
        *,
        symbol: str,
        instrument_id: str,
        market_datetime: str,
        received_at: str,
        expired: bool,
    ) -> None:
        with self._lock:
            old = dict(self._latest.get(symbol, {}))
            old.update(
                {
                    "symbol": symbol,
                    "instrument_id": instrument_id,
                    "datetime": market_datetime or old.get("datetime", ""),
                    "received_at": received_at,
                    "change": 0,
                    "has_quote": False,
                    "expired": expired,
                }
            )
            old.setdefault("last_price", None)
            old.setdefault("bid_price1", None)
            old.setdefault("ask_price1", None)
            old.setdefault("bid_volume1", None)
            old.setdefault("ask_volume1", None)
            old.setdefault("volume", None)
            old.setdefault("open_interest", None)
            self._latest[symbol] = old

    @property
    def contracts(self) -> dict[str, dict[str, str]]:
        with self._lock:
            return {
                product: values.copy()
                for product, values in self._contracts.items()
            }

    @property
    def symbols(self) -> list[str]:
        return flatten_contracts(self.contracts)

    def snapshot(self, now: datetime | None = None) -> dict[str, dict]:
        with self._lock:
            return {
                symbol: decorate_status(item, now)
                for symbol, item in self._latest.items()
            }

    def get(self, symbol: str, now: datetime | None = None) -> dict | None:
        return self.snapshot(now).get(symbol)

    def has_valid_quote(self) -> bool:
        with self._lock:
            return any(item.get("has_quote") for item in self._latest.values())
