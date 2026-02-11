import sys
from pathlib import Path
import unittest
from types import SimpleNamespace

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from janus.server import JanusServer


class DownloadInitialTests(unittest.TestCase):
    def setUp(self):
        self.server = JanusServer.__new__(JanusServer)
        self.server.symbol_registry = SimpleNamespace(normalize=lambda s: (s or "").strip().upper())

    def test_normalize_download_interval(self):
        self.assertEqual(JanusServer._normalize_download_interval("1"), "1m")
        self.assertEqual(JanusServer._normalize_download_interval("1m"), "1m")
        self.assertEqual(JanusServer._normalize_download_interval("D"), "d")
        self.assertEqual(JanusServer._normalize_download_interval("tick"), "tick")

    def test_download_initial_rejects_unsupported_interval(self):
        with self.assertRaises(ValueError) as ctx:
            self.server.download_initial("QQQ", "5s", "ib_z", replace=False)
        self.assertIn("only 1m is enabled now", str(ctx.exception))

    def test_download_initial_rejects_unsupported_interval_even_with_adjusted(self):
        with self.assertRaises(ValueError) as ctx:
            self.server.download_initial("QQQ", "5s", "ib_z", replace=False, adjusted=True)
        self.assertIn("only 1m is enabled now", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
