# Webull Trade Events Integration Design

## Goals
- Register trade-events subscription on the server for each Webull account.
- Update local order/position/account state promptly on events.
- Notify Janus client via vn.py event flow (gateway -> EventEngine -> RPC -> TUI).

## Terminology
- **gateway_name**: account alias in Janus (the name passed to `add_gateway`).
- **broker**: broker type from config (e.g., webull, ib).

## Context (vn.py Architecture Constraints)
- BaseGateway methods must be thread-safe, non-blocking, and auto-reconnect. See `../vnpy_all/vnpy/vnpy/trader/gateway.py`.
- `connect()` should perform initial snapshot queries (account/position/orders/trades/contracts) and push via `on_*`.
- EventEngine uses a queue with a worker thread; event producers should be lightweight and never block.
- Objects passed into `on_*` should be treated as immutable afterwards; if caching, push a copy.
- OmsEngine aggregates state and tracks active orders based on `Status`; correct status transitions are essential.

## Current State (Janus)
- Webull gateway polls account/position/open orders; no trade-events subscription.
- Client subscribes to all events and updates TUI on `eOrder` / `ePosition`.

## Proposed Architecture
**Selected: TradeEventsEngine (vn.py Engine)**
- Implemented as a vn.py `BaseEngine` so lifecycle is managed by `MainEngine`.
- Per-account subscription (assumes no shared credentials).
- Engine owns worker threads and delegates event payloads to the gateway.

## Data Flow
Webull gRPC -> EventsClient -> TradeEventsManager -> Webull gateway (account alias) -> EventEngine -> RpcService -> Janus client -> TUI

## TradeEventsManager Type
- Implemented as a vn.py Engine (`TradeEventsEngine`), owned by MainEngine.
- Not a BaseGateway; it only routes trade-events to Webull gateways.

## Threading & Lifecycle
- One dedicated daemon thread per account (no shared credentials).
- Thread runs blocking `do_subscribe([account_id])` and yields events via callback.
- Engine holds worker registry; dispatch directly to gateway for that account.
- Callback should be lightweight: parse payload, create/update OrderData, call `gateway.on_order` on the account gateway.
- Heavy refresh work (query_position/account/open_orders) is debounced and executed asynchronously to avoid blocking.
- Reconnect loop lives in the same thread: on SubscribeExpired/NumOfConnExceed, backoff then re-subscribe.
- Shutdown: set stop flag, close client channel if available, join thread before MainEngine close.
- Optional: use `EVENT_TIMER` for periodic health checks rather than an extra thread.

## Event Handling Details
**Event stream types**
- Trade events uses gRPC server-streaming; subscribeType currently only supports 1.
- SDK API: `TradeEventsClient(app_key, app_secret, region_id, host=optional)` + `do_subscribe([account_id])`.
- SubscribeRequest fields: subscribeType/timestamp/contentType/payload/accounts; response includes eventType/subscribeType/contentType/payload/requestId/timestamp.
- Handle EventType: SubscribeSuccess / Ping / AuthError / NumOfConnExceed / SubscribeExpired.
- Ignore Ping, log SubscribeSuccess, and trigger reconnect/backoff on errors.

**Order status updates**
- Filter messages where event_type=EVENT_TYPE_ORDER and subscribe_type=ORDER_STATUS_CHANGED.
- Payload contentType can be JSON; handle payload as dict if SDK already parses, otherwise JSON-decode. Ping uses text/plain.
- Events payload includes fields like request_id, account_id, client_order_id, instrument_id, order_status, symbol, qty, filled_price, filled_qty, filled_time, side, scene_type, category, order_type.
- Supported scene_type list from docs: FILLED, FINAL_FILLED, PLACE_FAILED, MODIFY_SUCCESS, MODIFY_FAILED, CANCEL_SUCCESS, CANCEL_FAILED.
- Resolve order_id in priority:
  1) payload orderId (if present)
  2) gateway map: client_order_id -> order_id (captured on send_order)
  3) fallback: use client_order_id as orderid
- Map side to vnpy Direction; order_type to vnpy OrderType.
- Map status using order_status if present:
  - SUBMITTED -> Status.NOTTRADED (or keep existing)
  - FILLED -> Status.PARTTRADED (if filled_qty < qty)
  - CANCELLED -> Status.CANCELLED
  - FAILED -> Status.REJECTED
- Observed combinations in doc examples:
  - scene_type=FILLED + order_status=SUBMITTED
  - scene_type=FINAL_FILLED + order_status=FILLED
  - scene_type=PLACE_FAILED + order_status=FAILED
  - scene_type=MODIFY_SUCCESS + order_status=SUBMITTED
  - scene_type=CANCEL_SUCCESS + order_status=CANCELLED
- Fallback to scene_type mapping:
  - FILLED -> Status.PARTTRADED
  - FINAL_FILLED -> Status.ALLTRADED
  - PLACE_FAILED / MODIFY_FAILED / CANCEL_FAILED -> Status.REJECTED
  - CANCEL_SUCCESS -> Status.CANCELLED
  - MODIFY_SUCCESS -> keep status, update fields if needed
- Volume: use qty if present; keep existing volume if not.
- Traded: use payload filled_qty / filled_quantity if present; otherwise refresh via snapshot.
- Update gateway order cache and emit `on_order(copy(order))` to respect immutability.

**Position / account refresh**
- On FILLED / FINAL_FILLED / CANCEL_SUCCESS, trigger refresh:
  - query_open_orders (to remove inactive orders)
  - query_position + query_account (to update holdings and balances)
- Debounce refreshes (e.g., coalesce within 1-2 seconds) to avoid storms.

**Scope limitation**
- This interface only supports order status change push (no direct position updates).

## Local State Strategy (Borrowed from legacy IB gateway patterns)
- Maintain a local order cache and only update delta fields on events.
- If event lacks full fields, fall back to cache (same pattern as IB `orderStatus` + `openOrder`).
- Emit a copy via `on_order(copy(order))` to avoid mutating objects after dispatch.

## Configuration
- Add optional trade_events config per account or global defaults:
  - enable: bool (default true for Webull)
  - host: optional gRPC endpoint override (PRD `events-api.webull.com`; UAT `us-openapi-alb.uat.webullbroker.com`)
  - region_id: already present in account config
- If host unset, EventsClient uses SDK defaults based on region_id.

## Reliability / Reconnect Strategy
- On AuthError: log and stop subscription (requires operator action).
- On SubscribeExpired: auto-resubscribe with exponential backoff (cap + jitter).
- On NumOfConnExceed: log and disable extra connections; prefer shared manager.
- Track last_event_timestamp per account for health checks (optional).

## Edge Cases & Behavior
- Orders placed outside Janus: still emit OrderData based on event payload and show in TUI.
- Partial fill without filled_qty: status update immediately, quantity refresh via snapshot.
- Events without price: keep prior price, or pull order detail if needed.
- Unknown scene_type/order_status: log once per type and keep last known status until refreshed.

## Files Likely Touched (no code yet)
- src/janus/server.py (create/init TradeEventsManager; register gateway accounts)
- src/janus/gateway/webull/webull_gateway.py (register with manager; handle event updates)
- src/janus/config.py + config.yaml.example (trade_events config)
- pyproject.toml (add trade-events SDK dependency if not bundled in current SDK)

## Open Questions / Confirmations
1) Do we expect multiple accounts to share the same app_key/app_secret? (affects manager vs per-gateway design)
2) Is it acceptable to add the trade-events SDK dependency explicitly?
3) For order updates, do you prefer immediate lightweight updates + async refresh, or always query order detail for accuracy?

## Phased Implementation Plan
1) Account registry + wiring
   - Register account_id -> gateway_name after connect completes.
   - Ensure manager uses account aliases (gateway_name) for dispatch.
2) Subscription lifecycle
   - Start TradeEventsManager after all accounts are connected.
   - Implement connect/reconnect loop and graceful shutdown.
3) Event handling
   - Parse payload, update local order cache, emit on_order(copy(order)).
   - Debounce snapshot refresh for account/position/open orders.
4) Reliability
   - Handle AuthError/SubscribeExpired/NumOfConnExceed with clear logs.
   - Add optional health check on EVENT_TIMER.

## References
- https://developer.webull.com/apis/docs/reference/custom/subscribe-trade-events/
- ../vnpy_all/vnpy/vnpy/trader/gateway.py
- ../vnpy_all/vnpy/vnpy/event/engine.py
- ../vnpy_all/vnpy/vnpy/trader/engine.py
- ../vnpy_all/vnpy_ib/vnpy_ib/ib_gateway.py (legacy pattern reference)
