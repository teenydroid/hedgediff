#!/usr/bin/env python3
"""Tests for hedgediff. Run with: python3 -m unittest test_hedgediff -v"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hedgediff

HERE = os.path.dirname(os.path.abspath(__file__))


def run_cli(*args):
    proc = subprocess.run(
        [sys.executable, os.path.join(HERE, "hedgediff.py")] + list(args),
        capture_output=True, text=True,
    )
    return proc


class LexiconTests(unittest.TestCase):
    def test_default_lexicon_compiles(self):
        patterns = hedgediff.compile_lexicon()
        self.assertGreaterEqual(len(patterns), 30)
        names = [name for name, _ in patterns]
        for required in ("might", "may", "could", "likely", "unverified", "pending", "allegedly", "unsure"):
            self.assertIn(required, names)

    def test_word_boundaries(self):
        patterns = hedgediff.compile_lexicon()
        by_name = dict(patterns)
        # none of these lines contains a hedge: correctly/mayflower must not
        # trip may/could substrings, and only a standalone unlikely is a hedge
        hits = hedgediff.find_hedges("the runner was sloppy but correct, mayflower sailed", patterns)
        self.assertEqual(hits, [])
        self.assertIsNone(by_name["likely"].search("unlikely"))
        self.assertIsNotNone(by_name["unlikely"].search("unlikely"))

    def test_may_not_a_date(self):
        by_name = dict(hedgediff.compile_lexicon())
        self.assertIsNone(by_name["may"].search("May 12 report"))
        self.assertIsNotNone(by_name["may"].search("this may explain"))

    def test_extra_words_and_lexicon_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write("# comment\ni cannot vouch\n\non hold\n")
            path = handle.name
        try:
            patterns = hedgediff.compile_lexicon(["vibes", "maybe"], path)
            by_name = dict(patterns)
            self.assertIn("vibes", by_name)
            self.assertIn("i cannot vouch", by_name)
            self.assertIn("on hold", by_name)
            maybe_entries = [p for name, p in patterns if name == "maybe"]
            self.assertEqual(len(maybe_entries), 2)  # default kept, extra is a no-op twin
            self.assertIsNotNone(maybe_entries[-1].search("MAYBE"))
            hits = hedgediff.find_hedges("status on hold, vibes pending", patterns)
            names = {h.hedge for h in hits}
            self.assertIn("on hold", names)
            self.assertIn("vibes", names)
            self.assertIn("pending", names)
        finally:
            os.unlink(path)

    def test_case_insensitive(self):
        hits = hedgediff.find_hedges("MIGHT be, PerHaps not", hedgediff.compile_lexicon())
        names = {h.hedge for h in hits}
        self.assertEqual(names, {"might", "perhaps"})

    def test_count_folds_repeats_on_one_line(self):
        hits = hedgediff.find_hedges("might be, might not be", hedgediff.compile_lexicon())
        self.assertEqual([(h.hedge, h.count) for h in hits], [("might", 2)])


class DiffTests(unittest.TestCase):
    def test_dropped_hedges_reported(self):
        source = (
            "vendor claims the batch is clean\n"
            "payment might arrive friday\n"
            "invoice unverified, flagged by bookkeeper\n"
        )
        summary = (
            "vendor claims the batch is clean\n"
            "payment arrives friday\n"
            "invoice flagged by bookkeeper\n"
        )
        verdicts = hedgediff.diff_hedges(source, summary, hedgediff.compile_lexicon())
        dropped = [v for v in verdicts if v.status == "dropped"]
        self.assertEqual(
            {(v.hedge, v.lineno) for v in dropped},
            {("might", 2), ("unverified", 3)},
        )
        for v in dropped:
            self.assertEqual(v.reason, "source line %d absent or rewritten in the summary" % v.lineno)

    def test_dropped_line_reports_its_hedge(self):
        source = "the fee is unclear\nthe fee is 3 units\n"
        summary = "the fee is 3 units\n"
        verdicts = hedgediff.diff_hedges(source, summary, hedgediff.compile_lexicon())
        dropped = [v for v in verdicts if v.status == "dropped"]
        self.assertEqual([(v.hedge, v.lineno) for v in dropped], [("unclear", 1)])
        self.assertIn("absent or rewritten", dropped[0].reason)
        self.assertIsNone(dropped[0].summary_line)

    def test_kept_hedge_not_reported_as_dropped(self):
        source = "delivery may slip\ndelivery may also be fine\n"
        summary = "delivery may slip\n"
        verdicts = hedgediff.diff_hedges(source, summary, hedgediff.compile_lexicon())
        dropped = [v for v in verdicts if v.status == "dropped"]
        self.assertEqual(dropped, [])

    def test_rewritten_line_without_hedge_is_dropped(self):
        source = "the transfer is pending confirmation\nall else settled\n"
        summary = "transfer still awaiting settlement\nall else settled\n"
        verdicts = hedgediff.diff_hedges(source, summary, hedgediff.compile_lexicon())
        dropped = [v for v in verdicts if v.status == "dropped"]
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0].hedge, "pending")
        self.assertIn("absent or rewritten", dropped[0].reason)

    def test_no_hedges_no_findings(self):
        verdicts = hedgediff.diff_hedges("a b c\n1 2 3\n", "a b c\n", hedgediff.compile_lexicon())
        self.assertEqual(verdicts, [])

    def test_identical_texts_drop_nothing(self):
        source = "it might rain\nit may snow\n"
        verdicts = hedgediff.diff_hedges(source, source, hedgediff.compile_lexicon())
        self.assertEqual([v for v in verdicts if v.status == "dropped"], [])

    def test_summary_paraphrase_dropping_attribution(self):
        # summary paraphrases the line but drops the hedge phrase entirely
        source = "the count is allegedly inflated\nthe count is 41\n"
        summary = "someone says the count is inflated\nthe count is 41\n"
        verdicts = hedgediff.diff_hedges(source, summary, hedgediff.compile_lexicon())
        dropped = [v for v in verdicts if v.status == "dropped"]
        self.assertEqual([(v.hedge, v.lineno) for v in dropped], [("allegedly", 1)])

    def test_verdict_row_shape(self):
        source = "maybe tonight\n"
        summary = "tonight\n"
        verdicts = hedgediff.diff_hedges(source, summary, hedgediff.compile_lexicon())
        row = verdicts[0].row()
        self.assertEqual(row["source_line"], 1)
        self.assertEqual(row["hedge"], "maybe")
        self.assertEqual(row["matched"], "maybe")
        self.assertEqual(row["status"], "dropped")
        self.assertIn("source_text", row)
        self.assertIn("reason", row)


class CliTests(unittest.TestCase):
    def test_exit_1_and_text_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "source.txt")
            summ = os.path.join(tmp, "summary.txt")
            with open(src, "w") as handle:
                handle.write("fee is unverified\nfee is 3\n")
            with open(summ, "w") as handle:
                handle.write("fee is 3\n")
            proc = run_cli(src, summ)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("1 of 1 hedges dropped", proc.stdout)
            self.assertIn("source:1", proc.stdout)
            self.assertIn("unverified", proc.stdout)

    def test_exit_0_when_nothing_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "source.txt")
            with open(src, "w") as handle:
                handle.write("it might rain\nit will pour\nit may snow\n")
            proc = run_cli(src, src)
            self.assertEqual(proc.returncode, 0)
            self.assertIn("0 of 2 hedges dropped", proc.stdout)

    def test_json_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "source.txt")
            summ = os.path.join(tmp, "summary.txt")
            with open(src, "w") as handle:
                handle.write("allegedly stolen\nmight be fine\n")
            with open(summ, "w") as handle:
                handle.write("stolen\nbe fine\n")
            proc = run_cli("--json", src, summ)
            self.assertEqual(proc.returncode, 1)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["counts"]["hits"], 2)
            self.assertEqual(payload["counts"]["dropped"], 2)
            hedges = {row["hedge"] for row in payload["hedges"]}
            self.assertEqual(hedges, {"allegedly", "might"})

    def test_all_flag_lists_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "source.txt")
            summ = os.path.join(tmp, "summary.txt")
            with open(src, "w") as handle:
                handle.write("might rain\nlikely snow\n")
            with open(summ, "w") as handle:
                handle.write("might rain\nsnow\n")
            proc = run_cli("--all", src, summ)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("DROPPED", proc.stdout)
            self.assertIn("kept", proc.stdout)
            self.assertIn("likely", proc.stdout)

    def test_hedge_flag_adds_word(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "source.txt")
            summ = os.path.join(tmp, "summary.txt")
            with open(src, "w") as handle:
                handle.write("shipment on hold\nshipment ships\n")
            with open(summ, "w") as handle:
                handle.write("shipment ships\n")
            proc = run_cli("--hedge", "on hold", src, summ)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("on hold", proc.stdout)
            # without the flag, no default hedge exists in either file
            proc = run_cli(src, summ)
            self.assertEqual(proc.returncode, 0)
            self.assertIn("no hedges found", proc.stdout)

    def test_lexicon_flag_adds_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "source.txt")
            summ = os.path.join(tmp, "summary.txt")
            lex = os.path.join(tmp, "extra.txt")
            with open(src, "w") as handle:
                handle.write("check is on hold\ncheck done\n")
            with open(summ, "w") as handle:
                handle.write("check done\n")
            with open(lex, "w") as handle:
                handle.write("# audit phrases\non hold\n")
            proc = run_cli("--lexicon", lex, src, summ)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("on hold", proc.stdout)

    def test_stdin(self):
        proc = run_cli("-", os.path.join(HERE, "hedgediff.py"))
        self.assertEqual(proc.returncode, 0)  # this file has no default hedges
        self.assertIn("no hedges found", proc.stdout)

    def test_missing_file_exit_2(self):
        proc = run_cli("/nonexistent/source.txt", "/nonexistent/summary.txt")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("hedgediff:", proc.stderr)

    def test_main_function_direct(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "s.txt")
            summ = os.path.join(tmp, "t.txt")
            with open(src, "w") as handle:
                handle.write("pending review\n")
            with open(summ, "w") as handle:
                handle.write("reviewed\n")
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = hedgediff.main([src, summ])
            self.assertEqual(code, 1)
            self.assertIn("pending", buffer.getvalue())

    def test_no_hedges_at_all_is_friendly_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "source.txt")
            summ = os.path.join(tmp, "summary.txt")
            with open(src, "w") as handle:
                handle.write("42\n")
            with open(summ, "w") as handle:
                handle.write("42\n")
            proc = run_cli(src, summ)
            self.assertEqual(proc.returncode, 0)
            self.assertIn("no hedges found", proc.stdout)


class MoltbookScenarioTests(unittest.TestCase):
    """The scenario from the request: a compaction rewrite that silently lost hedges."""

    SOURCE = """diviner note 40b3bd2b: audit of ledger week 45
the vendor refund of 41 units is allegedly double-counted
three entries remain unverified pending the bank feed
lightningzero flags the fee schedule as unclear
bookkeeper: totals might drift once the feed lands
end of note
"""

    COMPACTION = """diviner note 40b3bd2b: audit of ledger week 45
vendor refund of 41 units double-counted
three entries remain for the bank feed
lightningzero flags the fee schedule
bookkeeper: totals drift once the feed lands
end of note
"""

    def test_all_three_auditor_hedges_recovered(self):
        verdicts = hedgediff.diff_hedges(self.SOURCE, self.COMPACTION, hedgediff.compile_lexicon())
        dropped = {(v.hedge, v.lineno) for v in verdicts if v.status == "dropped"}
        self.assertIn(("allegedly", 2), dropped)
        self.assertIn(("unverified", 3), dropped)
        self.assertIn(("pending", 3), dropped)
        self.assertIn(("unclear", 4), dropped)
        self.assertIn(("might", 5), dropped)
        self.assertEqual(len(dropped), 5)

    def test_json_counts_for_auditor_tooling(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "source.txt")
            summ = os.path.join(tmp, "summary.txt")
            with open(src, "w") as handle:
                handle.write(self.SOURCE)
            with open(summ, "w") as handle:
                handle.write(self.COMPACTION)
            proc = run_cli("--json", src, summ)
        self.assertEqual(proc.returncode, 1)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["counts"], {"hits": 5, "dropped": 5, "kept": 0})
        lines = sorted(row["source_line"] for row in payload["hedges"])
        self.assertEqual(lines, [2, 3, 3, 4, 5])


if __name__ == "__main__":
    unittest.main()
