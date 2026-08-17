"""FastAPI application for SHFE copper and INE bonded copper quotes."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

try:
    from .config import PORT, TQ_USER
    from .store import QuoteStore
    from .tqsdk_worker import AppState, TqSdkWorker
except ImportError:  # pragma: no cover - direct ``uvicorn main:app`` execution
    from config import PORT, TQ_USER
    from store import QuoteStore
    from tqsdk_worker import AppState, TqSdkWorker


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
log = logging.getLogger("cubc-api")

store = QuoteStore()
app_state = AppState()
worker = TqSdkWorker(store, app_state)


@asynccontextmanager
async def lifespan(_: FastAPI):
    worker.start()
    yield
    worker.stop()


app = FastAPI(
    title="Cu/BC Realtime Quote API",
    description="SHFE copper and INE bonded copper M+1/M+2 latest traded prices",
    version="2.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=frontend_dir), name="static")


def _mapped_symbols() -> dict[str, str]:
    contracts = store.contracts
    return {
        "CU": contracts["cu"]["m_plus_1"],
        "CU_M1": contracts["cu"]["m_plus_1"],
        "CU_M2": contracts["cu"]["m_plus_2"],
        "BC": contracts["bc"]["m_plus_1"],
        "BC_M1": contracts["bc"]["m_plus_1"],
        "BC_M2": contracts["bc"]["m_plus_2"],
    }


def _resolve_symbol(value: str) -> str:
    mapped = _mapped_symbols()
    key = value.upper()
    if key in mapped:
        return mapped[key]
    for symbol in store.symbols:
        if value.lower() == symbol.lower():
            return symbol
    raise HTTPException(404, f"Unknown symbol: {value}")


def _safe_price(quote: dict | None) -> float | None:
    if not quote or not quote.get("has_quote"):
        return None
    return quote.get("last_price")


def _dashboard_payload() -> dict:
    contracts = store.contracts
    quotes = store.snapshot()

    cu_m1 = contracts["cu"]["m_plus_1"]
    cu_m2 = contracts["cu"]["m_plus_2"]
    bc_m1 = contracts["bc"]["m_plus_1"]
    bc_m2 = contracts["bc"]["m_plus_2"]

    cu_q1, cu_q2 = quotes.get(cu_m1), quotes.get(cu_m2)
    bc_q1, bc_q2 = quotes.get(bc_m1), quotes.get(bc_m2)
    cu1, cu2 = _safe_price(cu_q1), _safe_price(cu_q2)
    bc1, bc2 = _safe_price(bc_q1), _safe_price(bc_q2)

    received_times = [
        q.get("received_at", "") for q in quotes.values() if q.get("received_at")
    ]

    return {
        "status": app_state.status,
        "error": app_state.error,
        "cu": {
            "m_plus_1": cu_m1,
            "m_plus_2": cu_m2,
            "price_m1": cu1,
            "price_m2": cu2,
            "spread": round(cu1 - cu2, 2) if cu1 is not None and cu2 is not None else None,
            "quote_m1": cu_q1,
            "quote_m2": cu_q2,
        },
        "bc": {
            "m_plus_1": bc_m1,
            "m_plus_2": bc_m2,
            "price_m1": bc1,
            "price_m2": bc2,
            "spread": round(bc1 - bc2, 2) if bc1 is not None and bc2 is not None else None,
            "quote_m1": bc_q1,
            "quote_m2": bc_q2,
        },
        "arb": {
            "m_plus_1_ratio": round(cu1 / bc1, 4)
            if cu1 is not None and bc1 not in (None, 0)
            else None,
            "m_plus_2_ratio": round(cu2 / bc2, 4)
            if cu2 is not None and bc2 not in (None, 0)
            else None,
        },
        "cache_size": len(quotes),
        "update_time": max(received_times, default=""),
    }


@app.get("/", response_class=HTMLResponse)
async def root():
    if frontend_dir.joinpath("index.html").exists():
        return HTMLResponse(frontend_dir.joinpath("index.html").read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Cu/BC API Running</h1>")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return await root()


@app.get("/api/dashboard")
def get_dashboard():
    return _dashboard_payload()


@app.get("/quote")
def get_quote(
    symbol: Optional[str] = Query(None, description="CU/BC/CU_M2/BC_M2 or raw code")
):
    if symbol is None:
        return {
            "status": app_state.status,
            "error": app_state.error,
            "data": store.snapshot(),
        }
    resolved = _resolve_symbol(symbol)
    data = store.get(resolved)
    if data is None:
        raise HTTPException(503, "Quote has not initialized yet")
    return data


@app.get("/quote/{symbol}")
def get_quote_one(symbol: str):
    resolved = _resolve_symbol(symbol)
    data = store.get(resolved)
    if data is None:
        raise HTTPException(503, "Quote has not initialized yet")
    return {"status": app_state.status, "data": data}


@app.get("/symbols")
def get_symbols():
    return {"status": app_state.status, "contracts": store.contracts, "symbols": store.symbols}


@app.get("/health")
def health():
    return {
        "ok": app_state.status == "Running" and worker.is_alive and store.has_valid_quote(),
        "status": app_state.status,
        "error": app_state.error,
        "connected_at": app_state.connected_at,
        "symbols": store.symbols,
        "cache_size": len(store.snapshot()),
        "worker_alive": worker.is_alive,
        "tq_auth": "configured" if TQ_USER else "missing",
    }


@app.websocket("/ws/quote")
async def websocket_quote(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(_dashboard_payload())
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=PORT, log_level="info")
