"""Thread-safe snapshot of two hourly CU / BC ratio histories."""

from __future__ import annotations

import copy
import threading
from datetime import date, datetime, timedelta
from typing import Mapping, Sequence

from .ratio_history import build_ratio_series, trading_day_for
from .config import SHANGHAI_TZ


class RatioHistoryStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._payload = {
            "contracts": {},
            "m_plus_1": [],
            "m_plus_2": [],
            "status": "loading",
            "message": "等待一小时行情数据",
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
        points = all_m1 + all_m2
        if points:
            latest = max(datetime.fromisoformat(point["time"]) for point in points)
            anchor = trading_day_for(latest, calendar)
            selected = [day for day in calendar if anchor is not None and day <= anchor][-20:]
            selected_set = set(selected)
            m1 = [point for point in all_m1
                  if trading_day_for(datetime.fromisoformat(point["time"]), calendar)
                  in selected_set]
            m2 = [point for point in all_m2
                  if trading_day_for(datetime.fromisoformat(point["time"]), calendar)
                  in selected_set]
            observed = {
                trading_day_for(datetime.fromisoformat(point["time"]), calendar)
                for point in m1 + m2
            }
            message = "" if len(observed) >= 20 else "可用历史不足 20 个交易日"
            status = "ready"
        else:
            m1, m2 = [], []
            message = "暂无有效的一小时交易数据"
            status = "unavailable"
        self._replace({
            "contracts": contracts,
            "m_plus_1": m1,
            "m_plus_2": m2,
            "status": status,
            "message": message,
        })

    def set_unavailable(self, message: str) -> None:
        with self._lock:
            payload = {
                **self._payload,
                "m_plus_1": [],
                "m_plus_2": [],
                "status": "unavailable",
                "message": message,
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
