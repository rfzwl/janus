import logging
import uuid
from copy import copy
from typing import Any, Dict, Optional

from threading import Lock, Timer

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
        self.symbol_registry = None
        
        self._last_position_directions: Dict[str, Direction] = {}
        self._known_orders: Dict[str, OrderData] = {}
        self._client_order_id_map: Dict[str, str] = {}
        self._refresh_lock = Lock()
        self._refresh_timer: Optional[Timer] = None
        self._trade_events_debounce = 1.0

    def connect(self, setting: Dict[str, Any]):
        """
        连接 Webull 交易服务器
        """
        self.app_key = setting.get("app_key", "")
        self.app_secret = setting.get("app_secret", "")
        self.region_id = setting.get("region_id", "us")

        injected_api_client = setting.get("api_client")
        injected_trade_client = setting.get("trade_client")
        self.symbol_registry = setting.get("symbol_registry")
        if injected_api_client:
            self.api_client = injected_api_client
        if injected_trade_client:
            self.trade_client = injected_trade_client

        try:
            self.on_log("正在连接 Webull Open API (Trade Only)...")

            if not self.trade_client:
                if self.api_client:
                    self.trade_client = TradeClient(self.api_client)
                else:
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
            # 5. 轮询已取消，后续由客户端主动同步触发

        except Exception as e:
            self.on_log(f"连接异常: {e}")
            import traceback
            traceback.print_exc()

    def _apply_canonical_symbol(self, symbol: str) -> Optional[str]:
        registry = self.symbol_registry
        if not registry or not symbol:
            return None
        record = registry.get_by_webull_ticker(symbol)
        if record and record.canonical_symbol != symbol:
            return record.canonical_symbol
        return None

    def on_order(self, order: OrderData) -> None:
        canonical = self._apply_canonical_symbol(order.symbol)
        if canonical:
            order.symbol = canonical
            order.vt_symbol = f"{order.symbol}.{order.exchange.value}"
        super().on_order(order)

    def on_position(self, position: PositionData) -> None:
        canonical = self._apply_canonical_symbol(position.symbol)
        if canonical:
            position.symbol = canonical
            position.vt_symbol = f"{position.symbol}.{position.exchange.value}"
            position.vt_positionid = (
                f"{position.gateway_name}.{position.vt_symbol}.{position.direction.value}"
            )
        super().on_position(position)
    
    def send_order(self, req: OrderRequest | dict) -> str:
        """
        发送订单 (适配 Webull V2 接口)
        """
        if isinstance(req, dict):
            stop_price = req.pop("stop_price", None)
            limit_price = req.pop("limit_price", None)
            req = OrderRequest(**req)
        else:
            stop_price = getattr(req, "stop_price", None)
            limit_price = getattr(req, "limit_price", None)

        if not self.trade_client:
            return ""

        # 1. 参数准备
        lmt_price = str(req.price)
        stop_price_value = None
        if stop_price is not None:
            stop_price_value = str(stop_price)
        elif req.type == OrderType.STOP and req.price:
            stop_price_value = str(req.price)

        # 映射 OrderType
        wb_order_type = "LIMIT"
        if req.type == OrderType.MARKET:
            wb_order_type = "MARKET"
        elif req.type == OrderType.STOP:
            wb_order_type = "STOP_LOSS_LIMIT" if limit_price is not None else "STOP_LOSS"

        time_in_force = "DAY" if req.type == OrderType.MARKET else "GTC"

        client_order_id = uuid.uuid4().hex
        params = {
            "client_order_id": client_order_id,
            "combo_type": "NORMAL",
            "symbol": req.symbol.upper(),
            "instrument_type": "EQUITY",
            "market": "US",
            "side": DIRECTION_VT2WB.get(req.direction, 'BUY'),
            "order_type": wb_order_type,
            "quantity": str(int(req.volume)),
            "time_in_force": time_in_force,
            "entrust_type": "QTY",
            "support_trading_session": "N",
        }

        if req.type == OrderType.LIMIT:
            params["limit_price"] = lmt_price
        elif req.type == OrderType.STOP:
            if not stop_price_value:
                self.on_log("下单异常: STOP 订单缺少 stop_price")
                return ""
            params["stop_price"] = stop_price_value
            if limit_price is not None:
                params["limit_price"] = str(limit_price)

        try:
            # 日志脱敏处理
            safe_params = str(params).replace("{", "{{").replace("}", "}}")
            self.on_log(f"发送订单(V2): {safe_params}")

            resp = self.trade_client.order_v2.place_order(
                account_id=self.account_id,
                new_orders=[params],
            )

            if resp.status_code != 200 and params["time_in_force"] == "GTC":
                self.on_log("Webull 不支持 GTC，回退到 DAY")
                params["time_in_force"] = "DAY"
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
                self._known_orders[order.orderid] = order
                self._client_order_id_map[client_order_id] = order.orderid

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
                        pos.last_price = None
                        pos.market_value = None
                        pos.cost = None
                        pos.diluted_cost = None
                        self.on_position(pos)
                    self._last_position_directions.clear()
                    return

                latest_positions: Dict[str, Direction] = {}
                for item in pos_list:
                    ticker = item.get("ticker", {})
                    symbol = ticker.get("symbol") or item.get("symbol")
                    if not symbol:
                        continue

                    if self.symbol_registry:
                        description = None
                        if isinstance(ticker, dict):
                            description = ticker.get("name") or ticker.get("shortName") or ticker.get("longName")
                        if not description:
                            description = item.get("name") or item.get("shortName") or item.get("longName")
                        try:
                            self.symbol_registry.ensure_webull_symbol(symbol, description=description)
                        except Exception as exc:
                            self.on_log(f"Symbol registry update failed for {symbol}: {exc}")
                            raise

                    # 持仓数量
                    raw_qty = item.get("position")
                    if raw_qty is None:
                        raw_qty = item.get("quantity")
                    if raw_qty is None:
                        raw_qty = 0
                    volume = self._safe_float(raw_qty) or 0

                    # 判断方向 (简单逻辑: 正数为多)
                    direction = Direction.LONG
                    if volume < 0:
                        direction = Direction.SHORT
                        volume = abs(volume)

                    latest_positions[symbol] = direction
                    last_price = self._safe_float(self._pick_value(item, "last_price", "lastPrice"))
                    market_value = self._safe_float(self._pick_value(item, "market_value", "marketValue"))
                    cost = self._safe_float(self._pick_value(item, "cost", "costPrice"))
                    diluted_cost = self._safe_float(self._pick_value(
                        item,
                        "diluted_cost",
                        "dilutedCost",
                        "diluted_cost_price",
                        "dilutedCostPrice",
                        "costPrice",
                        "cost_price",
                    ))
                    if diluted_cost is None and cost is not None and volume:
                        diluted_cost = cost / volume
                    pnl = self._safe_float(self._pick_value(item, "unrealized_profit_loss", "unrealizedProfitLoss")) or 0
                    pos = PositionData(
                        symbol=symbol,
                        exchange=Exchange.SMART,
                        direction=direction,
                        volume=volume,
                        price=cost or 0, # 持仓成本
                        pnl=pnl, # 未实现盈亏
                        gateway_name=self.gateway_name
                    )
                    pos.last_price = last_price
                    pos.market_value = market_value
                    pos.cost = cost
                    pos.diluted_cost = diluted_cost
                    self.on_position(pos)
                missing_symbols = set(self._last_position_directions) - set(latest_positions)
                for symbol in missing_symbols:
                    direction = self._last_position_directions.get(symbol, Direction.LONG)
                    pos = PositionData(
                        symbol=symbol,
                        exchange=Exchange.SMART,
                        direction=direction,
                        volume=0,
                        price=0,
                        pnl=0,
                        gateway_name=self.gateway_name
                    )
                    pos.last_price = None
                    pos.market_value = None
                    pos.cost = None
                    pos.diluted_cost = None
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
        pass

    def query_open_orders(self):
        """
        查询未完成订单
        """
        if not self.trade_client: return
        try:
            resp = None
            if hasattr(self.trade_client, "order_v2") and hasattr(self.trade_client.order_v2, "get_order_open"):
                resp = self.trade_client.order_v2.get_order_open(self.account_id, page_size=100)
            else:
                resp = self.trade_client.trade.get_open_orders(self.account_id)
            if resp.status_code == 200:
                data = resp.json()
                orders_list = self._extract_list(data)
                current_open_orders: Dict[str, OrderData] = {}

                for item in orders_list:
                    if not isinstance(item, dict):
                        continue

                    detail = item.get("orders")
                    if isinstance(detail, list) and detail:
                        detail = detail[0]
                    if not isinstance(detail, dict):
                        detail = item

                    symbol = detail.get("symbol")
                    if not symbol:
                        ticker = detail.get("ticker")
                        if isinstance(ticker, dict):
                            symbol = ticker.get("symbol", "")
                    symbol = symbol or ""

                    side = self._pick_value(detail, "side", "action", "orderAction", "order_action")
                    direction = None
                    if isinstance(side, str):
                        side = side.upper()
                        if side in ("BUY", "BUY_OPEN", "BUY_TO_COVER"):
                            direction = Direction.LONG
                        elif side in ("SELL", "SELL_SHORT", "SELL_TO_OPEN"):
                            direction = Direction.SHORT

                    total = self._safe_float(self._pick_value(
                        detail, "total_quantity", "quantity", "totalQuantity"
                    ))
                    if total is None:
                        total = self._safe_float(self._pick_value(item, "total_quantity", "quantity", "totalQuantity"))
                    total = total or 0

                    traded = self._safe_float(self._pick_value(
                        detail, "filled_quantity", "filled_qty", "filledQuantity"
                    ))
                    if traded is None:
                        traded = self._safe_float(self._pick_value(item, "filled_quantity", "filled_qty", "filledQuantity"))
                    traded = traded or 0

                    status = Status.NOTTRADED
                    if traded > 0:
                        status = Status.PARTTRADED if traded < total else Status.ALLTRADED

                    status_raw = self._pick_value(detail, "status", "order_status")
                    if status_raw is None:
                        status_raw = self._pick_value(item, "status", "order_status")
                    if isinstance(status_raw, str):
                        status_norm = status_raw.lower().replace(" ", "_")
                        if "cancel" in status_norm:
                            status = Status.CANCELLED
                        elif "reject" in status_norm:
                            status = Status.REJECTED
                        elif "partial" in status_norm and "fill" in status_norm:
                            status = Status.PARTTRADED
                        elif "fill" in status_norm or "execut" in status_norm or "done" in status_norm:
                            status = Status.ALLTRADED

                    order_type_raw = self._pick_value(detail, "order_type", "orderType")
                    order_type = OrderType.LIMIT
                    if isinstance(order_type_raw, str):
                        order_type_raw = order_type_raw.upper()
                        if order_type_raw == "MARKET":
                            order_type = OrderType.MARKET
                        elif order_type_raw == "STOP":
                            order_type = OrderType.STOP

                    price = self._safe_float(self._pick_value(detail, "limit_price", "lmtPrice", "price")) or 0

                    order_id = self._pick_value(detail, "order_id", "orderId", "client_order_id")
                    if not order_id:
                        order_id = self._pick_value(item, "order_id", "orderId", "client_order_id")
                    if not order_id:
                        continue

                    order = OrderData(
                        orderid=str(order_id),
                        symbol=symbol,
                        exchange=Exchange.SMART,
                        type=order_type,
                        direction=direction,
                        price=price,
                        volume=total,
                        traded=traded,
                        status=status,
                        gateway_name=self.gateway_name
                    )
                    self.on_order(order)
                    current_open_orders[order.orderid] = order
                    self._known_orders[order.orderid] = order

                missing_order_ids = set(self._known_orders) - set(current_open_orders)
                for order_id in missing_order_ids:
                    order = self._known_orders.get(order_id)
                    if not order or not order.is_active():
                        continue
                    order.status = Status.ALLTRADED
                    order.traded = order.volume
                    self.on_order(order)
        except Exception:
            pass

    def set_trade_events_debounce(self, seconds: float) -> None:
        if seconds and seconds > 0:
            self._trade_events_debounce = float(seconds)

    def handle_trade_event(self, event_type, subscribe_type, payload, response) -> None:
        if not isinstance(payload, dict):
            return

        data = payload
        if isinstance(payload.get("data"), dict):
            data = payload.get("data") or payload

        account_id = self._pick_value(data, "account_id", "accountId", "secAccountId")
        if account_id and self.account_id and str(account_id) != str(self.account_id):
            return

        client_order_id = self._pick_value(data, "client_order_id", "clientOrderId")
        order_id = self._pick_value(data, "order_id", "orderId")
        if not order_id and client_order_id:
            order_id = self._client_order_id_map.get(str(client_order_id))
        if not order_id and client_order_id:
            order_id = client_order_id
        if not order_id:
            return
        if client_order_id:
            self._client_order_id_map[str(client_order_id)] = str(order_id)

        symbol = data.get("symbol") or ""
        if not symbol:
            ticker = data.get("ticker")
            if isinstance(ticker, dict):
                symbol = ticker.get("symbol") or ""

        side = self._pick_value(data, "side", "action", "orderAction", "order_action")
        direction = None
        if isinstance(side, str):
            side = side.upper()
            if side in ("BUY", "BUY_OPEN", "BUY_TO_COVER"):
                direction = Direction.LONG
            elif side in ("SELL", "SELL_SHORT", "SELL_TO_OPEN"):
                direction = Direction.SHORT

        total = self._safe_float(self._pick_value(
            data, "total_quantity", "quantity", "qty", "totalQuantity"
        )) or 0

        traded = self._safe_float(self._pick_value(
            data, "filled_quantity", "filled_qty", "filledQuantity"
        )) or 0

        order_type_raw = self._pick_value(data, "order_type", "orderType")
        order_type = OrderType.LIMIT
        if isinstance(order_type_raw, str):
            order_type_raw = order_type_raw.upper()
            if order_type_raw == "MARKET":
                order_type = OrderType.MARKET
            elif order_type_raw in ("STOP_LOSS", "STOP_LOSS_LIMIT", "STOP"):
                order_type = OrderType.STOP

        limit_price = self._safe_float(self._pick_value(
            data, "limit_price", "lmtPrice", "price"
        ))
        stop_price = self._safe_float(self._pick_value(data, "stop_price", "stopPrice"))

        price = 0.0
        if order_type == OrderType.LIMIT:
            price = limit_price or 0.0
        elif order_type == OrderType.STOP:
            price = stop_price or limit_price or 0.0

        status = None
        status_raw = self._pick_value(data, "status", "order_status")
        if isinstance(status_raw, str):
            status_norm = status_raw.lower().replace(" ", "_")
            if "cancel" in status_norm:
                status = Status.CANCELLED
            elif "reject" in status_norm or "fail" in status_norm:
                status = Status.REJECTED
            elif "partial" in status_norm and "fill" in status_norm:
                status = Status.PARTTRADED
            elif "fill" in status_norm or "execut" in status_norm or "done" in status_norm:
                status = Status.ALLTRADED
            elif "submit" in status_norm:
                status = Status.NOTTRADED

        scene_type = self._pick_value(data, "scene_type", "sceneType")
        if isinstance(scene_type, str):
            scene_type = scene_type.upper()
            if scene_type == "FINAL_FILLED":
                status = Status.ALLTRADED
            elif scene_type == "FILLED":
                status = Status.PARTTRADED
            elif scene_type in ("PLACE_FAILED", "MODIFY_FAILED", "CANCEL_FAILED"):
                status = Status.REJECTED
            elif scene_type == "CANCEL_SUCCESS":
                status = Status.CANCELLED

        if status is None:
            status = Status.NOTTRADED

        if status not in (Status.CANCELLED, Status.REJECTED) and total > 0:
            if traded >= total > 0:
                status = Status.ALLTRADED
            elif traded > 0:
                status = Status.PARTTRADED

        order = self._known_orders.get(str(order_id))
        if not order:
            order = OrderData(
                orderid=str(order_id),
                symbol=symbol,
                exchange=Exchange.SMART,
                type=order_type,
                direction=direction,
                price=price,
                volume=total,
                traded=traded,
                status=status,
                gateway_name=self.gateway_name
            )
        else:
            if symbol:
                order.symbol = symbol
                order.vt_symbol = f"{order.symbol}.{order.exchange.value}"
            if direction:
                order.direction = direction
            order.type = order_type
            order.price = price
            order.volume = total or order.volume
            order.traded = traded or order.traded
            order.status = status

        self._known_orders[str(order_id)] = order
        self.on_order(copy(order))

        if scene_type in ("FINAL_FILLED", "FILLED", "CANCEL_SUCCESS") or status in (
            Status.ALLTRADED,
            Status.CANCELLED,
        ):
            self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        with self._refresh_lock:
            if self._refresh_timer and self._refresh_timer.is_alive():
                return
            self._refresh_timer = Timer(self._trade_events_debounce, self._refresh_snapshot)
            self._refresh_timer.daemon = True
            self._refresh_timer.start()

    def _refresh_snapshot(self) -> None:
        try:
            self.query_open_orders()
            self.query_position()
            self.query_account()
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

    @staticmethod
    def _pick_value(item: Dict[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in item and item[key] not in (None, ""):
                return item[key]
        return None

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_list(value: Any) -> list:
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return value.get("data") or value.get("items") or value.get("orders") or []
        return []
