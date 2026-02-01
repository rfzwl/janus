import logging
import threading
import time
import uuid
from typing import Any, Dict, Optional

# Webull SDK
from webull.core.client import ApiClient
from webull.trade.trade_client import TradeClient

# vn.py 基础组件
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
    Webull Official Open API Gateway
    实现了资金查询、持仓查询、委托交易和状态轮询。
    """
    default_name = "WEBULL"

    def __init__(
        self,
        event_engine,
        gateway_name="WEBULL",
        api_client: Optional[ApiClient] = None,
        trade_client: Optional[TradeClient] = None,
    ):
        super().__init__(event_engine, gateway_name)

        self.api_client: Optional[ApiClient] = api_client
        self.trade_client: Optional[TradeClient] = trade_client

        self.account_id = ""
        self.app_key = ""
        self.app_secret = ""
        self.region_id = "us"
        
        self.active = False
        self.poll_thread = None
        self.query_interval = 2  # 轮询间隔(秒)
        self._last_position_directions: Dict[str, Direction] = {}

    def connect(self, setting: Dict[str, Any]):
        """
        连接 Webull 交易服务器
        """
        self.app_key = setting.get("app_key", "")
        self.app_secret = setting.get("app_secret", "")
        self.region_id = setting.get("region_id", "us")

        injected_api_client = setting.get("api_client")
        injected_trade_client = setting.get("trade_client")
        if injected_api_client:
            self.api_client = injected_api_client
        if injected_trade_client:
            self.trade_client = injected_trade_client

        try:
            self.on_log("正在连接 Webull Open API (Trade Only)...")

            if not self.trade_client:
                if not self.app_key or not self.app_secret:
                    self.on_log("配置错误: 缺少 app_key 或 app_secret")
                    return

                # ================= [日志静音处理] =================
                # 屏蔽 SDK 初始化过程中的 INFO 日志噪音
                previous_disable_level = logging.root.manager.disable
                logging.disable(logging.INFO)
                # ================================================

                try:
                    # 1. 初始化 SDK
                    self.api_client = ApiClient(self.app_key, self.app_secret, self.region_id)
                    self.api_client.add_endpoint(self.region_id, "api.webull.com")
                    self.trade_client = TradeClient(self.api_client)
                finally:
                    # 恢复日志
                    logging.disable(previous_disable_level)

            # 2. 清理 SDK 残留的 Logger Handler
            self._cleanup_webull_loggers()

            # 3. 获取账户列表
            self.on_log("正在获取账户列表...")
            resp = self.trade_client.account_v2.get_account_list()
            
            if resp.status_code != 200:
                self.on_log(f"获取账户失败 (Code {resp.status_code}): {resp.text}")
                return

            data = resp.json()
            acct_list = data if isinstance(data, list) else data.get('data', [])
            
            if not acct_list:
                self.on_log("未找到有效账户")
                return

            # 获取第一个账户 ID
            first_acct = acct_list[0]
            self.account_id = str(first_acct.get("account_id") or first_acct.get("secAccountId"))
            
            self.on_log(f"连接成功! 账户 ID: {self.account_id}")

            # 4. 初始化查询 (资金、持仓)
            self.query_account()
            self.query_position()

            # 5. 暂时禁用轮询线程 (准备转向事件驱动)
            # self.active = True
            # self.poll_thread = threading.Thread(target=self._polling_loop)
            # self.poll_thread.start()

        except Exception as e:
            self.on_log(f"连接异常: {e}")
            import traceback
            traceback.print_exc()
    
    def send_order(self, req: OrderRequest) -> str:
        """
        发送订单 (适配 Webull V2 接口)
        """
        if isinstance(req, dict):
            req = OrderRequest(**req)

        if not self.trade_client:
            return ""

        # 1. 参数准备
        lmt_price = str(req.price)
        
        # 映射 OrderType
        wb_order_type = "LIMIT"
        if req.type == OrderType.MARKET:
            wb_order_type = "MARKET"
        elif req.type == OrderType.STOP:
            wb_order_type = "STOP"

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

        if req.type == OrderType.LIMIT:
            params["limit_price"] = lmt_price

        try:
            # 日志脱敏处理
            safe_params = str(params).replace("{", "{{").replace("}", "}}")
            self.on_log(f"发送订单(V2): {safe_params}")

            resp = self.trade_client.order_v2.place_order(
                account_id=self.account_id,
                new_orders=[params],
            )

            if resp.status_code == 200:
                data = resp.json()
                wb_order_id = ""
                
                # 解析 Order ID
                raw_data = data.get("data", data)
                if isinstance(raw_data, list) and len(raw_data) > 0:
                    wb_order_id = str(raw_data[0].get("orderId", ""))
                elif isinstance(raw_data, dict):
                    wb_order_id = str(raw_data.get("orderId", ""))

                if not wb_order_id:
                    wb_order_id = params["client_order_id"]

                # 立即推送一个本地状态
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
        """
        撤单
        """
        if not self.trade_client: return
        try:
            self.trade_client.trade.cancel_order(self.account_id, req.orderid)
            self.on_log(f"已发送撤单: {req.orderid}")
        except Exception as e:
            self.on_log(f"撤单异常: {e}")

    def query_account(self):
        """
        查询账户资金并推送日志总结
        """
        if not self.trade_client: return

        try:
            resp = self.trade_client.account_v2.get_account_balance(self.account_id)
            if resp.status_code == 200:
                data = resp.json()
                
                # 获取净资产 (Net Liquidation Value)
                balance = float(data.get("total_net_liquidation_value", 0))
                # 获取现金余额 (Total Cash)
                total_cash = float(data.get("total_cash_balance", 0))
                
                # 构造 AccountData
                # 在 vnpy 中:
                # balance = 总资产
                # available = 可用资金
                # frozen = balance - available (即持仓市值 + 冻结保证金)
                account = AccountData(
                    accountid=self.account_id,
                    balance=balance,
                    frozen=balance - total_cash,  # 简化计算：非现金资产视为冻结
                    gateway_name=self.gateway_name
                )
                self.on_account(account)

                summary = (
                    f"\n{'='*30}\n"
                    f"[{self.gateway_name}] 账户同步完成！\n"
                    f"账户总额: {balance:.2f}\n"
                    f"现金余额: {total_cash:.2f}\n"
                    f"{'='*30}"
                )
                self.on_log(summary)
            else:
                self.on_log(f"查询资金失败: {resp.status_code}")
        except Exception as e:
            self.on_log(f"查询资金异常: {e}")

    def query_position(self):
        """
        查询持仓 (基于 account_v2.get_account_position)
        """
        if not self.trade_client:
            return

        try:
            resp = self.trade_client.account_v2.get_account_position(self.account_id)
            if resp.status_code == 200:
                raw_data = resp.json()
                # 兼容不同返回格式 (list 或 dict wrapper)
                pos_list = []
                if isinstance(raw_data, list):
                    pos_list = raw_data
                elif isinstance(raw_data, dict):
                    pos_list = raw_data.get("data") or raw_data.get("items") or []

                if not pos_list:
                    for symbol, direction in self._last_position_directions.items():
                        pos = PositionData(
                            symbol=symbol,
                            exchange=Exchange.SMART,
                            direction=direction,
                            volume=0,
                            price=0,
                            pnl=0,
                            gateway_name=self.gateway_name
                        )
                        self.on_position(pos)
                    self._last_position_directions.clear()
                    return

                latest_positions: Dict[str, Direction] = {}
                for item in pos_list:
                    ticker = item.get("ticker", {})
                    symbol = ticker.get("symbol")
                    if not symbol:
                        continue

                    # 持仓数量
                    volume = float(item.get("position", 0))
                    
                    # 判断方向 (简单逻辑: 正数为多)
                    direction = Direction.LONG
                    if volume < 0:
                        direction = Direction.SHORT
                        volume = abs(volume)

                    latest_positions[symbol] = direction
                    pos = PositionData(
                        symbol=symbol,
                        exchange=Exchange.SMART,
                        direction=direction,
                        volume=volume,
                        price=float(item.get("costPrice", 0)), # 持仓成本
                        pnl=float(item.get("unrealizedProfitLoss", 0)), # 未实现盈亏
                        gateway_name=self.gateway_name
                    )
                    self.on_position(pos)
                self._last_position_directions = latest_positions
            else:
                self.on_log(f"查询持仓失败: {resp.status_code}")
        except Exception as e:
            self.on_log(f"查询持仓异常: {e}")

    def subscribe(self, req: SubscribeRequest):
        """
        MVP 版本暂不实现实时行情订阅
        """
        pass

    def close(self):
        self.active = False
        if self.poll_thread:
            self.poll_thread.join()

    def _polling_loop(self):
        """
        轮询线程: 定期同步订单、资金和持仓
        """
        count = 0
        while self.active:
            try:
                # 1. 轮询订单 (高频: 每次循环都查)
                self._poll_orders()
                
                # 2. 轮询资金和持仓 (低频: 每5次循环查一次，即约10秒)
                if count % 5 == 0:
                    self.query_account()
                    self.query_position()
                
                count += 1
            except Exception as e:
                self.on_log(f"轮询出错: {e}")
            
            time.sleep(self.query_interval)

    def _poll_orders(self):
        """
        轮询未完成订单
        """
        if not self.trade_client: return
        try:
            resp = self.trade_client.trade.get_open_orders(self.account_id)
            if resp.status_code == 200:
                data = resp.json()
                orders_list = data if isinstance(data, list) else data.get("data", [])
                
                for item in orders_list:
                    # 状态映射逻辑需根据实际返回完善，此处简化为 NOTTRADED
                    status = Status.NOTTRADED
                    
                    # 尝试解析 filledQuantity
                    traded = float(item.get("filledQuantity", 0))
                    total = float(item.get("totalQuantity", 0))
                    
                    if traded > 0:
                        status = Status.PARTTRADED if traded < total else Status.ALLTRADED

                    order = OrderData(
                        orderid=str(item.get("orderId")),
                        symbol=item.get("ticker", {}).get("symbol", ""),
                        exchange=Exchange.SMART,
                        price=float(item.get("lmtPrice", 0)),
                        volume=total,
                        traded=traded,
                        status=status,
                        gateway_name=self.gateway_name
                    )
                    self.on_order(order)
        except Exception:
            pass

    def _cleanup_webull_loggers(self):
        """清理 webull SDK 自动添加的 log handlers"""
        webull_loggers = [logging.getLogger(name) for name in logging.root.manager.loggerDict if name.startswith("webull")]
        webull_loggers.append(logging.getLogger("webull"))
        
        for logger in webull_loggers:
            logger.setLevel(logging.WARNING)
            logger.propagate = False
            if logger.hasHandlers():
                logger.handlers.clear()
