# Order Commands

## Commands
- `buy <symbol> <volume> [price] [exchange]`
- `sell <symbol> <volume> [price] [exchange]`
- `bstop <symbol> <volume> <stop_price> [limit_price] [exchange]`
- `sstop <symbol> <volume> <stop_price> [limit_price] [exchange]`

## Rules
- If `price` is omitted for buy/sell, the order is a market order.
- If `price` is provided for buy/sell, the order is a limit order.
- `bstop` and `sstop` create stop orders.
- If `limit_price` is provided for `bstop`/`sstop`, the order becomes stop-limit.
- `exchange` is optional and defaults to `SMART`.
- Webull may ignore exchange; IB uses `SMART` for US equities.
- TIF defaults to GTC where supported; Webull will fall back to DAY if GTC is rejected.
