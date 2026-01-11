import threading
import time
import logging
from typing import Any, Dict, Optional

# Webull Official SDK
from webull.core.client import ApiClient
from webull.trade.trade_client import TradeClient
from webull.quotes.quotes_client import QuotesClient

from vnpy.trader.gateway import BaseGateway
from vnpy.trader.object import (
    SubscribeRequest, OrderRequest, CancelRequest,
    OrderData, AccountData, PositionData, TickData
)
from vnpy.trader.constant import (
    Direction, Exchange, OrderType, Status, Product
)

# 映射: vnpy Direction -> Webull Action
DIRECTION_VT2WB = {
    Direction.LONG: 'BUY',
    Direction.SHORT: 'SELL'
}

class WebullOfficialGateway(BaseGateway):
    """
    基于 webull-openapi-python-sdk 的官方网关
    参考: genliusrocks/webull-cli-trader
    """
    default_name = "WEBULL"

    def __init__(self, event_engine, gateway_name="WEBULL"):
        super().__init__(event_engine, gateway_name)

        self.api_client: Optional[ApiClient] = None
        self.trade_client: Optional[TradeClient] = None
        self.quotes_client: Optional[QuotesClient] = None # 用于查询 TickerID

        self.account_id = ""
        self.app_key = ""
        self.app_secret = ""
        self.region_id = "us"
        
        self.active = False
        self.poll_thread = None
        self.query_interval = 2

        # 缓存: Symbol -> TickerID (OpenAPI 下单必须用 ID)
        self.symbol_id_map: Dict[str, str] = {}
        # 缓存: WebullOrderID -> VnpyOrder
        self.orders_map: Dict[str, OrderData] = {}

    def connect(self, setting: Dict[str, Any]):
        self.app_key = setting.get("app_key", "")
        self.app_secret = setting.get("app_secret", "")
        self.region_id = setting.get("region_id", "us")

        if not self.app_key or not self.app_secret:
            self.on_log("配置错误: 缺少 app_key 或 app_secret")
            return

        try:
            self.on_log("正在连接 Webull Open API...")
            
            # 1. 初始化 SDK 客户端 (参考 adapter.py)
            self.api_client = ApiClient(self.app_key, self.app_secret, self.region_id)
            self.api_client.add_endpoint(self.region_id, "api.webull.com")
            
            self.trade_client = TradeClient(self.api_client)
            self.quotes_client = QuotesClient(self.api_client) # 初始化行情客户端

            # 2. 获取账户列表 (参考 adapter.py: get_first_account_id)
            resp = self.trade_client.account_v2.get_account_list()
            if resp.status_code != 200:
                self.on_log(f"获取账户失败: {resp.text}")
                return

            data = resp.json()
            # 兼容处理: 有时 data 是 list，有时是 dict 包含 data
            acct_list = data if isinstance(data, list) else data.get('data', [])
            
            if not acct_list:
                self.on_log("未找到有效 Webull 账户")
                return

            # 默认取第一个账户
            first_acct = acct_list[0]
            # Webull 返回可能是 account_id 或 secAccountId
            self.account_id = str(first_acct.get("account_id") or first_acct.get("secAccountId"))
            
            self.on_log(f"连接成功! 账户 ID: {self.account_id}")

            # 3. 启动轮询线程
            self.active = True
            self.poll_thread = threading.Thread(target=self._polling_loop)
            self.poll_thread.start()

        except Exception as e:
            self.on_log(f"连接异常: {e}")
            import traceback
            traceback.print_exc()

    def get_ticker_id(self, symbol: str) -> str:
        """
        OpenAPI 下单需要 TickerID (如 913256135)，而不是 Symbol (如 AAPL)。
        这里尝试查询并缓存。
        """
        if symbol in self.symbol_id_map:
            return self.symbol_id_map[symbol]

        # 简单的容错: 如果用户直接传了数字 ID，就直接用
        if symbol.isdigit():
            return symbol

        self.on_log(f"正在查询 Symbol ID: {symbol} ...")
        # 尝试使用 quotes 接口查询 (具体方法视 SDK 版本而定，这里是通用逻辑)
        # 如果 SDK 没有封装好 search，这里可能需要手动调 API
        # 假设: self.quotes_client.instrument.list(keyword=symbol)
        # MVP 阶段: 如果 SDK 暂时不支持搜索，建议用户在 Symbol 里直接填 ID，或者我们在 config 里写死映射

        # 模拟返回 (实际需对接 SDK 搜索接口)
        # self.on_log(f"警告: 暂时无法自动查找 ID，请确保 Symbol 传入的是 TickerID")
        return symbol # 暂时透传

    def send_order(self, req: OrderRequest) -> str:
        if not self.trade_client:
            return ""

        # 1. 获取 Ticker ID
        ticker_id = self.get_ticker_id(req.symbol)
        if not ticker_id.isdigit():
            self.on_log(f"下单失败: Symbol '{req.symbol}' 必须转换为数字 TickerID。")
            # MVP Hack: 提示用户去查 ID
            return ""

        # 2. 构建参数 (参考 trade.py)
        params = {
            "tickerId": int(ticker_id),
            "action": DIRECTION_VT2WB.get(req.direction, 'BUY'),
            "orderType": 'LMT' if req.type == OrderType.LIMIT else 'MKT',
            "timeInForce": "GTC", # 默认 GTC
            "quantity": int(req.volume)
        }
        if req.type == OrderType.LIMIT:
            params["lmtPrice"] = str(req.price) # SDK 通常要求价格为字符串以防精度丢失

        # 3. 发送请求
        try:
            self.on_log(f"发送订单: {params}")
            resp = self.trade_client.trade.place_order(self.account_id, params)
            
            if resp.status_code == 200:
                data = resp.json()
                # 兼容不同层级的返回
                order_data = data.get('data', data)
                wb_order_id = str(order_data.get('orderId'))
                
                # 生成本地 OrderData
                order = req.create_order_data(wb_order_id, self.gateway_name)
                order.status = Status.NOTTRADED
                self.on_order(order)
                return order.vt_orderid
            else:
                self.on_log(f"下单被拒绝: {resp.text}")
                return ""
        except Exception as e:
            self.on_log(f"下单异常: {e}")
            return ""

    def cancel_order(self, req: CancelRequest):
        if not self.trade_client:
            return
        try:
            self.trade_client.trade.cancel_order(self.account_id, req.orderid)
            self.on_log(f"已发送撤单请求: {req.orderid}")
        except Exception as e:
            self.on_log(f"撤单异常: {e}")

    def subscribe(self, req: SubscribeRequest):
        pass # Open API 暂不支持通过 SDK 订阅行情流，需轮询

    def close(self):
        self.active = False
        if self.poll_thread:
            self.poll_thread.join()

    def _polling_loop(self):
        while self.active:
            try:
                self._poll_orders()
                # self._poll_account() # 可选
            except Exception as e:
                pass
            time.sleep(self.query_interval)

    def _poll_orders(self):
        """轮询未成交订单"""
        if not self.trade_client: return

        resp = self.trade_client.trade.get_open_orders(self.account_id)
        if resp.status_code == 200:
            data = resp.json()
            orders_list = data if isinstance(data, list) else data.get('data', [])
            
            # 这里可以实现订单状态更新逻辑
            # 将 Webull 订单结构转为 vnpy OrderData 并调用 self.on_order()
            # MVP 阶段暂略