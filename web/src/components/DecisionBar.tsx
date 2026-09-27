// Sticky decision bar (docs/ui/DESIGN.md §2 Review keys and "A failed save rolls back and offers
// Retry"; §3 records): accept, reject one/all, defer, note (Ctrl+Enter saves), undo; shows the
// save state and rev. It only builds DecisionChange batches; the caller posts them.
import { useEffect, useId, useRef, useState } from "react";
import { useHotkeys, type HotkeyAction } from "../lib/hotkeys.ts";
import { parseKey } from "../lib/labels.ts";
import { TID } from "../lib/testids.ts";
import type { Action, DecisionChange, N, S } from "../lib/types.ts";

export interface DecisionBarProps {
  caseKey: S;
  optionKeys: S[]; // the case's proposal keys, strongest first
  selected: S; // the option key the bar acts on
  current: Record<S, { action?: Action | null; note?: S }>;
  saveState: "idle" | "saving" | "saved" | "failed";
  rev: N | null;
  onSubmit: (changes: DecisionChange[]) => void;
  onUndo: () => void;
  onRetry: () => void;
  /** Additive: a message for the live region from the caller (undo, failures); a new id shows it. */
  notice?: { id: N; text: S };
  /** Additive navigation hotkeys handled when DecisionBar is active */
  onNext?: () => void;
  onPrev?: () => void;
  onPick?: (optNum: number) => void;
  onConflict?: () => void;
}

/** "merge US-14.5 into US-1.1", "split US-17.3", "overlap US-1.2 + US-4.3" from a proposal key (§3). */
export function describeKey(key: S): S {
  const colon = key.indexOf(":");
  if (colon === -1) return key;
  const kind = key.slice(0, colon);
  const rest = key.slice(colon + 1);
  if (kind === "merge") {
    const [keep, drop] = rest.split(">");
    return `merge ${drop ?? "?"} into ${keep ?? "?"}`;
  }
  if (kind === "overlap") return `overlap ${rest.split("+").join(" + ")}`;
  return `${kind} ${rest}`;
}

const PAST: Record<Action, S> = { accept: "Accepted", reject: "Rejected", defer: "Deferred" };

export function DecisionBar(props: DecisionBarProps) {
  const { caseKey, optionKeys, selected, current, saveState, rev, onSubmit, onUndo, onRetry, notice } = props;
  const [message, setMessage] = useState("");
  const [noticeId, setNoticeId] = useState(notice?.id);
  const [noteOpen, setNoteOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const noteRef = useRef<HTMLTextAreaElement>(null);
  const noteButton = useRef<HTMLButtonElement>(null);
  const noteId = useId();
  const disabled = selected === "";

  if (notice && notice.id !== noticeId) {
    setNoticeId(notice.id);
    setMessage(notice.text);
  }

  const [noteFor, setNoteFor] = useState(selected);
  if (noteFor !== selected) {
    setNoteFor(selected);
    setNoteOpen(false);
  }

  useEffect(() => {
    if (noteOpen) noteRef.current?.focus();
  }, [noteOpen]);

  const decide = (action: Action) => {
    if (disabled) return;
    onSubmit([{ key: selected, action }]);
    setMessage(`${PAST[action]} ${describeKey(selected)} · u to undo`);
  };

  const rejectAll = () => {
    if (disabled || optionKeys.length === 0) return;
    onSubmit(optionKeys.map((key) => ({ key, action: "reject" as const })));
    const what = optionKeys.length === 1 ? describeKey(optionKeys[0]!) : `all ${optionKeys.length} options for ${parseKey(caseKey)?.ids.join(", ") ?? caseKey}`;
    setMessage(`Rejected ${what} · u to undo`);
  };

  const openNote = () => {
    if (disabled) return;
    setDraft(current[selected]?.note ?? "");
    setNoteOpen(true);
  };

  const closeNote = () => {
    setNoteOpen(false);
    noteButton.current?.focus();
  };

  const saveNote = () => {
    onSubmit([{ key: selected, note: draft }]);
    setMessage(`Saved the note on ${describeKey(selected)} · u to undo`);
    closeNote();
  };

  const undo = () => {
    onUndo();
    setMessage("Undoing the last change…");
  };

  const onAction = (action: HotkeyAction) => {
    switch (action.type) {
      case "accept":
        return decide("accept");
      case "reject":
        return decide("reject");
      case "rejectAll":
        return rejectAll();
      case "defer":
        return decide("defer");
      case "note":
        return openNote();
      case "undo":
        return undo();
      case "save":
        if (noteOpen) saveNote();
        return;
      case "escape":
        if (noteOpen) closeNote();
        return;
      case "next":
        props.onNext?.();
        return;
      case "prev":
        props.onPrev?.();
        return;
      case "pick":
        props.onPick?.(action.option);
        return;
      case "conflict":
        props.onConflict?.();
        return;
      default:
        return;
    }
  };

  useHotkeys("review", onAction);
  const action = current[selected]?.action ?? null;

  return (
    <div data-testid={TID.decisionBar} className="decision-bar border-t border-border bg-surface px-3 py-2">
      {noteOpen && (
        <div className="mb-2">
          <label htmlFor={noteId} className="mb-1 block text-xs font-medium text-text-2">
            Note on {describeKey(selected)} <span className="font-normal text-muted">· Ctrl+Enter saves · Esc cancels</span>
          </label>
          <div className="flex items-start gap-2">
            <textarea
              id={noteId}
              ref={noteRef}
              data-testid={TID.noteInput}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                  e.preventDefault();
                  saveNote();
                } else if (e.key === "Escape") {
                  e.preventDefault();
                  closeNote();
                }
              }}
              rows={2}
              className="field min-h-[3.25rem] flex-1 resize-y border rounded p-1"
              placeholder="Why, or how to split it: one part per line"
            />
            <button type="button" className="btn btn-primary px-3 py-1 border rounded" onClick={saveNote}>
              Save note
            </button>
            <button type="button" className="btn px-3 py-1 border rounded" onClick={closeNote}>
              Cancel
            </button>
          </div>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-1.5">
        <button
          type="button"
          data-testid={TID.acceptButton}
          aria-keyshortcuts="a"
          aria-pressed={action === "accept"}
          disabled={disabled}
          onClick={() => decide("accept")}
          className="btn btn-accept px-2.5 py-1 border rounded font-medium"
        >
          Accept <kbd className="text-xs opacity-75">a</kbd>
        </button>
        <button
          type="button"
          data-testid={TID.rejectButton}
          aria-keyshortcuts="r"
          aria-pressed={action === "reject"}
          disabled={disabled}
          onClick={() => decide("reject")}
          className="btn px-2.5 py-1 border rounded font-medium"
        >
          Reject <kbd className="text-xs opacity-75">r</kbd>
        </button>
        <button
          type="button"
          data-testid={TID.rejectAllButton}
          aria-keyshortcuts="Shift+R"
          disabled={disabled}
          onClick={rejectAll}
          className="btn px-2.5 py-1 border rounded font-medium"
        >
          Reject all <kbd className="text-xs opacity-75">R</kbd>
        </button>
        <button
          type="button"
          data-testid={TID.deferButton}
          aria-keyshortcuts="d"
          aria-pressed={action === "defer"}
          disabled={disabled}
          onClick={() => decide("defer")}
          className="btn px-2.5 py-1 border rounded font-medium"
        >
          Defer <kbd className="text-xs opacity-75">d</kbd>
        </button>
        <button
          type="button"
          ref={noteButton}
          data-testid={TID.noteButton}
          aria-keyshortcuts="n"
          aria-expanded={noteOpen}
          disabled={disabled}
          onClick={() => (noteOpen ? closeNote() : openNote())}
          className="btn px-2.5 py-1 border rounded font-medium"
        >
          Note <kbd className="text-xs opacity-75">n</kbd>
        </button>
        <button
          type="button"
          data-testid={TID.undoButton}
          aria-keyshortcuts="u"
          onClick={undo}
          className="btn px-2.5 py-1 border rounded font-medium"
        >
          Undo <kbd className="text-xs opacity-75">u</kbd>
        </button>
        <p
          data-testid={TID.liveRegion}
          role="status"
          aria-live="polite"
          className="ml-2 min-w-0 flex-1 truncate text-xs text-text-2"
          title={message}
        >
          {message}
        </p>
        <span className="flex shrink-0 items-center gap-2 text-xs text-muted tabular-nums">
          {saveState === "saving" && <span>saving…</span>}
          {saveState === "failed" && (
            <>
              <span className="flex items-center gap-1 font-medium text-red-600">
                Save failed · rolled back
              </span>
              <button type="button" data-testid={TID.retryButton} onClick={onRetry} className="btn btn-sm px-2 py-0.5 border rounded">
                Retry
              </button>
            </>
          )}
          {saveState !== "failed" && rev !== null && <span>saved · rev {rev}</span>}
        </span>
      </div>
    </div>
  );
}
