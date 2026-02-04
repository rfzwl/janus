import threading
import sys
from typing import List, Dict, Callable, Any, Optional

from vnpy.rpc import RpcClient
from vnpy.trader.object import OrderData, PositionData, SubscribeRequest
from vnpy.trader.constant import Direction, Exchange, OrderType, Offset, Status

from .tui import JanusTUI
from .config import ConfigLoader

class JanusRpcClient(RpcClient):
    def __init__(self):
        super().__init__()
        self.config = ConfigLoader()
        self.available_accounts = self._load_accounts()
        self.default_account = self._resolve_default_account()
        self.orders: Dict[str, OrderData] = {}
        self.positions: Dict[str, PositionData] = {}
        self.log_callback: Callable[[str], None] = lambda x: print(x) 
        self.tui = None

    def callback(self, topic: str, data: Any):
        """Standard vnpy RPC callback"""
        event_type = topic
        payload = data
        if hasattr(data, "type") and hasattr(data, "data"):
            event_type = data.type
            payload = data.data

        match event_type:
            case t if t.startswith("eOrder"):
                if payload.is_active():
                    self.orders[payload.vt_orderid] = payload
                elif payload.vt_orderid in self.orders:
                    self.orders[payload.vt_orderid] = payload
                
                if self.tui and self.tui.app.is_running:
                    self.tui.app.invalidate()
            case t if t.startswith("ePosition"):
                self.positions[payload.vt_positionid] = payload
                if self.tui and self.tui.app.is_running:
                    self.tui.app.invalidate()
                    
            case "eLog":
                if self.tui:
                    self.tui.log(f"[Server] {payload.msg}")

    def get_open_orders(self, account: Optional[str] = None) -> List[OrderData]:
        target_account = account or self.default_account
        return [order for order in self.orders.values() if order.gateway_name == target_account and order.is_active()]

    def get_positions(self, account: Optional[str] = None) -> List[PositionData]:
        target_account = account or self.default_account
        return [
            pos for pos in self.positions.values()
            if pos.gateway_name == target_account and (pos.volume or 0) > 0
        ]

    def process_command(self, cmd: str, log_func: Callable):
        self.log_callback = log_func
        parts = cmd.split()
        if not parts:
            return

        if parts[0] == "help":
            self._handle_help_command(parts, log_func)
            return

        if parts[0] in ("account", "broker"):
            self._handle_account_command(parts, log_func)
            return

        self._dispatch_command(parts, log_func)

    def _dispatch_command(self, parts: list, log_func: Callable, account_override: Optional[str] = None):
        match parts[0]:
            case "buy" | "sell" | "short" | "cover":
                self._send_order_cmd(parts, account_override=account_override)
            case "cancel":
                if len(parts) < 2:
                    log_func("Usage: cancel <vt_orderid>")
                else:
                    self.cancel_order(parts[1])
                    log_func(f"Cancel request sent for {parts[1]}")
            case "connect":
                self.subscribe_topic("")
                log_func("Subscribed to all events.")
            case "sync":
                self.request_sync(log_func=log_func)
            case "harmony":
                self.request_harmony(log_func=log_func)
            case _:
                log_func(f"Unknown command: {parts[0]}")

    def _handle_account_command(self, parts: list, log_func: Callable):
        if len(parts) == 1:
            log_func(f"Current account: {self.default_account}")
            log_func("Usage: account <name> | account list")
            return

        subcmd = parts[1]
        if subcmd in ("list", "ls"):
            self._list_accounts(log_func)
            return

        account = subcmd
        if account not in self.available_accounts:
            log_func(f"Unknown account: {account}")
            self._list_accounts(log_func)
            return

        if len(parts) == 2:
            self.default_account = account
            log_func(f"Default account set to: {account}")
            if self.tui:
                self.tui.update_prompt(account)
            self.request_sync(log_func=log_func)
            return

        self._dispatch_command(parts[2:], log_func, account_override=account)

    def _handle_help_command(self, parts: list, log_func: Callable):
        if len(parts) == 1:
            log_func(self._help_text())
            return

        command = parts[1].lower()
        if command == "broker":
            command = "account"
        detail = self._help_for(command)
        if detail:
            log_func(detail)
        else:
            log_func(f"Unknown help topic: {command}")

    def _help_text(self) -> str:
        lines = [
            "Commands:",
            "  account <name>           Switch default account",
            "  account list             List configured accounts (* is default)",
            "  account <name> <cmd...>  Run a command on an account without changing default",
            "  buy|sell|short|cover <symbol> <volume> <price>",
            "  cancel <vt_orderid>",
            "  connect                 Subscribe to all events",
            "  sync                    Sync account, positions, and open orders",
            "  harmony                 Fill missing symbol mappings (server-side)",
            "  help [command]",
            "  exit|quit",
            "",
            f"Current account: {self.default_account}",
        ]
        return "\n".join(lines)

    def _help_for(self, command: str) -> Optional[str]:
        details = {
            "account": "\n".join([
                "Usage:",
                "  account <name>",
                "  account list",
                "  account <name> <cmd...>",
                "Notes:",
                "  - Use 'account list' to see configured accounts.",
                "  - 'account <name> buy AAPL 1 100' routes only that command.",
            ]),
            "buy": "Usage: buy <symbol> <volume> <price>",
            "sell": "Usage: sell <symbol> <volume> <price>",
            "short": "Usage: short <symbol> <volume> <price>",
            "cover": "Usage: cover <symbol> <volume> <price>",
            "cancel": "Usage: cancel <vt_orderid>",
            "connect": "Usage: connect  (subscribe to all events)",
            "sync": "Usage: sync  (sync current account)",
            "harmony": "Usage: harmony  (fill missing symbol mappings)",
            "help": "Usage: help [command]",
            "exit": "Usage: exit  (stop remote server and quit)",
            "quit": "Usage: quit  (quit client)",
        }
        return details.get(command)

    def _list_accounts(self, log_func: Callable):
        if not self.available_accounts:
            log_func("No accounts configured.")
            return

        lines = ["Accounts:"]
        for name in self.available_accounts:
            marker = "*" if name == self.default_account else " "
            lines.append(f"{marker} {name}")
        log_func("\n".join(lines))

    def _send_order_cmd(self, parts: list, account_override: Optional[str] = None):
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
            
            account = account_override or self.default_account
            order_id = self.send_order_intent(req, account)
            self.log_callback(f"Order sent: {order_id} (account {account})")
            
        except Exception as e:
            self.log_callback(f"Order Error: {e}")

    def stop_remote_server(self):
        try:
            if hasattr(self, "remote_exit"):
                res = self.remote_exit()
                print(res)
        except Exception as e:
            print(f"Remote exit failed: {e}")

    def request_sync(self, account: Optional[str] = None, log_func: Optional[Callable[[str], None]] = None):
        target_account = account or self.default_account
        logger = log_func or self.log_callback or print
        if not hasattr(self, "_socket_req"):
            return
        remote = getattr(self, "sync_gateway", None)
        if not remote:
            logger("Sync not available on server.")
            return
        try:
            res = remote(target_account)
            if res is not None:
                logger(str(res))
            self._refresh_snapshot(target_account, logger)
        except Exception as e:
            logger(f"Sync failed: {e}")

    def request_harmony(self, log_func: Optional[Callable[[str], None]] = None):
        logger = log_func or self.log_callback or print
        if not hasattr(self, "_socket_req"):
            return
        remote = getattr(self, "harmony", None)
        if not remote:
            logger("Harmony not available on server.")
            return
        try:
            res = remote()
            if res is not None:
                logger(str(res))
        except Exception as e:
            logger(f"Harmony failed: {e}")

    def _refresh_snapshot(self, account: str, logger: Callable[[str], None]):
        try:
            active_orders = self.get_all_active_orders()
            positions = self.get_all_positions()
        except Exception as e:
            logger(f"Snapshot refresh failed: {e}")
            return

        self.orders = {
            order_id: order
            for order_id, order in self.orders.items()
            if order.gateway_name != account
        }
        for order in active_orders:
            if order.gateway_name == account:
                self.orders[order.vt_orderid] = order

        self.positions = {
            pos_id: pos
            for pos_id, pos in self.positions.items()
            if pos.gateway_name != account
        }
        for pos in positions:
            if pos.gateway_name == account:
                self.positions[pos.vt_positionid] = pos

        if self.tui and self.tui.app.is_running:
            self.tui.app.invalidate()

    def _resolve_default_account(self) -> str:
        default_account = self.config.get_default_account_name()
        if default_account and (not self.available_accounts or default_account in self.available_accounts):
            return default_account
        if self.available_accounts:
            return self.available_accounts[0]
        return "WEBULL"

    def _load_accounts(self) -> List[str]:
        accounts = self.config.get_all_accounts()
        account_names = [acct.get("name") for acct in accounts if acct.get("name")]
        return account_names

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
    client.request_sync(log_func=tui.log)
    
    try:
        tui.app.run()
    except Exception as e:
        print(f"UI Error: {e}")
    finally:
        client.stop()

if __name__ == "__main__":
    main()
