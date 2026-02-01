# Janus - Distributed Multi-Account Asset Management CLI

> **"The Watcher of Accounts."**

**Janus** is a lightweight, distributed asset management terminal and middleware built on top of **[vn.py](https://github.com/vnpy/vnpy)**.

Designed as a modern **OEMS (Order Execution Management System)** console, Janus connects to multiple underlying trading nodes (gateways). It provides a unified **REPL (Read-Eval-Print Loop)** interface that allows traders to monitor various broker accounts in real-time and execute manual interventions across different platforms from a single terminal.

## 🎯 Project Positioning

* **Multi-Broker Hub**: Native support for various brokers including **Interactive Brokers (IB)**, **Webull**, **Moomoo**, and **E-Trade** through vn.py gateways.
* **Persistent Connectivity**: The Janus Server automatically connects to all configured gateways upon startup. The client remains a lightweight interface that stays synchronized with the server's state.
* **Account Context Management**: A unified CLI where commands are routed to specific brokers. Users can switch the active "default" broker context seamlessly.
* **Distributed Architecture**: Separation of the heavy-duty trading server and the lightweight TUI client via ZeroMQ RPC.
* **Unified Strategy Plane**: Automated trading strategies are implemented via vn.py and managed directly through the Janus terminal.

## 🏗️ Architecture

```mermaid
graph TD
    subgraph "Server Side (vn.py Process)"
        A[vn.py Event Engine]
        B[Broker Gateways] -->|Webull/IB/Moomoo/E-Trade| M[Market]
        C[RpcService (Server)]
        S[vn.py Strategy Engine]
        A <--> B
        A <--> C
        A <--> S
    end

    subgraph "Client Side (Janus Terminal)"
        D[RpcService (Client)]
        E[REPL Interface (prompt_toolkit)]
        F[Live Dashboard (Rich TUI)]
        
        D <==>|ZMQ / TCP| C
        E -->|Order/Strategy Request| D
        D -->|Push: Tick/Order/Account| F
        D -->|Push: Log/Notification| E
    end
```

## 🧭 Command Interface

Janus provides a flexible command system for managing multiple accounts from a single session:

- **Broker context**: Use `broker <name>` (e.g., `broker webull`) to set the default account for subsequent commands.
- **Targeted commands**: Commands like `buy`, `sell`, or `cancel` target the current default broker context.

**Core commands**

- `buy/sell/short/cover <symbol> <volume> <price>`: Place orders on the active broker.
- `cancel <vt_orderid>`: Cancel an existing order.
- `broker <name>`: Switch the current default broker context.
- `broker list`: Show configured brokers, `*` marks the current default.
- `broker <name> <command...>`: Run a command against a broker without changing the default.
- `sync`: Manual data refresh. Triggers the Janus Server to proactively request a full data update (Account & Positions)
  from all active Broker Gateways. Use this to ensure the TUI display is aligned with the broker's authoritative state.
- `exit/quit`: Safely disconnect the client and close the terminal.

**Strategy management (coming soon)**

- `strategy <action> <name>`: Start, stop, or adjust automated vn.py strategies across different accounts.

## 🚀 Getting Started

1. **Configuration**: Define your broker credentials and gateway settings in `config.yaml`.
2. **Install (uv)**:
   - `uv venv`
   - `uv pip install -e . --python .venv`
3. **Start Server**: Run `uv run python -m janus.server`. The server initializes all configured broker connections.
4. **Start Client**: Run `uv run python -m janus.client`. The client subscribes to all server-side event streams on connect.
