import logging
import time
from threading import Event, Thread
from typing import Any, Dict, Optional

from vnpy.trader.engine import BaseEngine
from vnpy.trader.event import EVENT_TIMER
from vnpy.trader.object import LogData
from vnpy.trader.logger import DEBUG

try:
    from webull.trade.trade_events_client import TradeEventsClient
except Exception:  # pragma: no cover - optional import at runtime
    TradeEventsClient = None


class TradeEventsWorker:
    def __init__(self, gateway, settings: Dict[str, Any]) -> None:
        self.gateway = gateway
        self.gateway_name = gateway.gateway_name
        self.account_id = gateway.account_id
        self.app_key = gateway.app_key
        self.app_secret = gateway.app_secret
        self.region_id = settings.get("region_id", gateway.region_id)
        self.host = settings.get("host")
        self.enabled = settings.get("enabled", True)
        self.debounce_seconds = float(settings.get("debounce_seconds", 1.0))
        self.health_check_seconds = int(settings.get("health_check_seconds", 60))
        self.stale_seconds = int(settings.get("stale_seconds", 300))
        self.startup_grace_seconds = int(settings.get("startup_grace_seconds", 30))
        self.health_log_interval = int(settings.get("health_log_interval", 300))

        self._stop_event = Event()
        self._thread: Optional[Thread] = None
        self._logger = logging.getLogger(f"TradeEvents[{self.gateway_name}]")
        self._started_at: Optional[float] = None
        self._last_message_ts: Optional[float] = None
        self._last_health_check_ts: Optional[float] = None
        self._last_health_log_ts: Optional[float] = None
        self._last_health_state: Optional[str] = None

        if TradeEventsClient is None:
            raise RuntimeError("webull TradeEventsClient not available")

        self._client = TradeEventsClient(
            self.app_key,
            self.app_secret,
            region_id=self.region_id,
            host=self.host,
        )
        self._client.on_events_message = self._on_events_message
        self._client.on_log = self._on_log

    def start(self) -> None:
        if not self.enabled:
            self.gateway.write_log("Trade events disabled")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._started_at = time.time()
        self._last_message_ts = None
        self._thread = Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=1)
            if self._thread.is_alive():
                self.gateway.write_log("Trade events thread still running")

    def _run(self) -> None:
        if not self.account_id:
            self.gateway.write_log("Trade events skipped: missing account_id")
            return
        try:
            self.gateway.write_log("Trade events subscribing...")
            self._client.do_subscribe([self.account_id])
        except Exception as exc:
            self.gateway.write_log(f"Trade events stopped: {exc}")

    def _on_log(self, level: int, message: str) -> None:
        if isinstance(message, str) and "eventType: Ping" in message:
            return
        msg = f"TradeEvents: {message}"
        self.gateway.write_log(msg)
        if self._logger:
            self._logger.log(level, message)

    def _on_events_message(self, event_type, subscribe_type, payload, response) -> None:
        if self._stop_event.is_set():
            return
        self._last_message_ts = time.time()
        try:
            self.gateway.handle_trade_event(event_type, subscribe_type, payload, response)
        except Exception as exc:
            self.gateway.write_log(f"Trade events callback error: {exc}")

    def maybe_health_check(self) -> None:
        if not self.enabled or self.health_check_seconds <= 0:
            return
        now = time.time()
        if self._last_health_check_ts and (now - self._last_health_check_ts) < self.health_check_seconds:
            return
        self._last_health_check_ts = now

        issue, message = self._health_issue(now)
        if not issue:
            self._last_health_state = None
            return

        if self._should_log(issue, now):
            self.gateway.on_log(LogData(msg=message, gateway_name=self.gateway_name, level=DEBUG))
            self._last_health_state = issue
            self._last_health_log_ts = now

    def _health_issue(self, now: float) -> tuple[Optional[str], str]:
        if not self._thread or not self._thread.is_alive():
            return "stopped", "Trade events health check: worker stopped"

        if self._started_at and self._last_message_ts is None:
            if (now - self._started_at) > self.startup_grace_seconds:
                return (
                    "no_messages",
                    f"Trade events health check: no messages in {self.startup_grace_seconds}s",
                )

        if self._last_message_ts and (now - self._last_message_ts) > self.stale_seconds:
            elapsed = int(now - self._last_message_ts)
            return (
                "stale",
                f"Trade events health check: no messages for {elapsed}s",
            )

        return None, ""

    def _should_log(self, issue: str, now: float) -> bool:
        if self._last_health_state != issue:
            return True
        if not self._last_health_log_ts:
            return True
        return (now - self._last_health_log_ts) >= self.health_log_interval


class TradeEventsEngine(BaseEngine):
    def __init__(self, main_engine, event_engine):
        super().__init__(main_engine, event_engine, "TradeEvents")
        self._workers: Dict[str, TradeEventsWorker] = {}
        self.event_engine.register(EVENT_TIMER, self.process_timer_event)

    def register_gateway(self, gateway, settings: Dict[str, Any]) -> None:
        if gateway.gateway_name in self._workers:
            return

        trade_settings = settings.get("trade_events") or {}
        if not isinstance(trade_settings, dict):
            trade_settings = {}

        enabled = trade_settings.get("enabled", True)
        if not enabled:
            gateway.write_log("Trade events disabled by config")
            return

        merged_settings = dict(trade_settings)
        merged_settings.setdefault("region_id", settings.get("region_id", gateway.region_id))

        try:
            worker = TradeEventsWorker(gateway, merged_settings)
        except Exception as exc:
            gateway.write_log(f"Trade events init failed: {exc}")
            return

        debounce_seconds = merged_settings.get("debounce_seconds")
        if debounce_seconds is not None and hasattr(gateway, "set_trade_events_debounce"):
            try:
                gateway.set_trade_events_debounce(float(debounce_seconds))
            except Exception:
                pass

        self._workers[gateway.gateway_name] = worker
        worker.start()

    def close(self) -> None:
        self.event_engine.unregister(EVENT_TIMER, self.process_timer_event)
        for worker in list(self._workers.values()):
            worker.stop()
        self._workers.clear()

    def process_timer_event(self, event) -> None:
        for worker in list(self._workers.values()):
            worker.maybe_health_check()
