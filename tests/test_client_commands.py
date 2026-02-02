import sys
from pathlib import Path
import unittest
from unittest import mock

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from janus.client import JanusRpcClient


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


class ClientCommandTests(unittest.TestCase):
    def setUp(self):
        self.config_patcher = mock.patch("janus.client.ConfigLoader", FakeConfigLoader)
        self.rpc_init_patcher = mock.patch("janus.client.RpcClient.__init__", lambda self: None)
        self.config_patcher.start()
        self.rpc_init_patcher.start()

    def tearDown(self):
        self.config_patcher.stop()
        self.rpc_init_patcher.stop()

    def test_broker_set_default(self):
        client = CapturingClient()
        logs = []
        client.process_command("broker acct2", logs.append)
        self.assertEqual(client.default_gateway, "acct2")

    def test_broker_route_does_not_change_default(self):
        client = CapturingClient()
        logs = []
        client.process_command("broker acct2 buy AAPL 1 10", logs.append)
        self.assertEqual(client.default_gateway, "acct1")
        self.assertEqual(len(client.sent_orders), 1)
        self.assertEqual(client.sent_orders[0][1], "acct2")


if __name__ == "__main__":
    unittest.main()
