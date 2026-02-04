# Phase 4 (Order Types) Questions

## Scope / Commands
- For CLI inputs, should we keep the simple format (`buy AAPL 10 100`) and infer type, or add explicit commands (`stopb`, `stops`, `stoplimit` etc.)?
- market, limit should be inferred by the number of parameters. e.g. buy aapl 10 is market order.
- same things for stop buy market  and stop buy limit only one command stopb
- Should `price` be optional for market orders (e.g., `buy AAPL 10`), or require `market` keyword?
- Do we want `tif` support now (DAY vs GTC), or keep DAY only?
all default to GTC

## Order Mapping [Rules](Rules)
- For `sell` when position is 0: treat as short (Direction.SHORT) or reject unless `short` is explicit?
- treat as short
- For `cover`/`short`: should they be allowed for all brokers or only when account config allows shorting?
for all brokers
- Should `stop` map to stop-market only, or allow `stop-limit` when two prices provided?
- see above
- For IB, should we map stop-limit to native stop-limit order types or emulate?
- 不懂这个问题

## Broker Capabilities / Fallbacks
- If a broker doesn’t support a requested order type, do we reject or downgrade (e.g., stop-limit -> stop)?
- reject
- For Webull, do we need to translate stop orders into the broker-specific fields (aux/stopPrice), or use vn.py order types only?
- what do you mean? all orders should translate into the broker specific fields , right?

## Validation & Errors
- What’s the expected error message to user when mapping fails (client vs server)?
what do you mean
- Should we pre-validate against registry (symbol exists, conId exists) before parsing order type?
yes

## Open Items (need confirmation)
- `stopb` / `stops` syntax: is it `stopb <symbol> <qty> <stop_price> [limit_price]` and same for `stops`?
- IB stop-limit: vnpy_ib only supports STOP (stop-market). Should we **reject** stop-limit for IB?
- Webull stop orders: should `order_type` be `STOP`/`STOP_LIMIT` or `STOP LOSS`/`STOP LOSS LIMIT`, and use `stop_price` + `limit_price` fields?
- TIF=GTC: vn.py OrderRequest has no TIF field; OK to ignore for IB and only set `time_in_force=GTC` in Webull gateway?
- Error messaging: is “Order Error: <reason>” from client enough, with server raising ValueError on mapping failures?
