import csv
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import discord_notify as dn

KST = ZoneInfo('Asia/Seoul')


def gem_rows():
    # 7 full calendar days, 2 samples each day; latest 10겁 drops 10%.
    start = datetime(2026, 11, 1, 9, 17, tzinfo=KST)
    rows = []
    for d in range(7):
        for hour in (9, 21):
            t = start + timedelta(days=d, hours=hour-9)
            for lv in (8, 9, 10):
                for kind in ('겁화', '작열'):
                    price = {8: 300000, 9: 900000, 10: 2700000}[lv]
                    if d == 6 and hour == 21 and lv == 10 and kind == '겁화':
                        price = 2430000
                    rows.append({
                        'timestamp_kst': t.strftime('%Y-%m-%d %H:%M:%S%z'),
                        'date_kst': t.strftime('%Y-%m-%d'),
                        'level': str(lv), 'kind': kind,
                        'item_name': f'{lv}레벨 {kind}의 보석',
                        'min_buy_gold': str(price), 'total_listings': str(200 + d),
                        'top3_median_gold': str(price), 'sampled_listings': '10',
                    })
    return rows


class PriceDiscordTests(unittest.TestCase):
    def test_alert_and_daily_means(self):
        rows = gem_rows()
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'gems.csv'
            with p.open('w', encoding='utf-8-sig', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            latest, current, prev, by_gem = dn.load_data(p)
            self.assertEqual(len(current), 6)
            payload = dn.format_payload(latest, current, prev, by_gem, threshold=5.0)
            self.assertIn('10겁 -10.00%', payload['content'])
            self.assertNotIn('@everyone', json.dumps(payload))
            self.assertEqual(len(payload['embeds'][0]['fields']), 7)
            ten = next(f for f in payload['embeds'][0]['fields'] if f['name'] == '10레벨 겁화')
            self.assertIn('직전 -10.00%', ten['value'])
            self.assertIn('직전 +0건', ten['value'])
            self.assertIn('3일 평균', ten['value'])
            self.assertIn('7일 평균', ten['value'])
            self.assertIn('2,430,000 G', ten['value'])
            compare = payload['embeds'][0]['fields'][-1]['value']
            self.assertIn('10레벨 완제품 1개', compare)
            self.assertIn('9레벨 3개 합성', compare)
            self.assertIn('8레벨 9개 합성', compare)
            self.assertIn('10레벨 완제품 1개**', compare)
            self.assertLess(len(json.dumps(payload, ensure_ascii=False)), 6000)

    def test_no_history_average_missing(self):
        rows = gem_rows()[-6:]
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'gems.csv'
            with p.open('w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            latest, current, prev, by_gem = dn.load_data(p)
            payload = dn.format_payload(latest, current, prev, by_gem)
            self.assertNotIn('content', payload)
            self.assertIn('3일 평균 **자료 부족**', payload['embeds'][0]['fields'][0]['value'])
            self.assertIn('7일 평균 **자료 부족**', payload['embeds'][0]['fields'][0]['value'])

    def test_missing_one_day_prevents_full_average(self):
        rows = gem_rows()
        now = dn.as_timestamp(rows[-1]['timestamp_kst'])
        ten_rows = [dict(r, _time=dn.as_timestamp(r['timestamp_kst'])) for r in rows
                    if r['level']=='10' and r['kind']=='겁화'
                    and r['date_kst'] != '2026-11-05']
        self.assertIsNone(dn.average_last_days(ten_rows, now, 3))
        self.assertIsNone(dn.average_last_days(ten_rows, now, 7))

    def test_no_listing_and_threshold(self):
        self.assertIsNone(dn.percent_change('', 300))
        self.assertIsNone(dn.percent_change(100, ''))
        self.assertEqual(dn.calculate_synthesis([
            {'level': '10', 'kind':'겁화', 'min_buy_gold':''},
            {'level': '9', 'kind':'겁화', 'min_buy_gold':'900'},
            {'level': '8', 'kind':'작열', 'min_buy_gold':'350'},
        ])['cheapest'], ('9레벨 3개 합성',2700))
        self.assertEqual(dn.gold_text(None), '자료 부족')
        with self.assertRaises(ValueError):
            import unittest.mock
            with unittest.mock.patch.dict('os.environ', {'PRICE_DROP_ALERT_PCT': '0'}):
                dn.get_threshold()


if __name__ == '__main__':
    unittest.main()
