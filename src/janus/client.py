import threading
from typing import List, Dict, Callable
from vnpy.rpc import RpcClient
from vnpy.trader.object import OrderData, SubscribeRequest
from vnpy.trader.constant import Direction, Exchange, OrderType, Offset

from .tui import JanusTUI
from .config import ConfigLoader

class JanusRpcClient(RpcClient):
    def __init__(self):
        super().__init__()
        self.config = ConfigLoader()
        self.orders: Dict[str, OrderData] = {}
        self.log_callback: Callable[[str], None] = lambda x: print(x) # Placeholder
        self.tui = None

    def callback(self, topic: str, data: Any):
        """Standard vnpy RPC callback"""
        match topic:
            case "eOrder":
                # data is OrderData object (deserialized)
                if data.is_active():
                    self.orders[data.vt_orderid] = data
                elif data.vt_orderid in self.orders:
                    del self.orders[data.vt_orderid] # Remove finished orders
                
                # Invalidate UI to force redraw if needed immediately
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
                # manually re-trigger subscribe if needed, though init does it
                self.subscribe_topic("")
                log_func("Subscribed to all events.")
            case _:
                log_func(f"Unknown command: {parts[0]}")

    def _send_order_cmd(self, parts: list):
        # Syntax: buy <symbol> <volume> <price> [exchange]
        # Example: buy AAPL 10 150.0 SMART
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
            exchange_str = parts[4] if len(parts) > 4 else "SMART"
            
            # Simple wrapper for req
            req = {
                "symbol": symbol,
                "exchange": Exchange.SMART, # Default to SMART/IB
                "direction": direction_map[parts[0]],
                "type": OrderType.LIMIT,
                "volume": volume,
                "price": price,
                "offset": Offset.OPEN # Simplified
            }
            
            # vnpy RpcClient usually provides specific methods or generic 'send_order'
            # Note: standard RpcClient object doesn't have send_order helper locally 
            # unless we define it or use the generic remote call.
            # We assume server exposes 'send_order' via MainEngine or we use 'om' (OrderManagement)
            
            # Standard vnpy RPC logic: client calls function via __getattr__ or specific defined methods.
            # vnpy_rpcservice server exposes MainEngine's methods directly usually?
            # Actually, RpcServiceApp registers main_engine functions.
            # So we can call: client.send_order(req)
            
            # Note: OrderRequest needs to be passed. RpcClient serializes dicts nicely usually.
            # Let's construct a dict and pass it, hoping RpcServer side deserializes to object 
            # or we construct object here. 
            # Safest for RPC: pass dict, server handles it if wrapper exists. 
            # Or use standard vnpy Request objects.
            
            # For MVP, assume we call 'send_order' which is exposed.
            order_id = self.send_order(req) 
            self.log_callback(f"Order sent: {order_id}")
            
        except Exception as e:
            self.log_callback(f"Order Error: {e}")

    def stop_remote_server(self):
        # Call the custom registered function
        try:
            res = self.remote_exit()
            print(res)
        except Exception as e:
            print(f"Remote exit failed (maybe already down): {e}")

def main():
    # 1. Start Client
    client = JanusRpcClient()
    rpc_conf = client.config.get_rpc_setting()
    
    # 2. Connect
    client.subscribe_topic("") # Listen to all
    client.start(
        req_address=rpc_conf["rep_address"], 
        pub_address=rpc_conf["pub_address"]
    )
    
    # 3. Start UI
    tui = JanusTUI(client)
    client.tui = tui # Link back for logging
    
    try:
        tui.app.run()
    except Exception as e:
        print(f"UI Error: {e}")
    finally:
        client.stop()

if __name__ == "__main__":
    main()