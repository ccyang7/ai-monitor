# 上报接口

上报接口是平台唯一的公开写接口。每次模型调用完成后，向该接口发送一条记录。

```http
POST {平台地址}/api/v1/logs
X-API-Key: proj_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
Content-Type: application/json
```

## 认证：X-API-Key

上报接口使用 `X-API-Key` 认证，与网页端登录使用的 JWT（`Authorization: Bearer`）相互独立。

**请求体不包含 `project_id`**，该字段由平台根据 API Key 推导。使用其他项目的 API Key 只能向该 Key 所属项目写入数据。

## 请求体字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `provider` | string | 是 | 供应商，如 `deepseek`，长度 1~50 |
| `model` | string | 是 | 模型名，长度 1~100，**须与价格表完全一致才能计算成本** |
| `latency` | int | 是 | 耗时，**单位毫秒**（≥ 0） |
| `status` | string | 是 | 仅支持 `success` / `error` |
| `input_tokens` | int | 否 | 输入 Token，默认 0（≥ 0） |
| `cache_hit_tokens` | int | 否 | 输入 Token 中**命中缓存**的数量，默认 0（≥ 0，且不得大于 `input_tokens`） |
| `output_tokens` | int | 否 | 输出 Token，默认 0（≥ 0） |
| `total_tokens` | int | 否 | 不传时由服务端按 `input_tokens + output_tokens` 计算，**建议不传** |
| `input` | string | 否 | 提问原文或提示词 |
| `output` | string | 否 | 模型回答原文 |
| `error` | string | 否 | `status="error"` 时的错误信息 |
| `timestamp` | datetime | 否 | 调用发生时间（ISO 8601），不传则使用服务端当前时间 |

字段规则：

- **`total_tokens` 建议不传**，由服务端计算，避免两边统计口径不一致。
- **`cache_hit_tokens` 为命中数量**，是 `input_tokens` 的子集，不应重复计入 `total_tokens`。该值大于 `input_tokens` 时返回 422。
- **`input` / `output` 不限长度**，但不在列表接口返回，仅在详情抽屉中展示。正文较大时可只上报前若干千字，以降低带宽占用。
- **`error` 建议包含异常类型名**（如 `APITimeoutError: Request timed out`），异常分析按错误信息归类聚合。

> ⚠️ **`latency` 的单位是毫秒。** 部分 SDK 返回秒（float），直接上报会得到一条「0.0028 毫秒」的记录，需乘以 1000。

## 时间规则

`timestamp` **不带时区时按 UTC 解释**。

例如北京时间 `2026-10-10 15:30`，应发送 `2026-10-10T15:30:00+08:00`，或先转换为 UTC 后发送。不带时区且直接发送本地时间时，记录时间将整体偏移 8 小时，且列表中没有可见异常。

平台不校验 `timestamp` 与当前时间的先后，允许补报历史数据。记录会按 `timestamp` 落在列表中的对应位置。

## 响应

成功返回 **201**：

```json
{
  "id": 1024,
  "project_id": 13,
  "created_at": "2026-10-10T12:00:00.123456Z"
}
```

`created_at` 为列表页展示、排序和按时间筛选所使用的字段，取自上报的 `timestamp`。

## 成本怎么算

平台在写入时按当前模型价格计算成本，后续价格变更不会重算历史记录：

```text
cost = cache_hit_tokens / 1e6 × cache_hit_price
     + (input_tokens − cache_hit_tokens) / 1e6 × input_price
     + output_tokens / 1e6 × output_price
```

- 价格单位为**元 / 百万 Token**。
- 模型未配置缓存价时，命中部分按输入价计算。
- 模型名在价格表中无匹配时，记录正常写入，`estimated_cost` 为 `null`，页面显示「—」。该情况与免费模型的 `¥0.00` 含义不同；金额大于 0 但不足半分钱时显示 `<¥0.01`。

## 相关

- 错误含义与排查顺序：[错误码与处理](/docs/api/errors)
- 将上报写入业务代码：[把上报写进代码](/docs/getting-started/integration)
- 配置模型价格：[模型与价格](/docs/guide/models)
