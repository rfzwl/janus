# ib_async Study Notes

## Scope
Study `../ib_async` to understand how it replaces IBAPI, and extract the pieces we can reuse for a vn.py gateway rewrite in Janus (no `vnpy_ib`, no `ibapi`).

## Project Structure (ib_async)
- Package root: `../ib_async/ib_async/`
- Core modules:
- `ib.py` high-level facade with sync + async APIs, owns `Client` + `Wrapper`.
- `client.py` async socket client, request throttling, IB protocol serialization.
- `decoder.py` message dispatch (msgId -> wrapper method).
- `wrapper.py` stateful event handler and local cache for orders, fills, positions, portfolio, tickers.
- `connection.py` asyncio.Protocol socket transport.
- `contract.py`, `order.py`, `objects.py`, `ticker.py` data models.

## Key Architecture Patterns
- Async networking with asyncio.
- `Connection` emits events on data received and disconnects. It is a thin `asyncio.Protocol` wrapper.
- `Client` replaces `ibapi.client.EClient` and implements raw IBKR socket protocol.
- `Decoder` maps message IDs to handler functions and calls `Wrapper` methods.
- `Wrapper` is stateful and updates caches, emits events, and completes request futures.
- `IB` provides a blocking facade that still runs the event loop, plus async methods for advanced use.

## Connection + Protocol Flow
- `Client.connect()` uses `connectAsync()` then sends the API handshake ("API\0" + version range).
- Connection is considered ready after `nextValidId` and `managedAccounts` arrive.
- `Client.send()` serializes fields into the IB protocol: NUL-separated fields with length prefix.
- `Client` maintains reqId sequence and exposes `getReqId()` for caller-managed ids.
- Request throttling: `MaxRequests` and `RequestsInterval` queue requests and emit throttle events.

## State and Request Lifecycle
- `Wrapper` maintains per-request result buffers (`_results`) and completes them on *_End callbacks.
- Blocking methods in `IB` call `*_Async` and wait using `util.run()` while the loop keeps processing.
- `Wrapper` maintains caches that stay in sync:
- `trades`, `permId2Trade`, `fills`
- `positions`, `portfolio`
- `tickers` and `pendingTickers`

## Orders and Fills
- `IB.placeOrder()` always creates or updates a local `Trade` with `OrderStatus` and log entries.
- `Wrapper.openOrder()` backfills or creates `Trade` objects from open-order feeds.
- `Wrapper.orderStatus()` updates the existing `Trade` and emits status events.
- `Wrapper.execDetails()` creates `Fill` objects and associates with `Trade`.
- Local caches allow incomplete IB callbacks to be merged with previously known order fields.

## Positions and Portfolio
- `Wrapper.updatePortfolio()` updates a per-account portfolio cache, removing items on zero position.
- `Wrapper.position()` updates per-account positions, removing items on zero quantity.
- Both emit events immediately (push model), and requests return lists via `_results` when needed.

## Market Data (Tickers)
- `Client.reqMktData()` builds fields for contracts, including BAG combo legs and delta-neutral legs.
- `Wrapper` consolidates ticks into `Ticker` with tick-type maps (PRICE_TICK_MAP, SIZE_TICK_MAP, etc.).
- `Ticker` stores full L1/L2/tick-by-tick state and emits `updateEvent`.

## Notable Implementation Details
- `Client.send()` normalizes IB "unset" values to empty strings when requested.
- `Client` includes optional hooks `tcpDataArrived` and `tcpDataProcessed` for batch timestamping.
- `Wrapper` uses `OrderKey` logic to reconcile `orderId`, `clientId`, and `permId` updates.
- `IB.reqContractDetails()` treats empty or multi-result responses as missing or ambiguous.

## References (files)
- `../ib_async/ib_async/ib.py`
- `../ib_async/ib_async/client.py`
- `../ib_async/ib_async/connection.py`
- `../ib_async/ib_async/decoder.py`
- `../ib_async/ib_async/wrapper.py`
- `../ib_async/ib_async/contract.py`
- `../ib_async/ib_async/order.py`
- `../ib_async/ib_async/objects.py`
- `../ib_async/ib_async/ticker.py`
