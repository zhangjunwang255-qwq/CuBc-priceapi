"""Thread-safe snapshot of two hourly CU / BC ratio histories."""

from __future__ import annotations

import copy
import threading
from datetime import date, datetime, timedelta
from typing import Mapping, Sequence

try:
    from .ratio_history import build_ratio_series, trading_day_for
    from .config import SHANGHAI_TZ
except ImportError:  # pragma: no cover - direct ``uvicorn main:app`` execution
    from ratio_history import build_ratio_series, trading_day_for
    from config import SHANGHAI_TZ


class RatioHistoryStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._payload = {
            "contracts": {},
            "m_plus_1": [],
            "m_plus_2": [],
            "status": "loading",
            "message": "等待一小时行情数据",
            "series_messages": {"m_plus_1": "", "m_plus_2": ""},
        }
        self._revision = 0

    def _replace(self, payload: dict) -> None:
        with self._lock:
            if payload != self._payload:
                self._payload = copy.deepcopy(payload)
                self._revision += 1

    def set_history(
        self,
        contracts: dict,
        trading_dates: Sequence[date],
        bars: Mapping[str, Sequence[Mapping]],
        now: datetime,
    ) -> None:
        calendar = sorted(set(trading_dates))
        all_m1 = build_ratio_series(
            bars.get(contracts["cu"]["m_plus_1"], ()),
            bars.get(contracts["bc"]["m_plus_1"], ()), calendar, now,
        )
        all_m2 = build_ratio_series(
            bars.get(contracts["cu"]["m_plus_2"], ()),
            bars.get(contracts["bc"]["m_plus_2"], ()), calendar, now,
        )
        local_now = now.replace(tzinfo=SHANGHAI_TZ) if now.tzinfo is None else now.astimezone(SHANGHAI_TZ)
        current_days = [day for day in calendar if day <= local_now.date()]
        anchor = current_days[-1] if current_days else None
        for point in all_m1 + all_m2:
            point_time = datetime.fromisoformat(point["time"])
            if point_time <= local_now:
                point_day = trading_day_for(point_time, calendar)
                if point_day is not None and (anchor is None or point_day > anchor):
                    anchor = point_day
        selected = [day for day in calendar if anchor is not None and day <= anchor][-20:]
        selected_set = set(selected)
        m1 = [point for point in all_m1
              if trading_day_for(datetime.fromisoformat(point["time"]), calendar)
              in selected_set]
        m2 = [point for point in all_m2
              if trading_day_for(datetime.fromisoformat(point["time"]), calendar)
              in selected_set]
        status = "ready" if m1 or m2 else "unavailable"
        series_messages = {}
        for key, series in (("m_plus_1", m1), ("m_plus_2", m2)):
            observed = {
                trading_day_for(datetime.fromisoformat(point["time"]), calendar)
                for point in series
            }
            series_messages[key] = "" if len(observed) >= 20 else "可用历史不足 20 个交易日"
        if status == "ready":
            shortages = [label for key, label in (("m_plus_1", "M+1"), ("m_plus_2", "M+2"))
                         if series_messages[key]]
            message = f"{'、'.join(shortages)} 可用历史不足 20 个交易日" if shortages else ""
        else:
            message = "最近 20 个交易日暂无有效的一小时交易数据"
        self._replace({
            "contracts": contracts,
            "m_plus_1": m1,
            "m_plus_2": m2,
            "status": status,
            "message": message,
            "series_messages": series_messages,
        })

    def set_unavailable(self, message: str, contracts: dict | None = None) -> None:
        with self._lock:
            payload = {
                **self._payload,
                "contracts": contracts if contracts is not None else self._payload["contracts"],
                "m_plus_1": [],
                "m_plus_2": [],
                "status": "unavailable",
                "message": message,
                "series_messages": {"m_plus_1": "", "m_plus_2": ""},
            }
        self._replace(payload)

    def snapshot(self) -> dict:
        with self._lock:
            return {**copy.deepcopy(self._payload), "revision": self._revision}

    def needs_finalization(self, now: datetime) -> bool:
        local_now = now.replace(tzinfo=SHANGHAI_TZ) if now.tzinfo is None else now.astimezone(SHANGHAI_TZ)
        with self._lock:
            for key in ("m_plus_1", "m_plus_2"):
                points = self._payload[key]
                if points and points[-1]["partial"]:
                    bar_start = datetime.fromisoformat(points[-1]["time"])
                    if local_now >= bar_start + timedelta(hours=1):
                        return True
        return False
