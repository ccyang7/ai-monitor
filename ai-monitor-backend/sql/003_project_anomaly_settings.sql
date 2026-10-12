-- ============================================================================
-- AI Monitor — 阶段 9 建表脚本：项目级异常判定标准
--
-- 【本文件是表结构的唯一真相】
--   ORM 模型（app/models/project_anomaly_settings.py）只做映射，不建表。
--   改表结构时，必须同时改这两处：
--     ai-monitor-backend/sql/003_project_anomaly_settings.sql     ← 你在这里
--     ai-monitor-backend/app/models/project_anomaly_settings.py
--
-- 【执行方式】把本文件传到虚拟机上，然后：
--     psql -U ai_monitor -d ai_monitor      -h 127.0.0.1 -f 003_project_anomaly_settings.sql
--     psql -U ai_monitor -d ai_monitor_test -h 127.0.0.1 -f 003_project_anomaly_settings.sql
--   ⚠️ 开发库和测试库都要跑，漏了测试库会 check_anomaly.py 报「表不存在」
--
-- 【幂等】全部使用 IF NOT EXISTS，重复执行不会报错。
--   改字段请写 004_xxx.sql，不要改这里重跑。
--
-- 【取值范围有两个地方要改】三个阈值的上下限在下面有 CHECK 约束兜底，
--   接口层还有一份 Field(ge=..., le=...) 负责给出友好报错。
--   调整范围时两处一起改，否则会出现「接口放行、数据库报错」，
--   或者反过来「接口拦掉、数据库其实允许」。
--
-- 【本脚本包含的表】
--   project_anomaly_settings  项目级异常判定标准   ← 阶段 9 新增
-- ============================================================================


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
-- 【为什么不自增 id 当唯一键就够了】主键必须是 id（全库一致），
-- project_id 上的 UNIQUE 约束才是「一个项目只能有一行」的保证 ——
-- 没有它，重复保存会攒出多行，判定时读到哪一行取决于查询顺序，行为不可预测。
-- 这条约束顺带建了索引，按 project_id 查不用另建。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS project_anomaly_settings (
    id                      BIGSERIAL        PRIMARY KEY,           -- 主键，自增
    project_id              BIGINT           NOT NULL UNIQUE
                             REFERENCES projects(id) ON DELETE CASCADE,  -- 归属项目，一个项目最多一行
    token_anomaly_ratio     DOUBLE PRECISION,                       -- Token 异常倍数；NULL = 用默认 5.0
    latency_anomaly_ratio   DOUBLE PRECISION,                       -- 响应时间异常倍数；NULL = 用默认 3.0
    error_rate_threshold    DOUBLE PRECISION,                       -- 错误率上限，小数；NULL = 用默认 0.2
    created_at              TIMESTAMPTZ      NOT NULL DEFAULT now(),-- 创建时间（UTC）
    updated_at              TIMESTAMPTZ      NOT NULL DEFAULT now(),-- 更新时间（UTC），由应用层写入时刷新

    -- 下限不是摆设：倍数给到 1 等于宣布「一切皆异常」，用户能自己把功能变成噪音；
    -- 上限 20 是天花板，填个 1000 之后这个项目就再也报不出异常了
    CONSTRAINT ck_project_anomaly_settings_token_ratio
        CHECK (token_anomaly_ratio IS NULL OR token_anomaly_ratio BETWEEN 1.5 AND 20),
    CONSTRAINT ck_project_anomaly_settings_latency_ratio
        CHECK (latency_anomaly_ratio IS NULL OR latency_anomaly_ratio BETWEEN 1.5 AND 20),
    -- 错误率存小数不存百分数：判定比较的是 rate（0~1），存 20 就得在每个比较处除以 100
    CONSTRAINT ck_project_anomaly_settings_error_rate
        CHECK (error_rate_threshold IS NULL OR error_rate_threshold BETWEEN 0.05 AND 1.0)
);

COMMENT ON TABLE  project_anomaly_settings                     IS '项目级异常判定标准；字段为 NULL 表示该项目该项沿用系统默认';
COMMENT ON COLUMN project_anomaly_settings.id                  IS '主键，自增';
COMMENT ON COLUMN project_anomaly_settings.project_id          IS '归属项目 id，唯一（一个项目最多一行），带索引';
COMMENT ON COLUMN project_anomaly_settings.token_anomaly_ratio IS 'Token 异常倍数：本次 total_tokens 超过该模型近 7 天均值 × 此值即判异常；NULL = 默认 5.0';
COMMENT ON COLUMN project_anomaly_settings.latency_anomaly_ratio IS '响应时间异常倍数：本次 latency 超过该模型近 7 天均值 × 此值即判异常；NULL = 默认 3.0';
COMMENT ON COLUMN project_anomaly_settings.error_rate_threshold IS '窗口错误率上限，小数（0.2 即 20%）：当天错误占比超过此值即判异常；NULL = 默认 0.2';
COMMENT ON COLUMN project_anomaly_settings.created_at          IS '创建时间（UTC）';
COMMENT ON COLUMN project_anomaly_settings.updated_at          IS '更新时间（UTC），由应用层写入时刷新';
