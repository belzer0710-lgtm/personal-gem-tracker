import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
import collect


class CollectorTests(unittest.TestCase):
    def test_interpret(self):
        name = '10레벨 겁화의 보석'
        response = {
            'TotalCount': 9,
            'Items': [
                {'Name': name, 'AuctionInfo': {'BuyPrice': 2500000}},
                {'Name': name, 'AuctionInfo': {'BuyPrice': 2490000}},
                {'Name': name, 'AuctionInfo': {'BuyPrice': 2520000}},
            ],
        }
        result = collect.interpret_auction(response, name)
        self.assertEqual(result['min_buy_gold'], 2490000)
        self.assertEqual(result['total_listings'], 9)
        self.assertEqual(result['top3_median_gold'], 2500000)

    def test_mismatched_name_is_rejected(self):
        with self.assertRaises(ValueError):
            collect.interpret_auction({'TotalCount': 1, 'Items': [
                {'Name': '다른 보석', 'AuctionInfo': {'BuyPrice': 100}}
            ]}, '10레벨 겁화의 보석')

    def test_no_listings_is_not_zero_price(self):
        result = collect.interpret_auction({'TotalCount': 0, 'Items': []}, '8레벨 작열의 보석')
        self.assertEqual(result['min_buy_gold'], '')
        self.assertEqual(result['total_listings'], 0)

    def test_saving_and_report(self):
        kst = ZoneInfo('Asia/Seoul')
        current = datetime(2026, 10, 30, 21, 17, tzinfo=kst)
        def fake_response(name, _token):
            return {'TotalCount': 4, 'Items': [
                {'Name': name, 'AuctionInfo': {'BuyPrice': 100}},
                {'Name': name, 'AuctionInfo': {'BuyPrice': 110}},
                {'Name': name, 'AuctionInfo': {'BuyPrice': 120}},
            ]}
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(collect, 'CSV_PATH', Path(tmp)/'data/gems.csv'), \
                 patch.object(collect, 'REPORT_PATH', Path(tmp)/'data/latest.md'), \
                 patch.object(collect, 'query_auction', side_effect=fake_response), \
                 patch.object(collect.time, 'sleep', return_value=None):
                records = collect.collect('dummy_key', current)
                self.assertEqual(len(records), 6)
                collect.create_report([], records)
                collect.save_csv([], records)
                self.assertEqual(len(collect.existing_data()), 6)
                self.assertIn('10레벨 겁화', collect.REPORT_PATH.read_text(encoding='utf-8'))
                collect.save_csv(collect.existing_data(), records)
                self.assertEqual(len(collect.existing_data()), 6)


if __name__ == '__main__':
    unittest.main()
