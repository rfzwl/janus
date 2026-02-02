import sys
from pathlib import Path
import unittest
from unittest import mock

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from janus.gateway.webull.webull_gateway import WebullOfficialGateway
from vnpy.trader.object import OrderRequest
from vnpy.trader.constant import Direction, Exchange, OrderType, Offset


class DummyEventEngine:
    def register(self, *args, **kwargs):
        pass

    def unregister(self, *args, **kwargs):
        pass

    def put(self, *args, **kwargs):
        pass


class FakeResponse:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data
        self.text = "ok" if status_code == 200 else "error"

    def json(self):
        return self._data


class FakeAccountV2:
    def __init__(self, account_list, balance, positions):
        self.account_list = account_list
        self.balance = balance
        self.positions = positions
        self.account_list_called = False

    def get_account_list(self):
        self.account_list_called = True
        return FakeResponse(200, self.account_list)

    def get_account_balance(self, account_id):
        return FakeResponse(200, self.balance)

    def get_account_position(self, account_id):
        return FakeResponse(200, self.positions)


class FakeOrderV2:
    def __init__(self, response):
        self.response = response
        self.last_payload = None

    def place_order(self, account_id, new_orders):
        self.last_payload = {"account_id": account_id, "new_orders": new_orders}
        return self.response


class FakeTrade:
    def __init__(self, open_orders=None):
        self.open_orders = open_orders or []
        self.cancelled = []

    def cancel_order(self, account_id, orderid):
        self.cancelled.append((account_id, orderid))

    def get_open_orders(self, account_id):
        return FakeResponse(200, self.open_orders)


class FakeTradeClient:
    def __init__(self, account_list, balance, positions, order_response):
        self.account_v2 = FakeAccountV2(account_list, balance, positions)
        self.order_v2 = FakeOrderV2(order_response)
        self.trade = FakeTrade()


class CapturingGateway(WebullOfficialGateway):
    def __init__(self, event_engine, **kwargs):
        super().__init__(event_engine, **kwargs)
        self.logged = []
        self.orders = []
        self.accounts = []
        self.positions = []

    def on_log(self, msg):
        self.logged.append(msg)

    def on_order(self, order):
        self.orders.append(order)

    def on_account(self, account):
        self.accounts.append(account)

    def on_position(self, position):
        self.positions.append(position)


class WebullGatewayTests(unittest.TestCase):
    def setUp(self):
        self.event_engine = DummyEventEngine()

    def _make_trade_client(self, account_list=None, balance=None, positions=None, order_response=None):
        if account_list is None:
            account_list = [{"account_id": "123"}]
        if balance is None:
            balance = {"total_net_liquidation_value": 1000, "total_cash_balance": 250}
        if positions is None:
            positions = []
        if order_response is None:
            order_response = FakeResponse(200, {"data": [{"orderId": "999"}]})
        return FakeTradeClient(account_list, balance, positions, order_response)

    def test_connect_accepts_injected_trade_client(self):
        trade_client = self._make_trade_client()
        gateway = CapturingGateway(self.event_engine, trade_client=trade_client)

        gateway.connect({})

        self.assertTrue(trade_client.account_v2.account_list_called)
        self.assertEqual(gateway.account_id, "123")

    def test_connect_uses_injected_api_client(self):
        api_client = object()

        class TradeClientSpy:
            def __init__(self, api_client_arg):
                self.api_client_arg = api_client_arg
                self.account_v2 = FakeAccountV2(
                    [{"account_id": "123"}],
                    {"total_net_liquidation_value": 0, "total_cash_balance": 0},
                    []
                )

        with mock.patch("janus.gateway.webull.webull_gateway.TradeClient", TradeClientSpy):
            gateway = CapturingGateway(self.event_engine, api_client=api_client)
            gateway.connect({})

        self.assertIs(gateway.api_client, api_client)
        self.assertIs(gateway.trade_client.api_client_arg, api_client)

    def test_send_order_uses_response_order_id(self):
        trade_client = self._make_trade_client()
        gateway = CapturingGateway(self.event_engine, trade_client=trade_client)
        gateway.account_id = "123"

        req = OrderRequest(
            symbol="AAPL",
            exchange=Exchange.SMART,
            direction=Direction.LONG,
            type=OrderType.LIMIT,
            volume=1,
            price=10,
            offset=Offset.OPEN,
        )

        vt_orderid = gateway.send_order(req)

        self.assertIn("999", vt_orderid)
        self.assertEqual(len(gateway.orders), 1)
        payload = trade_client.order_v2.last_payload["new_orders"][0]
        self.assertEqual(payload["symbol"], "AAPL")
        self.assertEqual(payload["side"], "BUY")
        self.assertEqual(payload["order_type"], "LIMIT")
        self.assertEqual(payload["quantity"], "1")

    def test_query_account_emits_account_data(self):
        trade_client = self._make_trade_client()
        gateway = CapturingGateway(self.event_engine, trade_client=trade_client)
        gateway.account_id = "123"

        gateway.query_account()

        self.assertEqual(len(gateway.accounts), 1)
        account = gateway.accounts[0]
        self.assertEqual(account.balance, 1000)
        self.assertEqual(account.frozen, 750)

    def test_query_position_parses_positions(self):
        positions = {
            "data": [
                {
                    "ticker": {"symbol": "AAPL"},
                    "position": 5,
                    "costPrice": 10,
                    "unrealizedProfitLoss": 2.5,
                }
            ]
        }
        trade_client = self._make_trade_client(positions=positions)
        gateway = CapturingGateway(self.event_engine, trade_client=trade_client)
        gateway.account_id = "123"

        gateway.query_position()

        self.assertEqual(len(gateway.positions), 1)
        pos = gateway.positions[0]
        self.assertEqual(pos.symbol, "AAPL")
        self.assertEqual(pos.volume, 5)
        self.assertEqual(pos.direction, Direction.LONG)


if __name__ == "__main__":
    unittest.main()
