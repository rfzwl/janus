import threading
import sys
from typing import List, Dict, Callable, Any

from vnpy.rpc import RpcClient
from vnpy.trader.object import OrderData, SubscribeRequest
from vnpy.trader.constant import Direction, Exchange, OrderType, Offset, Status

from .tui import JanusTUI
from .config import ConfigLoader

class JanusRpcClient(RpcClient):
    def __init__(self):
        super().__init__()
        self.config = ConfigLoader()
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

        match parts[0]:
            case "buy" | "sell" | "short" | "cover":
                self._send_order_cmd(parts)
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

    def _send_order_cmd(self, parts: list):
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
            
            order_id = self.send_order(req, "WEBULL")
            self.log_callback(f"Order sent: {order_id}")
            
        except Exception as e:
            self.log_callback(f"Order Error: {e}")

    def stop_remote_server(self):
        try:
            if hasattr(self, "remote_exit"):
                res = self.remote_exit()
                print(res)
        except Exception as e:
            print(f"Remote exit failed: {e}")

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