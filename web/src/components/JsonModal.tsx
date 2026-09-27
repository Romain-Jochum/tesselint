// Raw JSON modal (docs/ui/DESIGN.md §2 chrome key `x`; STACK.md §1 reference): a focus-trapping
// dialog showing the current object as formatted JSON text. `Dialog` is the shared modal frame
// (the `?` key help uses it too): focus moves in, Tab cycles inside, Esc closes, focus returns.
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import type { S } from "../lib/types.ts";

export interface JsonModalProps {
  title: S;
  value: unknown;
  open: boolean;
  onClose: () => void;
}

export function JsonModal(props: JsonModalProps) {
  if (!props.open) return null;
  return <JsonDialog title={props.title} value={props.value} onClose={props.onClose} />;
}

function JsonDialog({ title, value, onClose }: { title: S; value: unknown; onClose: () => void }) {
  const text = useMemo(() => JSON.stringify(value, null, 2) ?? "undefined", [value]);
  const [copied, setCopied] = useState(false);
  const copy = () => {
    void navigator.clipboard?.writeText(text).then(
      () => setCopied(true),
      () => setCopied(false),
    );
  };
  return (
    <Dialog
      title={title}
      onClose={onClose}
      wide
      actions={
        <button type="button" className="btn btn-sm px-2 py-1 border rounded text-xs" onClick={copy}>
          {copied ? "Copied" : "Copy JSON"}
        </button>
      }
    >
      <pre
        tabIndex={0}
        aria-label="JSON"
        className="max-h-[70vh] overflow-auto rounded-md bg-surface-2 p-3 font-mono text-xs leading-relaxed"
      >
        {text}
      </pre>
    </Dialog>
  );
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

export interface DialogProps {
  title: S;
  onClose: () => void;
  children: ReactNode;
  actions?: ReactNode;
  wide?: boolean;
}

/** A modal dialog: role=dialog, aria-modal, labelled by its title, with a focus trap. */
export function Dialog({ title, onClose, children, actions, wide }: DialogProps) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();

  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    ref.current?.focus();
    return () => previous?.focus();
  }, []);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== "Tab" || !ref.current) return;
    const items = Array.from(ref.current.querySelectorAll<HTMLElement>(FOCUSABLE));
    const first = items[0];
    const last = items[items.length - 1];
    if (!first || !last) return event.preventDefault();
    const active = document.activeElement;
    if (event.shiftKey && (active === first || active === ref.current)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4 backdrop-blur-[1px]"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onKeyDown={onKeyDown}
        className={`dialog w-full rounded-lg border border-border bg-surface shadow-xl ${wide ? "max-w-3xl" : "max-w-lg"}`}
      >
        <div className="flex items-center gap-2 border-b border-border px-4 py-2.5">
          <h2 id={titleId} className="min-w-0 flex-1 truncate text-sm font-semibold">
            {title}
          </h2>
          {actions}
          <button type="button" className="btn btn-ghost btn-sm p-1" onClick={onClose} aria-label="Close dialog">
            ✕
          </button>
        </div>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}
