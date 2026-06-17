import os
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = os.environ.get("LODESTONE_CONFIG", "config/config.yaml")


class Config:
    def __init__(self, data: dict, path: str):
        self._data = data or {}
        self.path = path

    @property
    def telegram(self) -> dict:
        return self._data.get("telegram", {})

    @property
    def agents(self) -> list:
        return self._data.get("agents", []) or []

    @property
    def dispatch(self) -> dict:
        return self._data.get("dispatch", {"reply_timeout": 60})

    @property
    def ai(self) -> dict:
        return self._data.get("ai", {})

    @property
    def db_path(self) -> str:
        return self._data.get("database", {}).get("path", "data/lodestone.db")

    def agent(self, agent_id: str):
        for a in self.agents:
            if a.get("id") == agent_id:
                return a
        return None


def load_config(path: str = None) -> Config:
    path = path or DEFAULT_CONFIG_PATH
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"Config not found at '{p}'. Copy config.example.yaml to '{p}' and fill it in."
        )
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return Config(data, str(p))
