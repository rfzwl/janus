import time
import sys
import logging
from threading import Event

from vnpy.event import EventEngine
from vnpy.trader.engine import MainEngine
from vnpy_rpcservice import RpcServiceApp

# 导入新的 Webull Gateway
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

        self.main_engine.add_app(RpcServiceApp)
        # 添加 Webull Gateway
        self.main_engine.add_gateway(WebullOfficialGateway)

        self.rpc_engine = self.main_engine.get_engine("RPC")

    def run(self):
        logger.info("Starting Janus Server (Webull Official API)...")
        
        # 1. Connect Webull
        wb_setting = self.config.get_webull_setting()
        if wb_setting["app_key"]:
            self.main_engine.connect(wb_setting, "WEBULL")
        else:
            logger.warning("No Webull config found!")
        
        # 2. Start RPC
        rpc_setting = self.config.get_rpc_setting()
        self.rpc_engine.start(
            rep_address=rpc_setting["rep_address"],
            pub_address=rpc_setting["pub_address"]
        )
        logger.info(f"RPC Service running at {rpc_setting['rep_address']}")

        try:
            while not self.stop_event.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            self.shutdown()

    def shutdown(self):
        self.main_engine.close()
        sys.exit(0)

if __name__ == "__main__":
    # 允许直接运行此文件用于测试
    JanusServer().run()