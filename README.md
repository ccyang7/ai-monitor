# AI Monitor

轻量级大模型使用监控与智能分析平台：把 AI 项目的每一次模型调用，变成四个直接结论 —— **调了多少次、花了多少钱、哪里不对劲、那次到底问了什么**。

> 设计理念：少一点复杂数据，多一点直接结论。刻意不做 Trace / Span / P95 这类专业概念，目标是让不熟悉可观测性工具的开发者也能直接看懂。

## 功能一览

| 模块 | 说明 |
|---|---|
| 数据上报 | 接入方调完模型后发一条 HTTP POST（无 SDK、无代理层），上报失败不影响接入方业务 |
| 首页 | 我有哪些项目、各自是否正常（有异常时卡片标红点） |
| 项目概览 | 调用次数 / Token / 成本 / 平均响应四张统计卡，趋势图与模型占比 |
| 调用日志 | 逐条明细，支持按模型 / 状态 / 时间 / Token 量筛选与全文搜索，详情抽屉看提问与回答原文 |
| 异常分析 | 四类异常（Token 突增 / 响应变慢 / 调用失败 / 错误率偏高），判定阈值可调 |
| 模型管理 | 模型价格表，支持缓存命中分档计价，是成本估算的数据源 |
| AI 助手 | 用自然语言查询自己的数据；所有数字来自真实数据库查询，只读 |
| 管理后台 | 管理员维护内置模型价格、管理用户（含删除）、查看助手用量；看不到用户的项目数据和对话正文 |

## 技术栈

- **后端**：Python 3.11+ / FastAPI / SQLAlchemy 2 / PostgreSQL / LangChain
- **前端**：Vue 3 / TypeScript / Naive UI / ECharts / Vite

## 目录结构

```text
ai-monitor-backend/     FastAPI 后端
  app/                  业务代码（api / services / models / schemas / ai）
  scripts/              验收脚本（check_*.py）与数据初始化脚本
  sql/                  建表 SQL：schema.sql 完整版 + migrations/ 增量脚本
ai-monitor-frontend/    Vue 3 前端
prototype/              早期的 HTML 页面原型
docs/                   开发文档（需求 / 设计 / 实施计划 / 使用手册）
```

> `docs/` 是过程性的开发笔记，已加入 .gitignore 不进版本控制，克隆仓库时不会带上。

## 快速开始

### 0. 环境准备

- Python 3.11+，依赖用 [uv](https://docs.astral.sh/uv/) 管理
- Node.js 18+
- PostgreSQL

### 1. 建库建表

创建数据库 `ai_monitor`，然后执行建表脚本：

```bash
cd ai-monitor-backend
psql -d ai_monitor -f sql/schema.sql
```

### 2. 启动后端

```bash
cd ai-monitor-backend
cp .env.example .env      # 填 DATABASE_URL 与 JWT_SECRET_KEY
uv sync
uv run uvicorn main:app --port 8001
```

健康检查：`curl http://localhost:8001/health` → `{"status":"ok"}`

### 3. 启动前端

```bash
cd ai-monitor-frontend
npm install
npm run dev               # http://localhost:5173，已配置代理到 8001
```

浏览器打开 `http://localhost:5173`，注册账号即可使用。

### 4. 可选配置（不配也能启动）

- `LLM_API_KEY`：AI 助手用的模型 Key（DeepSeek）。不填时助手接口返回 503，平台其他功能不受影响
- `TAVILY_API_KEY`：助手联网搜索用的 Key。不填时助手只是没有联网能力

## 接入你的 AI 项目

在平台创建项目、拿到 `proj_` 开头的 API Key 后，每次模型调用完成时报一条：

```bash
curl -X POST http://localhost:8001/api/v1/logs \
  -H "X-API-Key: proj_你的Key" \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "deepseek",
    "model": "deepseek-chat",
    "latency": 1200,
    "status": "success",
    "input_tokens": 800,
    "output_tokens": 300
  }'
```

上报有四条铁律：**调用完成后报**、**后台发送不阻塞业务**、**所有异常就地吞掉**、**失败调用也要报**。完整的接入指南（字段表、错误码、Python / Node.js 示例、五条验证）见 `docs/使用文档.md`。

## 开发

- 开发约定（代码风格、注释规范、五条设计约束）见 [CLAUDE.md](CLAUDE.md)
- 验收脚本：`scripts/check_*.py`（如 `uv run python scripts/check_api.py`），需要数据库连通
- 初始化内置模型价格：`uv run python scripts/init_models.py`
