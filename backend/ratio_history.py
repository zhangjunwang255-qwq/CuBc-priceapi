"""Build trading-session-only hourly CU / BC ratio series."""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Mapping, Sequence

from .config import SHANGHAI_TZ


def trading_day_for(ts: datetime, trading_dates: Sequence[date]) -> date | None:
    """Map a Beijing K-line start to its exchange trading day."""
    local = ts.replace(tzinfo=SHANGHAI_TZ) if ts.tzinfo is None else ts.astimezone(SHANGHAI_TZ)
    if local.hour >= 21:
        return next((day for day in sorted(trading_dates) if day > local.date()), None)
    return local.date() if local.date() in trading_dates else None


def _valid_bars(rows: Sequence[Mapping]) -> dict[int, float]:
    values = {}
    for row in rows:
        try:
            timestamp = int(row["datetime"])
            close = float(row["close"])
            volume = float(row["volume"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if timestamp <= 0 or not math.isfinite(close) or not math.isfinite(volume):
            continue
        if volume <= 0:
            continue
        values[timestamp] = close
    return values


def build_ratio_series(
    cu_rows: Sequence[Mapping],
    bc_rows: Sequence[Mapping],
    selected_days: Sequence[date],
    now: datetime,
) -> list[dict]:
    """Align two one-hour K-line streams and divide their valid closes."""
    cu = _valid_bars(cu_rows)
    bc = _valid_bars(bc_rows)
    local_now = now.replace(tzinfo=SHANGHAI_TZ) if now.tzinfo is None else now.astimezone(SHANGHAI_TZ)
    stamps = sorted(cu.keys() & bc.keys())
    latest = stamps[-1] if stamps else None
    points = []
    for stamp in stamps:
        denominator = bc[stamp]
        if denominator <= 0:
            continue
        bar_time = datetime.fromtimestamp(stamp // 1_000_000_000, SHANGHAI_TZ)
        if trading_day_for(bar_time, selected_days) is None:
            continue
        age = (local_now - bar_time).total_seconds()
        points.append(
            {
                "time": bar_time.isoformat(),
                "ratio": round(cu[stamp] / denominator, 4),
                "partial": stamp == latest and 0 <= age < 3600,
            }
        )
    return points
