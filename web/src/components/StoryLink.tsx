// Story ID link (docs/ui/DESIGN.md §2 "Every story ID links to its detail, title on hover"):
// a monospace ID linking to /r/:run/story/:id.
import { href } from "../lib/router.ts";
import type { S } from "../lib/types.ts";

export interface StoryLinkProps {
  run: S;
  id: S;
  title?: S;
  className?: S;
}

export function StoryLink(props: StoryLinkProps) {
  return (
    <a
      href={href({ name: "story", run: props.run, id: props.id })}
      title={props.title}
      className={props.className ?? "font-mono font-semibold hover:underline"}
    >
      {props.id}
    </a>
  );
}
