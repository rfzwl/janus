# Janus (雅努斯) - Universal Trading Gateway

> **"One Gateway to Rule Them All."**

Janus 是一个高性能、异步的通用交易网关（Execution Management System）。它旨在作为量化策略引擎（如 Nautilus Trader, vn.py）与各种券商（Webull, IBKR, etc.）之间的标准化桥梁。

通过 FastAPI 构建，Janus 提供了现代化的 REST API 和 WebSocket 接口，屏蔽了底层券商 SDK 的同步/异步差异、协议复杂性（gRPC/Socket/HTTP）以及数据格式的不一致。

## ✨ 核心特性

* **⚡ 极速异步架构**: 基于 FastAPI + AsyncIO，采用单进程混合线程模型，完美平衡高并发 I/O 与同步 SDK 的兼容性。
* **🔌 通用适配器 (Universal Adapters)**:
    * **Webull**: 完整支持（基于官方 SDK），包含自动 Token 刷新、HTTP 交易、gRPC 事件流。
    * *(Planned)* **Interactive Brokers (IBKR)**: 基于 `ib_insync` 的异步封装。
    * *(Planned)* **Crypto (CCXT)**: 统一加密货币交易所接口。
* **🛡️ 统一数据模型**: 无论券商 API 如何千奇百怪，对外统一暴露标准的 `Order`, `Position`, `Account` 数据结构 (Pydantic)。
* **📡 实时事件流**: 将不同券商的成交回报（gRPC, Socket, Webhook）统一转换为 WebSocket 流推送给客户端。
* **🤖 策略友好**: 专为 Nautilus Trader 等框架设计，支持 TradingView Webhook 信号接入。
* **🐳 Docker Ready**: 开箱即用的 Docker Compose 配置，支持 Token 持久化与 7x24 小时守护运行。

## 🏗️ 架构概览

```text
[ Nautilus Trader / Strategies ] <--- WebSocket / HTTP ---> [ Janus Gateway (FastAPI) ]
                                                                    │
          ┌─────────────────────────────────────────────────────────┼───────────────────────────┐
          │                      Unified Router & Risk Manager (The Boss)                       │
          └───────────┬─────────────────────────────┬──────────────────────────────┬────────────┘
                      │ (Task)                      │ (Stream)                     │ (Auth)
          ┌───────────▼───────────┐    ┌────────────▼─────────────┐     ┌──────────▼──────────┐
          │  Workers (ThreadPool) │    │  Sentinels (BG Threads)  │     │   Config / Token    │
          │   [Sync SDK Calls]    │    │   [gRPC/Socket Loops]    │     │   [Persistence]     │
          └───────────┬───────────┘    └────────────┬─────────────┘     └─────────────────────┘
                      │                             │
                  [ Webull ]                    [ IBKR ]