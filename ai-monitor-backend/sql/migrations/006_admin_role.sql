-- ============================================================================
-- AI Monitor — users 增加 role 字段：管理员的身份来源
--
-- 【执行方式】把本文件传到虚拟机上，开发库和测试库都要跑：
--     psql -U ai_monitor -d ai_monitor      -h 127.0.0.1 -f 006_admin_role.sql
--     psql -U ai_monitor -d ai_monitor_test -h 127.0.0.1 -f 006_admin_role.sql
--   ⚠️ 漏了测试库，管理员相关的验收脚本会在建表之外全部报「字段不存在」
--
-- 【幂等】ADD COLUMN IF NOT EXISTS + DROP CONSTRAINT IF EXISTS 后重建，
--   重复执行安全。
--
-- 【同步事项】sql/schema.sql 已同步（新库只跑它）；app/models/user.py 加 role 映射。
--
-- 【为什么用 VARCHAR + CHECK 而不是 PG 的 ENUM】
--   与 projects.status / models.status 同一个理由：ENUM 增删取值要 ALTER TYPE，
--   而角色将来可能不止两个（只读审计、运营）。加值只要改 CHECK 的列表。
--
-- 【为什么默认 'user'，不是可空、更不是默认 admin】
--   这是安全字段，默认值必须是最小权限。可空意味着每个读角色的地方都要处理
--   NULL（漏一处就是越权）；默认 admin 会让「忘了赋角色」悄悄变成超级权限。
--   默认 'user' 还有个附带好处：注册接口一个字都不用改。
--
-- 【不加索引】只有两个取值，单列索引区分度太低，PG 的规划器会直接选择全表扫描；
--   用户表的规模也远没到需要索引的程度。
-- ============================================================================

ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(20) NOT NULL DEFAULT 'user';

-- 先 DROP 再 ADD：ADD CONSTRAINT 没有 IF NOT EXISTS，重复执行会报「约束已存在」
ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_role;
ALTER TABLE users ADD CONSTRAINT ck_users_role CHECK (role IN ('user', 'admin'));

COMMENT ON COLUMN users.role IS '角色：user 普通用户 / admin 管理员，默认最小权限';

-- 【生成第一个管理员】确定用户名后手动执行（不在本脚本里自动跑，因为不知道要提升谁）：
--   UPDATE users SET role = 'admin', updated_at = now() WHERE username = '<用户名>';
-- updated_at 要手动写：数据库没有 ON UPDATE 触发器，SQL 直接改不会刷新它。
