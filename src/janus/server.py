import time
import signal
import sys
import logging
from threading import Event

from vnpy.event import EventEngine
from vnpy.trader.engine import MainEngine
from vnpy.trader.ui import MainWindow, create_qapp
from vnpy_rpcservice import RpcServiceApp

from .config import ConfigLoader

# Setup Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("JanusServer")

class JanusServer:
    def __init__(self):
        self.config = ConfigLoader()
        self.event_engine = EventEngine()
        self.main_engine = MainEngine(self.event_engine)
        self.stop_event = Event()

        # Add Apps and Gateways
        self.main_engine.add_app(RpcServiceApp)

        # Get Engines
        self.rpc_engine = self.main_engine.get_engine("RPC")

    def remote_exit(self):
        """Registered function to allow remote shutdown via RPC"""
        logger.warning("Received remote EXIT command. Shutting down...")
        self.stop_event.set()
        return "Server shutting down..."

    def run(self):
        logger.info("Starting Janus Server...")
        
        # 1. Connect to Webull Official
        wb_setting = self.config.get_webull_official_setting()

        self.main_engine.add_gateway(WebullOfficialGateway)
        self.main_engine.connect(wb_setting, "WEBULL")
        
        # Give it a second to initiate connection (optional check could be added)
        time.sleep(2)

        # 2. Start RPC Server
        rpc_setting = self.config.get_rpc_setting()
        self.rpc_engine.start(
            rep_address=rpc_setting["rep_address"],
            pub_address=rpc_setting["pub_address"]
        )
        
        # 3. Register custom functions for CLI
        self.rpc_engine.register(self.remote_exit)
        
        logger.info(f"RPC Service started at {rpc_setting['rep_address']}")

        # 4. Main Loop (Daemonize)
        try:
            while not self.stop_event.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received.")
        finally:
            self.shutdown()

    def shutdown(self):
        logger.info("Closing engines...")
        self.rpc_engine.close()
        self.main_engine.close()
        logger.info("Janus Server Stopped.")
        sys.exit(0)

def run():
    server = JanusServer()
    server.run()

if __name__ == "__main__":
    run()
