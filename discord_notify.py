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


def format_payload(latest_time: datetime, current: list[dict],
                   previous: dict[tuple[str, str], dict], by_gem: dict,
                   threshold: float = 5.0) -> dict:
    fields = []
    alerts = []
    for row in sorted(current, key=lambda r: (int(r["level"]), 0 if r["kind"] == "겁화" else 1)):
        key = (row["level"], row["kind"])
        old = previous.get(key, {})
        price = valid_price(row["min_buy_gold"])
        change = percent_change(price, old.get("min_buy_gold"))
        change_label = f"{change:+.2f}%" if change is not None else "비교자료 없음"
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
                f"최저 **{gold_text(price) if price is not None else '매물 없음'}** (직전 {change_label})\n"
                f"3일 평균 **{gold_text(avg3)}** | 7일 평균 **{gold_text(avg7)}**\n"
                f"등록 **{count:,}건** (직전 {count_change})"
            ),
            "inline": False,
        })
        if change is not None and change <= -threshold:
            alerts.append(f"{row['level']}{'겁' if row['kind']=='겁화' else '작'} {change:+.2f}%")

    costs = calculate_synthesis(current)
    cost_rows = [f"{name}: **{gold_text(value)}**" for name, value in costs["options"]]
    if costs["cheapest"]:
        cost_rows.append(f"현재 최저 비용: **{costs['cheapest'][0]}**")
    cost_rows.append("※ 겁화·작열 중 낮은 최저호가 사용, 합성 결과 종류는 보장 안 됨")
    fields.append({"name": "10레벨 1개 확보 비용 비교", "value": "\n".join(cost_rows), "inline": False})

    content = "🚨 **급락 감지 (직전 수집 대비)**: " + ", ".join(alerts) if alerts else ""
    description = (
        f"수집: **{latest_time:%Y-%m-%d %H:%M} KST**\n"
        f"하락 알림 기준: 직전 대비 **-{threshold:g}% 이하**\n"
        f"평균: 한국 날짜 기준 최근 3일·7일의 일별 평균을 다시 평균\n"
        f"[GitHub 기존 최신 보고서]({REPORT_URL})"
    )
    return {
        "username": "로아 보석 시세",
        "allowed_mentions": {"parse": []},
        **({"content": content} if content else {}),
        "embeds": [{
            "title": "로스트아크 4티어 보석 시세 · 무료 분석",
            "description": description,
            "fields": fields,
            "footer": {"text": "호가·등록 건수 스냅샷. 체결가·거래량 아님. 날짜별 기록 부족 시 평균 미표시"},
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


def main() -> None:
    webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        raise RuntimeError("GitHub Secret DISCORD_WEBHOOK_URL이 없어.")
    latest_time, current, previous, by_gem = load_data(CSV_PATH)
    age_seconds = (datetime.now(KST) - latest_time).total_seconds()
    # When collect.py stops after Nov 15, never send an older snapshot again.
    if age_seconds < -60 or age_seconds > 2 * 60 * 60:
        print("최신 기록이 이번 실행에서 생성되지 않았어. Discord 전송을 생략해.")
        return
    payload = format_payload(latest_time, current, previous, by_gem, get_threshold())
    send_webhook(webhook, payload)
    print("Discord 보석 시세·3/7일 평균·합성비 보고서 전송 완료")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"디스코드 보고 실패: {exc}", file=sys.stderr)
        raise SystemExit(1)
