# DESIGN — TesseLint

Status: accepted, 2026-09-26; binding. Evidence: probes of Jev and of a local embedding pre-filter on one private 176-story backlog, whose aggregates are in `docs/results.md`. Probe spend: US$0.130. US$0.037 of that bought answers lost in two crashed runs, which is why answers persist on arrival.

Notation. T1' = the redefined T1, E1+E2+E20 (35 stories, 595 pairs). Full text = title + core + sub-bullets. o = P(duplicate)+P(A_contains_B)+P(B_contains_A)+P(partial_overlap). m = o − P(partial_overlap).

## 1. Candidate solutions ranked (ADR-0001)

Context: 15,400 pairs at US$0.042 per million input tokens, so recall and trust outweigh money. On T1': 16 in flight gave 53–58 calls/s with 0 errors; a repeated request kept its argmax on 573/595 pairs; a swapped one kept its mirrored argmax on only 80/120.

| # | Option | Quality | Dev | Speed T3 | Reliability | Cost T3 |
|---|---|---|---|---|---|---|
| **1** | **Relation `choice` on all pairs + gated swap; atomicity `noul`; average linkage; rules** | Best: every pair, bias averaged | ~2 d | ≈ 7 min | High | ≈ US$0.63 |
| 2 | Option 1 on a local embedding shortlist | Misses 36% of Jev's overlaps | +0.5 d | ≈ 1 min | Embedding-bound | ≈ US$0.06 |
| 3 | Cascade, `related` noul first | Safe recall costs +27% tokens | +0.5 d | ≈ 8 min | Two thresholds | Higher |
| 4 | `score` matrix only | No direction or containment | −0.5 d | ≈ 7 min | Fine | ≈ US$0.40 |
| 5 | Asymmetric nouls, 30,800 ordered pairs | No partial/duplicate | same | ≈ 12 min | Same bias | ≈ US$0.85 |
| 6 | Embeddings only | Cannot type relations | −1 d | 0.2 s | Deterministic | 0 |

Decision: option 1. Rejected: 2 drops a third of the overlaps to save US$0.57 (kept as a flag); 3 costs more wherever its recall is safe; 4 and 6 cannot say "merge X into Y"; 5 doubles the calls.

## 2. Design decisions

### 2.1 Question set

Frozen: a rewording is a new version and cache key.

- `rel.v1`, per pair, `choice`, the measured wording. Instructions: `How does user story A relate to user story B, in terms of the capability each asks for?` Options, in this order:
  - `duplicate` "A and B ask for the same capability; either could replace the other."
  - `A_contains_B` "Everything B asks for is part of A, and A asks for more."
  - `B_contains_A` "Everything A asks for is part of B, and B asks for more."
  - `partial_overlap` "A and B share part of a capability, and each also asks for something the other does not."
  - `same_area_distinct` "A and B are in the same feature area but ask for different capabilities."
  - `unrelated` "A and B are about different feature areas."
- `atomic.v2`, per story, `noul`: `This user story should be split: it asks for two or more separate capabilities, each of which a team could build, ship and test on its own. Acceptance criteria, examples, rules, constraints and edge cases of a single capability do not count as separate capabilities.`
- Dropped: `sim` (r = 0.86 with o; §3), the `related` noul (§2.3) and `atomic.v1`. v1 put 164/176 stories at ≥ 0.5; v2 puts 12 at ≥ 0.8, and its AUC on 20 weak positives is 0.69 against 0.64.
- Rendering: state `{"A": text, "B": text}` for a pair, the text for a story. Text = `clean(title + ". " + core)` plus one `- ` line per sub-bullet (flattened; table rows joined with `; `).
  - `clean` strips `**`, backticks, `_`, `[...]`, story references (`US-x.y`, `(US-1.19, 7.1)`), `§x`, `(G<n>)`, `(P<n>)` and extra whitespace.
  - An empty core takes the first sub-bullet. No truncation: the longest story is 736 words, inside the 32k context.
- Why full text: title+core finds only 33 of the 83 overlaps on T1'. One known related pair falls from o 0.45 to 0.06.
- Forward order: A is the story with the smaller text sha256, which stays stable when stories move. Identical texts get a local `duplicate` = 1.0, with no call.

### 2.2 Full matrix vs shortlist

Full matrix by default; `--prefilter` (k=10 ∪ top 2%) is opt-in. Every run still computes it locally (0.2 s), logs `prefilter_kept` per pair, and reports its recall against Jev's positives (o ≥ 0.5, m ≥ 0.5). On T1' that recall is 0.64 on overlaps and 1/1 on merges, with both known pairs kept. The default flips only if planted recall and T3 recall both reach 1.0.

### 2.3 Cascade: no

A noul costs 389 tokens against 648 for the choice. Keeping 0.98 of positives passes 67% of pairs (noul ≥ 0.6), which is +27% tokens per pair. The threshold that saves tokens (0.8) keeps only 0.69.

Only the swap gate stays: pairs with forward o ≥ 0.2 (32.6% of T1') are re-asked reversed. Mirrored o moved 0.12 on average and at most 0.43, so a pair below the gate cannot average past 0.5.

### 2.4 Per-story atomicity

176 calls × ≈ 437 tokens, US$0.003. Stories under 8 words get no call and no split, because bare titles score high (one title-only story scored 0.89).

### 2.5 Matrix to proposals

- **Verdict:** the forward probabilities, averaged with the mirrored swap if present, expressed in document order. `matrix.csv` holds o.
- **Clusters:** average linkage (UPGMA, `scipy.cluster.hierarchy`) on 1 − o, cut at o ≥ 0.5; pairs never asked count as 0. T1': 9 clusters (largest 6), with the known related pair together. Connected components at 0.7 chained 23 stories into one cluster, so they are rejected.
- **merge:** m ≥ 0.5. The contained story merges into the container; for a duplicate (the largest merge probability), the later story merges into the earlier. T1': two merges, at m 0.53 and 0.64.
- **overlap:** a cluster of 2 or more stories that no single merge covers; the reviewer merges them or moves the shared part. Partial overlap dominates (82 T1' pairs against 4 containments).
- **split:** atomic ≥ 0.80, or ≥ 0.70 with o ≥ 0.5 edges into 2 or more other clusters. Qualified tags are evidence only.
- **leave:** everything else, listed by epic.
- **borderline:** the deciding value is within 0.10 of its threshold (repeat drift p95 is 0.09).
- **Evidence:** IDs, titles, top-2 probabilities per order, o, m, atomicity, cluster, pre-filter cosine, and "also related" (o ≥ 0.4, where one known related pair lands).

### 2.6 Incremental cache

- **Key:** sha256 of canonical JSON `{v: 1, question_id, question_hash, model_requested, text_a, text_b}`; `text_b` is null for stories.
- **Storage:** `.cache/tesselint/answers.jsonl`, append-only, flushed per line, fsync every 50 lines and at exit. On load the last write wins and a torn tail is skipped.
- **Invalidation:** by content only. Entries record `model_resolved` (e.g. `typesafe/jev-1.13-20260917`); two resolved models in one run set `mixed_models`. `--refresh` re-asks everything.
- **Re-run cost:** unchanged, 0 calls. One edited story: ≤ 175 forward, ≈ 58 swap and 1 atomic call (≈ US$0.007).

### 2.7 Workers

- **Client:** asyncio with one `httpx.AsyncClient`, `POST https://openrouter.ai/api/alpha/decisions`.
- **Concurrency:** 16, via a semaphore and the connection limit. At 32 in flight: 20.7 calls/s, p95 3.3 s, 8 connect errors.
- **Rate limit:** an `aiolimiter.AsyncLimiter` token bucket, 50 requests/s.
- **Retries:** on 429, 408, 5xx, a 402 with `in_flight`, `httpx.TransportError`, or a 200 lacking `usage.cost` or `answers`. Delay `uniform(0, min(30, 0.5·2^attempt))`, at least `Retry-After`; 6 attempts, then the job fails uncached. Other 4xx are fatal. Hand-written (injectable `sleep`, `rng`).
- **Checkpoint:** the cache itself; `run --resume RUN_DIR` reuses that run's snapshot and pays only for missing answers.
- **Cost:** the sum of `usage.cost`, plus `GET /api/v1/key` usage before and after.
- **Spend cap** (`--max-usd`, default 1.0): an estimate above it exits 3 before any call. Jobs reserve their estimate; at the cap, dispatch stops, in-flight calls drain, partial outputs are written, and the run exits 3.
- **Aborts:** over 2% failed or 20 consecutive failures exits 6; SIGINT exits 130; both write partial outputs.

## 3. Lever table

Token model (T1', full text): `rel.v1` = 483 + 1.29 × (words A + words B); `atomic.v2` = 330 + 1.3 × words.

| Lever | Measured now | Still to measure on T1' | Decision |
|---|---|---|---|
| Billing | Input tokens only | — | Minimise input tokens |
| Cascade | +27% tokens at recall 0.98 | — | Off |
| One call, direction in options | Mirrored argmax 80/120 | Swap share, verdict stability | Keep; swap if o ≥ 0.2 (×1.33) |
| Local pre-filter | Keeps 9.0%; known pairs kept; Jev recall 0.64 | Planted recall; proposals on/off | Off; flag; recall logged |
| Linear atomicity | 176 calls, US$0.003 | Precision at 0.8 | On |
| Short inputs | 648 vs 1,004 tokens; core finds 40% | `--render core` | Full text |
| Batching | Questions per request −53%; pairs per request −31–54% but answers flip | — | One pair per request (one question now) |
| Drop `sim` | −68 tokens; r = 0.86 | Choice-only tokens | Dropped |
| Incremental cache | 0 unchanged; ≈ 234 per edit (design) | Both re-runs | On |
| Measure first | T3 ≤ US$0.63, ≈ 7 min | Each flag on/off | `--prefilter`, `--no-swap`, `--render` |

## 4. Tiers

- **T1 = E1+E2+E20:** 35 stories, 595 pairs, ≈ US$0.03. E20 is in because the known related pair sits there.
- **T2 = T1 + E3, E4, E23:** 67 stories, 2,211 pairs, ≈ US$0.10. It adds the epics where other overlaps were suspected, plus E23's restatements.
- **T3:** all 176 stories.

The tiers are presets for that calibration backlog; on another backlog use `--epics`. Projections use the token model on real word counts (T1: 175 words per story; whole document: 91).

## 5. Calibration

- **Synthetic first** (§6).
- **Real sample:** `eval RUN --make-labels F` writes a blind sheet: 80 T1 pairs stratified by forward o (20 each in [0,.2), [.2,.5), [.5,.7), [.7,1]), every T1 proposal, and the 35 T1 stories.
- **Labeller:** a blind LLM labeller; the reviewer adjudicates only its disagreements with Jev (≈ 20 rows, ≈ 10 min). `eval RUN --labels F` scores.
- **Pass bars:** `rel.v1` (merge/overlap/none): agreement ≥ 0.75, κ ≥ 0.5, merge recall ≥ 0.8. `atomic.v2`: precision ≥ 0.6 at 0.8, AUC ≥ 0.7.
- **On failure:** a "not trusted" banner on its proposals.

## 6. Synthetic data

- **Seeds:** `src/tesselint/data/seeds.toml` (`tomllib`), about 48 hand-written atomic stories in 8 fictional epics.
  - The style of the real backlog ("I …, so that …", tags, 0–4 sub-bullets, core ≈ 26 words), never copied.
  - ≥ 16 seeds carry a hand-written `paraphrase` and ≥ 16 an `addon` (an extra same-area capability); templates alone would make duplicates lexically trivial.
- **Planting** (`random.Random(seed)`): a duplicate puts the paraphrase in another epic; a containment is the seed's core + its addon + both sets of sub-bullets; a bundle fuses 2–3 seeds from different epics and removes the originals. Parser variants: a tombstone, a qualified tag, an empty core, a misplaced ID.
- **Default size:** 60 stories (1,770 pairs, ≈ 40 s, ≈ US$0.07 cold): 8 duplicates, 8 containments, 6 bundles. Same seed, same bytes.

```json
{"generator":"tesselint-synth/1","seed":42,"n":60,"seeds_sha256":"…",
 "duplicates":[["US-3.2","US-7.4"]],
 "containments":[{"container":"US-5.1","contained":"US-2.3"}],
 "bundled":[{"id":"US-6.2","parts":["S14","S31"]}]}
```

- **Metrics** (`eval RUN --truth F`): a planted pair is a true positive when a merge proposal names it (containment direction scored separately), and any other merge is a false positive. A bundle is a true positive when a split names it. Jev-level recall (o ≥ 0.5) and pre-filter recall are reported too.
- **Pass bars:** duplicates and containments recall ≥ 0.9, precision ≥ 0.8; bundles recall ≥ 0.8, precision ≥ 0.6; the pre-filter keeps 100%.

## 7. Architecture

Modules in `src/tesselint/`, one per job: `cli`, `config` (settings, `TIERS`, env), `models` (shared types), `parse`, `render`, `questions`, `jev` (HTTP), `cache`, `workers` (scheduler, bucket, retry, ledger), `prefilter`, `verdicts` (forward/swap, matrix), `cluster`, `proposals`, `outputs`, `pipeline` (run, report), `synth`, `evaluate`, and `data/seeds.toml`.

Flow: snapshot → parse → tier → render → estimate → forward + atomic jobs → swap jobs → verdicts → clusters → proposals → outputs.

CLI (argparse; `--help` on every command):

- `tesselint parse [--source P] [--epics E1,E2] [--json]`: prints counts and unparsed `US-` lines.
- `tesselint run [--source P] [--tier t1|t2|t3 | --epics …] [--out runs] [--resume RUN_DIR] [--prefilter] [--prefilter-k 10] [--no-swap] [--render full|core] [--concurrency 16] [--rate 50] [--max-usd 1.0] [--refresh] [--dry-run]`
- `tesselint synth --out DIR [--seed 42] [--n 60]`: writes `stories.md` and `truth.json`.
- `tesselint eval RUN_DIR (--truth F | --labels F | --make-labels F [--n 80])`
- `tesselint report RUN_DIR [--merge 0.5] [--cluster 0.5] [--split 0.8] [--swap-gate 0.2]`

Env vars come from the environment or the gitignored `.env` (python-dotenv), and values are never logged: `OPENROUTER_API_KEY` (required), `OPENROUTER_BASE_URL` (`https://openrouter.ai/api`), `TESSELINT_MODEL` (`typesafe/jev-1.13`), `TESSELINT_SOURCE` (no default: `parse` and `run` need it or `--source`), `TESSELINT_CACHE_DIR` (`.cache/tesselint`), `TESSELINT_MAX_USD` (1.0).

Exit codes: 0 ok; 1 unexpected (traceback with `--debug`); 2 usage; 3 spend cap; 4 bad input or unparsed lines; 5 Jev auth, credits or 400; 6 too many failed calls; 130 interrupted. Errors are one line on stderr, e.g. `tesselint: error: OPENROUTER_API_KEY is not set. Add it to .env or the environment.` (exit 5; `--dry-run`, `parse`, `synth`, `eval` and `report` never need the key). An unknown epic or tier is a usage error (exit 2).

Dependencies: httpx, aiolimiter, python-dotenv, numpy, scipy, model2vec. Dev: pytest (anyio plugin for async tests), ruff, mypy strict (`ignore_missing_imports`: model2vec, scipy, aiolimiter).

## 8. Interfaces (binding)

```python
# models.py
class Relation(StrEnum):
    DUPLICATE = "duplicate"; A_CONTAINS_B = "A_contains_B"; B_CONTAINS_A = "B_contains_A"
    PARTIAL = "partial_overlap"; SAME_AREA = "same_area_distinct"; UNRELATED = "unrelated"
class ProposalKind(StrEnum): MERGE = "merge"; OVERLAP = "overlap"; SPLIT = "split"; LEAVE = "leave"
Order = Literal["fwd", "swap"]
@dataclass(frozen=True, slots=True)
class Story: id: str; epic: str; title: str; tag: str; core: str; subs: tuple[str, ...]; line: int
@dataclass(frozen=True, slots=True)
class Tombstone: id: str; epic: str; title: str; target: str | None; line: int
@dataclass(frozen=True, slots=True)
class Snapshot: path: Path; text: str; sha256: str
@dataclass(frozen=True, slots=True)
class ParseResult: stories: tuple[Story, ...]; tombstones: tuple[Tombstone, ...]; unparsed: tuple[tuple[int, str], ...]
@dataclass(frozen=True, slots=True)  # lives here so config and proposals both import it without a cycle
class Thresholds: swap_gate: float = 0.2; merge: float = 0.5; cluster: float = 0.5; split: float = 0.8; split_corroborated: float = 0.7; related: float = 0.4; min_words_atomic: int = 8; borderline: float = 0.1

# config.py, cli.py
DEFAULT_BASE_URL = "https://openrouter.ai/api"; DEFAULT_MODEL = "typesafe/jev-1.13"; USD_PER_TOKEN = 4.2e-8
TIERS: Mapping[str, tuple[str, ...] | None] = {"t1": ("E1", "E2", "E20"), "t2": ("E1", "E2", "E20", "E3", "E4", "E23"), "t3": None}
@dataclass(frozen=True, slots=True)
class RunConfig: source: Path; out_root: Path; epics: tuple[str, ...] | None; render: Literal["full", "core"]; prefilter: bool; prefilter_k: int; swap: bool; concurrency: int; rate_per_s: float; max_usd: float; cache_dir: Path; model: str; base_url: str; resume: Path | None; refresh: bool; dry_run: bool; thresholds: Thresholds
def main(argv: Sequence[str] | None = None) -> int

# parse.py, render.py
def take_snapshot(source: Path, run_dir: Path) -> Snapshot
def load_snapshot(run_dir: Path) -> Snapshot
def parse_section(text: str) -> ParseResult
def select_epics(stories: Sequence[Story], epics: Collection[str] | None) -> tuple[Story, ...]  # keeps a story when its heading epic OR its ID's epic (US-<n>.x -> E<n>) is selected; None keeps all; document order
# Story.epic is the `### E<n>` heading the story sits under, which may differ from its ID's epic; Story.line is 1-based within snapshot.md
def clean(text: str) -> str
def render(story: Story, mode: Literal["full", "core"] = "full") -> str
def text_hash(text: str) -> str

# questions.py
@dataclass(frozen=True, slots=True)
class Question:
    id: str; payload: Mapping[str, Any]
    @property
    def hash(self) -> str: ...  # sha256 of json.dumps(payload, sort_keys=True, separators=(",", ":"))
REL_V1: Question; ATOMIC_V2: Question

# jev.py
@dataclass(frozen=True, slots=True)
class Answer:
    question_id: str; choice: str | None; confidence: float | None
    probabilities: Mapping[str, float]  # noul: {"yes": p}
    model: str; input_tokens: int; cost_usd: float; gen_id: str; latency_ms: float
class JevError(Exception): ...
class JevRetryable(JevError): retry_after_s: float | None
class JevFatal(JevError): status: int
class JevClient:  # tests pass httpx.MockTransport
    def __init__(self, api_key: str, *, base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_MODEL,
                 transport: httpx.AsyncBaseTransport | None = None, timeout_s: float = 30.0, max_connections: int = 16) -> None: ...
    async def decide(self, state: str | Mapping[str, str], question: Question) -> Answer: ...  # one attempt
    async def key_usage_usd(self) -> float | None: ...
    async def aclose(self) -> None: ...
def parse_response(body: Mapping[str, Any], question: Question, latency_ms: float) -> Answer

# cache.py
def cache_key(question: Question, model: str, text_a: str, text_b: str | None) -> str
class AnswerCache:
    def __init__(self, path: Path) -> None: ...
    def get(self, key: str) -> Answer | None: ...
    def put(self, key: str, answer: Answer, *, text_hash_a: str, text_hash_b: str | None) -> None: ...
    def close(self) -> None: ...

# workers.py
@dataclass(frozen=True, slots=True)
class Job: key: str; a: str; b: str | None; order: Order; state: str | Mapping[str, str]; question: Question; est_tokens: int; text_hash_a: str; text_hash_b: str | None
@dataclass(frozen=True, slots=True)
class JobResult: job: Job; answer: Answer | None; cached: bool; attempts: int; error: str | None
@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 6; base_s: float = 0.5; cap_s: float = 30.0
    def delay(self, attempt: int, retry_after_s: float | None, rng: random.Random) -> float: ...
class SpendLedger:
    spent_usd: float
    def __init__(self, cap_usd: float, usd_per_token: float = 4.2e-8) -> None: ...
    def try_reserve(self, est_tokens: int) -> bool: ...
    def settle(self, est_tokens: int, answer: Answer | None) -> None: ...
@dataclass(frozen=True, slots=True)
class RunStats: live: int; cached: int; failed: int; retries: int; by_status: Mapping[str, int]; wall_s: float; p50_ms: float; p95_ms: float; stopped: Literal["complete", "partial_cap", "partial_errors", "interrupted"]
async def run_jobs(jobs: Sequence[Job], client: JevClient, cache: AnswerCache, ledger: SpendLedger, *,
                   concurrency: int = 16, rate_per_s: float = 50.0, policy: RetryPolicy = RetryPolicy(),
                   on_result: Callable[[JobResult], None] = lambda r: None,
                   sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                   rng: random.Random | None = None) -> tuple[list[JobResult], RunStats]
def estimate_tokens(words_a: int, words_b: int | None) -> int

# prefilter.py
def similarity(texts: Sequence[str], model_name: str = "minishlab/potion-retrieval-32M") -> NDArray[np.float64]
def keep_pairs(sim: NDArray[np.float64], k: int = 10, top_pct: float = 2.0) -> set[tuple[int, int]]

# verdicts.py, cluster.py
@dataclass(frozen=True, slots=True)
class PairVerdict:
    a: str; b: str  # document order
    probs: Mapping[Relation, float]; fwd: Mapping[Relation, float]; swap: Mapping[Relation, float] | None
    overlap: float; merge: float; relation: Relation
def build_verdicts(stories: Sequence[Story], results: Iterable[JobResult]) -> dict[tuple[str, str], PairVerdict]
def overlap_matrix(ids: Sequence[str], verdicts: Mapping[tuple[str, str], PairVerdict]) -> NDArray[np.float64]  # NaN = not asked
@dataclass(frozen=True, slots=True)
class Cluster: id: str; members: tuple[str, ...]; mean_overlap: float
def average_linkage(ids: Sequence[str], overlap: NDArray[np.float64], threshold: float = 0.5) -> list[Cluster]

# proposals.py (Thresholds is imported from models)
@dataclass(frozen=True, slots=True)
class Proposal: id: str; kind: ProposalKind; target: str | None; stories: tuple[str, ...]; decisive_value: float; borderline: bool; evidence: tuple[str, ...]
def propose(stories: Sequence[Story], verdicts: Mapping[tuple[str, str], PairVerdict], clusters: Sequence[Cluster],
            atomic: Mapping[str, float | None], th: Thresholds) -> list[Proposal]
def render_proposals(proposals: Sequence[Proposal], stories: Sequence[Story], header: Mapping[str, str]) -> str

# synth.py, evaluate.py, pipeline.py, outputs.py
@dataclass(frozen=True, slots=True)
class Truth: seed: int; n: int; duplicates: tuple[tuple[str, str], ...]; containments: tuple[tuple[str, str], ...]; bundled: tuple[str, ...]
def generate(seed: int = 42, n: int = 60) -> tuple[str, Truth]  # returns a whole document framed by `## 4. User stories` ... `## 5. Workflows` so take_snapshot/parse_section treat it like the real one; `n` counts live stories
@dataclass(frozen=True, slots=True)
class PR: tp: int; fp: int; fn: int; precision: float; recall: float
def score_synthetic(run_dir: Path, truth: Truth) -> dict[str, PR]
def make_label_sheet(run_dir: Path, out: Path, n: int = 80, seed: int = 7) -> Path
def score_labels(run_dir: Path, labels: Path) -> dict[str, float]
async def run(cfg: RunConfig, client: JevClient) -> Path
def report(run_dir: Path, th: Thresholds) -> None
def write_outputs(run_dir: Path, stories: Sequence[Story], results: Sequence[JobResult], verdicts: Mapping[tuple[str, str], PairVerdict],
                  clusters: Sequence[Cluster], proposals: Sequence[Proposal], run_meta: Mapping[str, Any]) -> None
```

## 9. Run output schemas (`runs/YYYYMMDD-HHMMSS/`)

- **`snapshot.md` and `snapshot.sha256`:** the exact §4 bytes up to `## 5.`; the hash file reads `<hex>  snapshot.md`.
- **`stories.jsonl`:** `{id, epic, title, tag, line, words, text_hash, text, atomic, atomic_skipped}`.
- **`pairs.jsonl`:** one line per answer used; `a` is the story sent as A.

```json
{"a":"US-3.3","b":"US-4.4","order":"fwd","question":"rel.v1","question_hash":"…","text_hash_a":"…","text_hash_b":"…","choice":"partial_overlap","confidence":0.48,"probabilities":{"partial_overlap":0.53,"A_contains_B":0.37},"model":"typesafe/jev-1.13-20260917","input_tokens":1412,"cost_usd":5.93e-05,"latency_ms":301,"cached":false,"gen_id":"gen-dec-…","prefilter_cos":0.61,"prefilter_kept":true}
```

- **`matrix.csv`:** square, in document order, header `id,US-1.1,…`. Cells hold o to 3 decimals; empty means not asked.
- **`clusters.json`:** `{"method":"average_linkage","threshold":0.5,"clusters":[{"id":"C1","members":[…],"mean_overlap":0.95,"edges":[{"a","b","overlap","merge","relation"}]}],"singletons":[…]}`.
- **`proposals.md`** (illustrative):

```markdown
# tesselint proposals · run 20260926-141200 · snapshot 3b5548c8… · typesafe/jev-1.13-20260917
Calibration: rel.v1 PASS · atomic.v2 PASS
## Merge
### M1 · merge US-4.4 into US-3.3 (containment) · m 0.53 · borderline
- fwd partial 0.53 / A⊃B 0.37 · swap A⊃B 0.66 / partial 0.31 · C1 · also related: US-4.1 (o 0.45)
### O1 · overlap C2: US-2.1, US-2.2, US-2.4 · mean o 0.78
## Split
### S1 · split US-3.5 · bundled 0.85 · overlaps C5, C9 · tag [MVP; note]
## Leave alone (151)
- E1: US-1.3, US-1.4, …
```

- **`proposals.json`:** the machine-readable twin of `proposals.md`, read by `eval` and `report`: `{"thresholds": {…}, "proposals": [{"id": "M1", "kind": "merge", "target": "US-3.3", "stories": ["US-3.3", "US-4.4"], "decisive_value": 0.53, "borderline": true, "evidence": ["…"]}]}`. For merge, `target` is the story kept and `stories` lists the kept story first; for split, `target` is null and `stories` is the one story; for overlap, `target` is the cluster id.
- **`run.json`:** `run_id, started_at, finished_at, wall_s, status, snapshot_sha256, tier, epics, n_stories, n_tombstones, n_pairs, render, jobs{fwd,swap,atomic}, calls{live,cached,failed,retries,by_status}, tokens{total,mean,p95}, cost_usd{live,cached_equivalent,preflight_estimate,key_usage_delta}, latency_ms{p50,p95}, calls_per_s, concurrency, rate_per_s, max_usd, model_requested, models_resolved, mixed_models, questions{id:hash}, prefilter{enabled,k,top_pct,kept,recall_vs_jev_overlap,recall_vs_jev_merge}, thresholds, proposals{merge,overlap,split,leave}, versions`.
- **Cache line:** `{key, question_id, question_hash, model_requested, model_resolved, text_hash_a, text_hash_b, answer, created_at}`.

## 10. Risks and out of scope

1. **Position bias and drift** (67% mirrored agreement, drift up to 0.26): swap averaging, borderline flags, a cache that freezes answers.
2. **Weak atomicity signal** (AUC 0.69): the 0.8 threshold, corroboration, the calibration banner.
3. **A known related pair may appear only as "also related"** (same_area_distinct 0.53 vs partial 0.41): reported, not tuned away.
4. **Thin ground truth** (one real pair, an LLM labeller): the reviewer adjudicates disagreements.
5. **Jev is early access:** snapshots change, the 429 ceiling above 16 in flight is unknown, and a small key can run out mid-run (the spend cap guards it).
6. **The source is edited concurrently:** runs use snapshots, and the parse gate exits 4.

Out of scope: editing the source or applying proposals, a heatmap, pair batching, model routing, adaptive rate control, any server or database, non-English input.

## 11. Clarifications pinned by the tests (2026-09-26)

The test suite is the executable spec. Where this document was silent, the tests settle it as follows.

- **Questions:** `Question.hash` is the sha256 of `json.dumps(payload, separators=(",", ":"))` in insertion order, because option order is what Jev sees; reordering options is a new version. On the wire `rel.v1` is keyed `rel` and `atomic.v2` `atomic`. A `noul` answer has `choice` and `confidence` of None and `probabilities == {"yes": p}`.
- **Parse:** nested bullets become separate `subs` (one story has 13). Line numbers are 1-based within `snapshot.md`; `parse` adds the offset of the `## 4.` cut when it reports a line of the source.
- **Jev errors:** `Retry-After` is honoured on any retryable status, 503 included.
- **Cache:** `AnswerCache(path)` takes the file path (`<cache_dir>/answers.jsonl`). `put` also takes optional `question_hash` and `model_requested`, and `run_jobs` always passes them.
- **Workers:** `run_jobs` raises `JevFatal` on a fatal status (400/401/402 without `in_flight`/403/404) after draining; the CLI maps it to exit 5. "Over 2% failed" means failed jobs over 2% of all jobs in the run.
- **Spend cap:** an estimate above `--max-usd` makes `pipeline.run` return without any call, with `run.json` status `partial_cap`; the CLI maps that status to exit 3.
- **Pre-filter:** every run computes it locally. `write_outputs(..., prefilter=)` maps a document-ordered pair to `(cosine, kept)`. Tests that need the model2vec model are marked `slow` and load it from the local cache.
- **Verdicts:** `PairVerdict.fwd` and `.swap` are stored in document order. The `overlap_matrix` diagonal is NaN.
- **Clusters:** ids C1, C2, … ordered by mean overlap, highest first.
- **Synthetic data:** each seed's core appears verbatim in the bundle built from it. `n=20` still plants at least one duplicate, one containment and one bundle.
- **Evaluation:** the label sheet is JSON Lines with blank labels (`merge`, `overlap`, `none`); `score_labels` returns at least `rel_agreement` and `rel_kappa`. A merge that names no planted pair is a false positive for both duplicates and containments; a containment in the wrong direction is a false positive for direction.

## 12. Calibration outcome and operating point (2026-09-26)

Measured after the implementation, on real data; the aggregates are in `docs/results.md`.

- **Labels:** a blind LLM labeller (the §5 proxy) labelled the T1 sheet (80 stratified pairs, 35 stories, 21 proposals) and a hold-out of 80 stratified pairs drawn from T3 with no T1 story. The reviewer has not adjudicated the disagreements yet.
- **`atomic.v2` passes** on T1: precision 0.67 at 0.8, AUC 0.81 (bars 0.6 and 0.7).
- **`rel.v1` fails as a classifier** at its 0.5 boundary: T1 agreement 0.60, κ 0.15; hold-out agreement 0.54, κ 0.08. Jev over-calls overlap and containment; the labeller reads most of those pairs as a dependency or a reference.
- **`rel.v1` ranks well:** hold-out AUC of o against "related" is 0.91. On all 160 labelled pairs, no pair with o < 0.5 was related (0/83) and 9 of the 10 related pairs had o ≥ 0.7.
- **Thresholds fitted on T1 alone overfit:** o ≥ 0.85 and m ≥ 0.6 gave κ 0.64 in-sample but κ 0.09 and recall 0.25 on the hold-out, so they were not adopted.
- **Operating point:** the CLI uses `config.CALIBRATED_THRESHOLDS`, which cuts clusters (overlap proposals) at o ≥ 0.7: recall 0.90 and about half the review load of 0.5 on the labelled pairs. Merge (m ≥ 0.5) and split thresholds are unchanged; the labeller never answered "merge" in 160 pairs, so merge precision cannot be estimated and merges carry the "not trusted" banner. `models.Thresholds()` keeps the §2.5 defaults.
- **Consequence:** the proposals are a ranked review queue, not decisions. The reviewer's adjudication of the labeller's disagreements (about 20 rows) is the next calibration step.
