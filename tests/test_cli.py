import io
import sys
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from lodestone import cli


class CliTests(unittest.TestCase):
    def setUp(self):
        self.config = SimpleNamespace(db_path=":memory:")

    def _run_simple(self, argv, memory=None):
        with patch.object(sys, "argv", argv):
            with patch.object(cli, "load_config", return_value=self.config) as load_config:
                with patch("lodestone.cli.build_memory_client", return_value=memory) as build_memory:
                    out = io.StringIO()
                    with redirect_stdout(out):
                        cli.main()
        return out.getvalue(), load_config, build_memory

    def _run_agents(self, argv):
        with patch.object(sys, "argv", argv):
            with patch.object(cli, "load_config", return_value=self.config) as load_config:
                with patch.object(cli.db, "init_db") as init_db:
                    with patch.object(cli.db, "sync_from_config") as sync_from_config:
                        with patch("lodestone.hub.commands.cmd_agents", return_value="Agents") as cmd_agents:
                            out = io.StringIO()
                            with redirect_stdout(out):
                                cli.main()
        return out.getvalue(), load_config, init_db, sync_from_config, cmd_agents

    def test_agents_accepts_config_before_subcommand(self):
        out, load_config, init_db, sync_from_config, cmd_agents = self._run_agents(
            ["lodestone", "--config", "/tmp/config.yaml", "agents"]
        )
        self.assertEqual(out.strip(), "Agents")
        load_config.assert_called_once_with("/tmp/config.yaml")
        init_db.assert_called_once_with(":memory:")
        sync_from_config.assert_called_once_with(":memory:", self.config)
        cmd_agents.assert_called_once_with(":memory:")

    def test_agents_accepts_config_after_subcommand(self):
        out, load_config, init_db, sync_from_config, cmd_agents = self._run_agents(
            ["lodestone", "agents", "--config", "/tmp/config.yaml"]
        )
        self.assertEqual(out.strip(), "Agents")
        load_config.assert_called_once_with("/tmp/config.yaml")
        init_db.assert_called_once_with(":memory:")
        sync_from_config.assert_called_once_with(":memory:", self.config)
        cmd_agents.assert_called_once_with(":memory:")

    def test_memory_status_reports_disabled_backend(self):
        out, load_config, build_memory = self._run_simple(
            ["lodestone", "memory-status", "--config", "/tmp/config.yaml"], memory=None
        )
        self.assertIn("configured: no", out)
        load_config.assert_called_once_with("/tmp/config.yaml")
        build_memory.assert_called_once_with(self.config)

    def test_memory_smoke_prints_backend_result(self):
        class FakeMemory:
            async def smoke_test(self):
                return {
                    "configured": True,
                    "ok": True,
                    "smoke_ok": True,
                    "base_url": "http://127.0.0.1:8420",
                    "namespace": "lodestone",
                    "detail": "ok",
                }

        out, _load_config, _build_memory = self._run_simple(
            ["lodestone", "memory-smoke", "--config", "/tmp/config.yaml"], memory=FakeMemory()
        )
        self.assertIn("smoke_ok:   yes", out)
        self.assertIn("healthy:    yes", out)
