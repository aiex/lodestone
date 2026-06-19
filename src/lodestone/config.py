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
    def memory(self) -> dict:
        return self._data.get("memory", {}) or {}

    @property
    def memory_enabled(self) -> bool:
        cfg = self.memory
        return bool(cfg) and bool(cfg.get("enabled", False))

    @property
    def web(self) -> dict:
        """Dashboard settings. Defaults bind to localhost only (token auth)."""
        return self._data.get("web", {}) or {}

    @property
    def loop(self) -> dict:
        """Agent Loop settings. Absent block == feature off (enabled defaults False)."""
        return self._data.get("loop", {}) or {}

    @property
    def loop_enabled(self) -> bool:
        """Agent Loop is opt-in; absent block or enabled:false keeps it off."""
        cfg = self.loop
        return bool(cfg) and bool(cfg.get("enabled", False))

    @property
    def db_path(self) -> str:
        return self._data.get("database", {}).get("path", "data/lodestone.db")

    def agent(self, agent_id: str):
        for a in self.agents:
            if a.get("id") == agent_id:
                return a
        return None


VALID_PROJECT_STATUS = ("dev", "live")


def normalize_project(entry) -> tuple:
    """Accept a project as a bare string or a {name, status} mapping.

    Backward-compatible: a string defaults to status 'dev', so existing configs
    keep working. Any status other than dev/live falls back to 'dev' (fail-safe:
    you must opt a project into the stricter 'live' gate explicitly).
    """
    if isinstance(entry, str):
        return entry, "dev"
    name = entry.get("name")
    status = (entry.get("status") or "dev").strip().lower()
    if status not in VALID_PROJECT_STATUS:
        status = "dev"
    return name, status


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
