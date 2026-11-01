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
            self.assertIn('10겁 (약24h -10.00%, 급락)', payload['content'])
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
            self.assertIn('10겁 (약24h -10.00%, 급락)', dn.format_payload(latest, current, prev, by_gem)['content'])

    def test_alert_only_message_and_reference(self):
        def f(d, h, lv, kind, p):
            return int(p * .94) if d == 6 and h == 21 and lv == 10 and kind == '겁화' else p
        td, (latest, current, prev, by_gem) = analyze(fixture_rows(special=f))
        with td:
            events = dn.drop_events(latest, current, prev, by_gem, 5)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]['baseline']['_time'].strftime('%m/%d %H:%M'), '11/06 21:17')
            payload = dn.format_alert_payload(latest, events, 5)
            self.assertIn('신규 급등·급락', payload['embeds'][0]['title'])
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

    def test_hourly_price_update_contains_all_six_gems(self):
        td, (latest, current, prev, by_gem) = analyze(fixture_rows(days=7))
        with td:
            result = dn.format_price_payload(latest, current, prev, by_gem, 5)
            embed = result["embeds"][0]
            self.assertEqual(len(embed["fields"]), 6)
            self.assertIn("1시간 가격 알림", embed["title"])
            self.assertIn("2,700,000 G", embed["fields"][-2]["value"])
            self.assertIn("직전 +0.00%", embed["fields"][-2]["value"])
            self.assertIn("등록 **206건** (직전 +0건)", embed["fields"][-2]["value"])
            self.assertNotIn("3일 평균", json.dumps(result, ensure_ascii=False))
            self.assertNotIn("합성 비용", json.dumps(result, ensure_ascii=False))
            self.assertNotIn("content", result)

    def test_price_update_includes_new_drop_warning_only_when_crossing(self):
        def f(d, h, lv, kind, p):
            return int(p * .94) if d == 6 and h == 21 and lv == 10 and kind == '겁화' else p
        td, (latest, current, prev, by_gem) = analyze(fixture_rows(special=f))
        with td:
            payload = dn.format_price_payload(latest, current, prev, by_gem, 5)
            self.assertIn('10겁 (약24h -6.00%, 급락)', payload['content'])
            self.assertEqual(len(payload['embeds'][0]['fields']), 6)

    def test_hourly_reference_rejects_recent_manual_run(self):
        t = datetime(2026, 11, 7, 6, 17, tzinfo=KST)
        recent = {'_time': t - timedelta(minutes=11), 'min_buy_gold':'100'}
        target = {'_time': t - timedelta(hours=1), 'min_buy_gold':'200'}
        self.assertIs(dn.near_1h_reference([recent, target], t), target)
        self.assertIsNone(dn.near_1h_reference([recent], t))

    def test_hourly_drop_only_and_combined_single_message(self):
        # Twenty-four 1-hour samples each day: prices stable until last hour.
        rows = []
        for d in range(7):
            for h in range(24):
                timestamp = datetime(2026, 11, 1, h, 17, tzinfo=KST) + timedelta(days=d)
                for lv in (8, 9, 10):
                    for kind in ('겁화','작열'):
                        price = {8:300000,9:900000,10:2700000}[lv]
                        if d == 6 and h == 23 and lv == 10 and kind == '겁화':
                            price = int(price * .96)
                        rows.append({
                            'timestamp_kst':timestamp.strftime('%Y-%m-%d %H:%M:%S%z'),
                            'date_kst':timestamp.strftime('%Y-%m-%d'),
                            'level':str(lv),'kind':kind,
                            'item_name':f'{lv}레벨 {kind}의 보석',
                            'min_buy_gold':str(price),'total_listings':'200',
                            'top3_median_gold':str(price),'sampled_listings':'10',
                        })
        td, (latest, current, prev, by_gem) = analyze(rows)
        with td:
            self.assertEqual(len(dn.hourly_drop_events(latest, current, prev, by_gem, 3)), 1)
            self.assertFalse(dn.drop_events(latest, current, prev, by_gem, 5))
            payload = dn.format_price_payload(latest, current, prev, by_gem, 5, 3)
            self.assertIn('10겁 (약1h -4.00%, 급락)', payload['content'])
            self.assertIn('약1h -4.00%', payload['embeds'][0]['fields'][-2]['value'])
            # -7% from an unchanged prior hour crosses BOTH; only one gem entry.
            last = rows[-6:]  # final hour six gem records
            for item in last:
                if item['level']=='10' and item['kind']=='겁화':
                    item['min_buy_gold'] = str(int(2700000 * .93))
            td2, (latest2, current2, prev2, by_gem2) = analyze(rows)
            with td2:
                new_payload = dn.format_price_payload(latest2, current2, prev2, by_gem2, 5, 3)
                self.assertEqual(new_payload['content'].count('10겁 ('), 1)
                self.assertIn('약1h -7.00%', new_payload['content'])
                self.assertIn('약24h -7.00%', new_payload['content'])

    def test_hourly_baseline_missing_shows_data_gap(self):
        td, (latest, current, prev, by_gem) = analyze(fixture_rows(days=7))
        with td:
            self.assertIsNone(dn.percent_change_1h(current[0], by_gem)[0])
            payload = dn.format_price_payload(latest, current, prev, by_gem)
            self.assertIn('약1h 자료 부족', payload['embeds'][0]['fields'][0]['value'])

    def test_hourly_threshold_validation(self):
        with patch.dict(os.environ, {'HOURLY_DROP_ALERT_PCT':'0'}):
            with self.assertRaises(ValueError):
                dn.get_hourly_threshold()

    def test_hourly_three_percent_rise_triggers_once(self):
        # A freshly rising 10-gem +4% within one actual hour.
        rows = fixture_rows()
        last = rows[-6:]
        for r in last:
            if r['level'] == '10' and r['kind'] == '겁화':
                r['min_buy_gold'] = str(int(2700000 * 1.04))
        last_time = dn.as_timestamp(last[-1]['timestamp_kst'])
        prior_time = last_time - timedelta(hours=1)
        prior_rows = []
        for r in last:
            p = dict(r)
            p['timestamp_kst'] = prior_time.strftime('%Y-%m-%d %H:%M:%S%z')
            p['min_buy_gold'] = str({8:300000, 9:900000, 10:2700000}[int(p['level'])])
            prior_rows.append(p)
        td, (latest, current, prev, by_gem) = analyze(rows[:-6] + prior_rows + last)
        with td:
            events = dn.hourly_change_events(latest, current, prev, by_gem, 3)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]['direction'], '급등')
            self.assertAlmostEqual(events[0]['pct'], 4.0)
            self.assertEqual(dn.hourly_drop_events(latest, current, prev, by_gem, 3), events)
            payload = dn.format_price_payload(latest, current, prev, by_gem, 5, 3)
            self.assertIn('10겁 (약1h +4.00%, 급등)', payload['content'])
            self.assertIn('약1h ±3%', payload['embeds'][0]['description'])
            full = dn.format_payload(latest, current, prev, by_gem, 5, 3)
            self.assertIn('10겁 (약1h +4.00%, 급등)', full['content'])
            self.assertIn('약 1h **±3% 이상**', full['embeds'][0]['description'])

    def test_hourly_rise_persistence_is_not_realerted(self):
        rows = fixture_rows()
        latest = dn.as_timestamp(rows[-1]['timestamp_kst'])
        for hour, factor in [(2, 1.00), (1, 1.04)]:
            past = latest - timedelta(hours=hour)
            for old in rows[-6:]:
                new = dict(old)
                new['timestamp_kst'] = past.strftime('%Y-%m-%d %H:%M:%S%z')
                if new['level'] == '10' and new['kind'] == '겁화':
                    new['min_buy_gold'] = str(int(2700000 * factor))
                rows.append(new)
        for item in rows[-18:]:
            # Set the current snapshot to another +4% compared to 1h earlier.
            if item['timestamp_kst'] == latest.strftime('%Y-%m-%d %H:%M:%S%z') \
                    and item['level'] == '10' and item['kind'] == '겁화':
                item['min_buy_gold'] = str(int(2700000 * 1.04 * 1.04))
        td, (latest, current, prev, by_gem) = analyze(rows)
        with td:
            self.assertEqual(dn.hourly_change_events(latest, current, prev, by_gem, 3), [])

    def test_hourly_subthreshold_rise_does_not_trigger(self):
        rows = fixture_rows()
        latest = dn.as_timestamp(rows[-1]['timestamp_kst'])
        prior_time = latest - timedelta(hours=1)
        previous = []
        for r in rows[-6:]:
            x = dict(r)
            x['timestamp_kst'] = prior_time.strftime('%Y-%m-%d %H:%M:%S%z')
            previous.append(x)
            if r['level'] == '10' and r['kind'] == '겁화':
                r['min_buy_gold'] = str(int(2700000 * 1.029))
        td, (latest, current, prev, by_gem) = analyze(rows + previous)
        with td:
            self.assertFalse(dn.hourly_change_events(latest, current, prev, by_gem, 3))

    def test_hourly_rise_after_drop_is_a_new_direction(self):
        rows = fixture_rows()
        latest = dn.as_timestamp(rows[-1]['timestamp_kst'])
        for hours_back, value in [(2, 2700000), (1, 2592000)]:
            time = latest - timedelta(hours=hours_back)
            for old in rows[-6:]:
                x = dict(old)
                x['timestamp_kst'] = time.strftime('%Y-%m-%d %H:%M:%S%z')
                if x['level'] == '10' and x['kind'] == '겁화':
                    x['min_buy_gold'] = str(value)
                rows.append(x)
        for r in rows[-18:-12]:
            if r['level'] == '10' and r['kind'] == '겁화':
                r['min_buy_gold'] = str(int(2592000 * 1.04))
        td, (latest, current, prev, by_gem) = analyze(rows)
        with td:
            events = dn.hourly_change_events(latest, current, prev, by_gem, 3)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]['direction'], '급등')

    def test_hourly_three_percent_threshold_is_inclusive(self):
        self.assertEqual(dn.change_direction(3.0, 3), '급등')
        self.assertEqual(dn.change_direction(-3.0, 3), '급락')
        self.assertIsNone(dn.change_direction(2.999, 3))
        self.assertIsNone(dn.change_direction(-2.999, 3))
        self.assertEqual(dn.change_direction(5.0, 5), '급등')
        self.assertEqual(dn.change_direction(-5.0, 5), '급락')

    def test_opposite_hourly_and_daily_moves_marked_as_mixed(self):
        td, (latest, current, prev, by_gem) = analyze(fixture_rows())
        with td:
            row = next(x for x in current if x['level'] == '10' and x['kind'] == '겁화')
            hourly = {'row': row, 'pct': 4.0, 'direction': '급등'}
            daily = {'row': row, 'pct': -6.0, 'direction': '급락'}
            with patch.object(dn, 'hourly_change_events', return_value=[hourly]), \
                 patch.object(dn, 'drop_events', return_value=[daily]):
                warnings = dn.combined_new_drop_warnings(latest, current, prev, by_gem, 5, 3)
            self.assertEqual(warnings, ['10겁 (약1h +4.00% · 약24h -6.00%, 방향 혼조)'])

    def test_price_mode_always_sends_without_a_drop(self):
        td, data = analyze(fixture_rows(days=7))
        with td, patch.object(dn, 'CSV_PATH', Path(td.name)/'gems.csv'):
            latest = data[0]
            with patch.object(dn, 'now_kst', return_value=latest + timedelta(minutes=2)):
                with patch.dict(os.environ, {
                    'DISCORD_REPORT_MODE':'price',
                    'PRICE_DROP_ALERT_PCT':'5',
                    'DISCORD_WEBHOOK_URL': 'https://discord.com/api/webhooks/123/secret'
                }), patch.object(dn, 'send_webhook') as fake_send:
                    dn.main()
                    fake_send.assert_called_once()
                    self.assertEqual(len(fake_send.call_args.args[1]['embeds'][0]['fields']), 6)

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



    def test_new_24h_rise_warns_once(self):
        def f(d, h, lv, kind, p):
            if d == 6 and h == 21 and lv == 10 and kind == '겁화':
                return int(p * 1.06)
            return p
        td, (latest, current, prev, by_gem) = analyze(fixture_rows(special=f))
        with td:
            events = dn.drop_events(latest, current, prev, by_gem, 5)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]['direction'], '급등')
            self.assertAlmostEqual(events[0]['pct'], 6.0)
            payload = dn.format_price_payload(latest, current, prev, by_gem, 5)
            self.assertIn('+6.00%', payload['content'])
            self.assertIn('급등', payload['content'])
            self.assertIn('±5%', payload['embeds'][0]['description'])

    def test_no_repeat_when_24h_rise_persists(self):
        def f(d, h, lv, kind, p):
            if lv == 10 and kind == '겁화' and d == 6 and h >= 15:
                return int(p * 1.06)
            return p
        td, (latest, current, prev, by_gem) = analyze(fixture_rows(special=f))
        with td:
            self.assertEqual(dn.drop_events(latest, current, prev, by_gem, 5), [])

    def test_subthreshold_24h_rise_is_not_alerted(self):
        def f(d, h, lv, kind, p):
            if d == 6 and h == 21 and lv == 10 and kind == '겁화':
                return int(p * 1.04)
            return p
        td, (latest, current, prev, by_gem) = analyze(fixture_rows(special=f))
        with td:
            self.assertEqual(dn.drop_events(latest, current, prev, by_gem, 5), [])

if __name__ == '__main__':
    unittest.main()
