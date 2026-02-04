# Janus Status Summary (Session: janus/main)

## Context
Janus integrates multiple brokers (Webull + IB) via vn.py. We added a symbol registry
and `harmony` to backfill missing symbols. IB gateway has been rewritten to use
`ib_async` directly (no `vnpy_ib`, no `ibapi`).

## Environment & Dependencies
- IB uses `ib_async` (pure Python, no `ibapi`/TWS pythonclient).
- **protobuf conflict**:
  - Webull SDK pins protobuf 4.21.12.
  - We **force** protobuf 5.x and install Webull SDK with `--no-deps`.
- Runtime should avoid `uv` auto-sync:
  - `uv run --no-sync python -m janus.server`
  - `uv run --no-sync python -m janus.client`

## Bootstrap
- `bootstrap.sh` automates:
  - create `.venv`
  - install project
  - install `ib_async`
  - install Webull SDK with `--no-deps`

## Symbol Registry (Phase 1)
- Postgres table `janus.symbol_registry` (manual schema creation).
- Canonical symbol normalization: trim + uppercase.
- Cache is loaded at server start and write-through for updates.
- Webull holdings can create new entries; IB holdings fill conId.
- DB down on startup => hard fail.

## IB Integration
- `JanusIbGateway` uses `ib_async` directly.
- IB holdings update registry for `STK` + USD only.
- Non-equity holdings and non-USD: warn + skip.
- One IB connection per account; reconnect check every ~10s via EVENT_TIMER.

## Harmony (Phase 3)
Server-only RPC; client triggers and shows summary.
- Connected broker types only (per broker type).
- Fill **missing fields only** (no re-validation).
- IB lookup: US + SMART, ambiguous => warn/skip.
- Webull: ticker-only fill.
- On DB error: abort and return error (no partial results).
- On-demand only (no scheduler, no rate limiting).

## Current Commands
- Client: `sync`, `harmony` (RPC to server).
- Server: `send_order_intent` routes per broker via registry.

## Open Items / Phase 4
Goal: support order types: market, limit, stop, stop-limit.
Current decisions:
- CLI syntax uses `bstop` / `sstop`.
- IB stop-limit supported via `STP LMT`.
- Webull stop/stop-limit use `STOP_LOSS` / `STOP_LOSS_LIMIT` with `stop_price` / `limit_price`.
- TIF default is GTC (IB `tif="GTC"`); Webull GTC support is unclear in US docs, may require DAY fallback.

## Notes
- `question.md` is used for temporary planning and should be discarded after
  its content is moved into formal docs.
