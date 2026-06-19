import json
import unittest

from lodestone.hub.protocol import MARKER, parse_checkpoint, frame_instruction


def env(status, seq=1, **kw):
    d = {"status": status, "seq": seq, "summary": "s", "tokens_used": 100}
    d.update(kw)
    return f"{MARKER} {json.dumps(d)}"


class ProtocolTests(unittest.TestCase):
    def test_structured_envelope_parsed(self):
        cp = parse_checkpoint(
            "prose before\n" + env("GATE_PR", 3, pr_url="https://github.com/a/b/pull/1",
                                    tokens_used=12000) + "\ntrailing prose")
        self.assertTrue(cp.structured)
        self.assertEqual(cp.status, "GATE_PR")
        self.assertEqual(cp.seq, 3)
        self.assertEqual(cp.pr_url, "https://github.com/a/b/pull/1")
        self.assertEqual(cp.tokens_used, 12000)

    def test_unknown_status_normalizes_to_milestone(self):
        self.assertEqual(parse_checkpoint(env("WAT")).status, "MILESTONE")

    def test_malformed_envelope_falls_back_to_heuristic(self):
        cp = parse_checkpoint(MARKER + " {not valid json")
        self.assertFalse(cp.structured)
        self.assertEqual(cp.status, "MILESTONE")

    def test_structured_envelope_bad_numeric_fields_do_not_crash(self):
        cp = parse_checkpoint(env("DONE", seq="oops", tokens_used="bad"))
        self.assertTrue(cp.structured)
        self.assertEqual(cp.status, "DONE")
        self.assertIsNone(cp.seq)
        self.assertEqual(cp.tokens_used, 0)

    def test_heuristic_detects_pr_url(self):
        cp = parse_checkpoint("I opened https://github.com/a/b/pull/9 please review")
        self.assertEqual(cp.status, "GATE_PR")
        self.assertEqual(cp.pr_url, "https://github.com/a/b/pull/9")
        self.assertFalse(cp.structured)

    def test_heuristic_done_error_blocked_milestone(self):
        self.assertEqual(parse_checkpoint("All finished!").status, "DONE")
        self.assertEqual(parse_checkpoint("Traceback: it failed").status, "ERROR")
        self.assertEqual(parse_checkpoint("I'm blocked, need your input").status, "BLOCKED")
        self.assertEqual(parse_checkpoint("Refactored the parser").status, "MILESTONE")

    def test_error_takes_precedence_over_done_word(self):
        # "failed to finish" should read as error, not done.
        self.assertEqual(parse_checkpoint("failed to finish the build").status, "ERROR")

    def test_heuristic_disabled_treats_freetext_as_milestone(self):
        cp = parse_checkpoint("opened https://github.com/a/b/pull/9", allow_heuristic=False)
        self.assertEqual(cp.status, "MILESTONE")
        self.assertFalse(cp.structured)

    def test_frame_instruction_marks_live_gate(self):
        live = frame_instruction("do it", "coldplay.io", "live", 40)
        dev = frame_instruction("do it", "cricap", "dev", 40)
        self.assertIn("LIVE", live)
        self.assertIn("WAIT", live)
        self.assertIn("development", dev)
        self.assertIn(MARKER, live)


if __name__ == "__main__":
    unittest.main()
