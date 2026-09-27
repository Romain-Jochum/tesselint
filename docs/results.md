# Results

Everything here was measured on 2026-09-26 with `typesafe/jev-1.13` through OpenRouter. The real-data figures come from one private backlog of 176 user stories in 23 epics. That backlog is not published, so this page gives aggregates only. The synthetic figures come from `tesselint synth`, and you can reproduce them (see the end).

Two numbers recur below. **o** is Jev's probability that two stories overlap at all: duplicate, containment in either direction, or partial overlap. **m** is the same without partial overlap, so it measures how mergeable a pair is.

## Cost and speed

Jev bills input tokens only, at US$0.042 per million; output is free. A pair question costs about 480 + 1.3 × (words in both stories) tokens.

| Run on the calibration backlog | Stories | Pairs | Calls, live / cached | Cost | Wall time |
|---|---|---|---|---|---|
| T1 (3 epics) | 35 | 595 | 810 / 0 | US$0.031 | 17 s |
| T2 (6 epics) | 67 | 2,211 | 1,881 / 755 | US$0.072 | 37 s |
| T3 (all epics) | 176 | 15,400 | 15,079 / 2,636 | US$0.441 | 301 s |
| T3 again, after 7 stories were edited | 176 | 15,400 | 1,335 / 16,330 | US$0.062 | 27 s |
| From stories v1 JSON, after 72 edits and 14 new stories | 184 | 16,836 | 14,013 / 5,514 | US$0.550 | 321 s |

A cold T3 run, with nothing cached, costs about US$0.54 and takes about 6 minutes: 15,400 pair questions, about 2,100 re-asks in the swapped order and 175 atomicity questions. Jev kept up about 50 calls per second at 16 requests in flight. None of these runs had a failed call or a retry, and OpenRouter never answered 429. A cold 60-story synthetic set took 1,885 calls in 38 s (p50 282 ms, p95 442 ms) for US$0.047.

Cost grows with the number of pairs, n(n−1)/2, at about US$0.035 per 1,000 pairs:

| Items | Pairs | Cost | Time at 50 calls/s |
|---|---|---|---|
| 100 | 4,950 | ≈ US$0.17 | ≈ 2 min |
| 500 | 124,750 | ≈ US$4.4 | ≈ 50 min |
| 1,000 | 499,500 | ≈ US$17.5 | ≈ 3 h |

The default spend cap, `--max-usd 1`, stops the last two before any call (exit 3). `tesselint run --dry-run` prints the estimate without calling anything. Here the estimate ran 3–21% above the measured cost.

## Synthetic data

`tesselint synth` writes 60 stories from 48 hand-written seeds, with planted answers: 8 duplicates (a paraphrase in another epic), 8 containments (a seed plus one more capability) and 6 bundles (2 or 3 seeds fused into one story). `tesselint eval RUN --truth truth.json` scores a run against them.

| Seed | Duplicates P / R | Containments P / R | Direction P / R | Bundles P / R | Pre-filter recall |
|---|---|---|---|---|---|
| 42 | 0.89 / 1.00 | 0.89 / 1.00 | 0.89 / 1.00 | 1.00 / 1.00 | 1.00, keeping 23% of pairs |
| 7 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00, keeping 23% of pairs |

The bars in `docs/DESIGN.md` §6 are recall ≥ 0.9 and precision ≥ 0.8 for merges, and recall ≥ 0.8 and precision ≥ 0.6 for bundles. Both seeds pass. The one false merge, on seed 42, joins two seeds from different epics that both end a tenancy, at m 0.50, flagged borderline. Planted pairs are easier than real ones, which is why the calibration below matters more.

## Calibration

A blind LLM labeller read only the story texts and the relation definitions. It labelled 80 pairs from the T1 epics, stratified by o, a hold-out of 80 pairs from the other epics, and the 35 T1 stories for atomicity.

| Question | Sample | Result | Bar | Verdict |
|---|---|---|---|---|
| `atomic.v2`: should the story be split? | 35 stories | precision 0.67 at 0.8, AUC 0.82 | 0.6, 0.7 | pass |
| `rel.v1`: how do two stories relate? | 80 T1 pairs | agreement 0.60, κ 0.15 | 0.75, 0.5 | fail |
| `rel.v1` | 80 hold-out pairs | agreement 0.54, κ 0.08 | 0.75, 0.5 | fail |

As a classifier, `rel.v1` fails. Jev calls overlap and containment far more often than the labeller did; the labeller read most of those pairs as one story depending on or referring to another. It called 10 of the 160 pairs related and none a merge.

As a ranking, o works. On the hold-out, the AUC of o against "related" is 0.91. Across all 160 labelled pairs:

| o band | Pairs | Labelled related |
|---|---|---|
| o < 0.5 | 83 | 0 |
| 0.5–0.7 | 37 | 1 |
| 0.7–0.9 | 36 | 6 |
| o ≥ 0.9 | 4 | 3 |

So the CLI cuts overlap clusters at o ≥ 0.7. That keeps 9 of the 10 related pairs for about half the review load of a 0.5 cut. Merges stay at m ≥ 0.5 and are marked "not trusted": with no labelled merge, their precision is unknown. Thresholds fitted on the T1 sample alone (o ≥ 0.85, m ≥ 0.6) reached κ 0.64 there but 0.09 on the hold-out, so they were dropped.

Read the proposals as a ranked review queue, strongest first, not as verdicts. The labeller is a model, and nobody has adjudicated its disagreements with Jev yet.

## Levers

| Lever | Measured | Default |
|---|---|---|
| Full text vs title and statement only (`--render core`) | Core cut tokens by 43% on T1 but proposed 10 merges instead of 2, one of them merging a story into two targets | Full text |
| One question per pair, with the direction in the options | 15,400 calls instead of 30,800 for 176 stories | On |
| Re-ask in the swapped order when o ≥ 0.2 (`--no-swap` turns it off) | 12% more calls on T3, about US$0.06. Without it: 126 merges instead of 101, 38 of them seen in one order only | On, because Jev is order-biased |
| Local pre-filter (`--prefilter`, model2vec) | Keeps 8.9% of the T3 pairs but only 45% of Jev's overlaps and 54% of its merges; synthetic recall 1.0 | Off |
| A cheap yes/no question first | 27% more tokens wherever recall stays safe | Off |
| Several pairs per request | Fewer tokens, but the answers change | One pair per request |
| Answer cache keyed by text | Unchanged re-run: 0 calls. One story edited: 229 calls, US$0.006, 5 s | On |

## Limits

- `rel.v1` is not trusted as a classifier (see Calibration), and merge precision is unknown.
- The labels come from one LLM, not from people.
- On the calibration backlog, one related pair that a person knew about is never found: Jev answers "same area but distinct" in both orders, and so does the labeller.
- Every split is borderline. Atomicity scores sit between 0.70 and 0.90, so the borderline flag does not separate them, although the ranking passed.
- Everything was measured on English user stories from one product. Other items (requirements, tickets, test cases) can go through the stories v1 JSON input, but nobody has measured how Jev does on them.
- `key_usage_delta` in `run.json` lags, because OpenRouter's usage counter catches up after the run ends. `cost_usd.live` is the reliable figure.
- The matrix view has no keyboard focus or brush zoom yet; its table view is the keyboard-accessible version.
- The tier presets (`--tier t1|t2|t3`) name the calibration backlog's epics. On your own backlog, use `--epics`.

## Reproduce

```
uv run tesselint synth --out synth --seed 42 --n 60
uv run tesselint run --source synth/stories.md --dry-run   # the estimate: no call, no key
uv run tesselint run --source synth/stories.md             # about US$0.05 cold
uv run tesselint eval runs/<run id> --truth synth/truth.json
```

To calibrate on your own backlog, `tesselint eval runs/<run id> --make-labels labels.jsonl` writes a blind sheet. Fill in the labels, then `tesselint eval runs/<run id> --labels labels.jsonl` scores Jev and writes the run's `calibration.json`, which the review UI shows in its trust banner.
