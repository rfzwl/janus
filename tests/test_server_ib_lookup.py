import sys
from pathlib import Path
import unittest
from types import SimpleNamespace

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from janus.server import JanusServer


class DummyRegistry:
    def __init__(self, record=None):
        self._record = record
        self.ensure_calls = []

    def get_by_canonical(self, symbol: str):
        return self._record

    def ensure_ib_symbol(self, symbol, conid, currency=None, description=None):
        self.ensure_calls.append((symbol, conid, currency, description))
        return SimpleNamespace(ib_conid=conid)


class DummyContract:
    def __init__(self, conid, sec_type="STK", currency="USD"):
        self.conId = conid
        self.secType = sec_type
        self.currency = currency


class DummyDetail:
    def __init__(self, contract, long_name=None):
        self.contract = contract
        self.longName = long_name


class DummyGateway:
    def __init__(self, details, connected=True):
        self.api = SimpleNamespace(status=connected)
        self._details = details

    def request_contract_details(self, **_kwargs):
        return self._details


class IbLookupTests(unittest.TestCase):
    def setUp(self):
        self.server = JanusServer.__new__(JanusServer)

    def test_resolve_ib_conid_uses_lookup(self):
        registry = DummyRegistry()
        detail = DummyDetail(DummyContract(12345), long_name="Apple Inc.")
        gateway = DummyGateway([detail])
        self.server.symbol_registry = registry
        self.server._get_gateway_for_broker = lambda _broker: gateway

        conid = self.server._resolve_ib_conid("AAPL")

        self.assertEqual(conid, 12345)
        self.assertEqual(len(registry.ensure_calls), 1)
        self.assertEqual(registry.ensure_calls[0][0], "AAPL")

    def test_resolve_ib_conid_ambiguous_raises(self):
        registry = DummyRegistry()
        details = [
            DummyDetail(DummyContract(1)),
            DummyDetail(DummyContract(2)),
        ]
        gateway = DummyGateway(details)
        self.server.symbol_registry = registry
        self.server._get_gateway_for_broker = lambda _broker: gateway

        with self.assertRaises(ValueError):
            self.server._resolve_ib_conid("AAPL")


if __name__ == "__main__":
    unittest.main()
