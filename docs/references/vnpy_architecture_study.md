# vn.py Architecture Study

## Scope
学习 `../vnpy_all/vnpy` 的核心架构（EventEngine / MainEngine / BaseGateway / OmsEngine / RPC），提炼与 Janus 架构一致的模式与对接约束。

## Core Event Model
- `EventEngine` 使用队列 + 两个线程：
  - `_run` 线程从队列消费事件并分发。
  - `_run_timer` 线程按 interval 生成 `EVENT_TIMER`。见 `../vnpy_all/vnpy/vnpy/event/engine.py:33`。
- 事件分发：先按 type 定向分发，再分发给“通用处理器”。见 `../vnpy_all/vnpy/vnpy/event/engine.py:59`。
- 事件对象是 `Event(type, data)`，data 可为任意业务对象。见 `../vnpy_all/vnpy/vnpy/event/engine.py:17`。

## Event Types
- 交易事件类型集中定义（`eTick.` / `eOrder.` / `eTrade.` / `ePosition.` / `eAccount.` / `eContract.` / `eLog`）。见 `../vnpy_all/vnpy/vnpy/trader/event.py:5`。

## MainEngine (系统核心)
- `MainEngine` 在初始化时启动 `EventEngine` 并创建内置引擎：`LogEngine`、`OmsEngine`、`EmailEngine`。见 `../vnpy_all/vnpy/vnpy/trader/engine.py:73`。
- `MainEngine` 作为统一入口：负责添加 gateway/app，路由 connect/subscribe/send_order 等请求。见 `../vnpy_all/vnpy/vnpy/trader/engine.py:102`。
- `MainEngine.close()` 会先 stop EventEngine，再关闭 engines 与 gateways。见 `../vnpy_all/vnpy/vnpy/trader/engine.py:264`。

## BaseGateway Contract (重要约束)
- BaseGateway 明确要求：线程安全、非阻塞、自动重连。见 `../vnpy_all/vnpy/vnpy/trader/gateway.py:29`。
- `connect()` 必须完成账户/持仓/订单/成交/合约等初始查询并通过 `on_*` 推送。见 `../vnpy_all/vnpy/vnpy/trader/gateway.py:134`。
- `on_*` 方法会把对象塞入 EventEngine（同时发通用事件和按标的细分事件）。见 `../vnpy_all/vnpy/vnpy/trader/gateway.py:92`。
- 数据对象推送必须不可变（若缓存需 copy）。见 `../vnpy_all/vnpy/vnpy/trader/gateway.py:45`。

## OmsEngine (统一内存状态)
- OmsEngine 订阅所有核心事件并维护本地快照：ticks/orders/trades/positions/accounts/contracts/quotes。见 `../vnpy_all/vnpy/vnpy/trader/engine.py:303`。
- 维护“活动订单/报价”集合，决定 `is_active()` 的生命周期。见 `../vnpy_all/vnpy/vnpy/trader/engine.py:363`。
- OffsetConverter 按 gateway_name 维护，依赖订单/成交/持仓事件更新。见 `../vnpy_all/vnpy/vnpy/trader/engine.py:323`。

## LogEngine
- LogEngine 监听 `EVENT_LOG`，通过统一 logger 输出。见 `../vnpy_all/vnpy/vnpy/trader/engine.py:287`。

## App/Engine 扩展模型
- `BaseApp` 用于在 MainEngine 中注册扩展引擎（app -> engine）。见 `../vnpy_all/vnpy/vnpy/trader/app.py:9`。
- Janus 使用的 vnpy_rpcservice 就是典型 app/engine 模式的扩展。

## RPC (vnpy.rpc)
- `RpcServer` 使用 REP + PUB 两个 socket，后台线程轮询并发送心跳。见 `../vnpy_all/vnpy/vnpy/rpc/server.py:11`。
- `RpcClient` 使用 REQ + SUB 两个 socket，后台线程消费推送，心跳丢失触发 `on_disconnected`。见 `../vnpy_all/vnpy/vnpy/rpc/client.py:29`。
- `RpcClient.__getattr__` 使用动态方法名实现远程调用。见 `../vnpy_all/vnpy/vnpy/rpc/client.py:36`。

## Implications for Janus
- 事件驱动是核心：gateway -> `on_*` -> EventEngine -> OmsEngine/RPC/TUI。
- Gateway 必须遵循“非阻塞、线程安全、自动重连”硬约束。
- 初始 `connect()` 要主动补齐账户/持仓/订单/成交/合约，保证客户端启动后可立即展示。
- 对接 IB 实时行情时，应参考 vnpy 的 `tickPrice/tickSize/tickString` 合并逻辑，输出完整 TickData。

## References
- `../vnpy_all/vnpy/vnpy/event/engine.py`
- `../vnpy_all/vnpy/vnpy/trader/engine.py`
- `../vnpy_all/vnpy/vnpy/trader/gateway.py`
- `../vnpy_all/vnpy/vnpy/trader/event.py`
- `../vnpy_all/vnpy/vnpy/rpc/client.py`
- `../vnpy_all/vnpy/vnpy/rpc/server.py`
