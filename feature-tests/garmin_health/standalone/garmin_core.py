#!/usr/bin/env python3
"""Garmin 数据查询核心（独立版，可脱离 AstrBot / Agent 直接运行）。

本文件按功能需求独立实现（未复制第三方插件源码），只依赖 pip 包 garminconnect：
- 登录与会话：garminconnect + tokenstore，支持国服/国际区、MFA 交互、429/5xx 退避
- 健康数据：今日概览、近 N 天心率/睡眠/步数
- 活动数据：列表、类型/日期/关键词筛选、单条摘要
- 统计：运动量、个人最佳、年度报告
- 报告：汇总 + 规则建议（不调用任何 LLM）
- 可选写操作：活动改名（必须显式 --yes）

环境变量：
    GARMIN_EMAIL      Garmin 账号邮箱
    GARMIN_PASSWORD   Garmin 账号密码
    GARMIN_IS_CN      1=中国区（默认），0=国际区
    GARMIN_TOKENSTORE tokenstore 目录/文件，默认 ~/.garminconnect

运行依赖：Python 3.12+ 与 garminconnect>=0.3.4。可用 uv 临时环境运行：
    uv run --no-project --python 3.12 --with "garminconnect>=0.3.4" \
        python feature-tests/garmin_health/standalone/garmin_cli.py --help
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, TypeVar

try:
    from garminconnect import (
        Garmin,
        GarminConnectAuthenticationError,
        GarminConnectConnectionError,
        GarminConnectNotFoundError,
        GarminConnectTooManyRequestsError,
    )
except ImportError as exc:  # pragma: no cover - 依赖缺失时给出可执行提示
    raise SystemExit(
        "缺少依赖 garminconnect（>=0.3.4，需要 Python 3.12+）。\n"
        '推荐运行方式：uv run --no-project --python 3.12 --with "garminconnect>=0.3.4" python <脚本>'
    ) from exc

T = TypeVar("T")

DEFAULT_TOKENSTORE = os.environ.get(
    "GARMIN_TOKENSTORE", str(Path.home() / ".garminconnect")
)

# Garmin 活动列表接口只接受父类型；子类型在本地按 typeKey 精确过滤。
PARENT_TYPES = (
    "running",
    "cycling",
    "hiking",
    "walking",
    "swimming",
    "multi_sport",
    "fitness_equipment",
    "other",
)

# 常见子类型 → 父类型；未列出的子类型会原样作为父类型尝试，再本地过滤。
SUBTYPE_TO_PARENT = {
    # running
    "street_running": "running",
    "track_running": "running",
    "indoor_running": "running",
    "treadmill_running": "running",
    "virtual_run": "running",
    "trail_running": "running",
    "ultra_run": "running",
    "obstacle_run": "running",
    # cycling
    "road_biking": "cycling",
    "mountain_biking": "cycling",
    "gravel_cycling": "cycling",
    "indoor_cycling": "cycling",
    "virtual_ride": "cycling",
    "cyclocross": "cycling",
    "downhill_biking": "cycling",
    "e_bike_fitness": "cycling",
    "e_bike_mountain": "cycling",
    "track_cycling": "cycling",
    # hiking / walking
    "rucking": "hiking",
    "casual_walking": "walking",
    "speed_walking": "walking",
    # swimming
    "lap_swimming": "swimming",
    "open_water_swimming": "swimming",
}


# ─────────────────────────────────────────────────────────────
# 通用小工具（纯函数）
# ─────────────────────────────────────────────────────────────


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, "", "N/A"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds or 0))
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    if hours:
        return f"{hours}h{minutes:02d}min"
    return f"{minutes}min"


def fmt_hours(seconds: float) -> float:
    return round(safe_float(seconds) / 3600, 1)


def speed_to_pace(speed_mps: float) -> str:
    speed = safe_float(speed_mps)
    if speed <= 0:
        return "N/A"
    total_seconds = round(1000 / speed)
    return f"{total_seconds // 60}'{total_seconds % 60:02d}\"/km"


def kmh(speed_mps: float) -> float:
    return round(safe_float(speed_mps) * 3.6, 1)


def normalize_date(value: Optional[str], *, end_of_period: bool = False) -> Optional[str]:
    """校验/规范化 YYYY-MM-DD、YYYY-MM、YYYY。"""
    if not value:
        return None
    raw = str(value).strip().replace("/", "-")
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            dt = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        if fmt == "%Y-%m-%d":
            return dt.strftime("%Y-%m-%d")
        if fmt == "%Y-%m":
            if end_of_period:
                import calendar

                last = calendar.monthrange(dt.year, dt.month)[1]
                return f"{dt.year:04d}-{dt.month:02d}-{last:02d}"
            return dt.strftime("%Y-%m-01")
        return f"{dt.year:04d}-12-31" if end_of_period else f"{dt.year:04d}-01-01"
    raise ValueError(f"日期格式应为 YYYY-MM-DD / YYYY-MM / YYYY：{value!r}")


def normalize_date_range(
    start: Optional[str], end: Optional[str]
) -> tuple[Optional[str], Optional[str]]:
    start_n = normalize_date(start) if start else None
    end_n = normalize_date(end, end_of_period=True) if end else None
    if start and not end:
        # 只给起点时补全到该年/月；精确到日则表示“从该日起至今”
        raw_start = str(start).strip()
        if len(raw_start) == 4:
            end_n = f"{int(raw_start)}-12-31"
        elif len(raw_start) == 7:
            end_n = normalize_date(start, end_of_period=True)
    if start_n and end_n and start_n > end_n:
        raise ValueError(f"开始日期晚于结束日期：{start_n} > {end_n}")
    return start_n, end_n


def _iter_days(days: int) -> Iterable[str]:
    days = max(1, int(days))
    today = datetime.now().date()
    for offset in range(days):
        yield (today - timedelta(days=offset)).isoformat()


def _hr_values(data: dict[str, Any]) -> list[float]:
    values: list[float] = []
    for item in data.get("heartRateValues") or []:
        raw = None
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            raw = item[1]
        elif isinstance(item, dict):
            raw = item.get("bpm") or item.get("value") or item.get("heartRate")
        value = safe_float(raw)
        if value > 0:
            values.append(value)
    return values


def _sleep_summary(sleep_data: dict[str, Any]) -> dict[str, Any]:
    dto = sleep_data.get("dailySleepDTO") or {}
    seconds = safe_float(dto.get("sleepTimeSeconds"))
    scores = ((dto.get("sleepScores") or {}).get("overall") or {}).get("value")
    start_ts = dto.get("sleepStartTimestampLocal") or dto.get("sleepStartTimestampGMT")
    end_ts = dto.get("sleepEndTimestampLocal") or dto.get("sleepEndTimestampGMT")

    def as_clock(ts: Any) -> Optional[str]:
        ms = safe_float(ts)
        if ms <= 0:
            return None
        # Garmin 的 *TimestampLocal 用“本地墙上时间”编码成 epoch ms，
        # 按 UTC 渲染才能得到正确的本地钟点。
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%H:%M")

    return {
        "hours": round(seconds / 3600, 1) if seconds > 0 else None,
        "score": scores,
        "deep_min": round(safe_float(dto.get("deepSleepSeconds")) / 60) if seconds else None,
        "light_min": round(safe_float(dto.get("lightSleepSeconds")) / 60) if seconds else None,
        "rem_min": round(safe_float(dto.get("remSleepSeconds")) / 60) if seconds else None,
        "start": as_clock(start_ts),
        "end": as_clock(end_ts),
    }


def _normalize_activity(raw: dict[str, Any]) -> dict[str, Any]:
    type_info = raw.get("activityType") or {}
    distance_m = safe_float(raw.get("distance"))
    duration_s = safe_float(raw.get("duration"))
    speed = safe_float(raw.get("averageSpeed"))
    start_time = str(raw.get("startTimeLocal") or "")
    return {
        "activity_id": str(raw.get("activityId") or ""),
        "name": raw.get("activityName") or "未命名",
        "type": type_info.get("typeKey") or "unknown",
        "start_time": start_time,
        "date": start_time[:10],
        "distance_km": round(distance_m / 1000, 2),
        "duration_s": round(duration_s, 1),
        "duration": fmt_duration(duration_s),
        "elevation_m": round(safe_float(raw.get("elevationGain")), 1),
        "avg_speed_kmh": kmh(speed),
        "avg_pace": speed_to_pace(speed),
        "avg_hr": raw.get("averageHeartRate") or raw.get("averageHR"),
        "calories": round(safe_float(raw.get("calories")), 1),
        "steps": raw.get("steps"),
    }


# ─────────────────────────────────────────────────────────────
# 客户端：登录、会话、退避重试、数据查询
# ─────────────────────────────────────────────────────────────


class GarminClient:
    def __init__(
        self,
        *,
        email: Optional[str] = None,
        password: Optional[str] = None,
        is_cn: Optional[bool] = None,
        tokenstore: Optional[str] = None,
        retry: int = 3,
        retry_wait: float = 20.0,
    ) -> None:
        self.email = email or os.environ.get("GARMIN_EMAIL")
        self.password = password or os.environ.get("GARMIN_PASSWORD")
        self.is_cn = env_bool("GARMIN_IS_CN", True) if is_cn is None else bool(is_cn)
        self.tokenstore = tokenstore or DEFAULT_TOKENSTORE
        self.retry = max(0, int(retry))
        self.retry_wait = float(retry_wait)
        self._client: Optional[Garmin] = None

    # -- 登录 -------------------------------------------------
    def login(self) -> Garmin:
        if self._client is not None:
            return self._client
        if not self.email or not self.password:
            raise SystemExit(
                "缺少 Garmin 账号：请设置环境变量 GARMIN_EMAIL / GARMIN_PASSWORD，"
                "首次登录后再使用 tokenstore 复用会话。"
            )
        prompt_mfa: Optional[Callable[[], str]] = None
        if sys.stdin.isatty():
            prompt_mfa = lambda: input("Garmin MFA 验证码: ").strip()  # noqa: E731
        client = Garmin(
            self.email,
            self.password,
            is_cn=self.is_cn,
            prompt_mfa=prompt_mfa,
        )
        client.login(self.tokenstore)
        self._client = client
        return client

    @property
    def client(self) -> Garmin:
        return self.login()

    def account_name(self) -> str:
        self.login()
        return getattr(self._client, "display_name", None) or getattr(
            self._client, "full_name", None
        ) or "(已登录)"

    # -- 重试包装 ---------------------------------------------
    def call(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        last_error: Optional[BaseException] = None
        for attempt in range(self.retry + 1):
            try:
                return fn(*args, **kwargs)
            except GarminConnectTooManyRequestsError as exc:
                last_error = exc
            except GarminConnectConnectionError as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                try:
                    status_int = int(status) if status is not None else None
                except (TypeError, ValueError):
                    status_int = None
                if status_int is None or not 500 <= status_int < 600:
                    raise
                last_error = exc
            if attempt >= self.retry:
                break
            wait = self.retry_wait * (2 ** attempt)
            print(
                f"[重试] Garmin 接口限流/暂时不可用，{wait:.0f}s 后重试 "
                f"({attempt + 1}/{self.retry})",
                file=sys.stderr,
            )
            time.sleep(wait)
        assert last_error is not None
        raise last_error

    # -- 健康：今日 / 心率 / 睡眠 / 步数 ----------------------
    def today(self, day: Optional[str] = None) -> dict[str, Any]:
        day = normalize_date(day) if day else today_str()
        assert day is not None
        stats = self.call(self.client.get_stats, day) or {}
        heart = self.call(self.client.get_heart_rates, day) or {}
        sleep = self.call(self.client.get_sleep_data, day) or {}

        hr_values = _hr_values(heart)
        distance_m = safe_float(stats.get("totalDistance"))
        return {
            "date": day,
            "steps": stats.get("totalSteps"),
            "distance_km": round(distance_m / 1000, 2) if distance_m > 0 else None,
            "calories_active": stats.get("activeKilocalories"),
            "calories_total": stats.get("totalKilocalories"),
            "heart_rate": {
                "avg": round(sum(hr_values) / len(hr_values)) if hr_values else None,
                "max": round(max(hr_values)) if hr_values else None,
                "min": round(min(hr_values)) if hr_values else None,
            },
            "sleep": _sleep_summary(sleep),
        }

    def heart_rate_days(self, days: int = 7) -> list[dict[str, Any]]:
        rows = []
        for day in _iter_days(days):
            data = self.call(self.client.get_heart_rates, day) or {}
            values = _hr_values(data)
            rows.append(
                {
                    "date": day,
                    "avg": round(sum(values) / len(values)) if values else None,
                    "max": round(max(values)) if values else None,
                    "min": round(min(values)) if values else None,
                }
            )
        return rows

    def sleep_days(self, days: int = 7) -> list[dict[str, Any]]:
        rows = []
        for day in _iter_days(days):
            data = self.call(self.client.get_sleep_data, day) or {}
            row = {"date": day}
            row.update(_sleep_summary(data))
            rows.append(row)
        return rows

    def steps_days(self, days: int = 7) -> list[dict[str, Any]]:
        rows = []
        for day in _iter_days(days):
            stats = self.call(self.client.get_stats, day) or {}
            distance = safe_float(stats.get("totalDistance"))
            rows.append(
                {
                    "date": day,
                    "steps": stats.get("totalSteps"),
                    "distance_km": round(distance / 1000, 2) if distance > 0 else None,
                    "calories_active": stats.get("activeKilocalories"),
                }
            )
        return rows

    # -- 活动：列表与筛选 -------------------------------------
    def activities_raw(
        self,
        *,
        scan_limit: int = 2000,
        activity_type: Optional[str] = None,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        """分页拉取活动原始对象，最多 scan_limit 条。"""
        parent: Optional[str] = None
        if activity_type:
            wanted = activity_type.lower()
            parent = wanted if wanted in PARENT_TYPES else SUBTYPE_TO_PARENT.get(wanted)
            if parent is None:
                parent = None  # 未知类型：不交给 API 过滤，后面本地按 typeKey 精确筛

        collected: list[dict[str, Any]] = []
        start = 0
        while len(collected) < scan_limit:
            batch = self.call(
                self.client.get_activities,
                start,
                min(page_size, scan_limit - len(collected)),
                parent,
            )
            if not batch:
                break
            if isinstance(batch, dict):
                batch = batch.get("activityList") or []
            if not isinstance(batch, list):
                break
            collected.extend(batch)
            if len(batch) < page_size:
                break
            start += len(batch)
        return collected

    def activities(
        self,
        *,
        limit: int = 20,
        activity_type: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        keyword: Optional[str] = None,
        scan_limit: int = 2000,
    ) -> list[dict[str, Any]]:
        start_n, end_n = normalize_date_range(start_date, end_date)
        raw_list = self.activities_raw(
            scan_limit=scan_limit, activity_type=activity_type
        )
        matched: list[dict[str, Any]] = []
        for raw in raw_list:
            item = _normalize_activity(raw)
            if activity_type:
                wanted = activity_type.lower()
                item_type = item["type"].lower()
                if wanted in PARENT_TYPES:
                    # 父类型本应由 Garmin API 过滤；这里对已知子类型再做一次防御性校验
                    mapped = SUBTYPE_TO_PARENT.get(item_type, item_type)
                    if mapped != wanted and mapped in PARENT_TYPES:
                        continue
                elif item_type != wanted:
                    # 子类型/未知类型在本地按 typeKey 精确匹配
                    continue
            if start_n and item["date"] and item["date"] < start_n:
                continue
            if end_n and item["date"] and item["date"] > end_n:
                continue
            if keyword and keyword.lower() not in item["name"].lower():
                continue
            matched.append(item)
            if len(matched) >= max(0, int(limit)):
                break
        return matched

    # -- 统计：运动量 / PB / 年度 ------------------------------
    def volume(
        self,
        activity_type: str,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        scan_limit: int = 3000,
    ) -> dict[str, Any]:
        start_n, end_n = normalize_date_range(start_date, end_date)
        rows = self.activities(
            limit=10_000,
            activity_type=activity_type,
            start_date=start_n,
            end_date=end_n,
            scan_limit=scan_limit,
        )
        total_distance = sum(row["distance_km"] for row in rows)
        total_duration = sum(row["duration_s"] for row in rows)
        total_elevation = sum(row["elevation_m"] for row in rows)
        total_calories = sum(row["calories"] for row in rows)
        hr_weighted = 0.0
        hr_seconds = 0.0
        for row in rows:
            if row.get("avg_hr") and row["duration_s"] > 0:
                hr_weighted += safe_float(row["avg_hr"]) * row["duration_s"]
                hr_seconds += row["duration_s"]
        return {
            "type": activity_type,
            "start_date": start_n,
            "end_date": end_n,
            "count": len(rows),
            "distance_km": round(total_distance, 2),
            "duration_h": round(total_duration / 3600, 2),
            "elevation_m": round(total_elevation, 1),
            "calories": round(total_calories, 1),
            "avg_distance_km": round(total_distance / len(rows), 2) if rows else None,
            "avg_speed_kmh": round(total_distance / (total_duration / 3600), 1)
            if total_duration > 0 and total_distance > 0
            else None,
            "avg_hr": round(hr_weighted / hr_seconds) if hr_seconds > 0 else None,
        }

    def personal_best(
        self, activity_type: Optional[str] = None, *, scan_limit: int = 3000
    ) -> dict[str, Any]:
        rows = self.activities(
            limit=10_000, activity_type=activity_type, scan_limit=scan_limit
        )
        if not rows:
            return {"type": activity_type, "count": 0}
        longest = max(rows, key=lambda row: row["distance_km"])
        fastest = max(rows, key=lambda row: row["avg_speed_kmh"])
        highest = max(rows, key=lambda row: row["elevation_m"])
        longest_time = max(rows, key=lambda row: row["duration_s"])
        return {
            "type": activity_type,
            "count": len(rows),
            "longest": {**longest, "value_km": longest["distance_km"]},
            "fastest": {**fastest, "value_kmh": fastest["avg_speed_kmh"]},
            "highest": {**highest, "value_m": highest["elevation_m"]},
            "longest_time": {**longest_time, "value_s": longest_time["duration_s"]},
        }

    def yearly(self, year: int, *, scan_limit: int = 3000) -> dict[str, Any]:
        rows = self.activities(limit=10_000, scan_limit=scan_limit)
        rows = [row for row in rows if row["date"][:4] == str(year)]
        monthly: dict[int, float] = {month: 0.0 for month in range(1, 13)}
        for row in rows:
            try:
                month = int(row["date"][5:7])
            except (ValueError, IndexError):
                continue
            monthly[month] += row["distance_km"]
        return {
            "year": year,
            "count": len(rows),
            "distance_km": round(sum(row["distance_km"] for row in rows), 2),
            "duration_h": round(sum(row["duration_s"] for row in rows) / 3600, 2),
            "elevation_m": round(sum(row["elevation_m"] for row in rows), 1),
            "calories": round(sum(row["calories"] for row in rows), 1),
            "monthly_distance_km": {
                f"{month:02d}": round(value, 1) for month, value in monthly.items()
            },
        }

    # -- 综合报告：近 N 天均值 + 规则建议 ---------------------
    def report(self, days: int = 7) -> dict[str, Any]:
        days = max(1, min(int(days), 30))
        today = self.today()
        hr_rows = self.heart_rate_days(days)
        sleep_rows = self.sleep_days(days)
        step_rows = self.steps_days(days)

        hr_avgs = [row["avg"] for row in hr_rows if row.get("avg")]
        sleep_hours = [row["hours"] for row in sleep_rows if row.get("hours")]
        sleep_scores = [row["score"] for row in sleep_rows if row.get("score")]
        steps = [row["steps"] for row in step_rows if row.get("steps")]

        summary = {
            "days": days,
            "today": today,
            "avg_hr": round(sum(hr_avgs) / len(hr_avgs)) if hr_avgs else None,
            "avg_sleep_h": round(sum(sleep_hours) / len(sleep_hours), 1)
            if sleep_hours
            else None,
            "avg_sleep_score": round(sum(sleep_scores) / len(sleep_scores))
            if sleep_scores
            else None,
            "avg_steps": round(sum(steps) / len(steps)) if steps else None,
        }
        summary["tips"] = _rule_tips(
            avg_steps=summary["avg_steps"],
            avg_sleep_h=summary["avg_sleep_h"],
            avg_sleep_score=summary["avg_sleep_score"],
        )
        return summary

    # -- 可选写操作：活动改名 ---------------------------------
    def rename_activity(
        self,
        *,
        new_name: str,
        activity_id: Optional[str] = None,
        keyword: Optional[str] = None,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("改名是写操作，必须显式传入 confirmed=True（CLI 使用 --yes）")
        if not new_name.strip():
            raise ValueError("新活动名不能为空")
        target: Optional[dict[str, Any]] = None
        if activity_id:
            try:
                raw = self.call(self.client.get_activity, str(activity_id))
                target = _normalize_activity(raw or {})
            except GarminConnectNotFoundError:
                raise ValueError(f"找不到活动 {activity_id}") from None
        elif keyword:
            rows = self.activities(limit=50)
            matches = [row for row in rows if keyword.lower() in row["name"].lower()]
            if not matches:
                raise ValueError(f"没有名称包含「{keyword}」的活动")
            target = matches[0]
        else:
            raise ValueError("需要 activity_id 或 keyword 定位活动")

        self.call(self.client.set_activity_name, target["activity_id"], new_name)
        return {
            "activity_id": target["activity_id"],
            "old_name": target["name"],
            "new_name": new_name,
        }


def _rule_tips(
    *,
    avg_steps: Optional[int],
    avg_sleep_h: Optional[float],
    avg_sleep_score: Optional[int],
) -> list[str]:
    tips: list[str] = []
    if avg_steps is not None:
        if avg_steps < 8000:
            tips.append("日均步数偏少，建议每天增加 2000~3000 步")
        elif avg_steps >= 10000:
            tips.append("步数达标，继续保持")
    if avg_sleep_h is not None:
        if avg_sleep_h < 7:
            tips.append("睡眠不足 7 小时，建议提前入睡")
        elif avg_sleep_h >= 8:
            tips.append("睡眠时长充足")
    if avg_sleep_score is not None and avg_sleep_score < 70:
        tips.append("睡眠评分偏低，注意睡眠环境与作息规律")
    return tips or ["整体状态良好，继续保持"]


# ─────────────────────────────────────────────────────────────
# 文本格式化（与 CLI 共用）
# ─────────────────────────────────────────────────────────────


def format_today(data: dict[str, Any]) -> str:
    hr = data.get("heart_rate") or {}
    sleep = data.get("sleep") or {}
    distance = data.get("distance_km")
    lines = [f"📅 {data['date']}"]
    line = f"👣 步数 {data.get('steps') if data.get('steps') is not None else 'N/A'}"
    if distance is not None:
        line += f" | 距离 {distance} km"
    lines.append(line)
    lines.append(
        f"🔥 卡路里 {data.get('calories_active', 'N/A')} / {data.get('calories_total', 'N/A')} kcal"
    )
    lines.append(
        f"💓 心率 avg {hr.get('avg', 'N/A')} / max {hr.get('max', 'N/A')} / min {hr.get('min', 'N/A')} bpm"
    )
    sleep_parts = [f"{sleep.get('hours', 'N/A')}h", f"评分 {sleep.get('score', 'N/A')}"]
    if sleep.get("deep_min") is not None:
        sleep_parts.append(
            f"深睡 {sleep['deep_min']}min / 浅睡 {sleep.get('light_min')}min / REM {sleep.get('rem_min')}min"
        )
    if sleep.get("start") and sleep.get("end"):
        sleep_parts.append(f"{sleep['start']}-{sleep['end']}")
    lines.append("😴 睡眠 " + " | ".join(sleep_parts))
    return "\n".join(lines)


def format_heart_rate_days(rows: list[dict[str, Any]]) -> str:
    lines = [f"💓 最近 {len(rows)} 天心率"]
    for row in rows:
        if row.get("avg") is None:
            lines.append(f"{row['date']}: 无数据")
        else:
            lines.append(
                f"{row['date']}: avg {row['avg']} / max {row['max']} / min {row['min']} bpm"
            )
    return "\n".join(lines)


def format_sleep_days(rows: list[dict[str, Any]]) -> str:
    lines = [f"😴 最近 {len(rows)} 天睡眠"]
    for row in rows:
        if row.get("hours") is None:
            lines.append(f"{row['date']}: 无数据")
            continue
        parts = [f"{row['hours']}h", f"评分 {row.get('score', 'N/A')}"]
        if row.get("deep_min") is not None:
            parts.append(
                f"深 {row['deep_min']}/浅 {row.get('light_min')}/REM {row.get('rem_min')}min"
            )
        if row.get("start") and row.get("end"):
            parts.append(f"{row['start']}-{row['end']}")
        lines.append(f"{row['date']}: " + " | ".join(parts))
    return "\n".join(lines)


def format_steps_days(rows: list[dict[str, Any]]) -> str:
    lines = [f"👣 最近 {len(rows)} 天步数"]
    for row in rows:
        distance = f" / {row['distance_km']} km" if row.get("distance_km") else ""
        lines.append(
            f"{row['date']}: {row.get('steps', 'N/A')} 步{distance} / {row.get('calories_active', 'N/A')} kcal"
        )
    return "\n".join(lines)


def format_activities(rows: list[dict[str, Any]], title: str = "活动列表") -> str:
    if not rows:
        return "暂无匹配活动"
    lines = [f"🏃 {title}（{len(rows)} 条）"]
    for index, row in enumerate(rows, 1):
        line = (
            f"#{index} {row['date']} {row['name']}\n"
            f"   {row['type']} | {row['distance_km']} km | ⏱ {row['duration']}"
        )
        if row.get("elevation_m"):
            line += f" | ⛰ {row['elevation_m']:.0f} m"
        if row.get("avg_speed_kmh"):
            line += f" | 🚴 {row['avg_speed_kmh']} km/h"
        if row.get("avg_hr"):
            line += f" | 💓 {row['avg_hr']} bpm"
        if row.get("calories"):
            line += f" | 🔥 {row['calories']:.0f} kcal"
        lines.append(line)
    return "\n".join(lines)


def format_volume(data: dict[str, Any]) -> str:
    if not data.get("count"):
        return f"{data.get('type')}：暂无活动"
    period = ""
    if data.get("start_date") or data.get("end_date"):
        period = f"（{data.get('start_date') or '...'} ~ {data.get('end_date') or '...'}）"
    lines = [f"📊 {data['type']} 运动量{period}"]
    lines.append(
        f"次数 {data['count']} | 距离 {data['distance_km']} km | 时长 {data['duration_h']} h"
    )
    lines.append(
        f"爬升 {data['elevation_m']:.0f} m | 消耗 {data['calories']:.0f} kcal"
    )
    extra = []
    if data.get("avg_distance_km") is not None:
        extra.append(f"均次 {data['avg_distance_km']} km")
    if data.get("avg_speed_kmh") is not None:
        extra.append(f"均速 {data['avg_speed_kmh']} km/h")
    if data.get("avg_hr") is not None:
        extra.append(f"均心率 {data['avg_hr']} bpm")
    if extra:
        lines.append(" | ".join(extra))
    return "\n".join(lines)


def format_personal_best(data: dict[str, Any]) -> str:
    if not data.get("count"):
        return "暂无活动数据"
    label = data.get("type") or "全部活动"
    lines = [f"🏆 个人最佳（{label}，共 {data['count']} 条）"]
    longest = data["longest"]
    fastest = data["fastest"]
    highest = data["highest"]
    duration = data["longest_time"]
    lines.append(f"📏 最长距离 {longest['value_km']} km — {longest['date']} {longest['name']}")
    lines.append(
        f"🚀 最快均速 {fastest['value_kmh']} km/h — {fastest['date']} {fastest['name']}"
    )
    lines.append(f"⛰ 最大爬升 {highest['value_m']:.0f} m — {highest['date']} {highest['name']}")
    lines.append(
        f"⏱ 最长时长 {fmt_duration(duration['value_s'])} — {duration['date']} {duration['name']}"
    )
    return "\n".join(lines)


def format_yearly(data: dict[str, Any]) -> str:
    lines = [f"📅 {data['year']} 年度报告"]
    lines.append(
        f"活动 {data['count']} 次 | 距离 {data['distance_km']} km | 时长 {data['duration_h']} h"
    )
    lines.append(
        f"爬升 {data['elevation_m']:.0f} m | 消耗 {data['calories']:.0f} kcal"
    )
    monthly = [
        f"{month}月 {distance} km"
        for month, distance in data.get("monthly_distance_km", {}).items()
        if distance
    ]
    if monthly:
        lines.append("月度：" + " | ".join(monthly))
    return "\n".join(lines)


def format_report(data: dict[str, Any]) -> str:
    lines = [f"📋 综合健康报告（近 {data['days']} 天）", "", format_today(data["today"])]
    lines.append("")
    lines.append("📊 近 N 天均值")
    lines.append(
        f"步数 {data['avg_steps'] if data['avg_steps'] is not None else 'N/A'} | "
        f"睡眠 {data['avg_sleep_h'] if data['avg_sleep_h'] is not None else 'N/A'} h | "
        f"睡眠评分 {data['avg_sleep_score'] if data['avg_sleep_score'] is not None else 'N/A'} | "
        f"心率 {data['avg_hr'] if data['avg_hr'] is not None else 'N/A'} bpm"
    )
    lines.append("")
    lines.append("💡 建议")
    lines.extend(f"- {tip}" for tip in data.get("tips", []))
    return "\n".join(lines)


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


# ─────────────────────────────────────────────────────────────
# 入口：garmin_core.py 自身可做最小自检（完整 CLI 见 garmin_cli.py）
# ─────────────────────────────────────────────────────────────


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Garmin 数据查询核心（独立版）：--check 验证登录，--today 查看今日概览"
    )
    parser.add_argument("--check", action="store_true", help="验证登录并打印账号信息")
    parser.add_argument("--today", action="store_true", help="打印今日健康概览")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = parser.parse_args(argv)

    try:
        client = GarminClient()
        if args.check:
            info = {"account": client.account_name(), "is_cn": client.is_cn, "tokenstore": client.tokenstore}
            print(to_json(info) if args.json else f"登录成功：{info['account']}（国服={info['is_cn']}，tokenstore={info['tokenstore']}）")
            return 0
        if args.today:
            data = client.today()
            print(to_json(data) if args.json else format_today(data))
            return 0
    except (GarminConnectAuthenticationError, GarminConnectConnectionError) as exc:
        print(f"Garmin 访问失败：{exc}", file=sys.stderr)
        return 2
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 2
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
