import csv
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import discord_notify as dn

KST = ZoneInfo('Asia/Seoul')


def fixture_rows(days=7, special=None):
    """Return 8 full snapshots per Korea date for all six gem types."""
    start = datetime(2026, 11, 1, 0, 17, tzinfo=KST)
    rows = []
    for d in range(days):
        for h in range(0, 24, 3):
            t = start + timedelta(days=d, hours=h)
            for lv in (8, 9, 10):
                for kind in ('겁화', '작열'):
                    price = {8: 300000, 9: 900000, 10: 2700000}[lv]
                    if special:
                        price = special(d, h, lv, kind, price)
                    rows.append({
                        'timestamp_kst': t.strftime('%Y-%m-%d %H:%M:%S%z'),
                        'date_kst': t.strftime('%Y-%m-%d'),
                        'level': str(lv), 'kind': kind,
                        'item_name': f'{lv}레벨 {kind}의 보석',
                        'min_buy_gold': str(price) if price else '',
                        'total_listings': str(200 + d),
                        'top3_median_gold': str(price) if price else '',
                        'sampled_listings': '10',
                    })
    return rows


def analyze(rows):
    td = tempfile.TemporaryDirectory()
    p = Path(td.name)/'gems.csv'
    with p.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return td, dn.load_data(p)


class PriceDiscordTests(unittest.TestCase):
    def test_full_report_and_alert_for_recent_drop(self):
        def f(d, h, lv, kind, p):
            return int(p * .90) if d == 6 and h == 21 and lv == 10 and kind == '겁화' else p
        td, (latest, current, prev, by_gem) = analyze(fixture_rows(special=f))
        with td:
            payload = dn.format_payload(latest, current, prev, by_gem, threshold=5)
            self.assertIn('10겁 -10.00%', payload['content'])
            self.assertEqual(len(payload['embeds'][0]['fields']), 7)
            ten = next(field for field in payload['embeds'][0]['fields'] if field['name']=='10레벨 겁화')
            self.assertIn('직전 -10.00%', ten['value'])
            self.assertIn('약24h -10.00%', ten['value'])
            self.assertIn('3일 평균', ten['value'])
            self.assertIn('7일 평균', ten['value'])
            self.assertIn('등록 **206건** (직전 +0건)', ten['value'])
            self.assertIn('9레벨 3개 합성', payload['embeds'][0]['fields'][-1]['value'])
            self.assertNotIn('@everyone', json.dumps(payload))
            self.assertEqual(len(dn.drop_events(latest, current, prev, by_gem, 5)), 1)
            self.assertLess(len(json.dumps(payload, ensure_ascii=False)), 6000)

    def test_no_alert_repetition_when_drop_persists(self):
        def f(d, h, lv, kind, p):
            if lv == 10 and kind == '겁화' and d == 6 and h >= 15:
                return int(p * .90)
            return p
        td, (latest, current, prev, by_gem) = analyze(fixture_rows(special=f))
        with td:
            self.assertEqual(dn.drop_events(latest, current, prev, by_gem, 5), [])
            self.assertIn('10겁 -10.00%', dn.format_payload(latest, current, prev, by_gem)['content'])

    def test_alert_only_message_and_reference(self):
        def f(d, h, lv, kind, p):
            return int(p * .94) if d == 6 and h == 21 and lv == 10 and kind == '겁화' else p
        td, (latest, current, prev, by_gem) = analyze(fixture_rows(special=f))
        with td:
            events = dn.drop_events(latest, current, prev, by_gem, 5)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]['baseline']['_time'].strftime('%m/%d %H:%M'), '11/06 21:17')
            payload = dn.format_alert_payload(latest, events, 5)
            self.assertIn('신규 급락', payload['embeds'][0]['title'])
            self.assertIn('-6.00%', payload['embeds'][0]['description'])
            self.assertIn('2,700,000 G', payload['embeds'][0]['description'])
            self.assertNotIn('합성 비용', json.dumps(payload, ensure_ascii=False))

    def test_baseline_missing_does_not_invent_drop(self):
        td, (latest, current, prev, by_gem) = analyze(fixture_rows(days=1))
        with td:
            self.assertIsNone(dn.percent_change_24h(current[0], by_gem)[0])
            payload = dn.format_payload(latest, current, prev, by_gem)
            self.assertIn('약24h 자료 부족', payload['embeds'][0]['fields'][0]['value'])
            self.assertNotIn('content', payload)
            self.assertFalse(dn.drop_events(latest, current, prev, by_gem, 5))

    def test_nearest_24h_and_max_gap(self):
        latest = datetime(2026, 11, 7, 21, 17, tzinfo=KST)
        row_a = {'_time': latest - timedelta(hours=24, minutes=38), 'min_buy_gold':'100'}
        row_b = {'_time': latest - timedelta(hours=22), 'min_buy_gold':'200'}
        self.assertIs(dn.near_24h_reference([row_a, row_b], latest), row_a)
        self.assertIsNone(dn.near_24h_reference([
            {'_time': latest - timedelta(hours=19), 'min_buy_gold': '100'}
        ], latest))

    def test_average_requires_all_calendar_days(self):
        rows = fixture_rows()
        last = dn.as_timestamp(rows[-1]['timestamp_kst'])
        subset = [dict(r, _time=dn.as_timestamp(r['timestamp_kst'])) for r in rows
                  if r['level'] == '10' and r['kind'] == '겁화' and r['date_kst'] != '2026-11-05']
        self.assertIsNone(dn.average_last_days(subset, last, 3))
        self.assertIsNone(dn.average_last_days(subset, last, 7))

    def test_cost_and_threshold(self):
        self.assertIsNone(dn.percent_change('', 300))
        self.assertEqual(dn.calculate_synthesis([
            {'level':'10', 'kind':'겁화', 'min_buy_gold':''},
            {'level':'9', 'kind':'겁화', 'min_buy_gold':'900'},
            {'level':'8', 'kind':'작열', 'min_buy_gold':'350'},
        ])['cheapest'], ('9레벨 3개 합성', 2700))
        with patch.dict(os.environ, {'PRICE_DROP_ALERT_PCT':'0'}):
            with self.assertRaises(ValueError):
                dn.get_threshold()

    def test_main_alert_only_skips_sending_without_drop(self):
        rows = fixture_rows(days=7)
        td, data = analyze(rows)
        with td, patch.object(dn, 'CSV_PATH', Path(td.name)/'gems.csv'):
            latest = data[0]
            # simulate that the data were collected just now
            with patch.object(dn, 'now_kst', return_value=latest + timedelta(minutes=2)):
                with patch.dict(os.environ, {'DISCORD_REPORT_MODE':'alert_only', 'PRICE_DROP_ALERT_PCT':'5'}), \
                     patch.object(dn, 'send_webhook') as fake_send:
                    dn.main()
                    fake_send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
