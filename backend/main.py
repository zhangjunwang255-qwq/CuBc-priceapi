"""
CuBc-priceapi 实时行情后端
依赖 FastAPI + TqSdk 异步推送行情
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, date
from typing import Optional

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import tqsdk

# ─── 日志 ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("cubc-api")


# ─── 全局行情缓存 ────────────────────────────────────────────────────────────
# 结构: { symbol_code: { "last_price": float, "bid_price1": float, ... } }
quote_cache: dict = {}

# 全局 TqApi 实例（运行在独立线程，由 _api_loop 驱动）
_api: Optional[tqsdk.TqApi] = None
_loop: Optional[asyncio.AbstractEventLoop] = None

# ─── 合约代码动态生成 ────────────────────────────────────────────────────────
def _build_symbols() -> dict:
    """
    根据运行日期动态生成 M+1 / M+2 合约代码。
    沪铜: SHFE.cu{YY}{MM:02d}
    国际铜: INE.bc{YY}{MM:02d}
    跨年时年份自动滚动。
    """
    today = date.today()
    year = today.year          # 2026
    month = today.month        # 5
    yy   = year % 100          # 26

    # M+1、M+2（跨年+12 进位）
    m1 = month + 1
    y1 = yy
    if m1 > 12:
        m1 -= 12
        y1 = (year + 1) % 100

    m2 = month + 2
    y2 = yy
    if m2 > 12:
        m2 -= 12
        y2 = (year + 1) % 100

    cu_m1  = f"SHFE.cu{y1}{m1:02d}"
    cu_m2  = f"SHFE.cu{y2}{m2:02d}"
    bc_m1  = f"INE.bc{y1}{m1:02d}"
    bc_m2  = f"INE.bc{y2}{m2:02d}"

    logger.info(
        "生成的合约列表 | M+1 月=%d M+2 月=%d | "
        "CU: %s %s | BC: %s %s",
        m1, m2, cu_m1, cu_m2, bc_m1, bc_m2,
    )
    return {
        "cu":  {"m_plus_1": cu_m1,  "m_plus_2": cu_m2},
        "bc":  {"m_plus_1": bc_m1,  "m_plus_2": bc_m2},
    }


SYMBOLS = _build_symbols()
SYMBOL_CODES = list(SYMBOLS["cu"].values()) + list(SYMBOLS["bc"].values())


# ─── TqSdk 数据拉取循环（运行在专有线程）──────────────────────────────────────
def _api_loop():
    """
    在独立线程中运行 TqSdk 事件循环：
    - 初始化 TqApi（连接到 tcp://121.37.80.136:7701）
    - 订阅 SYMBOL_CODES 中所有合约
    - 实时更新全局 quote_cache
    """
    global _api, quote_cache

    try:
        logger.info("TqSdk 线程启动，连接行情服务器 …")
        _api = tqsdk.TqApi(
            # 行情服务器地址
            _td_url="tcp://121.37.80.136:7701",
            # 重连开关（断线自动重连，不崩溃）
            md_reconnect=True,
            # 仅接收行情数据，不需要交易
            front_debug=False,
        )

        # 注册所有合约订阅
        quotes = _api.subscribe_quotes(SYMBOL_CODES)
        logger.info("已订阅合约: %s", SYMBOL_CODES)

        while True:
            # wait_update 会阻塞直到行情有更新或超时（5 s 兜底心跳）
            _api.wait_update(deadline=_api.timeout(5))
            # 读取所有合约最新快照
            for sym in SYMBOL_CODES:
                try:
                    q = quotes[sym]
                    quote_cache[sym] = {
                        "symbol":        sym,
                        "last_price":    q.get("last_price"),
                        "bid_price1":   q.get("bid_price1"),
                        "ask_price1":   q.get("ask_price1"),
                        "bid_volume1":  q.get("bid_volume1"),
                        "ask_volume1":  q.get("ask_volume1"),
                        "volume":        q.get("volume"),
                        "open_interest": q.get("open_interest"),
                        "datetime":      q.get("datetime"),
                        "update_time":   datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                    }
                except Exception as e:
                    logger.warning("读取 %s 行情失败: %s", sym, e)

    except Exception as e:
        logger.critical("TqSdk 线程异常: %s", e, exc_info=True)
        raise


# ─── FastAPI 生命周期 ────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动 TqSdk 线程
    import threading
    t = threading.Thread(target=_api_loop, daemon=True, name="tqsdk-loop")
    t.start()
    logger.info("TqSdk 数据拉取线程已启动")
    yield
    # shutdown
    global _api
    if _api:
        _api.close()
    logger.info("FastAPI 已关闭")


app = FastAPI(
    title="CuBc 实时行情 API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Pydantic 模型 ───────────────────────────────────────────────────────────
class QuoteSnapshot(BaseModel):
    symbol:        str
    last_price:    Optional[float]
    bid_price1:    Optional[float]
    ask_price1:    Optional[float]
    bid_volume1:   Optional[float]
    ask_volume1:   Optional[float]
    volume:        Optional[float]
    open_interest: Optional[float]
    datetime:     Optional[str]
    update_time:  str


# ─── API 端点 ────────────────────────────────────────────────────────────────
@app.get("/quote", response_model=QuoteSnapshot)
async def get_quote(symbol: str = Query(..., description="CU 或 BC")):
    """
    单合约快照（调试用）。
    symbol=CU  → 返回沪铜 M+1
    symbol=BC  → 返回国际铜 M+1
    """
    sym_map = {"CU": SYMBOLS["cu"]["m_plus_1"], "BC": SYMBOLS["bc"]["m_plus_1"]}
    if symbol.upper() not in sym_map:
        raise HTTPException(400, f"未知 symbol: {symbol}，仅支持 CU / BC")

    sym_code = sym_map[symbol.upper()]
    data = quote_cache.get(sym_code)
    if not data:
        raise HTTPException(503, "行情尚未初始化，请稍后重试")

    return JSONResponse(content=data)


@app.get("/api/dashboard")
async def get_dashboard():
    """
    仪表盘数据接口。
    返回沪铜、国际铜 M+1/M+2 最新价格、跨月价差、以及 CU/BC 跨品种比值。
    所有数据取自内存缓存，无磁盘写入。
    """
    cu_m1  = SYMBOLS["cu"]["m_plus_1"]
    cu_m2  = SYMBOLS["cu"]["m_plus_2"]
    bc_m1  = SYMBOLS["bc"]["m_plus_1"]
    bc_m2  = SYMBOLS["bc"]["m_plus_2"]

    def p(sym: str) -> Optional[float]:
        d = quote_cache.get(sym)
        return d["last_price"] if d else None

    cu1 = p(cu_m1);  cu2 = p(cu_m2)
    bc1 = p(bc_m1);  bc2 = p(bc_m2)

    # 跨月价差（沪铜 M+1 - M+2）
    cu_spread  = round(cu1 - cu2, 2) if (cu1 is not None and cu2 is not None) else None
    # 国际铜跨月价差
    bc_spread  = round(bc1 - bc2, 2) if (bc1 is not None and bc2 is not None) else None

    # 跨品种比值：CU / BC（取 M+1 与 M+2 两个比值的平均）
    ratio_m1 = round(cu1 / bc1, 4) if (cu1 is not None and bc1 is not None) else None
    ratio_m2 = round(cu2 / bc2, 4) if (cu2 is not None and bc2 is not None) else None

    update_time = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    return {
        "cu": {
            "m_plus_1": cu_m1,
            "m_plus_2": cu_m2,
            "price_m1": cu1,
            "price_m2": cu2,
            "spread":   cu_spread,
        },
        "bc": {
            "m_plus_1": bc_m1,
            "m_plus_2": bc_m2,
            "price_m1": bc1,
            "price_m2": bc2,
            "spread":   bc_spread,
        },
        "arb": {
            "m_plus_1_ratio": ratio_m1,
            "m_plus_2_ratio": ratio_m2,
        },
        "update_time": update_time,
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "symbols": SYMBOL_CODES,
        "cache_size": len(quote_cache),
        "server": "tcp://121.37.80.136:7701",
    }


# ─── 启动入口（直接 python main.py 可运行）───────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
