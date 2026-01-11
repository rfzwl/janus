import yaml
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Any

@dataclass
class ServerConfig:
    rep_address: str = "tcp://*:2014"
    pub_address: str = "tcp://*:4102"

@dataclass
class IBConfig:
    TWS_HOST: str = "127.0.0.1"
    TWS_PORT: int = 7497
    CLIENT_ID: int = 1

class ConfigLoader:
    def __init__(self, config_path: str = "config.yaml"):
        self.path = Path(config_path)
        self.config: Dict[str, Any] = {}
        if self.path.exists():
            with open(self.path, "r") as f:
                self.config = yaml.safe_load(f)
        
    def get_ib_setting(self) -> Dict[str, Any]:
        # 返回符合 vnpy_ib connect 函数要求的字典
        ib_conf = self.config.get("ib", {})
        return {
            "TWS地址": ib_conf.get("host", "127.0.0.1"),
            "TWS端口": ib_conf.get("port", 7497),
            "客户号": ib_conf.get("client_id", 1),
        }

    def get_rpc_setting(self) -> Dict[str, str]:
        return self.config.get("rpc", {
            "rep_address": "tcp://*:2014",
            "pub_address": "tcp://*:4102"
        })