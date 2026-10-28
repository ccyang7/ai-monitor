# 快速开始

本文档按以下顺序完成接入：确认平台可用 → 注册 → 创建项目 → 配置价格 → 发送第一条上报 → 查看数据。完整流程约五分钟。

概念说明见 [基础概念](/docs/guide/concepts)，可在接入完成后阅读。

## 开始之前：确认平台连得上

在编写代码之前，先确认平台服务正常：

```bash
curl http://<平台地址>:8001/health
# 期望：{"status":"ok"}
```

> ⚠️ 健康检查接口位于**根路径**，不在 `/api/v1` 下。业务接口均以 `/api/v1/` 为前缀，健康检查是唯一例外。`8001` 为本地开发端口，实际地址以平台部署方提供的为准。

如果平台与 AI 项目不在同一台机器上，平台必须以 `--host 0.0.0.0` 启动，并放行对应端口。uvicorn 默认仅监听 `127.0.0.1`，未监听对外地址时，接入方会收到「连接被拒绝」，而平台日志中没有对应请求，表明请求未到达平台。

## 第一步：注册与登录

注册需要**用户名和密码**，邮箱为选填项，不影响任何功能。

登录状态有效期为 **7 天**，过期后重新登录即可。当前版本不提供自助修改密码与找回密码功能。

## 第二步：创建项目并获取 API Key

登录后进入首页，点击虚线卡片「创建项目」，填写名称、描述和技术栈。

平台以**项目**为数据隔离单位，**一个 AI 项目对应一个平台项目**。调用记录、成本与统计数据均按项目隔离，不同 AI 项目请分别创建平台项目。

创建成功后，弹窗会展示 **API Key 明文**。

> ⚠️ **API Key 明文仅展示一次。** 平台仅存储其 SHA-256 哈希，无法还原明文。如未保存，请在项目的「设置」页吊销后重新生成，详见 [项目设置与 Key](/docs/guide/project-settings)。

API Key 为 `proj_` 开头的 48 位字符串。请将其存入项目的环境变量（推荐 `.env`）：

```bash
AI_MONITOR_BASE_URL=http://<平台地址>:8001
AI_MONITOR_API_KEY=proj_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

> ⚠️ **请勿将 API Key 写入代码或提交至 Git。** `.env` 应加入 `.gitignore`。API Key 可随时吊销，吊销后立即生效，使用该 Key 的上报将返回 401。

## 第三步：确认模型价格

平台根据**模型名**匹配价格表中的单价计算成本。模型名未匹配时，调用记录仍会入库，成本列显示「—」，而非 0。

平台预置了一批常用模型（deepseek、openai、硅基流动）。完整价格表与新增模型的方法见 [模型与价格](/docs/guide/models)。

模型不在价格表中时，有两种处理方式：

- **不需要成本数据**：直接接入，成本列显示「—」，其余统计与筛选不受影响
- **需要成本数据**：在「模型管理」页新增模型价格

> ⚠️ **模型名必须与上报的 `model` 字段完全一致，且区分大小写。** `gpt-4o` 与 `GPT-4o` 会被视为两个模型。

## 第四步：使用 curl 完成首次上报

建议在编写业务代码之前先执行以下命令，以排除网络、Key 和字段三类问题。

### 成功调用

```bash
curl -X POST http://<平台地址>:8001/api/v1/logs \
  -H "X-API-Key: proj_你的Key" \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "deepseek",
    "model": "deepseek-chat",
    "latency": 1200,
    "status": "success",
    "input_tokens": 800,
    "output_tokens": 300,
    "input": "你好",
    "output": "你好！有什么可以帮你的？"
  }'
```

期望返回 **201**：

```json
{"id":1024,"project_id":13,"created_at":"2026-10-10T12:00:00.123456Z"}
```

### 失败调用

```bash
curl -X POST http://<平台地址>:8001/api/v1/logs \
  -H "X-API-Key: proj_你的Key" \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "deepseek",
    "model": "deepseek-chat",
    "latency": 30000,
    "status": "error",
    "error": "APITimeoutError: Request timed out"
  }'
```

失败调用不包含 Token 数据属正常情况：厂商在调用失败时通常不返回用量，Token 字段可省略，平台按 0 存储。异常分析依据失败次数与错误信息统计，不使用 Token 数据。

> ⚠️ **Windows 用户**：`cmd.exe` 不以单引号作为字符串定界符，上述命令会报错。请在 **Git Bash** 中执行，或使用 Postman / Apifox 等工具。

字段含义、时间规则与响应格式见 [上报接口](/docs/api/ingest)。

## 第五步：在平台中查看数据

登录平台，进入对应项目，打开「调用日志」页。上述两条记录应已显示，且时间、模型与耗时一致。

至此接口已连通，后续工作是将上报逻辑写入业务代码。

## 后续步骤

- 将上报写入业务代码（Python / Node.js 示例）：[把上报写进代码](/docs/getting-started/integration)
- 逐项完成接入验收：[接入验收清单](/docs/getting-started/verify)
- 了解页面指标含义：[基础概念](/docs/guide/concepts)
