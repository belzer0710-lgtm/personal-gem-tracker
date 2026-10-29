#!/usr/bin/env python3
"""Send the latest saved Lost Ark gem snapshot to Discord.
GitHub Secret DISCORD_WEBHOOK_URL supplies the destination.
Uses Python standard library only.
"""
from __future__ import annotations

import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
CSV_PATH = Path(__file__).resolve().parent / "data" / "gems.csv"
REPORT_URL = (
    "https://github.com/belzer0710-lgtm/"
    "personal-gem-tracker/blob/main/data/latest.md"
)


def load_snapshots(path: Path) -> tuple[datetime, list[dict], dict[tuple[str, str], dict]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        data = list(csv.DictReader(f))
    if not data:
        raise ValueError("data/gems.csv에 기록이 없어.")
    latest_str = max(row["timestamp_kst"] for row in data)
    latest_time = datetime.strptime(latest_str, "%Y-%m-%d %H:%M:%S%z")
    current = [row for row in data if row["timestamp_kst"] == latest_str]
    if len(current) != 6 or len({(r["level"], r["kind"]) for r in current}) != 6:
        raise ValueError("최신 수집 결과에 보석 6종이 모두 있지 않아.")
    earlier = [row for row in data if row["timestamp_kst"] < latest_str]
    previous = {}
    for row in earlier:
        key = (row["level"], row["kind"])
        if key not in previous or row["timestamp_kst"] > previous[key]["timestamp_kst"]:
            previous[key] = row
    return latest_time, current, previous


def change_text(now: str, before: str, *, is_price: bool) -> str:
    if not now or not before:
        return "비교자료 없음"
    a, b = int(now), int(before)
    if is_price:
        if b <= 0:
            return "비교자료 없음"
        return f"{(a / b - 1) * 100:+.2f}%"
    return f"{a - b:+,}건"


def format_payload(latest_time: datetime, current: list[dict],
                   previous: dict[tuple[str, str], dict]) -> dict:
    fields = []
    for row in sorted(current, key=lambda r: (int(r["level"]), 0 if r["kind"] == "겁화" else 1)):
        key = (row["level"], row["kind"])
        old = previous.get(key, {})
        price = row["min_buy_gold"]
        price_label = f"{int(price):,} G" if price else "매물 없음"
        price_change = change_text(price, old.get("min_buy_gold", ""), is_price=True)
        listing_change = change_text(row["total_listings"],
                                     old.get("total_listings", ""), is_price=False)
        fields.append({
            "name": f'{row["level"]}레벨 {row["kind"]}',
            "value": (
                f"최저가 **{price_label}** ({price_change})\n"
                f"등록 **{int(row['total_listings']):,}건** ({listing_change})"
            ),
            "inline": False,
        })
    return {
        "username": "로아 보석 시세",
        "allowed_mentions": {"parse": []},
        "embeds": [{
            "title": "로스트아크 4티어 보석 시세",
            "description": (
                f'수집: **{latest_time.astimezone(KST):%Y-%m-%d %H:%M} KST**\n'
                f'[GitHub 상세 보고서]({REPORT_URL})'
            ),
            "fields": fields,
            "footer": {"text": "즉시구매 최저 호가·현재 등록 건수 (체결가·거래량 아님)"},
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
        headers={"Content-Type": "application/json", "User-Agent": "lostark-gem-tracker/1.1"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=20) as response:
            if not (200 <= response.status < 300):
                raise RuntimeError(f"Discord 메시지 전송 실패 (HTTP {response.status})")
    except HTTPError as exc:
        # Do NOT print the URL: it contains the private webhook token.
        raise RuntimeError(f"Discord 메시지 전송 실패 (HTTP {exc.code})") from None
    except (URLError, TimeoutError):
        raise RuntimeError("Discord 서버 연결 실패") from None


def main() -> None:
    webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        raise RuntimeError("GitHub Secret DISCORD_WEBHOOK_URL이 없어.")
    latest_time, current, previous = load_snapshots(CSV_PATH)
    age_seconds = (datetime.now(KST) - latest_time.astimezone(KST)).total_seconds()
    # collect.py stops writing after the requested end date; don't resend old data.
    if age_seconds < -60 or age_seconds > 2 * 60 * 60:
        print("최신 기록이 이번 실행에서 생성되지 않았어. Discord 전송을 생략해.")
        return
    send_webhook(webhook, format_payload(latest_time, current, previous))
    print("Discord 보석 시세 보고서 전송 완료")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"디스코드 보고 실패: {exc}", file=sys.stderr)
        raise SystemExit(1)
