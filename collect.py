#!/usr/bin/env python3
"""Collect current 4-tier Lost Ark gem auction snapshots using stdlib only.

Never puts tokens in source code or output files.
"""
from __future__ import annotations

import csv
import json
import os
import statistics
import sys
import time
from datetime import datetime, date
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

API_URL = "https://developer-lostark.game.onstove.com/auctions/items"
KST = ZoneInfo("Asia/Seoul")
GEMS = [(level, kind) for level in (8, 9, 10) for kind in ("겁화", "작열")]
CSV_PATH = Path("data/gems.csv")
REPORT_PATH = Path("data/latest.md")
FIELDS = ["timestamp_kst", "date_kst", "level", "kind", "item_name",
          "min_buy_gold", "total_listings", "top3_median_gold", "sampled_listings"]


def api_token() -> str:
    token = os.environ.get("LOSTARK_API_KEY", "").strip()
    if not token:
        raise RuntimeError("LOSTARK_API_KEY GitHub Secret이 없거나 비어 있어.")
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


def query_auction(item_name: str, token: str) -> dict:
    payload = {
        "Sort": "BUY_PRICE", "CategoryCode": 210000,
        "ItemTier": 4, "ItemName": item_name, "PageNo": 1,
        "SortCondition": "ASC",
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        API_URL,
        data=body,
        method="POST",
        headers={
            "authorization": f"bearer {token}",
            "accept": "application/json",
            "content-type": "application/json",
            "user-agent": "lostark-gem-tracker/1.0",
        },
    )
    for attempt in range(4):
        try:
            with urlopen(request, timeout=25) as response:
                result = json.loads(response.read().decode("utf-8"))
                if not isinstance(result, dict):
                    raise ValueError("API 응답이 JSON 객체가 아님")
                return result
        except HTTPError as exc:
            # Do not log URL, authorization headers, or response body.
            if exc.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(5 * (attempt + 1))
                continue
            raise RuntimeError(f"공식 API 요청 실패 (HTTP {exc.code})") from None
        except (URLError, TimeoutError) as exc:
            if attempt < 3:
                time.sleep(5 * (attempt + 1))
                continue
            raise RuntimeError(f"공식 API 통신 실패: {type(exc).__name__}") from None
    raise RuntimeError("API 재시도 초과")


def interpret_auction(response: dict, item_name: str) -> dict:
    if not isinstance(response.get("Items"), list) or "TotalCount" not in response:
        raise ValueError(f"{item_name}: API 응답 필드 Items/TotalCount 확인 필요")
    total = int(response["TotalCount"])
    items = response["Items"]
    # Be strict: never count differently named gems as our target listings.
    names = {str(it.get("Name", "")).strip() for it in items if isinstance(it, dict)}
    if names and names != {item_name}:
        raise ValueError(f"{item_name}: 검색 결과에 다른 이름 포함: {sorted(names)}")
    if total < 0:
        raise ValueError("TotalCount < 0")
    prices = []
    for item in items:
        auction_info = item.get("AuctionInfo") or {}
        price = auction_info.get("BuyPrice")
        if price is not None and int(price) > 0:
            prices.append(int(price))
    prices.sort()
    if total > 0 and not prices:
        raise ValueError(f"{item_name}: 매물은 있지만 첫 페이지에서 즉시구매가를 찾지 못함")
    return {
        "min_buy_gold": prices[0] if prices else "",
        "total_listings": total,
        "top3_median_gold": int(statistics.median(prices[:3])) if prices else "",
        "sampled_listings": len(prices),
    }


def collect(token: str, collected_at: datetime) -> list[dict]:
    rows = []
    for level, kind in GEMS:
        item_name = f"{level}레벨 {kind}의 보석"
        stats = interpret_auction(query_auction(item_name, token), item_name)
        row = {
            "timestamp_kst": collected_at.strftime("%Y-%m-%d %H:%M:%S%z"),
            "date_kst": collected_at.strftime("%Y-%m-%d"),
            "level": level,
            "kind": kind,
            "item_name": item_name,
            **stats,
        }
        rows.append(row)
        print(f"{item_name}: 최저 {stats['min_buy_gold'] or '매물 없음'} G, "
              f"현재 등록 {stats['total_listings']}건")
        time.sleep(0.3)  # Do not burst requests needlessly.
    return rows


def existing_data() -> list[dict]:
    if not CSV_PATH.exists():
        return []
    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != FIELDS:
            raise ValueError("기존 data/gems.csv 열 구성이 다름. 자동으로 덮어쓰지 않음")
        return list(reader)


def save_csv(history: list[dict], rows: list[dict]) -> None:
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Replace same timestamp and item instead of creating duplicate records.
    replacing = {(r["timestamp_kst"], str(r["level"]), r["kind"]) for r in rows}
    result = [r for r in history if (r["timestamp_kst"], str(r["level"]), r["kind"]) not in replacing]
    result.extend(rows)
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(result)


def price_display(value) -> str:
    return f"{int(value):,} G" if value not in (None, "") else "매물 없음"


def create_report(history: list[dict], rows: list[dict]) -> None:
    previous = {}
    current_timestamp = rows[0]["timestamp_kst"]
    for row in history:
        if row["timestamp_kst"] >= current_timestamp:
            continue
        k = (str(row["level"]), row["kind"])
        if k not in previous or row["timestamp_kst"] > previous[k]["timestamp_kst"]:
            previous[k] = row

    lines = [
        "# 로스트아크 4티어 보석 경매장 기록",
        "",
        f"- 수집 시각(한국): **{current_timestamp}**",
        "- 출처: 로스트아크 공식 Open API /auctions/items",
        "- 최저가: 수집 당시 등록된 즉시구매 최저 호가 (실제 체결가 아님)",
        "- 등록 건수: API TotalCount (거래 완료 건수·일일 신규 매물 수 아님)",
        "- 상위 3개 중앙값: 첫 페이지에서 확인된 가장 낮은 즉시구매가 3개 기준",
        "",
        "| 보석 | 최저 즉시구매가 | 등록 건수 | 이전 수집 대비 최저가 | 상위 3개 중앙값 |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in rows:
        prev = previous.get((str(row["level"]), row["kind"]))
        if prev and prev["min_buy_gold"] not in ("", None) and row["min_buy_gold"] not in ("", None):
            prev_value = int(prev["min_buy_gold"])
            pct = (int(row["min_buy_gold"]) / prev_value - 1) * 100 if prev_value > 0 else None
            delta = f"{pct:+.2f}%" if pct is not None else "-"
        else:
            delta = "비교자료 없음"
        lines.append(
            f"| {row['level']}레벨 {row['kind']} | {price_display(row['min_buy_gold'])} | "
            f"{row['total_listings']:,} | {delta} | {price_display(row['top3_median_gold'])} |"
        )
    lines += [
        "", "## 해석 시 주의", "",
        "보석 검색 결과는 특정 시점의 등록 매물 스냅샷이야. "
        "하루에 몇 개 팔렸는지는 이 데이터만으로 계산할 수 없어. "
        "검색 첫 페이지의 즉시구매가가 비어 있거나 API 구조가 바뀌면 "
        "데이터를 기록하지 않고 오류로 종료하도록 설계했어.",
        "", "매수 판단과 현금 가격은 별도로 확인해야 해.", "",
    ]
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    now = datetime.now(KST)
    stop_at = date.fromisoformat(os.getenv("STOP_AFTER_KST", "2026-11-15"))
    if now.date() > stop_at:
        print(f"수집 종료일({stop_at})이 지났어. 자료 추가 없이 종료해.")
        return
    token = api_token()
    history = existing_data()
    records = collect(token, now)
    # Only persist if all 6 gem queries completed successfully.
    create_report(history, records)
    save_csv(history, records)
    print(f"저장 완료: {CSV_PATH}, {REPORT_PATH}")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, KeyError, OSError) as error:
        # Avoid accidentally echoing secret values (we never include them in errors).
        print(f"수집 실패: {error}", file=sys.stderr)
        raise SystemExit(1)
