"""Resilient TqSdk quote collector with automatic monthly rollover."""

from __future__ import annotations

import logging
import math
import threading
import time
from contextlib import suppress
from datetime import date, datetime, timedelta

try:
    from .config import (
        SHANGHAI_TZ,
        TQ_PASS,
        TQ_USER,
        WAIT_UPDATE_DEADLINE_SEC,
        flatten_contracts,
        get_contracts,
    )
    from .ratio_store import RatioHistoryStore
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
    from ratio_store import RatioHistoryStore
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


async def _subscribe_hourly_klines(api, symbols, klines, state) -> None:
    """Register serials while TqApi's event loop is running, so calls do not wait."""
    try:
        for symbol in symbols:
            if symbol in klines:
                continue
            try:
                klines[symbol] = api.get_kline_serial(symbol, 3600, data_length=800)
                state["changed"] = True
            except Exception as exc:
                log.warning("Hourly K-line unavailable for %s: %s", symbol, exc)
    finally:
        state["pending"] = False
        state["retry_at"] = time.time() + 30


class CalendarLoader:
    """Fetch the exchange calendar off the quote loop, retrying failures later."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loaded_on: date | None = None
        self._dates: list[date] = []
        self._error: str | None = None
        self._pending = False
        self._retry_at = 0.0

    def request(self, api, day: date) -> None:
        with self._lock:
            if (self._loaded_on == day or self._pending
                    or time.monotonic() < self._retry_at):
                return
            self._pending = True
        threading.Thread(
            target=self._load, args=(api, day), daemon=True,
            name="cubc-trading-calendar",
        ).start()

    def _load(self, api, day: date) -> None:
        try:
            frame = api.get_trading_calendar(
                day - timedelta(days=90), day + timedelta(days=7)
            )
            dates = [value.date() if hasattr(value, "date") else value
                     for value in frame.loc[frame["trading"], "date"]]
            with self._lock:
                self._dates = dates
                self._loaded_on = day
                self._error = None
                self._retry_at = 0.0
        except Exception as exc:
            with self._lock:
                self._error = f"交易日历暂不可用: {exc}"
                self._retry_at = time.monotonic() + 60
            log.warning("Trading calendar unavailable: %s", exc)
        finally:
            with self._lock:
                self._pending = False

    def snapshot(self) -> tuple[date | None, list[date], str | None]:
        with self._lock:
            return self._loaded_on, self._dates.copy(), self._error


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
        *,
        ratio_store: RatioHistoryStore | None = None,
    ) -> None:
        self.store = store
        self.app_state = app_state
        self.ratio_store = ratio_store
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
            if self.ratio_store is not None:
                self.ratio_store.set_unavailable(self.app_state.error)
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
        calendar_loader = CalendarLoader()

        while not self._stop.is_set():
            api = None
            rollover_requested = False
            active_contracts = get_contracts()
            active_symbols = flatten_contracts(active_contracts)
            self.store.set_contracts(active_contracts)
            if (self.ratio_store is not None
                    and self.ratio_store.snapshot()["contracts"] != active_contracts):
                self.ratio_store.set_unavailable(
                    "等待当前合约的一小时行情数据", contracts=active_contracts
                )
            self.app_state.status = "Connecting"
            self.app_state.error = None

            try:
                api = TqApi(auth=TqAuth(self.user, self.password))
                quotes = {symbol: api.get_quote(symbol) for symbol in active_symbols}
                initialized: set[str] = set()
                klines = {}
                calendar_loaded_on = None
                history_initialized = False
                subscription = {"pending": False, "retry_at": 0, "changed": False}

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

                    if (self.ratio_store is not None
                            and len(klines) < len(active_symbols)
                            and not subscription["pending"]
                            and time.time() >= subscription["retry_at"]):
                        subscription["pending"] = True
                        api.create_task(_subscribe_hourly_klines(
                            api, active_symbols, klines, subscription
                        ))

                    api.wait_update(deadline=time.time() + WAIT_UPDATE_DEADLINE_SEC)
                    current_time = datetime.now(SHANGHAI_TZ)
                    received_at = current_time.strftime("%Y-%m-%d %H:%M:%S")

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

                    if subscription["changed"]:
                        history_initialized = False
                        subscription["changed"] = False

                    if self.ratio_store is not None and klines:
                        try:
                            calendar_loader.request(api, current_time.date())
                            ready_on, calendar_dates, calendar_error = calendar_loader.snapshot()
                            if ready_on != current_time.date():
                                self.ratio_store.set_unavailable(
                                    calendar_error or "正在加载交易日历"
                                )
                                continue
                            calendar_changed = calendar_loaded_on != ready_on
                            calendar_loaded_on = ready_on
                            changed = (not history_initialized or calendar_changed
                                       or self.ratio_store.needs_finalization(current_time) or any(
                                api.is_changing(frame.iloc[-1], ["datetime", "close", "volume"])
                                for frame in klines.values()
                            ))
                            if changed:
                                self.ratio_store.set_history(
                                    active_contracts,
                                    calendar_dates,
                                    {symbol: frame.to_dict("records")
                                     for symbol, frame in klines.items()},
                                    current_time,
                                )
                                history_initialized = True
                        except Exception as exc:
                            self.ratio_store.set_unavailable(f"一小时历史数据暂不可用: {exc}")
                            log.exception("Hourly ratio history refresh failed")
                    elif self.ratio_store is not None and not subscription["pending"]:
                        self.ratio_store.set_unavailable("四个合约的一小时行情均不可用")

            except Exception as exc:
                self.app_state.status = "Reconnecting"
                self.app_state.error = str(exc)
                if self.ratio_store is not None:
                    self.ratio_store.set_unavailable("行情连接中断，正在重新连接")
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
