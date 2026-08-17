"""Resilient TqSdk quote collector with automatic monthly rollover."""

from __future__ import annotations

import logging
import math
import threading
import time
from contextlib import suppress
from datetime import datetime

try:
    from .config import (
        SHANGHAI_TZ,
        TQ_PASS,
        TQ_USER,
        WAIT_UPDATE_DEADLINE_SEC,
        flatten_contracts,
        get_contracts,
    )
    from .store import QuoteStore
except ImportError:  # pragma: no cover - direct ``uvicorn main:app`` execution
    from config import (
        SHANGHAI_TZ,
        TQ_PASS,
        TQ_USER,
        WAIT_UPDATE_DEADLINE_SEC,
        flatten_contracts,
        get_contracts,
    )
    from store import QuoteStore


log = logging.getLogger("cubc-tqsdk")

QUOTE_CHANGE_FIELDS = [
    "datetime",
    "last_price",
    "bid_price1",
    "ask_price1",
    "bid_volume1",
    "ask_volume1",
    "volume",
    "open_interest",
    "expired",
]


def clean_number(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


class AppState:
    def __init__(self) -> None:
        self.status = "Starting"
        self.error: str | None = None
        self.connected_at = ""


class TqSdkWorker:
    def __init__(
        self,
        store: QuoteStore,
        app_state: AppState,
        user: str = TQ_USER,
        password: str = TQ_PASS,
    ) -> None:
        self.store = store
        self.app_state = app_state
        self.user = user
        self.password = password
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not self.user or not self.password:
            self.app_state.status = "AuthRequired"
            self.app_state.error = "TQ_USER/TQ_PASS（或旧版 TQ_ACCOUNT/TQ_PASSWORD）未设置"
            log.error(self.app_state.error)
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="cubc-tqsdk-worker",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @property
    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _run(self) -> None:
        from tqsdk import TqApi, TqAuth

        retry_delay = 5

        while not self._stop.is_set():
            api = None
            rollover_requested = False
            active_contracts = get_contracts()
            active_symbols = flatten_contracts(active_contracts)
            self.store.set_contracts(active_contracts)
            self.app_state.status = "Connecting"
            self.app_state.error = None

            try:
                api = TqApi(auth=TqAuth(self.user, self.password))
                quotes = {symbol: api.get_quote(symbol) for symbol in active_symbols}
                initialized: set[str] = set()

                self.app_state.status = "Running"
                self.app_state.connected_at = datetime.now(SHANGHAI_TZ).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                retry_delay = 5
                log.info("TqSdk connected; subscribed: %s", active_symbols)

                while not self._stop.is_set():
                    new_contracts = get_contracts()
                    if new_contracts != active_contracts:
                        rollover_requested = True
                        self.app_state.status = "RollingOver"
                        log.info("Contract rollover: %s -> %s", active_contracts, new_contracts)
                        break

                    api.wait_update(deadline=time.time() + WAIT_UPDATE_DEADLINE_SEC)
                    received_at = datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M:%S")

                    for symbol, quote in quotes.items():
                        initial = symbol not in initialized
                        if not initial and not api.is_changing(quote, QUOTE_CHANGE_FIELDS):
                            continue
                        initialized.add(symbol)

                        last_price = clean_number(quote.last_price)
                        market_datetime = quote.datetime or ""
                        expired = bool(quote.expired)

                        if last_price is None:
                            self.store.mark_unavailable(
                                symbol=symbol,
                                instrument_id=quote.instrument_id or symbol,
                                market_datetime=market_datetime,
                                received_at=received_at,
                                expired=expired,
                            )
                            continue

                        self.store.update(
                            symbol=symbol,
                            instrument_id=quote.instrument_id or symbol,
                            last_price=last_price,
                            bid_price1=clean_number(quote.bid_price1),
                            ask_price1=clean_number(quote.ask_price1),
                            bid_volume1=clean_number(quote.bid_volume1),
                            ask_volume1=clean_number(quote.ask_volume1),
                            volume=clean_number(quote.volume),
                            open_interest=clean_number(quote.open_interest),
                            market_datetime=market_datetime,
                            received_at=received_at,
                            expired=expired,
                        )

            except Exception as exc:
                self.app_state.status = "Reconnecting"
                self.app_state.error = str(exc)
                log.exception("TqSdk failure; retrying in %s seconds", retry_delay)
            finally:
                if api is not None:
                    with suppress(Exception):
                        api.close()

            if self._stop.is_set():
                break
            if rollover_requested:
                continue
            self._stop.wait(retry_delay)
            retry_delay = min(retry_delay * 2, 300)

        self.app_state.status = "Stopped"
