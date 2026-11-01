#!/usr/bin/env python3
"""Send a free, calculation-only gem market snapshot to Discord.

Sources: data/gems.csv, collected by the existing official Lost Ark API script.
No OpenAI API, no additional dependencies, no token in generated files.
"""
from __future__ import annotations

import csv
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
CSV_PATH = Path(__file__).resolve().parent / "data" / "gems.csv"
REPORT_URL = "https://github.com/belzer0710-lgtm/personal-gem-tracker/blob/main/data/latest.md"
EXPECTED_KEYS = {(str(lv), kind) for lv in (8, 9, 10) for kind in ("겁화", "작열")}
MAX_BASELINE_DEVIATION = timedelta(minutes=90)
MAX_HOURLY_BASELINE_DEVIATION = timedelta(minutes=20)
MODES = {"full", "price", "alert_only"}


def as_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S%z").astimezone(KST)


def load_data(path: Path) -> tuple[datetime, list[dict], dict[tuple[str, str], dict], dict]:
    """Parse all records, pick latest snapshot and prior snapshot per gem."""
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or not {
            "timestamp_kst", "level", "kind", "min_buy_gold", "total_listings"
        }.issubset(reader.fieldnames):
            raise ValueError("CSV 열 구성이 예상과 달라.")
        rows = list(reader)
    if not rows:
        raise ValueError("data/gems.csv에 기록이 없어.")
    for row in rows:
        row["_time"] = as_timestamp(row["timestamp_kst"])
    rows.sort(key=lambda r: r["_time"])
    latest_time = rows[-1]["_time"]
    current = [r for r in rows if r["_time"] == latest_time]
    if len(current) != 6 or {(r["level"], r["kind"]) for r in current} != EXPECTED_KEYS:
        raise ValueError("최신 수집 결과에 보석 6종이 모두 있지 않아.")
    previous: dict[tuple[str, str], dict] = {}
    by_gem: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        key = (row["level"], row["kind"])
        by_gem[key].append(row)
        if row["_time"] < latest_time:
            previous[key] = row  # rows sorted; latest earlier observation wins
    return latest_time, current, previous, by_gem


def valid_price(value: str | int | None) -> int | None:
    try:
        number = int(value) if value not in (None, "") else 0
    except (ValueError, TypeError):
        return None
    return number if number > 0 else None


def percent_change(current: str | int | None, old: str | int | None) -> float | None:
    a, b = valid_price(current), valid_price(old)
    return (a / b - 1) * 100 if a is not None and b is not None else None


def average_last_days(rows: list[dict], now: datetime, days: int) -> float | None:
    """Average daily means for the last N calendar dates in Korea (incl. today).

    One morning and one evening sample from the same date get equal-day weight
    with a date having just one sample. Require a record on EVERY date.
    Missing price observations are skipped; if a date lacks prices, return None.
    """
    daily: dict[date, list[int]] = defaultdict(list)
    first_day = now.date() - timedelta(days=days - 1)
    for r in rows:
        t = r["_time"]
        if first_day <= t.date() <= now.date() and t <= now:
            value = valid_price(r["min_buy_gold"])
            if value is not None:
                daily[t.date()].append(value)
    expected = [first_day + timedelta(days=i) for i in range(days)]
    if any(not daily.get(day) for day in expected):
        return None
    return sum(sum(daily[day]) / len(daily[day]) for day in expected) / days


def gold_text(value: float | int | None) -> str:
    return f"{value:,.0f} G" if value is not None else "자료 부족"


def get_threshold() -> float:
    raw = os.getenv("PRICE_DROP_ALERT_PCT", "5").strip()
    try:
        threshold = float(raw)
    except ValueError:
        raise ValueError("PRICE_DROP_ALERT_PCT는 양의 숫자여야 해.") from None
    if not (0 < threshold <= 100):
        raise ValueError("PRICE_DROP_ALERT_PCT는 0 초과 100 이하로 설정해.")
    return threshold


def get_hourly_threshold() -> float:
    raw = os.getenv("HOURLY_DROP_ALERT_PCT", "3").strip()
    try:
        threshold = float(raw)
    except ValueError:
        raise ValueError("HOURLY_DROP_ALERT_PCT는 양의 숫자여야 해.") from None
    if not (0 < threshold <= 100):
        raise ValueError("HOURLY_DROP_ALERT_PCT는 0 초과 100 이하로 설정해.")
    return threshold


def calculate_synthesis(current: list[dict]) -> dict:
    best = {}
    for level in (8, 9, 10):
        prices = [valid_price(r["min_buy_gold"]) for r in current if r["level"] == str(level)]
        available = [n for n in prices if n is not None]
        best[level] = min(available) if available else None
    options = [
        ("10레벨 완제품 1개", best[10]),
        ("9레벨 3개 합성", best[9] * 3 if best[9] is not None else None),
        ("8레벨 9개 합성", best[8] * 9 if best[8] is not None else None),
    ]
    available = [(name, price) for name, price in options if price is not None]
    cheapest = min(available, key=lambda item: item[1]) if available else None
    return {"options": options, "cheapest": cheapest}


def near_24h_reference(rows: list[dict], sample_time: datetime) -> dict | None:
    """Return a real price observation nearest ~24h before sample_time.

    A 3-hour snapshot can be late or missed; never invent an exact-day price.
    Reject a reference more than 90 min from the intended 24h timestamp.
    """
    target = sample_time - timedelta(hours=24)
    candidates = [
        row for row in rows
        if row["_time"] < sample_time
        and abs(row["_time"] - target) <= MAX_BASELINE_DEVIATION
        and valid_price(row.get("min_buy_gold")) is not None
    ]
    return min(candidates, key=lambda row: abs(row["_time"] - target)) if candidates else None


def percent_change_24h(row: dict, by_gem: dict) -> tuple[float | None, dict | None]:
    key = (row["level"], row["kind"])
    earlier = near_24h_reference(by_gem[key], row["_time"])
    if earlier is None:
        return None, None
    return percent_change(row.get("min_buy_gold"), earlier.get("min_buy_gold")), earlier


def near_1h_reference(rows: list[dict], sample_time: datetime) -> dict | None:
    """Closest real observation to one hour earlier, within +/- 20 minutes.

    Avoid silently treating an immediately preceding manual run as a 1h sample.
    """
    target = sample_time - timedelta(hours=1)
    candidates = [
        row for row in rows
        if row["_time"] < sample_time
        and abs(row["_time"] - target) <= MAX_HOURLY_BASELINE_DEVIATION
        and valid_price(row.get("min_buy_gold")) is not None
    ]
    return min(candidates, key=lambda row: abs(row["_time"] - target)) if candidates else None


def percent_change_1h(row: dict, by_gem: dict) -> tuple[float | None, dict | None]:
    key = (row["level"], row["kind"])
    earlier = near_1h_reference(by_gem[key], row["_time"])
    if earlier is None:
        return None, None
    return percent_change(row.get("min_buy_gold"), earlier.get("min_buy_gold")), earlier


def change_direction(change: float | None, threshold: float) -> str | None:
    """Return the threshold-crossing direction, or None below either limit."""
    if change is None:
        return None
    if change >= threshold:
        return "급등"
    if change <= -threshold:
        return "급락"
    return None


def hourly_change_events(latest_time: datetime, current: list[dict],
                         previous: dict[tuple[str, str], dict], by_gem: dict,
                         threshold: float = 3.0) -> list[dict]:
    """Warn when the ~1h change newly crosses +threshold or -threshold.

    A persistent rise/drop is not a new event, while a reversal is.
    """
    events = []
    for row in sorted(current, key=lambda r: (int(r["level"]), r["kind"])):
        current_change, baseline = percent_change_1h(row, by_gem)
        current_direction = change_direction(current_change, threshold)
        if current_direction is None:
            continue
        prior = previous.get((row["level"], row["kind"]))
        prior_change = percent_change_1h(prior, by_gem)[0] if prior is not None else None
        if current_direction != change_direction(prior_change, threshold):
            events.append({"row": row, "pct": current_change,
                           "baseline": baseline, "direction": current_direction})
    return events


def hourly_drop_events(latest_time: datetime, current: list[dict],
                       previous: dict[tuple[str, str], dict], by_gem: dict,
                       threshold: float = 3.0) -> list[dict]:
    """Backward-compatible alias; handles both rises and drops now."""
    return hourly_change_events(latest_time, current, previous, by_gem, threshold)


def combined_new_drop_warnings(latest_time: datetime, current: list[dict],
                                previous: dict[tuple[str, str], dict], by_gem: dict,
                                threshold_24h: float, threshold_1h: float) -> list[str]:
    """One message entry per gem, even if both periods trigger."""
    grouped: dict[tuple[str, str], dict] = {}
    for period, events in (
        ("약1h", hourly_change_events(latest_time, current, previous, by_gem, threshold_1h)),
        ("약24h", drop_events(latest_time, current, previous, by_gem, threshold_24h)),
    ):
        for event in events:
            row = event["row"]
            key = (row["level"], row["kind"])
            grouped.setdefault(key, {})[period] = event["pct"]
    lines = []
    for (level, kind), changes in sorted(grouped.items(), key=lambda x: (int(x[0][0]), x[0][1])):
        label = f"{level}{'겁' if kind == '겁화' else '작'}"
        detail = ' · '.join(f"{period} {pct:+.2f}%" for period, pct in changes.items())
        directions = {"급등" if pct > 0 else "급락" for pct in changes.values()}
        movement = directions.pop() if len(directions) == 1 else "방향 혼조"
        lines.append(f"{label} ({detail}, {movement})")
    return lines


def drop_events(latest_time: datetime, current: list[dict],
                previous: dict[tuple[str, str], dict], by_gem: dict,
                threshold: float) -> list[dict]:
    """Report a new >=5% rise or <=-5% fall in approx-24h price.

    Do not repeat a warning while it stays above the same threshold.
    A reversal from drop to rise (or rise to drop) counts as a new event.
    """
    events = []
    for row in sorted(current, key=lambda r: (int(r["level"]), r["kind"])):
        current_change, baseline = percent_change_24h(row, by_gem)
        current_direction = change_direction(current_change, threshold)
        if current_direction is None:
            continue
        prior = previous.get((row["level"], row["kind"]))
        prior_change = percent_change_24h(prior, by_gem)[0] if prior is not None else None
        if current_direction != change_direction(prior_change, threshold):
            events.append({"row": row, "pct": current_change,
                           "baseline": baseline, "direction": current_direction})
    return events

def format_payload(latest_time: datetime, current: list[dict],
                   previous: dict[tuple[str, str], dict], by_gem: dict,
                   threshold: float = 5.0, hourly_threshold: float = 3.0) -> dict:
    """09:17/21:17 full report with both 1h and 24h comparisons."""
    fields = []
    alerts = []
    for row in sorted(current, key=lambda r: (int(r["level"]), 0 if r["kind"] == "겁화" else 1)):
        key = (row["level"], row["kind"])
        old = previous.get(key, {})
        price = valid_price(row["min_buy_gold"])
        change = percent_change(price, old.get("min_buy_gold"))
        change_label = f"{change:+.2f}%" if change is not None else "비교자료 없음"
        change_1h, baseline_1h = percent_change_1h(row, by_gem)
        label_1h = f"{change_1h:+.2f}%" if change_1h is not None else "자료 부족"
        change_24h, baseline_24h = percent_change_24h(row, by_gem)
        label_24h = f"{change_24h:+.2f}%" if change_24h is not None else "자료 부족"
        count = int(row["total_listings"])
        count_change = (
            f"{count - int(old['total_listings']):+,}건"
            if old and old.get("total_listings") not in (None, "") else "비교자료 없음"
        )
        avg3 = average_last_days(by_gem[key], latest_time, 3)
        avg7 = average_last_days(by_gem[key], latest_time, 7)
        fields.append({
            "name": f"{row['level']}레벨 {row['kind']}",
            "value": (
                f"최저 **{gold_text(price) if price is not None else '매물 없음'}** "
                f"(직전 {change_label} / 약1h {label_1h} / 약24h {label_24h})\n"
                f"3일 평균 **{gold_text(avg3)}** | 7일 평균 **{gold_text(avg7)}**\n"
                f"등록 **{count:,}건** (직전 {count_change})"
            ),
            "inline": False,
        })
        label = f"{row['level']}{'겁' if row['kind'] == '겁화' else '작'}"
        triggers = []
        if change_direction(change_1h, hourly_threshold):
            triggers.append(f"약1h {change_1h:+.2f}%")
        if change_24h is not None and abs(change_24h) >= threshold:
            triggers.append(f"약24h {change_24h:+.2f}%")
        if triggers:
            directions = set()
            if change_direction(change_1h, hourly_threshold):
                directions.add(change_direction(change_1h, hourly_threshold))
            if change_direction(change_24h, threshold):
                directions.add(change_direction(change_24h, threshold))
            movement = directions.pop() if len(directions) == 1 else "방향 혼조"
            alerts.append(label + " (" + " · ".join(triggers) + f", {movement})")

    costs = calculate_synthesis(current)
    cost_rows = [f"{name}: **{gold_text(value)}**" for name, value in costs["options"]]
    if costs["cheapest"]:
        cost_rows.append(f"현재 최저 비용: **{costs['cheapest'][0]}**")
    cost_rows.append("※ 겁화·작열 중 낮은 최저호가 사용, 합성 결과 종류는 보장 안 됨")
    fields.append({"name": "10레벨 1개 확보 비용 비교", "value": "\n".join(cost_rows), "inline": False})

    content = "🚨 **급등·급락 기준 도달**: " + ", ".join(alerts) if alerts else ""
    description = (
        f"수집: **{latest_time:%Y-%m-%d %H:%M} KST**\n"
        f"기준: 약 1h **±{hourly_threshold:g}% 이상**, 약 24h **±{threshold:g}% 이상**\n"
        f"실제 수집 시각 기준: 1h ±20분, 24h ±90분 허용\n"
        f"3일·7일 평균: 한국 날짜별 평균 후 재평균\n"
        f"[GitHub 최신 보고서]({REPORT_URL})"
    )
    return {
        "username": "로아 보석 시세",
        "allowed_mentions": {"parse": []},
        **({"content": content} if content else {}),
        "embeds": [{
            "title": "로스트아크 4티어 보석 시세 · 무료 분석",
            "description": description,
            "fields": fields,
            "footer": {"text": "현재 등록된 호가·건수, 체결가·거래량 아님. 날짜별 기록 부족 시 평균 미표시"},
        }],
    }



def format_price_payload(latest_time: datetime, current: list[dict],
                         previous: dict[tuple[str, str], dict],
                         by_gem: dict, threshold: float = 5.0,
                         hourly_threshold: float = 3.0) -> dict:
    """Hourly update: newly crossed +/-3%/1h or +/-5%/24h moves."""
    fields = []
    for row in sorted(current, key=lambda r: (int(r["level"]), 0 if r["kind"] == "겁화" else 1)):
        key = (row["level"], row["kind"])
        old = previous.get(key, {})
        price = valid_price(row.get("min_buy_gold"))
        change = percent_change(price, old.get("min_buy_gold"))
        change_text = f"{change:+.2f}%" if change is not None else "비교자료 없음"
        change_1h, baseline_1h = percent_change_1h(row, by_gem)
        label_1h = f"{change_1h:+.2f}%" if change_1h is not None else "자료 부족"
        change_24h, baseline_24h = percent_change_24h(row, by_gem)
        label_24h = f"{change_24h:+.2f}%" if change_24h is not None else "자료 부족"
        count = int(row["total_listings"])
        count_change = (
            f"{count - int(old['total_listings']):+,}건"
            if old and old.get("total_listings") not in (None, "")
            else "비교자료 없음"
        )
        fields.append({
            "name": f"{row['level']}레벨 {row['kind']}",
            "value": (
                f"최저 **{gold_text(price) if price is not None else '매물 없음'}** "
                f"(직전 {change_text})\n"
                f"약1h {label_1h} / 약24h {label_24h}\n"
                f"등록 **{count:,}건** (직전 {count_change})"
            ),
            "inline": True,
        })

    warnings = combined_new_drop_warnings(latest_time, current, previous, by_gem,
                                          threshold, hourly_threshold)
    content = ("🚨 **신규 급등·급락 감지:** " + ", ".join(warnings)) if warnings else ""
    return {
        "username": "로아 보석 시세",
        "allowed_mentions": {"parse": []},
        **({"content": content} if content else {}),
        "embeds": [{
            "title": "로스트아크 4티어 보석 · 1시간 가격 알림",
            "description": (
                f"수집: **{latest_time:%Y-%m-%d %H:%M} KST**\n"
                f"강조 기준: 약1h ±{hourly_threshold:g}%, 약24h ±{threshold:g}% 급등·급락 (신규 진입만 강조)\n"
                f"실제 수집 간격에 따라 1h ±20분, 24h ±90분 허용\n"
                f"[GitHub 최신 보고서]({REPORT_URL})"
            ),
            "fields": fields,
            "footer": {"text": "최저 호가와 현재 등록 건수 기준, 거래 체결가격·거래량 아님"},
        }],
    }



def format_alert_payload(latest_time: datetime, events: list[dict], threshold: float) -> dict:
    """Send only new 24h rise/drop events."""
    lines = []
    for event in events:
        row, baseline, pct = event["row"], event["baseline"], event["pct"]
        baseline_time = baseline["_time"].strftime("%m/%d %H:%M")
        lines.append(
            f"**{row['level']}레벨 {row['kind']}**: {pct:+.2f}% ({event['direction']})\n"
            f"{gold_text(valid_price(baseline['min_buy_gold']))} ({baseline_time}) → "
            f"{gold_text(valid_price(row['min_buy_gold']))}"
        )
    return {
        "username": "로아 보석 급등·급락 알림",
        "allowed_mentions": {"parse": []},
        "embeds": [{
            "title": f"🚨 신규 급등·급락 감지 (약 24시간 ±{threshold:g}% 이상)",
            "description": (
                f"수집: **{latest_time:%Y-%m-%d %H:%M} KST**\n\n"
                + "\n\n".join(lines)
                + "\n\n기준을 새로 넘었을 때 1회 알림. 체결가가 아닌 최저 호가 기준."
            ),
            "footer": {"text": "최신 시세 스냅샷 / 지난 약 24시간의 근사 비교"},
        }],
    }


def send_webhook(url: str, payload: dict) -> None:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != "discord.com"
            or not parsed.path.startswith("/api/webhooks/")):
        raise ValueError("DISCORD_WEBHOOK_URL은 Discord 웹훅 주소여야 해.")
    req = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "lostark-gem-tracker/2.0"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=20) as response:
            if not (200 <= response.status < 300):
                raise RuntimeError(f"Discord 메시지 전송 실패 (HTTP {response.status})")
    except HTTPError as exc:
        # Webhook token is secret: NEVER print the URL or HTTP body.
        raise RuntimeError(f"Discord 메시지 전송 실패 (HTTP {exc.code})") from None
    except (URLError, TimeoutError):
        raise RuntimeError("Discord 서버 연결 실패") from None


def now_kst() -> datetime:
    return datetime.now(KST)


def main() -> None:
    mode = os.getenv("DISCORD_REPORT_MODE", "full").strip().lower()
    if mode not in MODES:
        raise ValueError("DISCORD_REPORT_MODE는 full, price, alert_only 중 하나여야 해.")
    latest_time, current, previous, by_gem = load_data(CSV_PATH)
    age_seconds = (now_kst() - latest_time).total_seconds()
    # After the original stop date, do not report a stale snapshot.
    if age_seconds < -60 or age_seconds > 2 * 60 * 60:
        print("이번 실행에서 수집한 새 데이터가 없어. Discord 전송을 생략해.")
        return
    threshold = get_threshold()
    hourly_threshold = get_hourly_threshold()
    if mode == "full":
        payload = format_payload(latest_time, current, previous, by_gem, threshold, hourly_threshold)
    elif mode == "price":
        payload = format_price_payload(latest_time, current, previous, by_gem, threshold, hourly_threshold)
    else:
        events = drop_events(latest_time, current, previous, by_gem, threshold)
        if not events:
            print("이번 중간 수집에서는 새 24시간 급등·급락이 없어서 디스코드 알림을 생략해.")
            return
        payload = format_alert_payload(latest_time, events, threshold)
    webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        raise RuntimeError("GitHub Secret DISCORD_WEBHOOK_URL이 없어.")
    send_webhook(webhook, payload)
    print(f"Discord 전송 완료 (mode={mode})")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"디스코드 보고 실패: {exc}", file=sys.stderr)
        raise SystemExit(1)
