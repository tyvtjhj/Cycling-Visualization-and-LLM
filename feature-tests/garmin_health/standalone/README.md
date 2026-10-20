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

> 运行前必须先按第三节填写 `credentials.json`；未填写时 `check`/查询命令会直接报错并以退出码 2 结束。`--help` 不读取凭证。

## 三、测试账号文件（必需，不入库）

程序启动时读取**同目录下的 `credentials.json`**。该文件含个人隐私，已被同目录 `.gitignore` 排除，**不会进入 git 提交**。

### 3.1 文件位置

```
feature-tests/garmin_health/standalone/credentials.json
```

仓库里已创建好空模板，直接用编辑器填入账号即可。也可用 `--config 路径` 或环境变量 `GARMIN_CONFIG` 指定其他位置。

### 3.2 填写格式

```json
{
  "email": "你的 Garmin 登录账号",
  "password": "你的 Garmin 密码",
  "is_cn": true,
  "tokenstore": "~/.garminconnect"
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `email` | 是 | Garmin 登录账号（邮箱；国服账号若用手机号登录，也可填手机号）。也兼容字段名 `username` |
| `password` | 是 | Garmin 登录密码；仅本机保存，勿提交 |
| `is_cn` | 否 | `true`=中国区（默认），`false`=国际区 |
| `tokenstore` | 否 | 会话 token 保存路径，默认 `~/.garminconnect`；首次登录成功后自动复用 |

### 3.3 填写步骤

1. 用编辑器打开 `feature-tests/garmin_health/standalone/credentials.json`；
2. 把 `email`、`password` 换成你的测试账号；国服保持 `is_cn: true`，国际区改 `false`；
3. 保持 JSON 合法（字段用双引号、逗号分隔，末尾不要多逗号）；
4. **不要** `git add` / 提交该文件（已在 `.gitignore` 中，`git status` 不应出现它）；
5. 首次运行会写 tokenstore，之后可复用会话；删除 tokenstore 即退出登录。

### 3.4 缺少文件或字段的后果

- 文件不存在或 `email`/`password` 为空：程序会打印缺少账号文件的提示并**以退出码 2 结束**，不会发起登录；
- 密码错误：Garmin 认证失败，同样以退出码 2 结束；
- MFA 账号：交互终端会提示输入验证码，非交互环境会失败并给出提示；
- 首次登录可能先出现 mobile SSO `429` 警告（Garmin 风控，不一定只是 IP）；库会自动尝试其他登录策略，成功即可。若所有策略都 429，稍后重试或换网络；
- `tokenstore` 目录必须可写。若不可写（例如受限环境），登录仍可能成功但会话不会保存，后续命令会重新登录并更容易触发 429；此时把 `tokenstore` 指到可写目录（项目内可用 `tmp/garmin_tokenstore`）；
- 账号文件只用于本地测试：`vendor/`、`credentials.json` 等含隐私或第三方源码的内容都不会入库。

### 3.5 安全提醒

- `credentials.json` 含**明文密码**，只放在本机该目录；不要把内容粘贴到聊天、截图或提交记录里；
- 如文件被误拷贝到别处，请同样按隐私数据处理，必要时修改 Garmin 密码并撤销会话；
- 通过 Python 直接 `import garmin_core` 调用 `GarminClient()` 时，仍可用 `GARMIN_EMAIL` / `GARMIN_PASSWORD` / `GARMIN_IS_CN` / `GARMIN_TOKENSTORE` 环境变量兜底；命令行（CLI）固定读取上面的 JSON 文件。

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

全局参数：`--json`（机器可读）、`--config`（账号 JSON 路径）、`--region cn|global`、`--tokenstore`、`--retry`、`--retry-wait`。写在子命令**前或后**都可以，程序会自动归一化：

```bash
$CLI --config /path/to/credentials.json check
$CLI --json --region cn today
$CLI today --json          # 与上一行等价
```

### 4.1 下载原始 ZIP / FIT

```bash
# 默认下载账号最新 1 条活动；批量时用 --all-pending --limit N
uv run --no-project --python 3.12 --with "garminconnect>=0.3.4" \
  python feature-tests/garmin_health/standalone/garmin_download.py --limit 1
```

说明、参数与批量建议见 [DOWNLOAD_ORIGINAL.md](DOWNLOAD_ORIGINAL.md)。

### 4.2 FIT 数据分析（第二阶段）

```bash
uv run --no-project --python 3.12 --with fitdecode \
  python feature-tests/garmin_health/standalone/analyze_fit.py \
  --fit "feature-tests/数据分析/<activity_id>.fit" \
  --activity-id <activity_id>
```

输出 Markdown/JSON 到 `devdata/garmin/analysis/`（本地，含个人数据，不入库）。

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
2. **凭证安全**：账号密码只从本地 `credentials.json` 读取（已 gitignore），不写入其他文件；tokenstore 内有会话 token，注意本机权限。
3. **交付边界**：按 PROJECT_CONTEXT 3.1，本目录只用于开发期评估与对拍参考，**不进入交付系统**；正式系统仍只导入 FIT/GPX/TCX。
4. **版权边界**：`../vendor/` 的第三方源码无 LICENSE，不得复制进本目录或再分发；本目录是独立实现。
5. **写操作**：`rename` 会修改 Garmin 云端数据，默认拒绝执行，必须显式 `--yes`。

## 八、文件

| 文件 | 说明 |
|---|---|
| `garmin_core.py` | 核心库：登录/会话、健康数据、活动、统计、报告、可选改名；也可 `python garmin_core.py --check/--today` 最小自检 |
| `garmin_cli.py` | 命令行入口（子命令 + `--json`） |
| `garmin_download.py` | 通过 activity id 下载原始 ZIP、解压 FIT 并回写开发期工具库 |
| `analyze_fit.py` | 第二阶段数据分析：解析 FIT，输出数据质量、指标复算与对拍报告 |
| `DOWNLOAD_ORIGINAL.md` | 原始 ZIP 下载的测试结论、用法、批量建议与隐私说明 |
| `credentials.json` | **本地测试账号（不入库）**；运行前必须填写，格式见第三节 |
| `.gitignore` | 排除 `credentials.json`，防止隐私文件入库 |
| `README.md` | 本文件 |

纯函数（`fmt_duration`、`speed_to_pace`、`normalize_date_range`、`_rule_tips` 等）不依赖网络，可直接导入单测。
