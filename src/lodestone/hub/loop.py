"""Agent Loop supervisor (Phase 4).

Lodestone is the supervisor; a remote agent bot is the worker. The worker
self-loops through its own cheap steps and reports a checkpoint after each one
(see hub/protocol.py). The supervisor spends no LLM tokens on routine progress —
it only parses checkpoints, tracks the budget, and decides continue / pause /
stop. The two gates it actually enforces:

  * dev vs live — a live project HALTS at GATE_PR and will not be told to
    proceed until a human approves; a dev project is auto-acknowledged.
  * budget — a hard token cap plus tiered warnings.

Transport is injected as ``send_and_wait(peer, text) -> reply_text`` so this is
testable without Telethon and reuses the account client at runtime. The whole
run is a state machine persisted in ``loop_runs`` / ``loop_events``: when a run
parks (awaiting approval/input) the coroutine returns, and ``resume`` / a gate
method continues it later. That keeps a paused live deploy from pinning a task.
"""

import uuid

from ..registry import db
from ..ai import cost as cost_mod
from ..ai.budget import BudgetMonitor, estimate_run
from . import protocol


class LoopResult:
    """What a drive step hands back to the control surface to show the user."""

    def __init__(self, task_id, state, message, done=False):
        self.task_id = task_id
        self.state = state          # running | awaiting_pr_approval | awaiting_input | done | stopped | error
        self.message = message      # human-facing text
        self.done = done            # terminal?


class LoopSupervisor:
    def __init__(self, db_path, config, send_and_wait):
        self.db_path = db_path
        self.config = config
        # send_and_wait(peer, text) -> reply_text (async)
        self._send = send_and_wait
        cfg = config.loop if hasattr(config, "loop") else {}
        self.cfg = cfg or {}
        self.token_budget = int(self.cfg.get("token_budget", 2_000_000))
        self.max_steps = int(self.cfg.get("max_steps", 40))
        self.warn_at = float(self.cfg.get("warn_at", 0.75))
        self.constrain_at = float(self.cfg.get("constrain_at", 0.90))
        self.allow_heuristic = bool(self.cfg.get("allow_heuristic", True))

    # --- estimation / start ------------------------------------------------

    def estimate(self, project_name, task, steps=None):
        """Pre-flight: persist an 'estimated' run and return (task_id, Estimate).

        Does NOT start work — the control surface shows the estimate and waits
        for /loop_confirm. Returns (None, error_str) if the project is unknown.
        """
        row = db.get_project(self.db_path, project_name)
        if not row:
            return None, f"No such project: {project_name}"
        agent = self.config.agent(row["agent_id"])
        if not agent or not agent.get("telegram_peer"):
            return None, f"{row['agent_id']} has no telegram_peer configured."

        steps = int(steps) if steps else self.max_steps
        avg = db.avg_tokens_per_call(self.db_path)
        est = estimate_run(steps, avg, self.token_budget)

        task_id = uuid.uuid4().hex[:12]
        db.create_loop_run(
            self.db_path, task_id, row["agent_id"], project_name, row["status"],
            task, est.est_tokens, status="estimated",
        )
        db.log_loop_event(self.db_path, task_id, 0, "estimate", est.summary())
        return task_id, est

    # --- driving the run ---------------------------------------------------

    async def confirm_and_run(self, task_id):
        """Begin (or re-begin) an estimated run: send the opening instruction."""
        run = db.get_loop_run(self.db_path, task_id)
        if not run:
            return LoopResult(task_id, "error", f"Unknown loop: {task_id}", done=True)
        if run["status"] not in ("estimated",):
            return LoopResult(task_id, run["status"],
                              f"Loop {task_id} is already {run['status']}.")
        agent = self.config.agent(run["agent_id"])
        instruction = protocol.frame_instruction(
            run["task"], run["project"], run["project_status"], self.max_steps,
        )
        db.update_loop_run(self.db_path, task_id, status="running")
        db.log_event(self.db_path, run["agent_id"], "loop_start",
                     f"[{task_id}] {run['project']}: {run['task']}"[:500])
        return await self._pump(task_id, agent.get("telegram_peer"), instruction)

    async def resume(self, task_id, message):
        """Continue a parked run by sending ``message`` to the agent."""
        run = db.get_loop_run(self.db_path, task_id)
        if not run:
            return LoopResult(task_id, "error", f"Unknown loop: {task_id}", done=True)
        agent = self.config.agent(run["agent_id"])
        db.update_loop_run(self.db_path, task_id, status="running")
        return await self._pump(task_id, agent.get("telegram_peer"), message)

    async def approve_pr(self, task_id):
        run = db.get_loop_run(self.db_path, task_id)
        if not run:
            return LoopResult(task_id, "error", f"Unknown loop: {task_id}", done=True)
        if run["status"] != "awaiting_pr_approval":
            return LoopResult(task_id, run["status"],
                              f"Loop {task_id} is not awaiting PR approval (it is {run['status']}).")
        db.log_loop_event(self.db_path, task_id, run["last_seq"], "pr_approved", run.get("pr_url") or "")
        db.log_event(self.db_path, run["agent_id"], "loop_pr_approved", f"[{task_id}]")
        return await self.resume(task_id, protocol.CONTINUE)

    async def reject_pr(self, task_id, reason=""):
        run = db.get_loop_run(self.db_path, task_id)
        if not run:
            return LoopResult(task_id, "error", f"Unknown loop: {task_id}", done=True)
        db.update_loop_run(self.db_path, task_id, status="stopped")
        db.log_loop_event(self.db_path, task_id, run["last_seq"], "pr_rejected", reason)
        db.log_event(self.db_path, run["agent_id"], "loop_pr_rejected", f"[{task_id}] {reason}"[:500])
        return LoopResult(task_id, "stopped",
                          f"PR rejected for loop {task_id}. Run stopped.", done=True)

    async def provide_input(self, task_id, text):
        run = db.get_loop_run(self.db_path, task_id)
        if not run:
            return LoopResult(task_id, "error", f"Unknown loop: {task_id}", done=True)
        if run["status"] != "awaiting_input":
            return LoopResult(task_id, run["status"],
                              f"Loop {task_id} is not awaiting input (it is {run['status']}).")
        return await self.resume(task_id, f"{protocol.CONTINUE} {text}".strip())

    async def stop(self, task_id):
        run = db.get_loop_run(self.db_path, task_id)
        if not run:
            return LoopResult(task_id, "error", f"Unknown loop: {task_id}", done=True)
        db.update_loop_run(self.db_path, task_id, status="stopped")
        db.log_event(self.db_path, run["agent_id"], "loop_stopped", f"[{task_id}]")
        return LoopResult(task_id, "stopped", f"Loop {task_id} stopped.", done=True)

    # --- the core pump -----------------------------------------------------

    async def _pump(self, task_id, peer, first_message):
        """Send messages and consume checkpoints until terminal or parked.

        One iteration: send -> await reply -> parse checkpoint -> branch. The
        loop's hard guards (max_steps, budget) are enforced here in code, never
        left to the agent's discretion.
        """
        run = db.get_loop_run(self.db_path, task_id)
        agent_id = run["agent_id"]
        project = run["project"]
        project_status = run["project_status"]
        monitor = BudgetMonitor(self.token_budget, self.warn_at, self.constrain_at,
                                used=run["used_tokens"], steps=run["steps_done"])
        last_seq = run["last_seq"]
        pr_url = run.get("pr_url")

        message = first_message
        while True:
            if monitor.steps >= self.max_steps:
                db.update_loop_run(self.db_path, task_id, status="stopped")
                db.log_event(self.db_path, agent_id, "loop_maxsteps", f"[{task_id}]")
                return LoopResult(task_id, "stopped",
                                  f"Loop {task_id} hit the {self.max_steps}-step cap and stopped.",
                                  done=True)
            try:
                reply = await self._send(peer, message)
            except Exception as e:  # noqa: BLE001 — surface transport failure
                db.update_loop_run(self.db_path, task_id, status="error")
                db.log_event(self.db_path, agent_id, "loop_error", f"[{task_id}] {e}"[:500])
                return LoopResult(task_id, "error",
                                  f"Loop {task_id} transport error: {e}", done=True)

            cp = protocol.parse_checkpoint(reply or "", self.allow_heuristic)

            seq_warn = ""
            if cp.seq is not None and last_seq and cp.seq > last_seq + 1:
                seq_warn = f" (warning: skipped seq {last_seq + 1}..{cp.seq - 1})"
            if cp.seq is not None:
                last_seq = cp.seq

            signal = monitor.add(cp.tokens_used, steps_planned=self.max_steps)
            pr_url = cp.pr_url or pr_url
            db.update_loop_run(self.db_path, task_id, used_tokens=monitor.used,
                               steps_done=monitor.steps, last_seq=last_seq, pr_url=pr_url)
            db.log_loop_event(self.db_path, task_id, cp.seq, cp.status,
                              cp.summary, cp.tokens_used)

            # Budget gates take precedence — a hard stop overrides everything.
            if signal == "stop":
                db.update_loop_run(self.db_path, task_id, status="stopped")
                return LoopResult(task_id, "stopped",
                                  self._budget_line(task_id, monitor,
                                                    f"Loop {task_id} hit the token budget and stopped."),
                                  done=True)

            # Terminal / gate handling by checkpoint status.
            if cp.status == "DONE":
                db.update_loop_run(self.db_path, task_id, status="done")
                db.log_event(self.db_path, agent_id, "loop_done", f"[{task_id}]")
                return LoopResult(task_id, "done", self._delivery(task_id, monitor, cp), done=True)

            if cp.status == "ERROR":
                db.update_loop_run(self.db_path, task_id, status="error")
                db.log_event(self.db_path, agent_id, "loop_error",
                             f"[{task_id}] {cp.summary}"[:500])
                return LoopResult(task_id, "error",
                                  f"Loop {task_id} agent reported an error:\n{cp.summary}", done=True)

            if cp.status == "BLOCKED":
                db.update_loop_run(self.db_path, task_id, status="awaiting_input")
                return LoopResult(task_id, "awaiting_input",
                                  f"Loop {task_id} is BLOCKED and needs your input:\n{cp.summary}\n"
                                  f"Reply: /loop_input {task_id} <your answer>")

            if cp.status == "GATE_PR":
                if project_status == "live":
                    db.update_loop_run(self.db_path, task_id, status="awaiting_pr_approval")
                    pr = cp.pr_url or "(no PR url reported)"
                    return LoopResult(task_id, "awaiting_pr_approval",
                                      f"Loop {task_id} reached a PR on LIVE project "
                                      f"'{project}'. Review and decide:\n{pr}\n{cp.summary}\n"
                                      f"Approve: /approve {task_id}   Reject: /reject {task_id}")
                # dev: acknowledge and keep going, no human in the loop.
                message = protocol.CONTINUE
                continue

            # MILESTONE / BUDGET_WARN: log, optionally surface a budget alert,
            # and keep the agent going. Routine progress costs zero LLM tokens.
            if signal in ("warn", "constrain") or cp.status == "BUDGET_WARN":
                # Surface a budget alert to the user but do not stop; the agent
                # continues. (Constrain could downshift; left as a notice here.)
                db.log_loop_event(self.db_path, task_id, cp.seq, "budget_alert",
                                  self._budget_line(task_id, monitor, ""))
            message = protocol.CONTINUE + (f" {seq_warn}" if seq_warn else "")

    # --- reporting ---------------------------------------------------------

    def _cost_usd(self, tokens):
        model = (self.config.ai or {}).get("model")
        pricing = (self.config.ai or {}).get("pricing")
        # Tokens here are total; price them as output-side for a conservative
        # upper estimate (we do not have a prompt/completion split per loop).
        return cost_mod.cost_usd(model, 0, tokens, pricing)

    def _budget_line(self, task_id, monitor, prefix):
        usd = self._cost_usd(monitor.used)
        pct = int(monitor.fraction * 100)
        tail = (f"Budget: {monitor.used:,}/{monitor.budget:,} tokens ({pct}%)"
                f"  ~${usd:.4f}")
        return f"{prefix}\n{tail}" if prefix else tail

    def _delivery(self, task_id, monitor, cp):
        run = db.get_loop_run(self.db_path, task_id)
        lines = [
            f"Loop {task_id} DONE — project '{run['project']}' ({run['project_status']}).",
            cp.summary or "(no summary)",
        ]
        if run.get("pr_url"):
            lines.append(f"PR: {run['pr_url']}")
        lines.append(self._budget_line(task_id, monitor, ""))
        lines.append(f"Steps: {run['steps_done']}")
        return "\n".join(lines)
