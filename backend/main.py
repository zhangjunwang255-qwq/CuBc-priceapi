"""
CuBc-priceapi 实时行情后端
依赖 FastAPI + TqSdk 异步推送行情

Railway 部署时需在 Dashboard → Variables 设置以下环境变量：
  TQ_ACCOUNT   天勤账号
  TQ_PASSWORD  天勤密码
  TQ_SERVER    行情服务器地址（默认 tcp://121.37.80.136:7701）
  PORT         Railway 自动注入，无需手动配置

启动命令：
  uvicorn main:app --host 0.0.0.0 --port $PORT
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, date
from typing import Optional

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger("cubc-api")


# ─── 全局行情缓存（纯内存，无磁盘写入）───────────────────────────────────────
quote_cache: dict = {}

# ─── TqSdk 连接参数（从环境变量读取）────────────────────────────────────────
TQ_ACCOUNT  = os.getenv("TQ_ACCOUNT",  "")
TQ_PASSWORD = os.getenv("TQ_PASSWORD", "")
TQ_SERVER   = os.getenv("TQ_SERVER",   "tcp://121.37.80.136:7701")

if TQ_ACCOUNT and TQ_PASSWORD:
    logger.info("使用天勤账号登录: %s", TQ_ACCOUNT)
    TQAPI_KWARGS = {
        "user_lang":       "zh_CN",
        "auth":            None,            # TqAuth 对象由线程内构造
        "_td_url":         TQ_SERVER,
        "md_reconnect":    True,
        "front_debug":     False,
    }
else:
    logger.info("未配置 TQ_ACCOUNT/TQ_PASSWORD，采用匿名模式（仅免费行情）")
    TQAPI_KWARGS = {
        "_td_url":      TQ_SERVER,
        "md_reconnect": True,
        "front_debug":  False,
    }


# ─── 动态合约代码生成 ────────────────────────────────────────────────────────
def _build_symbols() -> dict:
    """
    根据运行日期动态生成 M+1 / M+2 合约代码。
    沪铜:  SHFE.cu{YY}{MM:02d}
    国际铜: INE.bc{YY}{MM:02d}
    跨年时月份 +12 进位，年份同步滚动。
    """
    today  = date.today()
    year   = today.year
    month  = today.month
    yy     = year % 100

    def roll(yy_, mm_):
        if mm_ > 12:
            return (yy_ + 1) % 100, mm_ - 12
        return yy_, mm_

    yy1, mm1 = roll(yy, month + 1)
    yy2, mm2 = roll(yy, month + 2)

    cu_m1 = f"SHFE.cu{yy1}{mm1:02d}"
    cu_m2 = f"SHFE.cu{yy2}{mm2:02d}"
    bc_m1 = f"INE.bc{yy1}{mm1:02d}"
    bc_m2 = f"INE.bc{yy2}{mm2:02d}"

    logger.info(
        "动态合约 | 当前月=%d → M+1=%d M+2=%d | "
        "CU: %s %s | BC: %s %s",
        month, mm1, mm2, cu_m1, cu_m2, bc_m1, bc_m2,
    )
    return {
        "cu": {"m_plus_1": cu_m1, "m_plus_2": cu_m2},
        "bc": {"m_plus_1": bc_m1, "m_plus_2": bc_m2},
    }


SYMBOLS     = _build_symbols()
SYMBOL_CODES = list(SYMBOLS["cu"].values()) + list(SYMBOLS["bc"].values())


# ─── TqSdk 数据拉取线程 ──────────────────────────────────────────────────────
def _api_loop():
    """
    在独立线程中运行 TqSdk 事件循环：
    - 连接 TQ_SERVER
    - 订阅 SYMBOL_CODES 所有合约
    - 实时更新全局 quote_cache（纯内存）
    - md_reconnect=True，断线自动重连，不崩溃
    """
    import tqsdk

    try:
        logger.info("TqSdk 线程启动，连接 %s …", TQ_SERVER)

        if TQ_ACCOUNT and TQ_PASSWORD:
            auth = tqsdk.TqAuth(TQ_ACCOUNT, TQ_PASSWORD)
            api_kwargs = {
                **TQAPI_KWARGS,
                "auth": auth,
            }
        else:
            api_kwargs = TQAPI_KWARGS.copy()

        api = tqsdk.TqApi(**api_kwargs)
        quotes = api.subscribe_quotes(SYMBOL_CODES)
        logger.info("已订阅合约: %s", SYMBOL_CODES)

        while True:
            # wait_update 阻塞直到行情更新或 5 s 心跳超时
            api.wait_update(deadline=api.timeout(5))

            for sym in SYMBOL_CODES:
                try:
                    q = quotes[sym]
                    quote_cache[sym] = {
                        "symbol":         sym,
                        "last_price":     q.get("last_price"),
                        "bid_price1":     q.get("bid_price1"),
                        "ask_price1":     q.get("ask_price1"),
                        "bid_volume1":    q.get("bid_volume1"),
                        "ask_volume1":    q.get("ask_volume1"),
                        "volume":         q.get("volume"),
                        "open_interest":  q.get("open_interest"),
                        "datetime":       q.get("datetime"),
                        "update_time":    datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                    }
                except Exception as e:
                    logger.warning("读取 %s 行情失败: %s", sym, e)

    except Exception as e:
        logger.critical("TqSdk 线程异常: %s", e, exc_info=True)
        raise


# ─── FastAPI 生命周期 ────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    import threading
    t = threading.Thread(target=_api_loop, daemon=True, name="tqsdk-loop")
    t.start()
    logger.info("TqSdk 数据拉取线程已启动（环境变量配置: TQ_ACCOUNT=%s)",
                "已配置" if TQ_ACCOUNT else "未配置（匿名模式）")
    yield
    logger.info("FastAPI 已关闭")


app = FastAPI(
    title="CuBc 实时行情 API",
    version="1.1.0",
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
    symbol:         str
    last_price:     Optional[float]
    bid_price1:     Optional[float]
    ask_price1:     Optional[float]
    bid_volume1:    Optional[float]
    ask_volume1:    Optional[float]
    volume:         Optional[float]
    open_interest:  Optional[float]
    datetime:       Optional[str]
    update_time:    str


# ─── API 端点 ────────────────────────────────────────────────────────────────
@app.get("/quote", response_model=QuoteSnapshot)
async def get_quote(symbol: str = Query(..., description="CU 或 BC")):
    """
    单合约快照（调试用）。
    symbol=CU → 沪铜 M+1
    symbol=BC → 国际铜 M+1
    """
    sym_map = {"CU": SYMBOLS["cu"]["m_plus_1"], "BC": SYMBOLS["bc"]["m_plus_1"]}
    key = symbol.upper()
    if key not in sym_map:
        raise HTTPException(400, f"未知 symbol: {symbol}，仅支持 CU / BC")

    data = quote_cache.get(sym_map[key])
    if not data:
        raise HTTPException(503, "行情尚未初始化，请稍后重试")

    return JSONResponse(content=data)


@app.get("/api/dashboard")
async def get_dashboard():
    """
    仪表盘数据接口。
    返回沪铜 / 国际铜 M+1 / M+2 最新价格、跨月价差、CU/BC 比值。
    所有数据取自内存缓存，无磁盘写入。
    """
    def p(sym: str) -> Optional[float]:
        d = quote_cache.get(sym)
        return d["last_price"] if d else None

    cu_m1 = SYMBOLS["cu"]["m_plus_1"]
    cu_m2 = SYMBOLS["cu"]["m_plus_2"]
    bc_m1 = SYMBOLS["bc"]["m_plus_1"]
    bc_m2 = SYMBOLS["bc"]["m_plus_2"]

    cu1 = p(cu_m1);  cu2 = p(cu_m2)
    bc1 = p(bc_m1);  bc2 = p(bc_m2)

    cu_spread = round(cu1 - cu2, 2) if (cu1 is not None and cu2 is not None) else None
    bc_spread = round(bc1 - bc2, 2) if (bc1 is not None and bc2 is not None) else None
    ratio_m1  = round(cu1 / bc1, 4) if (cu1 is not None and bc1 is not None) else None
    ratio_m2  = round(cu2 / bc2, 4) if (cu2 is not None and bc2 is not None) else None

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
        "update_time": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }


@app.get("/health")
async def health():
    return {
        "status":    "ok",
        "symbols":  SYMBOL_CODES,
        "cache_size": len(quote_cache),
        "tq_server":  TQ_SERVER,
        "tq_auth":    "已配置" if TQ_ACCOUNT else "匿名模式",
    }


# ─── 直接运行入口（本地调试用）───────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
