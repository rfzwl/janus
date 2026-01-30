import sys
import logging
from threading import Event

from vnpy.event import EventEngine
from vnpy.trader.engine import MainEngine
from vnpy.trader.event import EVENT_LOG
from vnpy.trader.object import LogData
from vnpy_rpcservice import RpcServiceApp

from .gateway.webull.webull_gateway import WebullOfficialGateway
from .config import ConfigLoader

logging.basicConfig(level=logging.WARNING, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
sys_logger = logging.getLogger("JanusBootstrap")

class JanusServer:
    def __init__(self):
        self.config = ConfigLoader()
        self.event_engine = EventEngine()
        self.event_engine.register(EVENT_LOG, self._sanitize_log_event)
        self.main_engine = MainEngine(self.event_engine)
        self.stop_event = Event()

        # 1. 加载 App
        self.main_engine.add_app(RpcServiceApp)

        # 2. 注册 Gateway 类 (Map 结构)
        self.broker_map = {
            "webull": WebullOfficialGateway,
            # "ib": IbGateway,
        }

        # 3. 获取 RPC 引擎
        self.rpc_engine = self.main_engine.get_engine("RpcService")
        
        if not self.rpc_engine:
            sys_logger.error("严重错误：无法加载 RPC 引擎！请检查 vnpy_rpcservice 是否安装正确。")
            sys_logger.error(f"Available Engines: {list(self.main_engine.engines.keys())}")
            sys.exit(1)

        self.rpc_engine.server.register(self.remote_exit)
        self.rpc_engine.server.register(self.sync_all)

    def sync_all(self):
        """主动触发所有 Gateway 同步数据"""
        for gateway_name in self.main_engine.gateways.keys():
            gateway = self.main_engine.get_gateway(gateway_name)
            if gateway:
                gateway.query_account()
                gateway.query_position()
        return "Sync request sent to all gateways."

    def _sanitize_log_event(self, event) -> None:
        """
        修复日志事件数据的格式问题。
        这是一个"中间人"函数，在日志交给 MainEngine 处理前先清洗一遍。
        """
        data = event.data

        # --- 修复 1: 解决 AttributeError (崩溃元凶) ---
        # 如果数据是纯字符串 (通常由 Loguru 拦截 Webull 日志产生)
        if isinstance(data, str):
            # 原地将 event.data 替换为标准的 LogData 对象
            # 这样 MainEngine 收到后就能正常读取 .level 属性了
            event.data = LogData(
                msg=data,
                gateway_name="WebullSDK",
                level=logging.INFO
            )
            return  # 处理完毕，直接返回

        # --- 修复 2: 解决 KeyError (Loguru 格式化错误) ---
        # 如果数据已经是 LogData，但内容里包含花括号 { }
        # (通常是 Webull 打印了字典类型的调试信息)
        if isinstance(data, LogData):
            try:
                # 将 { 转义为 {{，将 } 转义为 }}
                # 这样 Loguru 就会把它当做普通字符，而不是格式化占位符
                if "{" in str(data.msg):
                    data.msg = str(data.msg).replace("{", "{{").replace("}", "}}")
            except Exception:
                pass

    def remote_exit(self):
        """
        供客户端调用的远程关闭函数
        """
        msg = "收到客户端远程关闭指令 (Remote Exit) ..."
        self.main_engine.write_log(msg)
        self.stop_event.set()
        return "Server is shutting down..."

    def run(self):
        sys_logger.info("Starting Janus Server ...")

        # 4. 循环连接所有配置的账户
        accounts = self.config.get_all_accounts()
        for acct_config in accounts:
            broker_type = acct_config.get("broker", "").lower()
            acct_name = acct_config.get("name", "Unknown")

            gateway_class = self.broker_map.get(broker_type)
            if not gateway_class:
                self.main_engine.write_log(
                    f"WARNING: Unsupported broker type {broker_type} for account {acct_name}."
                )
                continue

            sys_logger.info(f"Connecting to account: {acct_name} ({broker_type})")
            self.main_engine.add_gateway(gateway_class, acct_name)
            self.main_engine.connect(acct_config, acct_name)

        # 5. 启动 RPC 服务
        rpc_setting = self.config.get_rpc_setting()
        try:
            self.rpc_engine.start(
                rep_address=rpc_setting["rep_address"],
                pub_address=rpc_setting["pub_address"]
            )
            self.main_engine.write_log(
                f"Janus Server Ready. RPC at {rpc_setting['rep_address']}"
            )
        except Exception as e:
            sys_logger.error(f"Failed to start RPC service: {e}")
            self.shutdown()
        
        # 6. 主循环
        try:
            while not self.stop_event.is_set():
                self.stop_event.wait(1.0)
        except KeyboardInterrupt:
            sys_logger.info("KeyboardInterrupt received.")
        finally:
            self.shutdown()

    def shutdown(self):
        sys_logger.info("Shutting down...")
        self.main_engine.close()
        sys.exit(0)

if __name__ == "__main__":
    JanusServer().run()
