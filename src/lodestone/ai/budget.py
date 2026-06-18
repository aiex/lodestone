"""Token-budget estimation and live monitoring for the Agent Loop.

Two jobs, matching what you asked for:

1. ESTIMATE BEFORE WORK. Before a loop starts, produce an estimated total token
   cost and compare it to the agent's remaining budget, so you can confirm with
   eyes open. The estimate is deliberately crude — step count × historical
   average tokens-per-call × a safety band. A perfect estimator is neither
   achievable nor necessary: the runtime counter + hard cap below is what
   actually prevents an overrun.

2. MONITOR DURING WORK. A running counter compares consumed vs. budget and emits
   tiered signals — warn (75%), constrain (90%), stop (100%) — and a
   *projected*-overrun alert that fires before you are actually over, using the
   observed burn rate. When it fires, the supervisor notifies you with an
   updated estimate.

Cost in USD is layered on via ai/cost.py at report time; this module deals in
tokens, which are exact.
"""

from dataclasses import dataclass

DEFAULT_SAFETY = 2.0  # historical-average estimators land near ~2x; budget for it.


@dataclass
class Estimate:
    steps: int
    avg_per_step: int
    raw_tokens: int          # steps * avg_per_step
    safety: float
    est_tokens: int          # raw * safety, rounded
    remaining: int           # agent's remaining budget (token_budget)
    fits: bool               # est_tokens <= remaining

    def summary(self) -> str:
        verdict = "fits" if self.fits else "RISK: exceeds remaining budget"
        return (
            f"~{self.est_tokens:,} tokens estimated "
            f"({self.steps} steps x ~{self.avg_per_step:,}/step x {self.safety:g} safety); "
            f"budget {self.remaining:,} -> {verdict}."
        )


def estimate_run(steps: int, avg_per_step: int, token_budget: int,
                 safety: float = DEFAULT_SAFETY) -> Estimate:
    steps = max(int(steps), 1)
    avg_per_step = max(int(avg_per_step), 1)
    raw = steps * avg_per_step
    est = int(round(raw * safety))
    return Estimate(
        steps=steps, avg_per_step=avg_per_step, raw_tokens=raw,
        safety=safety, est_tokens=est, remaining=int(token_budget),
        fits=est <= int(token_budget),
    )


class BudgetMonitor:
    """Tracks running token consumption against a per-run cap.

    Each ``add`` returns a signal string or None:
      - None          : nothing to report
      - 'warn'        : crossed warn_at (first time), or projected to overrun
      - 'constrain'   : crossed constrain_at (first time)
      - 'stop'        : reached/exceeded the cap — the loop must halt

    Thresholds fire once each (edge-triggered) so the supervisor does not spam
    you. The projected-overrun check uses burn-per-step to warn early even when
    absolute consumption is still under warn_at.
    """

    def __init__(self, token_budget: int, warn_at: float = 0.75,
                 constrain_at: float = 0.90, used: int = 0, steps: int = 0):
        self.budget = max(int(token_budget), 1)
        self.warn_at = warn_at
        self.constrain_at = constrain_at
        self.used = max(int(used), 0)
        self.steps = max(int(steps), 0)
        # Edge-triggered flags must reflect already-consumed budget so a resumed
        # run does not re-fire an alert it already sent on a prior leg.
        self._warned = self.fraction >= warn_at
        self._constrained = self.fraction >= constrain_at

    @property
    def fraction(self) -> float:
        return self.used / self.budget

    def projected_total(self, steps_planned: int) -> int:
        """Linear projection: current burn-per-step x planned steps."""
        if self.steps <= 0:
            return self.used
        per_step = self.used / self.steps
        return int(per_step * max(steps_planned, self.steps))

    def add(self, tokens: int, steps_planned: int = None):
        self.used += max(int(tokens or 0), 0)
        self.steps += 1
        if self.used >= self.budget:
            return "stop"
        if not self._constrained and self.fraction >= self.constrain_at:
            self._constrained = True
            return "constrain"
        if not self._warned:
            over_projected = (
                steps_planned is not None
                and self.projected_total(steps_planned) > self.budget
            )
            if self.fraction >= self.warn_at or over_projected:
                self._warned = True
                return "warn"
        return None
