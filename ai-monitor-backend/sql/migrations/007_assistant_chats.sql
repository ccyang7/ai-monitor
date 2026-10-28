-- ============================================================================
-- AI Monitor — assistant_chats：AI 助手的用量记录
--
-- 【执行方式】把本文件传到虚拟机上，开发库和测试库都要跑：
--     psql -U ai_monitor -d ai_monitor      -h 127.0.0.1 -f 007_assistant_chats.sql
--     psql -U ai_monitor -d ai_monitor_test -h 127.0.0.1 -f 007_assistant_chats.sql
--   ⚠️ 漏了测试库，管理后台的助手用量页会在查询时报「表不存在」
--
-- 【幂等】CREATE TABLE IF NOT EXISTS，重复执行安全。
--
-- 【同步事项】sql/schema.sql 已同步（新库只跑它）；后面还要写 app/models/assistant_chat.py。
--
-- 【一行 = 一次用户提问】不是一条消息，也不是一次模型调用。
--   助手内部可能为了回答一个问题连续调几次模型、跑几次工具（查项目、查日志…），
--   这一整轮只记一行：token 是这一轮所有模型调用的总和，工具次数是这一轮的总次数。
--   这样「平均问一次话花多少 token / 多久」才是一个有意义的成本指标。
--
-- 【为什么不存提问和回答的正文】
--   1. 管理后台要展示用户的使用情况，按约定管理员不能看对话内容。
--      表里干脆没有正文，管理员接口无论怎么写都不可能泄露 —— 比「写接口时记得别返回」
--      可靠得多，边界要落在数据结构上，不是落在人身上。
--   2. 正文已经在 LangGraph 的 checkpointer 里（进程内存），再落一份库是双倍存储，
--      而且会话历史、保留期限、用户删号后怎么处理，都是一整套要单独设计的东西。
--   V1 只解决「用量」这一个问题。将来真要做对话历史，另建表、另定规则。
--
-- 【thread_id 存完整值】形如 user_7_3f2a...，是服务端拼好的那个（带 user_ 前缀）。
--   只存用户在浏览器里生成的那半截，就分不清是谁的会话；以后再想按会话聚合还得回头补。
--   VARCHAR(100)：user_ + 最长 20 位的 id + _ + 36 位 uuid，留了余量。
--
-- 【token 三个字段分开存】与 llm_requests 同口径，查看和聚合的代码可以直接复用。
--   不强制 total = input + output：不同厂商对 total 的定义有出入
--   （有的把推理 token 单独计，不一定加进 total），以厂商上报的为准。
--
-- 【为什么没有 updated_at】这是一张事实表：一行写进去就再也不改，
--   和 llm_requests 一样。加 updated_at 只会让人误以为记录可以被编辑。
--
-- 【没有外键到 projects】助手是账号级能力，不挂在某个项目下面；
--   user_id 就是它的隔离边界。
-- ============================================================================

CREATE TABLE IF NOT EXISTS assistant_chats (
    id              BIGSERIAL      PRIMARY KEY,             -- 主键，自增
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
-- 第二个字段跟着 created_at DESC 走，与 llm_requests 的同款索引同一个理由：
-- 时间相同时要能稳定翻页。id DESC 兜底保证顺序唯一。
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
