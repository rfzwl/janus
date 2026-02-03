# Cross-Document Implementation Plan

This plan consolidates the phased steps from:
- `docs/design/janus_ib_support.md`
- `docs/design/symbol_mapping.md`
- `docs/design/webull_trade_events.md`

## Phase 1 — Registry + Postgres (foundation)
1) Create a dedicated PostgreSQL instance for Janus symbol data.
2) Create the thin registry table:
   - `id` PK, `canonical_symbol` unique, plus broker fields (ib_conid, webull_ticker, etc.).
3) Implement read-only registry lookup path and in-memory cache in server startup.
4) Define auto-fill rule for IB lookup:
   - Apply default market filter (US + SMART).
   - If unique, store ib_conid; if multiple, do not write and return error.

## Phase 2 — IB gateway bootstrap
1) Add IB gateway class to broker_map and extend config (host/port/client id/account).
2) Load IB accounts on server startup (connect + initial holdings snapshot).
3) When holdings load, perform symbol lookup + update:
   - Resolve canonical_symbol where possible.
   - Fill missing ib_conid via default market lookup.

## Phase 3 — Harmony command (MVP)
1) Add client command `harmony`:
   - Ask server to contact each connected broker and fill missing symbol/id fields.
   - Only write registry entries when lookup result is unique after default filter.

## Phase 4 — Order routing via registry
1) Add server-side OrderIntent mapping to broker OrderRequest.
2) Resolve canonical_symbol through registry before sending orders.
3) Missing symbol handling:
   - IB: attempt auto-lookup once; if still missing, reject with clear message.
   - Webull: allow fallback to ticker only when canonical_symbol is allowed.

## Phase 5 — IB market data
1) Implement server API for IB tick subscriptions.
2) Cache tick prices by canonical_symbol; compute derived valuation for display only.
3) Do not update position quantity/cost from ticks.

## Phase 6 — Webull trade-events integration
1) Register account_id -> gateway_name after connect completes.
2) Start TradeEventsManager after all accounts are connected.
3) Event handling:
   - Parse payload, update local order cache, emit on_order(copy(order)).
   - Debounce snapshot refresh for account/position/open orders.
4) Reliability:
   - Handle AuthError/SubscribeExpired/NumOfConnExceed with clear logs.
   - Optional health check via EVENT_TIMER.

## Phase 7 — Polish
1) Add health checks and improved logging.
2) Add UI indicators for derived pricing (from IB ticks).
3) Ensure graceful shutdown of trade-events threads and IB client threads.
1) Add health checks and improved logging.
2) Add UI indicators for derived pricing (from IB ticks).
3) Ensure graceful shutdown of trade-events threads and IB client threads.
