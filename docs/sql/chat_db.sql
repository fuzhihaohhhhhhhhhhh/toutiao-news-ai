-- ════════════════════════════════════════════════════════════
-- PostgreSQL 聊天库表结构（chat_db）
--
-- 为什么需要这份文件：
--   项目原先**没有**聊天库的建表脚本（表是手工建的），也没有用
--   Base.metadata.create_all()，导致他人 clone 后无法建出这两张表。
--   这里按 backend/models/chat.py 的 ORM 定义补齐。
--
-- 用法：
--   1) 建库（用你的 postgres 超级用户）：
--        psql -U postgres -c "CREATE DATABASE chat_db;"
--   2) 建业务账号（用户名/密码建议与 backend/.env 里的 PG_USER / PG_PASSWORD 一致）：
--        psql -U postgres -c "CREATE USER chatuser WITH PASSWORD '你的密码';"
--        psql -U postgres -c "GRANT ALL PRIVILEGES ON DATABASE chat_db TO chatuser;"
--   3) 建表（连到 chat_db 执行本文件）：
--        psql -U chatuser -d chat_db -f chat_db.sql
--
-- 注意：与 MySQL 业务库**故意分库**，两张表都不建跨库外键，
--       user_id 的合法性完全由应用层的令牌鉴权保证（见 backend/utils/auth.py）。
-- ════════════════════════════════════════════════════════════

-- 聊天会话表
CREATE TABLE IF NOT EXISTS chat_session (
    id         SERIAL       PRIMARY KEY,
    user_id    INTEGER      NOT NULL,
    title      VARCHAR(50)  NOT NULL,
    created_at TIMESTAMP    NOT NULL DEFAULT now(),
    updated_at TIMESTAMP    NOT NULL DEFAULT now()
);

COMMENT ON TABLE  chat_session            IS '聊天会话表';
COMMENT ON COLUMN chat_session.user_id    IS '用户ID（对应 MySQL user 表，不建跨库外键）';
COMMENT ON COLUMN chat_session.title      IS '会话名称，格式：年-月-日-时.分';
COMMENT ON COLUMN chat_session.updated_at IS '每次新增消息时刷新，用于会话列表按最近活跃排序';

-- 按 user_id 查会话列表是高频操作
CREATE INDEX IF NOT EXISTS fk_chat_session_user_idx ON chat_session (user_id);


-- 聊天消息表
CREATE TABLE IF NOT EXISTS chat_message (
    id         SERIAL      PRIMARY KEY,
    session_id INTEGER     NOT NULL REFERENCES chat_session (id),
    role       VARCHAR(20) NOT NULL,
    content    TEXT        NOT NULL,
    created_at TIMESTAMP   NOT NULL DEFAULT now()
);

COMMENT ON TABLE  chat_message            IS '聊天消息表（用户提问 + AI 回复）';
COMMENT ON COLUMN chat_message.role       IS '消息角色：user / assistant';
COMMENT ON COLUMN chat_message.content    IS '消息内容（用 TEXT 支持长回复）';

-- 按 session_id 查消息历史是高频操作
CREATE INDEX IF NOT EXISTS fk_chat_message_session_idx ON chat_message (session_id);
