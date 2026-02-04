# Webull OpenAPI Summary

Sources:
- `https_developer.webull.com_apis_home_260202/developer.webull.com/apis/docs/` (static HTML snapshot)
- `https://developer.webull.com/api-doc/trade/order/place-order/` (live docs)
- `https://developer.webull.com/api-doc/develop/dictionary/` (OrderType/OrderTIF reference)
- `https://developer.webull.co.jp/api-doc/develop/dictionary/` (JP dictionary; includes GTC)

Notes:
- Authentication uses a digest signature with App Key/App Secret; HTTPS is required.
- The SDK can generate signatures; protect App Key/App Secret credentials.
- Optional 2FA requires a token parameter; tokens default to 15 days and require in-app verification.
- Connect API uses OAuth 2.0 for third-party account authorization.
- Market Data API supports HTTP and streaming (WebSocket/TCP) access; advanced quotes are a subscription service.
- Trading API supports trading and order status change subscriptions via HTTP and gRPC; broker API is under construction.

## Trading Order Types (OpenAPI Dictionary)
- OrderType (U.S. stock, US site): `LIMIT`, `MARKET`, `STOP_LOSS`, `STOP_LOSS_LIMIT`, `TRAILING_STOP_LOSS`.
- OrderTIF (US site): `DAY` only.
- OrderTIF (JP site): includes `DAY` and `GTC`.
- Conclusion: Webull US docs only guarantee `DAY`; `GTC` support is unclear. If `GTC` is required, test and fall back to `DAY` on errors.

## Place Order (U.S. stocks/ETFs)
- Endpoint: `POST /trade/order/place`
- `stock_order` required fields include:
- `client_order_id`, `side` (BUY/SELL), `tif`, `instrument_id`, `order_type`, `qty`, `extended_hours_trading`.
- `limit_price` required for `LIMIT` and `STOP_LOSS_LIMIT`; must be >0.
- `stop_price` required for `STOP_LOSS`, `STOP_LOSS_LIMIT`, `TRAILING_STOP_LOSS`; must be >0.
- `trailing_type` + `trailing_stop_step` required for `TRAILING_STOP_LOSS`.
- `extended_hours_trading=true` is only allowed for `LIMIT` orders.

## API Endpoints

### Authentication

#### General

| Name | Method | Path | Notes |
| --- | --- | --- | --- |
| Check Token | POST | `/openapi/auth/token/check` | - Function description: Query Token Status. This API is used to check the validity of a given token. If the status is NORMAL, the token is active and can be used normally. If the status is PENDING, the token is pending verification and requires a mobile verification code via the Webull App. If the status is INVALID, the token is invalid and must be regenerated. If the status is EXPIRED, the token has expired and must be regenerated. Rate limit: 10 requests every 30 seconds |
| Create Token | POST | `/openapi/auth/token/create` | - Function description: Create an access token. This interface is used to generate a new Token, which is the credential for accessing other API interfaces. Upon successful creation, it returns a response containing Token information, expiration time, and status. The Token status defaults to 'Pending Verification' and requires verification via Webull App SMS code. Tokens are time-sensitive (default 15 days) and need to be refreshed before expiration. Rate limit: 10 requests every 30 seconds |

### Connect API

#### General

| Name | Method | Path | Notes |
| --- | --- | --- | --- |
| Create And Refresh Token | POST | `/openapi/oauth2/token` | This is the second step of the OAuth process. An access token is created using the authorization code from the first step's response. The access token is a key used for API access. These tokens should be protected like passwords. |
| Get An Authorization Code | GET | `/oauth2/authenticate/login` | This is the first step of the OAuth2 process. An authorization code is created when the user authorizes your application to access their account. If the user grants permission to your application, the callback URL registered in your application will be invoked. The interface for obtaining the authorization code is completed in the browser. 'SEND API REQUEST' function for this endpoint does not work in UAT environment. |

### Market Data API

#### Crypto

| Name | Method | Path | Notes |
| --- | --- | --- | --- |
| Historical Bars | GET | `/openapi/market-data/crypto/bars` | Retrieve historical candlestick (K-line) data for a specified crypto symbol. Supports multiple time intervals such as M1, M5, H1, D, etc. Daily and higher intervals return forward-adjusted bars; minute intervals return non-adjusted bars. Supports retrieving the most recent N bars: - Range: 1-1200 bars (all intervals) Rate Limits: - 1 request per second per App Key - Market Data Global Limit: 600 requests per minute |
| Snapshot | GET | `/openapi/market-data/crypto/snapshot` | Retrieve real-time market snapshot data for one or more crypto symbols. The response includes key market indicators such as latest price, price change, price change percentage, bid/ask quotes, and other real-time metrics. Supports querying up to 20 symbols per request. Rate Limits: - 1 request per second per App Key - Market Data Global Limit: 600 requests per minute |

#### Event

| Name | Method | Path | Notes |
| --- | --- | --- | --- |
| Depth | GET | `/openapi/market-data/event/depth` | - Function description: Get the current order book for a specific event instrument. The order book shows all active bid orders for both yes and no sides of a binary market. It returns yes bids and no bids only (no asks are returned). This is because in binary markets, a bid for yes at price X is equivalent to an ask for no at price (100-X). For example, a yes bid at 6 cents is the same as a no ask at 94 cents, with identical contract sizes. Rate limit: Market-data interfaces limit 600 requests per minute |
| Snapshot | GET | `/openapi/market-data/event/snapshot` | - Function description: Get real-time market snapshot data for a event instrument. Rate limit: Market-data interfaces limit 600 requests per minute |

#### Futures

| Name | Method | Path | Notes |
| --- | --- | --- | --- |
| Depth of Book | GET | `/openapi/market-data/futures/depth` | Get the latest bid/ask data for a security with a level-2 subscription. Returns bid/ask information for a specified depth, including price, quantity. Rate limit: 1 call per second per App Key. |
| Footprint | GET | `/openapi/market-data/futures/footprint` | - Function description: Query the most recent N footprint records based on futures symbol, and category, time granularity. Rate limit: Market-data interfaces limit is 600 requests per minute. |
| Historical Bars | GET | `/openapi/market-data/futures/bars` | Batch query interface. Query the recent N bars of data based on futures symbols, time granularity, and type. Supports historical bars of various granularities like M1, M5, etc. Currently, daily bars (D) and above only provide forward-adjusted bars; minute bars provide unadjusted bars. Rate limit: 1 call per second per App Key. |
| Snapshot | GET | `/openapi/market-data/futures/snapshot` | Get real-time market snapshot data for a security. Returns key market indicators such as latest price, price change, volume, turnover rate, etc. Rate limit: 1 call per second per App Key. |
| Tick | GET | `/openapi/market-data/futures/tick` | Get tick-by-tick trade data for a security. Returns detailed tick trade records within a specified time range for a given security, including trade time, price, volume, direction. Data is sorted in reverse chronological order (latest first). Rate limit: 1 call per second per App Key. |

#### Stock

| Name | Method | Path | Notes |
| --- | --- | --- | --- |
| Footprint | GET | `/openapi/market-data/stock/footprint` | - Function description: Query the most recent N footprint records based on stock symbol, and category, time granularity. Rate limit: Market-data interfaces limit is 600 requests per minute. |
| Historical Bars | POST | `/openapi/market-data/stock/batch-bars` | - Function description: Batch query interface. Query the recent N bars of data based on stock symbols, time granularity, and type. Supports historical bars of various granularities like M1, M5, etc. Currently, daily bars (D) and above only provide forward-adjusted bars; minute bars provide unadjusted bars. Rate limit: Market-data interfaces limit is 600 requests per minute. |
| Historical Bars (single symbol) | GET | `/openapi/market-data/stock/bars` | - Function description: Query the recent N bars of data based on stock symbol, time granularity, and type. Supports historical bars of various granularities like M1, M5, etc. Currently, daily bars (D) and above only provide forward-adjusted bars; minute bars provide unadjusted bars. Rate limit: Market-data interfaces limit is 600 requests per minute. |
| Quotes | GET | `/openapi/market-data/stock/quotes` | - Function description: Get the latest bid/ask data for a security. Returns bid/ask information for a specified depth, including price, quantity, order details, etc. Rate limit: Market-data interfaces limit is 600 requests per minute. |
| Snapshot | GET | `/openapi/market-data/stock/snapshot` | - Function description: Get real-time market snapshot data for a security. Returns key market indicators such as latest price, price change, volume, turnover rate, etc. Supports querying various security types including US stocks, with optional inclusion of pre-market, after-hours, and overnight trading data. Rate limit: Market-data interfaces limit is 600 requests per minute. |
| Tick | GET | `/openapi/market-data/stock/tick` | - Function description: Get tick-by-tick trade data for a security. Returns detailed tick trade records within a specified time range for a given security, including trade time, price, volume, direction, and other details. Data is sorted in reverse chronological order (latest first). Rate limit: Market-data interfaces limit is 600 requests per minute. |

#### Streaming

| Name | Method | Path | Notes |
| --- | --- | --- | --- |
| Subscribe | POST | `/openapi/market-data/streaming/subscribe` | - Function description: Subscribe to real-time market data streaming. This interface allows you to subscribe to various types of market data including quotes, snapshots, and tick data for specified securities. Rate limit: Market-data interfaces limit is 600 requests per minute. |
| Unsubscribe | POST | `/openapi/market-data/streaming/unsubscribe` | - Function description: After successfully establishing the market data streaming MQTT connection, call this interface to unsubscribe from real-time market data push. Successful call returns no value; failures return an Error. Unsubscribing will release the topic quota. Rate limit: 1 call per second per App Key; Market-data interfaces limit is 600 requests per minute. |

### Trading API

#### Account

| Name | Method | Path | Notes |
| --- | --- | --- | --- |
| Account List | GET | `/openapi/account/list` | - Function description: Query the account list and return account information. Rate limit: 10 requests every 30 seconds |

#### Assets

| Name | Method | Path | Notes |
| --- | --- | --- | --- |
| Account Balance | GET | `/openapi/assets/balance` | - Function description: Query account details by account ID. Rate limit: 2 requests every 2 seconds |
| Account Positions | GET | `/openapi/assets/positions` | - Function description: Query positions according to the account ID. Rate limit: 2 requests every 2 seconds |

#### Instrument

| Name | Method | Path | Notes |
| --- | --- | --- | --- |
| Get Crypto Instrument | GET | `/openapi/instrument/crypto/list` | - Function description: Get profile information for one or more instruments. Rate limit: 60 requests every 60 seconds |
| Get Event Instrument | GET | `/openapi/instrument/event/market/list` | - Function: Retrieve profile information for event contract markets based on the series symbol. Rate limit: 60 requests per 60 seconds. |
| Get Event Series | GET | `/openapi/instrument/event/series/list` | Function: Retrieve multiple series with specified filters. A series represents a template for recurring events that follow the same format and rules (e.g., "Monthly Jobs Report"). This endpoint allows you to browse and discover available series templates by category. Rate limit: 60 requests per 60 seconds. |
| Get Futures Instrument | GET | `/openapi/instrument/futures/list` | - Function: Retrieve profile information for one or multiple futures trading instruments by symbol(s). Rate limit: 60 requests per 60 seconds. |
| Get Futures Instrument By Code | GET | `/openapi/instrument/futures/by-code` | - Function: Retrieve profile information for tradable futures trading instruments based on the futures product code. Rate limit: 60 requests per 60 seconds. |
| Get Futures Products | GET | `/openapi/instrument/futures/products` | - Retrieve all futures underlying products and their corresponding product codes, returned as a list. Rate limit: 60 requests per 60 seconds. |
| Get Stock Instrument | GET | `/openapi/instrument/stock/list` | - Function description: Get profile information for one or more instruments. Rate limit: 60 requests every 60 seconds |

#### Order

| Name | Method | Path | Notes |
| --- | --- | --- | --- |
| Cancel Options | POST | `/openapi/trade/option/order/cancel` | - Function description: Cancel options orders according to the incoming client_order_id. Only supports OPTION. Rate limit: 600 requests per minute |
| Cancel Order | POST | `/openapi/trade/stock/order/cancel` | - Function description: Cancel the equity order according to the incoming client_order_id. Only supports EQUITY. Rate limit: 600 requests per minute |
| Open Order | GET | `/openapi/trade/order/open` | - Function description: Query pending orders by page, and modify or cancel orders based on client_order_id. Rate limit: 2 requests every 2 seconds |
| Order Batch Place | POST | `/openapi/trade/order/batch-place` | - Function description: Batch Place order, allows multiple orders to be submitted at once. A maximum of 50 orders can be submitted once. Currently only stocks are supported. This service is not currently available to all clients. Please contact Webull if you require assistance. Rate limit: 600 requests per minute |
| Order Cancel | POST | `/openapi/trade/order/cancel` | - Function description: Cancel orders for equities, options, futures and cryptos according to the incoming account_id and client_order_id. Rate limit: 600 requests per minute |
| Order Detail | GET | `/openapi/trade/order/detail` | - Function description: Order details, query the specified order details through the order ID. Rate limit: 2 requests every 2 seconds |
| Order History | GET | `/openapi/trade/order/history` | - Function description: Historical orders, query the records of the past 7 days. If they are group orders, will be returned together, and the number of orders returned on one page may exceed the page_size. Rate limit: 2 requests every 2 seconds |
| Order Place | POST | `/openapi/trade/order/place` | - Function description: Place equity orders (preferred), including simple orders. For futures, only quantity orders are supported. Please note: When selling crypto, your position must not fall below $2 after placing the order. Rate limit: 600 requests per minute |
| Order Preview | POST | `/openapi/trade/order/preview` | - Function description: Calculate the estimated amount and cost based on the incoming information, and support simple orders. For crypto trading, this feature is currently not supported. Rate limit: 150 requests every 10 seconds |
| Order Replace | POST | `/openapi/trade/order/replace` | - Function description: Modify equity, options and futures orders, including simple orders. For crypto trading, this feature is currently not supported. Futures order modification rules: - For market orders, only `quantity` can be modified. For limit orders, only `order_type`, `time_in_force`, `quantity` and `limit_price` can be modified; if modifying `order_type`, it can only be changed to `market`. For stop orders, only `order_type`, `time_in_force`, `quantity` and `stop_price` can be modified; if modifying `order_type`, it can only be changed to `market`. For stop limit orders, only `order_type`, `time_in_force`, `quantity`, `limit_price` and `stop_price` can be modified; if modifying `order_type`, it can only be changed to `limit`. For trailing stop orders, only `trailing_stop_step` can be modified; `order_type` , `trailing_type` and `time_in_force` cannot be modified. Rate limit: 600 requests per minute |
| Place Options | POST | `/openapi/trade/option/order/place` | - Function description: Place options orders. Only supports OPTION. Rate limit: 600 requests per minute |
| Place Order | POST | `/openapi/trade/stock/order/place` | - Function description: Place equity orders (preferred), including simple orders. Only supports EQUITY. Rate limit: 600 requests per minute |
| Preview Options | POST | `/openapi/trade/option/order/preview` | - Function description: Calculate the estimated amount and cost of options orders according to the incoming information. Only supports OPTION. Rate limit: 150 requests every 10 seconds |
| Preview Order | POST | `/openapi/trade/stock/order/preview` | - Function description: Calculate the estimated amount and cost based on the incoming information, and support simple orders. Only supports EQUITY. Rate limit: 150 requests every 10 seconds |
| Replace Options | POST | `/openapi/trade/option/order/replace` | - Function description: Updates an existing order with new parameters; each one overrides the corresponding attribute. Only supports OPTION. Rate limit: 600 requests per minute |
| Replace Order | POST | `/openapi/trade/stock/order/replace` | - Function description: Updates an existing order with new parameters; each one overrides the corresponding attribute. Only supports EQUITY. Rate limit: 600 requests per minute |
