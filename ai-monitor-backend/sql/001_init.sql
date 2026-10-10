-- ============================================================================
-- AI Monitor — 初始化建表脚本（阶段 3）
--
-- 【本文件是表结构的唯一真相】
--   ORM 模型（app/models/）只做映射，不建表（没有 Base.metadata.create_all）。
--   改表结构时，必须同时改这两处：
--     ai-monitor-backend/sql/001_init.sql   ← 你在这里
--     ai-monitor-backend/app/models/*.py
--   两边不一致时，以本文件为准（数据库里实际长什么样，由它决定）。
--
-- 【执行前提】数据库和用户还没有，先在虚拟机上执行这两条（用 postgres 超级用户）：
--     CREATE USER ai_monitor WITH PASSWORD '你的密码';
--     CREATE DATABASE ai_monitor OWNER ai_monitor;
--
-- 【执行方式】把本文件传到虚拟机上，然后：
--     psql -U ai_monitor -d ai_monitor -h 127.0.0.1 -f 001_init.sql
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
--   注意：这也意味着它【不会】帮你更新已存在的表结构。改字段请写新的
--   002_xxx.sql，而不是改这里重跑。
--
-- 【本脚本包含的表】
--   users      用户
--   projects   项目       ← 平台核心单位
--   api_keys   项目密钥   ← 数据上报的凭证
--   （models / llm_requests 两张表属于阶段 6，届时另写脚本）
-- ============================================================================


-- ---------------------------------------------------------------------------
-- users — 用户
-- 所有数据的最终归属人。用户隔离的硬边界就是这张表的 id。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL    PRIMARY KEY,               -- 主键，自增
    username      VARCHAR(50)  NOT NULL UNIQUE,           -- 登录名，全局唯一
    password_hash VARCHAR(255) NOT NULL,                  -- 密码哈希，绝不存明文
    email         VARCHAR(255) UNIQUE,                    -- 邮箱，唯一但可空
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),    -- 创建时间（UTC）
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()     -- 更新时间（UTC）
);

COMMENT ON TABLE  users               IS '用户';
COMMENT ON COLUMN users.id            IS '主键，自增';
COMMENT ON COLUMN users.username      IS '登录名，全局唯一';
COMMENT ON COLUMN users.password_hash IS '密码哈希，绝不存明文';
COMMENT ON COLUMN users.email         IS '邮箱，唯一但可空';
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
-- 说明：以下索引属于阶段 6 的 llm_requests 表，本次不建，记录在此备忘
--   llm_requests(project_id, created_at)   监控中心所有查询的基础
--   llm_requests(model)                    按模型筛选
-- ---------------------------------------------------------------------------
