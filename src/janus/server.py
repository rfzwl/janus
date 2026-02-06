import sys
import logging
import argparse
from threading import Event
from typing import Any

from vnpy.event import EventEngine
from vnpy.trader.engine import MainEngine
from vnpy.trader.event import EVENT_LOG
from vnpy.trader.object import LogData, SubscribeRequest, CancelRequest
from vnpy_rpcservice import RpcServiceApp

from .gateway.webull.webull_gateway import WebullOfficialGateway
from .gateway.ib.ib_gateway import JanusIbGateway
from .config import ConfigLoader
from .symbol_registry import SymbolRegistry
from .trade_events_engine import TradeEventsEngine
from vnpy.trader.constant import Exchange, Direction, OrderType, Offset

logging.basicConfig(level=logging.WARNING, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
sys_logger = logging.getLogger("JanusBootstrap")

class JanusServer:
    def __init__(self, use_remote_ib: bool = False):
        self.config = ConfigLoader()
        self.event_engine = EventEngine()
        self.event_engine.register(EVENT_LOG, self._sanitize_log_event)
        self.main_engine = MainEngine(self.event_engine)
        self.stop_event = Event()
        self.symbol_registry = self._init_symbol_registry()
        self.account_broker = self._load_account_brokers()
        self.use_remote_ib = use_remote_ib
        self.trade_events_engine = self.main_engine.add_engine(TradeEventsEngine)

        # 1. 加载 App
        self.main_engine.add_app(RpcServiceApp)

        # 2. 注册 Gateway 类 (Map 结构)
        self.broker_map = {
            "webull": WebullOfficialGateway,
            "ib": JanusIbGateway,
            # "ib": IbGateway,
        }

        # 3. 获取 RPC 引擎
        self.rpc_engine = self.main_engine.get_engine("RpcService")
        
        if not self.rpc_engine:
            sys_logger.error("严重错误：无法加载 RPC 引擎！请检查 vnpy_rpcservice 是否安装正确。")
            sys_logger.error(f"Available Engines: {list(self.main_engine.engines.keys())}")
            sys.exit(1)

        self.rpc_engine.server.register(self.remote_exit)
        self.rpc_engine.server.register(self.sync_all)
        self.rpc_engine.server.register(self.sync_gateway)
        self.rpc_engine.server.register(self.send_order)
        self.rpc_engine.server.register(self.cancel_order)
        self.rpc_engine.server.register(self.harmony)
        self.rpc_engine.server.register(self.subscribe_bars)
        self.rpc_engine.server.register(self.unsubscribe_bars)
        self.rpc_engine.server.register(self.get_bar_snapshots)

    def _init_symbol_registry(self) -> SymbolRegistry:
        try:
            db_setting = self.config.get_database_setting()
            return SymbolRegistry(db_setting)
        except Exception as exc:
            sys_logger.error(f"Failed to initialize symbol registry: {exc}")
            sys.exit(1)

    def _load_account_brokers(self) -> dict:
        mapping = {}
        for acct in self.config.get_all_accounts():
            name = acct.get("name")
            broker = acct.get("broker", "").lower()
            if name:
                mapping[name] = broker
        return mapping

    @staticmethod
    def _parse_exchange(value) -> Exchange:
        if isinstance(value, Exchange):
            return value
        if not value:
            return Exchange.SMART
        if isinstance(value, str):
            upper = value.upper()
            for ex in Exchange:
                if ex.value.upper() == upper or ex.name.upper() == upper:
                    return ex
        return Exchange.SMART

    def _parse_order_intent(self, req: dict) -> dict:
        if "direction" in req and "type" in req:
            intent = dict(req)
            intent["exchange"] = self._parse_exchange(intent.get("exchange"))
            if "volume" not in intent or intent["volume"] is None:
                raise ValueError("Order intent missing volume")
            intent["volume"] = float(intent["volume"])

            order_type = intent.get("type")
            if not isinstance(order_type, OrderType):
                raise ValueError("Order intent type must be OrderType")

            if order_type == OrderType.MARKET:
                intent.setdefault("price", 0)
            elif order_type == OrderType.LIMIT:
                if intent.get("price") is None:
                    raise ValueError("Limit order missing price")
                intent["price"] = float(intent["price"])
            elif order_type == OrderType.STOP:
                stop_price = intent.get("stop_price")
                if stop_price is None:
                    stop_price = intent.get("price")
                if stop_price is None:
                    raise ValueError("Stop order missing stop_price")
                intent["stop_price"] = float(stop_price)
                intent["price"] = float(stop_price)
                if intent.get("limit_price") is not None:
                    intent["limit_price"] = float(intent["limit_price"])
            else:
                raise ValueError(f"Unsupported order type: {order_type}")

            return intent

        action = req.get("action") or req.get("command") or req.get("side")
        if not action:
            raise ValueError("Order intent missing action")

        symbol = req.get("symbol")
        if not symbol:
            raise ValueError("Order intent missing symbol")

        volume = req.get("volume")
        if volume is None:
            raise ValueError("Order intent missing volume")

        price = req.get("price")
        stop_price = req.get("stop_price")
        limit_price = req.get("limit_price")
        exchange = self._parse_exchange(req.get("exchange"))
        offset = req.get("offset", Offset.OPEN)

        action = str(action).lower()
        volume = float(volume)
        intent: dict[str, Any] = {
            "symbol": symbol,
            "exchange": exchange,
            "volume": volume,
            "offset": offset,
        }

        if "reference" in req:
            intent["reference"] = req["reference"]

        if action in ("buy", "sell"):
            intent["direction"] = Direction.LONG if action == "buy" else Direction.SHORT
            if price is None:
                intent["type"] = OrderType.MARKET
                intent["price"] = 0
            else:
                intent["type"] = OrderType.LIMIT
                intent["price"] = float(price)
        elif action in ("bstop", "sstop"):
            intent["direction"] = Direction.LONG if action == "bstop" else Direction.SHORT
            if stop_price is None:
                if price is None:
                    raise ValueError("Stop order missing stop_price")
                stop_price = price
            intent["type"] = OrderType.STOP
            intent["stop_price"] = float(stop_price)
            intent["price"] = float(stop_price)
            if limit_price is not None:
                intent["limit_price"] = float(limit_price)
        else:
            raise ValueError(f"Unsupported order action: {action}")

        return intent

    def send_order(self, req: dict, gateway_name: str) -> str:
        if not isinstance(req, dict):
            raise ValueError("Order intent must be a dict")

        broker = self.account_broker.get(gateway_name)
        if not broker:
            raise ValueError(f"Unknown account: {gateway_name}")

        intent = self._parse_order_intent(req)
        symbol = intent.get("symbol")

        if broker == "webull":
            record = self.symbol_registry.ensure_webull_symbol(symbol)
            canonical_symbol = record.canonical_symbol
            intent["symbol"] = record.webull_ticker or canonical_symbol
            if intent.get("direction") == Direction.SHORT:
                long_volume = self._get_position_volume(
                    gateway_name=gateway_name,
                    symbol=canonical_symbol,
                    direction=Direction.LONG,
                )
                if long_volume <= 0:
                    intent["webull_side"] = "SHORT"
        elif broker == "ib":
            conid = self._resolve_ib_conid(symbol)
            intent["symbol"] = str(conid)
            if self._is_future_symbol(symbol) and intent.get("exchange") == Exchange.SMART:
                intent["exchange"] = Exchange.CME
            else:
                intent["exchange"] = Exchange.SMART

        return self.main_engine.send_order(intent, gateway_name)

    def _get_position_volume(
        self,
        gateway_name: str,
        symbol: str,
        direction: Direction,
    ) -> float:
        for position in self.main_engine.get_all_positions():
            if position.gateway_name != gateway_name:
                continue
            if position.symbol != symbol:
                continue
            if position.direction != direction:
                continue
            return float(position.volume or 0)
        return 0.0

    def cancel_order(self, vt_orderid: str) -> str:
        if not isinstance(vt_orderid, str) or not vt_orderid:
            raise ValueError("Cancel requires vt_orderid")
        order = self.main_engine.get_order(vt_orderid)
        if not order:
            raise ValueError(f"Order not found: {vt_orderid}")
        req = CancelRequest(orderid=order.orderid, symbol=order.symbol, exchange=order.exchange)
        self.main_engine.cancel_order(req, order.gateway_name)
        return f"Cancel request sent: {vt_orderid}"

    def subscribe_bars(self, symbols: list[str] | str, account: str, rth: bool = False) -> str:
        target_account = self._resolve_ib_account(account)
        gateway = self.main_engine.get_gateway(target_account)
        if not gateway:
            raise ValueError(f"Gateway not found: {target_account}")

        if isinstance(symbols, str):
            symbols = [symbols]
        if not symbols:
            raise ValueError("No symbols provided for bar subscription")

        market_settings = self._get_ib_market_data_settings(target_account)
        what_to_show = self._normalize_what_to_show(market_settings.get("what_to_show"))
        use_rth = bool(rth)

        subscribed = []
        for symbol in symbols:
            conid = self._resolve_ib_conid(symbol)
            req = SubscribeRequest(symbol=str(conid), exchange=Exchange.SMART)
            gateway.subscribe_bars(req, what_to_show=what_to_show, use_rth=use_rth)
            subscribed.append(symbol)

        routed = f" (routed from {account})" if target_account != account else ""
        return (
            f"IB bars subscribed: {', '.join(subscribed)} "
            f"(account {target_account}, rth={use_rth}){routed}"
        )

    def unsubscribe_bars(self, symbols: list[str] | str, account: str) -> str:
        target_account = self._resolve_ib_account(account)
        gateway = self.main_engine.get_gateway(target_account)
        if not gateway:
            raise ValueError(f"Gateway not found: {target_account}")

        if isinstance(symbols, str):
            symbols = [symbols]
        if not symbols:
            raise ValueError("No symbols provided for bar unsubscribe")

        unsubscribed = []
        for symbol in symbols:
            conid = self._resolve_ib_conid(symbol)
            req = SubscribeRequest(symbol=str(conid), exchange=Exchange.SMART)
            gateway.unsubscribe_bars(req)
            unsubscribed.append(symbol)

        routed = f" (routed from {account})" if target_account != account else ""
        return (
            f"IB bars unsubscribed: {', '.join(unsubscribed)} "
            f"(account {target_account}){routed}"
        )

    def get_bar_snapshots(self, account: str) -> dict:
        target_account = self._resolve_ib_account(account or "")
        gateway = self.main_engine.get_gateway(target_account)
        if not gateway:
            raise ValueError(f"Gateway not found: {target_account}")

        bar_cache = getattr(gateway, "bar_cache", {}) or {}
        snapshots = {}
        for symbol, payload in bar_cache.items():
            if isinstance(payload, dict):
                snapshots[symbol] = dict(payload)
        return snapshots

    def _get_ib_market_data_settings(self, account: str) -> dict:
        for acct in self.config.get_all_accounts():
            if acct.get("name") == account:
                setting = acct.get("ib_market_data") or {}
                return setting if isinstance(setting, dict) else {}
        return {}

    def _resolve_ib_account(self, account: str) -> str:
        if self.account_broker.get(account) == "ib":
            return account
        for acct in self.config.get_all_accounts():
            if acct.get("broker", "").lower() == "ib":
                name = acct.get("name")
                if name:
                    return name
        raise ValueError("No IB account configured for bars subscription")

    @staticmethod
    def _normalize_what_to_show(value: Any) -> str:
        if not value:
            return "TRADES"
        upper = str(value).upper()
        if upper not in ("TRADES", "MIDPOINT", "BID", "ASK"):
            return "TRADES"
        return upper

    def _subscribe_default_bars(self, account: str) -> None:
        settings = self._get_ib_market_data_settings(account)
        symbols = settings.get("default_symbols") or []
        if not symbols:
            return
        if not isinstance(symbols, list):
            symbols = [symbols]

        gateway = self.main_engine.get_gateway(account)
        if not gateway:
            return

        what_to_show = self._normalize_what_to_show(settings.get("what_to_show"))
        use_rth = bool(settings.get("use_rth", False))

        for symbol in symbols:
            try:
                conid = self._resolve_ib_conid(symbol)
            except Exception as exc:
                self.main_engine.write_log(
                    f"IB bars default skipped for {symbol}: {exc}"
                )
                continue
            req = SubscribeRequest(symbol=str(conid), exchange=Exchange.SMART)
            gateway.subscribe_bars(req, what_to_show=what_to_show, use_rth=use_rth)

    def _resolve_ib_conid(self, symbol: str) -> int:
        record = self.symbol_registry.get_by_canonical(symbol)
        if record and record.ib_conid:
            return int(record.ib_conid)

        future_parts = self._parse_future_symbol(symbol)
        if future_parts:
            root, _yymm, yyyymm = future_parts
            return self._resolve_ib_future_conid(symbol, root, yyyymm)

        gateway = self._get_gateway_for_broker("ib")
        if not gateway:
            raise ValueError(f"IB conId missing for symbol {symbol}: no connected IB gateway")
        api = getattr(gateway, "api", None)
        if not api or not getattr(api, "status", False):
            raise ValueError(f"IB conId missing for symbol {symbol}: IB gateway not connected")

        details = gateway.request_contract_details(symbol=symbol)
        if not details:
            raise ValueError(f"IB lookup failed for symbol {symbol}: no contract details")

        matches = []
        for detail in details:
            contract = getattr(detail, "contract", None)
            if not contract:
                continue
            sec_type = getattr(contract, "secType", None)
            currency = getattr(contract, "currency", None)
            conid = getattr(contract, "conId", None)
            if sec_type != "STK":
                continue
            if not currency or currency.upper() != "USD":
                continue
            if not conid:
                continue
            matches.append((detail, contract))

        if not matches:
            raise ValueError(f"IB lookup failed for symbol {symbol}: no US equity match")
        if len(matches) != 1:
            raise ValueError(
                f"IB lookup failed for symbol {symbol}: ambiguous ({len(matches)} matches)"
            )

        detail, contract = matches[0]
        conid = int(contract.conId)
        description = getattr(detail, "longName", None)
        record = self.symbol_registry.ensure_ib_symbol(
            symbol=symbol,
            conid=conid,
            currency=getattr(contract, "currency", None),
            description=description,
        )
        return int(record.ib_conid or conid)

    @staticmethod
    def _parse_future_symbol(symbol: str) -> tuple[str, str, str] | None:
        if not symbol or "." not in symbol:
            return None
        root, suffix = symbol.split(".", 1)
        if len(suffix) != 4 or not suffix.isdigit():
            return None
        yyyymm = f"20{suffix}"
        return root.upper(), suffix, yyyymm

    @staticmethod
    def _is_future_symbol(symbol: str) -> bool:
        return JanusServer._parse_future_symbol(symbol) is not None

    def _resolve_ib_future_conid(self, canonical: str, root: str, expiry: str) -> int:
        gateway = self._get_gateway_for_broker("ib")
        if not gateway:
            raise ValueError(f"IB conId missing for symbol {canonical}: no connected IB gateway")
        api = getattr(gateway, "api", None)
        if not api or not getattr(api, "status", False):
            raise ValueError(f"IB conId missing for symbol {canonical}: IB gateway not connected")

        exchange = "CME"
        currency = "USD"
        details = gateway.request_contract_details(
            symbol=root,
            exchange=exchange,
            currency=currency,
            sec_type="FUT",
            expiry=expiry,
        )
        if not details:
            exchange = "GLOBEX"
            details = gateway.request_contract_details(
                symbol=root,
                exchange=exchange,
                currency=currency,
                sec_type="FUT",
                expiry=expiry,
            )
        if not details:
            raise ValueError(
                "IB lookup failed for symbol "
                f"{canonical}: no futures match (root={root}, expiry={expiry}, "
                f"exchange={exchange}, currency={currency})"
            )

        matches = []
        for detail in details:
            contract = getattr(detail, "contract", None)
            if not contract:
                continue
            sec_type = getattr(contract, "secType", None)
            conid = getattr(contract, "conId", None)
            contract_expiry = getattr(contract, "lastTradeDateOrContractMonth", None)
            if sec_type != "FUT" or not conid:
                continue
            if contract_expiry:
                contract_expiry_str = str(contract_expiry)
                if not contract_expiry_str.startswith(expiry):
                    continue
            matches.append((detail, contract))

        if len(matches) != 1:
            raise ValueError(
                f"IB lookup failed for symbol {canonical}: ambiguous ({len(matches)} matches)"
            )

        detail, contract = matches[0]
        conid = int(contract.conId)
        record = self.symbol_registry.ensure_ib_symbol(
            symbol=canonical,
            conid=conid,
            asset_class="FUTURE",
            currency=getattr(contract, "currency", None),
            description=getattr(contract, "symbol", None),
        )
        return int(record.ib_conid or conid)

    def sync_all(self):
        """主动触发所有 Gateway 同步数据"""
        for gateway_name in self.main_engine.gateways.keys():
            gateway = self.main_engine.get_gateway(gateway_name)
            if gateway:
                self._sync_gateway(gateway)
        return "Sync request sent to all gateways."

    def sync_gateway(self, gateway_name: str):
        """主动触发指定 Gateway 同步数据"""
        gateway = self.main_engine.get_gateway(gateway_name)
        if not gateway:
            return f"Gateway not found: {gateway_name}"
        self._sync_gateway(gateway)
        return f"Sync request sent to {gateway_name}."

    def harmony(self):
        """Fill missing symbol registry fields for connected brokers."""
        connected = self._connected_brokers()
        if not connected:
            return "Harmony skipped: no connected brokers."

        summary_lines = []
        errors = []

        if "webull" in connected:
            updated = []
            skipped = []
            for record in self.symbol_registry.list_records():
                if record.webull_ticker:
                    continue
                if record.asset_class == "FUTURE":
                    skipped.append(f"{record.canonical_symbol} (future)")
                    continue
                try:
                    self.symbol_registry.ensure_webull_symbol(record.canonical_symbol)
                    updated.append(record.canonical_symbol)
                except Exception as exc:
                    errors.append(f"Webull update failed for {record.canonical_symbol}: {exc}")
                    break
            summary_lines.append(
                f"Webull updated: {len(updated)}; skipped: {len(skipped)}"
            )
            if updated:
                summary_lines.append(f"  Webull updated symbols: {', '.join(updated)}")

        if "ib" in connected and not errors:
            gateway = self._get_gateway_for_broker("ib")
            if not gateway:
                summary_lines.append("IB skipped: no connected IB gateway.")
            else:
                api = getattr(gateway, "api", None)
                updated = []
                skipped = []
                missing = []

                if not api or not getattr(api, "status", False):
                    summary_lines.append("IB skipped: gateway not connected.")
                else:
                    for record in self.symbol_registry.list_records():
                        if record.ib_conid:
                            continue
                        if record.asset_class != "EQUITY":
                            skipped.append(f"{record.canonical_symbol} (non-equity)")
                            continue
                        currency = (record.currency or "USD").upper()
                        if currency != "USD":
                            skipped.append(f"{record.canonical_symbol} (non-US {currency})")
                            continue
                        try:
                            results = api.request_contract_details(
                                symbol=record.canonical_symbol,
                                exchange="SMART",
                                currency="USD",
                                sec_type="STK",
                            )
                        except Exception as exc:
                            errors.append(f"IB lookup failed for {record.canonical_symbol}: {exc}")
                            break

                        if len(results) == 1:
                            detail = results[0]
                            conid = getattr(detail.contract, "conId", None)
                            sec_type = getattr(detail.contract, "secType", None)
                            if not conid or sec_type != "STK":
                                skipped.append(f"{record.canonical_symbol} (invalid contract)")
                                continue
                            try:
                                self.symbol_registry.ensure_ib_symbol(
                                    symbol=record.canonical_symbol,
                                    conid=conid,
                                    currency="USD",
                                    description=getattr(detail, "longName", None),
                                )
                            except Exception as exc:
                                errors.append(
                                    f"IB update failed for {record.canonical_symbol}: {exc}"
                                )
                                break
                            updated.append(record.canonical_symbol)
                        elif len(results) == 0:
                            missing.append(record.canonical_symbol)
                        else:
                            skipped.append(f"{record.canonical_symbol} (ambiguous)")

                    summary_lines.append(
                        f"IB updated: {len(updated)}; missing: {len(missing)}; skipped: {len(skipped)}"
                    )
                    if updated:
                        summary_lines.append(f"  IB updated symbols: {', '.join(updated)}")
                    if missing:
                        summary_lines.append(f"  IB missing symbols: {', '.join(missing)}")
                    if skipped:
                        summary_lines.append(f"  IB skipped symbols: {', '.join(skipped)}")

        if errors:
            raise RuntimeError("Harmony failed: " + "; ".join(errors))

        return "\n".join(summary_lines) if summary_lines else "Harmony completed."

    @staticmethod
    def _sync_gateway(gateway):
        if hasattr(gateway, "query_account"):
            gateway.query_account()
        if hasattr(gateway, "query_position"):
            gateway.query_position()
        if hasattr(gateway, "query_open_orders"):
            gateway.query_open_orders()

    def _connected_brokers(self) -> set[str]:
        brokers = set()
        for gateway_name in self.main_engine.gateways.keys():
            broker = self.account_broker.get(gateway_name)
            if broker:
                brokers.add(broker)
        return brokers

    def _get_gateway_for_broker(self, broker: str):
        for gateway_name in self.main_engine.gateways.keys():
            if self.account_broker.get(gateway_name) == broker:
                return self.main_engine.get_gateway(gateway_name)
        return None

    def _sanitize_log_event(self, event) -> None:
        """
        修复日志事件数据的格式问题。
        这是一个"中间人"函数，在日志交给 MainEngine 处理前先清洗一遍。
        """
        data = event.data

        # --- 修复 1: 解决 AttributeError (崩溃元凶) ---
        # 如果数据是纯字符串 (通常由 Loguru 拦截 Webull 日志产生)
        if isinstance(data, str):
            # 原地将 event.data 替换为标准的 LogData 对象
            # 这样 MainEngine 收到后就能正常读取 .level 属性了
            event.data = LogData(
                msg=data,
                gateway_name="WebullSDK",
                level=logging.INFO
            )
            return  # 处理完毕，直接返回

        # --- 修复 2: 解决 KeyError (Loguru 格式化错误) ---
        # 如果数据已经是 LogData，但内容里包含花括号 { }
        # (通常是 Webull 打印了字典类型的调试信息)
        if isinstance(data, LogData):
            try:
                # 将 { 转义为 {{，将 } 转义为 }}
                # 这样 Loguru 就会把它当做普通字符，而不是格式化占位符
                if "{" in str(data.msg):
                    data.msg = str(data.msg).replace("{", "{{").replace("}", "}}")
            except Exception:
                pass

    def remote_exit(self):
        """
        供客户端调用的远程关闭函数
        """
        msg = "收到客户端远程关闭指令 (Remote Exit) ..."
        self.main_engine.write_log(msg)
        self.stop_event.set()
        return "Server is shutting down..."

    def run(self):
        sys_logger.info("Starting Janus Server ...")

        # 4. 循环连接所有配置的账户
        accounts = self.config.get_all_accounts()
        for acct_config in accounts:
            broker_type = acct_config.get("broker", "").lower()
            acct_name = acct_config.get("name", "Unknown")

            gateway_class = self.broker_map.get(broker_type)
            if not gateway_class:
                self.main_engine.write_log(
                    f"WARNING: Unsupported broker type {broker_type} for account {acct_name}."
                )
                continue

            sys_logger.info(f"Connecting to account: {acct_name} ({broker_type})")
            self.main_engine.add_gateway(gateway_class, acct_name)
            acct_setting = dict(acct_config)
            acct_setting["symbol_registry"] = self.symbol_registry
            if broker_type == "ib" and self.use_remote_ib:
                host_remote = acct_setting.get("host_remote")
                port_remote = acct_setting.get("port_remote")
                if host_remote:
                    acct_setting["host"] = host_remote
                if port_remote:
                    acct_setting["port"] = port_remote
            try:
                self.main_engine.connect(acct_setting, acct_name)
            except Exception as exc:
                sys_logger.error(f"Connect failed for {acct_name} ({broker_type}): {exc}")
                if not self._prompt_continue():
                    self.shutdown()
            if broker_type == "webull":
                gateway = self.main_engine.get_gateway(acct_name)
                if gateway:
                    self.trade_events_engine.register_gateway(gateway, acct_setting)
            if broker_type == "ib":
                self._subscribe_default_bars(acct_name)

        # 5. 启动 RPC 服务
        rpc_setting = self.config.get_rpc_setting()
        try:
            self.rpc_engine.start(
                rep_address=rpc_setting["rep_address"],
                pub_address=rpc_setting["pub_address"]
            )
            self.main_engine.write_log(
                f"Janus Server Ready. RPC at {rpc_setting['rep_address']}"
            )
        except Exception as e:
            sys_logger.error(f"Failed to start RPC service: {e}")
            self.shutdown()
        
        # 6. 主循环
        try:
            while not self.stop_event.is_set():
                self.stop_event.wait(1.0)
        except KeyboardInterrupt:
            sys_logger.info("KeyboardInterrupt received.")
        finally:
            self.shutdown()

    def shutdown(self):
        sys_logger.info("Shutting down...")
        self.main_engine.close()
        sys.exit(0)

    @staticmethod
    def _prompt_continue() -> bool:
        try:
            answer = input("Continue startup? [y/N]: ").strip().lower()
        except EOFError:
            return False
        return answer in ("y", "yes")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Janus Server")
    parser.add_argument("-r", "--remote", action="store_true", help="Use remote IB host/port")
    args = parser.parse_args()
    JanusServer(use_remote_ib=args.remote).run()
