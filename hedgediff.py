#!/usr/bin/env python3
"""hedgediff - report every epistemic hedge a summary dropped from its source.

Reads a source transcript and a summary written from it, aligns the two line
by line with difflib, and reports every hedge word or phrase (might, may,
could, likely, unverified, pending, allegedly, unsure and cousins) that is
present in the source but absent from the summary, with the source line
number.

Standard library only, no network. Python 3.9+.

Exit codes: 0 = nothing dropped, 1 = dropped hedges found, 2 = error.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from dataclasses import dataclass

# (display name, regex fragment). Word boundaries are wrapped around every
# fragment at compile time, so "likely" never matches inside "unlikely".
DEFAULT_HEDGES = [
    ("might", r"might"),
    ("may", r"may(?!\s+\d)"),  # "May 12" is a date, not a hedge
    ("could", r"could"),
    ("cannot confirm", r"can(?:'?t|not) confirm"),
    ("likely", r"likely"),
    ("unlikely", r"unlikely"),
    ("maybe", r"maybe"),
    ("perhaps", r"perhaps"),
    ("possibly", r"possibly"),
    ("possible", r"possible"),
    ("probably", r"probably"),
    ("probable", r"probable"),
    ("apparently", r"apparently"),
    ("reportedly", r"reportedly"),
    ("allegedly", r"allegedly"),
    ("alleged", r"alleged"),
    ("rumoured", r"rumoured"),
    ("rumored", r"rumored"),
    ("presumably", r"presumably"),
    ("seems", r"seems?"),
    ("seemed", r"seemed"),
    ("appear to", r"appear(?:s|ed)? to"),
    ("looks like", r"look(?:s|ed)? like"),
    ("unclear", r"unclear"),
    ("uncertain", r"uncertain(?:ty)?"),
    ("unverified", r"unverified"),
    ("unconfirmed", r"unconfirmed"),
    ("uncorroborated", r"uncorroborated"),
    ("unresolved", r"unresolved"),
    ("inconclusive", r"inconclusive"),
    ("tentative", r"tentative"),
    ("provisional", r"provisional"),
    ("preliminary", r"preliminary"),
    ("pending", r"pending"),
    ("unsure", r"unsure"),
    ("not sure", r"not sure"),
    ("suspect", r"suspect(?:s|ed)?"),
    ("questionable", r"questionable"),
    ("dubious", r"dubious"),
    ("tbd", r"tbd"),
    ("to be confirmed", r"to be confirmed"),
    ("assuming", r"assuming"),
    ("i think", r"\bi think\b"),
]


@dataclass
class HedgeHit:
    """A hedge found on one line of the source."""

    lineno: int
    hedge: str
    matched: str
    source_text: str
    count: int


@dataclass
class HedgeVerdict:
    """What the summary did to one source hedge."""

    lineno: int
    hedge: str
    matched: str
    source_text: str
    count: int
    summary_line: "int | None"
    status: str  # "dropped" or "kept"
    reason: str

    def row(self) -> dict:
        return {
            "source_line": self.lineno,
            "hedge": self.hedge,
            "matched": self.matched,
            "source_text": self.source_text,
            "count": self.count,
            "summary_line": self.summary_line,
            "status": self.status,
            "reason": self.reason,
        }


def compile_lexicon(extra_words=(), lexicon_file=None):
    """Return [(name, compiled_pattern)] for the default lexicon plus extras.

    extra_words: literal phrases added from the command line.
    lexicon_file: path to a file with one phrase per line, # starts a comment.
    """
    patterns = [
        (name, re.compile(r"\b(?:" + frag + r")\b", re.IGNORECASE))
        for name, frag in DEFAULT_HEDGES
    ]
    for word in extra_words:
        word = word.strip()
        if word:
            patterns.append(
                (word.lower(), re.compile(r"\b" + re.escape(word) + r"\b", re.IGNORECASE))
            )
    if lexicon_file:
        with open(lexicon_file, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                phrase = line.strip()
                if not phrase or phrase.startswith("#"):
                    continue
                patterns.append(
                    (phrase.lower(), re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE))
                )
    return patterns


def find_hedges(text, patterns):
    """Every hedge hit in text, in order: (line, hedge, first match, line text).

    One hit per distinct hedge per line; repeated uses of the same hedge on a
    line are folded into count.
    """
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        seen = {}
        for name, pattern in patterns:
            matches = list(pattern.finditer(line))
            if not matches:
                continue
            name = name.lower()
            if name in seen:
                continue
            seen[name] = HedgeHit(lineno, name, matches[0].group(0), line, len(matches))
        hits.extend(seen.values())
    return hits


def _norm(line):
    """Normalized form used only for alignment: lowercase, collapsed spaces."""
    return " ".join(line.lower().split())


def align_counterparts(source_lines, summary_lines):
    """Map source line index -> summary line index for lines that survived.

    Uses difflib.SequenceMatcher over whitespace-normalized lines, so a
    rewrite that only recapitalized or re-spaced a line still lines up.
    Heavy rephrasing does not match: those lines report as rewritten.
    """
    matcher = difflib.SequenceMatcher(
        None,
        [_norm(line) for line in source_lines],
        [_norm(line) for line in summary_lines],
        autojunk=False,
    )
    counterparts = {}
    for i, j, size in matcher.get_matching_blocks():
        for offset in range(size):
            counterparts[i + offset] = j + offset
    return counterparts


def diff_hedges(source_text, summary_text, patterns):
    """Every hedge in the source, with what the summary did to it.

    A hedge is "dropped" when it is present in the source and absent from the
    summary as a whole (a compaction rewrite that lost it). If the phrase
    survives anywhere in the summary the hedge is "kept", even when its own
    line was rewritten elsewhere.
    """
    source_lines = source_text.splitlines()
    summary_lines = summary_text.splitlines()
    counterparts = align_counterparts(source_lines, summary_lines)
    by_name = dict(patterns)

    verdicts = []
    for hit in find_hedges(source_text, patterns):
        pattern = by_name[hit.hedge]
        summary_line = counterparts.get(hit.lineno - 1)
        survives = pattern.search(summary_text) is not None
        if survives:
            if summary_line is not None:
                reason = "hedge appears on summary line %d" % (summary_line + 1)
            else:
                reason = "hedge survives elsewhere in the summary"
            status = "kept"
        else:
            if summary_line is not None:
                reason = "summary line %d kept the line but lost the hedge" % (summary_line + 1)
            else:
                reason = "source line %d absent or rewritten in the summary" % hit.lineno
            status = "dropped"
        verdicts.append(HedgeVerdict(
            hit.lineno, hit.hedge, hit.matched, hit.source_text, hit.count,
            None if summary_line is None else summary_line + 1,
            status, reason,
        ))
    return verdicts


def read_input(path):
    """Read a file, or stdin when the path is '-'."""
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="hedgediff",
        description="Report every epistemic hedge a summary dropped from its source.",
        epilog="Exit codes: 0 nothing dropped, 1 dropped hedges found, 2 error. "
               "Use '-' for exactly one of the two paths to read it from stdin.",
    )
    parser.add_argument("source", help="source transcript (the text that had the hedges)")
    parser.add_argument("summary", help="summary written from the source")
    parser.add_argument(
        "--hedge", action="append", default=[], metavar="WORD",
        help="add a literal phrase to the hedge lexicon (repeatable)",
    )
    parser.add_argument(
        "--lexicon", metavar="FILE",
        help="extra lexicon file: one phrase per line, # starts a comment",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="also list hedges the summary kept",
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="machine-readable JSON output (includes kept hedges)",
    )
    args = parser.parse_args(argv)

    try:
        source_text = read_input(args.source)
        summary_text = read_input(args.summary)
    except OSError as exc:
        print("hedgediff: %s" % exc, file=sys.stderr)
        return 2
    try:
        patterns = compile_lexicon(args.hedge, args.lexicon)
    except OSError as exc:
        print("hedgediff: %s" % exc, file=sys.stderr)
        return 2

    verdicts = diff_hedges(source_text, summary_text, patterns)
    dropped = [v for v in verdicts if v.status == "dropped"]
    kept = [v for v in verdicts if v.status == "kept"]

    if args.as_json:
        print(json.dumps({
            "counts": {
                "hits": len(verdicts),
                "dropped": len(dropped),
                "kept": len(kept),
            },
            "hedges": [v.row() for v in verdicts],
        }, indent=2))
    else:
        if not verdicts:
            print("hedgediff: no hedges found in the source")
        else:
            print("hedgediff: %d of %d hedges dropped by the summary" % (len(dropped), len(verdicts)))
            for v in verdicts:
                if v.status == "dropped" or args.all:
                    tag = "DROPPED" if v.status == "dropped" else "kept"
                    print("  source:%-4d %-16s %-8s %s" % (v.lineno, v.hedge, tag, v.reason))
                    print("      | %s" % v.source_text.strip())

    return 1 if dropped else 0


if __name__ == "__main__":
    sys.exit(main())
