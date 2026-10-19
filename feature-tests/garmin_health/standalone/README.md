# Garmin 独立运行版（standalone）

> 目的：把「佳明健康看板」插件里的核心能力，独立实现成**不依赖 AstrBot / Agent / LLM** 的可执行 Python 脚本。
> 说明：本目录代码为按功能需求独立实现，未复制 `../vendor/` 中的第三方源码，只参考其功能范围与 Garmin 接口用法。

## 一、提供什么

| 能力 | 说明 |
|---|---|
| 登录 / 会话 | `garminconnect` + tokenstore；国服/国际区；MFA 交互提示；429/5xx 指数退避 |
| 健康数据 | 今日概览、近 N 天心率 / 睡眠 / 步数 |
| 活动 | 活动列表；父类型、子类型、日期范围、名称关键词筛选 |
| 统计 | 运动量（距离/时长/爬升/卡路里/均速/均心率）、个人最佳、年度报告 |
| 报告 | 近 N 天均值 + 规则建议（**不调用任何 LLM**） |
| 可选写操作 | 活动改名（必须显式 `--yes`） |

## 二、环境要求

- Python **3.12+**（当前 `garminconnect` 要求）
- `garminconnect>=0.3.4`（非官方 Garmin Connect Web 客户端）
- **不需要** AstrBot；也不需要装进 `backend/` 的 uv 环境（避免污染交付系统依赖，见 PROJECT_CONTEXT 3.1）

推荐用 uv 临时环境运行，用完即弃：

```bash
cd <repo-root>
uv run --no-project --python 3.12 --with "garminconnect>=0.3.4" \
    python feature-tests/garmin_health/standalone/garmin_cli.py --help
```

## 三、账号与区域（环境变量）

| 变量 | 必填 | 说明 |
|---|---|---|
| `GARMIN_EMAIL` | 是（首次） | Garmin 账号邮箱 |
| `GARMIN_PASSWORD` | 是（首次） | Garmin 账号密码；不要写入文件或提交仓库 |
| `GARMIN_IS_CN` | 否 | `1`=中国区（默认），`0`=国际区 |
| `GARMIN_TOKENSTORE` | 否 | tokenstore 路径，默认 `~/.garminconnect`；登录成功后自动复用 |

PowerShell 示例：

```powershell
$env:GARMIN_EMAIL = "you@example.com"
$env:GARMIN_PASSWORD = "******"
$env:GARMIN_IS_CN = "1"
```

- 首次登录会向 tokenstore 写会话文件；之后可不再提供密码（删除 tokenstore 即退出登录）。
- 开启 MFA 的账号：交互终端会提示输入验证码；非交互环境会失败并给出提示。
- 账号密码只用于首次换取 token，之后请求使用 token；仍请只在个人机器上使用。

## 四、命令

| 命令 | 说明 |
|---|---|
| `check` | 验证登录并打印账号/区域/tokenstore |
| `today [--date YYYY-MM-DD]` | 今日健康概览（步数/距离/卡路里/心率/睡眠） |
| `hr --days 7` | 近 N 天心率 |
| `sleep --days 7` | 近 N 天睡眠（时长/评分/深睡/浅睡/REM/起止） |
| `steps --days 7` | 近 N 天步数/距离/卡路里 |
| `activities [--type] [--start] [--end] [--keyword] [--limit]` | 活动列表与筛选 |
| `volume --type cycling [--start] [--end]` | 运动量统计 |
| `pb [--type cycling]` | 个人最佳 |
| `yearly [--year 2026]` | 年度报告 |
| `report [--days 7]` | 综合报告 + 规则建议 |
| `rename (--activity-id ID \| --keyword 名称) --name 新名 --yes` | 改名（写操作） |

示例：

```bash
CLI="uv run --no-project --python 3.12 --with garminconnect>=0.3.4 -- python feature-tests/garmin_health/standalone/garmin_cli.py"

$CLI check
$CLI today
$CLI hr --days 7
$CLI sleep --days 7
$CLI activities --type cycling --limit 5
$CLI activities --type road_biking --start 2026-09-01
$CLI volume --type cycling --start 2026
$CLI pb --type cycling
$CLI yearly --year 2026
$CLI report --days 7
```

`--type` 支持父类型（`running` / `cycling` / `hiking` / `walking` / `swimming` / `multi_sport` / `fitness_equipment` / `other`）和常见子类型（如 `road_biking`、`mountain_biking`、`gravel_cycling`、`trail_running`、`lap_swimming`）。父类型交给 Garmin API 过滤，子类型在本地按 `typeKey` 精确匹配。

全局参数：`--json`（机器可读）、`--region cn|global`、`--tokenstore`、`--retry`、`--retry-wait`。

## 五、JSON 输出

加 `--json` 输出结构化数据，便于接到看板或对拍脚本，例如：

```bash
$CLI today --json
# {"date": "2026-10-19", "steps": 8123, "distance_km": 5.67,
#  "calories_active": 320, "heart_rate": {"avg": 68, "max": 142, "min": 47},
#  "sleep": {"hours": 7.2, "score": 82, "deep_min": 78, "light_min": 310,
#            "rem_min": 95, "start": "23:10", "end": "06:30"}}
```

## 六、数据口径与限制

- `today` 的距离取 Garmin `totalDistance`，缺失时为 `null`，不做事后估算。
- 睡眠起止时间按 Garmin `*TimestampLocal` 的语义用 UTC 渲染，得到本地钟点。
- 活动列表分页拉取，`--scan-limit` 限制最多扫描条数（默认 2000；`volume`/`pb`/`yearly` 默认 3000），活动很多时可能不是全量。
- 无本地缓存：每次执行都按需请求 Garmin；`report` 约 1 + 3×N 次调用，注意频率。
- 内置 429/5xx 退避（`--retry`、`--retry-wait`），但无法规避 Garmin 风控或改版。

## 七、风险与边界

1. **非官方接口**：`garminconnect` 是社区实现；Garmin 改版、风控、限流都可能导致失败。
2. **凭证安全**：账号密码只从环境变量读取，不写入任何文件；tokenstore 内有会话 token，注意本机权限。
3. **交付边界**：按 PROJECT_CONTEXT 3.1，本目录只用于开发期评估与对拍参考，**不进入交付系统**；正式系统仍只导入 FIT/GPX/TCX。
4. **版权边界**：`../vendor/` 的第三方源码无 LICENSE，不得复制进本目录或再分发；本目录是独立实现。
5. **写操作**：`rename` 会修改 Garmin 云端数据，默认拒绝执行，必须显式 `--yes`。

## 八、文件

| 文件 | 说明 |
|---|---|
| `garmin_core.py` | 核心库：登录/会话、健康数据、活动、统计、报告、可选改名；也可 `python garmin_core.py --check/--today` 最小自检 |
| `garmin_cli.py` | 命令行入口（子命令 + `--json`） |
| `README.md` | 本文件 |

纯函数（`fmt_duration`、`speed_to_pace`、`normalize_date_range`、`_rule_tips` 等）不依赖网络，可直接导入单测。
