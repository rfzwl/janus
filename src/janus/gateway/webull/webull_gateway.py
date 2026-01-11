# src/janus/gateway/webull/webull_gateway.py

import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

# Official SDK imports based on your provided code
from webull.core.client import ApiClient
from webull.trade.trade_client import TradeClient

from vnpy.trader.gateway import BaseGateway
from vnpy.trader.object import (
    SubscribeRequest, OrderRequest, CancelRequest,
    TickData, OrderData, TradeData, PositionData, AccountData,
    ContractData
)
from vnpy.trader.constant import (
    Direction, Exchange, OrderType, Product, Status
)
from vnpy.event import Event

# Mapping Constants
DIRECTION_VT2WB = {
    Direction.LONG: 'BUY',
    Direction.SHORT: 'SELL'
}
DIRECTION_WB2VT = {v: k for k, v in DIRECTION_VT2WB.items()}

STATUS_WB2VT = {
    'Pending': Status.NOTTRADED,
    'Working': Status.NOTTRADED,
    'PartiallyFilled': Status.PARTTRADED, # Note: Check exact API string
    'Filled': Status.ALLTRADED,
    'Cancelled': Status.CANCELLED,
    'Failed': Status.REJECTED,
}

class WebullOfficialGateway(BaseGateway):
    """
    vn.py Gateway for Webull Official Open API
    Ref: https://github.com/genliusrocks/webull-cli-trader
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
        self.query_interval = 2 # Seconds

        # Cache
        self.orders_map = {} # vt_orderid -> wb_order_id

    def connect(self, setting: Dict[str, Any]):
        """
        Connect using Webull Open API Key/Secret
        """
        self.app_key = setting.get("app_key", "")
        self.app_secret = setting.get("app_secret", "")
        self.region_id = setting.get("region_id", "us")

        if not self.app_key or not self.app_secret:
            self.on_log("Error: Missing Webull App Key or Secret.")
            return

        try:
            self.on_log("Initializing Webull Open API Client...")
            
            # 1. Init Client (Ref: webull-cli-trader/app/adapter.py)
            self.api_client = ApiClient(self.app_key, self.app_secret, self.region_id)
            # Ensure endpoint is set (Ref: adapter.py)
            self.api_client.add_endpoint(self.region_id, "api.webull.com") 
            
            self.trade_client = TradeClient(self.api_client)
            
            # 2. Get Account ID (Ref: adapter.py: get_first_account_id)
            response = self.trade_client.account_v2.get_account_list()
            if response.status_code != 200:
                 self.on_log(f"Login failed / Get Account failed: {response.text}")
                 return
            
            data = response.json()
            # Handle different response structures as seen in adapter.py
            acct_list = data.get("data", []) if isinstance(data, dict) else data
            
            if not acct_list:
                self.on_log("No Webull accounts found.")
                return

            # Pick first account
            first = acct_list[0]
            self.account_id = str(first.get("account_id") or first.get("secAccountId"))
            
            self.on_log(f"Webull Connected. Account ID: {self.account_id}")

            # 3. Start Polling
            self.active = True
            self.poll_thread = threading.Thread(target=self._polling_loop)
            self.poll_thread.start()

        except Exception as e:
            self.on_log(f"Connection Exception: {e}")

    def send_order(self, req: OrderRequest) -> str:
        """
        Place Order via Open API
        """
        if not self.trade_client:
            return ""

        try:
            # Construct Order Dict (Ref: webull-cli-trader trade.py logic)
            # Note: Webull Open API typically requires Ticker ID. 
            # If req.symbol is "AAPL", this might fail if API expects "913243251".
            # For MVP, assume user passes Ticker ID in symbol or API is smart enough.
            
            payload = {
                "tickerId": int(req.symbol) if req.symbol.isdigit() else req.symbol, # Attempt to handle ID
                "action": DIRECTION_VT2WB.get(req.direction, 'BUY'),
                "orderType": 'LMT' if req.type == OrderType.LIMIT else 'MKT',
                "quantity": int(req.volume),
                "timeInForce": "GTC", # Default to GTC for now
            }
            
            if req.type == OrderType.LIMIT:
                payload["lmtPrice"] = str(req.price) # API usually expects string for decimal precision

            self.on_log(f"Placing Order: {payload}")
            
            # Call SDK
            # Note: Verify exact method signature in your SDK version. 
            # Based on standard usage: place_order(account_id, params)
            resp = self.trade_client.trade.place_order(self.account_id, payload)
            
            if resp.status_code == 200:
                data = resp.json()
                # Extract Order ID
                wb_order_id = str(data.get('data', {}).get('orderId'))
                
                # Generate local VT ID
                order = req.create_order_data(wb_order_id, self.gateway_name)
                self.on_order(order)
                return order.vt_orderid
            else:
                self.on_log(f"Order Failed: {resp.text}")
                return ""

        except Exception as e:
            self.on_log(f"Send Order Error: {e}")
            return ""

    def cancel_order(self, req: CancelRequest):
        if not self.trade_client:
            return
        
        try:
            # req.orderid is the Webull Order ID
            self.trade_client.trade.cancel_order(self.account_id, req.orderid)
            self.on_log(f"Cancel sent for {req.orderid}")
        except Exception as e:
            self.on_log(f"Cancel Error: {e}")

    def close(self):
        self.active = False
        if self.poll_thread:
            self.poll_thread.join()

    def _polling_loop(self):
        """
        Poll Account & Orders
        """
        while self.active:
            try:
                self._poll_account()
                self._poll_orders()
                # self._poll_positions() # Optional
            except Exception as e:
                self.on_log(f"Polling Error: {e}")
            
            time.sleep(self.query_interval)

    def _poll_account(self):
        # Ref: adapter.py -> get_account_balances logic (inferred)
        # Assuming trade_client.account.get_account_balance(acct_id)
        pass # Implement based on specific SDK response structure

    def _poll_orders(self):
        # Query Open Orders
        resp = self.trade_client.trade.get_open_orders(self.account_id)
        if resp.status_code == 200:
            data = resp.json()
            orders_list = data.get('data', [])
            
            for item in orders_list:
                # Map Webull Order Dict -> OrderData
                # This depends on exact JSON fields (e.g., 'orderId', 'status', 'filledQuantity')
                pass