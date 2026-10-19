-- 骑行功率训练系统 · 正式应用库建表脚本（第一批：账户段 + 导入段 + 活动）
-- 依据：PROJECT_CONTEXT.md 6.6「数据库设计」与「落地顺序」第一步
-- 范围：只建 4 张表 —— users / user_profiles / files / activities
--       每秒数据表（第二步）、课程支线（第三步）、导入批次等辅助表，随对应功能再增补
-- 定位：原始文件落盘不入库，本库只存台账与派生结果（D10）；
--       正式代码必须通过 ORM 抽象访问，切换靠连接串（D4），本脚本只负责建库基线。
--
-- 用法（在项目根目录执行）：
--   python -c "import sqlite3,pathlib; c=sqlite3.connect('storage/app.db'); c.executescript(pathlib.Path('backend/src/data_access/storage/schema.sql').read_text(encoding='utf-8')); c.commit(); c.close()"
--
-- 三条必须遵守的约束（6.6）：
--   1. 内容哈希 + 唯一约束，重复导入由数据库挡住；
--   2. 外键级联删除，删活动连带删其每秒数据；磁盘原始文件不随记录自动删除；
--   3. 数据库只存相对路径，绝不存绝对路径。

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA user_version = 1;

-- ─────────────────────────────────────────────────────────────
-- 表 1 users：账户身份
-- 口令只存哈希，禁止明文（与 feature-tests 库一致）
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,                     -- 登录名
    password_hash TEXT    NOT NULL,                            -- 口令哈希，禁止明文
    display_name  TEXT,                                        -- 展示名
    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at    TEXT
);

-- ─────────────────────────────────────────────────────────────
-- 表 2 user_profiles：用户档案（与 users 1:1）
-- 存放「当前参数」。活动表会另存参数快照，改参数不影响历史数字（6.6）
-- 三项均允许为空：未填写时相关指标按不可计算处理，不硬凑数值
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS user_profiles (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL UNIQUE,
    ftp_w          INTEGER,                                    -- 入参 FTP（W），D17；与派生 eFTP 分离
    weight_kg      REAL,                                       -- 体重（kg）
    max_heart_rate INTEGER,                                    -- 最大心率，以手动设定值为准，D16
    created_at     TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at     TEXT,
    CHECK (ftp_w IS NULL OR ftp_w > 0),
    CHECK (weight_kg IS NULL OR weight_kg > 0),
    CHECK (max_heart_rate IS NULL OR max_heart_rate > 0),
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

-- ─────────────────────────────────────────────────────────────
-- 表 3 files：导入文件台账（导入链的锚点）
-- 磁盘上按 storage/raw/<user_id>/<YYYY>/<MM>/<sha256>.<ext> 落盘，
-- 本表只存相对路径与元数据；原始文件不进库（D10 / 6.7）
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS files (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL,
    original_name  TEXT    NOT NULL,                           -- 原始文件名，供展示与下载
    relative_path  TEXT    NOT NULL,                           -- 相对路径，如 storage/raw/1/2026/10/<sha256>.fit
    content_hash   TEXT    NOT NULL UNIQUE,                    -- 内容哈希，导入幂等的唯一依据
    file_size      INTEGER NOT NULL CHECK (file_size > 0),     -- 字节数
    file_format    TEXT    NOT NULL
                   CHECK (file_format IN ('fit', 'gpx', 'tcx')), -- 扩展格式
    content_type   TEXT    NOT NULL
                   CHECK (content_type IN ('activity', 'workout')), -- 读自文件内容，不用文件名判断
    parser_version TEXT    NOT NULL,                           -- 解析器版本，支持按版本筛选重解析
    parse_status   TEXT    NOT NULL DEFAULT 'pending'
                   CHECK (parse_status IN ('pending', 'parsing', 'parsed', 'failed', 'ignored')),
    parse_error    TEXT,                                       -- 失败原因，failed 时必填
    uploaded_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    parsed_at      TEXT,
    -- 状态显式化：失败必须附错误原因，不允许用"有没有数据"反推状态
    CHECK (parse_status <> 'failed' OR parse_error IS NOT NULL),
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_files_user_uploaded
    ON files (user_id, uploaded_at);
CREATE INDEX IF NOT EXISTS idx_files_user_status
    ON files (user_id, parse_status);

-- ─────────────────────────────────────────────────────────────
-- 表 4 activities：一次骑行汇总（由 files 解析得到，属可重建的派生结果）
-- 一个文件最多产生一个活动（file_id 唯一）；按内容类型分流，不会同时产生课程
-- 除基础摘要外，按 6.6 额外保留三组列：
--   参数快照    指标用参数算出，用户改参数后历史数字仍能自证
--   设备原始值  对拍不必重新解析文件，差异可追溯
--   数据完整度  聚合时可显式选口径，界面可标注数据不完整
-- 本地计算的 NP / IF / TSS 等指标列，等批次4 领域计算契约冻结后再按需增补
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS activities (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id              INTEGER NOT NULL,                     -- 冗余用户列，便于按用户隔离查询
    file_id              INTEGER NOT NULL UNIQUE,              -- 一个文件最多一个活动
    activity_type        TEXT    NOT NULL,                     -- 运动类型，读自文件内容（如 cycling）
    start_time           TEXT    NOT NULL,                     -- ISO 8601 本地开始时间
    duration_s           REAL,                                 -- 计时时长（秒）
    elapsed_s            REAL,                                 -- 总历时（秒），时长口径另行固定
    distance_m           REAL,                                 -- 距离（米）
    elevation_gain_m     REAL,                                 -- 爬升（米）
    avg_power_w          REAL,                                 -- 平均功率（W）
    max_power_w          REAL,                                 -- 最大功率（W）
    avg_hr               REAL,                                 -- 平均心率（bpm）
    max_hr               REAL,                                 -- 最大心率（bpm）
    avg_cadence          REAL,                                 -- 平均踏频（rpm）
    avg_speed_mps        REAL,                                 -- 平均速度（m/s）
    max_speed_mps        REAL,                                 -- 最大速度（m/s）
    calories             REAL,                                 -- 消耗（kcal）

    -- 参数快照（创建活动时从 user_profiles 复制）
    ftp_w_snapshot          INTEGER,
    weight_kg_snapshot      REAL,
    max_heart_rate_snapshot INTEGER,

    -- 设备原始值（FIT session 内由码表计算的字段，用于对拍）
    device_np_w              REAL,                             -- 设备标准化功率
    device_intensity_factor  REAL,                             -- 设备强度因子
    device_tss               REAL,                             -- 设备训练压力分数
    device_ftp_w             INTEGER,                          -- 设备记录的阈值功率

    -- 数据完整度
    sample_count          INTEGER,                             -- 样本总数
    power_valid_seconds   INTEGER,                             -- 功率有效秒数
    hr_valid_seconds      INTEGER,                             -- 心率有效秒数
    quality_flag          TEXT,                                -- 活动级质量标记（取值待批次4 契约确认）

    created_at           TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at           TEXT,
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY (file_id) REFERENCES files (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_activities_user_start
    ON activities (user_id, start_time);
CREATE INDEX IF NOT EXISTS idx_activities_user_type
    ON activities (user_id, activity_type);
