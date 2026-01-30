import yaml
from pathlib import Path
from typing import Dict, Any, List

class ConfigLoader:
    def __init__(self, config_path: str = "config.yaml"):
        self.path = Path(config_path)
        self.config: Dict[str, Any] = {}
        if self.path.exists():
            with open(self.path, "r") as f:
                self.config = yaml.safe_load(f) or {}

    def get_all_accounts(self) -> List[Dict[str, Any]]:
        """获取所有配置的账户列表"""
        return self.config.get("accounts", [])

    def get_rpc_setting(self) -> Dict[str, str]:
        return self.config.get("rpc", {
            "rep_address": "tcp://*:2014",
            "pub_address": "tcp://*:4102"
        })

    def get_history_setting(self) -> str:
        return self.config.get("history_file", ".janus_history")
    
    def get_default_account_name(self) -> str:
        """获取默认账户名"""
        if "default_account" in self.config:
            return self.config["default_account"]
        accounts = self.get_all_accounts()
        return accounts[0]["name"] if accounts else ""