# vnpy_ib Study Summary (Legacy Reference)

Note: This document is kept for historical reference only. Janus no longer
depends on `vnpy_ib` or `ibapi` at runtime.

## Scope
对 `../vnpy_all/vnpy_ib` 中与“订单状态推送/事件回调/线程模型/本地状态维护”相关的实现做梳理，提炼可复用的模式，便于参考到 Janus/Webull trade events 设计中。

## Key Patterns Worth Reusing
### 1) 订单/成交事件的“本地缓存 + 增量更新”
- 订单缓存：`IbApi.orders` 作为本地单体来源，`orderStatus` 和 `openOrder` 都在更新该缓存并通过 `gateway.on_order` 推送事件。
- `orderStatus` 仅更新“成交量+状态”，其余字段来自本地缓存，避免回调字段不全。见 `../vnpy_all/vnpy_ib/vnpy_ib/ib_gateway.py:496`。
- `openOrder` 在订单首次出现时构造 `OrderData`，并在本地缓存后推送。见 `../vnpy_all/vnpy_ib/vnpy_ib/ib_gateway.py:541`。
- `execDetails` 只负责成交推送（`TradeData`），不回写订单状态，逻辑清晰分层。见 `../vnpy_all/vnpy_ib/vnpy_ib/ib_gateway.py:746`。

可迁移结论：
- Webull trade events 也可采用“订单事件只做最小字段更新、其余字段取本地缓存”的策略，降低事件缺字段的影响。

### 2) 明确的状态映射表 + 事件过滤
- IB 状态映射：`STATUS_IB2VT` 明确把 IB status 映射到 vn.py Status。见 `../vnpy_all/vnpy_ib/vnpy_ib/ib_gateway.py:47`。
- `orderStatus` 只在映射表命中时更新状态，过滤中间态。见 `../vnpy_all/vnpy_ib/vnpy_ib/ib_gateway.py:531`。

可迁移结论：
- Webull 也应维护映射表（`order_status`/`scene_type` -> vn.py Status），并保留“不在映射表内则不更新”的守护逻辑。

### 3) 事件回调线程与主线程解耦
- `IbApi.connect` 启动 `EClient.run` 的独立线程处理回调。见 `../vnpy_all/vnpy_ib/vnpy_ib/ib_gateway.py:850`。
- `check_connection` 若断线则重建连接并重新启动线程。见 `../vnpy_all/vnpy_ib/vnpy_ib/ib_gateway.py:860`。
- VN.py 事件推送通过 `gateway.on_*`，由 EventEngine 统一处理，避免回调线程直接操作 UI/客户端。

可迁移结论：
- Webull trade events 也应采用“独立线程拉流 + 回调轻量处理 + 推送 EventEngine”的模型。

### 4) 重连/状态健康检查通过定时事件驱动
- `IbGateway` 注册 `EVENT_TIMER`，每 10 次触发 `api.check_connection`，做心跳式重连。见 `../vnpy_all/vnpy_ib/vnpy_ib/ib_gateway.py:223`。
- 错误码 2104 作为行情连接已就绪标志，触发重订阅已缓存的订阅列表。见 `../vnpy_all/vnpy_ib/vnpy_ib/ib_gateway.py:330`。

可迁移结论：
- Webull trade events 可通过“后台线程内重连 + 指数退避”，或通过 EventEngine 定时触发健康检查。

### 5) 订单创建与本地缓存的一致性
- `send_order` 创建本地 `OrderData` 并立即 `on_order` 推送，同时写入 `orders` 字典，保证后续回调能补齐字段。见 `../vnpy_all/vnpy_ib/vnpy_ib/ib_gateway.py:966`。
- `openOrder` 对已存在的本地订单进行补齐，避免交易所返回 exchange 变动造成的字段漂移。见 `../vnpy_all/vnpy_ib/vnpy_ib/ib_gateway.py:553`。

可迁移结论：
- Webull send_order 后立即缓存 order，事件回调只更新关键字段即可。

## Real-time Market Data (IB) — Deep Dive
### Subscription flow
- `subscribe` 入口：校验交易所、去重、解析合约、请求合约详情、发起 `reqMktData`。见 `../vnpy_all/vnpy_ib/vnpy_ib/ib_gateway.py:920`。
- 合约解析：支持字符串风格与 ConId 两种格式，字符串格式通过 `generate_ib_contract` 解构。见 `../vnpy_all/vnpy_ib/vnpy_ib/ib_gateway.py:1183`。
- 订阅去重：使用 `self.subscribed` 保存 `vt_symbol` -> SubscribeRequest，避免重复订阅。见 `../vnpy_all/vnpy_ib/vnpy_ib/ib_gateway.py:934`。
- 订阅 reqid：`self.reqid` 递增并用于 `reqMktData`，同时建立 `self.ticks[reqid] = TickData` 的本地缓存。见 `../vnpy_all/vnpy_ib/vnpy_ib/ib_gateway.py:954`。

### Tick aggregation & normalization
- `tickPrice/tickSize`：按 `TICKFIELD_IB2VT` 映射字段更新 `TickData`，并触发 `gateway.on_tick`。见 `../vnpy_all/vnpy_ib/vnpy_ib/ib_gateway.py:360` 和 `../vnpy_all/vnpy_ib/vnpy_ib/ib_gateway.py:392`。
- `tickString(LAST_TIMESTAMP)`：更新 tick.datetime，补齐 tick 时间戳。见 `../vnpy_all/vnpy_ib/vnpy_ib/ib_gateway.py:414`。
- Forex/Spot 简化：IDEALPRO 或 `CMDTY` 品种缺少 last 时，使用 bid/ask 计算中间价并更新时间。见 `../vnpy_all/vnpy_ib/vnpy_ib/ib_gateway.py:379`。
- 期权 Greeks：`tickOptionComputation` 写入 `tick.extra`，统一由 TickData 承载扩展字段。见 `../vnpy_all/vnpy_ib/vnpy_ib/ib_gateway.py:438`。

### Snapshot vs streaming
- `query_tick` 使用 `reqMktData(..., snapshot=True)` 获取行情切片。见 `../vnpy_all/vnpy_ib/vnpy_ib/ib_gateway.py:1135`。
- `tickSnapshotEnd` 作为切片完成信号，仅记录日志。见 `../vnpy_all/vnpy_ib/vnpy_ib/ib_gateway.py:473`。

### Unsubscribe
- `unsubscribe` 通过搜索 `self.ticks` 找到对应 reqid 并调用 `cancelMktData`。见 `../vnpy_all/vnpy_ib/vnpy_ib/ib_gateway.py:1165`。
- 取消订阅不清理 `self.ticks` 缓存（仅停数据流），这可能留下已取消的 TickData 记录。

### Resubscribe on connection-ready
- 在 `error` 中监听错误码 2104 作为行情连接就绪信号，触发重订阅缓存的 SubscribeRequest。见 `../vnpy_all/vnpy_ib/vnpy_ib/ib_gateway.py:330`。
- 这是一种“连接就绪再重发订阅”的模式，可直接借鉴到 IB/Webull 的实时行情对接。

## Implications for Janus IB Integration
- 采用 `reqid -> TickData` 缓存 + 多回调合并（price/size/timestamp/greeks）构造完整 TickData。
- 订阅管理需显式去重/恢复，断线后可重放 `self.subscribed`。
- 对外推送统一走 `gateway.on_tick`，确保 event_engine/RPC 客户端解耦。

## Thread Model (vnpy_ib)
- 主要回调线程：`EClient.run` 在独立线程中运行，所有 IB 回调在该线程中触发。
- 事件下发：回调中调用 `gateway.on_*` 将事件交给 vn.py EventEngine；EventEngine 再分发给 RPC/TUI。
- 健康检查线程：无独立线程；通过 EventEngine 的 `EVENT_TIMER` 定期触发 `check_connection`。

## Practical Implications for Janus/Webull
- 建议 Webull trade events 在服务端采用“独立线程流式订阅 + 轻量回调 + on_order/on_trade 推送”的模型。
- 使用本地订单缓存（类似 `IbApi.orders`）保证事件字段不完整时仍能输出完整 OrderData。
- 状态映射必须显式、可控；未知状态保持不更新，触发一次性告警日志。

## References
- `../vnpy_all/vnpy_ib/vnpy_ib/ib_gateway.py`
