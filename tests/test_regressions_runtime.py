"""Regressions for the reviewed runtime defects (jev, workers, cache, pipeline).

Each test names the review item it pins: review_runtime H1-H5, M1-M5, L1, L2, L5, L7, L8;
review_cli H1, H2, M1, M2, M4; review_analysis H2. FakeJev stands in for Jev at the HTTP boundary;
a few tests wrap its transport to delay or break single responses. Pipeline tests compute the
local pre-filter, so they load the cached model2vec model offline and are marked slow.
"""

import asyncio
import csv
import dataclasses
import itertools
import json
import random
import re
from collections import Counter
from collections.abc import Callable, Coroutine, Mapping
from contextlib import aclosing, closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from tesselint import pipeline
from tesselint.cache import AnswerCache
from tesselint.config import USD_PER_TOKEN, RunConfig
from tesselint.errors import InputError
from tesselint.jev import JevClient, JevFatal, JevRetryable, parse_response
from tesselint.models import Thresholds
from tesselint.pipeline import report
from tesselint.prefilter import keep_pairs, similarity
from tesselint.questions import ATOMIC_V2, REL_V1, Question
from tesselint.render import text_hash
from tesselint.workers import (
    Job,
    JobResult,
    RetryPolicy,
    RunStats,
    SpendLedger,
    estimate_tokens,
    run_jobs,
)
from tests.fakes import (
    Failure,
    FakeJev,
    State,
    cost_for,
    http_error,
    missing_answers,
    timeout_error,
)
from tests.helpers_pipeline import (
    API_KEY,
    BASE_URL,
    FAST_RATE,
    RUN_FILES,
    SynthWorld,
    build_world,
    edit_story,
    expected_jobs,
    force_hf_offline,
    jev_client,
    load_world,
    make_config,
    read_json,
    read_jsonl,
    run_pipeline,
    unplanted_story,
    words,
)
from tests.test_workers import RecordingSleep, atomic_job, canned_answer, pair_job

Handler = Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]
Mutation = Callable[[dict[str, Any]], None]

TICK = re.compile(
    r"(fwd|swap|atomic) (\d+)/(\d+) · cached (\d+) · failed (\d+) · \$\d+\.\d+/\$\d+\.\d+"
)


@pytest.fixture(autouse=True)
def _hf_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    force_hf_offline(monkeypatch)


# --- helpers --------------------------------------------------------------------------------------


def client_on(handler: Handler) -> JevClient:
    """A real JevClient whose HTTP goes to `handler`."""
    return JevClient(API_KEY, base_url=BASE_URL, transport=httpx.MockTransport(handler))


def request_state(request: httpx.Request) -> State:
    """The `state` of a decisions request."""
    state: State = json.loads(request.content)["state"]
    return state


async def serve(
    jobs: list[Job],
    client: JevClient,
    cache: AnswerCache,
    ledger: SpendLedger | None = None,
    *,
    concurrency: int = 4,
    seen: list[JobResult] | None = None,
) -> tuple[list[JobResult], RunStats]:
    """run_jobs with injected sleep and rng."""
    return await run_jobs(
        jobs,
        client,
        cache,
        ledger or SpendLedger(1.0),
        concurrency=concurrency,
        rate_per_s=FAST_RATE,
        on_result=(seen if seen is not None else []).append,
        sleep=RecordingSleep(),
        rng=random.Random(0),
    )


def config(world: SynthWorld, root: Path, **changes: Any) -> RunConfig:
    """make_config for the world's source, with RunConfig fields replaced."""
    return dataclasses.replace(make_config(world.source, root), **changes)


async def run_with_progress(cfg: RunConfig, fake: FakeJev, lines: list[str]) -> Path:
    """pipeline.run with a progress sink."""
    async with jev_client(fake, model=cfg.model) as client:
        return await pipeline.run(cfg, client, progress=lines.append)


def read_matrix(run_dir: Path) -> dict[tuple[str, str], str]:
    """matrix.csv cells by (row id, column id)."""
    with (run_dir / "matrix.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    header = rows[0][1:]
    return {
        (row[0], col): cell for row in rows[1:] for col, cell in zip(header, row[1:], strict=True)
    }


def floor_usd(world: SynthWorld) -> tuple[float, float]:
    """Token-model cost of (every forward pair, every atomic call) of a cold run (DESIGN §3)."""
    ids, texts = world.ids, world.texts
    fwd = sum(
        estimate_tokens(words(texts[a]), words(texts[b]))
        for i, a in enumerate(ids)
        for b in ids[i + 1 :]
        if texts[a] != texts[b]
    )
    atomic = sum(estimate_tokens(words(texts[s]), None) for s in ids if words(texts[s]) >= 8)
    return fwd * USD_PER_TOKEN, atomic * USD_PER_TOKEN


def cache_file(cfg: RunConfig) -> Path:
    return cfg.cache_dir / "answers.jsonl"


# --- jev.py: malformed 200s (review_runtime M3) and billed rejections (M2) ------------------------


def _set(path: tuple[str, ...], value: Any) -> Mutation:
    def mutate(body: dict[str, Any]) -> None:
        target = body
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return mutate


def _drop(path: tuple[str, ...]) -> Mutation:
    def mutate(body: dict[str, Any]) -> None:
        target = body
        for key in path[:-1]:
            target = target[key]
        del target[path[-1]]

    return mutate


MALFORMED: list[Any] = [
    pytest.param("choice_rel", REL_V1, _set(("usage", "cost"), None), id="cost-null"),
    pytest.param("choice_rel", REL_V1, _set(("usage", "cost"), "n/a"), id="cost-not-a-number"),
    pytest.param("choice_rel", REL_V1, _set(("usage", "input_tokens"), None), id="tokens-null"),
    pytest.param(
        "choice_rel", REL_V1, _set(("answers", "rel", "probabilities"), None), id="probs-null"
    ),
    pytest.param(
        "choice_rel", REL_V1, _drop(("answers", "rel", "probabilities")), id="choice-without-probs"
    ),
    pytest.param(
        "choice_rel", REL_V1, _set(("answers", "rel", "probabilities"), {}), id="probs-{}"
    ),
    pytest.param("choice_rel", REL_V1, _set(("answers", "rel"), None), id="answer-null"),
    pytest.param(
        "noul_atomic_v2", ATOMIC_V2, _set(("answers", "atomic", "noul"), None), id="noul-null"
    ),
]


@pytest.mark.parametrize(("body_name", "question", "mutate"), MALFORMED)
def test_malformed_200_body_is_retryable(
    jev_bodies: dict[str, Any], body_name: str, question: Question, mutate: Mutation
) -> None:
    """M3: a 200 that cannot be read as an answer is retried, never a crash or a poisoned answer."""
    body = jev_bodies[body_name]
    mutate(body)

    with pytest.raises(JevRetryable):
        parse_response(body, question, 301.0)


def test_rejected_200_reports_the_cost_it_was_billed(jev_bodies: dict[str, Any]) -> None:
    """M2: a 200 without answers was still billed; the error carries its usage.cost."""
    body = jev_bodies["choice_rel"]
    del body["answers"]

    with pytest.raises(JevRetryable) as raised:
        parse_response(body, REL_V1, 301.0)

    assert raised.value.cost_usd == pytest.approx(2.5284e-05)


@pytest.mark.anyio
async def test_undecodable_200_is_retryable() -> None:
    """M3: httpx.DecodingError is not a TransportError, but it is still a retryable failure."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Encoding": "gzip"}, content=b"not gzip")

    async with aclosing(client_on(handler)) as client:
        with pytest.raises(JevRetryable):
            await client.decide("Rate limits. Caps and pacing.", ATOMIC_V2)


# --- workers.py -----------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_billed_rejected_attempt_is_charged_to_the_ledger(tmp_path: Path) -> None:
    """M2: the ledger adds usage.cost of a rejected 200, not only of the final success."""
    job = atomic_job(1)
    fake = FakeJev()
    fake.fail(missing_answers())
    ledger = SpendLedger(1.0)

    async with jev_client(fake) as client:
        with closing(AnswerCache(tmp_path / "answers.jsonl")) as cache:
            [result], _ = await serve([job], client, cache, ledger)

    assert result.answer is not None and fake.requests == 2
    assert fake.total_cost == pytest.approx(2 * cost_for(job.state))
    assert ledger.spent_usd == pytest.approx(fake.total_cost)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure", [missing_answers(), timeout_error()], ids=["billed-200-without-answers", "timeout"]
)
async def test_every_retry_reserves_so_spend_stays_under_the_cap(
    tmp_path: Path, failure: Failure
) -> None:
    """M2: each attempt reserves its estimate; a possibly billed attempt stays counted."""
    job = atomic_job(1)
    unit = cost_for(job.state)  # est_tokens is exact for atomic_job
    ledger = SpendLedger(cap_usd=1.5 * unit)
    fake = FakeJev()
    fake.fail(failure)

    async with jev_client(fake) as client:
        with closing(AnswerCache(tmp_path / "answers.jsonl")) as cache:
            [result], stats = await serve([job], client, cache, ledger)

    assert fake.requests == 1, "a second attempt would pass the cap"
    assert (result.answer, stats.stopped) == (None, "partial_cap")
    assert ledger.spent_usd == pytest.approx(unit)
    assert ledger.spent_usd <= ledger.cap_usd


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(_drop(("answers", "rel", "probabilities")), id="choice-without-probs"),
        pytest.param(_set(("usage", "cost"), None), id="cost-null"),
        pytest.param(_set(("answers", "rel", "probabilities"), None), id="probs-null"),
    ],
)
async def test_malformed_200_is_retried_and_never_cached(
    tmp_path: Path, jev_bodies: dict[str, Any], mutate: Mutation
) -> None:
    """M3: a malformed 200 is retried; only the good answer reaches the result and the cache."""
    job = pair_job(1)
    bad = jev_bodies["choice_rel"]
    mutate(bad)
    fake = FakeJev(lambda _s, _q: {"partial_overlap": 0.7, "unrelated": 0.3})
    sent: list[State] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request_state(request))
        if len(sent) == 1:
            return httpx.Response(200, json=bad)
        return await fake.transport.handle_async_request(request)

    path = tmp_path / "answers.jsonl"
    async with aclosing(client_on(handler)) as client:
        with closing(AnswerCache(path)) as cache:
            [result], stats = await serve([job], client, cache)

    assert result.answer is not None and result.attempts == 2
    assert result.answer.probabilities["partial_overlap"] == pytest.approx(0.7)
    assert stats.stopped == "complete"
    with closing(AnswerCache(path)) as reread:
        assert reread.get(job.key) == result.answer


def _fatal_402() -> httpx.Response:
    return httpx.Response(402, json={"error": {"message": "Insufficient credits", "code": 402}})


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("boom", "error"),
    [
        pytest.param("fatal", JevFatal, id="JevFatal-L2"),
        pytest.param("unexpected", RuntimeError, id="unexpected-M3"),
    ],
)
async def test_an_abort_drains_in_flight_calls_and_keeps_their_answers(
    tmp_path: Path, boom: str, error: type[BaseException]
) -> None:
    """L2, M3: on JevFatal or another error, in-flight calls finish and get cached first."""
    jobs = [atomic_job(i) for i in range(4)]
    doomed = jobs[0].state
    fake = FakeJev()
    finished: list[State] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        state = request_state(request)
        if state == doomed:
            if boom == "fatal":
                return _fatal_402()
            raise RuntimeError("unexpected transport bug")
        await asyncio.sleep(0.05)
        response = await fake.transport.handle_async_request(request)
        finished.append(state)
        return response

    seen: list[JobResult] = []
    path = tmp_path / "answers.jsonl"
    async with aclosing(client_on(handler)) as client:
        with closing(AnswerCache(path)) as cache:
            with pytest.raises(error):
                await serve(jobs, client, cache, concurrency=4, seen=seen)
            finished_at_raise = list(finished)

    assert sorted(map(str, finished_at_raise)) == sorted(str(j.state) for j in jobs[1:])
    assert sorted(r.job.key for r in seen) == sorted(j.key for j in jobs[1:])
    with closing(AnswerCache(path)) as reread:
        assert all(reread.get(j.key) is not None for j in jobs[1:])


@pytest.mark.anyio
async def test_refresh_reasks_cached_jobs_and_rewrites_the_cache(tmp_path: Path) -> None:
    """M4: `refresh=True` skips cache.get but still writes the new answers."""
    jobs = [atomic_job(i) for i in range(3)]
    path = tmp_path / "answers.jsonl"
    with closing(AnswerCache(path)) as cache:
        for i, job in enumerate(jobs):
            cache.put(job.key, canned_answer(i), text_hash_a=job.text_hash_a, text_hash_b=None)
    fake = FakeJev()

    async with jev_client(fake) as client:
        with closing(AnswerCache(path)) as cache:
            results, stats = await run_jobs(
                jobs, client, cache, SpendLedger(1.0), rate_per_s=FAST_RATE, refresh=True
            )

    assert fake.requests == 3
    assert (stats.live, stats.cached) == (3, 0)
    with closing(AnswerCache(path)) as reread:
        assert all(reread.get(r.job.key) == r.answer for r in results)
        assert all(reread.get(j.key) != canned_answer(i) for i, j in enumerate(jobs))


@pytest.mark.anyio
async def test_failures_in_the_last_batch_still_stop_the_run(tmp_path: Path) -> None:
    """L1: 3 of 100 jobs failing at the very end is over 2%, so the run is partial_errors."""
    jobs = [atomic_job(i) for i in range(100)]
    doomed = {str(job.state) for job in jobs[-3:]}

    def is_doomed(state: State, question_id: str) -> bool:
        return isinstance(state, str) and state in doomed

    fake = FakeJev()
    fake.fail(*[http_error(503, when=is_doomed)] * 18)

    async with jev_client(fake) as client:
        with closing(AnswerCache(tmp_path / "answers.jsonl")) as cache:
            _, stats = await serve(jobs, client, cache, concurrency=16)

    assert (stats.live, stats.failed) == (97, 3)
    assert stats.stopped == "partial_errors"


# --- cache.py (L5) --------------------------------------------------------------------------------


def test_unreadable_lines_in_the_middle_of_the_cache_are_counted(tmp_path: Path) -> None:
    """L5: bad rows mid-file are counted (a torn tail is not); invalid answers are not served."""
    path = tmp_path / "answers.jsonl"
    jobs = [atomic_job(i) for i in range(2)]
    with closing(AnswerCache(path)) as cache:
        for i, job in enumerate(jobs):
            cache.put(job.key, canned_answer(i), text_hash_a=job.text_hash_a, text_hash_b=None)
    good = path.read_text("utf-8").splitlines(keepends=True)
    null_probs = json.loads(good[0])
    null_probs["key"], null_probs["answer"]["probabilities"] = "k-null-probs", None
    empty_rel = json.loads(good[0])
    empty_rel["key"] = "k-empty-rel"
    empty_rel["answer"].update(question_id=REL_V1.id, choice="unrelated", probabilities={})
    path.write_text(
        good[0]
        + "{not json\n"
        + json.dumps(null_probs)
        + "\n"
        + json.dumps(empty_rel)
        + "\n"
        + good[1]
        + good[0][: len(good[0]) // 2],
        encoding="utf-8",
    )

    with closing(AnswerCache(path)) as cache:
        assert cache.bad_lines == 3
        assert [cache.get(j.key) for j in jobs] == [canned_answer(0), canned_answer(1)]
        assert cache.get("k-null-probs") is None and cache.get("k-empty-rel") is None


def test_a_complete_but_invalid_last_line_is_counted_not_cut(tmp_path: Path) -> None:
    """L5: only an undecodable (torn) tail is cut; a whole JSON row that is not an answer stays."""
    path = tmp_path / "answers.jsonl"
    job = atomic_job(1)
    with closing(AnswerCache(path)) as cache:
        cache.put(job.key, canned_answer(1), text_hash_a=job.text_hash_a, text_hash_b=None)
    invalid = json.dumps({"key": "k-invalid", "answer": {"question_id": "atomic.v2"}})
    path.write_text(path.read_text("utf-8") + invalid, encoding="utf-8")

    with closing(AnswerCache(path)) as cache:
        assert cache.bad_lines == 1
        assert cache.get(job.key) == canned_answer(1)

    assert path.read_text("utf-8").splitlines()[-1] == invalid
    with closing(AnswerCache(path)) as reread:
        assert (reread.bad_lines, reread.get(job.key)) == (1, canned_answer(1))


# --- pipeline.run: parse gate, run dir, resume (review_cli H1, M1, M4; review_runtime H1, L4) -----


@pytest.mark.anyio
@pytest.mark.slow
@pytest.mark.parametrize("dry_run", [False, True], ids=["run", "dry-run"])
async def test_unparsed_story_line_stops_the_run_before_any_spend(
    tmp_path: Path, dry_run: bool
) -> None:
    """cli H1: an unparsed `- **US-` line is an InputError naming its source line, before a call."""
    world = build_world(tmp_path)
    lines = world.source.read_text("utf-8").splitlines(keepends=True)
    at = next(i for i, line in enumerate(lines) if line.startswith("### E1")) + 1
    lines.insert(at, "- **US-1.99 Broken story [MVP] with no closing bold marker\n")
    broken = tmp_path / "broken.md"
    broken.write_text("".join(lines), encoding="utf-8")
    cfg = dataclasses.replace(make_config(broken, tmp_path), dry_run=dry_run)
    fake = FakeJev(world.answer)

    with pytest.raises(InputError) as raised:
        await run_pipeline(cfg, fake)

    assert "US-1.99" in str(raised.value)
    assert f"line {at + 1}" in str(raised.value), "line numbers count from the source file"
    assert (fake.requests, fake.key_requests) == (0, 0)
    assert not cfg.out_root.exists() or list(cfg.out_root.iterdir()) == []


@pytest.mark.anyio
@pytest.mark.slow
async def test_a_second_run_in_the_same_second_gets_its_own_directory(tmp_path: Path) -> None:
    """cli M4, L4: an existing run dir is never reused; the new one takes a `-2` suffix."""
    world = build_world(tmp_path)
    cfg = make_config(world.source, tmp_path, dry_run=True)
    now = datetime.now(UTC)
    taken = [cfg.out_root / (now + timedelta(seconds=s)).strftime("%Y%m%d-%H%M%S") for s in (0, 1)]
    for directory in taken:
        directory.mkdir(parents=True)

    run_dir = await run_pipeline(cfg, FakeJev(world.answer))

    assert run_dir not in taken
    assert re.fullmatch(r"\d{8}-\d{6}-2", run_dir.name)
    assert all(list(directory.iterdir()) == [] for directory in taken)
    assert read_json(run_dir / "run.json")["run_id"] == run_dir.name


@pytest.mark.anyio
@pytest.mark.slow
async def test_resume_reuses_the_runs_snapshot_and_settings(tmp_path: Path) -> None:
    """H1, cli M1: resume keeps the run's snapshot (not the edited source) and epics/render/swap."""
    world = build_world(tmp_path)
    first = config(
        world,
        tmp_path,
        epics=("E1", "E2"),
        render="core",
        swap=False,
        prefilter=True,
        prefilter_k=3,
    )
    run_dir = await run_pipeline(first, FakeJev(world.answer))
    snapshot = (run_dir / "snapshot.md").read_bytes()
    world.source.write_text(edit_story(world, unplanted_story(world), " Now offline."), "utf-8")
    fake = FakeJev(world.answer)

    resumed = await run_pipeline(config(world, tmp_path, resume=run_dir), fake)

    assert resumed == run_dir
    assert (run_dir / "snapshot.md").read_bytes() == snapshot
    run = read_json(run_dir / "run.json")
    settings = (run["epics"], run["render"], run["swap"], run["prefilter"]["enabled"])
    assert settings == (["E1", "E2"], "core", False, True)
    assert run["prefilter"]["k"] == 3
    assert (fake.requests, run["status"]) == (0, "complete")


@pytest.mark.anyio
@pytest.mark.slow
@pytest.mark.parametrize("kind", ["missing", "not-a-run", "tampered-snapshot"])
async def test_resume_needs_an_intact_run_directory(tmp_path: Path, kind: str) -> None:
    """cli M1: resuming a dir without run.json/snapshot.md, or with a changed snapshot, fails."""
    world = build_world(tmp_path)
    if kind == "missing":
        target = tmp_path / "never" / "existed"
    elif kind == "not-a-run":
        target = tmp_path / "notarun"
        target.mkdir()
        (target / "notes.txt").write_text("mine", "utf-8")
    else:
        target = await run_pipeline(config(world, tmp_path, dry_run=True), FakeJev(world.answer))
        (target / "snapshot.md").write_text("## 4. User stories\n", "utf-8")
    before = sorted(p.name for p in target.iterdir()) if target.exists() else None
    fake = FakeJev(world.answer)

    with pytest.raises(InputError):
        await run_pipeline(config(world, tmp_path, resume=target), fake)

    assert (fake.requests, fake.key_requests) == (0, 0)
    assert (sorted(p.name for p in target.iterdir()) if target.exists() else None) == before


# --- pipeline.run: estimate and progress (review_runtime M1, review_cli M2) -----------------------


@pytest.mark.anyio
@pytest.mark.slow
async def test_estimate_counts_expected_swaps_against_the_cap(tmp_path: Path) -> None:
    """M1: a cap above forward+atomic but below forward+atomic+swaps is refused before any call."""
    world = build_world(tmp_path)
    fwd_usd, atomic_usd = floor_usd(world)
    cap = (fwd_usd + atomic_usd) * 1.05
    fake = FakeJev(world.answer)

    run_dir = await run_pipeline(make_config(world.source, tmp_path, max_usd=cap), fake)

    run = read_json(run_dir / "run.json")
    assert (fake.requests, run["status"]) == (0, "partial_cap")
    assert run["cost_usd"]["preflight_estimate"] == pytest.approx(
        fwd_usd * 1.33 + atomic_usd, rel=1e-3
    )


@pytest.mark.anyio
@pytest.mark.slow
async def test_rerun_after_one_edit_is_estimated_on_cache_misses_only(tmp_path: Path) -> None:
    """M1: cached jobs cost nothing in the preflight, so a one-edit re-run fits a small cap."""
    world = build_world(tmp_path)
    await run_pipeline(make_config(world.source, tmp_path), FakeJev(world.answer))
    story = unplanted_story(world)
    edited_source = tmp_path / "edited.md"
    edited_source.write_text(edit_story(world, story, " It also works offline."), "utf-8")
    edited = load_world(edited_source, world.truth, tmp_path / "oracle-edited")
    cap = sum(floor_usd(world)) / 4
    fake = FakeJev(edited.answer)

    run_dir = await run_pipeline(
        make_config(edited_source, tmp_path, out="runs2", max_usd=cap), fake
    )

    run = read_json(run_dir / "run.json")
    assert run["status"] == "complete"
    assert 0 < fake.requests <= len(world.stories)
    assert run["cost_usd"]["preflight_estimate"] <= cap
    assert run["estimate"]["to_ask"] < run["estimate"]["jobs"]


@pytest.mark.anyio
@pytest.mark.slow
async def test_run_reports_its_dir_estimate_and_progress(tmp_path: Path) -> None:
    """cli M2: `run dir`, then the estimate line, then fwd, atomic and swap progress lines."""
    world = build_world(tmp_path)
    expected = expected_jobs(world)
    first = expected.fwd + expected.atomic
    lines: list[str] = []

    run_dir = await run_with_progress(
        make_config(world.source, tmp_path), FakeJev(world.answer), lines
    )

    assert lines[0] == f"run dir: {run_dir}"
    assert re.fullmatch(
        rf"estimate: {first} jobs \({first} to ask, 0 cached\) · ~\$\d+\.\d+ · cap \$1\.00",
        lines[1],
    )
    ticks = [TICK.fullmatch(line) for line in lines[2:]]
    assert all(ticks), lines[2:]
    last = {m[1]: (int(m[2]), int(m[3]), int(m[5])) for m in ticks if m}
    assert last == {
        "fwd": (expected.fwd, expected.fwd, 0),
        "swap": (expected.swap, expected.swap, 0),
        "atomic": (expected.atomic, expected.atomic, 0),
    }
    assert list(dict.fromkeys(m[1] for m in ticks if m)) == ["fwd", "atomic", "swap"]


# --- pipeline.run: partial outputs on abort (review_runtime H2, L3) -------------------------------


@pytest.mark.anyio
@pytest.mark.slow
async def test_fatal_error_mid_run_writes_partial_outputs_then_reraises(tmp_path: Path) -> None:
    """H2: JevFatal mid-run still writes every file with status partial_errors and the error."""
    world = build_world(tmp_path)
    fake = FakeJev(world.answer)
    request_no = itertools.count()
    fake.fail(http_error(402, when=lambda _s, _q: next(request_no) == 40))
    cfg = make_config(world.source, tmp_path)

    with pytest.raises(JevFatal):
        await run_pipeline(cfg, fake)

    [run_dir] = list(cfg.out_root.iterdir())
    assert [name for name in RUN_FILES if not (run_dir / name).is_file()] == []
    text = (run_dir / "run.json").read_text("utf-8")
    run = json.loads(text)
    assert run["status"] == "partial_errors"
    assert "402" in run["error"]
    assert API_KEY not in text
    assert run["calls"]["live"] >= 30
    assert len(read_jsonl(cache_file(cfg))) == run["calls"]["live"], "cache flushed and closed"


@pytest.mark.anyio
@pytest.mark.slow
async def test_interrupt_writes_partial_outputs_then_reraises(tmp_path: Path) -> None:
    """H2: a cancelled run (Ctrl-C) writes partial outputs with status interrupted."""
    world = build_world(tmp_path)
    fake = FakeJev(world.answer, latency_s=0.002)
    cfg = make_config(world.source, tmp_path)
    holder: dict[str, asyncio.Task[Path]] = {}

    def progress(line: str) -> None:
        if line.startswith("fwd ") and "cancelled" not in holder:
            holder["cancelled"] = holder["task"]
            holder["task"].cancel()

    async with jev_client(fake, model=cfg.model) as client:
        holder["task"] = asyncio.create_task(pipeline.run(cfg, client, progress=progress))
        with pytest.raises(asyncio.CancelledError):
            await holder["task"]

    [run_dir] = list(cfg.out_root.iterdir())
    run = read_json(run_dir / "run.json")
    assert run["status"] == "interrupted"
    assert 0 < run["calls"]["live"] < expected_jobs(world).total
    assert len(read_jsonl(run_dir / "pairs.jsonl")) >= 1


# --- pipeline.run: honest metrics (review_runtime H3, H4, M5, L7) ---------------------------------


@pytest.mark.anyio
@pytest.mark.slow
async def test_key_usage_delta_is_measured_on_the_key(tmp_path: Path) -> None:
    """H3: the delta comes from GET /v1/key, so it sees a billed attempt the answers do not."""
    world = build_world(tmp_path)
    fake = FakeJev(world.answer, key_usage_start=0.25)
    fake.fail(missing_answers())

    run_dir = await run_pipeline(make_config(world.source, tmp_path), fake)

    cost = read_json(run_dir / "run.json")["cost_usd"]
    assert cost["key_usage_delta"] == pytest.approx(fake.total_cost)
    assert cost["live"] < fake.total_cost


@pytest.mark.anyio
@pytest.mark.slow
@pytest.mark.parametrize("dry_run", [False, True], ids=["key-endpoint-500", "dry-run"])
async def test_key_usage_delta_is_null_when_not_measured(tmp_path: Path, dry_run: bool) -> None:
    """H3: no key usage reading (endpoint failure, or a dry run) means null, never a guess."""
    world = build_world(tmp_path)
    fake = FakeJev(world.answer, key_status=500)

    run_dir = await run_pipeline(make_config(world.source, tmp_path, dry_run=dry_run), fake)

    assert read_json(run_dir / "run.json")["cost_usd"]["key_usage_delta"] is None
    if dry_run:
        assert fake.key_requests == 0


@pytest.mark.anyio
@pytest.mark.slow
async def test_prefilter_recall_is_measured_against_jev_positives(tmp_path: Path) -> None:
    """H4: recall = Jev positives (o >= 0.5, m >= 0.5) the pre-filter kept, even when it is off."""
    world = build_world(tmp_path)
    ids = world.ids
    kept = keep_pairs(similarity([world.texts[s] for s in ids]), k=1, top_pct=2.0)
    planted = {frozenset(p) for p in [*world.truth.duplicates, *world.truth.containments]}
    dropped = [
        frozenset((ids[i], ids[j]))
        for i in range(len(ids))
        for j in range(i + 1, len(ids))
        if (i, j) not in kept and frozenset((ids[i], ids[j])) not in planted
    ]
    extra_overlap, extra_merge = dropped[0], dropped[1]

    def answer(state: State, question_id: str) -> Mapping[str, float]:
        if question_id == REL_V1.id and not isinstance(state, str):
            pair = frozenset((world.by_text[state["A"]], world.by_text[state["B"]]))
            if pair == extra_overlap:
                return {"partial_overlap": 0.9, "same_area_distinct": 0.1}
            if pair == extra_merge:
                return {"duplicate": 0.9, "partial_overlap": 0.1}
        return world.answer(state, question_id)

    def recall(positives: set[frozenset[str]]) -> float:
        pos = {sid: i for i, sid in enumerate(ids)}
        hits = sum(tuple(sorted(pos[s] for s in pair)) in kept for pair in positives)
        return hits / len(positives)

    run_dir = await run_pipeline(config(world, tmp_path, prefilter_k=1), FakeJev(answer))

    prefilter = read_json(run_dir / "run.json")["prefilter"]
    overlap_recall = recall(planted | {extra_overlap, extra_merge})
    merge_recall = recall(planted | {extra_merge})
    assert overlap_recall < 1.0 and merge_recall < 1.0
    assert prefilter["recall_vs_jev_overlap"] == pytest.approx(overlap_recall)
    assert prefilter["recall_vs_jev_merge"] == pytest.approx(merge_recall)


@pytest.mark.anyio
@pytest.mark.slow
async def test_by_status_keeps_the_errors_of_every_phase(tmp_path: Path) -> None:
    """M5: 429s and 503s from every phase reach run.json calls.by_status, next to the 200s."""
    world = build_world(tmp_path)

    def is_swap(state: State, question_id: str) -> bool:
        return (
            question_id == REL_V1.id
            and not isinstance(state, str)
            and text_hash(state["A"]) > text_hash(state["B"])
        )

    def is_atomic(state: State, question_id: str) -> bool:
        return question_id == ATOMIC_V2.id

    fake = FakeJev(world.answer)
    fake.fail(http_error(503, when=is_swap), http_error(429, when=is_atomic, retry_after=0))

    run_dir = await run_pipeline(make_config(world.source, tmp_path), fake)

    calls = read_json(run_dir / "run.json")["calls"]
    assert calls["by_status"] == {"200": calls["live"], "429": 1, "503": 1}
    assert calls["retries"] == 2


@pytest.mark.anyio
@pytest.mark.slow
async def test_run_json_records_the_tier_and_worker_throughput(tmp_path: Path) -> None:
    """L7: `tier` comes from the config; calls_per_s divides by the time spent calling."""
    world = build_world(tmp_path)

    run_dir = await run_pipeline(config(world, tmp_path, tier="t1"), FakeJev(world.answer))

    run = read_json(run_dir / "run.json")
    assert run["tier"] == "t1"
    assert run["calls_per_s"] > run["calls"]["live"] / run["wall_s"]


# --- identical texts (review_analysis H2, review_runtime H5, L8) ----------------------------------


def with_twin(world: SynthWorld, root: Path, twin_id: str) -> tuple[SynthWorld, str]:
    """The world's source plus a copy of an unplanted story under a new ID in the last epic."""
    story = unplanted_story(world)
    lines = world.source.read_text("utf-8").splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if line.startswith("## 4."))
    at = start + story.line - 1
    end = at + 1
    while end < len(lines) and lines[end].startswith("  - "):
        end += 1
    copy = [lines[at].replace(story.id, twin_id, 1), *lines[at + 1 : end]]
    section_end = next(i for i, line in enumerate(lines) if line.startswith("## 5."))
    lines[section_end:section_end] = [*copy, "\n"]
    source = root / "twins.md"
    source.write_text("".join(lines), encoding="utf-8")
    return load_world(source, world.truth, root / "oracle-twins"), story.id


@pytest.mark.anyio
@pytest.mark.slow
async def test_identical_texts_get_a_local_duplicate_verdict(tmp_path: Path) -> None:
    """analysis H2, H5, L8: equal texts are never asked, merge at 1.0, and are asked about once."""
    twins, original = with_twin(build_world(tmp_path), tmp_path, "US-8.77")
    text = twins.texts[original]
    assert twins.texts["US-8.77"] == text
    fake = FakeJev(twins.answer)

    run_dir = await run_pipeline(make_config(twins.source, tmp_path), fake)

    rel_states = [
        dict(c.state)
        for c in fake.ok_calls()
        if c.question_id == REL_V1.id and not isinstance(c.state, str)
    ]
    assert all(state["A"] != state["B"] for state in rel_states)
    asked = Counter(frozenset(state.values()) for state in rel_states)
    others = [s for s in twins.ids if s not in (original, "US-8.77")]
    assert [asked[frozenset((text, twins.texts[s]))] for s in others] == [1] * len(others)
    assert [c.state for c in fake.ok_calls() if c.question_id == ATOMIC_V2.id].count(text) == 1
    assert read_matrix(run_dir)[(original, "US-8.77")] == "1.000"
    proposals = read_json(run_dir / "proposals.json")["proposals"]
    merges = [p for p in proposals if p["kind"] == "merge"]
    assert any(p["stories"] == [original, "US-8.77"] and p["target"] == original for p in merges)
    clusters = read_json(run_dir / "clusters.json")["clusters"]
    assert any({original, "US-8.77"} <= set(c["members"]) for c in clusters)
    [local] = [
        r for r in read_jsonl(run_dir / "pairs.jsonl") if {r["a"], r["b"]} == {original, "US-8.77"}
    ]
    assert (local["model"], local["cost_usd"], local["cached"]) == ("local", 0.0, True)
    run = read_json(run_dir / "run.json")
    assert run["jobs"]["fwd"] == expected_jobs(twins).fwd
    assert "local" not in run["models_resolved"]

    report(run_dir, Thresholds())

    assert read_json(run_dir / "proposals.json")["proposals"] == proposals


@pytest.mark.anyio
@pytest.mark.slow
async def test_a_job_sharing_the_key_of_a_failed_job_fails_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L8: a deduplicated job whose shared call failed is reported failed, not dropped."""
    monkeypatch.setattr(RetryPolicy, "delay", lambda *_args: 0.0)  # no real backoff waits
    twins, original = with_twin(build_world(tmp_path), tmp_path, "US-8.77")
    text = twins.texts[original]

    def is_twin_atomic(state: State, question_id: str) -> bool:
        return question_id == ATOMIC_V2.id and state == text

    fake = FakeJev(twins.answer)
    fake.fail(*[http_error(503, when=is_twin_atomic)] * 6)

    run_dir = await run_pipeline(make_config(twins.source, tmp_path), fake)

    run = read_json(run_dir / "run.json")
    assert (run["status"], run["calls"]["failed"]) == ("complete", 2)
    rows = {row["id"]: row for row in read_jsonl(run_dir / "stories.jsonl")}
    assert rows[original]["atomic"] is None and rows["US-8.77"]["atomic"] is None


# --- pipeline.report (review_cli H2) --------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.slow
async def test_report_rederives_only_the_stories_of_the_runs_epics(tmp_path: Path) -> None:
    """cli H2: report() on an epic-subset run lists only that run's stories, as the run did."""
    world = build_world(tmp_path)
    run_dir = await run_pipeline(config(world, tmp_path, epics=("E1", "E2")), FakeJev(world.answer))
    run_ids = {row["id"] for row in read_jsonl(run_dir / "stories.jsonl")}
    before = read_json(run_dir / "proposals.json")

    report(run_dir, Thresholds())

    after = read_json(run_dir / "proposals.json")
    assert {sid for p in after["proposals"] for sid in p["stories"]} == run_ids
    assert after == before


@pytest.mark.anyio
@pytest.mark.slow
async def test_report_puts_the_calibration_line_in_the_proposals_header(tmp_path: Path) -> None:
    """Contract: a run dir's calibration.json becomes the proposals.md calibration line."""
    world = build_world(tmp_path)
    run_dir = await run_pipeline(config(world, tmp_path, epics=("E1",)), FakeJev(world.answer))
    calibration = {"rel_pass": True, "atomic_pass": False}
    (run_dir / "calibration.json").write_text(json.dumps(calibration), "utf-8")

    report(run_dir, Thresholds())

    markdown = (run_dir / "proposals.md").read_text("utf-8")
    assert "rel.v1 PASS · atomic.v2 FAIL (not trusted)" in markdown


@pytest.mark.slow
def test_report_on_a_directory_that_is_not_a_run_is_an_input_error(tmp_path: Path) -> None:
    """cli H2/H3: report() names the problem instead of a KeyError or FileNotFoundError."""
    (tmp_path / "notarun").mkdir()

    with pytest.raises(InputError):
        report(tmp_path / "notarun", Thresholds())
