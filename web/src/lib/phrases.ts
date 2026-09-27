// Shared-phrase highlighting and word diffs (docs/ui/DESIGN.md §5 "Phrases"; stale word diff §2
// Review, edited stories §2 Runs). Spans render as <mark>, never via innerHTML.

import type { N, S } from "./types.ts";

/** Token pattern (§5); tokens are lower-cased. */
export const TOKEN_RE = /[\p{L}\p{N}]+(?:['’-][\p{L}\p{N}]+)*/gu;

/** Shortest shared run, in tokens (§5). */
export const MIN_RUN = 3;

/** Above this many DP cells (len(a) × len(b)), fall back to shared FALLBACK_NGRAM-grams (§5). */
export const DP_CELL_LIMIT = 250_000;
export const FALLBACK_NGRAM = 4;

/** A run made only of these tokens is never highlighted (§5). */
export const STOPWORDS: ReadonlySet<S> = new Set([
  "a", "about", "after", "all", "also", "an", "and", "any", "are", "as", "at", "be", "been",
  "before", "but", "by", "can", "could", "did", "do", "does", "each", "for", "from", "had", "has",
  "have", "he", "her", "his", "how", "i", "if", "in", "into", "is", "it", "its", "it's", "just",
  "me", "more", "my", "no", "not", "of", "on", "one", "only", "or", "other", "our", "out", "own",
  "she", "so", "some", "such", "than", "that", "the", "their", "them", "then", "there", "these",
  "they", "this", "those", "to", "too", "up", "us", "very", "was", "we", "were", "what", "when",
  "where", "which", "who", "why", "will", "with", "would", "you", "your",
]);

/** A token with its character offsets in the original text; `text` is lower-cased. */
export interface Token {
  text: S;
  start: N;
  end: N;
}

/** A highlighted character range [start, end); `run` pairs it with its twin in the other text. */
export interface Span {
  start: N;
  end: N;
  run: N;
}

/** Text cut into plain and highlighted pieces, in order; concatenated they equal the input. */
export interface Segment {
  text: S;
  run: N | null;
}

/** A word-diff piece: kept, added (only in the new text) or deleted (only in the old). */
export interface DiffPart {
  op: "same" | "add" | "del";
  text: S;
}

/** Split text into tokens with TOKEN_RE (§5). */
export function tokenize(text: S): Token[] {
  return Array.from(text.matchAll(TOKEN_RE), (m) => ({ text: m[0].toLowerCase(), start: m.index, end: m.index + m[0].length }));
}

/** A matched run: `len` tokens from index i in a and j in b. */
interface Run {
  i: N;
  j: N;
  len: N;
}

const hasContent = (tokens: readonly Token[], from: N, len: N) =>
  tokens.slice(from, from + len).some((t) => !STOPWORDS.has(t.text));

/** Longest common run of unused tokens holding at least one non-stopword (earliest in a on ties). */
function longestRun(a: readonly Token[], b: readonly Token[], usedA: boolean[], usedB: boolean[]): Run | null {
  // Two rolling rows: run length and non-stopword count of the run ending at (i, j).
  let prevLen = new Uint32Array(b.length + 1);
  let prevContent = new Uint32Array(b.length + 1);
  let len = new Uint32Array(b.length + 1);
  let content = new Uint32Array(b.length + 1);
  let best: Run | null = null;
  for (let i = 1; i <= a.length; i++) {
    len.fill(0);
    content.fill(0);
    const ta = a[i - 1]!;
    if (!usedA[i - 1]) {
      const word = STOPWORDS.has(ta.text) ? 0 : 1;
      for (let j = 1; j <= b.length; j++) {
        if (usedB[j - 1] || b[j - 1]!.text !== ta.text) continue;
        len[j] = prevLen[j - 1]! + 1;
        content[j] = prevContent[j - 1]! + word;
        if (content[j]! > 0 && len[j]! >= MIN_RUN && len[j]! > (best?.len ?? 0)) best = { i: i - len[j]!, j: j - len[j]!, len: len[j]! };
      }
    }
    [prevLen, len] = [len, prevLen];
    [prevContent, content] = [content, prevContent];
  }
  return best;
}

/** Runs by repeated longest-common-run extraction (§5); each token is used at most once. */
function dpRuns(a: readonly Token[], b: readonly Token[]): Run[] {
  const usedA = new Array<boolean>(a.length).fill(false);
  const usedB = new Array<boolean>(b.length).fill(false);
  const runs: Run[] = [];
  for (let run = longestRun(a, b, usedA, usedB); run; run = longestRun(a, b, usedA, usedB)) {
    runs.push(run);
    usedA.fill(true, run.i, run.i + run.len);
    usedB.fill(true, run.j, run.j + run.len);
  }
  return runs;
}

/** Fallback above DP_CELL_LIMIT (§5): seed on shared FALLBACK_NGRAM-grams, extend greedily. */
function ngramRuns(a: readonly Token[], b: readonly Token[]): Run[] {
  const gram = (tokens: readonly Token[], at: N) =>
    tokens
      .slice(at, at + FALLBACK_NGRAM)
      .map((t) => t.text)
      .join(" ");
  const seeds = new Map<S, N[]>();
  for (let j = 0; j + FALLBACK_NGRAM <= b.length; j++) {
    const key = gram(b, j);
    seeds.set(key, [...(seeds.get(key) ?? []), j]);
  }
  const usedB = new Array<boolean>(b.length).fill(false);
  const runs: Run[] = [];
  for (let i = 0; i + FALLBACK_NGRAM <= a.length; ) {
    let best: Run | null = null;
    for (const j of seeds.get(gram(a, i)) ?? []) {
      let len = 0;
      while (i + len < a.length && j + len < b.length && !usedB[j + len] && a[i + len]!.text === b[j + len]!.text) len++;
      if (len >= FALLBACK_NGRAM && len > (best?.len ?? 0)) best = { i, j, len };
    }
    if (best && hasContent(a, best.i, best.len)) {
      runs.push(best);
      usedB.fill(true, best.j, best.j + best.len);
      i += best.len;
    } else {
      i++;
    }
  }
  return runs;
}

/**
 * Shared phrases of a and b (§5): repeatedly take the longest common run of ≥ MIN_RUN tokens,
 * skipping stopword-only runs; above DP_CELL_LIMIT use shared FALLBACK_NGRAM-grams. Twins share
 * a `run` index; spans are sorted by start and never overlap.
 */
export function sharedPhrases(a: S, b: S): { a: Span[]; b: Span[] } {
  const ta = tokenize(a);
  const tb = tokenize(b);
  const runs = ta.length * tb.length > DP_CELL_LIMIT ? ngramRuns(ta, tb) : dpRuns(ta, tb);
  const spans = (tokens: readonly Token[], at: (r: Run) => N) =>
    runs
      .map((r, run) => ({ start: tokens[at(r)]!.start, end: tokens[at(r) + r.len - 1]!.end, run }))
      .sort((x, y) => x.start - y.start);
  return { a: spans(ta, (r) => r.i), b: spans(tb, (r) => r.j) };
}

/** Cut text into Segments by spans (for <mark> rendering). */
export function toSegments(text: S, spans: readonly Span[]): Segment[] {
  const segments: Segment[] = [];
  let at = 0;
  for (const span of [...spans].sort((x, y) => x.start - y.start)) {
    const start = Math.max(span.start, at);
    const end = Math.min(span.end, text.length);
    if (end <= start) continue;
    if (start > at) segments.push({ text: text.slice(at, start), run: null });
    segments.push({ text: text.slice(start, end), run: span.run });
    at = end;
  }
  if (at < text.length || segments.length === 0) segments.push({ text: text.slice(at), run: null });
  return segments;
}

/** Above this many LCS cells, the changed middle is shown as one deletion plus one addition. */
const DIFF_CELL_LIMIT = 1_000_000;

/** Words with their trailing whitespace, so parts keep the text's own spacing and line breaks. */
const chunks = (text: S): S[] => text.trimStart().match(/\S+\s*/g) ?? [];

function pushPart(parts: DiffPart[], op: DiffPart["op"], text: S): void {
  const last = parts[parts.length - 1];
  if (last?.op === op) last.text += text;
  else parts.push({ op, text });
}

/**
 * Word diff of a stored text against the current one (§2 stale cases, run diff edits). Words
 * compare exactly (case and punctuation count) and keep their trailing whitespace, so the same +
 * add parts concatenate to the new text (leading whitespace dropped); render with pre-wrap.
 */
export function wordDiff(oldText: S, newText: S): DiffPart[] {
  const olds = chunks(oldText);
  const news = chunks(newText);
  const word = (chunk: S) => chunk.trimEnd();
  let head = 0;
  while (head < olds.length && head < news.length && word(olds[head]!) === word(news[head]!)) head++;
  let tail = 0;
  while (
    tail < olds.length - head &&
    tail < news.length - head &&
    word(olds[olds.length - 1 - tail]!) === word(news[news.length - 1 - tail]!)
  ) {
    tail++;
  }
  const a = olds.slice(head, olds.length - tail);
  const b = news.slice(head, news.length - tail);
  const parts: DiffPart[] = [];
  for (const chunk of news.slice(0, head)) pushPart(parts, "same", chunk);
  if (a.length * b.length > DIFF_CELL_LIMIT) {
    for (const chunk of a) pushPart(parts, "del", chunk);
    for (const chunk of b) pushPart(parts, "add", chunk);
  } else {
    // lcs[i][j]: LCS length of a[i:] and b[j:], then walk it forwards.
    const width = b.length + 1;
    const lcs = new Uint32Array((a.length + 1) * width);
    for (let i = a.length - 1; i >= 0; i--) {
      for (let j = b.length - 1; j >= 0; j--) {
        lcs[i * width + j] =
          word(a[i]!) === word(b[j]!)
            ? lcs[(i + 1) * width + j + 1]! + 1
            : Math.max(lcs[(i + 1) * width + j]!, lcs[i * width + j + 1]!);
      }
    }
    let i = 0;
    let j = 0;
    while (i < a.length || j < b.length) {
      if (i < a.length && j < b.length && word(a[i]!) === word(b[j]!)) {
        pushPart(parts, "same", b[j++]!);
        i++;
      } else if (j < b.length && (i === a.length || lcs[i * width + j + 1]! >= lcs[(i + 1) * width + j]!)) {
        pushPart(parts, "add", b[j++]!);
      } else {
        pushPart(parts, "del", a[i++]!);
      }
    }
  }
  for (const chunk of news.slice(news.length - tail)) pushPart(parts, "same", chunk);
  return parts;
}
