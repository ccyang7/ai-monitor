-- ============================================================================
-- AI Monitor — 完整建表脚本（全新部署只跑这一个文件）
--
-- 【本文件是表结构的唯一真相】
--   ORM 模型（app/models/）只做映射，不建表（没有 Base.metadata.create_all）。
--   改表结构时，必须同时改这三处：
--     ai-monitor-backend/sql/schema.sql           ← 你在这里，完整版
--     ai-monitor-backend/app/models/*.py          ORM 映射
--     ai-monitor-backend/sql/migrations/          新增一个增量脚本，供已有库升级
--   两边不一致时，以本文件为准（数据库里实际长什么样，由它决定）。
--
-- 【面向全新部署】从一个空库建出全部 7 张表。
--   ⚠️ 已存在的库不要跑本文件做升级 —— 它全是 CREATE ... IF NOT EXISTS，
--      对已存在的表一个字段都不会改，跑了等于没跑。升级走 sql/migrations/
--      下按编号的增量脚本。两个用途别混。
--
-- 【执行前提】数据库和用户还没有，先在数据库服务器上执行这两条（用超级用户）：
--     CREATE USER ai_monitor WITH PASSWORD '你的密码';
--     CREATE DATABASE ai_monitor OWNER ai_monitor;
--
-- 【执行方式】以 ai_monitor 用户连到目标库执行：
--     psql -U ai_monitor -d ai_monitor      -h 127.0.0.1 -f schema.sql
--     psql -U ai_monitor -d ai_monitor_test -h 127.0.0.1 -f schema.sql
--
--   ⚠️ 一定要用 ai_monitor 执行，别用 sudo -u postgres —— 表的 owner 是
--      「执行建表的那个人」，用 postgres 建出来的表 ai_monitor 一点权限都没有，
--      连 SELECT 都会报 permission denied。而 CREATE DATABASE ... OWNER ai_monitor
--      只给了「数据库本身」，库里已有的对象不归库 owner。
--   ⚠️ -h 127.0.0.1 不能省：不加会走本地 socket，PG 默认用 peer 认证
--      （要求系统用户名也叫 ai_monitor），会认证失败。
--
-- 【看字段注释】执行完在 psql 里用 \d+ 表名，可以直接看到每个字段的中文说明：
--     \d+ users
--
-- 【幂等】全部使用 IF NOT EXISTS，重复执行不会报错，也不会改动已有表。
--
-- 【本脚本包含的表】（按外键依赖顺序）
--   users                     用户
--   projects                  项目，平台核心单位
--   api_keys                  项目密钥，数据上报的凭证
--   models                    模型价格，上报时算成本用
--   llm_requests              大模型调用日志，平台全部数据源
--   project_anomaly_settings  项目级异常判定标准
--   assistant_chats           AI 助手用量记录（不含对话正文）
--
-- 【不含内置模型数据】models 表的初始价格由 scripts/init_models.py 写入（幂等，
--   可反复执行），不放进建表脚本 —— 价格是「数据」不是「结构」，会随厂商调价改。
-- ============================================================================


-- ---------------------------------------------------------------------------
-- users — 用户
-- 所有数据的最终归属人。用户隔离的硬边界就是这张表的 id。
-- role = 'admin' 是系统里唯一能跨用户读取的地方，且仅限账号信息本身 ——
-- 项目数据、调用日志、AI 助手对话仍然只对本人可见。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL    PRIMARY KEY,               -- 主键，自增
    username      VARCHAR(50)  NOT NULL UNIQUE,           -- 登录名，全局唯一
    password_hash VARCHAR(255) NOT NULL,                  -- 密码哈希，绝不存明文
    email         VARCHAR(255) UNIQUE,                    -- 邮箱，唯一但可空
    role          VARCHAR(20)  NOT NULL DEFAULT 'user',   -- 角色：user 普通用户 / admin 管理员
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),    -- 创建时间（UTC）
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),    -- 更新时间（UTC）

    CONSTRAINT ck_users_role CHECK (role IN ('user', 'admin'))
);

COMMENT ON TABLE  users               IS '用户';
COMMENT ON COLUMN users.id            IS '主键，自增';
COMMENT ON COLUMN users.username      IS '登录名，全局唯一';
COMMENT ON COLUMN users.password_hash IS '密码哈希，绝不存明文';
COMMENT ON COLUMN users.email         IS '邮箱，唯一但可空';
COMMENT ON COLUMN users.role          IS '角色：user 普通用户 / admin 管理员，默认最小权限';
COMMENT ON COLUMN users.created_at    IS '创建时间（UTC）';
COMMENT ON COLUMN users.updated_at    IS '更新时间（UTC），由应用层写入时刷新';


-- ---------------------------------------------------------------------------
-- projects — 项目
-- 平台的核心单位，一个项目对应一个实际的 AI 应用。
--
-- ⚠️ owner_id 是用户隔离的基础：所有查询项目的 SQL 都必须带 owner_id 过滤，
--    漏掉这个条件 = 列出全系统所有项目。这是聚合区最典型的越权 bug。
--
-- status 用 CHECK 约束而不是 PG 的 ENUM 类型：ENUM 增删值要 ALTER TYPE，麻烦；
-- CHECK 直接改约束就行。作用是在数据库层兜底，防止写入非法状态值。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS projects (
    id          BIGSERIAL    PRIMARY KEY,                 -- 主键，自增
    owner_id    BIGINT       NOT NULL REFERENCES users(id) ON DELETE CASCADE,  -- 归属用户，隔离基础
    name        VARCHAR(100) NOT NULL,                    -- 项目名称
    description TEXT,                                     -- 项目描述，可空
    tech_stack  VARCHAR(50),                              -- 技术栈，如 Python，可空
    status      VARCHAR(20)  NOT NULL DEFAULT 'active',   -- 状态：active / archived
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),      -- 创建时间（UTC）
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),      -- 更新时间（UTC）

    CONSTRAINT ck_projects_status CHECK (status IN ('active', 'archived'))
);

-- 首页项目列表的查询基础：WHERE owner_id = ?
CREATE INDEX IF NOT EXISTS idx_projects_owner_id ON projects (owner_id);

COMMENT ON TABLE  projects             IS '项目，平台核心单位';
COMMENT ON COLUMN projects.id          IS '主键，自增';
COMMENT ON COLUMN projects.owner_id    IS '归属用户 id，用户隔离的基础，带索引';
COMMENT ON COLUMN projects.name        IS '项目名称，同一用户下不重复（由业务层校验）';
COMMENT ON COLUMN projects.description IS '项目描述，可空';
COMMENT ON COLUMN projects.tech_stack  IS '技术栈，如 Python / Java，可空';
COMMENT ON COLUMN projects.status      IS '状态：active 正常 / archived 已归档';
COMMENT ON COLUMN projects.created_at  IS '创建时间（UTC）';
COMMENT ON COLUMN projects.updated_at  IS '更新时间（UTC），由应用层写入时刷新';


-- ---------------------------------------------------------------------------
-- api_keys — 项目密钥
-- 数据上报接口用它识别是哪个项目。一个项目可以有多把 Key
-- （轮换时先加新的、再吊销旧的，服务不中断）。
--
-- ⚠️ 只存哈希不存明文：数据库泄露时攻击者也无法反推出可用的 Key。
--    明文只在创建那一刻返回一次，之后再也拿不到。
--
-- 本表刻意没有 updated_at：Key 建好后内容不再变化，只有 status 会改。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS api_keys (
    id         BIGSERIAL    PRIMARY KEY,                  -- 主键，自增
    project_id BIGINT       NOT NULL REFERENCES projects(id) ON DELETE CASCADE,  -- 归属项目
    key_hash   VARCHAR(255) NOT NULL UNIQUE,              -- Key 的哈希，不存明文
    key_prefix VARCHAR(20)  NOT NULL,                     -- Key 前几位，如 proj_a1b2c3
    status     VARCHAR(20)  NOT NULL DEFAULT 'active',    -- 状态：active / revoked
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),       -- 创建时间（UTC）
    expired_at TIMESTAMPTZ,                               -- 过期时间，NULL 表示永不过期

    CONSTRAINT ck_api_keys_status CHECK (status IN ('active', 'revoked'))
);

-- 列出某个项目下所有 Key 时的查询基础
CREATE INDEX IF NOT EXISTS idx_api_keys_project_id ON api_keys (project_id);

COMMENT ON TABLE  api_keys            IS '项目密钥，数据上报的凭证';
COMMENT ON COLUMN api_keys.id         IS '主键，自增';
COMMENT ON COLUMN api_keys.project_id IS '归属项目 id，带索引';
COMMENT ON COLUMN api_keys.key_hash   IS 'Key 的哈希，唯一，不存明文';
COMMENT ON COLUMN api_keys.key_prefix IS 'Key 前几位，仅用于界面展示识别';
COMMENT ON COLUMN api_keys.status     IS '状态：active 可用 / revoked 已吊销';
COMMENT ON COLUMN api_keys.created_at IS '创建时间（UTC）';
COMMENT ON COLUMN api_keys.expired_at IS '过期时间（UTC），NULL 表示永不过期';


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
    id              BIGSERIAL      PRIMARY KEY,              -- 主键，自增
    owner_id        BIGINT         REFERENCES users(id) ON DELETE CASCADE,  -- 归属用户；NULL = 内置模型
    provider        VARCHAR(50)    NOT NULL,                 -- 厂商，如 deepseek / openai
    model_name      VARCHAR(100)   NOT NULL,                 -- 模型名，与上报日志里的 model 字段匹配
    input_price     NUMERIC(12, 6) NOT NULL,                 -- 输入价格（元 / 百万 token，缓存未命中档）
    output_price    NUMERIC(12, 6) NOT NULL,                 -- 输出价格（元 / 百万 token）
    cache_hit_price NUMERIC(12, 6),                          -- 缓存命中单价；NULL = 该模型无缓存档
    is_builtin      BOOLEAN        NOT NULL DEFAULT false,   -- 是否内置
    status          VARCHAR(20)    NOT NULL DEFAULT 'active',-- 状态：active / disabled
    created_at      TIMESTAMPTZ    NOT NULL DEFAULT now(),   -- 创建时间（UTC）
    updated_at      TIMESTAMPTZ    NOT NULL DEFAULT now(),   -- 更新时间（UTC），由应用层写入时刷新

    CONSTRAINT ck_models_status CHECK (status IN ('active', 'disabled')),
    -- 价格可以为 0（自部署模型不计成本），但不能为负：负价格会让成本统计出现负数
    CONSTRAINT ck_models_price_nonneg CHECK (input_price >= 0 AND output_price >= 0),
    CONSTRAINT ck_models_cache_hit_price_nonneg
        CHECK (cache_hit_price IS NULL OR cache_hit_price >= 0),
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

-- 同一用户的自建模型也不许重名。只约束 active 行（禁用行保留下来供历史追溯，
-- 不占名字）—— 与 services/model.py 的重名校验口径一致。两边口径不一致的后果：
-- 建模型 → 上报过 → 禁用 → 用同一个名字再建 → 应用层放行、INSERT 撞索引 → 500
CREATE UNIQUE INDEX IF NOT EXISTS uq_models_owner_name
    ON models (owner_id, model_name) WHERE owner_id IS NOT NULL AND status = 'active';

COMMENT ON INDEX uq_models_owner_name IS
    '同一用户的自建模型不许重名；只约束 active 行 —— 禁用行保留下来供历史追溯，不占名字';

COMMENT ON TABLE  models                 IS '模型价格，上报时算成本用';
COMMENT ON COLUMN models.id              IS '主键，自增';
COMMENT ON COLUMN models.owner_id        IS '归属用户 id；NULL 表示内置模型，全体用户可见';
COMMENT ON COLUMN models.provider        IS '厂商，如 deepseek / openai';
COMMENT ON COLUMN models.model_name      IS '模型名，与上报日志的 model 字段匹配';
COMMENT ON COLUMN models.input_price     IS '输入价格，元 / 百万 token';
COMMENT ON COLUMN models.output_price    IS '输出价格，元 / 百万 token';
COMMENT ON COLUMN models.cache_hit_price IS '缓存命中单价（元 / 百万 token）；NULL = 该模型无缓存档，命中部分按 input_price 算；0 是真实价格（免费模型的缓存命中）';
COMMENT ON COLUMN models.is_builtin      IS '是否内置模型';
COMMENT ON COLUMN models.status          IS '状态：active 可用 / disabled 已禁用（禁用不物理删除，保留历史可解释性）';
COMMENT ON COLUMN models.created_at      IS '创建时间（UTC）';
COMMENT ON COLUMN models.updated_at      IS '更新时间（UTC），由应用层写入时刷新';


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
    id               BIGSERIAL      PRIMARY KEY,            -- 主键，自增
    project_id       BIGINT         NOT NULL REFERENCES projects(id) ON DELETE CASCADE,  -- 归属项目
    provider         VARCHAR(50)    NOT NULL,               -- 厂商，如 deepseek
    model            VARCHAR(100)   NOT NULL,               -- 模型名，如 deepseek-chat
    input            TEXT,                                  -- 用户输入，可空
    output           TEXT,                                  -- AI 输出，失败调用没有输出
    input_tokens     INTEGER        NOT NULL DEFAULT 0,     -- 输入 token 数
    cache_hit_tokens INTEGER        NOT NULL DEFAULT 0,     -- 输入中命中缓存的数量
    output_tokens    INTEGER        NOT NULL DEFAULT 0,     -- 输出 token 数
    total_tokens     INTEGER        NOT NULL DEFAULT 0,     -- 总 token 数
    latency          INTEGER        NOT NULL,               -- 耗时，单位毫秒
    status           VARCHAR(20)    NOT NULL,               -- 状态：success / error
    error_message    TEXT,                                  -- 错误信息，成功时为 NULL
    estimated_cost   NUMERIC(14, 8),                        -- 成本快照（元）；模型没配价格时为 NULL
    created_at       TIMESTAMPTZ    NOT NULL DEFAULT now(), -- 调用时间（UTC）；接入方传了 timestamp 就用它的

    CONSTRAINT ck_llm_requests_status CHECK (status IN ('success', 'error')),
    -- token 和耗时不能为负。数据库层兜底：应用层漏了校验时，脏数据写不进来
    CONSTRAINT ck_llm_requests_tokens CHECK (
        input_tokens >= 0 AND output_tokens >= 0 AND total_tokens >= 0
    ),
    -- 命中数不能为负，也不能超过输入总数 —— 物理上命中是输入的子集
    -- （老接入方不传 cache_hit_tokens，默认 0，成本按全部未命中算，与改动前一致）
    CONSTRAINT ck_llm_requests_cache_hit CHECK (
        cache_hit_tokens >= 0 AND cache_hit_tokens <= input_tokens
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

COMMENT ON TABLE  llm_requests                   IS '大模型调用日志，平台的数据源';
COMMENT ON COLUMN llm_requests.id                IS '主键，自增';
COMMENT ON COLUMN llm_requests.project_id        IS '归属项目 id，项目隔离的基础';
COMMENT ON COLUMN llm_requests.provider          IS '厂商，如 deepseek';
COMMENT ON COLUMN llm_requests.model             IS '模型名，如 deepseek-chat';
COMMENT ON COLUMN llm_requests.input             IS '用户输入，可空';
COMMENT ON COLUMN llm_requests.output            IS 'AI 输出，失败调用为 NULL';
COMMENT ON COLUMN llm_requests.input_tokens      IS '输入 token 数';
COMMENT ON COLUMN llm_requests.cache_hit_tokens  IS '输入 token 中命中缓存的数量，默认 0（老接入方不传）；未命中 = input_tokens − cache_hit_tokens，不单独存';
COMMENT ON COLUMN llm_requests.output_tokens     IS '输出 token 数';
COMMENT ON COLUMN llm_requests.total_tokens      IS '总 token 数；接入方未传时由 input + output 补齐';
COMMENT ON COLUMN llm_requests.latency           IS '耗时，单位毫秒';
COMMENT ON COLUMN llm_requests.status            IS '状态：success 成功 / error 失败';
COMMENT ON COLUMN llm_requests.error_message     IS '错误信息，成功时为 NULL';
COMMENT ON COLUMN llm_requests.estimated_cost    IS '成本快照（元），写入时按当时的模型价格算好；未匹配到模型价格为 NULL';
COMMENT ON COLUMN llm_requests.created_at        IS '调用时间（UTC）';


-- ---------------------------------------------------------------------------
-- project_anomaly_settings — 项目级异常判定标准
--
-- 【一行 = 一个项目的自定义标准】异常本来就按项目算（llm_requests 挂 project_id，
-- 基线按 (project_id, model) 分组），判定标准跟着项目走，不引入新的隔离面。
-- 查询时用 project_id 取这一行，取不到就用 config.py 里的默认值。
--
-- 【NULL = 跟随默认】三个字段都可空，NULL 表示「这一项用系统默认」。
-- 这样 config.py 里的默认标准调整时，没单独设过的项目会跟着变 ——
-- 如果保存时把 5 / 3 / 20% 写死进表，默认值就再也调不动了。
-- 恢复默认就是把三列都置回 NULL（整行全 NULL 与没有这行等价，
-- 应用层可以顺手删行，留着也不影响判定）。
--
-- 【列名与 config.py 一一对应】故意用长名字（token_anomaly_ratio 而不是 token_ratio）：
-- 解析时就是「这一列有值就用它，为空就取 settings 里同名的那个」，
-- 名字一样，读代码的人不用来回对表。
--
-- 【为什么不把 baseline_min_samples 也放进来】「近 7 天满 10 条才判定」
-- 是统计口径（样本够不够），不是异常标准（多离谱算异常）。
-- 第一版只开放真正的标准，口径保持固定 —— 免得有人把门槛降到 1 条之后满屏误报。
-- 将来要开放，加一列即可。
--
-- 【为什么用 DOUBLE PRECISION 不用 NUMERIC】倍数和错误率不参与累加、也不涉及钱，
-- 浮点表示误差在这里没有意义；NUMERIC 留给价格那种要精确对账的字段。
--
-- 【project_id 上的 UNIQUE 才是「一个项目只能有一行」的保证】没有它，
-- 重复保存会攒出多行，判定时读到哪一行取决于查询顺序，行为不可预测。
-- 这条约束顺带建了索引，按 project_id 查不用另建。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS project_anomaly_settings (
    id                    BIGSERIAL        PRIMARY KEY,           -- 主键，自增
    project_id            BIGINT           NOT NULL UNIQUE
                           REFERENCES projects(id) ON DELETE CASCADE,  -- 归属项目，一个项目最多一行
    token_anomaly_ratio   DOUBLE PRECISION,                       -- Token 异常倍数；NULL = 用默认 5.0
    latency_anomaly_ratio DOUBLE PRECISION,                       -- 响应时间异常倍数；NULL = 用默认 3.0
    error_rate_threshold  DOUBLE PRECISION,                       -- 错误率上限，小数；NULL = 用默认 0.2
    created_at            TIMESTAMPTZ      NOT NULL DEFAULT now(),-- 创建时间（UTC）
    updated_at            TIMESTAMPTZ      NOT NULL DEFAULT now(),-- 更新时间（UTC），由应用层写入时刷新

    -- 下限不是摆设：倍数给到 1 等于宣布「一切皆异常」，用户能自己把功能变成噪音；
    -- 上限 20 是天花板，填个 1000 之后这个项目就再也报不出异常了。
    -- 三个阈值的上下限在接口层还有一份 Field(ge=..., le=...)，改范围要两处一起改
    CONSTRAINT ck_project_anomaly_settings_token_ratio
        CHECK (token_anomaly_ratio IS NULL OR token_anomaly_ratio BETWEEN 1.5 AND 20),
    CONSTRAINT ck_project_anomaly_settings_latency_ratio
        CHECK (latency_anomaly_ratio IS NULL OR latency_anomaly_ratio BETWEEN 1.5 AND 20),
    -- 错误率存小数不存百分数：判定比较的是 rate（0~1），存 20 就得在每个比较处除以 100
    CONSTRAINT ck_project_anomaly_settings_error_rate
        CHECK (error_rate_threshold IS NULL OR error_rate_threshold BETWEEN 0.05 AND 1.0)
);

COMMENT ON TABLE  project_anomaly_settings                       IS '项目级异常判定标准；字段为 NULL 表示该项目该项沿用系统默认';
COMMENT ON COLUMN project_anomaly_settings.id                    IS '主键，自增';
COMMENT ON COLUMN project_anomaly_settings.project_id            IS '归属项目 id，唯一（一个项目最多一行），带索引';
COMMENT ON COLUMN project_anomaly_settings.token_anomaly_ratio   IS 'Token 异常倍数：本次 total_tokens 超过该模型近 7 天均值 × 此值即判异常；NULL = 默认 5.0';
COMMENT ON COLUMN project_anomaly_settings.latency_anomaly_ratio IS '响应时间异常倍数：本次 latency 超过该模型近 7 天均值 × 此值即判异常；NULL = 默认 3.0';
COMMENT ON COLUMN project_anomaly_settings.error_rate_threshold  IS '窗口错误率上限，小数（0.2 即 20%）：当天错误占比超过此值即判异常；NULL = 默认 0.2';
COMMENT ON COLUMN project_anomaly_settings.created_at            IS '创建时间（UTC）';
COMMENT ON COLUMN project_anomaly_settings.updated_at            IS '更新时间（UTC），由应用层写入时刷新';

-- ---------------------------------------------------------------------------
-- assistant_chats — AI 助手用量记录
--
-- 【一行 = 一次用户提问】不是一条消息，也不是一次模型调用。助手回答一个问题时，
-- 内部可能连续调几次模型、跑几次工具（查项目、查日志…），这一整轮只记一行：
-- token 是这一轮所有模型调用的总和，工具次数是这一轮的总次数。
-- 这样「平均问一次话花多少 token / 多久」才是有意义的成本指标。
--
-- 【为什么不存提问和回答的正文】管理后台要展示用户的使用情况，而按约定管理员
-- 不能看对话内容。表里干脆没有正文，管理员接口无论怎么写都不可能泄露 ——
-- 边界落在数据结构上，比落在「写接口时记得别返回」上可靠。正文本身还在
-- LangGraph 的 checkpointer 里（进程内存），再落一份库是双倍存储，
-- 而会话历史、保留期限、删号后怎么处理都是要单独设计的一整套东西。
-- V1 只解决「用量」这一个问题；将来要做对话历史，另建表、另定规则。
--
-- 【thread_id 存完整值】形如 user_7_3f2a...，是服务端拼好的那个（带 user_ 前缀）。
-- 只存用户在浏览器里生成的那半截，就分不清是谁的会话。
-- VARCHAR(100)：user_ + 最长 20 位的 id + _ + 36 位 uuid，留了余量。
--
-- 【token 分三个字段】与 llm_requests 同口径，查看和聚合的代码可以直接复用。
-- 不强制 total = input + output：不同厂商对 total 的定义有出入
-- （有的把推理 token 单独计，不一定加进 total），以厂商上报的为准。
--
-- 【为什么没有 updated_at】这是一张事实表：写进去就再也不改，和 llm_requests 一样。
-- 加 updated_at 只会让人误以为记录可以被编辑。
--
-- 【没有外键到 projects】助手是账号级能力，不挂在某个项目下面；user_id 就是隔离边界。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS assistant_chats (
    id              BIGSERIAL      PRIMARY KEY,            -- 主键，自增
    user_id         BIGINT         NOT NULL REFERENCES users(id) ON DELETE CASCADE,  -- 提问的用户
    thread_id       VARCHAR(100)   NOT NULL,                -- 会话标识，形如 user_7_3f2a...
    model           VARCHAR(100)   NOT NULL,                -- 本轮使用的模型名
    input_tokens    INTEGER        NOT NULL DEFAULT 0,      -- 本轮所有模型调用的输入 token 之和
    output_tokens   INTEGER        NOT NULL DEFAULT 0,      -- 本轮所有模型调用的输出 token 之和
    total_tokens    INTEGER        NOT NULL DEFAULT 0,      -- 本轮总 token（以厂商上报为准）
    tool_call_count INTEGER        NOT NULL DEFAULT 0,      -- 本轮调用的工具次数（只记次数，不记参数）
    latency         INTEGER        NOT NULL,                -- 从收到提问到回答结束的耗时，毫秒
    status          VARCHAR(20)    NOT NULL,                -- 状态：success / error
    error_message   TEXT,                                   -- 失败原因，成功时为 NULL
    created_at      TIMESTAMPTZ    NOT NULL DEFAULT now(),  -- 提问时间（UTC）

    CONSTRAINT ck_assistant_chats_status CHECK (status IN ('success', 'error')),
    -- 数据库层兜底：应用层算错了也写不进负数
    CONSTRAINT ck_assistant_chats_tokens CHECK (
        input_tokens >= 0 AND output_tokens >= 0 AND total_tokens >= 0
    ),
    CONSTRAINT ck_assistant_chats_tool_calls CHECK (tool_call_count >= 0),
    CONSTRAINT ck_assistant_chats_latency CHECK (latency >= 0)
);

-- 管理后台的两条主查询：按用户看用量、全局按时间统计。
-- 第二、三个字段跟着 created_at DESC 走，与 llm_requests 同款索引同一个理由：
-- 只按时间排序时同一毫秒的记录顺序不稳定，翻页会重复或漏条。
CREATE INDEX IF NOT EXISTS idx_assistant_chats_user_created
    ON assistant_chats (user_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_assistant_chats_created
    ON assistant_chats (created_at DESC, id DESC);

COMMENT ON TABLE  assistant_chats                 IS 'AI 助手用量记录：一行 = 一次用户提问（含内部多轮工具调用），不含对话正文';
COMMENT ON COLUMN assistant_chats.id              IS '主键，自增';
COMMENT ON COLUMN assistant_chats.user_id         IS '提问的用户 id；用户删除时记录一并删除';
COMMENT ON COLUMN assistant_chats.thread_id       IS '会话标识，服务端拼好的完整值，形如 user_7_3f2a...';
COMMENT ON COLUMN assistant_chats.model           IS '本轮使用的模型名';
COMMENT ON COLUMN assistant_chats.input_tokens    IS '本轮所有模型调用的输入 token 之和';
COMMENT ON COLUMN assistant_chats.output_tokens   IS '本轮所有模型调用的输出 token 之和';
COMMENT ON COLUMN assistant_chats.total_tokens    IS '本轮总 token，以厂商上报为准';
COMMENT ON COLUMN assistant_chats.tool_call_count IS '本轮调用的工具次数，只记次数不记参数';
COMMENT ON COLUMN assistant_chats.latency         IS '本轮耗时，毫秒';
COMMENT ON COLUMN assistant_chats.status          IS '状态：success 成功 / error 失败';
COMMENT ON COLUMN assistant_chats.error_message   IS '失败原因，成功时为 NULL';
COMMENT ON COLUMN assistant_chats.created_at      IS '提问时间（UTC）';
