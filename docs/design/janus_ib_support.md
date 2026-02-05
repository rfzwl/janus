# Janus IB Support Design

## Goals
- Add Interactive Brokers (IB) as a supported broker in Janus.
- Provide a broker-agnostic order abstraction at the Janus server layer.
- Normalize symbols so Webull/IB can be reasoned about consistently.
- Support IB real-time market data and use it to enrich UI/state.

## Non-goals (for now)
- Advanced derivatives workflows (multi-leg options, spreads, Greeks/risk).
- Cross-broker position netting or automated hedging.
- Direct portfolio transfer between brokers.

## Terminology
- **gateway_name**: account alias in Janus (the name passed to `add_gateway`).
- **broker**: broker type from config (e.g., webull, ib).

## Architecture Overview
- Janus server owns:
  - Symbol normalization + mapping table.
  - Order abstraction layer (intent -> broker-specific OrderRequest).
  - Market data cache (from IB streaming) for display/mark-to-market.
- Gateways remain vn.py BaseGateway implementations (thread-safe, non-blocking).
- Event flow remains: gateway.on_* -> EventEngine -> RpcService -> Janus client.

## Symbol Normalization
### Canonical symbol format
- Canonical symbol keys used inside Janus:
  - `symbol` (uppercase string, e.g. AAPL)
  - `asset_class` (EQUITY | FOREX | FUTURE | OPTION | CRYPTO)
  - `exchange` (SMART for IB equities, SMART/NYSE/NASDAQ for display only)
  - `currency` (USD default)
- Equities: `AAPL` style.
- Futures: `ROOT.YYMM` (e.g., `NQ.2603`).
- Options (display-only today): `ROOT.YYMMDD.C/P.STRIKE` (e.g., `NQ.260320.C.15000`).

### Mapping rules
- IB expects structured contracts (secType/exchange/currency/conId). `ib_async` supports building contracts directly.
- Webull expects plain equity symbol (AAPL) and market=US.
- Store a registry:
  - canonical -> broker-specific (ib_symbol, ib_exchange, webull_symbol, market)
  - optionally allow conId for IB to avoid ambiguity.
- IB auto-lookup should apply a default market/exchange filter (e.g., US + SMART) to reduce ambiguity.
- IB futures default to CME (fallback to GLOBEX when resolving conId).

### Where mapping lives
- Config-driven mapping table, with defaults for simple US equities.
- If mapping not found:
  - allow IB subscribe by canonical (AAPL) using SMART+USD.
  - allow Webull by symbol only.
  - log a warning for ambiguous assets.

## Market Data Strategy (IB streaming)
- IB gateway supports `subscribe` via reqMktData and 5-second real-time bars.
- Market data updates (ticks/bars) update a server cache:
  - `last_price`, `bid/ask`, `timestamp` keyed by canonical symbol.
- Important: market data does NOT change position quantity.
  - It only updates derived fields (market_value, unrealized PnL) for display.
- If desired, publish derived position snapshots to clients on tick update (rate-limited).

## Holdings-Driven Symbol Fill
- When IB accounts are loaded and holdings are received, perform symbol lookup to fill missing registry fields.
- Default filter applies (US + SMART) and only unique matches are persisted.
- Reference behavior (from taurus): use `reqContractDetails`; if multiple ContractDetails are returned, treat as ambiguous and skip.
- Futures holdings (FUT) are written to the registry as `ROOT.YYMM`.
- Options holdings are displayed using canonical-like symbols but are not persisted yet.

## Order Abstraction (Server Layer)
### OrderIntent (broker-agnostic)
Fields:
  - `account` (target account alias / gateway_name)
  - `symbol` (canonical)
  - `side` (BUY | SELL)
  - `type` (MARKET | LIMIT | STOP | STOP_LIMIT)
  - `qty`
  - `limit_price` (optional)
  - `stop_price` (optional)
  - `tif` (DAY default)

### Mapping to vn.py OrderRequest
- `side=BUY` -> Direction.LONG
- `side=SELL` -> Direction.SHORT or Direction.LONG depending on position/netting rules
- `type` mapping:
  - MARKET -> OrderType.MARKET
  - LIMIT -> OrderType.LIMIT (limit_price required)
  - STOP -> OrderType.STOP (stop_price required; mapped to aux/stop)
  - STOP_LIMIT -> OrderType.STOP (stop_price + limit_price; broker-specific capability)

### Short vs Sell behavior
- Some brokers require explicit "short" for short sales; others allow "sell".
- Proposed rule:
  - If current position > 0: SELL reduces long (Direction.SHORT not used).
  - If position == 0 and `allow_short` is true: treat SELL as short (Direction.SHORT).
  - If position < 0: SELL increases short (Direction.SHORT).
- `allow_short` and `locate_required` should be per-account config flags.

### Stop order semantics
- STOP BUY (bstop):
  - If only stop_price -> stop market buy
  - If stop_price + limit_price -> stop limit buy
- STOP SELL (sstop):
  - If only stop_price -> stop market sell
  - If stop_price + limit_price -> stop limit sell

## IB Integration Points
- Add IB gateway class to Janus server broker_map.
- Extend config.yaml.example with IB connection fields (host/port/client id/account).
- Optionally add a "market_data_account" for centralized IB streaming.
- Use `ib_async` for streaming data, reconnection, and order status updates.
- `sync` triggers IB `reqAllOpenOrders` so orders from other IB clients appear.

## Cross-Broker Behavior Considerations
- Orders and positions remain broker-specific; Janus should not merge them.
- Market data can be shared across brokers for display only.
- If Webull position changes are shown using IB ticks, mark them clearly as "derived".

## Threading Model
- `ib_async` runs an asyncio loop in a dedicated thread; callbacks call gateway.on_* (gateway instance == account alias).
- Janus server should keep all heavy work (symbol mapping, order parsing) outside gateway callbacks.
- Market data derived refresh should be debounced (e.g., 200-500ms) to avoid UI storms.

## Risks / Edge Cases
- Symbol ambiguity (same ticker on different exchanges).
- Short sale constraints differ by broker; need config rules.
- Stop-limit availability may differ per broker; need fallback behavior.
- Order type mapping conflicts (IB vs Webull vs vn.py).

## Open Questions
1) Should Janus treat IB market data as the single source of truth for pricing, or per-broker streams?
2) For SELL with zero position, do we default to short? Or require explicit short command?
3) Do we need per-symbol shortability flags or just per-account allow_short?
4) For STOP_LIMIT mapping: are we OK with best-effort mapping if a broker lacks support?
5) Should we publish derived position PnL updates to clients on tick, or only on explicit sync?

## Harmony Command
- Add client command `harmony` to request server-side symbol/id fill across all connected brokers.
- Server should only write registry entries when lookup result is unique after default market filter.
- Harmony scope:
  - server-only RPC, client receives final summary
  - connected broker types only (per broker type, not per account)
  - fill missing fields only; no re-validate/overwrite
  - Webull uses ticker only; no region/market reconciliation
  - abort on DB write error (return failure)
  - on-demand only; no rate limiting in MVP

## Phased Implementation Plan
1) Registry + Postgres (no trading changes)
   - Create Postgres instance and symbol registry table.
   - Implement read-only lookup path and in-memory cache.
   - Add auto-fill via IB lookup with default market filter (US + SMART).
   - If ambiguous, do not write; return clear error to caller.
2) Order routing via registry
   - Add server-side OrderIntent mapping to broker OrderRequest.
   - Gate missing symbols: IB auto-lookup once; if still missing, reject with message.
   - Keep Webull fallback to ticker only when canonical_symbol is allowed.
3) IB gateway integration
   - Add IB gateway to broker_map and config.
   - Implement server API to subscribe IB ticks.
   - Cache prices and compute derived valuation only (no position qty changes).
4) Webull trade-events
   - Add TradeEventsManager and account_id registry.
   - Minimal order status/traded updates + debounced snapshot refresh.
   - Reconnect and error handling.
5) Polish
   - Health checks, logging, and UI indicators for derived pricing.
