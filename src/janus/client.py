import threading
import sys
from typing import List, Dict, Callable, Any, Optional

from vnpy.rpc import RpcClient
from vnpy.trader.object import OrderData, SubscribeRequest
from vnpy.trader.constant import Direction, Exchange, OrderType, Offset, Status

from .tui import JanusTUI
from .config import ConfigLoader

class JanusRpcClient(RpcClient):
    def __init__(self):
        super().__init__()
        self.config = ConfigLoader()
        self.available_gateways = self._load_gateways()
        self.default_gateway = self._resolve_default_gateway()
        self.orders: Dict[str, OrderData] = {}
        self.log_callback: Callable[[str], None] = lambda x: print(x) 
        self.tui = None

    def callback(self, topic: str, data: Any):
        """Standard vnpy RPC callback"""
        match topic:
            case "eOrder":
                if data.is_active():
                    self.orders[data.vt_orderid] = data
                elif data.vt_orderid in self.orders:
                    self.orders[data.vt_orderid] = data 
                
                if self.tui and self.tui.app.is_running:
                    self.tui.app.invalidate()
                    
            case "eLog":
                if self.tui:
                    self.tui.log(f"[Server] {data.msg}")

    def get_open_orders(self) -> List[OrderData]:
        return list(self.orders.values())

    def process_command(self, cmd: str, log_func: Callable):
        self.log_callback = log_func
        parts = cmd.split()
        if not parts:
            return

        if parts[0] == "help":
            self._handle_help_command(parts, log_func)
            return

        if parts[0] == "broker":
            self._handle_broker_command(parts, log_func)
            return

        self._dispatch_command(parts, log_func)

    def _dispatch_command(self, parts: list, log_func: Callable, gateway_override: Optional[str] = None):
        match parts[0]:
            case "buy" | "sell" | "short" | "cover":
                self._send_order_cmd(parts, gateway_override=gateway_override)
            case "cancel":
                if len(parts) < 2:
                    log_func("Usage: cancel <vt_orderid>")
                else:
                    self.cancel_order(parts[1])
                    log_func(f"Cancel request sent for {parts[1]}")
            case "connect":
                self.subscribe_topic("")
                log_func("Subscribed to all events.")
            case _:
                log_func(f"Unknown command: {parts[0]}")

    def _handle_broker_command(self, parts: list, log_func: Callable):
        if len(parts) == 1:
            log_func(f"Current broker: {self.default_gateway}")
            log_func("Usage: broker <name> | broker list")
            return

        subcmd = parts[1]
        if subcmd in ("list", "ls"):
            self._list_brokers(log_func)
            return

        broker = subcmd
        if broker not in self.available_gateways:
            log_func(f"Unknown broker: {broker}")
            self._list_brokers(log_func)
            return

        if len(parts) == 2:
            self.default_gateway = broker
            log_func(f"Default broker set to: {broker}")
            return

        self._dispatch_command(parts[2:], log_func, gateway_override=broker)

    def _handle_help_command(self, parts: list, log_func: Callable):
        if len(parts) == 1:
            log_func(self._help_text())
            return

        command = parts[1].lower()
        detail = self._help_for(command)
        if detail:
            log_func(detail)
        else:
            log_func(f"Unknown help topic: {command}")

    def _help_text(self) -> str:
        lines = [
            "Commands:",
            "  broker <name>           Switch default broker",
            "  broker list             List configured brokers (* is default)",
            "  broker <name> <cmd...>  Run a command on a broker without changing default",
            "  buy|sell|short|cover <symbol> <volume> <price>",
            "  cancel <vt_orderid>",
            "  connect                 Subscribe to all events",
            "  help [command]",
            "  exit|quit",
            "",
            f"Current broker: {self.default_gateway}",
        ]
        return "\n".join(lines)

    def _help_for(self, command: str) -> Optional[str]:
        details = {
            "broker": "\n".join([
                "Usage:",
                "  broker <name>",
                "  broker list",
                "  broker <name> <cmd...>",
                "Notes:",
                "  - Use 'broker list' to see configured brokers.",
                "  - 'broker <name> buy AAPL 1 100' routes only that command.",
            ]),
            "buy": "Usage: buy <symbol> <volume> <price>",
            "sell": "Usage: sell <symbol> <volume> <price>",
            "short": "Usage: short <symbol> <volume> <price>",
            "cover": "Usage: cover <symbol> <volume> <price>",
            "cancel": "Usage: cancel <vt_orderid>",
            "connect": "Usage: connect  (subscribe to all events)",
            "help": "Usage: help [command]",
            "exit": "Usage: exit  (stop remote server and quit)",
            "quit": "Usage: quit  (quit client)",
        }
        return details.get(command)

    def _list_brokers(self, log_func: Callable):
        if not self.available_gateways:
            log_func("No brokers configured.")
            return

        lines = ["Brokers:"]
        for name in self.available_gateways:
            marker = "*" if name == self.default_gateway else " "
            lines.append(f"{marker} {name}")
        log_func("\n".join(lines))

    def _send_order_cmd(self, parts: list, gateway_override: Optional[str] = None):
        if len(parts) < 4:
            self.log_callback("Usage: <action> <symbol> <volume> <price> [exchange]")
            return

        direction_map = {
            "buy": Direction.LONG, "sell": Direction.SHORT,
            "short": Direction.SHORT, "cover": Direction.LONG
        }
        
        try:
            symbol = parts[1]
            volume = float(parts[2])
            price = float(parts[3])
            
            req = {
                "symbol": symbol,
                "exchange": Exchange.SMART, 
                "direction": direction_map[parts[0]],
                "type": OrderType.LIMIT,
                "volume": volume,
                "price": price,
                "offset": Offset.OPEN 
            }
            
            broker = gateway_override or self.default_gateway
            order_id = self.send_order(req, broker)
            self.log_callback(f"Order sent: {order_id} (broker {broker})")
            
        except Exception as e:
            self.log_callback(f"Order Error: {e}")

    def stop_remote_server(self):
        try:
            if hasattr(self, "remote_exit"):
                res = self.remote_exit()
                print(res)
        except Exception as e:
            print(f"Remote exit failed: {e}")

    def _resolve_default_gateway(self) -> str:
        default_gateway = self.config.get_default_account_name()
        if default_gateway and (not self.available_gateways or default_gateway in self.available_gateways):
            return default_gateway
        if self.available_gateways:
            return self.available_gateways[0]
        return "WEBULL"

    def _load_gateways(self) -> List[str]:
        accounts = self.config.get_all_accounts()
        gateways = [acct.get("name") for acct in accounts if acct.get("name")]
        return gateways

def main():
    client = JanusRpcClient()
    rpc_conf = client.config.get_rpc_setting()
    history_file = client.config.get_history_setting() # 获取配置的路径
    
    req_addr = rpc_conf["rep_address"].replace("*", "localhost")
    sub_addr = rpc_conf["pub_address"].replace("*", "localhost")

    print(f"Connecting to RPC at {req_addr}...")
    
    client.subscribe_topic("")
    
    client.start(
        req_address=req_addr, 
        sub_address=sub_addr 
    )
    
    # 将历史记录路径传给 TUI
    tui = JanusTUI(client, history_path=history_file)
    client.tui = tui 
    
    try:
        tui.app.run()
    except Exception as e:
        print(f"UI Error: {e}")
    finally:
        client.stop()

if __name__ == "__main__":
    main()
