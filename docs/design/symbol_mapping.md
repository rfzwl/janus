# Symbol Mapping (Canonical Registry)

## Purpose
- Eliminate ambiguity across brokers (IB vs Webull) by using a canonical symbol.
- Present a unified symbol in UI/logs regardless of account.
- Decouple broker-specific identifiers (e.g., IB conId) from core trading logic.

## Terminology
- **canonical_symbol**: business-level symbol used in CLI/UI (e.g., AAPL).
- **gateway_name**: account alias in Janus.
- **broker**: broker type (e.g., ib, webull).

## Registry Model (Thin)
A minimal registry keyed by canonical_symbol. Broker-specific fields are optional.

Proposed fields:
- `id` (integer, primary key)
- `canonical_symbol` (string, unique)
- `asset_class` (string, default EQUITY)
- `currency` (string, default USD)
- `ib_conid` (integer, optional, unique)
- `webull_ticker` (string, optional)
- `description` (string, optional)

Notes:
- `canonical_symbol` stays human-facing; `id` is for internal joins and future DB usage.
- No status/source fields by design (keep it thin).
- Uniqueness: `canonical_symbol`, `ib_conid`, and `webull_ticker` are enforced unique in the table.

## Storage Backend
- Use a dedicated PostgreSQL instance for Janus symbol data.
- This registry can be expanded later to store more symbol metadata in the same database.
- If DB fields are missing from config, use defaults (dbname postgres, port 5432, no password).
- Schema/table are created manually via psql; Janus does not auto-create metadata.

## Default Market Assumptions
- For EQUITY, IB auto-lookup uses US + SMART as default filter.
- Webull uses ticker + US market and does not need exchange mapping.

## Non-Equity Scope
- Non-equity assets (options/futures) are out of scope for the main registry for now and will be added later as needed.

## Auto-Fill Behavior
- If registry entry missing for canonical_symbol:
  1) Attempt IB contract lookup using default market filter.
  2) If a unique match is found, store `ib_conid`.
  3) If multiple matches remain, do not write; require manual mapping.
 - Auto-fill can be triggered by holdings load or by the client `harmony` command.

## Operational Rules (MVP)
- Canonical normalization: trim + uppercase before lookup and store.
- Webull holdings auto-insert: if only ticker is present, create row with canonical_symbol=ticker.
- Webull mismatch: if canonical exists but webull_ticker differs, warn and skip (no overwrite).
- Description: populate from broker; first value wins.
- Cache strategy: startup load + write-through on updates.
- Single-writer: no multiple Janus server instances writing to the same DB.
- Postgres unavailable: hard-fail startup.
- Unique constraint conflicts: raise error (do not silently ignore).

## Resolution Flow
1) User input: `buy AAPL 10`
2) Resolve canonical_symbol = AAPL
3) Lookup registry entry
4) Route per broker:
   - IB: use ib_conid (preferred) with SMART/USD contract
   - Webull: use webull_ticker (fallback to canonical_symbol)

## Derived Pricing (Cross-Broker Display)
- IB tick stream can provide pricing for Webull positions.
- This updates display/valuation only (not position quantity or cost).

## Open Questions
1) Do we need a separate mapping table for non-equity assets later?
2) Should we allow per-account default market (e.g., US vs HK)?
3) How to handle canonical_symbol collisions if non-US markets are added?

## Phased Implementation Plan
1) Postgres bootstrap
   - Create Postgres instance and schema for Janus symbol data.
   - Create the thin registry table with `id` PK and `canonical_symbol` unique constraint.
2) Read path
   - Load registry into in-memory cache on server start.
   - Lookup by canonical_symbol; fall back to DB.
3) Auto-fill path
   - On miss, attempt IB contract lookup with US + SMART filter.
   - If unique, store ib_conid; if multiple, do not write and return error.
4) Integration
   - Use registry resolution for order routing and IB market data subscription.
