-- ============================================================================
-- AI Monitor — 模型管理实施（批次 1）改表脚本：缓存两档价
--
-- 【本文件是表结构的唯一真相】
--   它在 002_models_and_llm_requests.sql 的基础上做增量修改。
--   改表结构时，必须同时改这两处：
--     ai-monitor-backend/sql/004_cache_pricing.sql                        ← 你在这里
--     ai-monitor-backend/app/models/model.py、app/models/llm_request.py
--   ⚠️ ORM 的同步修改在批次 2 进行 —— 本脚本执行、表结构确认之后，再动 ORM
--
-- 【执行方式】把本文件传到虚拟机上，然后：
--     psql -U ai_monitor -d ai_monitor      -h 127.0.0.1 -f 004_cache_pricing.sql
--     psql -U ai_monitor -d ai_monitor_test -h 127.0.0.1 -f 004_cache_pricing.sql
--   ⚠️ 开发库和测试库都要跑，漏了测试库会 check_ingest.py 报「列不存在」
--
-- 【幂等】ADD COLUMN 用 IF NOT EXISTS；ADD CONSTRAINT 在 PG 里没有
--   IF NOT EXISTS，用 DO 块查 pg_constraint 判断后再加。重复执行安全。
--
-- 【本次改动的背景】
--   部分厂商（如 DeepSeek）对输入 token 分「缓存命中 / 未命中」两档计价。
--   原来 models 表只有一档 input_price 装不下两档，有缓存命中的调用成本偏高。
--   本次加两个字段把两档价打通：
--     models.cache_hit_price        第二档价（可空 = 该模型无缓存档）
--     llm_requests.cache_hit_tokens 输入中命中的数量（未命中数用减法算，不存字段）
-- ============================================================================


-- ---------------------------------------------------------------------------
-- models.cache_hit_price — 缓存命中单价
--
-- 【为什么可空，而不是默认等于 input_price】
--   NULL 与「价格是 0」是两回事，也与「等于未命中价」是两回事：
--     NULL     该模型没有缓存档（或价格未知）→ 计费时命中部分按 input_price 算
--     0        该模型的缓存价就是 0（免费模型的缓存命中不花钱）
--   把 NULL 存成 input_price 的副本，就丢失了「没配过」这个信息 ——
--   用户以后追问「这个模型有没有缓存优惠」，库里答不上来。
--
-- 【单位与量纲】元 / 百万 token，与 input_price / output_price 同一口径，
--   照抄厂商官网的报价数字即可，不做换算。
-- ---------------------------------------------------------------------------
ALTER TABLE models
    ADD COLUMN IF NOT EXISTS cache_hit_price NUMERIC(12, 6);

-- 价格不能为负（与 002 的 ck_models_price_nonneg 同一条理由：负价格会让成本统计出现负数）。
-- 单独一条约束而不是改旧的：约束名更精准，PG 报错时一眼看出是哪个字段越界。
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_models_cache_hit_price_nonneg'
          AND conrelid = 'models'::regclass
    ) THEN
        ALTER TABLE models ADD CONSTRAINT ck_models_cache_hit_price_nonneg
            CHECK (cache_hit_price IS NULL OR cache_hit_price >= 0);
    END IF;
END $$;

COMMENT ON COLUMN models.cache_hit_price IS
    '缓存命中单价（元 / 百万 token）；NULL = 该模型无缓存档，命中部分按 input_price 算；0 是真实价格（免费模型的缓存命中）';


-- ---------------------------------------------------------------------------
-- llm_requests.cache_hit_tokens — 输入 token 中命中缓存的数量
--
-- 【只存命中数，不存未命中数】
--   未命中 = input_tokens − cache_hit_tokens，用时减法算（成本公式里就是这么用的）。
--   两个数都存，就多一个「对不上」的口径 —— 减出来的一定自洽，存下来的不一定。
--
-- 【为什么 NOT NULL DEFAULT 0】
--   老接入方不传这个字段（D2 决策：平台侧先做，埋点后补），默认 0 让它们
--   的行为与改动前完全一致：全部输入按未命中价算，成本逐分钱不变。
--
-- 【为什么加这条列不会锁表】PG 11 起 ADD COLUMN ... DEFAULT 是瞬时操作，
--   默认值存在 catalog 里，不重写表数据。
-- ---------------------------------------------------------------------------
ALTER TABLE llm_requests
    ADD COLUMN IF NOT EXISTS cache_hit_tokens INTEGER NOT NULL DEFAULT 0;

-- 命中数不能为负，也不能超过输入总数 —— 物理上命中是输入的子集。
-- 应用层（schema）已校验 ≤ input_tokens，这条是 DB 层兜底防绕过，
-- 与 002 的 ck_llm_requests_tokens 同一个思路。
--
-- 加约束时 PG 会对存量行做一次全表校验，这里必然通过：
-- 新列默认 0，而 002 的 ck_llm_requests_tokens 已保证 input_tokens >= 0，
-- 所以「0 <= input_tokens」对每一行都成立 —— 不存在加约束失败的场景。
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_llm_requests_cache_hit'
          AND conrelid = 'llm_requests'::regclass
    ) THEN
        ALTER TABLE llm_requests ADD CONSTRAINT ck_llm_requests_cache_hit
            CHECK (cache_hit_tokens >= 0 AND cache_hit_tokens <= input_tokens);
    END IF;
END $$;

COMMENT ON COLUMN llm_requests.cache_hit_tokens IS
    '输入 token 中命中缓存的数量，默认 0（老接入方不传）；未命中 = input_tokens − cache_hit_tokens，不单独存';
