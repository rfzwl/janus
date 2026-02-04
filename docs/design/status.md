# Janus Status Summary (Session: janus/main)

## Context
Janus integrates multiple brokers (Webull + IB) via vn.py. We added a symbol registry
and IB bootstrap, then implemented a `harmony` command to backfill missing symbols.

## Environment & Dependencies
- IB official TWS API `pythonclient` is required.
- `vnpy_ib==10.30.1.1`.
- **protobuf conflict**:
  - IB official `ibapi` needs protobuf 5.x.
  - Webull SDK pins protobuf 4.21.12.
  - We **force** protobuf 5.x and install Webull SDK with `--no-deps`.
- Runtime must avoid `uv` auto-sync:
  - `uv run --no-sync python -m janus.server`
  - `uv run --no-sync python -m janus.client`

## Bootstrap
- `bootstrap.sh` automates:
  - create `.venv`
  - install project
  - install IB pythonclient
  - install Webull SDK with `--no-deps`
- Readme updated with this workflow.

## Symbol Registry (Phase 1)
- Postgres table `janus.symbol_registry` (manual schema creation).
- Canonical symbol normalization: trim + uppercase.
- Cache is loaded at server start and write-through for updates.
- Webull holdings can create new entries; IB holdings fill conId.
- DB down on startup => hard fail.

## IB Integration (Phase 2)
- `JanusIbGateway` wraps vnpy_ib.
- IB holdings update registry for `STK` + USD only.
- Non-equity holdings and non-USD: warn + skip.
- Remote IB host/port supported with `-r/--remote`.

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
Key unresolved decisions:
- `stopb` / `stops` exact syntax (proposed: `stopb <symbol> <qty> <stop_price> [limit_price]`).
- IB stop-limit:
  - IB supports it, **vnpy_ib does not** (only STOP).
  - Options: reject stop-limit for IB, or override `JanusIbGateway.send_order` to place
    `STP LMT` order directly.
- Webull stop/stop-limit:
  - Confirm `order_type` values and `stop_price`/`limit_price` fields.
- TIF=GTC:
  - vn.py OrderRequest has no TIF; likely only apply to Webull gateway.
- Error messaging format for mapping failures.

## Notes
- `question.md` is used for temporary planning and should be discarded after
  its content is moved into formal docs.
