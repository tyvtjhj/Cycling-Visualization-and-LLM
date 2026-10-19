#!/usr/bin/env python3
"""Garmin 数据查询 CLI（独立版，无 AstrBot / Agent 依赖）。

运行前设置环境变量：
    GARMIN_EMAIL / GARMIN_PASSWORD / GARMIN_IS_CN(默认1) / GARMIN_TOKENSTORE(可选)

示例：
    uv run --no-project --python 3.12 --with "garminconnect>=0.3.4" \
        python feature-tests/garmin_health/standalone/garmin_cli.py check
    ... garmin_cli.py today
    ... garmin_cli.py activities --type cycling --limit 5
    ... garmin_cli.py volume --type cycling --start 2026-01-01
    ... garmin_cli.py report --days 7 --json
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from typing import Any, Optional

from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from garmin_core import (
    GarminClient,
    format_activities,
    format_heart_rate_days,
    format_personal_best,
    format_report,
    format_sleep_days,
    format_steps_days,
    format_today,
    format_volume,
    format_yearly,
    to_json,
)

ACTIVITY_TYPES = (
    "running",
    "cycling",
    "hiking",
    "walking",
    "swimming",
    "multi_sport",
    "fitness_equipment",
    "other",
    "road_biking",
    "mountain_biking",
    "gravel_cycling",
    "indoor_cycling",
    "virtual_ride",
    "trail_running",
    "treadmill_running",
    "lap_swimming",
    "open_water_swimming",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="garmin_cli.py",
        description="Garmin 数据查询 CLI（独立版，不依赖 AstrBot）。账号从环境变量读取。",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON（便于脚本/看板消费）")
    parser.add_argument(
        "--region",
        choices=("cn", "global"),
        default=None,
        help="账号区域：cn=中国区，global=国际区；默认读 GARMIN_IS_CN（默认中国区）",
    )
    parser.add_argument("--tokenstore", default=None, help="tokenstore 路径，默认 ~/.garminconnect")
    parser.add_argument("--retry", type=int, default=3, help="429/5xx 重试次数，默认 3")
    parser.add_argument(
        "--retry-wait", type=float, default=20.0, help="首次重试等待秒数，默认 20（指数退避）"
    )

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="验证登录并打印账号信息")

    today = sub.add_parser("today", help="今日健康概览")
    today.add_argument("--date", default=None, help="日期 YYYY-MM-DD，默认今天")

    hr = sub.add_parser("hr", help="近 N 天心率")
    hr.add_argument("--days", type=int, default=7)

    sleep = sub.add_parser("sleep", help="近 N 天睡眠")
    sleep.add_argument("--days", type=int, default=7)

    steps = sub.add_parser("steps", help="近 N 天步数")
    steps.add_argument("--days", type=int, default=7)

    activities = sub.add_parser("activities", help="活动列表（可按类型/日期/关键词筛选）")
    activities.add_argument("--type", choices=ACTIVITY_TYPES, default=None, help="活动类型")
    activities.add_argument("--start", default=None, help="开始日期 YYYY-MM-DD / YYYY-MM / YYYY")
    activities.add_argument("--end", default=None, help="结束日期")
    activities.add_argument("--keyword", default=None, help="活动名称关键词")
    activities.add_argument("--limit", type=int, default=20, help="最多显示条数，默认 20")
    activities.add_argument("--scan-limit", type=int, default=2000, help="最多扫描的原始活动数")

    volume = sub.add_parser("volume", help="运动量统计")
    volume.add_argument("--type", required=True, choices=ACTIVITY_TYPES)
    volume.add_argument("--start", default=None)
    volume.add_argument("--end", default=None)
    volume.add_argument("--scan-limit", type=int, default=3000)

    pb = sub.add_parser("pb", help="个人最佳（距离/均速/爬升/时长）")
    pb.add_argument("--type", choices=ACTIVITY_TYPES, default=None)
    pb.add_argument("--scan-limit", type=int, default=3000)

    yearly = sub.add_parser("yearly", help="年度报告")
    yearly.add_argument("--year", type=int, default=datetime.now().year)
    yearly.add_argument("--scan-limit", type=int, default=3000)

    report = sub.add_parser("report", help="综合报告（近 N 天均值 + 规则建议，不调用 LLM）")
    report.add_argument("--days", type=int, default=7)

    rename = sub.add_parser("rename", help="修改活动名称（写操作，必须 --yes）")
    rename.add_argument("--activity-id", default=None, help="活动 ID")
    rename.add_argument("--keyword", default=None, help="按活动名关键词定位首条匹配")
    rename.add_argument("--name", required=True, help="新活动名")
    rename.add_argument("--yes", action="store_true", help="确认执行写操作")

    return parser


def _make_client(args: argparse.Namespace) -> GarminClient:
    is_cn: Optional[bool] = None
    if args.region is not None:
        is_cn = args.region == "cn"
    return GarminClient(
        is_cn=is_cn,
        tokenstore=args.tokenstore,
        retry=args.retry,
        retry_wait=args.retry_wait,
    )


def _emit(args: argparse.Namespace, data: Any, text: str) -> None:
    print(to_json(data) if args.json else text)


def run(args: argparse.Namespace) -> int:
    client = _make_client(args)
    command = args.command

    if command == "check":
        info = {
            "account": client.account_name(),
            "is_cn": client.is_cn,
            "tokenstore": client.tokenstore,
        }
        print(
            to_json(info)
            if args.json
            else f"✅ 登录成功：{info['account']}（中国区={info['is_cn']}，tokenstore={info['tokenstore']}）"
        )
        return 0

    if command == "today":
        data = client.today(args.date)
        _emit(args, data, format_today(data))
        return 0

    if command == "hr":
        rows = client.heart_rate_days(args.days)
        _emit(args, rows, format_heart_rate_days(rows))
        return 0

    if command == "sleep":
        rows = client.sleep_days(args.days)
        _emit(args, rows, format_sleep_days(rows))
        return 0

    if command == "steps":
        rows = client.steps_days(args.days)
        _emit(args, rows, format_steps_days(rows))
        return 0

    if command == "activities":
        rows = client.activities(
            limit=args.limit,
            activity_type=args.type,
            start_date=args.start,
            end_date=args.end,
            keyword=args.keyword,
            scan_limit=args.scan_limit,
        )
        title = f"{args.type or '全部'} 活动"
        _emit(args, rows, format_activities(rows, title))
        return 0

    if command == "volume":
        data = client.volume(
            args.type,
            start_date=args.start,
            end_date=args.end,
            scan_limit=args.scan_limit,
        )
        _emit(args, data, format_volume(data))
        return 0

    if command == "pb":
        data = client.personal_best(args.type, scan_limit=args.scan_limit)
        _emit(args, data, format_personal_best(data))
        return 0

    if command == "yearly":
        data = client.yearly(args.year, scan_limit=args.scan_limit)
        _emit(args, data, format_yearly(data))
        return 0

    if command == "report":
        data = client.report(args.days)
        _emit(args, data, format_report(data))
        return 0

    if command == "rename":
        if not args.yes:
            print("⚠️ 改名会修改 Garmin 云端数据，请确认后加 --yes 再执行", file=sys.stderr)
            return 1
        data = client.rename_activity(
            new_name=args.name,
            activity_id=args.activity_id,
            keyword=args.keyword,
            confirmed=True,
        )
        _emit(
            args,
            data,
            f"✅ 已改名：{data['old_name']} → {data['new_name']}（活动 {data['activity_id']}）",
        )
        return 0

    raise AssertionError(f"未知命令：{command}")


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except SystemExit as exc:  # core 在缺少账号时会 raise SystemExit(message)
        message = exc.code if isinstance(exc.code, str) else "执行中止"
        print(f"❌ {message}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"❌ 参数或数据错误：{exc}", file=sys.stderr)
        return 2
    except GarminConnectAuthenticationError as exc:
        print(f"❌ Garmin 登录失败：{exc}", file=sys.stderr)
        return 2
    except GarminConnectTooManyRequestsError as exc:
        print(f"❌ Garmin 限流：{exc}；请降低频率后重试", file=sys.stderr)
        return 3
    except GarminConnectConnectionError as exc:
        print(f"❌ Garmin 访问失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
