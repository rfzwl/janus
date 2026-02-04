import sys
from pathlib import Path
import unittest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from janus.server import JanusServer
from vnpy.trader.constant import Direction, Exchange, Offset, OrderType


class OrderParsingTests(unittest.TestCase):
    def setUp(self):
        # Avoid full JanusServer init (DB, engines). We only need parsing helpers.
        self.server = JanusServer.__new__(JanusServer)

    def test_buy_market(self):
        intent = self.server._parse_order_intent({
            "action": "buy",
            "symbol": "AAPL",
            "volume": 1,
        })
        self.assertEqual(intent["direction"], Direction.LONG)
        self.assertEqual(intent["type"], OrderType.MARKET)
        self.assertEqual(intent["price"], 0)
        self.assertEqual(intent["exchange"], Exchange.SMART)
        self.assertEqual(intent["offset"], Offset.OPEN)

    def test_buy_limit(self):
        intent = self.server._parse_order_intent({
            "action": "buy",
            "symbol": "AAPL",
            "volume": 1,
            "price": 100,
        })
        self.assertEqual(intent["type"], OrderType.LIMIT)
        self.assertEqual(intent["price"], 100)

    def test_sell_limit_with_exchange(self):
        intent = self.server._parse_order_intent({
            "action": "sell",
            "symbol": "AAPL",
            "volume": 2,
            "price": 101,
            "exchange": "NASDAQ",
        })
        self.assertEqual(intent["direction"], Direction.SHORT)
        self.assertEqual(intent["exchange"], Exchange.NASDAQ)

    def test_bstop_stop_market(self):
        intent = self.server._parse_order_intent({
            "action": "bstop",
            "symbol": "AAPL",
            "volume": 1,
            "stop_price": 98,
        })
        self.assertEqual(intent["direction"], Direction.LONG)
        self.assertEqual(intent["type"], OrderType.STOP)
        self.assertEqual(intent["stop_price"], 98)
        self.assertEqual(intent["price"], 98)
        self.assertNotIn("limit_price", intent)

    def test_sstop_stop_limit(self):
        intent = self.server._parse_order_intent({
            "action": "sstop",
            "symbol": "AAPL",
            "volume": 1,
            "stop_price": 95,
            "limit_price": 94.5,
        })
        self.assertEqual(intent["direction"], Direction.SHORT)
        self.assertEqual(intent["type"], OrderType.STOP)
        self.assertEqual(intent["stop_price"], 95)
        self.assertEqual(intent["limit_price"], 94.5)
        self.assertEqual(intent["price"], 95)

    def test_missing_action(self):
        with self.assertRaises(ValueError):
            self.server._parse_order_intent({"symbol": "AAPL", "volume": 1})


if __name__ == "__main__":
    unittest.main()
