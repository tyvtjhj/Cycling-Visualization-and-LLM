# Garmin 活动镜像库：结构与变更记录

> 位置：`devdata/garmin/garmin_sync.db`（本地开发库，已被 `.gitignore` 排除，**不入库**）
> 定位：开发期活动镜像 + 原始文件下载索引，不是 PROJECT_CONTEXT 6.6 的正式应用库
> 建表脚本：`devdata/garmin/schema.sql`；同步脚本：`devdata/garmin/sync_activities.py`
> 下载脚本：`feature-tests/garmin_health/standalone/garmin_download.py`
> 本文只记录库结构、字段与统计结果，不含账号、活动 ID、活动名称、地点与时间。

## 一、用途与边界

- 把 Garmin 活动列表的**外部可见信息**镜像到本地，供开发期查看、对拍与下载索引；
- 原始 ZIP/FIT 也存在本机 `devdata/garmin/raw/`，数据库只存相对路径与哈希；
- 按 PROJECT_CONTEXT 3.1，正式系统仍只走「平台导出 FIT/GPX/TCX 导入」，本库不属于交付功能；
- 库内含个人活动数据，只在本地保留，不提交、不对外分发。

## 二、版本与变更记录

| 版本 | 日期 | 变更内容 |
|---|---|---|
| v1 | 2026-10-19 | 初始库：`garmin_activity`（20 列：活动 ID/名称/类型/开始时间/日期/距离/时长/爬升/均速/均心率/卡路里 + FIT 索引/下载状态）+ `sync_state` |
| v2 | 2026-10-20 | 按外部活动可见字段扩展到 **53 列**：位置/设备/隐私、GMT/时区、总历时/移动时长、下降/海拔、最大速度、最大/最小心率、BMR 热量、功率/NP/20 分钟最大功率/IF/TSS、踏频、温度、训练效果/无氧效果/VO2max、划水、圈数、`raw_json`；重建空库并同步 **709 条**活动 |
| v3 | 2026-10-20 | 新增 `raw_zip_path` / `raw_zip_size` / `raw_zip_sha256`，合计 **56 列**；下载首条活动的原始 ZIP/FIT 并回填下载索引（其余仍 `pending`） |

> v2 重建时库内为 0 行，没有数据损失；v3 通过 `ALTER TABLE` 增量加列。

## 三、当前结构（56 列，按组）

| 分组 | 列 |
|---|---|
| 标识 | `activity_id`(PK)、`activity_uuid`、`activity_name`、`activity_type`、`location_name`、`manufacturer`、`device_id`、`privacy` |
| 时间 | `start_time_local`、`start_time_gmt`、`start_date`、`time_zone_id` |
| 距离/时长/爬升 | `distance_m`、`duration_s`、`elapsed_s`、`moving_s`、`elevation_gain_m`、`elevation_loss_m`、`min_elevation_m`、`max_elevation_m` |
| 速度 | `avg_speed_mps`、`max_speed_mps` |
| 心率 | `avg_hr`、`max_hr`、`min_hr` |
| 热量 | `calories`、`bmr_calories` |
| 功率/负荷 | `avg_power_w`、`max_power_w`、`norm_power_w`、`max_20min_power_w`、`intensity_factor`、`tss` |
| 踏频 | `avg_cadence`、`max_cadence` |
| 温度 | `avg_temperature_c`、`max_temperature_c`、`min_temperature_c` |
| 训练效果 | `training_effect`、`anaerobic_training_effect`、`vo2max` |
| 其他指标 | `strokes`、`lap_count` |
| 完整外部对象 | `raw_json`（保留未单列出来的可见字段，防止 Garmin 后续新增字段丢失） |
| 原始文件索引 | `raw_zip_path`、`raw_zip_size`、`raw_zip_sha256`、`fit_path`、`fit_size`、`fit_sha256`、`detail_json_path` |
| 下载状态 | `download_status`（`pending/downloaded/missing/failed/ignored`）、`download_error`、`downloaded_at` |
| 元数据 | `created_at`、`updated_at` |

索引：

- `idx_garmin_activity_start_date`、`idx_garmin_activity_type`、`idx_garmin_activity_status`
- `uq_garmin_activity_uuid`（唯一，非空）
- `uq_garmin_activity_fit_sha256`（唯一，非空）

同步状态表 `sync_state`：`last_sync_at`、`last_activity_id`（活动 ID 不在此文档记录）、`last_sync_count`、`page_size=100`、`max_activities=10000`。

## 四、同步与校验结果（2026-10-20）

| 项目 | 结果 |
|---|---|
| 数据库行数 | **709** |
| Garmin 服务端 `count_activities()` | **709**（一致） |
| 二次拉取外部数据逐字段比对 | **0 处不一致**（43 个映射字段） |
| `PRAGMA integrity_check` | `ok` |
| `PRAGMA user_version` | `3` |
| 下载状态 | `downloaded=1`，`pending=708` |

按活动类型分布：

| 类型 | 数量 |
|---|---:|
| indoor_cycling | 315 |
| road_biking | 185 |
| cycling | 70 |
| indoor_cardio | 61 |
| running | 22 |
| strength_training | 12 |
| badminton | 9 |
| virtual_ride | 9 |
| walking | 8 |
| other | 5 |
| stair_climbing | 4 |
| breathwork | 2 |
| lap_swimming | 2 |
| stop_watch | 2 |
| trail_running | 2 |
| treadmill_running | 1 |

部分字段为空属于正常：外部源本身只在对应活动类型上提供该字段（例如功率、踏频、爬升），不是数据库丢失。

## 五、重建、同步与下载

```bash
# 1) 重建空库（schema.sql 幂等，重复执行不破坏已有数据）
python -c "import sqlite3,pathlib; c=sqlite3.connect('devdata/garmin/garmin_sync.db'); c.executescript(pathlib.Path('devdata/garmin/schema.sql').read_text(encoding='utf-8')); c.commit(); c.close()"

# 2) 同步活动列表（会做二次拉取逐字段校验）
uv run --no-project --python 3.12 --with "garminconnect>=0.3.4" \
  python devdata/garmin/sync_activities.py

# 3) 下载原始 ZIP/FIT（先下载 1 条验证，再批量）
uv run --no-project --python 3.12 --with "garminconnect>=0.3.4" \
  python feature-tests/garmin_health/standalone/garmin_download.py --limit 1
```

## 六、隐私与边界

1. 数据库、ZIP、FIT 均含个人活动数据，只允许保存在本机 `devdata/`（已 gitignore），**不得提交或上传**；
2. `feature-tests/garmin_health/standalone/credentials.json` 与 `tmp/garmin_tokenstore` 同样不入库；
3. 本文档已脱敏：不包含账号、活动 ID、名称、地点与时间；
4. 正式应用库 `storage/app.db`（`users` / `user_profiles` / `files` / `activities`）**未被本库修改**，仍等待 FIT 文件导入流程；
5. 该镜像库仅用于开发期评估与对拍，不改变 PROJECT_CONTEXT 3.1 的交付边界。
