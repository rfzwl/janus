import yaml
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Any

class ConfigLoader:
    def __init__(self, config_path: str = "config.yaml"):
        self.path = Path(config_path)
        self.config: Dict[str, Any] = {}
        if self.path.exists():
            with open(self.path, "r") as f:
                self.config = yaml.safe_load(f)

    def get_webull_setting(self) -> Dict[str, Any]:
        """Load Official Open API Config"""
        wb_conf = self.config.get("webull", {})
        return {
            "app_key": wb_conf.get("app_key", ""),
            "app_secret": wb_conf.get("app_secret", ""),
            "region_id": wb_conf.get("region_id", "us"),
        }

    def get_rpc_setting(self) -> Dict[str, str]:
        return self.config.get("rpc", {
            "rep_address": "tcp://*:2014",
            "pub_address": "tcp://*:4102"
        })

    def get_history_setting(self) -> str:
        """获取历史记录文件路径，默认为当前目录下的 .janus_history"""
        return self.config.get("history_file", ".janus_history")