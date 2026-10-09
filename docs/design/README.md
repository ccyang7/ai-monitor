# AI Monitor — 方案设计文档（总设计）

> 本目录是《[模块需求文档](../requirements/README.md)》对应的**方案设计**：
> 需求文档回答"**做什么**"，设计文档回答"**怎么做**"。

------

## 0. 关于设计文档的使用方式

**设计文档是活文档，不是圣经。**

实现过程中如果发现设计不合理，**改设计**是正确的做法 —— 但要在文档里同步改掉，而不是让代码和文档分叉。

改动时要问自己：这是个案问题（只影响这一处），还是设计问题（其他模块也受影响）？

------

## 1. 架构分层

V1 采用**四层结构**，不做 Repository 层（对当前规模的收益不足）：

```text
┌─────────────────────────────────────┐
│  api/       路由层                   │  接收请求、参数校验、返回响应
├─────────────────────────────────────┤
│  services/  业务逻辑层               │  业务规则、数据库操作、事务
├─────────────────────────────────────┤
│  models/    ORM 模型（SQLAlchemy）    │  表结构定义
│  schemas/   Pydantic 模型            │  请求/响应数据结构
└─────────────────────────────────────┘
```

### 各层职责与边界

| 层 | 做什么 | 不做什么 |
|---|---|---|
| `api/` | 定义路由、声明依赖、调用 service、返回结果 | 不写业务逻辑、不直接操作数据库 |
| `services/` | 业务规则、查询组装、事务控制 | 不感知 HTTP（不接收 Request、不抛 HTTPException） |
| `models/` | 表结构、关系映射 | 不含业务逻辑 |
| `schemas/` | 请求校验、响应序列化 | 不含业务逻辑 |

> **为什么分层**：让"改一处不影响另一处"。比如以后统计逻辑变化，只动 `services/`，路由和表结构不动。
>
> **不要为了分层而分层**：如果某个接口只是一句查询，service 层可以很薄 —— 但层要在，这是为了保持一致性。

------

## 2. 目录结构

```text
ai-monitor-backend/
├── main.py                      应用入口：创建 FastAPI 实例、挂载路由、注册异常处理
├── pyproject.toml               依赖声明
├── uv.lock                      依赖锁定（提交）
├── .env                         本地配置（不提交）
├── .env.example                 配置模板（提交）
└── app/
    ├── __init__.py
    ├── config.py                配置管理：从环境变量读取设置
    ├── database.py              数据库：engine、SessionLocal、get_db 依赖
    ├── deps.py                  通用依赖：get_current_user、get_own_project
    │
    ├── models/                  ORM 模型（每张表一个文件）
    ├── schemas/                 Pydantic 模型（每个领域一个文件）
    ├── services/                业务逻辑（每个领域一个文件）
    ├── api/                     路由（每个领域一个文件）
    │
    └── ai/                      AI 分析助手（阶段 11-12）
        ├── agent.py             Agent 构建与会话管理
        └── tools.py             工具定义（含权限校验）
```

### 为什么按"领域"分文件，不按"类型"分

```text
✅ 按领域：  api/projects.py  services/projects.py  schemas/project.py
❌ 按类型：  api/routers.py  services/all_services.py
```

按领域分，改项目相关功能时只碰 project 那几个文件；按类型分，所有功能挤在一个大文件里，改一处要翻半天。

------

## 3. 通用约定

### 3.1 API 路径

```text
统一前缀：/api/v1
资源名用复数：/api/v1/projects
嵌套资源：/api/v1/projects/{project_id}/logs
```

### 3.2 响应格式：不包装

**直接返回数据，用 HTTP 状态码表达结果** —— 这是 REST 的惯例，也是 FastAPI 的原生风格。

```json
// 成功：200 + 直接数据
{"id": 1, "name": "AI知识问答"}

// 失败：4xx/5xx + FastAPI 标准错误体
{"detail": "项目不存在"}
```

> ❌ 不采用 `{"code": 0, "data": {...}, "message": "ok"}` 这类统一包装 —— 它会让 `/docs` 的自动文档变模糊（所有接口看起来都返回同一个结构），也丢失了 HTTP 状态码的语义。

### 3.3 状态码约定

| 场景 | 状态码 |
|---|---|
| 查询成功 | 200 |
| 创建成功 | 201 |
| 参数校验失败 | 422（FastAPI 自动） |
| 未登录 / 凭证无效 | 401 |
| **资源不存在或不属于当前用户** | **404** |
| 业务规则冲突（如超项目上限） | 400 / 409 |

> ⚠️ **资源不属于当前用户时返回 404 而非 403** —— 403 会泄露"这个 ID 确实存在，只是你没权限"，攻击者可以据此枚举出系统中所有资源。
>
> 这条规则在所有涉及项目的接口上都要遵守（见 [01-用户与项目管理](01-用户与项目管理.md)）。

### 3.4 分页

```text
请求：?page=1&page_size=20
响应：{"items": [...], "total": 135, "page": 1, "page_size": 20}
```

### 3.5 时间

```text
存储：UTC，带时区
传输：ISO 8601 字符串，如 "2026-10-06T11:42:31Z"
```

### 3.6 命名

| 位置 | 风格 | 例 |
|---|---|---|
| 数据库表名 | snake_case 复数 | `llm_requests` |
| 数据库字段 | snake_case | `input_tokens` |
| Python 变量/函数 | snake_case | `get_project_usage` |
| Python 类 | PascalCase | `LLMRequest` |
| API JSON 字段 | snake_case | `input_tokens` |

> 对外 JSON 用 snake_case 是**跨语言契约**（见需求文档 9.5），一经确定不再更改。

------

## 4. 数据库表结构全貌

```text
users              用户
projects           项目          ← 平台核心单位
api_keys           项目密钥
models             模型与价格
llm_requests       调用日志      ← 数据量最大
```

### 4.1 users

| 字段 | 类型 | 说明 |
|---|---|---|
| id | bigint PK | 自增 |
| username | varchar(50) | 唯一 |
| password_hash | varchar(255) | 哈希值，绝不存明文 |
| email | varchar(255) | 唯一，可空 |
| created_at | timestamptz | |
| updated_at | timestamptz | |

### 4.2 projects

| 字段 | 类型 | 说明 |
|---|---|---|
| id | bigint PK | 自增 |
| owner_id | bigint FK → users.id | **用户隔离的基础**，索引 |
| name | varchar(100) | |
| description | text | 可空 |
| status | varchar(20) | active / archived |
| created_at | timestamptz | |
| updated_at | timestamptz | |

### 4.3 api_keys

| 字段 | 类型 | 说明 |
|---|---|---|
| id | bigint PK | |
| project_id | bigint FK → projects.id | 索引 |
| key_hash | varchar(255) | 唯一索引 |
| key_prefix | varchar(20) | 展示用，如 `proj_a1b2` |
| status | varchar(20) | active / revoked |
| created_at | timestamptz | |
| expired_at | timestamptz | 可空，null = 永不过期 |

> **为什么存 `key_hash` 不存明文**：数据库泄露时，攻击者拿到哈希也无法反推出可用的 Key。这和存密码是同一个道理。

### 4.4 models

| 字段 | 类型 | 说明 |
|---|---|---|
| id | bigint PK | |
| provider | varchar(50) | deepseek / openai / ... |
| model_name | varchar(100) | 如 deepseek-chat |
| input_price | numeric(12,6) | 每 1M Token 单价 |
| output_price | numeric(12,6) | 每 1M Token 单价 |
| is_builtin | boolean | 系统预置标记 |
| owner_id | bigint FK → users.id | 预置为 null |
| status | varchar(20) | active / disabled |
| created_at / updated_at | timestamptz | |

> **价格用 `numeric` 不用 `float`** —— 浮点数有精度误差（`0.1 + 0.2 != 0.3`），金额计算必须用精确类型。

### 4.5 llm_requests

| 字段 | 类型 | 说明 |
|---|---|---|
| id | bigint PK | |
| project_id | bigint FK → projects.id | **核心关联字段** |
| provider | varchar(50) | |
| model | varchar(100) | |
| input | text | 用户输入 |
| output | text | AI 输出 |
| input_tokens | int | |
| output_tokens | int | |
| total_tokens | int | |
| latency | int | 毫秒 |
| status | varchar(20) | success / error |
| error_message | text | 可空 |
| estimated_cost | numeric(12,6) | 写入时快照 |
| created_at | timestamptz | |

### 4.6 必须建立的索引

```text
llm_requests(project_id, created_at)   ← 监控中心所有查询的基础
llm_requests(model)                     ← 按模型筛选
projects(owner_id)                      ← 首页项目列表
api_keys(key_hash) UNIQUE               ← 上报接口鉴权，每次请求都要查
```

------

## 5. 阶段映射

每份设计文档对应一个开发阶段，按阶段推进时看对应的文档：

| 设计文档 | 开发阶段 |
|---|---|
| [01-用户与项目管理](01-用户与项目管理.md) | 阶段 3 |
| [02-数据接入](02-数据接入.md) | 阶段 6 |
| [03-项目监控中心](03-项目监控中心.md) | 阶段 7、9 |
| [04-调用日志与详情](04-调用日志与详情.md) | 阶段 6、9 |
| [05-异常分析](05-异常分析.md) | 阶段 9 |
| [06-模型管理](06-模型管理.md) | 阶段 7 |
| [07-AI分析助手](07-AI分析助手.md) | 阶段 11、12 |
| [08-前端页面](08-前端页面.md) | 阶段 10 |

**通用基础设施**（`config.py` / `database.py` / `deps.py`）在**阶段 2-5** 建立，是所有模块的地基。

------

## 6. 本设计中的关键技术决策

| 决策 | 选择 | 原因 |
|---|---|---|
| 架构形态 | 模块化单体 | V1 规模不需要微服务 |
| 分层 | 四层，无 Repository | 规模不够，多一层是负担 |
| 响应格式 | 不包装，用 HTTP 状态码 | 保住 REST 语义和 `/docs` 自动文档 |
| 越权访问 | 返回 404 | 403 会泄露资源存在性 |
| 主键 | 数据库自增 | 单库单实例，雪花算法无收益 |
| 金额字段 | `numeric` 不用 `float` | 浮点精度误差 |
| 密码/Key | 只存哈希 | 数据库泄露时的最后一道防线 |
| 成本计算 | 写入时快照 | 历史成本不可变，符合用户心智 |
