# 把上报写进代码

本文档说明如何将上报逻辑写入业务代码。字段与响应格式见 [上报接口](/docs/api/ingest)。

## 四条要求

上报实现必须满足以下四条要求：

1. **后台发送**：使用线程或协程，不阻塞主流程
2. **短超时**：2~5 秒
3. **捕获所有异常**：上报失败仅记录本地日志，不得向上抛出
4. **失败调用也上报**：仅上报成功调用会遗漏出问题的部分

上报时机为**调用完成后**，不在调用前上报。调用完成后才能取得 `output`、Token 数量、`latency` 和 `status`。

> ⚠️ **请勿将上报实现为同步调用。** 同步上报会使平台故障传导至业务系统，平台重启时业务也会中断。因此 [接入验收清单](/docs/getting-started/verify) 将「关闭平台后运行一次业务」列为必测项。

## Python 示例

```python
"""AI Monitor 上报封装。后台线程发送，失败仅记录日志，不向业务抛出异常。"""

import logging
import os
import threading
import time

import httpx

logger = logging.getLogger(__name__)

# 平台地址与 API Key 从环境变量读取，不写死在代码里
BASE_URL = os.getenv("AI_MONITOR_BASE_URL", "")
API_KEY = os.getenv("AI_MONITOR_API_KEY", "")

# 上报超时设短：平台响应慢时，最多等 3 秒就放弃
TIMEOUT_SECONDS = 3.0


def report(
    model: str,
    *,
    input_text: str | None,
    output_text: str | None,
    input_tokens: int,
    output_tokens: int,
    latency_ms: float,
    status: str,
    error: str | None = None,
    provider: str = "deepseek",
    cache_hit_tokens: int = 0,
) -> None:
    """上报一次模型调用。未配置环境变量时直接返回，上报失败不影响调用方。"""
    # 没配置环境变量时直接返回：本地开发和测试环境不需要上报
    if not (BASE_URL and API_KEY):
        return

    # 请求体字段见「上报接口」；project_id 由平台根据 API Key 自动确定
    payload = {
        "provider": provider,
        "model": model,
        "latency": int(latency_ms),
        "status": status,
        "input_tokens": input_tokens,
        "cache_hit_tokens": cache_hit_tokens,
        "output_tokens": output_tokens,
        "input": input_text,
        "output": output_text,
    }
    if error:
        payload["error"] = error

    # 真正的网络请求放在独立函数里，交给后台线程执行
    def _send() -> None:
        try:
            httpx.post(
                f"{BASE_URL}/api/v1/logs",
                json=payload,
                headers={"X-API-Key": API_KEY},
                timeout=TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 —— 上报失败不得影响业务，此处必须捕获全部异常
            logger.warning("AI Monitor 上报失败(已忽略): %s", exc)

    # daemon=True 表示守护线程：主程序退出时不会为了等它而卡住
    threading.Thread(target=_send, daemon=True).start()
```

调用示例：成功与失败两条路径均需上报。

```python
started = time.perf_counter()  # perf_counter 返回秒，因此需要乘以 1000
try:
    resp = client.chat.completions.create(model=MODEL, messages=...)
except Exception as exc:
    # 失败的调用也要上报：异常分析需要看到失败记录
    report(
        MODEL,
        input_text=prompt, output_text=None,
        input_tokens=0, output_tokens=0,
        latency_ms=(time.perf_counter() - started) * 1000,
        status="error", error=str(exc),
    )
    raise  # 业务异常正常抛出，与上报无关

# 成功时上报完整用量；成功与失败两条路径都报，平台才能统计出真实成功率
report(
    MODEL,
    input_text=prompt,
    output_text=resp.choices[0].message.content,
    input_tokens=resp.usage.prompt_tokens,
    cache_hit_tokens=getattr(resp.usage, "prompt_cache_hit_tokens", 0),
    output_tokens=resp.usage.completion_tokens,
    latency_ms=(time.perf_counter() - started) * 1000,
    status="success",
)
```

`cache_hit_tokens` 按厂商字段取值：DeepSeek 为 `usage.prompt_cache_hit_tokens`，OpenAI 为 `usage.prompt_tokens_details.cached_tokens`。部分模型不返回该字段，使用 `getattr(..., 0)` 取默认值 0，与不上报该字段的效果一致。

`except` 块中先调用 `report(...)`，再执行 `raise`。上报不改变业务异常的处理流程。环境变量为空时，`report()` 在首行返回，不发送任何网络请求，因此本地开发和测试环境可以不配置。

## asyncio 项目

上述线程实现可在 asyncio 项目中直接使用。如果业务代码运行在事件循环中，可将 `report()` 末尾的 `threading.Thread(...).start()` 替换为 `_spawn_send(payload)`，并加入以下函数：

```python
# 必须保留强引用：仅 create_task 而不保存引用时，任务可能在完成前被垃圾回收，
# 导致上报丢失且不报错
_pending: set[asyncio.Task] = set()


def _spawn_send(payload: dict) -> None:
    """将一次上报放入后台后立即返回，兼容同步与异步运行环境。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # 不在事件循环中（同步代码或工作线程）时使用线程
        threading.Thread(target=_send_sync, args=(payload,), daemon=True).start()
    else:
        task = loop.create_task(_send_async(payload))
        _pending.add(task)
        task.add_done_callback(_pending.discard)


def _send_sync(payload: dict) -> None:
    # 同步环境（工作线程）里使用 httpx 的同步客户端
    try:
        httpx.post(
            f"{BASE_URL}/api/v1/logs",
            json=payload,
            headers={"X-API-Key": API_KEY},
            timeout=TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 —— 上报失败不得影响业务
        logger.warning("AI Monitor 上报失败(已忽略): %s", exc)


async def _send_async(payload: dict) -> None:
    # 事件循环里使用异步客户端，等待网络响应时不阻塞其它协程
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            await client.post(
                f"{BASE_URL}/api/v1/logs",
                json=payload,
                headers={"X-API-Key": API_KEY},
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("AI Monitor 上报失败(已忽略): %s", exc)
```

`_pending` 集合用于持有任务强引用，避免任务在 `await` 完成前被垃圾回收。任务完成后通过 `add_done_callback` 移除。

## 其他语言

上报接口是一个标准 HTTP POST 请求，任何支持发送 POST 的语言均可实现。四条要求不变（后台发送、短超时、捕获异常、失败也上报）。

### Node.js

```js
// 平台地址与 API Key 从环境变量读取，不写死在代码里
const BASE_URL = process.env.AI_MONITOR_BASE_URL || "";
const API_KEY = process.env.AI_MONITOR_API_KEY || "";

// 发出即返回：不 await、不重试、失败时静默
function report(payload) {
  // 没配置环境变量时直接返回：本地开发和测试环境不需要上报
  if (!BASE_URL || !API_KEY) return;

  fetch(`${BASE_URL}/api/v1/logs`, {
    method: "POST",
    headers: { "X-API-Key": API_KEY, "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    // 3 秒内没完成就中断，避免请求一直挂着
    signal: AbortSignal.timeout(3000),
  }).catch(() => {});  // 上报失败只吞掉异常，绝不影响业务
}
```

```js
// Date.now() 返回毫秒时间戳，两次相减就是耗时
const started = Date.now();
try {
  const resp = await callModel(prompt);
  report({
    provider: "deepseek", model: MODEL,
    latency: Date.now() - started, status: "success",
    input_tokens: resp.usage.prompt_tokens,
    output_tokens: resp.usage.completion_tokens,
  });
  return resp;
} catch (err) {
  // 失败的调用也要上报：异常分析需要看到失败记录
  report({
    provider: "deepseek", model: MODEL,
    latency: Date.now() - started, status: "error",
    error: `${err.name}: ${err.message}`,
  });
  throw err;  // 业务异常正常抛出，与上报无关
}
```

### Java

示例要求 Java 11 及以上，使用 JDK 自带的 `java.net.http.HttpClient`，并以 Jackson 序列化 JSON（Spring Boot 项目已内置；非 Spring 项目需添加 `jackson-databind` 依赖）：

```java
import com.fasterxml.jackson.databind.ObjectMapper;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Map;

public final class AiMonitorReporter {
    // 平台地址与 API Key 从环境变量读取，不写死在代码里
    private static final String BASE_URL = System.getenv("AI_MONITOR_BASE_URL");
    private static final String API_KEY = System.getenv("AI_MONITOR_API_KEY");

    // 上报超时设短：平台响应慢时，最多等 3 秒就放弃
    private static final Duration TIMEOUT = Duration.ofSeconds(3);

    // HttpClient 是线程安全的，创建一次全局复用即可
    private static final HttpClient HTTP = HttpClient.newBuilder()
            .connectTimeout(TIMEOUT)
            .build();

    // Jackson 的 ObjectMapper 也设计为可复用，不必每次新建
    private static final ObjectMapper JSON = new ObjectMapper();

    private AiMonitorReporter() {
    }

    public static void report(Map<String, Object> payload) {
        // 没配置环境变量时直接返回：本地开发和测试环境不需要上报
        if (BASE_URL == null || BASE_URL.isBlank() || API_KEY == null || API_KEY.isBlank()) {
            return;
        }

        // 每次上报起一个线程，调用方立即返回，不等网络请求完成
        Thread sender = new Thread(() -> send(payload), "ai-monitor-report");
        // 守护线程：主程序退出时，不会为了等上报线程而卡住
        sender.setDaemon(true);
        sender.start();
    }

    private static void send(Map<String, Object> payload) {
        try {
            HttpRequest request = HttpRequest.newBuilder()
                    .uri(URI.create(BASE_URL + "/api/v1/logs"))
                    .timeout(TIMEOUT)
                    .header("X-API-Key", API_KEY)
                    .header("Content-Type", "application/json")
                    .POST(HttpRequest.BodyPublishers.ofString(JSON.writeValueAsString(payload)))
                    .build();
            // discarding()：不读取响应体；上报只要请求送达即可
            HTTP.send(request, HttpResponse.BodyHandlers.discarding());
        } catch (Exception e) {
            // 上报失败只记一条本地日志，绝不向上抛出
            System.err.println("AI Monitor 上报失败(已忽略): " + e);
        }
    }
}
```

调用示例（省略 import）：

```java
// nanoTime 是单调时钟，测耗时不受系统时间调整影响；除以 1_000_000 换算成毫秒
long started = System.nanoTime();
try {
    ChatResponse resp = client.chat(MODEL, prompt);

    // 请求体字段见「上报接口」；project_id 由平台根据 API Key 自动确定
    Map<String, Object> payload = new HashMap<>();
    payload.put("provider", "deepseek");
    payload.put("model", MODEL);
    payload.put("latency", (System.nanoTime() - started) / 1_000_000);
    payload.put("status", "success");
    payload.put("input_tokens", resp.promptTokens());
    payload.put("output_tokens", resp.completionTokens());
    payload.put("input", prompt);
    payload.put("output", resp.content());
    AiMonitorReporter.report(payload);
} catch (Exception e) {
    // 失败的调用也要上报：异常分析需要看到失败记录
    Map<String, Object> payload = new HashMap<>();
    payload.put("provider", "deepseek");
    payload.put("model", MODEL);
    payload.put("latency", (System.nanoTime() - started) / 1_000_000);
    payload.put("status", "error");
    // 带上异常类型名，平台会按错误信息归类聚合
    payload.put("error", e.getClass().getSimpleName() + ": " + e.getMessage());
    AiMonitorReporter.report(payload);

    // 业务异常按原流程抛出，与上报无关
    throw e;
}
```

示例中的 `client.chat(...)` 与 `resp.promptTokens()` 表示所用 SDK 的对应接口，请按实际 SDK 调整；SDK 提供缓存命中数量时，可一并加入 `payload` 的 `cache_hit_tokens` 字段。

## Serverless 环境

在 Vercel、AWS Lambda 等 Serverless 环境中，函数返回后进程可能被立即冻结，「发出即忘」的请求可能无法完成。此类环境应 `await` 上报请求并配合 try/catch 与短超时，或使用平台提供的日志、队列机制异步发送。

## 后续步骤

接入完成后，请按 [接入验收清单](/docs/getting-started/verify) 逐项检查。如上报未生效，请参阅 [错误码与处理](/docs/api/errors)。
