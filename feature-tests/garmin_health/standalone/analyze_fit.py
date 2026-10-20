#!/usr/bin/env python3
"""第二阶段·数据分析：解析 Garmin FIT 原始文件，输出数据质量与指标分析报告。

输入：download_activity(ORIGINAL) 下载 zip 后解压出的 .fit 文件
输出：Markdown + JSON 分析报告（默认写到 devdata/garmin/analysis/，本地不入库）

用法（项目根目录）：
    uv run --no-project --python 3.12 --with fitdecode \
      python feature-tests/garmin_health/standalone/analyze_fit.py \
      --fit devdata/garmin/raw/<activity_id>.fit \
      --activity-id <activity_id>

本脚本只做只读分析：不修改 FIT 文件、不改数据库、不上传任何数据。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    import fitdecode  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "缺少依赖 fitdecode。运行方式：\n"
        "uv run --no-project --python 3.12 --with fitdecode python <本脚本>"
    ) from exc

ROOT = Path(__file__).resolve().parents[3]  # standalone -> garmin_health -> feature-tests -> repo
DEFAULT_DB = ROOT / "devdata" / "garmin" / "garmin_sync.db"
DEFAULT_OUT = ROOT / "devdata" / "garmin" / "analysis"

POWER_SPIKE_W = 2500  # PROJECT_CONTEXT D14 功率硬上限
HR_ZERO_RUN_S = 60  # 连续零值达 1 分钟判为心率掉线


@dataclass
class FitData:
    file_id: dict[str, Any] = field(default_factory=dict)
    sport: dict[str, Any] = field(default_factory=dict)
    session: dict[str, Any] = field(default_factory=dict)
    laps: list[dict[str, Any]] = field(default_factory=list)
    records: list[dict[str, Any]] = field(default_factory=list)


def _get(frame: Any, name: str) -> Any:
    try:
        return frame.get_value(name, fallback=None)
    except Exception:  # noqa: BLE001
        for f in frame.fields:
            if f.name == name:
                return f.value
    return None


def _session_dict(frame: Any) -> dict[str, Any]:
    return {f.name: f.value for f in frame.fields}


def parse_fit(path: Path) -> FitData:
    data = FitData()
    with fitdecode.FitReader(str(path)) as fit:
        for frame in fit:
            if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                continue
            if frame.name == "file_id" and not data.file_id:
                data.file_id = _session_dict(frame)
            elif frame.name == "sport" and not data.sport:
                data.sport = _session_dict(frame)
            elif frame.name == "session" and not data.session:
                data.session = _session_dict(frame)
            elif frame.name == "lap":
                data.laps.append(_session_dict(frame))
            elif frame.name == "record":
                item = {
                    "timestamp": _get(frame, "timestamp"),
                    "distance": _get(frame, "distance"),
                    "speed": _get(frame, "enhanced_speed") or _get(frame, "speed"),
                    "altitude": _get(frame, "enhanced_altitude")
                    if _get(frame, "enhanced_altitude") is not None
                    else _get(frame, "altitude"),
                    "power": _get(frame, "power"),
                    "heart_rate": _get(frame, "heart_rate"),
                    "cadence": _get(frame, "cadence"),
                    "temperature": _get(frame, "temperature"),
                }
                data.records.append(item)
    return data


def _mean(values: list[float]) -> Optional[float]:
    return round(statistics.fmean(values), 3) if values else None


def _diff(computed: Any, reference: Any) -> dict[str, Any]:
    if computed is None or reference is None:
        return {"computed": computed, "reference": reference, "abs": None, "rel_pct": None}
    try:
        c, r = float(computed), float(reference)
    except (TypeError, ValueError):
        return {"computed": computed, "reference": reference, "abs": None, "rel_pct": None}
    absolute = c - r
    relative = (absolute / r * 100) if r else None
    return {
        "computed": round(c, 3),
        "reference": round(r, 3),
        "abs": round(absolute, 3),
        "rel_pct": round(relative, 2) if relative is not None else None,
    }


def compute_metrics(data: FitData) -> dict[str, Any]:
    records = data.records
    session = data.session

    powers = [float(r["power"]) for r in records if r["power"] is not None]
    hrs = [float(r["heart_rate"]) for r in records if r["heart_rate"] is not None]
    cadences = [float(r["cadence"]) for r in records if r["cadence"] is not None]
    speeds = [float(r["speed"]) for r in records if r["speed"] is not None]
    temps = [float(r["temperature"]) for r in records if r["temperature"] is not None]

    duration = session.get("total_timer_time") or session.get("total_elapsed_time")
    distance = session.get("total_distance")
    if distance is None and records:
        distance = records[-1]["distance"]

    # 30 秒滚动窗口（向后看完整窗口），按 1Hz 记录近似
    np_value = None
    max_20min = None
    if powers:
        window = 30
        rolling = [
            statistics.fmean(powers[i - window + 1 : i + 1])
            for i in range(window - 1, len(powers))
        ]
        if rolling:
            np_value = round((sum(v ** 4 for v in rolling) / len(rolling)) ** 0.25, 1)
        w1200 = 1200
        if len(powers) >= w1200:
            max_20min = round(
                max(
                    statistics.fmean(powers[i - w1200 + 1 : i + 1])
                    for i in range(w1200 - 1, len(powers))
                ),
                1,
            )

    avg_power = _mean(powers)
    ftp = session.get("threshold_power")
    intensity_factor = (
        round(np_value / ftp, 3) if np_value is not None and ftp else None
    )
    tss = (
        round(duration * np_value * intensity_factor / (ftp * 3600) * 100, 1)
        if duration and np_value and intensity_factor and ftp
        else None
    )
    variability = (
        round(np_value / avg_power, 3)
        if np_value is not None and avg_power
        else None
    )

    # 采样间隔与数据质量
    timestamps = [r["timestamp"] for r in records if isinstance(r["timestamp"], datetime)]
    intervals = [
        (timestamps[i] - timestamps[i - 1]).total_seconds()
        for i in range(1, len(timestamps))
    ]
    intervals = [i for i in intervals if i > 0]
    zero_power = sum(1 for p in powers if p == 0)
    zero_hr = sum(1 for h in hrs if h == 0)
    cadences_nonzero = [c for c in cadences if c > 0]
    longest_zero_hr_run = 0
    run = 0
    for h in hrs:
        run = run + 1 if h == 0 else 0
        longest_zero_hr_run = max(longest_zero_hr_run, run)

    ascent = 0.0
    altitudes = [r["altitude"] for r in records if r["altitude"] is not None]
    for i in range(1, len(altitudes)):
        delta = float(altitudes[i]) - float(altitudes[i - 1])
        if delta > 0:
            ascent += delta

    return {
        "record_count": len(records),
        "duration_s": _diff(duration, session.get("total_timer_time")),
        "elapsed_s": session.get("total_elapsed_time"),
        "distance_m": _diff(distance, session.get("total_distance")),
        "ascent_m": _diff(round(ascent, 1), session.get("total_ascent")),
        "avg_power_w": avg_power,
        "max_power_w": max(powers) if powers else None,
        "np_w": np_value,
        "if_value": intensity_factor,
        "tss": tss,
        "vi": variability,
        "max_20min_power_w": max_20min,
        "avg_hr": _mean(hrs),
        "max_hr": max(hrs) if hrs else None,
        "avg_cadence": _mean(cadences),
        "avg_cadence_nonzero": _mean(cadences_nonzero),
        "max_cadence": max(cadences) if cadences else None,
        "avg_speed_mps": _mean(speeds),
        "max_speed_mps": max(speeds) if speeds else None,
        "avg_temperature_c": _mean(temps),
        "ftp_w": ftp,
        "gain_m": session.get("total_ascent"),
        "loss_m": session.get("total_descent"),
        "calories": session.get("total_calories"),
        "data_quality": {
            "power_missing": len(records) - len(powers),
            "hr_missing": len(records) - len(hrs),
            "cadence_missing": len(records) - len(cadences),
            "zero_power_records": zero_power,
            "zero_power_pct": round(zero_power / len(powers) * 100, 2) if powers else None,
            "zero_hr_records": zero_hr,
            "cadence_zero_records": len(cadences) - len(cadences_nonzero),
            "longest_zero_hr_run_s": longest_zero_hr_run,
            "power_spikes_over_2500w": sum(1 for p in powers if p > POWER_SPIKE_W),
            "sample_interval_median_s": round(statistics.median(intervals), 3)
            if intervals
            else None,
            "sample_interval_min_s": round(min(intervals), 3) if intervals else None,
            "sample_interval_max_s": round(max(intervals), 3) if intervals else None,
            "gaps_over_1s": sum(1 for value in intervals if value > 1.5),
            "first_timestamp": timestamps[0].isoformat() if timestamps else None,
            "last_timestamp": timestamps[-1].isoformat() if timestamps else None,
        },
    }


def compare_with_garmin(metrics: dict[str, Any], session: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        ("平均功率 (W)", metrics["avg_power_w"], session.get("avg_power")),
        ("最大功率 (W)", metrics["max_power_w"], session.get("max_power")),
        ("标准化功率 NP (W)", metrics["np_w"], session.get("normalized_power")),
        ("强度因子 IF", metrics["if_value"], session.get("intensity_factor")),
        ("训练压力 TSS", metrics["tss"], session.get("training_stress_score")),
        ("总距离 (m)", metrics["distance_m"]["computed"], session.get("total_distance")),
        ("计时时长 (s)", metrics["duration_s"]["computed"], session.get("total_timer_time")),
        ("爬升 (m)", metrics["ascent_m"]["computed"], session.get("total_ascent")),
        ("平均心率 (bpm)", metrics["avg_hr"], session.get("avg_heart_rate")),
        ("最大心率 (bpm)", metrics["max_hr"], session.get("max_heart_rate")),
        ("平均踏频 (rpm)", metrics["avg_cadence"], session.get("avg_cadence")),
        ("平均踏频（非零）(rpm)", metrics["avg_cadence_nonzero"], session.get("avg_cadence")),
        ("最大踏频 (rpm)", metrics["max_cadence"], session.get("max_cadence")),
        ("卡路里", metrics["calories"], session.get("total_calories")),
    ]
    return [
        {"name": name, **_diff(computed, reference)}
        for name, computed, reference in rows
    ]


def db_reconcile(
    activity_id: Optional[str],
    db_path: Path,
    session: dict[str, Any],
    metrics: dict[str, Any],
) -> list[dict[str, Any]]:
    if not activity_id or not db_path.exists():
        return []
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM garmin_activity WHERE activity_id = ?", (activity_id,)
    ).fetchone()
    con.close()
    if row is None:
        return []

    pairs = [
        ("distance_m", row["distance_m"], session.get("total_distance")),
        ("duration_s", row["duration_s"], session.get("total_timer_time")),
        ("elapsed_s", row["elapsed_s"], session.get("total_elapsed_time")),
        ("elevation_gain_m", row["elevation_gain_m"], session.get("total_ascent")),
        ("avg_power_w", row["avg_power_w"], session.get("avg_power")),
        ("max_power_w", row["max_power_w"], session.get("max_power")),
        ("norm_power_w", row["norm_power_w"], session.get("normalized_power")),
        ("max_20min_power_w", row["max_20min_power_w"], metrics["max_20min_power_w"]),
        ("intensity_factor", row["intensity_factor"], session.get("intensity_factor")),
        ("tss", row["tss"], session.get("training_stress_score")),
        ("avg_hr", row["avg_hr"], session.get("avg_heart_rate")),
        ("max_hr", row["max_hr"], session.get("max_heart_rate")),
        ("avg_cadence", row["avg_cadence"], session.get("avg_cadence")),
        ("calories", row["calories"], session.get("total_calories")),
    ]
    return [{"column": column, **_diff(session_value, db_value)} for column, db_value, session_value in pairs]


def _fmt(value: Any) -> str:
    return "—" if value is None else str(value)


def render_markdown(
    fit_path: Path, data: FitData, metrics: dict[str, Any], comparisons: list[dict[str, Any]],
    db_rows: list[dict[str, Any]],
) -> str:
    session = data.session
    lines = [
        "# Garmin FIT 数据分析报告（第二阶段）",
        "",
        "> 本报告由 analyze_fit.py 生成；只读分析，不改 FIT、不改数据库。",
        "",
        "## 一、文件与基础信息",
        "",
        f"- FIT 文件：`{fit_path.name}`（{fit_path.stat().st_size} 字节，sha256 `{hashlib.sha256(fit_path.read_bytes()).hexdigest()[:16]}…`）",
        f"- 运动类型：{(data.sport or {}).get('sport')} / {(data.sport or {}).get('sub_sport')}",
        f"- 记录数：{metrics['record_count']}；圈数：{len(data.laps)}",
        f"- 时间范围：{metrics['data_quality']['first_timestamp']} ~ {metrics['data_quality']['last_timestamp']}（UTC）",
        "",
        "## 二、设备 session 摘要",
        "",
        "| 指标 | 设备值 |",
        "|---|---|",
    ]
    for key in (
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
        lines.append(f"| {key} | {_fmt(session.get(key))} |")

    lines += [
        "",
        "## 三、记录流计算值 vs 设备值",
        "",
        "| 指标 | 本地计算 | 设备值 | 绝对差 | 相对差 |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in comparisons:
        lines.append(
            f"| {row['name']} | {_fmt(row['computed'])} | {_fmt(row['reference'])} | "
            f"{_fmt(row['abs'])} | {_fmt(row['rel_pct'])}% |"
        )

    q = metrics["data_quality"]
    lines += [
        "",
        "## 四、数据质量",
        "",
        "| 检查项 | 结果 |",
        "|---|---|",
        f"| 采样间隔中位数 | {_fmt(q['sample_interval_median_s'])} s |",
        f"| 采样间隔范围 | {_fmt(q['sample_interval_min_s'])} ~ {_fmt(q['sample_interval_max_s'])} s |",
        f"| 功率缺失记录 | {q['power_missing']} |",
        f"| 心率缺失记录 | {q['hr_missing']} |",
        f"| 踏频缺失记录 | {q['cadence_missing']} |",
        f"| 零功率记录 | {q['zero_power_records']}（{_fmt(q['zero_power_pct'])}%） |",
        f"| 零踏频记录 | {q['cadence_zero_records']} |",
        f"| 零心率记录 | {q['zero_hr_records']} |",
        f"| 最长连续零心率 | {q['longest_zero_hr_run_s']} s |",
        f"| 超过 {POWER_SPIKE_W} W 的功率点 | {q['power_spikes_over_2500w']} |",
        f"| 大于 1.5 s 的采样断档 | {q['gaps_over_1s']} 处 |",
        "",
        "## 五、本地复算指标",
        "",
        "| 指标 | 值 |",
        "|---|---:|",
        f"| 平均功率 | {_fmt(metrics['avg_power_w'])} W |",
        f"| 标准化功率 NP | {_fmt(metrics['np_w'])} W |",
        f"| 强度因子 IF | {_fmt(metrics['if_value'])} |",
        f"| 训练压力 TSS | {_fmt(metrics['tss'])} |",
        f"| 变异性指数 VI | {_fmt(metrics['vi'])} |",
        f"| 20 分钟最大平均功率 | {_fmt(metrics['max_20min_power_w'])} W |",
        "",
    ]

    if db_rows:
        lines += [
            "## 六、数据库镜像对拍",
            "",
            "| 字段 | 设备 session 值 | 数据库值 | 绝对差 | 相对差 |",
            "|---|---:|---:|---:|---:|",
        ]
        for row in db_rows:
            lines.append(
                f"| {row['column']} | {_fmt(row['computed'])} | {_fmt(row['reference'])} | "
                f"{_fmt(row['abs'])} | {_fmt(row['rel_pct'])}% |"
            )
        lines.append("")

    by_name = {row["name"]: row for row in comparisons}

    def _rel(name: str) -> str:
        row = by_name.get(name)
        return "—" if not row or row["rel_pct"] is None else f"{row['rel_pct']}%"

    lines += [
        "## 七、阶段结论（初判）",
        "",
        f"- 本地复算 vs 设备：平均功率差 {_rel('平均功率 (W)')}，"
        f"NP 差 {_rel('标准化功率 NP (W)')}，IF 差 {_rel('强度因子 IF')}，"
        f"TSS 差 {_rel('训练压力 TSS')}；",
        f"- 平均踏频：含滑行 0 值 {metrics['avg_cadence']} rpm，"
        f"非零值 {metrics['avg_cadence_nonzero']} rpm，设备值 {session.get('avg_cadence')} rpm，"
        "说明踏频口径需按踩踏时间计算；",
        f"- 记录流累加爬升 {metrics['ascent_m']['computed']} m vs 设备 {session.get('total_ascent')} m，"
        "差异来自气压计平滑与逐点噪声，需按设备口径复核；",
        "- 记录流包含功率、心率、踏频、速度、海拔、温度字段，可支撑功率指标与区间分析；",
        "- 具体容差与清洗规则需在批次 2/4 中冻结，本报告作为第二阶段数据分析的起点。",
        "",
    ]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="第二阶段：Garmin FIT 数据分析")
    parser.add_argument("--fit", type=Path, required=True, help="解压后的 .fit 文件")
    parser.add_argument("--activity-id", default=None, help="可选：与工具库对应活动 ID 对拍")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="工具库路径")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT, help="报告输出目录")
    parser.add_argument("--ftp", type=float, default=None, help="FTP（未提供时用 session threshold_power）")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.fit.exists():
        print(f"FIT 文件不存在：{args.fit}", file=sys.stderr)
        return 1
    data = parse_fit(args.fit)
    if not data.records:
        print("FIT 中没有 record 帧", file=sys.stderr)
        return 1
    if args.ftp:
        data.session["threshold_power"] = args.ftp

    metrics = compute_metrics(data)
    comparisons = compare_with_garmin(metrics, data.session)
    db_rows = db_reconcile(args.activity_id, args.db, data.session, metrics)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    markdown = render_markdown(args.fit, data, metrics, comparisons, db_rows)
    report_path = args.out_dir / f"{args.fit.stem}_analysis.md"
    report_path.write_text(markdown, encoding="utf-8")
    json_path = args.out_dir / f"{args.fit.stem}_analysis.json"
    json_path.write_text(
        json.dumps(
            {"metrics": metrics, "comparisons": comparisons, "database": db_rows},
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    print(f"records={metrics['record_count']} laps={len(data.laps)}")
    print(f"duration_s={metrics['duration_s']['computed']} distance_m={metrics['distance_m']['computed']}")
    print(
        f"avg_power={metrics['avg_power_w']} NP={metrics['np_w']} "
        f"IF={metrics['if_value']} TSS={metrics['tss']} VI={metrics['vi']}"
    )
    print("differences (computed vs device):")
    for row in comparisons:
        if row["name"] in ("标准化功率 NP (W)", "强度因子 IF", "训练压力 TSS", "平均功率 (W)"):
            print(f"  {row['name']}: {row['computed']} vs {row['reference']} (rel {row['rel_pct']}%)")
    print(f"report={report_path}")
    print(f"json={json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
