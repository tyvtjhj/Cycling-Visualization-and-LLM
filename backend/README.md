# Backend

Python 3.12 + uv 管理环境。

## 环境

```bash
uv sync
uv run python --version
uv run pytest
```

## 数据库

- 正式应用库：`../storage/app.db`（不入仓库，可重建）
- 建表脚本：`src/data_access/storage/schema.sql`
- 访问必须经 ORM 抽象（D4），切换数据库靠连接串

## 依赖增加原则

按批次增加，不提前锁定未确认选型：批次 1 加 FIT 解析库，批次 6 加 FastAPI/uvicorn。
