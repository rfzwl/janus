import time
import sys
import logging
from threading import Event

from vnpy.event import EventEngine
from vnpy.trader.engine import MainEngine
from vnpy_rpcservice import RpcServiceApp

# 确保导入了 Gateway
from .gateway.webull.webull_gateway import WebullOfficialGateway
from .config import ConfigLoader

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("JanusServer")

class JanusServer:
    def __init__(self):
        self.config = ConfigLoader()
        self.event_engine = EventEngine()
        self.main_engine = MainEngine(self.event_engine)
        self.stop_event = Event()

        # 1. 加载 App 和 Gateway
        self.main_engine.add_app(RpcServiceApp)
        self.main_engine.add_gateway(WebullOfficialGateway)

        # 2. 获取 RPC 引擎
        # 【关键修复】名称必须是 "RpcService" 而不是 "RPC"
        self.rpc_engine = self.main_engine.get_engine("RpcService")
        
        if not self.rpc_engine:
            logger.error("严重错误：无法加载 RPC 引擎！请检查 vnpy_rpcservice 是否安装正确。")
            # 打印当前所有引擎名称以供调试
            logger.error(f"Available Engines: {list(self.main_engine.engines.keys())}")
            sys.exit(1)

    def run(self):
        logger.info("Starting Janus Server (Webull Official API)...")
        
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
                time.sleep(1)
        except KeyboardInterrupt:
            self.shutdown()

    def shutdown(self):
        logger.info("Shutting down...")
        self.main_engine.close()
        sys.exit(0)

if __name__ == "__main__":
    JanusServer().run()