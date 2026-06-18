import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from lodestone.registry import db
from lodestone.hub.loop import LoopSupervisor
from lodestone.hub.protocol import MARKER


def env(status, seq, tokens=100, **kw):
    d = {"status": status, "seq": seq, "summary": status.lower(), "tokens_used": tokens}
    d.update(kw)
    return f"{MARKER} {json.dumps(d)}"


def make_config(loop_cfg):
    agents = [{
        "id": "a1", "name": "A1", "type": "t", "host": "h", "telegram_peer": "@a",
        "projects": ["devproj", {"name": "liveproj", "status": "live"}],
        "permissions": ["x"],
    }]
    cfg = SimpleNamespace(agents=agents, ai={"model": "gpt-4o-mini"},
                          dispatch={"reply_timeout": 30}, loop=loop_cfg)
    cfg.agent = lambda aid: next((a for a in agents if a["id"] == aid), None)
    return cfg


class LoopSupervisorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "l.db")
        self.cfg = make_config({"token_budget": 100000, "max_steps": 10})
        db.init_db(self.db_path)
        db.sync_from_config(self.db_path, self.cfg)

    def tearDown(self):
        self.tmp.cleanup()

    def _supervisor(self, script):
        it = iter(script)

        async def send(peer, text):
            return next(it)

        return LoopSupervisor(self.db_path, self.cfg, send)

    async def test_dev_project_runs_through_pr_to_done(self):
        sup = self._supervisor([
            env("MILESTONE", 1),
            env("GATE_PR", 2, pr_url="https://github.com/a/b/pull/1"),
            env("DONE", 3),
        ])
        tid, est = sup.estimate("devproj", "build it")
        self.assertTrue(est.fits)
        res = await sup.confirm_and_run(tid)
        self.assertEqual(res.state, "done")
        self.assertTrue(res.done)
        self.assertEqual(db.get_loop_run(self.db_path, tid)["status"], "done")

    async def test_live_project_pauses_at_pr_and_resumes_on_approve(self):
        sup = self._supervisor([
            env("MILESTONE", 1),
            env("GATE_PR", 2, pr_url="https://github.com/a/b/pull/2"),
        ])
        tid, _ = sup.estimate("liveproj", "ship it")
        res = await sup.confirm_and_run(tid)
        self.assertEqual(res.state, "awaiting_pr_approval")
        self.assertIn("pull/2", res.message)
        self.assertEqual(db.get_loop_run(self.db_path, tid)["status"], "awaiting_pr_approval")

        # Now approve — supply the rest of the script for the resumed run.
        sup._send = self._supervisor([env("DONE", 3)])._send
        res2 = await sup.approve_pr(tid)
        self.assertEqual(res2.state, "done")

    async def test_live_project_reject_stops_run(self):
        sup = self._supervisor([
            env("GATE_PR", 1, pr_url="https://github.com/a/b/pull/3"),
        ])
        tid, _ = sup.estimate("liveproj", "ship it")
        await sup.confirm_and_run(tid)
        res = await sup.reject_pr(tid, "not ready")
        self.assertEqual(res.state, "stopped")
        self.assertEqual(db.get_loop_run(self.db_path, tid)["status"], "stopped")

    async def test_approve_on_non_paused_run_is_a_noop_guard(self):
        sup = self._supervisor([env("DONE", 1)])
        tid, _ = sup.estimate("devproj", "build it")
        res_done = await sup.confirm_and_run(tid)   # finishes immediately
        self.assertEqual(res_done.state, "done")
        # Approving an already-finished run must not re-run it; it returns a guard.
        res = await sup.approve_pr(tid)
        self.assertIn("not awaiting", res.message.lower())
        self.assertEqual(db.get_loop_run(self.db_path, tid)["status"], "done")

    async def test_blocked_round_trips_input(self):
        sup = self._supervisor([env("BLOCKED", 1)])
        tid, _ = sup.estimate("devproj", "build it")
        res = await sup.confirm_and_run(tid)
        self.assertEqual(res.state, "awaiting_input")

        sup._send = self._supervisor([env("DONE", 2)])._send
        res2 = await sup.provide_input(tid, "use postgres")
        self.assertEqual(res2.state, "done")

    async def test_budget_hard_stop(self):
        cfg = make_config({"token_budget": 500, "max_steps": 100})
        db.sync_from_config(self.db_path, cfg)
        it = iter(env("MILESTONE", i, tokens=200) for i in range(1, 100))

        async def send(peer, text):
            return next(it)

        sup = LoopSupervisor(self.db_path, cfg, send)
        tid, _ = sup.estimate("devproj", "x")
        res = await sup.confirm_and_run(tid)
        self.assertEqual(res.state, "stopped")
        self.assertIn("budget", res.message.lower())

    async def test_max_steps_watchdog_stops_forever_loop(self):
        cfg = make_config({"token_budget": 10**9, "max_steps": 3})
        db.sync_from_config(self.db_path, cfg)
        it = iter(env("MILESTONE", i, tokens=1) for i in range(1, 100))

        async def send(peer, text):
            return next(it)

        sup = LoopSupervisor(self.db_path, cfg, send)
        tid, _ = sup.estimate("devproj", "x")
        res = await sup.confirm_and_run(tid)
        self.assertEqual(res.state, "stopped")
        self.assertEqual(db.get_loop_run(self.db_path, tid)["steps_done"], 3)

    async def test_transport_error_marks_error(self):
        async def send(peer, text):
            raise RuntimeError("peer offline")

        sup = LoopSupervisor(self.db_path, self.cfg, send)
        tid, _ = sup.estimate("devproj", "x")
        res = await sup.confirm_and_run(tid)
        self.assertEqual(res.state, "error")

    async def test_unknown_project_estimate_errors(self):
        sup = self._supervisor([])
        tid, msg = sup.estimate("nope", "x")
        self.assertIsNone(tid)
        self.assertIn("No such project", msg)

    async def test_freetext_pr_on_live_still_gates(self):
        # Agent that ignores the protocol but mentions a PR url: heuristic still
        # catches GATE_PR, so a live project still pauses.
        sup = self._supervisor(["Opened https://github.com/a/b/pull/7 for review"])
        tid, _ = sup.estimate("liveproj", "ship it")
        res = await sup.confirm_and_run(tid)
        self.assertEqual(res.state, "awaiting_pr_approval")


if __name__ == "__main__":
    unittest.main()
