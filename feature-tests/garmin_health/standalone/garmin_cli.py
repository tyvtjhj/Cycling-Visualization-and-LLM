#!/usr/bin/env python3
"""Garmin 数据查询 CLI（独立版，无 AstrBot / Agent 依赖）。

测试账号默认读取同目录 credentials.json（含隐私，已 gitignore，不会入库）；
也可用 --config 指定其他路径或 GARMIN_CONFIG 环境变量。格式见同目录 README.md。

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
    configure_stdio,
    credentials_path,
    format_activities,
    format_heart_rate_days,
    format_personal_best,
    format_report,
    format_sleep_days,
    format_steps_days,
    format_today,
    format_volume,
    format_yearly,
    load_credentials,
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


def _global_options() -> argparse.ArgumentParser:
    """全局参数：允许写在子命令前或后（子命令通过 parents 复用）。"""
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="输出 JSON（便于脚本/看板消费）",
    )
    parent.add_argument(
        "--config",
        default=argparse.SUPPRESS,
        help="测试账号 JSON 路径，默认脚本同目录 credentials.json（不入库）",
    )
    parent.add_argument(
        "--region",
        choices=("cn", "global"),
        default=argparse.SUPPRESS,
        help="账号区域：cn=中国区，global=国际区；未指定时用账号文件的 is_cn",
    )
    parent.add_argument(
        "--tokenstore",
        default=argparse.SUPPRESS,
        help="tokenstore 路径；未指定时用账号文件配置或 ~/.garminconnect",
    )
    parent.add_argument(
        "--retry", type=int, default=argparse.SUPPRESS, help="429/5xx 重试次数，默认 3"
    )
    parent.add_argument(
        "--retry-wait",
        type=float,
        default=argparse.SUPPRESS,
        help="首次重试等待秒数，默认 20（指数退避）",
    )
    return parent


def build_parser() -> argparse.ArgumentParser:
    global_opts = _global_options()
    parser = argparse.ArgumentParser(
        prog="garmin_cli.py",
        description="Garmin 数据查询 CLI（独立版，不依赖 AstrBot）。账号从 credentials.json 读取。",
        parents=[global_opts],
    )
    parser.set_defaults(
        json=False,
        config=None,
        region=None,
        tokenstore=None,
        retry=3,
        retry_wait=20.0,
    )

    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, help=help_text)

    add("check", "验证登录并打印账号信息")

    today = add("today", "今日健康概览")
    today.add_argument("--date", default=None, help="日期 YYYY-MM-DD，默认今天")

    hr = add("hr", "近 N 天心率")
    hr.add_argument("--days", type=int, default=7)

    sleep = add("sleep", "近 N 天睡眠")
    sleep.add_argument("--days", type=int, default=7)

    steps = add("steps", "近 N 天步数")
    steps.add_argument("--days", type=int, default=7)

    activities = add("activities", "活动列表（可按类型/日期/关键词筛选）")
    activities.add_argument("--type", choices=ACTIVITY_TYPES, default=None, help="活动类型")
    activities.add_argument("--start", default=None, help="开始日期 YYYY-MM-DD / YYYY-MM / YYYY")
    activities.add_argument("--end", default=None, help="结束日期")
    activities.add_argument("--keyword", default=None, help="活动名称关键词")
    activities.add_argument("--limit", type=int, default=20, help="最多显示条数，默认 20")
    activities.add_argument("--scan-limit", type=int, default=2000, help="最多扫描的原始活动数")

    volume = add("volume", "运动量统计")
    volume.add_argument("--type", required=True, choices=ACTIVITY_TYPES)
    volume.add_argument("--start", default=None)
    volume.add_argument("--end", default=None)
    volume.add_argument("--scan-limit", type=int, default=3000)

    pb = add("pb", "个人最佳（距离/均速/爬升/时长）")
    pb.add_argument("--type", choices=ACTIVITY_TYPES, default=None)
    pb.add_argument("--scan-limit", type=int, default=3000)

    yearly = add("yearly", "年度报告")
    yearly.add_argument("--year", type=int, default=datetime.now().year)
    yearly.add_argument("--scan-limit", type=int, default=3000)

    report = add("report", "综合报告（近 N 天均值 + 规则建议，不调用 LLM）")
    report.add_argument("--days", type=int, default=7)

    rename = add("rename", "修改活动名称（写操作，必须 --yes）")
    rename.add_argument("--activity-id", default=None, help="活动 ID")
    rename.add_argument("--keyword", default=None, help="按活动名关键词定位首条匹配")
    rename.add_argument("--name", required=True, help="新活动名")
    rename.add_argument("--yes", action="store_true", help="确认执行写操作")

    return parser


def _normalize_argv(argv: list[str]) -> list[str]:
    """把全局参数统一挪到子命令前，兼容 `today --json` 与 `--json today`。"""
    flags = {"--json"}
    value_opts = {"--config", "--region", "--tokenstore", "--retry", "--retry-wait"}
    head: list[str] = []
    rest: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        name = token.split("=", 1)[0]
        if name in flags:
            head.append(token)
            index += 1
        elif name in value_opts:
            head.append(token)
            if "=" not in token and index + 1 < len(argv):
                head.append(argv[index + 1])
                index += 2
            else:
                index += 1
        else:
            rest.append(token)
            index += 1
    return head + rest


def _make_client(args: argparse.Namespace) -> tuple[GarminClient, str]:
    cfg_file = credentials_path(args.config)
    cfg = load_credentials(cfg_file)
    is_cn = (args.region == "cn") if args.region is not None else cfg["is_cn"]
    client = GarminClient(
        email=cfg["email"],
        password=cfg["password"],
        is_cn=is_cn,
        tokenstore=args.tokenstore or cfg["tokenstore"],
        retry=args.retry,
        retry_wait=args.retry_wait,
    )
    return client, str(cfg_file)


def _emit(args: argparse.Namespace, data: Any, text: str) -> None:
    print(to_json(data) if args.json else text)


def run(args: argparse.Namespace) -> int:
    client, config_file = _make_client(args)
    command = args.command

    if command == "check":
        info = {
            "account": client.account_name(),
            "is_cn": client.is_cn,
            "tokenstore": client.tokenstore,
            "credentials": config_file,
        }
        print(
            to_json(info)
            if args.json
            else (
                f"✅ 登录成功：{info['account']}（中国区={info['is_cn']}，"
                f"tokenstore={info['tokenstore']}，账号文件={info['credentials']}）"
            )
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
    configure_stdio()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(_normalize_argv(raw_argv))
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
        print(
            f"❌ Garmin 登录/接口被限流（429）：{exc}\n"
            "建议：稍后重试、关闭代理或换网络、确认账号区域与 is_cn 是否匹配。",
            file=sys.stderr,
        )
        return 3
    except GarminConnectConnectionError as exc:
        print(f"❌ Garmin 访问失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
