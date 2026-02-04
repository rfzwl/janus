# IB Gateway Rewrite Notes (ib_async)

## Scope
Notes for rewriting Janus IB gateway using `ib_async` instead of `vnpy_ib` or `ibapi`.

## Current Janus State
- IB gateway is `src/janus/gateway/ib/ib_gateway.py` and uses `ib_async` directly.
- Server wires broker map in `src/janus/server.py` as `"ib": JanusIbGateway`.
- `JanusIbGateway.request_contract_details()` is used by `JanusServer.harmony()` for symbol registry fill.
- Symbol normalization and conId mapping are handled in `src/janus/symbol_registry.py`.

## Target Constraints
- No `vnpy_ib` dependency.
- No `ibapi` dependency.
- Preserve vn.py BaseGateway contract: non-blocking, thread-safe, reconnect-capable.

## Integration Sketch (ib_async -> vn.py)
- Use `ib_async.IB` as the core client.
- Run `ib_async` on a dedicated thread with its own asyncio event loop.
- Expose thread-safe methods on the gateway that schedule work on the IB loop.
- Register ib_async events and translate to vn.py `on_*` events.

## Recommended Components
- `IbAsyncGateway` (BaseGateway) as the vn.py entry.
- `IbAsyncApi` internal adapter that owns `IB`, `Wrapper`, and event loop thread.
- Small mapping layer for:
- Contract conversion (IB Contract -> vn.py ContractData, SubscribeRequest -> IB Contract).
- Order conversion (vn.py OrderRequest -> ib_async Order + Contract).
- Status mapping (IB OrderStatus strings -> vn.py Status).
- Tick conversion (ib_async Ticker or TickData -> vn.py TickData).

## Event Mapping (ib_async -> vn.py)
- Orders:
- `IB.openOrderEvent` and `IB.orderStatusEvent` -> `on_order`.
- Trades/Fills:
- `IB.execDetailsEvent` -> `on_trade`.
- Positions:
- `IB.positionEvent` -> `on_position`.
- Accounts/Portfolio:
- `IB.accountValueEvent` + `IB.updatePortfolioEvent` -> `on_account` and `on_position` (if needed).
- Market data:
- `IB.pendingTickersEvent` or `Ticker.updateEvent` -> `on_tick`.

## Threading Considerations
- vn.py expects gateway calls to be non-blocking; all IB calls should be queued to the IB loop thread.
- Use `asyncio.run_coroutine_threadsafe` or `loop.call_soon_threadsafe` to interact with the IB loop.
- Do not call `ib_async.util.run()` on the vn.py thread, or it can block the event engine.

## Startup Flow (Proposed)
- Gateway `connect()`:
- Start IB loop thread.
- `IB.connectAsync(host, port, clientId)`.
- When `connectedEvent` fires, request initial data:
- `reqAccountUpdates` (or account summary), `reqPositions`, `reqOpenOrders`.
- For Janus symbol registry, expose a blocking method that wraps `reqContractDetails` with timeout.

## Contract and Symbol Handling
- Janus expects conId-based IB mapping from registry.
- For IB equities, use `Contract(conId=..., secType="STK", exchange="SMART", currency="USD")` when conId is known.
- For ticker-only requests, use `Stock(symbol, "SMART", "USD")` and call `reqContractDetails` to qualify.

## Order Mapping Notes
- vn.py `OrderRequest` -> `ib_async.order.Order`:
- MARKET -> `MarketOrder`
- LIMIT -> `LimitOrder`
- STOP -> `StopOrder`
- STOP_LIMIT -> `StopLimitOrder` if supported
- Action: `Direction.LONG` -> BUY, `Direction.SHORT` -> SELL
- Time-in-force: map vn.py requests to IB `tif` when available (DAY/GTC).
- Webull order_type (U.S. stock): `MARKET`, `LIMIT`, `STOP_LOSS`, `STOP_LOSS_LIMIT`, `TRAILING_STOP_LOSS`.
- Webull `stop_price` is required for STOP_LOSS/STOP_LOSS_LIMIT/TRAILING_STOP_LOSS.
- Webull `limit_price` is required for LIMIT/STOP_LOSS_LIMIT.
- Webull `tif` documented as DAY on US docs; JP docs list GTC too. GTC support may be region-specific; prefer DAY fallback if GTC rejected.

## Decisions (from question.md)
- Dependency: use `ib_async` as a normal dependency from PyPI.
- Connection model: one IB connection per Janus account, each with its own clientId.
- Market data: subscribe explicitly; no always-on streaming.
- Data scope: MVP limited to US equities (STK/SMART/USD).
- Account values: prefer pull + push (account summary + streaming updates).
- Contract details: if multiple results, warn and skip.
- Harmony: keep a synchronous `request_contract_details()` on the gateway (thread-safe + timeout).
- IB stop-limit: support `STOP_LIMIT` via IB `STP LMT`.
- TIF default: use GTC (IB `tif="GTC"`, Webull `time_in_force="GTC"`).
- CLI stop syntax: use `bstop` / `sstop` (not `stopb` / `stops`).
- Error messaging: server raises `ValueError`, client displays `Order Error: <reason>`.
- Auto-reconnect: follow legacy vnpy_ib cadence (check every 10s via EVENT_TIMER).

## Gaps To Verify
- Auto-reconnect cadence (legacy vnpy_ib used timer-based connection checks).
- Webull stop order field mapping (order_type strings + stop/limit fields).

## References
- `src/janus/gateway/ib/ib_gateway.py`
- `src/janus/server.py`
- `src/janus/symbol_registry.py`
- `../ib_async/ib_async/ib.py`
- `../ib_async/ib_async/wrapper.py`
- `../ib_async/ib_async/client.py`
