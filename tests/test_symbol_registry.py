import sys
from pathlib import Path
import unittest
from unittest import mock

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from janus.symbol_registry import SymbolRegistry, SymbolRecord


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.conn.statements.append((sql, params))

    def fetchall(self):
        return list(self.conn.rows)


class FakeConn:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.statements = []

    def cursor(self):
        return FakeCursor(self)


class SymbolRegistryTests(unittest.TestCase):
    def _make_registry(self, rows=None):
        fake_conn = FakeConn(rows=rows)
        with mock.patch.object(SymbolRegistry, "_connect", return_value=fake_conn):
            registry = SymbolRegistry(settings={})
        return registry, fake_conn

    def test_ensure_webull_symbol_inserts_defaults(self):
        registry, _ = self._make_registry()
        record = registry.ensure_webull_symbol(" aapl ", description="Apple Inc.")

        self.assertEqual(record.canonical_symbol, "AAPL")
        self.assertEqual(record.webull_ticker, "AAPL")
        self.assertEqual(record.asset_class, "EQUITY")
        self.assertEqual(record.currency, "USD")
        self.assertEqual(record.description, "Apple Inc.")

        by_symbol = registry.get_by_canonical("AAPL")
        self.assertIs(by_symbol, record)
        self.assertEqual(len(registry.list_records()), 1)

    def test_ensure_webull_symbol_keeps_first_description(self):
        rows = [("AAPL", "EQUITY", "USD", None, "AAPL", "First")]
        registry, _ = self._make_registry(rows=rows)

        record = registry.ensure_webull_symbol("AAPL", description="Second")
        self.assertEqual(record.description, "First")

    def test_ensure_ib_symbol_conid_conflict_returns_existing(self):
        registry, _ = self._make_registry()
        first = registry.ensure_ib_symbol("AAPL", conid=101)
        second = registry.ensure_ib_symbol("MSFT", conid=101)

        self.assertIs(second, first)
        self.assertEqual(first.canonical_symbol, "AAPL")

    def test_ensure_ib_symbol_fills_missing_conid(self):
        rows = [("AAPL", "EQUITY", "USD", None, None, None)]
        registry, _ = self._make_registry(rows=rows)

        record = registry.ensure_ib_symbol("AAPL", conid=202)
        self.assertEqual(record.ib_conid, 202)
        self.assertIs(registry.get_by_ib_conid(202), record)

    def test_get_by_webull_ticker(self):
        rows = [("AAPL", "EQUITY", "USD", None, "WB", "Apple Inc.")]
        registry, _ = self._make_registry(rows=rows)

        record = registry.get_by_webull_ticker("wb")
        self.assertIsNotNone(record)
        self.assertEqual(record.canonical_symbol, "AAPL")


if __name__ == "__main__":
    unittest.main()
