// Readable labels for keys, relations and conflicts (docs/ui/DESIGN.md §2 Review "Rows show ID,
// title…", the conflict strip, the merge pane's relation in words; §3 keys). Pure helpers: views
// never show a raw `mg:`/`split:`/`overlap:` key to the reader.

import type { Kind, MergeDetails, N, Probs, Relation, S } from "./types.ts";

export const KIND_LABEL: Readonly<Record<Kind, S>> = { merge: "Merge", split: "Split", overlap: "Overlap" };

/** Looks up a story title by ID (undefined when unknown). */
export type TitleOf = (id: S) => S | undefined;

/** Kind and story IDs of a case or proposal key: `mg:<drop>`, `merge:<keep>><drop>`, `split:<id>`, `overlap:<a>+<b>…`. */
export function parseKey(key: S): { kind: Kind; ids: S[] } | null {
  const at = key.indexOf(":");
  const rest = key.slice(at + 1);
  switch (key.slice(0, at)) {
    case "mg":
      return { kind: "merge", ids: [rest] };
    case "merge":
      return { kind: "merge", ids: rest.split(">") };
    case "split":
      return { kind: "split", ids: [rest] };
    case "overlap":
      return { kind: "overlap", ids: rest.split("+") };
    default:
      return null;
  }
}

/** The Review case a proposal key belongs to (§4.3): `mg:<drop>` for merges, else the key itself. */
export function caseKeyOf(key: S): S {
  const merge = /^merge:[^>]+>(.+)$/.exec(key);
  return merge ? `mg:${merge[1]}` : key;
}

export interface KeyLabel {
  kind: S; // "Merge", "Split", "Overlap"
  ids: S; // "US-6.3", "US-6.3 into US-6.2", "US-1.2, US-4.3"
  title: S | null; // the story the case is about, when there is one
  text: S; // `Merge · US-6.3 "Sign up for a work party"`
}

/** A readable label for a case or proposal key; overlaps list their members and carry no title. */
export function keyLabel(key: S, titleOf: TitleOf): KeyLabel {
  const parsed = parseKey(key);
  if (!parsed) return { kind: "", ids: key, title: null, text: key };
  const { kind, ids } = parsed;
  const [first = "", second = ""] = ids;
  const isMergeProposal = key.startsWith("merge:");
  const idText = kind === "overlap" ? ids.join(", ") : isMergeProposal ? `${second} into ${first}` : first;
  const subject = isMergeProposal ? second : first;
  const title = kind === "overlap" ? null : (titleOf(subject) ?? null);
  const text = `${KIND_LABEL[kind]} · ${idText}${title ? ` “${title}”` : ""}`;
  return { kind: KIND_LABEL[kind], ids: idText, title, text };
}

/** "the split proposal on US-6.2 “…”" and friends: one conflicting case or proposal in words. */
export function conflictPhrase(key: S, titleOf: TitleOf): S {
  const parsed = parseKey(key);
  if (!parsed) return key;
  const [first = "", second = ""] = parsed.ids;
  const quoted = (id: S) => (titleOf(id) ? ` “${titleOf(id)}”` : "");
  if (key.startsWith("merge:")) return `the merge of ${second} into ${first}`;
  if (parsed.kind === "merge") return `the merge group for ${first}${quoted(first)}`;
  if (parsed.kind === "split") return `the split proposal on ${first}${quoted(first)}`;
  return `the overlap of ${parsed.ids.join(", ")}`;
}

/** The conflict strip's sentence: one conflict with its title, several grouped by kind. */
export function conflictText(keys: readonly S[], titleOf: TitleOf): S {
  if (keys.length === 1) return `Conflicts with ${conflictPhrase(keys[0]!, titleOf)}`;
  const merges: S[] = [];
  const splits: S[] = [];
  const parts: S[] = [];
  for (const key of keys) {
    const parsed = parseKey(key);
    if (parsed?.kind === "merge") merges.push(parsed.ids[parsed.ids.length - 1]!);
    else if (parsed?.kind === "split") splits.push(parsed.ids[0]!);
    else parts.push(conflictPhrase(key, titleOf));
  }
  const plural = (n: N) => (n > 1 ? "s" : "");
  if (merges.length) parts.unshift(`the merge group${plural(merges.length)} for ${merges.join(", ")}`);
  if (splits.length) parts.push(`the split proposal${plural(splits.length)} on ${splits.join(", ")}`);
  return `Conflicts with ${parts.join(" and ")}`;
}

/** A relation between A (the earlier story in document order) and B, in words. */
export function relationLabel(relation: Relation, a: S, b: S): S {
  switch (relation) {
    case "duplicate":
      return "duplicate";
    case "A_contains_B":
      return `${a} contains ${b}`;
    case "B_contains_A":
      return `${b} contains ${a}`;
    case "partial_overlap":
      return "partial overlap";
    case "same_area_distinct":
      return "same area, distinct";
    case "unrelated":
      return "unrelated";
  }
}

/** The merge's relation in words: "US-6.2 contains US-6.3" or "US-8.1 duplicates US-7.1". */
export function mergeRelationWords(d: Pick<MergeDetails, "relation" | "keep" | "drop">): S {
  return d.relation === "duplicate" ? `${d.drop} duplicates ${d.keep}` : `${d.keep} contains ${d.drop}`;
}

/** The n most likely relations, highest first (ties keep the Relation order); null probs give []. */
export function topProbs(probs: Probs | null, n = 2): [Relation, N][] {
  if (!probs) return [];
  const entries = Object.entries(probs) as [Relation, N][];
  return entries
    .map((entry, index) => ({ entry, index }))
    .sort((x, y) => y.entry[1] - x.entry[1] || x.index - y.index)
    .slice(0, n)
    .map(({ entry }) => entry);
}
