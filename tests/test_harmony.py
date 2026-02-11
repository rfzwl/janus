import os
import sys
import tempfile
from pathlib import Path
import unittest
from types import SimpleNamespace

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

_HOME_DIR = tempfile.TemporaryDirectory()
os.environ["HOME"] = _HOME_DIR.name
os.makedirs(os.path.join(_HOME_DIR.name, ".vntrader", "log"), exist_ok=True)

from janus.server import JanusServer
from janus.symbol_registry import SymbolRecord


class FakeRegistry:
    def __init__(self, records):
        self._records = records
        self.ib_updates = []
        self.webull_updates = []

    def list_records(self):
        return list(self._records)

    def ensure_webull_symbol(self, canonical_symbol, *args, **kwargs):
        self.webull_updates.append(canonical_symbol)

    def ensure_ib_symbol(self, symbol, conid, currency, description=None, **kwargs):
        self.ib_updates.append((symbol, conid, currency, description))


class FakeDetail:
    def __init__(self, conid, sec_type="STK", long_name=None):
        self.contract = SimpleNamespace(conId=conid, secType=sec_type)
        self.longName = long_name


class FakeIbApi:
    def __init__(self, responses):
        self.status = True
        self._responses = responses

    def request_contract_details(self, symbol, exchange, currency, sec_type):
        return self._responses.get(symbol, [])


class FakeGateway:
    def __init__(self, api):
        self.api = api


class FakeServer:
    def __init__(self, registry, api):
        self.symbol_registry = registry
        self._gateway = FakeGateway(api)

    def _connected_brokers(self):
        return {"ib"}

    def _get_gateway_for_broker(self, broker):
        if broker == "ib":
            return self._gateway
        return None


class HarmonyTests(unittest.TestCase):
    def test_harmony_updates_ib_conid(self):
        record = SymbolRecord(
            canonical_symbol="AAPL",
            asset_class="EQUITY",
            currency="USD",
            ib_conid=None,
            webull_ticker=None,
            description=None,
        )
        registry = FakeRegistry([record])
        api = FakeIbApi({"AAPL": [FakeDetail(123, "STK", "Apple Inc.")]})
        server = FakeServer(registry, api)

        result = JanusServer.harmony(server)

        self.assertIn("IB updated: 1", result)
        self.assertEqual(registry.ib_updates[0][0], "AAPL")

    def test_harmony_marks_ambiguous(self):
        record = SymbolRecord(
            canonical_symbol="MSFT",
            asset_class="EQUITY",
            currency="USD",
            ib_conid=None,
            webull_ticker=None,
            description=None,
        )
        registry = FakeRegistry([record])
        api = FakeIbApi({"MSFT": [FakeDetail(1), FakeDetail(2)]})
        server = FakeServer(registry, api)

        result = JanusServer.harmony(server)

        self.assertIn("skipped: 1", result)
        self.assertEqual(registry.ib_updates, [])

    def test_harmony_treats_etf_and_stock_as_equity_like(self):
        for asset_class, symbol, conid in (
            ("ETF", "QQQ", 320227571),
            ("STOCK", "AAPL", 265598),
        ):
            with self.subTest(asset_class=asset_class):
                record = SymbolRecord(
                    canonical_symbol=symbol,
                    asset_class=asset_class,
                    currency="USD",
                    ib_conid=None,
                    webull_ticker=None,
                    description=None,
                )
                registry = FakeRegistry([record])
                api = FakeIbApi({symbol: [FakeDetail(conid, "STK", symbol)]})
                server = FakeServer(registry, api)

                result = JanusServer.harmony(server)

                self.assertIn("IB updated: 1", result)
                self.assertEqual(registry.ib_updates[0][0], symbol)


if __name__ == "__main__":
    unittest.main()
