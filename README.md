# Janus - Distributed Asset Management CLI

> **"The Watcher of Accounts."**

**Janus** is a lightweight, distributed asset management terminal and middleware built on top of **[vn.py](https://github.com/vnpy/vnpy)** and **[vnpy_rpcservice](https://github.com/vnpy/vnpy_rpcservice)**.

Designed as a modern **OEMS (Order Execution Management System)** client, Janus connects to underlying trading nodes (running vn.py) via RPC. It provides a unified **REPL (Read-Eval-Print Loop)** interface that allows traders to monitor multiple accounts in real-time, receive trade notifications, and execute manual interventions, while serving as a reliable gateway for strategy engines like **Nautilus Trader**.

## 🎯 Project Positioning

* **Role**: Asset Management Console & Strategy Routing Gateway.
* **Use Cases**: Multi-account manual supervision, offline data recording, and live trading execution channel for external strategy engines.
* **Architecture**: Server-Client model based on ZeroMQ RPC (Server: vn.py, Client: Janus CLI).

## 🏗️ Architecture

```mermaid
graph TD
    subgraph "Server Side (vn.py Process)"
        A[vn.py Event Engine]
        B[Broker Gateway] -->|Webull/Moomoo/IBKR| M[Market]
        C[RpcService (Server)]
        A <--> B
        A <--> C
    end

    subgraph "Client Side (Janus Terminal)"
        D[RpcService (Client)]
        E[REPL Interface (cmd/prompt_toolkit)]
        F[Live Dashboard (Rich TUI)]
        
        D <==>|ZMQ / TCP| C
        E -->|Order Request| D
        D -->|Push: Tick/Order/Account| F
        D -->|Push: Log/Notification| E
    end

    subgraph "Future Integration"
        G[NOrion / Nautilus Trader] -.->|RPC / IPC| D
    end