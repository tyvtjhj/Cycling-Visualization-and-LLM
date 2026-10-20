#!/usr/bin/env python3
"""通过 Garmin activity id 下载原始文件 ZIP，解压 FIT 并回写开发期工具库。

功能：
- 默认下载账号最新 1 条活动（--limit 可调），也支持 --activity-id 指定、--all-pending 批量
- 调用 garminconnect 的 download_activity(ORIGINAL) 获取原始 ZIP
- 校验 ZIP / 解压 `.fit`，记录 zip/FIT 的大小与 sha256
- 回写 devdata/garmin/garmin_sync.db 的 raw_zip_* / fit_* 列与 download_status
- 串行下载、每条之间限速；已下载文件可跳过（--force 强制重下）

依赖：Python 3.12+、garminconnect>=0.3.4；账号来自同目录 credentials.json（不入库）。
运行（项目根目录）：
    uv run --no-project --python 3.12 --with "garminconnect>=0.3.4" \
        python feature-tests/garmin_health/standalone/garmin_download.py --limit 1
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]  # standalone -> garmin_health -> feature-tests -> repo root
sys.path.insert(0, str(HERE))

import garmin_core as core  # noqa: E402
from garminconnect import Garmin  # noqa: E402

DEFAULT_DB = ROOT / "devdata" / "garmin" / "garmin_sync.db"
DEFAULT_RAW_DIR = ROOT / "devdata" / "garmin" / "raw"
DEFAULT_TOKENSTORE = ROOT / "tmp" / "garmin_tokenstore"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def rel_path(path: Path) -> str:
    """尽量返回相对仓库根目录的路径；自定义目录在仓库外时返回绝对路径。"""
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def ensure_schema(conn: sqlite3.Connection) -> None:
    """确保工具库存在 v3 所需的下载索引列。"""
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='garmin_activity'"
        )
    }
    if not tables:
        raise SystemExit(
            "工具库缺少 garmin_activity 表：请先运行 devdata/garmin/sync_activities.py "
            "建立并同步活动列表。"
        )
    existing = {row[1] for row in conn.execute("PRAGMA table_info(garmin_activity)")}
    for column, sql_type in (
        ("raw_zip_path", "TEXT"),
        ("raw_zip_size", "INTEGER"),
        ("raw_zip_sha256", "TEXT"),
        ("fit_path", "TEXT"),
        ("fit_size", "INTEGER"),
        ("fit_sha256", "TEXT"),
    ):
        if column not in existing:
            conn.execute(f"ALTER TABLE garmin_activity ADD COLUMN {column} {sql_type}")
    conn.execute("PRAGMA user_version = 3")
    conn.commit()


def planned_ids(
    conn: Optional[sqlite3.Connection],
    client: core.GarminClient,
    args: argparse.Namespace,
) -> list[str]:
    if args.activity_id:
        return [str(aid) for aid in args.activity_id]
    if args.all_pending:
        if conn is None:
            raise SystemExit("--all-pending 需要可用的工具库")
        rows = conn.execute(
            "SELECT activity_id FROM garmin_activity "
            "WHERE download_status IN ('pending', 'failed') "
            "ORDER BY COALESCE(start_time_local, '') DESC LIMIT ?",
            (args.limit,),
        ).fetchall()
        return [str(row[0]) for row in rows]
    activities = client.call(client.client.get_activities, 0, max(1, args.limit), None)
    return [str(item["activityId"]) for item in activities if item.get("activityId")]


def extract_fit(data: bytes, zip_path: Path, fit_path: Path) -> tuple[Optional[Path], Optional[int]]:
    """写入 ZIP，并尽量解出第一个 .fit 文件；返回 (fit_path, fit_size)。"""
    if data[:2] == b"PK":
        zip_path.write_bytes(data)
        with zipfile.ZipFile(zip_path) as zf:
            bad = zf.testzip()
            if bad:
                raise ValueError(f"ZIP 校验失败：{bad}")
            fits = [name for name in zf.namelist() if name.lower().endswith(".fit")]
            if not fits:
                raise ValueError("ZIP 内没有 .fit 文件")
            fit_bytes = zf.read(fits[0])
    elif len(data) >= 12 and data[8:12] == b".FIT":
        fit_bytes = data
    else:
        raise ValueError(f"未知文件格式（magic={data[:8]!r}）")

    fit_path.write_bytes(fit_bytes)
    if len(fit_bytes) < 12 or fit_bytes[8:12] != b".FIT":
        raise ValueError("解压出的文件不是合法 FIT（缺少 .FIT 签名）")
    return fit_path, len(fit_bytes)


def download_one(
    client: core.GarminClient,
    conn: Optional[sqlite3.Connection],
    activity_id: str,
    raw_dir: Path,
    *,
    force: bool,
) -> dict[str, Any]:
    zip_path = raw_dir / f"{activity_id}.zip"
    fit_path = raw_dir / f"{activity_id}.fit"

    if not force and zip_path.exists() and fit_path.exists():
        return {
            "activity_id": activity_id,
            "status": "skipped",
            "raw_zip_path": rel_path(zip_path),
            "raw_zip_size": zip_path.stat().st_size,
            "fit_path": rel_path(fit_path),
            "fit_size": fit_path.stat().st_size,
        }

    data = client.call(
        client.client.download_activity,
        activity_id,
        dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL,
    )
    if not data:
        raise ValueError("下载结果为空")

    raw_dir.mkdir(parents=True, exist_ok=True)
    fit_result, fit_size = extract_fit(data, zip_path, fit_path)
    if fit_result is None:
        raise ValueError("未能解出 FIT 文件")

    result = {
        "activity_id": activity_id,
        "status": "downloaded",
        "raw_zip_path": rel_path(zip_path) if zip_path.exists() else None,
        "raw_zip_size": zip_path.stat().st_size if zip_path.exists() else None,
        "raw_zip_sha256": sha256_file(zip_path) if zip_path.exists() else None,
        "fit_path": rel_path(fit_path),
        "fit_size": fit_size,
        "fit_sha256": sha256_file(fit_path),
        "downloaded_at": datetime.now().isoformat(timespec="seconds"),
    }
    if conn is not None:
        cursor = conn.execute(
            "UPDATE garmin_activity SET raw_zip_path=?, raw_zip_size=?, raw_zip_sha256=?, "
            "fit_path=?, fit_size=?, fit_sha256=?, download_status='downloaded', "
            "download_error=NULL, downloaded_at=?, updated_at=datetime('now','localtime') "
            "WHERE activity_id=?",
            (
                result["raw_zip_path"],
                result["raw_zip_size"],
                result["raw_zip_sha256"],
                result["fit_path"],
                result["fit_size"],
                result["fit_sha256"],
                result["downloaded_at"],
                activity_id,
            ),
        )
        if cursor.rowcount == 0:
            # 活动不在工具库中时补一条最小记录，保证文件索引可追溯
            conn.execute(
                "INSERT INTO garmin_activity (activity_id, raw_zip_path, raw_zip_size, "
                "raw_zip_sha256, fit_path, fit_size, fit_sha256, download_status, "
                "downloaded_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'downloaded', ?)",
                (
                    activity_id,
                    result["raw_zip_path"],
                    result["raw_zip_size"],
                    result["raw_zip_sha256"],
                    result["fit_path"],
                    result["fit_size"],
                    result["fit_sha256"],
                    result["downloaded_at"],
                ),
            )
        conn.commit()
    return result


def mark_failed(conn: Optional[sqlite3.Connection], activity_id: str, message: str) -> None:
    if conn is None:
        return
    conn.execute(
        "UPDATE garmin_activity SET download_status='failed', download_error=?, "
        "updated_at=datetime('now','localtime') WHERE activity_id=?",
        (message[:500], activity_id),
    )
    conn.commit()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="通过 activity id 下载 Garmin 原始 ZIP 并解压 FIT")
    parser.add_argument("--activity-id", action="append", default=None, help="指定活动 ID，可重复")
    parser.add_argument("--limit", type=int, default=1, help="最多下载条数，默认 1")
    parser.add_argument("--all-pending", action="store_true", help="从工具库取 pending/failed 的活动")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"工具库路径，默认 {DEFAULT_DB}")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR, help=f"文件目录，默认 {DEFAULT_RAW_DIR}")
    parser.add_argument("--delay", type=float, default=1.5, help="每条之间等待秒数，默认 1.5")
    parser.add_argument("--force", action="store_true", help="已存在文件也重新下载")
    parser.add_argument("--dry-run", action="store_true", help="只列出计划，不下载")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    core.configure_stdio()
    args = build_parser().parse_args(argv)
    args.limit = max(1, args.limit)

    cfg = core.load_credentials()
    client = core.GarminClient(
        email=cfg["email"],
        password=cfg["password"],
        is_cn=cfg["is_cn"],
        tokenstore=str(DEFAULT_TOKENSTORE),
    )

    conn: Optional[sqlite3.Connection] = None
    if args.db.exists():
        conn = sqlite3.connect(args.db)
        ensure_schema(conn)
    elif args.all_pending:
        raise SystemExit(f"工具库不存在：{args.db}（先运行 sync_activities.py）")

    ids = planned_ids(conn, client, args)
    if not ids:
        print("没有符合条件的活动")
        return 1
    print(f"计划下载 {len(ids)} 条：{ids}")
    if args.dry_run:
        return 0

    ok = failed = skipped = 0
    for index, activity_id in enumerate(ids, 1):
        try:
            result = download_one(client, conn, activity_id, args.raw_dir, force=args.force)
            if result["status"] == "skipped":
                skipped += 1
            else:
                ok += 1
            print(
                f"[{index}/{len(ids)}] {activity_id}: {result['status']} "
                f"zip={result.get('raw_zip_size')} fit={result.get('fit_size')}"
            )
        except Exception as exc:  # noqa: BLE001
            failed += 1
            mark_failed(conn, activity_id, f"{type(exc).__name__}: {exc}")
            print(f"[{index}/{len(ids)}] {activity_id}: FAILED {type(exc).__name__}: {exc}", file=sys.stderr)
        if index < len(ids):
            time.sleep(max(0.0, args.delay))

    if conn is not None:
        conn.close()
    print(f"完成：downloaded={ok} skipped={skipped} failed={failed}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
