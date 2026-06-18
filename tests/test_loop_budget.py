import unittest

from lodestone.ai.budget import estimate_run, BudgetMonitor


class EstimateTests(unittest.TestCase):
    def test_estimate_math_and_fit(self):
        e = estimate_run(steps=10, avg_per_step=1500, token_budget=40000)
        self.assertEqual(e.raw_tokens, 15000)
        self.assertEqual(e.est_tokens, 30000)   # raw * 2.0 safety
        self.assertTrue(e.fits)

    def test_estimate_flags_overrun(self):
        e = estimate_run(steps=40, avg_per_step=1500, token_budget=40000)
        self.assertFalse(e.fits)
        self.assertIn("RISK", e.summary())

    def test_estimate_clamps_zero_inputs(self):
        e = estimate_run(steps=0, avg_per_step=0, token_budget=1000)
        self.assertGreaterEqual(e.est_tokens, 1)


class MonitorTests(unittest.TestCase):
    def test_tiered_thresholds_fire_once_each(self):
        m = BudgetMonitor(token_budget=1000, warn_at=0.75, constrain_at=0.90)
        signals = [m.add(100) for _ in range(12)]   # no plan -> no early projection
        self.assertIn("warn", signals)
        self.assertIn("constrain", signals)
        self.assertIn("stop", signals)
        self.assertEqual(signals.count("warn"), 1)
        self.assertEqual(signals.count("constrain"), 1)

    def test_warn_at_75_percent(self):
        m = BudgetMonitor(token_budget=1000, warn_at=0.75, constrain_at=0.90)
        self.assertIsNone(m.add(700))    # 70%
        self.assertEqual(m.add(100), "warn")   # 80%

    def test_stop_at_budget(self):
        m = BudgetMonitor(token_budget=1000)
        self.assertEqual(m.add(1000), "stop")

    def test_projected_overrun_warns_early(self):
        # Small absolute use, but burn rate projects over budget across the plan.
        m = BudgetMonitor(token_budget=10000, warn_at=0.75, constrain_at=0.90)
        # 2000 in one step, plan 10 steps -> projected 20000 > 10000.
        self.assertEqual(m.add(2000, steps_planned=10), "warn")
        self.assertLess(m.fraction, 0.75)   # confirms it was the projection, not absolute


if __name__ == "__main__":
    unittest.main()
