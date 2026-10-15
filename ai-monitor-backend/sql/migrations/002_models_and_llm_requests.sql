

-- ⚠️ 已归档（2026-10-15）：完整表结构见 sql/schema.sql（新库部署跑它）。
--    本文件仅作演进历史与老库升级路径保留；文中「本文件是唯一真相」等表述已不再适用。
-- ============================================================================
-- AI Monitor — 阶段 6 建表脚本：模型价格表 + 调用日志表
--
-- 【本文件是表结构的唯一真相】
--   ORM 模型（app/models/model.py、app/models/llm_request.py）只做映射，不建表。
--   改表结构时，必须同时改这两处：
--     ai-monitor-backend/sql/002_models_and_llm_requests.sql   ← 你在这里
--     ai-monitor-backend/app/models/*.py
--
-- 【执行方式】把本文件传到虚拟机上，然后：
--     psql -U ai_monitor -d ai_monitor      -h 127.0.0.1 -f 002_models_and_llm_requests.sql
--     psql -U ai_monitor -d ai_monitor_test -h 127.0.0.1 -f 002_models_and_llm_requests.sql
--   ⚠️ 开发库和测试库都要跑，漏了测试库会 check_ingest.py 报「表不存在」
--
-- 【幂等】全部使用 IF NOT EXISTS，重复执行不会报错。
--   但也不会更新已存在的表结构 —— 改字段请写 003_xxx.sql，不要改这里重跑。
--
-- 【本脚本包含的表】
--   models        模型价格    ← 上报时算成本用，内置模型（owner_id 为空）+ 用户自建
--   llm_requests  调用日志    ← 平台所有监控功能的唯一数据源
-- ============================================================================


-- ---------------------------------------------------------------------------
-- models — 模型价格
-- 上报接口拿到日志里的 model 名后，来这张表查价格、算 estimated_cost。
--
-- 【两种记录共存】靠 owner_id 是否为 NULL 区分：
--   owner_id IS NULL     内置模型，全体用户可见、谁都不能改
--   owner_id IS NOT NULL 用户自建，只有创建者可见
-- 所以查询时的过滤条件是 (is_builtin = true OR owner_id = 当前用户)，
-- 漏掉前半段，用户就看不到内置模型了。
--
-- 【价格单位】人民币元 / 每百万 token。厂商都是按「每百万 token」报价的，
-- 存这个口径可以照抄官网数字，不用做换算。
--
-- 【为什么 NUMERIC 不用 FLOAT】浮点数存不下 0.1 这类十进制小数（存的是近似值），
-- 钱相关的字段累加多次后偏差会显现出来。NUMERIC 是精确十进制。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS models (
    id           BIGSERIAL      PRIMARY KEY,              -- 主键，自增
    owner_id     BIGINT         REFERENCES users(id) ON DELETE CASCADE,  -- 归属用户；NULL = 内置模型
    provider     VARCHAR(50)    NOT NULL,                 -- 厂商，如 deepseek / openai
    model_name   VARCHAR(100)   NOT NULL,                 -- 模型名，与上报日志里的 model 字段匹配
    input_price  NUMERIC(12, 6) NOT NULL,                 -- 输入价格（元 / 百万 token）
    output_price NUMERIC(12, 6) NOT NULL,                 -- 输出价格（元 / 百万 token）
    is_builtin   BOOLEAN        NOT NULL DEFAULT false,   -- 是否内置
    status       VARCHAR(20)    NOT NULL DEFAULT 'active',-- 状态：active / disabled
    created_at   TIMESTAMPTZ    NOT NULL DEFAULT now(),   -- 创建时间（UTC）
    updated_at   TIMESTAMPTZ    NOT NULL DEFAULT now(),   -- 更新时间（UTC），由应用层写入时刷新

    CONSTRAINT ck_models_status CHECK (status IN ('active', 'disabled')),
    -- 价格可以为 0（自部署模型不计成本），但不能为负：负价格会让成本统计出现负数
    CONSTRAINT ck_models_price_nonneg CHECK (input_price >= 0 AND output_price >= 0),
    -- is_builtin 和 owner_id 是两个可以互相矛盾的字段，这条约束保证它们不会
    -- 出现「内置模型却归属某人」或「自建模型却不归属任何人」的脏数据
    CONSTRAINT ck_models_builtin_owner CHECK (
        (is_builtin AND owner_id IS NULL) OR (NOT is_builtin AND owner_id IS NOT NULL)
    )
);

-- 上报接口每次调用都要按 model_name 查一次价格，这个索引是高频路径的基础
CREATE INDEX IF NOT EXISTS idx_models_model_name ON models (model_name);

-- 内置模型之间不许重名：同名两条会让上报随机匹配到其中一个价格
CREATE UNIQUE INDEX IF NOT EXISTS uq_models_builtin_name
    ON models (model_name) WHERE is_builtin;

-- 同一用户的自建模型也不许重名。WHERE 子句让 NULL 不参与唯一性判断，
-- 也就是说这条索引只管自建模型，不会和上面的内置模型索引打架
CREATE UNIQUE INDEX IF NOT EXISTS uq_models_owner_name
    ON models (owner_id, model_name) WHERE owner_id IS NOT NULL;

COMMENT ON TABLE  models              IS '模型价格，上报时算成本用';
COMMENT ON COLUMN models.id           IS '主键，自增';
COMMENT ON COLUMN models.owner_id     IS '归属用户 id；NULL 表示内置模型，全体用户可见';
COMMENT ON COLUMN models.provider     IS '厂商，如 deepseek / openai';
COMMENT ON COLUMN models.model_name   IS '模型名，与上报日志的 model 字段匹配';
COMMENT ON COLUMN models.input_price  IS '输入价格，元 / 百万 token';
COMMENT ON COLUMN models.output_price IS '输出价格，元 / 百万 token';
COMMENT ON COLUMN models.is_builtin   IS '是否内置模型';
COMMENT ON COLUMN models.status       IS '状态：active 可用 / disabled 已禁用（禁用不物理删除，保留历史可解释性）';
COMMENT ON COLUMN models.created_at   IS '创建时间（UTC）';
COMMENT ON COLUMN models.updated_at   IS '更新时间（UTC），由应用层写入时刷新';


-- ---------------------------------------------------------------------------
-- llm_requests — 大模型调用日志
-- 一次 LLM 调用一条记录。平台后面所有的统计、图表、异常判定、AI 分析，
-- 全部建立在这张表之上 —— 它是整个平台的数据源。
--
-- 【写入方只有一处】上报接口 POST /api/v1/logs，接入方拿 API Key 写、
-- 没有登录用户的概念，所以本表直接挂 project_id（不是 user_id）。
--
-- 【本表没有 updated_at】日志是「发生过的事实」，写入后不再修改，
-- 所以不继承 TimestampMixin，只声明 created_at。
--
-- 【estimated_cost 是快照】写入时按当时的模型价格算好存进来。
-- 以后改模型价格，只影响之后的调用，历史记录不变 —— 这是刻意的：
-- 用户不会希望「我改了价格，上个月的成本报表也跟着变了」。
--
-- 【project_id 的 ON DELETE CASCADE】删项目时连带删掉它的全部日志。
-- 日志离开项目没有意义，且用户删项目就是要清干净数据。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS llm_requests (
    id             BIGSERIAL      PRIMARY KEY,            -- 主键，自增
    project_id     BIGINT         NOT NULL REFERENCES projects(id) ON DELETE CASCADE,  -- 归属项目
    provider       VARCHAR(50)    NOT NULL,               -- 厂商，如 deepseek
    model          VARCHAR(100)   NOT NULL,               -- 模型名，如 deepseek-chat
    input          TEXT,                                  -- 用户输入，可空
    output         TEXT,                                  -- AI 输出，失败调用没有输出
    input_tokens   INTEGER        NOT NULL DEFAULT 0,     -- 输入 token 数
    output_tokens  INTEGER        NOT NULL DEFAULT 0,     -- 输出 token 数
    total_tokens   INTEGER        NOT NULL DEFAULT 0,     -- 总 token 数
    latency        INTEGER        NOT NULL,               -- 耗时，单位毫秒
    status         VARCHAR(20)    NOT NULL,               -- 状态：success / error
    error_message  TEXT,                                  -- 错误信息，成功时为 NULL
    estimated_cost NUMERIC(14, 8),                        -- 成本快照（元）；模型没配价格时为 NULL
    created_at     TIMESTAMPTZ    NOT NULL DEFAULT now(), -- 调用时间（UTC）；接入方传了 timestamp 就用它的

    CONSTRAINT ck_llm_requests_status CHECK (status IN ('success', 'error')),
    -- token 和耗时不能为负。数据库层兜底：应用层漏了校验时，脏数据写不进来
    CONSTRAINT ck_llm_requests_tokens CHECK (
        input_tokens >= 0 AND output_tokens >= 0 AND total_tokens >= 0
    ),
    CONSTRAINT ck_llm_requests_latency CHECK (latency >= 0)
);

-- 监控中心所有查询的基础索引：先按项目过滤，再按时间倒序翻页。
-- 第二、三个字段跟着 ORDER BY created_at DESC, id DESC 走：
-- 只按 created_at 排序时，同一毫秒的多条记录顺序不稳定，翻页会重复或漏条
CREATE INDEX IF NOT EXISTS idx_llm_requests_project_created
    ON llm_requests (project_id, created_at DESC, id DESC);

-- 按模型筛选、以及后续「模型使用分布」统计用
CREATE INDEX IF NOT EXISTS idx_llm_requests_model ON llm_requests (model);

COMMENT ON TABLE  llm_requests                IS '大模型调用日志，平台的数据源';
COMMENT ON COLUMN llm_requests.id             IS '主键，自增';
COMMENT ON COLUMN llm_requests.project_id     IS '归属项目 id，项目隔离的基础';
COMMENT ON COLUMN llm_requests.provider       IS '厂商，如 deepseek';
COMMENT ON COLUMN llm_requests.model          IS '模型名，如 deepseek-chat';
COMMENT ON COLUMN llm_requests.input          IS '用户输入，可空';
COMMENT ON COLUMN llm_requests.output         IS 'AI 输出，失败调用为 NULL';
COMMENT ON COLUMN llm_requests.input_tokens   IS '输入 token 数';
COMMENT ON COLUMN llm_requests.output_tokens  IS '输出 token 数';
COMMENT ON COLUMN llm_requests.total_tokens   IS '总 token 数；接入方未传时由 input + output 补齐';
COMMENT ON COLUMN llm_requests.latency        IS '耗时，单位毫秒';
COMMENT ON COLUMN llm_requests.status         IS '状态：success 成功 / error 失败';
COMMENT ON COLUMN llm_requests.error_message  IS '错误信息，成功时为 NULL';
COMMENT ON COLUMN llm_requests.estimated_cost IS '成本快照（元），写入时按当时的模型价格算好；未匹配到模型价格为 NULL';
COMMENT ON COLUMN llm_requests.created_at     IS '调用时间（UTC）';
