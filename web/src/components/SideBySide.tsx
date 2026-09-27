// Two story texts side by side (docs/ui/DESIGN.md §2 Merge pane; §5 "Phrases"): shared phrases
// as <mark> spans (hovering one lights its twin), optional captions (bundled scores, roles) and,
// for stale cases, a word diff of stored against current text. Text is always rendered as text.
import { useMemo, useState } from "react";
import { sharedPhrases, toSegments, wordDiff, type Span } from "../lib/phrases.ts";
import { ATTR, TID } from "../lib/testids.ts";
import type { N, S, StoryRow } from "../lib/types.ts";
import { StoryLink } from "./StoryLink.tsx";

export interface SideBySideProps {
  run: S;
  a: StoryRow;
  b: StoryRow;
  captions?: [S, S];
  oldTexts?: Record<S, S>; // stored texts of stale stories, by ID
  className?: S; // additive: extra classes on the grid
}

export function SideBySide(props: SideBySideProps) {
  const { run, a, b, captions, oldTexts } = props;
  const phrases = useMemo(() => sharedPhrases(a.text, b.text), [a.text, b.text]);
  const [active, setActive] = useState<N | null>(null);
  return (
    <div className={`grid min-h-0 grid-cols-2 gap-3 ${props.className ?? ""}`}>
      <StoryColumn
        testId={TID.sideA}
        run={run}
        story={a}
        caption={captions?.[0]}
        spans={phrases.a}
        oldText={oldTexts?.[a.id]}
        active={active}
        onActive={setActive}
      />
      <StoryColumn
        testId={TID.sideB}
        run={run}
        story={b}
        caption={captions?.[1]}
        spans={phrases.b}
        oldText={oldTexts?.[b.id]}
        active={active}
        onActive={setActive}
      />
    </div>
  );
}

export interface StoryColumnProps {
  run: S;
  story: StoryRow;
  caption?: S;
  spans?: readonly Span[];
  oldText?: S; // stored text: shown as a word diff against the current one
  active?: N | null;
  onActive?: (run: N | null) => void;
  testId?: S;
}

/** One story: ID link, title and caption, then its text (marked phrases) or its word diff. */
export function StoryColumn(props: StoryColumnProps) {
  const { run, story, caption, spans = [], oldText, active = null, onActive, testId } = props;
  const stale = oldText !== undefined && oldText !== story.text;
  return (
    <section
      data-testid={testId}
      aria-label={`${story.id} ${story.title}`}
      className="flex min-h-0 min-w-0 flex-col rounded-lg border border-border bg-surface"
    >
      <header className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5 border-b border-border px-3 py-2">
        <StoryLink run={run} id={story.id} title={story.title} className="text-sm font-semibold" />
        <span className="min-w-0 flex-1 truncate font-medium" title={story.title}>
          {story.title}
        </span>
        {caption && <span className="text-xs text-text-2 tabular-nums">{caption}</span>}
      </header>
      <div
        role="region"
        aria-label={`${story.id} text`}
        tabIndex={0}
        className="story-text min-h-0 flex-1 overflow-auto px-3 py-2"
      >
        {stale ? (
          <>
            <p className="mb-1.5 flex items-center gap-1 text-xs font-medium text-amber-800 dark:text-amber-300">
              Edited since it was decided: struck words were removed, underlined ones added.
            </p>
            <WordDiff oldText={oldText} newText={story.text} />
          </>
        ) : (
          <MarkedText text={story.text} spans={spans} active={active} onActive={onActive} />
        )}
      </div>
    </section>
  );
}

/** Text with shared phrases as <mark data-run>; hovering one marks its twin active. */
export function MarkedText(props: { text: S; spans: readonly Span[]; active: N | null; onActive?: (run: N | null) => void }) {
  const { text, spans, active, onActive } = props;
  const segments = useMemo(() => toSegments(text, spans), [text, spans]);
  return (
    <p className="whitespace-pre-wrap">
      {segments.map((segment, index) =>
        segment.run === null ? (
          <span key={index}>{segment.text}</span>
        ) : (
          <mark
            key={index}
            {...{ [ATTR.run]: segment.run, [ATTR.active]: active === segment.run ? "true" : undefined }}
            onPointerEnter={() => onActive?.(segment.run)}
            onMouseEnter={() => onActive?.(segment.run)}
            onPointerLeave={() => onActive?.(null)}
            onMouseLeave={() => onActive?.(null)}
          >
            {segment.text}
          </mark>
        ),
      )}
    </p>
  );
}

/** Word diff of a stored text against the current one (§2 stale cases): <del> and <ins>. */
export function WordDiff({ oldText, newText }: { oldText: S; newText: S }) {
  const parts = useMemo(() => wordDiff(oldText, newText), [oldText, newText]);
  return (
    <p data-testid={TID.wordDiff} className="word-diff whitespace-pre-wrap">
      {parts.map((part, index) =>
        part.op === "same" ? (
          <span key={index}>{part.text}</span>
        ) : part.op === "add" ? (
          <ins key={index}>{part.text}</ins>
        ) : (
          <del key={index}>{part.text}</del>
        ),
      )}
    </p>
  );
}
