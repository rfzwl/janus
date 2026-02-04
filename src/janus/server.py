import sys
import logging
import argparse
from threading import Event

from vnpy.event import EventEngine
from vnpy.trader.engine import MainEngine
from vnpy.trader.event import EVENT_LOG
from vnpy.trader.object import LogData
from vnpy_rpcservice import RpcServiceApp

from .gateway.webull.webull_gateway import WebullOfficialGateway
from .gateway.ib.ib_gateway import JanusIbGateway
from .config import ConfigLoader
from .symbol_registry import SymbolRegistry
from vnpy.trader.constant import Exchange

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
        self.rpc_engine.server.register(self.send_order_intent)
        self.rpc_engine.server.register(self.harmony)

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

    def send_order_intent(self, req: dict, gateway_name: str) -> str:
        if not isinstance(req, dict):
            raise ValueError("Order intent must be a dict")

        broker = self.account_broker.get(gateway_name)
        if not broker:
            raise ValueError(f"Unknown account: {gateway_name}")

        symbol = req.get("symbol")
        if not symbol:
            raise ValueError("Order intent missing symbol")

        intent = dict(req)

        if broker == "webull":
            record = self.symbol_registry.ensure_webull_symbol(symbol)
            intent["symbol"] = record.webull_ticker or record.canonical_symbol
        elif broker == "ib":
            record = self.symbol_registry.get_by_canonical(symbol)
            if not record or not record.ib_conid:
                raise ValueError(f"IB conId missing for symbol {symbol}")
            intent["symbol"] = str(record.ib_conid)
            intent["exchange"] = Exchange.SMART

        return self.main_engine.send_order(intent, gateway_name)

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
