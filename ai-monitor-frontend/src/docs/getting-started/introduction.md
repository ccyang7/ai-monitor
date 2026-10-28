# 平台介绍

AI Monitor 记录 AI 项目的模型调用，并将调用记录整理为四类信息：

1. **调用次数**：用量
2. **Token 消耗与预估成本**：成本
3. **异常事件**：异常
4. **单次调用的提问、回答与指标**：详情

## 接入方式

平台不提供 SDK，也不提供代理层。接入方在每次模型调用完成后，向平台发送一次 HTTP POST 请求，即完成一次上报。接口说明见 [上报接口](/docs/api/ingest)。

```text
你的 AI 项目 ──上报──> AI Monitor ──> 首页（我有哪些项目）
                                        └─> 项目页
                                             ├─ 概览   （这个项目现在怎么样）
                                             ├─ 调用日志（一条条翻）
                                             ├─ 异常   （哪里不对劲）
                                             └─ 设置   （改名字、管 Key、删项目）

AI 助手：任何页面右下角，用说话的方式查你的数据（能跨项目对比）
```

上报在后台异步发送，平台不可用时不影响接入方业务。

## 从哪读起

| 你的处境 | 从哪读起 |
| --- | --- |
| 第一次使用，尚未接入数据 | [快速开始](/docs/getting-started/quickstart) |
| 数据已接入，需要了解页面含义 | [基础概念](/docs/guide/concepts) → [项目概览](/docs/guide/overview) |
| 遇到具体问题 | [常见问题](/docs/faq/integration) |

如果页面上没有任何数据，请按 [接入验收清单](/docs/getting-started/verify) 逐项检查；如果已有数据但无法理解页面含义，请从基础概念开始阅读。

## 文档范围

| 内容 | 页面 |
| --- | --- |
| 注册、创建项目、获取 API Key | [快速开始](/docs/getting-started/quickstart) |
| 上报字段、时间规则、成本计算 | [上报接口](/docs/api/ingest) |
| 将上报写入业务代码（Python / Node.js） | [把上报写进代码](/docs/getting-started/integration) |
| 接入后的验收项 | [接入验收清单](/docs/getting-started/verify) |
| 页面与指标说明 | [基础概念](/docs/guide/concepts) 起的「使用指南」 |
| 连接失败、401、无数据 | [错误码与处理](/docs/api/errors)、[常见问题](/docs/faq/integration) |
