# Cu / BC 实时行情仪表盘

> 沪铜 (SHFE.CU) + 国际铜 (INE.BC) 实时行情 API + Dashboard，部署于 Railway

---

## 目录

- [快速开始（本地）](#快速开始本地)
- [Railway 部署](#railway-部署)
- [环境变量说明](#环境变量说明)
- [API 文档](#api-文档)
- [本地开发](#本地开发)

---

## 快速开始（本地）

```bash
cd backend
pip install -r requirements.txt

# 方式一：匿名模式（免费行情，无需账号）
python main.py

# 方式二：使用天勤账号
TQ_ACCOUNT=你的账号 TQ_PASSWORD=你的密码 python main.py

# 浏览器打开 http://localhost:8000/frontend/index.html
```

---

## Railway 部署

### 1. 关联 GitHub 仓库

在 Railway Dashboard 点击 **New Project → Deploy from GitHub repo**，选择本仓库。

### 2. 配置环境变量

在 Railway 项目面板 → **Variables** 中添加：

| 变量名 | 示例值 | 必填 |
|--------|--------|------|
| `TQ_ACCOUNT` | `your_tq_account` | 推荐配置 |
| `TQ_PASSWORD` | `your_tq_password` | 推荐配置 |
| `TQ_SERVER` | `tcp://121.37.80.136:7701` | 可选（默认已有） |
| `NIXPACKS_PYTHON_VERSION` | `3.11` | 推荐设置，避免版本不匹配 |

> 💡 **不填写 `TQ_ACCOUNT` / `TQ_PASSWORD` 时**，程序以匿名模式启动（仅接收部分免费行情数据）。建议配置以获取完整行情。

### 3. 部署命令

Railway 会自动读取 `railway.toml` 中的配置：
```
cd backend && pip install -r requirements.txt && uvicorn main:app --host 0.0.0.0 --port $PORT
```

或手动在 **Start Command** 中填入：
```
cd backend && pip install -r requirements.txt && uvicorn main:app --host 0.0.0.0 --port $PORT
```

### 4. 访问 Dashboard

部署成功后，Railway 会分配一个 `.up.railway.app` 域名：
```
https://你的项目.up.railway.app/frontend/index.html
```

---

## 环境变量说明

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `TQ_ACCOUNT` | *(空)* | 天勤账号，未配置则匿名模式 |
| `TQ_PASSWORD` | *(空)* | 天勤密码，未配置则匿名模式 |
| `TQ_SERVER` | `tcp://121.37.80.136:7701` | 行情服务器地址 |
| `PORT` | `8000` | Railway 自动注入，无需手动配置 |
| `NIXPACKS_PYTHON_VERSION` | `3.11` | Python 版本（建议设置） |

> ⚠️ **重要**：所有账号信息通过 Railway 环境变量注入，**不写入代码或 `.env` 文件**，符合 12-Factor App 安全原则。

---

## API 文档

部署后自动生成 Swagger 文档：`/docs`

### 端点列表

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/dashboard` | 仪表盘完整数据（CU/BC 价格、跨月价差、比值） |
| `GET` | `/quote?symbol=CU` | 沪铜 M+1 合约快照 |
| `GET` | `/quote?symbol=BC` | 国际铜 M+1 合约快照 |
| `GET` | `/health` | 服务健康状态 + 缓存统计 |

### `/api/dashboard` 返回示例

```json
{
  "cu": {
    "m_plus_1": "SHFE.cu2606",
    "m_plus_2": "SHFE.cu2607",
    "price_m1": 72350.0,
    "price_m2": 72100.0,
    "spread": 250.0
  },
  "bc": {
    "m_plus_1": "INE.bc2606",
    "m_plus_2": "INE.bc2607",
    "price_m1": 64500.0,
    "price_m2": 64320.0,
    "spread": 180.0
  },
  "arb": {
    "m_plus_1_ratio": 1.1217,
    "m_plus_2_ratio": 1.1208
  },
  "update_time": "2026-05-07T14:35:00"
}
```

---

## 本地开发

### 项目结构

```
CuBc-priceapi/
├── backend/
│   ├── main.py              # FastAPI + TqSdk 后端
│   └── requirements.txt     # Python 依赖
├── frontend/
│   └── index.html           # 自动刷新 Dashboard
├── railway.toml             # Railway 部署配置
└── README.md
```

### 动态合约说明

程序根据**运行日期**自动计算当前月 M，并订阅 **M+1** 和 **M+2** 两个合约：

| 运行日期 | 沪铜 M+1 | 沪铜 M+2 | 国际铜 M+1 | 国际铜 M+2 |
|----------|----------|----------|------------|------------|
| 2026-05-07 | SHFE.cu2606 | SHFE.cu2607 | INE.bc2606 | INE.bc2607 |
| 2026-06-15 | SHFE.cu2607 | SHFE.cu2608 | INE.bc2607 | INE.bc2608 |
| 2026-12-20 | SHFE.cu2601 | SHFE.cu2602 | INE.bc2601 | INE.bc2602 |

跨年时年份自动滚动（如 2026-12 月的 M+2 = 2027-02 → cu2702）。

---

## 技术栈

- **后端**: FastAPI + TqSdk 异步行情
- **前端**: 原生 HTML/JS，3 秒轮询，无框架依赖
- **部署**: Railway（支持 GitHub 自动部署）
- **存储**: 纯内存缓存，无磁盘写入，无数据库依赖
