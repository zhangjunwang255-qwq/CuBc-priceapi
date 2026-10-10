# Hourly CU / BC Ratio Charts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add separate M+1 and M+2 hourly CU / BC ratio charts covering the latest 20 trading days, with closed sessions removed from each time axis.

**Architecture:** The existing TqSdk worker subscribes to four 3600-second K-line series and refreshes a thread-safe in-memory history snapshot. A pure calculator aligns each CU/BC pair and filters invalid bars; a read-only API exposes two independent series. The dashboard renders each series as one SVG chart using equal spacing between actual trading bars.

**Tech Stack:** Python 3.11 in deployment (Python 3.11+ for local tests), FastAPI, TqSdk 3.10+, `unittest`, HTML/CSS, native SVG and JavaScript, Node 22 for frontend tests.

**Spec:** `docs/superpowers/specs/2026-10-10-hourly-ratio-chart-design.md`

## Global Constraints

- Base commit: GitHub `main` at `2890da68e6e70d5e270407c5ec1df03ec61fb5a4`.
- Chart period: 3600 seconds; default range: latest 20 exchange trading days; timezone: `Asia/Shanghai`.
- M+1 uses SHFE.cu M+1 / INE.bc M+1; M+2 uses the matching M+2 pair.
- Only equal-timestamp bars with positive volume, finite close prices, and positive BC close produce a point; do not fill closed hours.
- Each chart has one line and its own time axis and value scale. The final forming bar is marked provisional and updates live.
- Keep `/api/dashboard` compatible; add no database or runtime chart dependency.
- Deliver a newly named ZIP backup to the desktop without overwriting the existing ZIP.

## File Map

`backend/ratio_history.py` handles calculation and trading-day mapping; `backend/ratio_store.py` owns the synchronized snapshot; `backend/tqsdk_worker.py` collects K-lines; `backend/main.py` exposes them. `frontend/ratio-chart.mjs` lays out and renders one SVG series, and `frontend/index.html` hosts two instances. Matching tests live under `tests/` and beside the frontend module.

## Review Focus

- CU and BC bars with different timestamps must never be divided together (Task 1 test).
- NaN, zero-volume, zero-denominator and duplicate bars must not yield misleading points (Task 1 test).
- Night-session bars before holidays and midnight must map to the correct exchange trading date (Task 1 test).
- Month rollover must replace contracts and history together; old series must not leak (Task 2 test).
- Missing credentials or unavailable history must produce an explicit empty state while existing quotes remain accessible (Tasks 2–4 tests).

---

### Task 1: Pure hourly ratio calculation

**Files:** Create `backend/ratio_history.py`; create `tests/test_ratio_history.py`.

**Interfaces:** `trading_day_for(ts: datetime, trading_dates: Sequence[date]) -> date | None`; `build_ratio_series(cu_rows: Sequence[Mapping], bc_rows: Sequence[Mapping], selected_days: Sequence[date], now: datetime) -> list[dict]`. Each output point has `time` (ISO 8601 with `+08:00`), `ratio` (four decimals), and `partial` (bool). Inputs are TqSdk DataFrame records containing nanosecond `datetime`, `close`, and `volume`; duplicate timestamps use the last valid record. The caller supplies the same 20 trading dates to both pair calculations.

- [ ] Write `unittest` cases asserting same-hour `72000/64000 == 1.125`, unequal timestamps are excluded, and invalid/duplicate bars yield no extra point.
- [ ] Add cases asserting Monday 21:00 maps to Tuesday, Tuesday 00:30 maps to Tuesday, the 21st trading day is excluded, and an active last bar is provisional while an elapsed bar is final.
- [ ] Run `python -m unittest tests.test_ratio_history -v`; confirm the new tests fail for the missing interfaces.
- [ ] Implement the two functions in `backend/ratio_history.py`, keeping all datetime conversion timezone aware and output sorted by actual K-line start time.
- [ ] Re-run the module tests and `python -m unittest discover -v`; confirm all pass, then commit Task 1 files.

### Task 2: Worker subscription and history cache

**Files:** Modify `backend/tqsdk_worker.py`; create `backend/ratio_store.py`; create `tests/test_ratio_store.py`; modify `tests/test_store.py` only if needed for rollover coverage.

**Interfaces:** `RatioHistoryStore.set_history(contracts: dict, trading_dates: Sequence[date], bars: Mapping[str, Sequence[Mapping]], now: datetime) -> None`; `RatioHistoryStore.set_unavailable(message: str) -> None`; `RatioHistoryStore.snapshot() -> dict` returns `contracts`, `m_plus_1`, `m_plus_2`, `status`, `message`, `revision`. Revision increments only when the payload changes. The store anchors one shared 20-day window to the latest valid bar across the four streams. `TqSdkWorker(..., ratio_store: RatioHistoryStore)` accepts the new cache; the worker owns four `get_kline_serial(symbol, 3600, data_length=800)` subscriptions and refreshes the exchange calendar daily.

- [ ] Write cache tests: four aligned synthetic streams produce two independent series on the same 20-day window; identical updates keep revision stable; changed prices and contract rollover increment revision and clear old points; empty bars or missing credentials return `status="unavailable"` with a message.
- [ ] Run `python -m unittest tests.test_ratio_store -v`; confirm failure before implementation.
- [ ] Implement the locked cache and wire the existing `TqApi`/`wait_update()` loop to TqSdk K-lines and `get_trading_calendar()`. Refresh only on initial load, changed K-lines, calendar-day change, or contract rollover; retain quote collection if history retrieval fails.
- [ ] Re-run `python -m unittest discover -v`; confirm all pass, then commit Task 2 files.

### Task 3: Read-only history endpoint

**Files:** Modify `backend/main.py`; create `tests/test_ratio_api.py`; update `README.md`.

**Interfaces:** `GET /api/ratio-history` returns `period_seconds: 3600`, `timezone: "Asia/Shanghai"`, `trading_days: 20`, `contracts`, `status`, `message`, `revision`, and independent `m_plus_1`/`m_plus_2` point arrays. Both arrays use Task 1's point shape. `/api/dashboard` keeps all existing fields and adds `ratio_history_revision` so the existing three-second dashboard poll can trigger a history fetch only when data changes.

- [ ] Create `.venv` with a local Python 3.11+ interpreter and install `backend/requirements.txt`; use `.venv/bin/python` for this and later Python verification commands.
- [ ] Write an endpoint test with injected cache data asserting both arrays, metadata, and the registered route path; add an unavailable-data case and a regression assertion for all prior `/api/dashboard` keys plus `ratio_history_revision`.
- [ ] Run `.venv/bin/python -m unittest tests.test_ratio_api -v`; confirm failure before implementation.
- [ ] Initialize one `RatioHistoryStore` in `main.py`, pass it to the worker, add the GET endpoint, and document its payload in `README.md`.
- [ ] Re-run `.venv/bin/python -m unittest discover -v`; confirm all pass, then commit Task 3 files.

### Task 4: Two independent charts and delivery

**Files:** Modify `frontend/index.html`; create `frontend/ratio-chart.mjs`; create `frontend/ratio-chart.test.mjs`.

**Interfaces:** `layoutSeries(points: Array<{time: string, ratio: number, partial: boolean}>, width: number, height: number) -> Array<{x: number, y: number, time: string, ratio: number, partial: boolean}>` gives consecutive valid bars equal x spacing; `renderRatioChart(svg, points, options)` draws one responsive line, axes, actual-time labels, and a hover readout. The page creates one M+1 chart and one M+2 chart from separate endpoint arrays.

- [ ] Write Node tests for equal x spacing across an overnight/weekend gap, independent y scaling, a one-point series, and empty input. Run `node --test frontend/ratio-chart.test.mjs`; confirm failure.
- [ ] Implement the SVG module and two chart panels in `frontend/index.html`; display their individual contracts, a provisional-point label, loading/unavailable messages, and fetch history on page load or when `ratio_history_revision` changes. Closed hours cause no history fetch unless source data actually changes.
- [ ] Run `node --test frontend/ratio-chart.test.mjs` and `.venv/bin/python -m unittest discover -v`; confirm all pass. Inspect both charts in a browser at wide and narrow widths using synthetic history, including a closed-session gap and empty data.
- [ ] Run `git diff --check`, inspect the final diff against the spec, and commit the frontend files. Package the committed source as a dated desktop ZIP, excluding `.git` and secrets; verify the ZIP file list and byte hashes against the source tree.
