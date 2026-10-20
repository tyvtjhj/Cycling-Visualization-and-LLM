#!/usr/bin/env python3
"""FIT 预处理脚本骨架（当前不做任何预处理，只展示 FIT 原始内容）。

本目录约定：`feature-tests/数据分析/` 内的所有分析/预处理操作只读 FIT 样本，
不写回原文件；需要落盘的中间产物放本目录 `output/`（已 gitignore）。

用法（项目根目录）：
    uv run --no-project --python 3.12 --with fitdecode \
      python feature-tests/数据分析/preprocess.py            # 自动找本目录第一个 .fit
    ... preprocess.py --fit "feature-tests/数据分析/<id>.fit" --show all
    ... preprocess.py --show fields      # 每种消息有哪些字段、非空数量、样例值
    ... preprocess.py --show records --limit 5
    ... preprocess.py --json             # 结构化摘要

说明：当前版本只做“查看/盘点”，预处理逻辑（清洗、异常值、重采样等）后续在本文件补充。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

try:
    import fitdecode  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "缺少依赖 fitdecode。运行方式：\n"
        "uv run --no-project --python 3.12 --with fitdecode python feature-tests/数据分析/preprocess.py"
    ) from exc

HERE = Path(__file__).resolve().parent
OUTPUT_DIR = HERE / "output"


def _first_fit_in_folder() -> Optional[Path]:
    fits = sorted(HERE.glob("*.fit"))
    return fits[0] if fits else None


def _readable(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= 120 else text[:117] + "..."


def collect_frames(path: Path) -> dict[str, Any]:
    """遍历 FIT 全部 DATA 帧，统计消息类型、字段与样例。"""
    message_counts: Counter[str] = Counter()
    message_fields: dict[str, dict[str, dict[str, Any]]] = defaultdict(
        lambda: defaultdict(lambda: {"non_null": 0, "total": 0, "sample": None})
    )
    first_frame: dict[str, dict[str, Any]] = {}
    laps: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    total_frames = 0

    with fitdecode.FitReader(str(path)) as fit:
        for frame in fit:
            if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                continue
            total_frames += 1
            name = frame.name
            message_counts[name] += 1
            values = {field.name: field.value for field in frame.fields}
            for field_name, value in values.items():
                info = message_fields[name][field_name]
                info["total"] += 1
                if value is not None:
                    info["non_null"] += 1
                    if info["sample"] is None:
                        info["sample"] = value
            if name not in first_frame:
                first_frame[name] = values
            if name == "lap":
                laps.append(values)
            elif name == "record":
                records.append(values)

    return {
        "total_frames": total_frames,
        "message_counts": dict(sorted(message_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "message_fields": {
            msg: {field: dict(info) for field, info in fields.items()}
            for msg, fields in message_fields.items()
        },
        "first_frame": first_frame,
        "laps": laps,
        "records": records,
    }


def summarize(path: Path, data: dict[str, Any]) -> dict[str, Any]:
    records = data["records"]
    record_fields = list(data["message_fields"].get("record", {}))
    return {
        "file": {
            "name": path.name,
            "size_bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        },
        "frames": {
            "data_frames": data["total_frames"],
            "message_types": len(data["message_counts"]),
        },
        "message_counts": data["message_counts"],
        "file_id": data["first_frame"].get("file_id"),
        "sport": data["first_frame"].get("sport"),
        "session": data["first_frame"].get("session"),
        "lap_count": len(data["laps"]),
        "record_count": len(records),
        "record_fields": record_fields,
        "record_first": records[0] if records else None,
        "record_last": records[-1] if records else None,
    }


def print_summary(path: Path, data: dict[str, Any], summary: dict[str, Any]) -> None:
    print("=" * 72)
    print("FIT 文件内容概览（只读盘点，不做任何预处理）")
    print("=" * 72)
    print(f"文件      : {path}")
    print(f"大小      : {summary['file']['size_bytes']} 字节")
    print(f"SHA256    : {summary['file']['sha256']}")
    print(f"DATA 帧数 : {summary['frames']['data_frames']}")
    print(f"消息类型  : {summary['frames']['message_types']}")

    print("\n—— 消息类型统计 ——")
    for name, count in data["message_counts"].items():
        print(f"  {name:<28} {count}")

    print("\n—— 关键信息 ——")
    file_id = data["first_frame"].get("file_id") or {}
    sport = data["first_frame"].get("sport") or {}
    session = data["first_frame"].get("session") or {}
    print(f"file_id.type      : {file_id.get('type')}")
    print(f"file_id.manufacturer: {file_id.get('manufacturer')}")
    print(f"file_id.product   : {file_id.get('product')}")
    print(f"file_id.serial_number: {file_id.get('serial_number')}")
    print(f"sport / sub_sport : {sport.get('sport')} / {sport.get('sub_sport')}")
    print(f"session 字段数    : {len(session)}")
    for key in (
        "start_time",
        "total_timer_time",
        "total_elapsed_time",
        "total_distance",
        "total_ascent",
        "total_descent",
        "avg_power",
        "max_power",
        "normalized_power",
        "intensity_factor",
        "training_stress_score",
        "avg_heart_rate",
        "max_heart_rate",
        "avg_cadence",
        "max_cadence",
        "total_calories",
        "threshold_power",
    ):
        if key in session:
            print(f"  {key:<24} {session[key]!r}")
    print(f"lap 数量          : {len(data['laps'])}")
    print(f"record 数量       : {len(data['records'])}")
    print(f"record 字段       : {', '.join(summary['record_fields'])}")
    if data["records"]:
        print(f"首条 record       : {_readable(data['records'][0])}")
        print(f"末条 record       : {_readable(data['records'][-1])}")


def print_fields(data: dict[str, Any]) -> None:
    print("\n—— 各消息类型字段清单（非空/总数，样例值）——")
    for message, fields in sorted(data["message_fields"].items()):
        print(f"\n[{message}] 帧数={data['message_counts'].get(message)}")
        for field_name, info in fields.items():
            print(
                f"  {field_name:<32} {info['non_null']}/{info['total']}  "
                f"{_readable(info['sample'])}"
            )


def print_records(records: list[dict[str, Any]], limit: int) -> None:
    print(f"\n—— record 明细（前 {min(limit, len(records))} 条 / 共 {len(records)} 条）——")
    for index, record in enumerate(records[:limit], 1):
        print(f"  #{index}: {json.dumps(record, ensure_ascii=False, default=str)}")


def print_session(data: dict[str, Any]) -> None:
    print("\n—— file_id 全字段 ——")
    for key, value in (data["first_frame"].get("file_id") or {}).items():
        print(f"  {key:<32} {value!r}")
    print("\n—— session 全字段 ——")
    for key, value in (data["first_frame"].get("session") or {}).items():
        print(f"  {key:<32} {value!r}")
    print("\n—— lap 全字段（逐圈）——")
    for index, lap in enumerate(data["laps"], 1):
        print(f"  Lap {index}:")
        for key, value in lap.items():
            print(f"    {key:<30} {value!r}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FIT 原始内容盘点（当前不做预处理）")
    parser.add_argument("--fit", type=Path, default=None, help="FIT 文件；默认本目录第一个 .fit")
    parser.add_argument(
        "--show",
        choices=("summary", "messages", "fields", "records", "session", "all"),
        default="summary",
        help="展示内容：summary/messages/fields/records/session/all",
    )
    parser.add_argument("--limit", type=int, default=5, help="records 模式展示条数")
    parser.add_argument("--json", action="store_true", help="输出结构化摘要 JSON")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass

    args = build_parser().parse_args(argv)
    fit_path = args.fit or _first_fit_in_folder()
    if fit_path is None:
        print("未找到 FIT 文件：请用 --fit 指定，或把 .fit 放到本目录", file=sys.stderr)
        return 1
    if not fit_path.exists():
        print(f"FIT 文件不存在：{fit_path}", file=sys.stderr)
        return 1

    data = collect_frames(fit_path)
    summary = summarize(fit_path, data)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        return 0

    if args.show in ("summary", "all"):
        print_summary(fit_path, data, summary)
    if args.show in ("messages", "all"):
        print("\n—— 消息类型统计 ——")
        for name, count in data["message_counts"].items():
            print(f"  {name:<28} {count}")
    if args.show in ("fields", "all"):
        print_fields(data)
    if args.show in ("records", "all"):
        print_records(data["records"], max(0, args.limit))
    if args.show == "session":
        print_session(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
