# Futures in Symbol Registry (Design Draft)

## Goals
- Record IB futures (FUT) in `janus.symbol_registry` so positions and orders can map `conId` <-> canonical symbol.
- Keep changes small and compatible with the current registry schema.
- Avoid ambiguity across contract expiries.

## Current Behavior
- IB portfolio handler only inserts **US equities** into registry.
- For futures, `_symbol_from_contract()` falls back to `contract.symbol` (root only), so UI shows `NQ` even if registry is empty.

## Proposed Approach (No Schema Change)
**Use a unique, expiry-specific canonical symbol for futures.**

Recommended canonical format:
- **Root + expiry (YYMM)** (e.g., `NQ.2503`)
  - Derived from `lastTradeDateOrContractMonth` (YYYYMM -> YYMM).

**Insertion rule (on IB portfolio update):**
- If `secType == "FUT"` and `conId` exists:
  - Determine canonical as `ROOT.YYMM` (e.g., `NQ.2503`).
  - Call `ensure_ib_symbol(symbol=canonical, conid=conid, asset_class="FUTURE", currency=currency, description=root)`

This keeps registry unique per expiry without altering DB schema.

## Order Routing
- Orders should use canonical symbol (localSymbol / root.expiry).
- `_resolve_ib_conid()` already looks up registry by canonical; will work once futures are stored.

## Display
- `_symbol_from_contract()` already prefers registry by `conId`, so futures will display canonical symbol once inserted.

## Open Questions
None.

## Harmony Behavior
- Futures are **IB-only** for now.
- Harmony should **skip webull lookups** for `asset_class == "FUTURE"` records (leave `webull_ticker` empty).
