"""Append-only answer cache keyed by question, model and texts (DESIGN §2.6, §9 cache line)."""

import contextlib
import dataclasses
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from tesselint.jev import Answer
from tesselint.questions import Question


def cache_key(question: Question, model: str, text_a: str, text_b: str | None) -> str:
    """Return a stable key from question hash, requested model and the sent texts (DESIGN §2.6)."""
    canonical_payload = {
        "model_requested": model,
        "question_hash": question.hash,
        "question_id": question.id,
        "text_a": text_a,
        "text_b": text_b,
        "v": 1,
    }
    encoded = json.dumps(canonical_payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _answer_from_row(row: Any) -> tuple[str, Answer] | None:
    """(key, Answer) from one parsed cache line, or None if the line is not a usable answer.

    An answer with no probabilities is refused, so a malformed 200 cached by an older version is
    asked again instead of counting as o = 0 forever.
    """
    if not isinstance(row, dict) or not isinstance(row.get("key"), str):
        return None
    data = row.get("answer")
    if not isinstance(data, dict) or not isinstance(data.get("question_id"), str):
        return None
    probabilities = data.get("probabilities")
    if not isinstance(probabilities, dict) or not probabilities:
        return None
    if not all(
        isinstance(p, int | float) and not isinstance(p, bool) for p in probabilities.values()
    ):
        return None
    try:
        answer = Answer(
            question_id=data["question_id"],
            choice=data.get("choice"),
            confidence=data.get("confidence"),
            probabilities={str(k): float(v) for k, v in probabilities.items()},
            model=str(data.get("model", "")),
            input_tokens=int(data.get("input_tokens", 0)),
            cost_usd=float(data.get("cost_usd", 0.0)),
            gen_id=str(data.get("gen_id", "")),
            latency_ms=float(data.get("latency_ms", 0.0)),
        )
    except (TypeError, ValueError):
        return None
    return row["key"], answer


class AnswerCache:
    """Cache of Jev answers stored under `path`; it doubles as the run checkpoint (§2.6, §2.7).

    `bad_lines` counts unreadable lines before the last one (a torn tail is expected after a
    crash and is not counted); the pipeline reports them, since each one is paid for again.
    """

    def __init__(self, path: Path) -> None:
        """Open or create the cache at `path` and load existing lines (DESIGN §2.6)."""
        if path.is_file() or path.suffix == ".jsonl":
            self.file_path = path
        else:
            self.file_path = path / "answers.jsonl"

        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._entries: dict[str, Answer] = {}
        self._writes = 0
        self.bad_lines = 0

        if self.file_path.exists():
            content = self.file_path.read_bytes()
            lines = content.split(b"\n")
            tail = lines.pop()  # b"" when the file ends with a newline
            for raw_line in lines:
                self.bad_lines += self._load(raw_line) != "ok"
            tail_state = self._load(tail) if tail else "ok"
            if tail_state == "torn":
                # A crash mid-write: cut the partial line so it never becomes a bad middle line.
                os.truncate(self.file_path, len(content) - len(tail))
            elif tail:
                self.bad_lines += tail_state == "bad"
                with self.file_path.open("ab") as f:
                    f.write(b"\n")  # a complete last line that only lacked its newline

        self._file = self.file_path.open("a", encoding="utf-8")

    def _load(self, raw_line: bytes) -> Literal["ok", "bad", "torn"]:
        """Load one line: ok (or blank), bad (JSON but not a usable answer) or torn (not JSON)."""
        if not raw_line.strip():
            return "ok"
        try:
            row = json.loads(raw_line.decode("utf-8"))
        except ValueError:  # JSONDecodeError and UnicodeDecodeError
            return "torn"
        loaded = _answer_from_row(row)
        if loaded is None:
            return "bad"
        key, answer = loaded
        self._entries[key] = answer
        return "ok"

    def get(self, key: str) -> Answer | None:
        """Return the cached Answer for `key`, or None (DESIGN §2.6)."""
        return self._entries.get(key)

    def put(
        self,
        key: str,
        answer: Answer,
        *,
        text_hash_a: str,
        text_hash_b: str | None,
        question_hash: str | None = None,
        model_requested: str | None = None,
    ) -> None:
        """Append one cache line in the DESIGN §9 schema and make it visible to get().

        run_jobs always passes question_hash (job.question.hash) and model_requested (client model).
        """
        record: dict[str, Any] = {
            "key": key,
            "question_id": answer.question_id,
            "question_hash": question_hash,
            "model_requested": model_requested,
            "model_resolved": answer.model,
            "text_hash_a": text_hash_a,
            "text_hash_b": text_hash_b,
            "answer": dataclasses.asdict(answer),
            "created_at": datetime.now(UTC).isoformat(),
        }
        line = json.dumps(record, separators=(",", ":")) + "\n"
        self._file.write(line)
        self._file.flush()
        self._writes += 1
        if self._writes % 50 == 0:
            with contextlib.suppress(OSError):
                os.fsync(self._file.fileno())
        self._entries[key] = answer

    def close(self) -> None:
        """Flush and release the cache file."""
        if hasattr(self, "_file") and not self._file.closed:
            self._file.flush()
            with contextlib.suppress(OSError):
                os.fsync(self._file.fileno())
            self._file.close()
