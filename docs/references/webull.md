# Webull positions reference (webull-cli-trader)

Sources:
- ../webull-cli-trader/app/commands/account.py (handle_account_positions)
- ../webull-cli-trader/app/utils.py (print_positions)

How positions are fetched:
- client = api.get_trade_client()
- account_id = api.get_first_account_id(client)
- res = client.account_v2.get_account_position(account_id)
- if res.status_code == 200:
  - positions = api.extract_list_from_response(res.json())
  - print_positions(positions)

Columns shown in CLI:
- Symbol
- Qty
- Last Price
- Mkt Value
- Cost
- Diluted Cost
- Unrealized P&L

Field mapping used in print_positions:
- symbol: pos.ticker.symbol OR pos.symbol OR "Unknown"
- qty: pos.position OR pos.quantity OR "0"
- last price: pos.last_price OR pos.lastPrice
- market value: pos.market_value OR pos.marketValue
- cost: pos.cost OR pos.costPrice
- diluted cost: pos.diluted_cost OR pos.dilutedCost OR pos.diluted_cost_price OR pos.dilutedCostPrice OR pos.costPrice OR pos.cost_price
  - if diluted cost is missing but cost and qty exist, compute cost / qty
- unrealized P&L: pos.unrealized_profit_loss OR pos.unrealizedProfitLoss
