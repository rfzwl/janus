import sys
import tempfile
from pathlib import Path
import unittest
from unittest import mock

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from janus.client import JanusRpcClient
from janus.tui import JanusTUI
from vnpy.event import Event
from vnpy.trader.constant import Direction, Exchange, OrderType, Status
from vnpy.trader.object import OrderData, PositionData


class FakeConfigLoader:
    def __init__(self, *args, **kwargs):
        pass

    def get_all_accounts(self):
        return [{"name": "acct1"}, {"name": "acct2"}]

    def get_default_account_name(self):
        return "acct1"

    def get_rpc_setting(self):
        return {"rep_address": "tcp://*:2014", "pub_address": "tcp://*:4102"}

    def get_history_setting(self):
        return ".janus_history"


class CapturingClient(JanusRpcClient):
    def __init__(self):
        super().__init__()
        self.sent_orders = []

    def send_order(self, req, gateway_name):
        self.sent_orders.append((req, gateway_name))
        return "order1"


class SyncCapturingClient(JanusRpcClient):
    def __init__(self):
        super().__init__()
        self.sync_calls = []

    def request_sync(self, account=None, log_func=None):
        self.sync_calls.append(account or self.default_account)


class HarmonyCapturingClient(JanusRpcClient):
    def __init__(self):
        super().__init__()
        self.harmony_calls = 0

    def request_harmony(self, log_func=None):
        self.harmony_calls += 1


class SnapshotSyncClient(JanusRpcClient):
    def __init__(self):
        super().__init__()
        self._socket_req = object()
        self.sync_calls = []

    def sync_gateway(self, account):
        self.sync_calls.append(account)
        return "ok"

    def get_all_active_orders(self):
        return [
            OrderData(
                symbol="AAPL",
                exchange=Exchange.SMART,
                orderid="order1",
                direction=Direction.LONG,
                gateway_name="acct1",
            ),
            OrderData(
                symbol="MSFT",
                exchange=Exchange.SMART,
                orderid="order2",
                direction=Direction.LONG,
                gateway_name="acct2",
            ),
        ]

    def get_all_positions(self):
        return [
            PositionData(
                symbol="AAPL",
                exchange=Exchange.SMART,
                direction=Direction.LONG,
                volume=5,
                price=100,
                pnl=0,
                gateway_name="acct1",
            ),
            PositionData(
                symbol="MSFT",
                exchange=Exchange.SMART,
                direction=Direction.LONG,
                volume=2,
                price=50,
                pnl=0,
                gateway_name="acct2",
            ),
        ]


class DownloadCapturingClient(JanusRpcClient):
    def __init__(self):
        super().__init__()
        self.download_calls = []

    def request_download_initial(
        self,
        symbol,
        interval,
        account=None,
        replace=False,
        log_func=None,
    ):
        self.download_calls.append(
            {
                "symbol": symbol,
                "interval": interval,
                "account": account,
                "replace": replace,
            }
        )


class ClientCommandTests(unittest.TestCase):
    def setUp(self):
        self.config_patcher = mock.patch("janus.client.ConfigLoader", FakeConfigLoader)
        self.rpc_init_patcher = mock.patch("janus.client.RpcClient.__init__", lambda self: None)
        self.config_patcher.start()
        self.rpc_init_patcher.start()

    def tearDown(self):
        self.config_patcher.stop()
        self.rpc_init_patcher.stop()

    def test_account_set_default(self):
        client = CapturingClient()
        logs = []
        client.process_command("account acct2", logs.append)
        self.assertEqual(client.default_account, "acct2")

    def test_account_route_does_not_change_default(self):
        client = CapturingClient()
        logs = []
        client.process_command("account acct2 buy AAPL 1 10", logs.append)
        self.assertEqual(client.default_account, "acct1")
        self.assertEqual(len(client.sent_orders), 1)
        self.assertEqual(client.sent_orders[0][1], "acct2")

    def test_command_history_persists_across_sessions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = str(Path(tmpdir) / "janus_history.txt")
            client = CapturingClient()
            tui = JanusTUI(client, history_path=history_path)
            tui.history.append_string("buy AAPL 1 100")

            another_client = CapturingClient()
            next_tui = JanusTUI(another_client, history_path=history_path)
            history_entries = list(next_tui.history.load_history_strings())

            self.assertIn("buy AAPL 1 100", history_entries)

    def test_callback_handles_event_wrapper(self):
        client = CapturingClient()
        pos = PositionData(
            symbol="AAPL",
            exchange=Exchange.SMART,
            direction=Direction.LONG,
            volume=1,
            price=10,
            pnl=0,
            gateway_name="acct1",
        )
        event = Event("ePosition.", pos)
        client.callback("", event)

        positions = client.get_positions("acct1")
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0].symbol, "AAPL")

        order = OrderData(
            symbol="AAPL",
            exchange=Exchange.SMART,
            orderid="order1",
            direction=Direction.LONG,
            gateway_name="acct1",
        )
        order_event = Event("eOrder.", order)
        client.callback("", order_event)

        orders = client.get_open_orders("acct1")
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].orderid, "order1")

    def test_sync_command_triggers_sync(self):
        client = SyncCapturingClient()
        logs = []
        client.process_command("sync", logs.append)
        self.assertEqual(client.sync_calls, ["acct1"])

    def test_account_switch_triggers_sync(self):
        client = SyncCapturingClient()
        logs = []
        client.process_command("account acct2", logs.append)
        self.assertEqual(client.sync_calls, ["acct2"])

    def test_sync_refreshes_snapshot_for_account(self):
        client = SnapshotSyncClient()
        client.request_sync(account="acct1", log_func=lambda _: None)

        orders = client.get_open_orders("acct1")
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].orderid, "order1")

        positions = client.get_positions("acct1")
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0].symbol, "AAPL")

    def test_harmony_command_triggers_request(self):
        client = HarmonyCapturingClient()
        logs = []
        client.process_command("harmony", logs.append)
        self.assertEqual(client.harmony_calls, 1)

    def test_download_initial_command_routes_to_current_account(self):
        client = DownloadCapturingClient()
        logs = []

        client.process_command("download initial qqq 1", logs.append)

        self.assertEqual(len(client.download_calls), 1)
        call = client.download_calls[0]
        self.assertEqual(call["symbol"], "qqq")
        self.assertEqual(call["interval"], "1")
        self.assertEqual(call["account"], "acct1")
        self.assertFalse(call["replace"])

    def test_download_initial_command_with_replace(self):
        client = DownloadCapturingClient()
        logs = []

        client.process_command("download initial qqq 1 replace", logs.append)

        self.assertEqual(len(client.download_calls), 1)
        self.assertTrue(client.download_calls[0]["replace"])

    def test_download_initial_account_override(self):
        client = DownloadCapturingClient()
        logs = []

        client.process_command("account acct2 download initial qqq 1 replace", logs.append)

        self.assertEqual(len(client.download_calls), 1)
        self.assertEqual(client.download_calls[0]["account"], "acct2")

    def test_download_initial_usage_error(self):
        client = DownloadCapturingClient()
        logs = []

        client.process_command("download initial", logs.append)

        self.assertEqual(len(client.download_calls), 0)
        self.assertTrue(any("Usage: download initial" in msg for msg in logs))

    def test_open_order_prices_for_stop_market(self):
        order = OrderData(
            symbol="AAPL",
            exchange=Exchange.SMART,
            orderid="order1",
            direction=Direction.LONG,
            type=OrderType.STOP,
            price=102.5,
            gateway_name="acct1",
        )

        price, aux = JanusTUI._format_order_prices(order)
        self.assertEqual(price, "-")
        self.assertEqual(aux, "102.5")

    def test_open_order_prices_for_stop_limit(self):
        order = OrderData(
            symbol="AAPL",
            exchange=Exchange.SMART,
            orderid="order1",
            direction=Direction.LONG,
            type=OrderType.STOP,
            price=102.5,
            gateway_name="acct1",
        )
        order.extra = {"aux_price": 102.5, "limit_price": 102.0}

        price, aux = JanusTUI._format_order_prices(order)
        self.assertEqual(price, "102.0")
        self.assertEqual(aux, "102.5")

    def test_alltraded_without_fill_price_does_not_log_filled(self):
        client = CapturingClient()
        logs = []
        client.log_callback = logs.append

        order = OrderData(
            symbol="QQQ",
            exchange=Exchange.SMART,
            orderid="order1",
            direction=Direction.LONG,
            type=OrderType.STOP,
            volume=1,
            price=500,
            status=Status.ALLTRADED,
            gateway_name="acct1",
        )
        order.filled_price = ""

        client._log_order_update(order, prev_status=None)
        self.assertEqual(logs, [])

    def test_alltraded_with_fill_price_logs_filled(self):
        client = CapturingClient()
        logs = []
        client.log_callback = logs.append

        order = OrderData(
            symbol="QQQ",
            exchange=Exchange.SMART,
            orderid="order1",
            direction=Direction.LONG,
            type=OrderType.STOP,
            volume=1,
            price=500,
            status=Status.ALLTRADED,
            gateway_name="acct1",
        )
        order.filled_price = 501.25

        client._log_order_update(order, prev_status=None)
        self.assertEqual(len(logs), 1)
        self.assertIn("filled 501.25", logs[0])


if __name__ == "__main__":
    unittest.main()
