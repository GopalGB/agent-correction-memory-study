# Do persistent correction memories stop coding agents from repeating mistakes?

A seven-month single-developer field study (observational, n = 1).

Author: Gopal Bagaswar, Independent Researcher. Date: 7 November 2026.

## Contents

| Path | What it is |
|---|---|
| `paper.md` | The paper (Markdown source) |
| `paper.pdf` | The paper built with pandoc + xelatex |
| `figures/*.png` | Four figures made from aggregates only |
| `analysis/analyze.py` | Main analysis: takes the memory database and memory directory as CLI arguments, writes aggregates |
| `analysis/make_figures.py` | Builds the figures from `results/aggregates.json` |
| `analysis/test_pipeline.py` | Self-test on synthetic data (runs both scripts end to end) |
| `results/aggregates.json` | Every number in the paper (aggregates only, no text) |
| `LICENSE` | MIT for code, CC BY 4.0 for text and figures |

## Reproduce on your own data

The private data (a local SQLite observation database with `observations`, `user_prompts`, `sdk_sessions` and `file_context_injections` tables, and a directory of `feedback_*.md` memory files) are not released.

```
pip install numpy scipy matplotlib
python3 analysis/test_pipeline.py            # self-test on a synthetic DB and memory dir
python3 analysis/analyze.py --db <memory.db> --memdir <memory dir> --project "<project name>" \
    [--user-aliases "alias1,alias2"] --out results/aggregates.json [--private-dir <dir>] \
    [--labels L1.json --labels_lenient L1b.json --labels2 L2.json --catlabels C.json]
python3 analysis/make_figures.py --agg results/aggregates.json --out figures/
```

- Label arguments are optional. Without them, everything except the hand-check blocks is produced and the figures script omits the hand-check counts from Figure 3 (it prints a note).
- `--private-dir` receives the hand-check samples. They contain prompt text and memory names: never publish them. Label them yourself; a label file is a JSON list of 0/1 values in sample order, or `{"fingerprint": "<sha from aggregates sample_fingerprints>", "labels": [...]}`, which is checked against the sample.
- `--user-aliases` has no default. Without it the alias-mention proxy (a text mention, not verified authorship) is skipped.
- `--start-date` / `--end-date` (UTC) default to the first and last observation dates.
- Seeds are fixed; `aggregates.json` records SHA-256 digests of the inputs, the script, and package versions.

## AI assistance

Analysis code and drafting were done with AI coding agents (Claude) under the author's direction.
The hand-check labels were also produced by an AI assistant (single rater, not blind). See the paper, Sections 4.2 and 7.
