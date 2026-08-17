# Cu / BC 实时行情 API

沪铜 `SHFE.cu` 与国际铜 `INE.bc` 的 M+1、M+2 实时行情 API 和仪表盘，数据来自 TqSdk。

API 中的 `last_price` 是**最新成交价**，不是交易所结算价。

## 合约规则

程序按北京时间，以运行月份为 M，订阅自然月 M+1 和 M+2，并在跨月运行时自动重新订阅：

| 运行日期 | 沪铜 M+1 | 沪铜 M+2 | 国际铜 M+1 | 国际铜 M+2 |
|---|---|---|---|---|
| 2026-05-07 | SHFE.cu2606 | SHFE.cu2607 | INE.bc2606 | INE.bc2607 |
| 2026-06-15 | SHFE.cu2607 | SHFE.cu2608 | INE.bc2607 | INE.bc2608 |
| 2026-12-20 | SHFE.cu2701 | SHFE.cu2702 | INE.bc2701 | INE.bc2702 |

跨年会同步滚动年份，例如 2026 年 12 月的 M+2 是 `cu2702` / `bc2702`。

## 主要能力

- TqSdk 标准 `TqAuth` 认证；
- 四个具体月份合约实时最新成交价；
- 北京时间合约计算与运行中自动换月；
- 按合约真实变化更新缓存；
- 无效 `NaN` 行情转换为 JSON 安全的 `null`；
- 保留交易所行情时间与 API 接收时间；
- `live` / `stale` / `no_quote` / `expired` 状态；
- 断线指数退避重连；
- HTTP 轮询和 WebSocket 推送；
- 无数据库依赖的纯内存缓存。

## 环境变量

| 变量 | 必填 | 默认值 | 说明 |
|---|---:|---:|---|
| `TQ_USER` | 是 | - | 快期账户用户名、手机号或邮箱 |
| `TQ_PASS` | 是 | - | 快期账户密码 |
| `PORT` | 否 | `8000` | HTTP 端口，Railway 会自动注入 |
| `STALE_AFTER_SEC` | 否 | `120` | 超过多少秒没有交易所行情即标记 stale |
| `WAIT_UPDATE_DEADLINE_SEC` | 否 | `2` | TqSdk 每轮最长等待时间 |

兼容旧环境变量 `TQ_ACCOUNT` / `TQ_PASSWORD`，推荐逐步迁移到 `TQ_USER` / `TQ_PASS`。

## 本地运行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt

export TQ_USER="你的快期账号"
export TQ_PASS="你的快期密码"

cd backend
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

打开：

- 仪表盘：`http://localhost:8000/`
- Swagger：`http://localhost:8000/docs`
- 健康检查：`http://localhost:8000/health`

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/dashboard` | CU/BC M+1、M+2 行情、价差和比值 |
| `GET` | `/quote` | 四个合约的全部快照 |
| `GET` | `/quote?symbol=CU` | 沪铜 M+1，兼容旧调用 |
| `GET` | `/quote?symbol=CU_M2` | 沪铜 M+2 |
| `GET` | `/quote?symbol=BC` | 国际铜 M+1，兼容旧调用 |
| `GET` | `/quote?symbol=BC_M2` | 国际铜 M+2 |
| `GET` | `/quote/{symbol}` | 映射名或完整合约代码 |
| `GET` | `/symbols` | 当前 M+1/M+2 映射 |
| `GET` | `/health` | 连接、线程、认证和缓存状态 |
| `WS` | `/ws/quote` | 每秒推送仪表盘数据 |

示例：

```json
{
  "status": "Running",
  "cu": {
    "m_plus_1": "SHFE.cu2609",
    "m_plus_2": "SHFE.cu2610",
    "price_m1": 78530.0,
    "price_m2": 78610.0,
    "spread": -80.0
  },
  "bc": {
    "m_plus_1": "INE.bc2609",
    "m_plus_2": "INE.bc2610",
    "price_m1": 69820.0,
    "price_m2": 69910.0,
    "spread": -90.0
  }
}
```

## 测试

```bash
python -m unittest discover -v
```

测试覆盖指定日期合约、跨年、无效行情、行情新鲜度和换月清理。

## Railway

仓库中的 `Dockerfile`、`nixpacks.toml` 和 `start.sh` 均可启动服务。在 Railway Variables 中至少设置：

```text
TQ_USER=你的快期账号
TQ_PASS=你的快期密码
```

部署完成后访问 Railway 分配的域名即可。
