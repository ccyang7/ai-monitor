-- ============================================================================
-- AI Monitor — 修正 uq_models_owner_name：唯一性只管 active 行
--
-- 【本文件是表结构的唯一真相】
--   它在 002_models_and_llm_requests.sql 的基础上做增量修改。
--   002 里的索引定义不改写（历史脚本记录当时的样子），修正走本文件。
--   ⚠️ 本脚本不改 ORM —— models 的唯一索引只存在于 SQL 里，
--   app/models/model.py 的 __table_args__ 本来就没有对应声明，无需同步。
--
-- 【执行方式】把本文件传到虚拟机上，然后：
--     psql -U ai_monitor -d ai_monitor      -h 127.0.0.1 -f 005_models_unique_active.sql
--     psql -U ai_monitor -d ai_monitor_test -h 127.0.0.1 -f 005_models_unique_active.sql
--   ⚠️ 开发库和测试库都要跑，漏了测试库，check_models.py 的
--   「禁用后重建同名」用例会以 500 失败
--
-- 【幂等】DROP IF EXISTS + CREATE IF NOT EXISTS，重复执行安全。
--   小表加索引不锁多久，不用 CONCURRENTLY（它不能在事务块里执行）。
--
-- 【本次改动的背景】
--   002 建的索引是 (owner_id, model_name) WHERE owner_id IS NOT NULL ——
--   已禁用的行也占着名字。但应用层的重名校验（services/model.py 的
--   _ensure_name_available）查的是 status = 'active'，禁用行不参与查重、放行。
--   两边口径不一致的后果：
--     建模型 → 上报过 → 禁用 → 用同一个名字再建 → 应用层放行、INSERT 撞索引
--     → IntegrityError 没有被翻译 → 用户拿到 500
--   新加的「未配价模型提示」正好会主动把用户领进这条路（点击提示里的
--   模型名补价 = 用同名再建），所以先把两边口径对齐。
--
-- 【为什么是收窄索引而不是改应用层】
--   唯一索引的本意是「一个名字不能有两个在用的价格」（002 的注释原文：
--   同名两条会让上报随机匹配到其中一个价格），禁用行不参与价格匹配，
--   本就不该占名字。收窄索引之后，索引口径与 _ensure_name_available 的
--   校验口径完全一致，POST 和 PATCH（改名撞自己禁用行的名字）两条路径
--   一起修好，Python 一行不用改。
-- ============================================================================


DROP INDEX IF EXISTS uq_models_owner_name;

CREATE UNIQUE INDEX IF NOT EXISTS uq_models_owner_name
    ON models (owner_id, model_name) WHERE owner_id IS NOT NULL AND status = 'active';

COMMENT ON INDEX uq_models_owner_name IS
    '同一用户的自建模型不许重名；只约束 active 行 —— 禁用行保留下来供历史追溯，不占名字';
