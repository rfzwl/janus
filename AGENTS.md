# Janus System Agents & Architecture

本文档定义了 Janus 网关内部的并发模型和“活动实体 (Agents)”的职责分工。所有代码贡献必须严格遵守此架构，以确保异步 IO 与同步 SDK 的兼容性。

## 🏛️ 核心架构 (The Architecture)

Janus 采用 **单进程混合并发模型 (Single Process Hybrid Model)**。
系统不使用多进程 (Multiprocessing) 进行内部通讯，而是通过 `asyncio` 事件循环协调所有组件。

### 1. The Boss (主线程 / Main Event Loop)
**身份**: `MainThread` + `AsyncIO Loop`
**职责**:
- **是系统的唯一大脑**。所有决策、状态更新、路由分发都在这里发生。
- 运行 FastAPI (`Uvicorn`) 服务器，处理外部 HTTP/WebSocket 请求。
- 维护全局状态 (如 `GlobalOrderBook`, `PositionCache`)。
- **绝对禁忌**: 严禁运行任何阻塞 (Blocking) 代码（如 `time.sleep`, `requests.get`）。
- **通信方式**: 通过 `await` 调用原生异步库，或通过 `run_in_executor` 调度工人。

### 2. The Workers (执行工人 / Thread Pool)
**身份**: `ThreadPoolExecutor` 中的子线程
**职责**:
- **是同步代码的避难所**。负责“脏活累活”。
- 包装所有第三方同步 SDK (如 `Webull Python SDK`, `ib_gateway` 阻塞调用)。
- 处理耗时的 CPU 计算（如果有）。
- **行为模式**:
    - 接收: 从 The Boss 接过任务。
    - 执行: 阻塞等待网络响应 (I/O Bound)。
    - 回报: 返回结果给 The Boss (Future resolve)。

### 3. The Sentinels (哨兵 / Daemon Threads)
**身份**: 独立的 `threading.Thread (daemon=True)`
**职责**:
- **是系统的眼睛和耳朵**。负责维持长连接。
- 运行 Webull gRPC `trade_events` 监听循环。
- 运行旧式 Socket 的 `while True` 接收循环。
- **通信方式**:
    - 收到数据后，**必须** 使用线程安全方法 (如 `loop.call_soon_threadsafe`) 将事件“注入”回 The Boss 的事件队列。
    - 严禁直接修改 The Boss 的内存状态。

---

## 🧩 适配器模式 (Adapter Pattern)

所有券商接入必须继承自 `BaseGateway` 并实现以下标准：

- **Northbound (对外)**: 必须返回 `UnifiedOrder`, `UnifiedBalance` 等 Pydantic 标准模型。
- **Southbound (对内)**: 内部封装具体的 Broker SDK。
- **Methodology**:
    - HTTP 接口: 使用 `run_in_executor` 包装。
    - Stream 接口: 在 `lifespan` 中启动 Sentinel 线程。

## 🤖 交互流程示例 (Interaction Flow)

**场景: 下单 (Place Order)**

1.  **Client** (Nautilus) 发送 POST 请求。
2.  **Boss** (FastAPI) 接收请求，校验 Pydantic 模型。
3.  **Boss** 将具体的 SDK 调用扔给 **Worker**。
4.  **Worker** 阻塞等待 Broker HTTP 响应，拿到 `order_id`。
5.  **Boss** 收到 `order_id`，立即返回 HTTP 200 给 Client。
6.  (异步) **Sentinel** 在 gRPC 流中收到 "FILLED" 事件。
7.  **Sentinel** 将事件推送到 **Boss** 的 `Queue`。
8.  **Boss** 更新内存状态，并通过 WebSocket 推送给 **Client**。

---
*此文档旨在指导 AI Assistant 和开发者理解 Janus 的非阻塞设计哲学。*