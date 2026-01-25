import sys
import logging
from threading import Event

from vnpy.event import EventEngine
from vnpy.trader.engine import MainEngine
from vnpy.trader.event import EVENT_LOG
from vnpy_rpcservice import RpcServiceApp

from .gateway.webull.webull_gateway import WebullOfficialGateway
from .config import ConfigLoader

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("JanusServer")

class JanusServer:
    def __init__(self):
        self.config = ConfigLoader()
        self.event_engine = EventEngine()
        if hasattr(self.event_engine, "add_handler"):
            self.event_engine.add_handler(EVENT_LOG, self._sanitize_log_event)
        else:
            self.event_engine.register(EVENT_LOG, self._sanitize_log_event)
        self.main_engine = MainEngine(self.event_engine)
        self.stop_event = Event()

        # 1. 加载 App 和 Gateway
        self.main_engine.add_app(RpcServiceApp)
        self.main_engine.add_gateway(WebullOfficialGateway)

        # 2. 获取 RPC 引擎
        self.rpc_engine = self.main_engine.get_engine("RpcService")
        
        if not self.rpc_engine:
            logger.error("严重错误：无法加载 RPC 引擎！请检查 vnpy_rpcservice 是否安装正确。")
            logger.error(f"Available Engines: {list(self.main_engine.engines.keys())}")
            sys.exit(1)

        self.rpc_engine.server.register(self.remote_exit)

    def _sanitize_log_event(self, event) -> None:
        log_data = getattr(event, "data", None)
        if not log_data or not hasattr(log_data, "msg"):
            return
        try:
            log_data.msg = str(log_data.msg).replace("{", "{{").replace("}", "}}")
        except Exception:
            pass

    def remote_exit(self):
        """
        供客户端调用的远程关闭函数
        """
        logger.warning("收到客户端远程关闭指令 (Remote Exit) ...")
        self.stop_event.set()
        return "Server is shutting down..."

    def run(self):
        logger.info("Starting Janus Server ...")
        
        # 3. 连接 Webull
        wb_setting = self.config.get_webull_setting()
        if wb_setting.get("app_key"):
            self.main_engine.connect(wb_setting, "WEBULL")
        else:
            logger.warning("No Webull config found in config.yaml!")
        
        # 4. 启动 RPC 服务
        rpc_setting = self.config.get_rpc_setting()
        try:
            self.rpc_engine.start(
                rep_address=rpc_setting["rep_address"],
                pub_address=rpc_setting["pub_address"]
            )
            logger.info(f"RPC Service running at {rpc_setting['rep_address']}")
        except Exception as e:
            logger.error(f"Failed to start RPC service: {e}")
            self.shutdown()
        
        # 5. 主循环
        try:
            while not self.stop_event.is_set():
                self.stop_event.wait(1.0)
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received.")
        finally:
            self.shutdown()

    def shutdown(self):
        logger.info("Shutting down...")
        self.main_engine.close()
        sys.exit(0)

if __name__ == "__main__":
    JanusServer().run()
