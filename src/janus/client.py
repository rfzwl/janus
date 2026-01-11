import threading
import sys
# 【关键修复】添加 Any 到导入列表
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
        # 默认的日志回调，稍后会被 TUI 覆盖
        self.log_callback: Callable[[str], None] = lambda x: print(x) 
        self.tui = None

    def callback(self, topic: str, data: Any):
        """Standard vnpy RPC callback"""
        # 使用 Python 3.10+ 的 match 语法
        match topic:
            case "eOrder":
                # data 是反序列化后的 OrderData 对象
                if data.is_active():
                    self.orders[data.vt_orderid] = data
                elif data.vt_orderid in self.orders:
                    # 如果订单完成（如已撤销或全部成交），从活跃列表中移除
                    # 也可以选择保留，视显示需求而定
                    # 这里为了 MVP 简单，我们只显示活跃订单，或者更新状态
                    self.orders[data.vt_orderid] = data 
                
                # 强制刷新 UI
                if self.tui and self.tui.app.is_running:
                    self.tui.app.invalidate()
                    
            case "eLog":
                if self.tui:
                    self.tui.log(f"[Server] {data.msg}")

    def get_open_orders(self) -> List[OrderData]:
        # 返回所有缓存的订单
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
                # 手动触发订阅
                self.subscribe_topic("")
                log_func("Subscribed to all events.")
            case _:
                log_func(f"Unknown command: {parts[0]}")

    def _send_order_cmd(self, parts: list):
        # 语法: buy <symbol> <volume> <price>
        # 例如: buy 913256135 10 150.0 (Webull MVP 需要数字 ID)
        if len(parts) < 4:
            self.log_callback("Usage: <action> <symbol> <volume> <price>")
            return

        direction_map = {
            "buy": Direction.LONG, "sell": Direction.SHORT,
            "short": Direction.SHORT, "cover": Direction.LONG
        }
        
        try:
            symbol = parts[1]
            volume = float(parts[2])
            price = float(parts[3])
            
            # 构造下单请求字典
            req = {
                "symbol": symbol,
                "exchange": Exchange.SMART, 
                "direction": direction_map[parts[0]],
                "type": OrderType.LIMIT,
                "volume": volume,
                "price": price,
                "offset": Offset.OPEN 
            }
            
            # 调用 RPC 的 send_order
            # 注意：vnpy_rpcservice 注册的函数名通常是 "send_order"
            order_id = self.send_order(req) 
            self.log_callback(f"Order sent: {order_id}")
            
        except Exception as e:
            self.log_callback(f"Order Error: {e}")

    def stop_remote_server(self):
        # 尝试调用远程自定义函数（如果 Server 端注册了的话）
        # 如果没注册，这里会报错，忽略即可
        try:
            # self.remote_exit() 是动态生成的 RPC 方法
            # 如果 IDE 报错，可以忽略，运行时存在
            if hasattr(self, "remote_exit"):
                res = self.remote_exit()
                print(res)
        except Exception as e:
            print(f"Remote exit failed: {e}")

def main():
    # 1. 初始化客户端
    client = JanusRpcClient()
    rpc_conf = client.config.get_rpc_setting()
    
    # 2. 连接 RPC
    print(f"Connecting to RPC at {rpc_conf['rep_address']}...")
    client.subscribe_topic("") # 订阅所有推送
    client.start(
        req_address=rpc_conf["rep_address"], 
        pub_address=rpc_conf["pub_address"]
    )
    
    # 3. 启动 TUI 界面
    tui = JanusTUI(client)
    client.tui = tui 
    
    try:
        tui.app.run()
    except Exception as e:
        print(f"UI Error: {e}")
    finally:
        client.stop()

if __name__ == "__main__":
    main()