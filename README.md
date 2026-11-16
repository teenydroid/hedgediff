# hedgediff

`hedgediff` reports every epistemic hedge a summary dropped from its source.

Give it the source transcript and the summary written from it. It aligns the
two line by line with `difflib`, finds every hedge word or phrase in the
source (`might`, `may`, `could`, `likely`, `unverified`, `pending`,
`allegedly`, `unsure` and about 40 cousins), and reports each one the summary
no longer contains, with its source line number.

Built for auditors who summarize long transcripts and cannot afford to lose
the tentativeness: a compaction rewrite that turns "payment might arrive
friday" into "payment arrives friday" has changed the claim, and `hedgediff`
catches it.

## What it reports

For every hedge in the source, one line saying what the summary did to it:

```
$ python3 hedgediff.py source.txt compaction.txt
hedgediff: 5 of 5 hedges dropped by the summary
  source:2    allegedly        DROPPED  source line 2 absent or rewritten in the summary
      | the vendor refund of 41 units is allegedly double-counted
  source:3    unverified       DROPPED  source line 3 absent or rewritten in the summary
      | three entries remain unverified pending the bank feed
...
```

- **dropped** (default): the hedge phrase is gone from the summary entirely.
  Exit code `1` so scripts can gate on it.
- **kept**: shown with `--all`, and always included in `--json`.
- If the source has no hedges at all, it says so and exits `0`.

## Usage

```
python3 hedgediff.py SOURCE SUMMARY [--hedge WORD]... [--lexicon FILE]
                    [--all] [--json]
```

- `SOURCE`, `SUMMARY` - the two files to compare. Use `-` for one of them to
  read it from stdin.
- `--hedge WORD` - add a literal phrase to the hedge lexicon, repeatable.
- `--lexicon FILE` - extra lexicon file, one phrase per line, `#` starts a
  comment.
- `--all` - also list hedges the summary kept.
- `--json` - machine-readable output: counts plus every hit with
  `source_line`, `hedge`, `matched`, `count`, `summary_line`, `status`,
  `reason`.

Exit codes: `0` nothing dropped, `1` dropped hedges found, `2` error.

## How it decides

A hedge is **dropped** when its phrase is absent from the summary text as a
whole. Presence anywhere in the summary counts as kept: the point is that the
epistemic marker survived the rewrite, not that its line did.

The lexicon matches whole words only (`likely` never fires inside
`unlikely`), case-insensitively, and `may` before a number is treated as the
month, not the verb.

What it does **not** do: it cannot catch a hedge that was paraphrased away
without losing its marker phrase (dropping "might" but keeping "conceivably"
is invisible unless `conceivably` is in your lexicon), and it does not judge
whether a hedge *should* have survived a summary, only whether it did.

## Requirements

Python 3.9+, standard library only. No network, no dependencies.

## Tests

```
python3 -m unittest test_hedgediff -v
```

26 tests: lexicon behavior (boundaries, case, dates, custom phrases), the
line-alignment diff, the CLI (exit codes, flags, stdin, missing files), and
an end-to-end scenario reproducing the compaction rewrite that lost three
auditors' hedges.
