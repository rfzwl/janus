import asyncio
from copy import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event as ThreadEvent
from threading import Lock, Thread
from typing import Any, Dict, Optional

from ib_async import IB, RealTimeBarList
from ib_async.contract import Contract, Stock
from ib_async.ib import StartupFetch
from ib_async.order import LimitOrder, MarketOrder, StopLimitOrder, StopOrder
from ib_async.ticker import Ticker
from ib_async.util import isNan
from zoneinfo import ZoneInfo

from vnpy.event import Event
from vnpy.trader.event import EVENT_TIMER
from vnpy.trader.gateway import BaseGateway
from vnpy.trader.logger import DEBUG
from vnpy.trader.object import (
    AccountData,
    CancelRequest,
    LogData,
    OrderData,
    OrderRequest,
    PositionData,
    SubscribeRequest,
    TickData,
    TradeData,
)
from vnpy.trader.constant import Direction, Exchange, OrderType, Status


STATUS_IB2VT: dict[str, Status] = {
    "ApiPending": Status.SUBMITTING,
    "PendingSubmit": Status.SUBMITTING,
    "PreSubmitted": Status.NOTTRADED,
    "Submitted": Status.NOTTRADED,
    "ApiCancelled": Status.CANCELLED,
    "Cancelled": Status.CANCELLED,
    "Filled": Status.ALLTRADED,
    "Inactive": Status.REJECTED,
}

try:
    PACIFIC_TZ = ZoneInfo("America/Los_Angeles")
except Exception:
    PACIFIC_TZ = timezone.utc

DIRECTION_VT2IB: dict[Direction, str] = {
    Direction.LONG: "BUY",
    Direction.SHORT: "SELL",
}
DIRECTION_IB2VT: dict[str, Direction] = {
    "BUY": Direction.LONG,
    "SELL": Direction.SHORT,
    "BOT": Direction.LONG,
    "SLD": Direction.SHORT,
}

ORDERTYPE_VT2IB: dict[OrderType, str] = {
    OrderType.LIMIT: "LMT",
    OrderType.MARKET: "MKT",
    OrderType.STOP: "STP",
}
ORDERTYPE_IB2VT: dict[str, OrderType] = {
    "LMT": OrderType.LIMIT,
    "MKT": OrderType.MARKET,
    "STP": OrderType.STOP,
    "STP LMT": OrderType.STOP,
}

EXCHANGE_VT2IB: dict[Exchange, str] = {
    Exchange.SMART: "SMART",
    Exchange.NYSE: "NYSE",
    Exchange.NASDAQ: "NASDAQ",
    Exchange.AMEX: "AMEX",
    Exchange.ARCA: "ARCA",
    Exchange.ISLAND: "ISLAND",
    Exchange.BATS: "BATS",
    Exchange.IEX: "IEX",
}
EXCHANGE_IB2VT: dict[str, Exchange] = {v: k for k, v in EXCHANGE_VT2IB.items()}

ACCOUNTFIELD_IB2VT: dict[str, str] = {
    "NetLiquidationByCurrency": "balance",
    "NetLiquidation": "balance",
    "UnrealizedPnL": "positionProfit",
    "AvailableFunds": "available",
    "MaintMarginReq": "margin",
}


@dataclass
class BarSubscription:
    contract: Contract
    what_to_show: str
    use_rth: bool
    bars: Optional[RealTimeBarList] = None


class IbAsyncApi:
    def __init__(self, gateway: "JanusIbGateway") -> None:
        self.gateway = gateway
        self.gateway_name = gateway.gateway_name

        self.host: str = ""
        self.port: int = 0
        self.client_id: int = 0
        self.account: str = ""

        self._ib: Optional[IB] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[Thread] = None

        self._connected = False
        self._connecting = False
        self._stop_event = ThreadEvent()
        self._loop_ready = ThreadEvent()
        self._lock = Lock()

        self._subscribed: Dict[str, Contract] = {}
        self._bar_subscriptions: Dict[str, "BarSubscription"] = {}
        self._accounts: Dict[str, AccountData] = {}
        self._seen_trades: set[str] = set()
        self._last_position_direction: Dict[str, Direction] = {}


    @property
    def status(self) -> bool:
        return self._connected

    def connect(self, host: str, port: int, client_id: int, account: str) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self.account = account

        if not self._thread or not self._thread.is_alive():
            self._thread = Thread(target=self._run_loop, daemon=True)
            self._thread.start()

        self._loop_ready.wait(timeout=2)
        self._call_soon(self._ensure_connect)

    def close(self) -> None:
        if not self._loop:
            return

        def _shutdown() -> None:
            if self._ib:
                for sub in list(self._bar_subscriptions.values()):
                    if sub.bars:
                        try:
                            self._ib.cancelRealTimeBars(sub.bars)
                        except Exception:
                            pass
                for contract in list(self._subscribed.values()):
                    try:
                        self._ib.cancelMktData(contract)
                    except Exception:
                        pass
                self._bar_subscriptions.clear()
                self._subscribed.clear()
                self._ib.disconnect()
            if self._loop:
                self._loop.stop()

        self._call_soon(_shutdown)

        if self._thread:
            self._thread.join(timeout=2)

    def check_connection(self) -> None:
        self._call_soon(self._ensure_connect)

    def subscribe(self, req: SubscribeRequest) -> None:
        if not self._loop:
            return

        def _subscribe() -> None:
            if not self._ib or not self._connected:
                return
            if req.vt_symbol in self._subscribed:
                return
            contract = self._contract_from_symbol(req.symbol, req.exchange)
            self._subscribed[req.vt_symbol] = contract
            self._ib.reqMktData(contract)

        self._call_soon(_subscribe)

    def subscribe_bars(
        self,
        req: SubscribeRequest,
        what_to_show: str = "TRADES",
        use_rth: bool = False,
    ) -> None:
        if not self._loop:
            return

        what_to_show = (what_to_show or "TRADES").upper()

        def _subscribe() -> None:
            existing = self._bar_subscriptions.get(req.vt_symbol)
            if existing:
                if existing.what_to_show == what_to_show and existing.use_rth == use_rth:
                    return
                if self._ib and existing.bars:
                    try:
                        self._ib.cancelRealTimeBars(existing.bars)
                    except Exception:
                        pass
            contract = self._contract_from_symbol(req.symbol, req.exchange)
            sub = BarSubscription(contract=contract, what_to_show=what_to_show, use_rth=use_rth)
            self._bar_subscriptions[req.vt_symbol] = sub
            if not self._ib or not self._connected:
                return
            sub.bars = self._ib.reqRealTimeBars(contract, 5, what_to_show, use_rth)

        self._call_soon(_subscribe)

    def unsubscribe_bars(self, req: SubscribeRequest) -> None:
        if not self._loop:
            return

        def _unsubscribe() -> None:
            sub = self._bar_subscriptions.pop(req.vt_symbol, None)
            if not sub or not self._ib or not sub.bars:
                return
            try:
                self._ib.cancelRealTimeBars(sub.bars)
            except Exception as exc:
                self.gateway.write_log(f"IB cancel bars failed: {exc}")

        self._call_soon(_unsubscribe)

    def send_order(
        self,
        req: OrderRequest,
        stop_price: Optional[float] = None,
        limit_price: Optional[float] = None,
    ) -> str:
        if not self._loop or not self._ib:
            self.gateway.write_log("IB not connected")
            return ""

        fut = asyncio.run_coroutine_threadsafe(
            self._send_order_async(req, stop_price, limit_price),
            self._loop,
        )
        try:
            orderid = fut.result(timeout=5)
        except Exception as exc:
            self.gateway.write_log(f"IB send order failed: {exc}")
            return ""

        order = req.create_order_data(str(orderid), self.gateway_name)
        order.status = Status.SUBMITTING
        self.gateway.on_order(order)
        return order.vt_orderid

    def cancel_order(self, req: CancelRequest) -> None:
        if not self._loop:
            return

        def _cancel() -> None:
            if not self._ib:
                return
            try:
                self._ib.client.cancelOrder(int(req.orderid))
            except Exception as exc:
                self.gateway.write_log(f"IB cancel order failed: {exc}")

        self._call_soon(_cancel)

    def request_contract_details(
        self,
        symbol: str,
        exchange: str = "SMART",
        currency: str = "USD",
        sec_type: str = "STK",
        expiry: Optional[str] = None,
        timeout: float = 5.0,
    ) -> list[Any]:
        if not self._loop or not self._ib:
            return []

        async def _req() -> list[Any]:
            if not self._ib:
                return []
            contract = Contract()
            contract.symbol = symbol
            contract.exchange = exchange
            contract.currency = currency
            contract.secType = sec_type
            if expiry:
                contract.lastTradeDateOrContractMonth = expiry
            return await self._ib.reqContractDetailsAsync(contract)

        fut = asyncio.run_coroutine_threadsafe(_req(), self._loop)
        try:
            return fut.result(timeout=timeout)
        except Exception as exc:
            self.gateway.write_log(f"IB contract details failed: {exc}")
            return []

    def query_open_orders(self) -> None:
        if not self._loop:
            return

        def _query() -> None:
            if not self._ib or not self._connected:
                return

            async def _req() -> None:
                try:
                    await self._ib.reqAllOpenOrdersAsync()
                except Exception as exc:
                    self.gateway.write_log(f"IB reqAllOpenOrders failed: {exc}")

            asyncio.create_task(_req())

        self._call_soon(_query)

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        self._ib = IB()
        self._register_handlers()
        self._loop_ready.set()

        self._loop.run_forever()

    def _register_handlers(self) -> None:
        assert self._ib
        self._ib.connectedEvent += self._on_connected
        self._ib.disconnectedEvent += self._on_disconnected
        self._ib.openOrderEvent += self._on_order
        self._ib.orderStatusEvent += self._on_order
        self._ib.execDetailsEvent += self._on_trade
        self._ib.updatePortfolioEvent += self._on_portfolio
        self._ib.accountValueEvent += self._on_account_value
        self._ib.accountSummaryEvent += self._on_account_summary
        self._ib.pendingTickersEvent += self._on_tickers
        self._ib.barUpdateEvent += self._on_bar_update
        self._ib.errorEvent += self._on_error

    def _call_soon(self, func, *args) -> None:
        if self._loop:
            self._loop.call_soon_threadsafe(func, *args)

    def _ensure_connect(self) -> None:
        if not self._ib or not self._loop:
            return
        if self._connecting:
            return
        if self._ib.client.isConnected():
            return

        self._connecting = True

        async def _connect() -> None:
            try:
                await self._ib.connectAsync(
                    host=self.host,
                    port=self.port,
                    clientId=self.client_id,
                    account=self.account,
                    fetchFields=(
                        StartupFetch.POSITIONS
                        | StartupFetch.ORDERS_OPEN
                        | StartupFetch.ACCOUNT_UPDATES
                        | StartupFetch.SUB_ACCOUNT_UPDATES
                    ),
                )
            except Exception as exc:
                self.gateway.write_log(f"IB connect failed: {exc}")
            finally:
                self._connecting = False

        asyncio.create_task(_connect())

    async def _send_order_async(
        self,
        req: OrderRequest,
        stop_price: Optional[float],
        limit_price: Optional[float],
    ) -> int:
        assert self._ib

        contract = self._contract_from_symbol(req.symbol, req.exchange)
        action = DIRECTION_VT2IB.get(req.direction, "BUY")
        stop_price = float(stop_price) if stop_price is not None else None
        limit_price = float(limit_price) if limit_price is not None else None
        volume = float(req.volume)
        price = float(req.price)

        if req.type == OrderType.MARKET:
            order = MarketOrder(action, volume)
        elif req.type == OrderType.LIMIT:
            order = LimitOrder(action, volume, price)
        elif req.type == OrderType.STOP and limit_price is not None:
            stop = stop_price if stop_price is not None else price
            order = StopLimitOrder(action, volume, limit_price, stop)
        elif req.type == OrderType.STOP:
            stop = stop_price if stop_price is not None else price
            order = StopOrder(action, volume, stop)
        else:
            raise ValueError(f"Unsupported IB order type: {req.type}")

        order.tif = "GTC"
        if self.account:
            order.account = self.account

        trade = self._ib.placeOrder(contract, order)
        return trade.order.orderId

    def _on_connected(self) -> None:
        self._connected = True
        self.gateway.write_log("IB connected")
        if self._ib:
            for contract in self._subscribed.values():
                self._ib.reqMktData(contract)
            for sub in self._bar_subscriptions.values():
                sub.bars = self._ib.reqRealTimeBars(
                    sub.contract, 5, sub.what_to_show, sub.use_rth
                )
            asyncio.create_task(self._ib.reqAccountSummaryAsync())

    def _on_disconnected(self) -> None:
        self._connected = False
        self.gateway.write_log("IB disconnected")

    def _on_error(self, *args) -> None:
        try:
            req_id, code, msg, *_rest = args
            contract = _rest[0] if _rest else None
            extra = ""
            if contract is not None:
                try:
                    extra = (
                        f" [contract secType={getattr(contract, 'secType', None)}"
                        f" symbol={getattr(contract, 'symbol', None)}"
                        f" expiry={getattr(contract, 'lastTradeDateOrContractMonth', None)}"
                        f" exchange={getattr(contract, 'exchange', None)}"
                        f" currency={getattr(contract, 'currency', None)}"
                        f" conId={getattr(contract, 'conId', None)}]"
                    )
                except Exception:
                    extra = ""
            text = f"IB error {code} (req {req_id}): {msg}{extra}"
            if code == 2108:
                return
            if code in (2105, 2106):
                self.gateway.on_log(LogData(msg=text, gateway_name=self.gateway_name, level=DEBUG))
            else:
                self.gateway.write_log(text)
        except Exception:
            self.gateway.write_log(f"IB error: {args}")

    def _on_order(self, trade) -> None:
        order = trade.order
        contract = trade.contract
        status = trade.orderStatus.status

        exchange = self._exchange_from_contract(contract)
        symbol = self._symbol_from_contract(contract)

        data = OrderData(
            symbol=symbol,
            exchange=exchange,
            orderid=str(order.orderId),
            direction=DIRECTION_IB2VT.get(order.action),
            type=ORDERTYPE_IB2VT.get(order.orderType, OrderType.LIMIT),
            volume=float(order.totalQuantity),
            gateway_name=self.gateway_name,
        )

        if data.type == OrderType.LIMIT:
            data.price = float(order.lmtPrice or 0)
        elif data.type == OrderType.STOP:
            data.price = float(order.auxPrice or 0)

        data.traded = float(getattr(trade.orderStatus, "filled", 0) or 0)
        data.status = STATUS_IB2VT.get(status, Status.SUBMITTING)
        data.datetime = datetime.now()

        self.gateway.on_order(copy(data))

    def _on_trade(self, trade, fill) -> None:
        execution = fill.execution
        trade_id = execution.execId
        if not trade_id or trade_id in self._seen_trades:
            return
        self._seen_trades.add(trade_id)

        contract = trade.contract
        exchange = self._exchange_from_contract(contract)
        symbol = self._symbol_from_contract(contract)

        data = TradeData(
            symbol=symbol,
            exchange=exchange,
            orderid=str(trade.order.orderId),
            tradeid=trade_id,
            direction=DIRECTION_IB2VT.get(execution.side),
            price=float(execution.price),
            volume=float(execution.shares),
            datetime=execution.time,
            gateway_name=self.gateway_name,
        )
        self.gateway.on_trade(data)

    def _on_portfolio(self, item) -> None:
        contract = item.contract

        registry = getattr(self.gateway, "symbol_registry", None)
        if registry:
            try:
                sec_type = getattr(contract, "secType", None)
                currency = getattr(contract, "currency", None)
                conid = getattr(contract, "conId", None)
                if sec_type == "STK" and currency and currency.upper() == "USD" and conid:
                    registry.ensure_ib_symbol(
                        symbol=contract.symbol,
                        conid=conid,
                        currency=currency,
                    )
                elif sec_type == "FUT" and conid:
                    canonical = self._future_canonical_symbol(contract)
                    if canonical:
                        registry.ensure_ib_symbol(
                            symbol=canonical,
                            conid=conid,
                            asset_class="FUTURE",
                            currency=currency,
                            description=getattr(contract, "symbol", None),
                        )
            except Exception as exc:
                self.gateway.write_log(
                    f"Symbol registry update failed for IB holding {contract.symbol}: {exc}"
                )

        position = float(item.position)
        exchange = self._exchange_from_contract(contract)
        symbol = self._symbol_from_contract(contract)

        if position == 0:
            direction = self._last_position_direction.get(symbol, Direction.LONG)
            volume = 0.0
        else:
            direction = Direction.LONG if position > 0 else Direction.SHORT
            volume = abs(position)
            self._last_position_direction[symbol] = direction

        avg_cost = float(item.averageCost or 0)
        market_price = float(item.marketPrice or 0)
        pnl = float(item.unrealizedPNL or 0)

        pos = PositionData(
            symbol=symbol,
            exchange=exchange,
            direction=direction,
            volume=volume,
            price=avg_cost if avg_cost else market_price,
            pnl=pnl,
            gateway_name=self.gateway_name,
        )
        pos.last_price = market_price if market_price else None
        pos.market_value = (market_price * volume) if (market_price and volume) else None
        pos.cost = (avg_cost * volume) if (avg_cost and volume) else None
        pos.diluted_cost = avg_cost if avg_cost else None
        self.gateway.on_position(pos)

    def _on_account_value(self, value) -> None:
        self._update_account(value.account, value.tag, value.value, value.currency)

    def _on_account_summary(self, value) -> None:
        self._update_account(value.account, value.tag, value.value, value.currency)

    def _update_account(self, account: str, key: str, val: str, currency: str) -> None:
        if not currency or key not in ACCOUNTFIELD_IB2VT:
            return

        accountid = f"{account}.{currency}"
        account_data = self._accounts.get(accountid)
        if not account_data:
            account_data = AccountData(accountid=accountid, gateway_name=self.gateway_name)
            self._accounts[accountid] = account_data

        name = ACCOUNTFIELD_IB2VT[key]
        try:
            setattr(account_data, name, float(val))
        except ValueError:
            return

        if hasattr(account_data, "balance") and hasattr(account_data, "frozen"):
            try:
                account_data.available = account_data.balance - account_data.frozen
            except Exception:
                pass

        self.gateway.on_account(copy(account_data))

    @staticmethod
    def _future_canonical_symbol(contract: Contract) -> Optional[str]:
        root = getattr(contract, "symbol", None) or ""
        if not root:
            return None
        expiry = getattr(contract, "lastTradeDateOrContractMonth", None) or ""
        expiry_str = str(expiry)
        digits = "".join(ch for ch in expiry_str if ch.isdigit())
        if len(digits) < 6:
            return None
        yymm = digits[2:6]
        return f"{root.upper()}.{yymm}"

    @staticmethod
    def _option_canonical_symbol(contract: Contract) -> Optional[str]:
        root = getattr(contract, "symbol", None) or ""
        if not root:
            return None
        expiry = getattr(contract, "lastTradeDateOrContractMonth", None) or ""
        digits = "".join(ch for ch in str(expiry) if ch.isdigit())
        yymmdd = ""
        if len(digits) >= 8:
            yymmdd = digits[2:8]
        elif len(digits) == 6:
            yymmdd = digits
        right = getattr(contract, "right", None) or ""
        strike = getattr(contract, "strike", None)
        strike_str = ""
        if strike is not None:
            try:
                strike_val = float(strike)
                if strike_val.is_integer():
                    strike_str = str(int(strike_val))
                else:
                    strike_str = f"{strike_val:.8f}".rstrip("0").rstrip(".")
            except Exception:
                strike_str = str(strike)
        parts = [root.upper()]
        if yymmdd:
            parts.append(yymmdd)
        if right:
            parts.append(right.upper())
        if strike_str:
            parts.append(strike_str)
        if len(parts) == 1:
            return None
        return ".".join(parts)

    @staticmethod
    def _parse_future_symbol(symbol: str) -> Optional[tuple[str, str]]:
        if not symbol or "." not in symbol:
            return None
        root, suffix = symbol.split(".", 1)
        if len(suffix) != 4 or not suffix.isdigit():
            return None
        yyyymm = f"20{suffix}"
        return root.upper(), yyyymm

    def _on_tickers(self, tickers: set[Ticker]) -> None:
        for ticker in tickers:
            tick = self._ticker_to_tickdata(ticker)
            if tick:
                self.gateway.on_tick(tick)

    def _on_bar_update(self, bars: RealTimeBarList, has_new_bar: bool) -> None:
        if not has_new_bar or not bars:
            return
        bar = bars[-1]
        contract = getattr(bars, "contract", None)
        symbol = self._symbol_from_contract(contract) if contract else ""
        if not symbol and contract:
            symbol = getattr(contract, "symbol", "") or ""
        if not symbol:
            return

        open_val = getattr(bar, "open_", getattr(bar, "open", 0.0))
        high_val = getattr(bar, "high", 0.0)
        low_val = getattr(bar, "low", 0.0)
        close_val = getattr(bar, "close", 0.0)
        volume_val = getattr(bar, "volume", 0.0)
        vwap_val = getattr(bar, "wap", getattr(bar, "average", 0.0))

        close_display = close_val
        if close_display in (None, 0) or isNan(close_display):
            if vwap_val not in (None, 0) and not isNan(vwap_val):
                close_display = vwap_val
            else:
                close_display = open_val

        self.gateway.bar_cache[symbol] = {
            "time": getattr(bar, "time", None),
            "open": float(open_val),
            "high": float(high_val),
            "low": float(low_val),
            "close": float(close_val),
            "volume": float(volume_val),
            "vwap": float(vwap_val),
        }

        bar_time = getattr(bar, "time", None)
        local_now = datetime.now(PACIFIC_TZ)
        if isinstance(bar_time, datetime):
            if bar_time.tzinfo is None:
                bar_time = bar_time.replace(tzinfo=timezone.utc)
            bar_time = bar_time.astimezone(PACIFIC_TZ)
            data_label = bar_time.strftime("%H%M:%S")
        else:
            data_label = local_now.strftime("%H%M:%S")
        local_label = f"{local_now.strftime('%S')}.{local_now.microsecond // 1000:03d}"
        # No bar logs; keep UI clean.

    def _ticker_to_tickdata(self, ticker: Ticker) -> Optional[TickData]:
        contract = ticker.contract
        if not contract:
            return None

        exchange = self._exchange_from_contract(contract)
        symbol = self._symbol_from_contract(contract)
        dt = ticker.time or ticker.lastTimestamp or datetime.now()

        tick = TickData(
            symbol=symbol,
            exchange=exchange,
            datetime=dt,
            gateway_name=self.gateway_name,
        )

        def _val(value: float) -> float:
            return 0.0 if value is None or isNan(value) else float(value)

        tick.last_price = _val(ticker.last)
        tick.last_volume = _val(ticker.lastSize)
        tick.volume = _val(ticker.volume)
        tick.open_price = _val(ticker.open)
        tick.high_price = _val(ticker.high)
        tick.low_price = _val(ticker.low)
        tick.pre_close = _val(ticker.close)
        tick.bid_price_1 = _val(ticker.bid)
        tick.ask_price_1 = _val(ticker.ask)
        tick.bid_volume_1 = _val(ticker.bidSize)
        tick.ask_volume_1 = _val(ticker.askSize)
        tick.localtime = datetime.now()

        return tick

    def _exchange_from_contract(self, contract: Contract) -> Exchange:
        if contract.exchange:
            exchange = EXCHANGE_IB2VT.get(contract.exchange)
        elif contract.primaryExchange:
            exchange = EXCHANGE_IB2VT.get(contract.primaryExchange)
        else:
            exchange = None
        return exchange or Exchange.SMART

    def _symbol_from_contract(self, contract: Contract) -> str:
        registry = getattr(self.gateway, "symbol_registry", None)
        conid = getattr(contract, "conId", None)
        if registry and conid:
            record = registry.get_by_ib_conid(conid)
            if record:
                return record.canonical_symbol
        sec_type = getattr(contract, "secType", None)
        if sec_type in ("OPT", "FOP"):
            canonical = self._option_canonical_symbol(contract)
            if canonical:
                return canonical
        if sec_type == "FUT":
            canonical = self._future_canonical_symbol(contract)
            if canonical:
                return canonical
        symbol = getattr(contract, "symbol", "") or ""
        if symbol:
            return symbol
        if conid:
            return str(conid)
        return ""

    def _contract_from_symbol(self, symbol: str, exchange: Exchange) -> Contract:
        if symbol.isdigit():
            registry = getattr(self.gateway, "symbol_registry", None)
            if registry:
                record = registry.get_by_ib_conid(int(symbol))
                if record and record.asset_class == "FUTURE":
                    future_parts = self._parse_future_symbol(record.canonical_symbol)
                    if future_parts:
                        root, yyyymm = future_parts
                        contract = Contract()
                        contract.conId = int(symbol)
                        contract.secType = "FUT"
                        contract.symbol = root
                        ib_exchange = EXCHANGE_VT2IB.get(exchange, "CME")
                        if ib_exchange == "SMART":
                            ib_exchange = "CME"
                        contract.exchange = ib_exchange
                        contract.currency = record.currency or "USD"
                        return contract
            contract = Contract()
            contract.conId = int(symbol)
            contract.secType = "STK"
            contract.exchange = "SMART"
            contract.currency = "USD"
            return contract

        future_parts = self._parse_future_symbol(symbol)
        if future_parts:
            root, yyyymm = future_parts
            contract = Contract()
            contract.secType = "FUT"
            contract.symbol = root
            contract.lastTradeDateOrContractMonth = yyyymm
            ib_exchange = EXCHANGE_VT2IB.get(exchange, "CME")
            if ib_exchange == "SMART":
                ib_exchange = "CME"
            contract.exchange = ib_exchange
            contract.currency = "USD"
            return contract

        ib_exchange = EXCHANGE_VT2IB.get(exchange, "SMART")
        return Stock(symbol.upper(), ib_exchange, "USD")


class JanusIbGateway(BaseGateway):
    def __init__(self, event_engine, gateway_name: str) -> None:
        super().__init__(event_engine, gateway_name)
        self.api = IbAsyncApi(self)
        self.symbol_registry = None
        self.bar_cache: Dict[str, Dict[str, Any]] = {}
        self._timer_count = 0

    def connect(self, setting: dict) -> None:
        self.symbol_registry = setting.get("symbol_registry")

        host: Optional[str] = setting.get("host") or setting.get("TWS地址")
        port: Optional[int] = setting.get("port") or setting.get("TWS端口")
        client_id: Optional[int] = setting.get("client_id") or setting.get("客户号")
        account: str = setting.get("account") or setting.get("交易账户") or ""

        if host is None or port is None or client_id is None:
            raise ValueError("Missing IB connection settings (host/port/client_id)")

        self.api.connect(host, port, client_id, account)
        self.event_engine.register(EVENT_TIMER, self.process_timer_event)

    def close(self) -> None:
        self.api.close()

    def subscribe(self, req: SubscribeRequest) -> None:
        self.api.subscribe(req)

    def subscribe_bars(
        self,
        req: SubscribeRequest,
        what_to_show: str = "TRADES",
        use_rth: bool = False,
    ) -> None:
        self.api.subscribe_bars(req, what_to_show=what_to_show, use_rth=use_rth)

    def unsubscribe_bars(self, req: SubscribeRequest) -> None:
        self.api.unsubscribe_bars(req)

    def send_order(self, req: OrderRequest | dict) -> str:
        stop_price = None
        limit_price = None
        if isinstance(req, dict):
            stop_price = req.pop("stop_price", None)
            limit_price = req.pop("limit_price", None)
            req = OrderRequest(**req)
        else:
            stop_price = getattr(req, "stop_price", None)
            limit_price = getattr(req, "limit_price", None)
        return self.api.send_order(req, stop_price=stop_price, limit_price=limit_price)

    def cancel_order(self, req: CancelRequest) -> None:
        self.api.cancel_order(req)

    def query_account(self) -> None:
        pass

    def query_position(self) -> None:
        pass

    def query_open_orders(self) -> None:
        self.api.query_open_orders()

    def request_contract_details(self, *args, **kwargs):
        return self.api.request_contract_details(*args, **kwargs)

    def process_timer_event(self, event: Event) -> None:
        self._timer_count += 1
        if self._timer_count < 10:
            return
        self._timer_count = 0
        self.api.check_connection()

    def _apply_canonical_symbol(self, symbol: str) -> Optional[str]:
        registry = self.symbol_registry
        if not registry or not symbol:
            return None
        record = None
        try:
            conid = int(symbol)
        except (TypeError, ValueError):
            conid = None
        if conid is not None:
            record = registry.get_by_ib_conid(conid)
        if not record:
            record = registry.get_by_canonical(symbol)
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
