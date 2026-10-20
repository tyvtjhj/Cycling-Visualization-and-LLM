# 通过 activity id 下载原始 ZIP：测试结论与使用说明

> 日期：2026-10-20
> 范围：Garmin 活动原始文件下载能力验证 + 开发期下载脚本
> 定位：开发期取数工具，不是 PROJECT_CONTEXT 6.6 的正式交付功能；明确测试对象与个人数据已做脱敏。
> 前置：同目录 `credentials.json`（不入库），Python 3.12+ 与 `garminconnect>=0.3.4`。

## 一、结论

**可行。** 用账号登录后，通过 `activity id` 调用 `garminconnect` 的
`download_activity(activity_id, dl_fmt=ActivityDownloadFormat.ORIGINAL)`，可以直接拿到 Garmin 原始文件 ZIP；
ZIP 内包含可解析的 FIT 文件，且可以回写到开发期工具库的文件索引列。

本次只下载账号内最新 1 条活动做验证（ID、活动名、地点与时间不写入本文件）。

## 二、测试方法

```text
garminconnect.Garmin(...).download_activity(activity_id, ActivityDownloadFormat.ORIGINAL)
        ↓
原始 ZIP（PK 开头）
        ↓
解压得到 *.fit（.FIT 签名校验）
        ↓
fitdecode 解析验证
```

底层实际请求路径（账号为国际区时）：

```text
https://connectapi.garmin.com/download-service/files/activity/{activity_id}
```

若账号为中国区（`is_cn=true`），库会使用 `connectapi.garmin.cn` 同一路径。

## 三、测试结果

| 项目 | 结果 |
|---|---|
| 下载方式 | `download_activity(ORIGINAL)`，无需手拼网页 URL |
| ZIP 完整性 | `zipfile.testzip()` 通过 |
| ZIP 内容 | 1 个 FIT 文件 |
| ZIP 体积 | 393,789 字节（约 0.4 MB，随活动变化） |
| FIT 体积 | 813,421 字节（约 0.8 MB，随活动变化） |
| FIT 签名 | `.FIT` 头校验通过 |
| FIT 解析（fitdecode） | `record` 帧 11,478 条；`lap` 3 条；`session` 1 条；`hrv` 11,968 条 |
| 结论 | 下载文件完整、可解析，可直接进入 FIT 处理流程 |

## 四、关于网页端下载链接

浏览器里抓到的网页端地址形如：

```text
https://connectus.garmin.cn/gc-api/download-service/files/activity/{activity_id}
```

用当前账号 / 库直接访问该地址会失败（`Invalid API host`）。原因：

- 该地址是**网页端网关**，需要网页登录态，不是 API 客户端入口；
- `connectus.garmin.cn` 带有区域前缀，与库当前配置的区域域名（本账号为国际区 `.com`）不一致。

因此**不需要抓网页链接**，使用库方法即可；它走的是移动端同款 API 入口，返回的也是同一个原始 ZIP。

## 五、数据库写入

工具库 `devdata/garmin/garmin_sync.db` 升级到 **v3**，新增列：

| 列 | 说明 |
|---|---|
| `raw_zip_path` | 原始 ZIP 相对路径 |
| `raw_zip_size` | ZIP 字节数 |
| `raw_zip_sha256` | ZIP 内容哈希 |
| `fit_path` | 解压后的 FIT 相对路径 |
| `fit_size` | FIT 字节数 |
| `fit_sha256` | FIT 内容哈希 |

同时把对应行的 `download_status` 置为 `downloaded`，写入 `downloaded_at`；批量下载时其余活动保持 `pending`。

完整的 v1 → v3 结构变更、56 列清单、709 条同步校验结果见 [../SYNC_DB.md](../SYNC_DB.md)。

## 六、脚本用法

脚本：[garmin_download.py](garmin_download.py)（独立版，无 AstrBot 依赖）

```bash
# 默认：下载账号最新 1 条
uv run --no-project --python 3.12 --with "garminconnect>=0.3.4" \
  python feature-tests/garmin_health/standalone/garmin_download.py --limit 1

# 指定活动（可重复 --activity-id）
... garmin_download.py --activity-id <activity_id>

# 从工具库取 pending/failed 的活动批量下载，先跑 10 条
... garmin_download.py --all-pending --limit 10 --delay 2

# 只观察计划，不下载
... garmin_download.py --limit 5 --dry-run
```

参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--limit` | 1 | 最多下载条数 |
| `--activity-id` | — | 指定活动 ID，可重复 |
| `--all-pending` | 关 | 从工具库取 `pending`/`failed` 的活动 |
| `--db` | `devdata/garmin/garmin_sync.db` | 工具库路径 |
| `--raw-dir` | `devdata/garmin/raw` | ZIP/FIT 保存目录 |
| `--delay` | 1.5 | 每条之间等待秒数 |
| `--force` | 关 | 已存在文件也重新下载 |
| `--dry-run` | 关 | 只列出计划 |

脚本行为：串行下载；ZIP 完整性与 FIT 签名校验；文件已存在且不是 `--force` 时跳过；失败写入
`download_status='failed'` 与错误原因；`--all-pending` 可断点续跑。

## 七、批量下载建议

- 先 `--all-pending --limit 10` 验证稳定性，再逐步放量；
- 单条 ZIP 约 0.4 MB、FIT 约 0.8 MB（因活动而异），全量 700+ 条需要预留数百 MB 到 1 GB；
- 保持串行与 `--delay`（1~2 秒），遇到 429 由库退避重试；
- 下载完成后按 `download_status` 统计失败项并重试即可。

## 八、隐私与安全

1. 原始 ZIP / FIT 含个人活动轨迹、心率等数据，只保存在 `devdata/garmin/raw/`（已被 `.gitignore` 排除），**不进入 git**；
2. 账号文件 `credentials.json` 与 `tmp/garmin_tokenstore` 同样不入库；
3. 本文件已脱敏：不记录活动 ID、活动名称、地点、精确时间、账号邮箱或任何凭证；
4. 测试结束可删除 `tmp/garmin_tokenstore` 退出会话；原始文件按需保留或清理；
5. 该能力仅用于开发期获取数据/对拍，交付系统仍按 PROJECT_CONTEXT 3.1 只导入平台导出文件。
