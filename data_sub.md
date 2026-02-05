# IB Market Data Subscription Design (Draft)

## Goals
- Provide a server-side API to subscribe/unsubscribe IB real-time bars.
- Use `canonical_symbol` as the external key; map to IB `conId` internally.
- Subscriptions are initiated only via CLI command or config defaults (no auto-subscribe from holdings).
- Do not mutate position values from bars; bars are for separate market-data display.

## Server API (RPC)
- `subscribe_bars(symbols: list[str], account: str, rth: bool = False) -> str`  (5-second streaming)
- `unsubscribe_bars(symbols: list[str], account: str) -> str`
- Symbols are `canonical_symbol`.
- Resolve conId via registry; if missing, attempt auto-lookup once and persist.
 
## Subscription Model (5s streaming bars)
- Use IB real-time bars (5-second stream) instead of ticks.
- `reqRealTimeBars` with barSize=5; cancel via `cancelRealTimeBars`.

## CLI
- `bars <symbol> [rth]`
  - Default is all-hours (no `rth`).
  - If `rth` is provided, subscribe with `rth=true`.
- `unbars <symbol>`
  - Unsubscribe the 5-second bar stream.

## Flow (Subscribe)
1) Client calls `subscribe_bars(["AAPL", "TSLA"], "ib_main")`.
2) Server finds IB gateway by account.
3) For each symbol:
   - `conId` from registry; if missing -> `_resolve_ib_conid(symbol)`.
   - Build `SubscribeRequest(symbol=str(conId), exchange=Exchange.SMART)`.
4) Call `gateway.subscribe_bars(req)` (new method for real-time bars).

## Flow (Unsubscribe)
1) Server builds `SubscribeRequest` with same `conId` mapping.
2) Call `gateway.unsubscribe_bars(req)` (new method).
3) Gateway cancels IB real-time bars and removes from local bar subscriptions.

## IB Gateway Changes
- Add `subscribe_bars()` / `unsubscribe_bars()` to `JanusIbGateway` and `IbAsyncApi`.
- `IbAsyncApi.subscribe_bars()` uses `reqRealTimeBars` (barSize=5).
- `IbAsyncApi.unsubscribe_bars()` uses `cancelRealTimeBars`.
- Keep `_subscribed` map keyed by `vt_symbol` to deduplicate.

## Bar Cache & Client Visibility
- Server listens to bar events and stores latest bar by `canonical_symbol`.
- Store full bar payload: OHLCV + VWAP (if provided by IB).
- Use `close` as the displayed price (fallback to `wap` or `last` if needed).
- Do not change position `volume` or average cost fields.
- Client visibility: push close-only to client log on each bar update.

## Config (Optional)
- `ib_market_data`:
  - `default_symbols: list[str]` (pre-subscribe at startup)
  - `bar_size_seconds: int` (default 5, IB only supports 5)
  - `what_to_show: str` (default TRADES)
  - `use_rth: bool` (default false; CLI `rth=true` overrides)

## Open Questions
None.
