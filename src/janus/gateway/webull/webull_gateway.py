import logging
import threading
import time
import uuid
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

            # ================= [核武器级静音补丁 Start] =================
            # 既然 SDK 喜欢在初始化时乱动日志配置，我们就用 logging.disable 强行压制。
            # 这行代码的意思是：暂时禁用所有 INFO (20) 及以下级别的日志。
            # 这样，SDK 初始化期间产生的所有 INFO 噪音都会被 Python 解释器直接丢弃。
            # (注：WARNING 级别的日志依然会被放行，符合您的需求)
            previous_disable_level = logging.root.manager.disable
            logging.disable(logging.INFO)
            # ==========================================================

            try:
                # 1. 初始化 SDK (此时所有 INFO 日志都会消失)
                self.api_client = ApiClient(self.app_key, self.app_secret, self.region_id)
                self.api_client.add_endpoint(self.region_id, "api.webull.com")
                self.trade_client = TradeClient(self.api_client)
            finally:
                # ================= [恢复现场] =================
                # SDK 初始化完了，必须恢复全局日志功能，否则 Janus 自己的日志也看不到了
                logging.disable(previous_disable_level)
                # ============================================

            # 2. 战场打扫 (Cleanup)
            # 虽然我们拦截了初始化时的日志，但 SDK 可能留下了“私有 Handler”。
            # 为了防止未来的日志（如订单回调）格式乱掉，我们需要把这些 Handler 拆除。
            def cleanup_webull_loggers():
                # 找出所有 webull 相关的 logger（包括子模块）
                webull_loggers = [logging.getLogger(name) for name in logging.root.manager.loggerDict if name.startswith("webull")]
                webull_loggers.append(logging.getLogger("webull")) # 加上根节点
                
                for logger in webull_loggers:
                    logger.setLevel(logging.WARNING) # 锁定为 WARNING
                    logger.propagate = False         # 禁止冒泡
                    if logger.hasHandlers():
                        logger.handlers.clear()      # 拆除私有 Handler

            cleanup_webull_loggers()

            # 3. 获取账户
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

            # 4. 启动轮询
            self.active = True
            self.poll_thread = threading.Thread(target=self._polling_loop)
            self.poll_thread.start()

        except Exception as e:
            self.on_log(f"连接异常: {e}")
            import traceback
            traceback.print_exc()
    
    def send_order(self, req: OrderRequest) -> str:
        # [RPC 兼容] 字典转对象
        if isinstance(req, dict):
            req = OrderRequest(**req)

        if not self.trade_client:
            return ""

        # 1. 价格处理
        lmt_price = str(req.price)

        # 2. 映射 OrderType (V2 接口必须用全称!)
        # V1: LMT, MKT
        # V2: LIMIT, MARKET
        wb_order_type = "LIMIT"
        if req.type == OrderType.MARKET:
            wb_order_type = "MARKET"
        elif req.type == OrderType.STOP:
            wb_order_type = "STOP"

        # 3. 构造参数字典
        params = {
            "client_order_id": uuid.uuid4().hex,
            "combo_type": "NORMAL",
            "symbol": req.symbol.upper(),
            "instrument_type": "EQUITY",
            "market": "US",
            "side": DIRECTION_VT2WB.get(req.direction, 'BUY'),
            "order_type": wb_order_type,
            "quantity": str(int(req.volume)),
            "time_in_force": "DAY",
            "entrust_type": "QTY",
            "support_trading_session": "N",
        }

        # 4. 补充限价参数
        if req.type == OrderType.LIMIT:
            params["limit_price"] = lmt_price

        try:
            # 日志脱敏
            safe_params = str(params).replace("{", "{{").replace("}", "}}")
            self.on_log(f"发送订单(V2): {safe_params}")

            resp = self.trade_client.order_v2.place_order(
                account_id=self.account_id,
                new_orders=[params],
            )

            if resp.status_code == 200:
                data = resp.json()

                # 解析 Order ID
                wb_order_id = ""
                raw_data = data.get("data", data)
                if isinstance(raw_data, list) and len(raw_data) > 0:
                    wb_order_id = str(raw_data[0].get("orderId", ""))
                elif isinstance(raw_data, dict):
                    wb_order_id = str(raw_data.get("orderId", ""))

                if not wb_order_id:
                    wb_order_id = params["client_order_id"]

                order = req.create_order_data(wb_order_id, self.gateway_name)
                order.status = Status.NOTTRADED
                self.on_order(order)

                return order.vt_orderid
            else:
                self.on_log(f"Webull 拒单: {resp.status_code} {resp.text}")
                return ""

        except Exception as e:
            self.on_log(f"下单异常: {e}")
            import traceback
            traceback.print_exc()
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
