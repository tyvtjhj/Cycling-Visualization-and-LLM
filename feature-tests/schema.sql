-- 骑行功率训练系统 · 功能测试库 建表脚本
-- 依据：PROJECT_CONTEXT.md 6.6 数据库设计（账户段 + 导入段）
-- 说明：本脚本只建当前需要的两张表，其余表按 6.6 落地顺序随功能增补
-- 用法：sqlite3 feature_test.db ".read schema.sql"

PRAGMA foreign_keys = ON;

-- 表1 用户：身份信息 + 个人训练参数
-- 个人参数在此并入本表（功能测试库的简化）；正式库按 6.6 拆为用户与用户档案两张表
-- 三个参数允许为空：未填写时相关指标按不可计算处理，不硬凑数值
CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    username       TEXT    NOT NULL UNIQUE,          -- 登录名
    password_hash  TEXT    NOT NULL,                 -- 口令哈希，禁止存明文
    display_name   TEXT,                             -- 展示名
    ftp            INTEGER,                          -- 入参功能阈值功率，单位 W，见 D17
    weight_kg      REAL,                             -- 体重，单位 kg
    max_heart_rate INTEGER,                          -- 最大心率，以用户档案手动设定值为准，见 D16
    created_at     TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at     TEXT
);

-- 表2 文件：用户导入文件的台账与磁盘位置
-- 只存相对路径，绝对路径不落库，否则换机器即断链
CREATE TABLE IF NOT EXISTS files (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL,
    original_name  TEXT    NOT NULL,                -- 原始文件名，供展示与下载
    relative_path  TEXT    NOT NULL,                -- 存储相对路径，如 raw/1/2026/09/<sha256>.fit
    content_hash   TEXT    NOT NULL UNIQUE,         -- 内容哈希，导入去重的依据
    file_size      INTEGER NOT NULL,                -- 字节数
    file_format    TEXT    NOT NULL
                   CHECK (file_format IN ('fit', 'gpx', 'tcx')),   -- 扩展格式
    content_type   TEXT    NOT NULL
                   CHECK (content_type IN ('activity', 'workout')), -- 读自文件内容，不用文件名判断
    parser_version TEXT    NOT NULL,                -- 支持按版本筛选需重解析的文件
    parse_status   TEXT    NOT NULL DEFAULT 'pending'
                   CHECK (parse_status IN ('pending', 'parsing', 'parsed', 'failed', 'ignored')),
    parse_error    TEXT,                            -- 失败原因，状态为 failed 时必填
    uploaded_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    parsed_at      TEXT,
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_files_user_uploaded ON files (user_id, uploaded_at);
