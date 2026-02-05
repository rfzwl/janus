import sys
from pathlib import Path
import unittest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from janus.gateway.webull.webull_gateway import WebullOfficialGateway
from vnpy.event import EventEngine
from vnpy.trader.constant import Direction, Exchange, OrderType, Status
from vnpy.trader.object import OrderData


class FakeGateway(WebullOfficialGateway):
    def __init__(self):
        super().__init__(EventEngine(), "WEBULL_TEST")
        self.orders = []

    def on_order(self, order):
        self.orders.append(order)


class WebullTradeEventsTests(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.gateway.account_id = "acct1"

    def test_trade_event_updates_order(self):
        payload = {
            "account_id": "acct1",
            "client_order_id": "c1",
            "order_id": "o1",
            "symbol": "AAPL",
            "side": "BUY",
            "quantity": "10",
            "filled_qty": "4",
            "order_type": "STOP_LOSS",
            "stop_price": "98",
            "order_status": "SUBMITTED",
            "scene_type": "FILLED",
        }

        self.gateway.handle_trade_event(0, 0, payload, None)
        self.assertTrue(self.gateway.orders)
        order = self.gateway.orders[-1]
        self.assertEqual(order.symbol, "AAPL")
        self.assertEqual(order.direction, Direction.LONG)
        self.assertEqual(order.type, OrderType.STOP)
        self.assertEqual(order.price, 98.0)
        self.assertEqual(order.volume, 10.0)
        self.assertEqual(order.traded, 4.0)
        self.assertEqual(order.status, Status.PARTTRADED)

    def test_trade_event_ignores_other_account(self):
        payload = {
            "account_id": "acct2",
            "order_id": "o2",
            "symbol": "MSFT",
            "side": "SELL",
            "quantity": "1",
        }
        self.gateway.handle_trade_event(0, 0, payload, None)
        self.assertEqual(len(self.gateway.orders), 0)

    def test_trade_event_place_failed_marks_order_rejected(self):
        order = OrderData(
            orderid="oid1",
            symbol="AAPL",
            exchange=Exchange.SMART,
            direction=Direction.LONG,
            type=OrderType.LIMIT,
            volume=1,
            price=10,
            status=Status.NOTTRADED,
            gateway_name=self.gateway.gateway_name,
        )
        self.gateway._known_orders[order.orderid] = order

        payload = {
            "account_id": "acct1",
            "client_order_id": "cid1",
            "symbol": "AAPL",
            "side": "BUY",
            "quantity": "1",
            "filled_qty": "0",
            "order_type": "LIMIT",
            "limit_price": "10",
            "order_status": "FAILED",
            "scene_type": "PLACE_FAILED",
        }

        self.gateway.handle_trade_event(0, 0, payload, None)

        updated = self.gateway._known_orders["oid1"]
        self.assertEqual(updated.status, Status.REJECTED)
        self.assertFalse(updated.is_active())


if __name__ == "__main__":
    unittest.main()
