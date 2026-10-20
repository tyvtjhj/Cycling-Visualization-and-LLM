# 数据分析（本地 FIT 样本）

> 定位：第二阶段「数据分析」的本地 FIT 样本目录。
> 隐私：本目录的 `*.fit` / `*.zip` 已由 `.gitignore` 排除，含个人 GPS、心率等数据，**不进入 git**。
> 当前样本：1 条 Garmin 原始 FIT（`garmin_download.py` 下载 zip 后解压得到，文件名按 activity_id 命名，此处不记录 ID）。

## 用途

- 存放第二阶段数据分析用的本地 FIT 样例；
- 分析脚本只读该文件，不改动原始数据：

```bash
uv run --no-project --python 3.12 --with fitdecode \
  python feature-tests/garmin_health/standalone/analyze_fit.py \
  --fit "feature-tests/数据分析/<activity_id>.fit" \
  --activity-id <activity_id>
```

## 重新获取

```bash
# 下载原始 zip 到 devdata/garmin/raw，解压后可移动到本目录
uv run --no-project --python 3.12 --with "garminconnect>=0.3.4" \
  python feature-tests/garmin_health/standalone/garmin_download.py --limit 1
```

## 说明

- 本目录仅保留本地样本；其余下载文件仍在 `devdata/garmin/raw/`；
- 数据库 `devdata/garmin/garmin_sync.db` 的 `fit_path` 已同步指向本目录；
- 如果不再需要样本，直接删除本目录下的 `.fit` 即可，不影响仓库。
