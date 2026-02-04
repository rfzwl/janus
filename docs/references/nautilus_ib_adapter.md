# Nautilus IB Adapter Study (nautilus_trader/adapters/interactive_brokers)

Scope: quick architecture/feature scan for IB support patterns and async design.

## High‑level structure
- **InteractiveBrokersClient** is the core IB API wrapper (async, asyncio‑driven).  
  It composes multiple mixins: connection, account, market data, orders, contracts, errors.  
  (`client/client.py`, `client/*.py`)
- **Data / Execution clients** sit on top of the client and integrate with Nautilus engines:  
  - `InteractiveBrokersDataClient` (market data)  
  - `InteractiveBrokersExecutionClient` (orders/positions)  
  (`data.py`, `execution.py`)
- **Instrument provider** resolves IB contract details and builds `Instrument` objects,  
  with optional futures/options chain expansion and MIC venue mapping.  
  (`providers.py`, `parsing/instruments.py`)
- **Gateway (Dockerized)** helper can start a local IB Gateway/TWS container.  
  (`gateway.py`)

## Async design highlights
- **Fully async** client with multiple background tasks:
  - incoming message reader
  - internal message queue processor
  - message handler queue
  - connection watchdog / reconnect loop  
  (`client/client.py`)
- **Connection management** uses explicit async handshake and retries, with events:
  - `_is_ib_connected`, `_is_client_ready`
  - reconnection / resubscribe flow on disconnect  
  (`client/connection.py`, `client/client.py`)
- **Request/Subscription registry**:
  - request id -> name/handle/cancel mapping
  - async wait on request futures  
  (`client/common.py`)

## Contract + instrument resolution
- Uses IB `ContractDetails` queries and **instrument parsing** to build canonical
  `Instrument` objects (with caching).  
- Supports **exchange -> MIC venue mapping** and **symbol‑specific venue overrides**.
- Can build **options and futures chains**, with expiry filtering.  
  (`providers.py`)

## Order/execution mapping
- Execution client maps **order type**, **TIF**, **trigger methods**, and **side**
  between Nautilus orders and IB API types.  
  (`execution.py`, `parsing/execution.py`)
- Uses IB orderId tracking + `orderRef` mapping for reconciliation.  
  (`client/order.py`)

## What seems stronger than our current Janus IB path
- Async, back‑pressure‑safe IB client with queueing and reconnect logic.
- Rich contract resolution + instrument cache (including chains).
- Explicit mapping tables for order types/TIF/triggers.

## Takeaways for Janus
- If we need more order types (e.g., stop‑limit), Nautilus maps directly to IB order fields.
- For symbol resolution, a contract‑details request + **ambiguity handling** is baked in.
- Async client architecture is overkill for Janus now, but pieces (request registry,
  explicit mapping tables) are good reference patterns.

## Files reviewed
- `adapters/interactive_brokers/client/client.py`
- `adapters/interactive_brokers/client/connection.py`
- `adapters/interactive_brokers/client/order.py`
- `adapters/interactive_brokers/client/common.py`
- `adapters/interactive_brokers/data.py`
- `adapters/interactive_brokers/execution.py`
- `adapters/interactive_brokers/providers.py`
- `adapters/interactive_brokers/gateway.py`
