import logging
import threading
import time
from typing import Any, Dict, Optional

# 只保留核心和交易模块
from webull.core.client import ApiClient
from webull.trade.trade_client import TradeClient

from vnpy.trader.gateway import BaseGateway
from vnpy.trader.object import (
    SubscribeRequest, OrderRequest, CancelRequest,
    OrderData, AccountData, PositionData, TickData
)
from vnpy.trader.constant import (
    Direction, Exchange, OrderType, Status
)

# 映射: vnpy Direction -> Webull Action
DIRECTION_VT2WB = {
    Direction.LONG: 'BUY',
    Direction.SHORT: 'SELL'
}

class WebullOfficialGateway(BaseGateway):
    """
    Webull Official Open API Gateway (No Quotes MVP)
    """
    default_name = "WEBULL"

    def __init__(self, event_engine, gateway_name="WEBULL"):
        super().__init__(event_engine, gateway_name)

        self.api_client: Optional[ApiClient] = None
        self.trade_client: Optional[TradeClient] = None

        self.account_id = ""
        self.app_key = ""
        self.app_secret = ""
        self.region_id = "us"
        
        self.active = False
        self.poll_thread = None
        self.query_interval = 2

    def connect(self, setting: Dict[str, Any]):
        self.app_key = setting.get("app_key", "")
        self.app_secret = setting.get("app_secret", "")
        self.region_id = setting.get("region_id", "us")

        if not self.app_key or not self.app_secret:
            self.on_log("配置错误: 缺少 app_key 或 app_secret")
            return

        try:
            self.on_log("正在连接 Webull Open API (Trade Only)...")
            
            # ================= [强力静音补丁 Start] =================
            # 定义一个内部函数，专门用来清理 Webull 的日志污染
            # 这里的目的是防止 Webull SDK 的 INFO 级别日志刷屏并导致系统崩溃
            def silence_webull():
                # 获取 webull 的总舵主 logger
                wb_logger = logging.getLogger("webull")
                
                # 1. 级别压制：只许报忧，不许报喜 (屏蔽 INFO)
                wb_logger.setLevel(logging.WARNING)
                
                # 2. 掐断传播：禁止日志向上冒泡到 Root Logger (解决重复日志)
                wb_logger.propagate = False
                
                # 3. 暴力拆除：移除 Webull 自己偷偷加的所有 Handler (解决特殊格式日志)
                if wb_logger.hasHandlers():
                    wb_logger.handlers.clear()

            # 第一次静音：防止 SDK 模块 import 时产生的 Handler 捣乱
            silence_webull()
            # ================= [强力静音补丁 End] =================

            # 1. 初始化 SDK
            # (Webull 在这里面可能会重新添加 Handler 或重置 Level)
            self.api_client = ApiClient(self.app_key, self.app_secret, self.region_id)
            
            # ================= [强力静音补丁 Again] =================
            # 第二次静音：SDK 初始化完了，如果它刚才不听话又加了 Handler，现在再次杀掉
            silence_webull()
            # ================= [强力静音补丁 End] =================

            self.api_client.add_endpoint(self.region_id, "api.webull.com")
            
            self.trade_client = TradeClient(self.api_client)

            # 2. 获取账户
            self.on_log("正在获取账户列表...")
            resp = self.trade_client.account_v2.get_account_list()
            
            if resp.status_code != 200:
                self.on_log(f"获取账户失败 (Code {resp.status_code}): {resp.text}")
                return

            data = resp.json()
            # 兼容 list 或 dict
            acct_list = data if isinstance(data, list) else data.get('data', [])
            
            if not acct_list:
                self.on_log("未找到有效账户")
                return

            first_acct = acct_list[0]
            self.account_id = str(first_acct.get("account_id") or first_acct.get("secAccountId"))
            
            self.on_log(f"连接成功! 账户 ID: {self.account_id}")

            # 3. 启动轮询
            self.active = True
            self.poll_thread = threading.Thread(target=self._polling_loop)
            self.poll_thread.start()

        except Exception as e:
            self.on_log(f"连接异常: {e}")
            import traceback
            traceback.print_exc()

    def send_order(self, req: OrderRequest) -> str:
        if not self.trade_client:
            return ""
        
        if not req.symbol.isdigit():
            self.on_log(f"下单失败: MVP版本不支持代码查询。请直接输入TickerID数字 (例如 AAPL=913256135)")
            return ""
        
        ticker_id = int(req.symbol)

        params = {
            "tickerId": ticker_id,
            "action": DIRECTION_VT2WB.get(req.direction, 'BUY'),
            "orderType": 'LMT' if req.type == OrderType.LIMIT else 'MKT',
            "timeInForce": "GTC",
            "quantity": int(req.volume)
        }
        if req.type == OrderType.LIMIT:
            params["lmtPrice"] = str(req.price)

        try:
            # 转义花括号，防止 Loguru 崩溃 (保留此防御措施)
            safe_params = str(params).replace("{", "{{").replace("}", "}}")
            self.on_log(f"发送订单: {safe_params}")
            resp = self.trade_client.trade.place_order(self.account_id, params)
            
            if resp.status_code == 200:
                data = resp.json()
                order_data = data.get('data', data)
                wb_order_id = str(order_data.get('orderId'))
                
                order = req.create_order_data(wb_order_id, self.gateway_name)
                order.status = Status.NOTTRADED
                self.on_order(order)
                return order.vt_orderid
            else:
                self.on_log(f"Webull 拒单: {resp.text}")
                return ""
        except Exception as e:
            self.on_log(f"下单异常: {e}")
            return ""

    def cancel_order(self, req: CancelRequest):
        if not self.trade_client: return
        try:
            self.trade_client.trade.cancel_order(self.account_id, req.orderid)
            self.on_log(f"已发送撤单: {req.orderid}")
        except Exception as e:
            self.on_log(f"撤单异常: {e}")

    # --- 必须实现的抽象方法 (Dummy Implementations) ---
    def subscribe(self, req: SubscribeRequest):
        pass

    def query_account(self):
        """MVP Dummy Method"""
        pass

    def query_position(self):
        """MVP Dummy Method"""
        pass
    # -----------------------------------------------

    def close(self):
        self.active = False
        if self.poll_thread:
            self.poll_thread.join()

    def _polling_loop(self):
        while self.active:
            try:
                self._poll_orders()
            except Exception as e:
                pass
            time.sleep(self.query_interval)

    def _poll_orders(self):
        if not self.trade_client: return
        try:
            resp = self.trade_client.trade.get_open_orders(self.account_id)
            if resp.status_code == 200:
                # 暂时只做空跑，防止报错
                pass
        except:
            pass