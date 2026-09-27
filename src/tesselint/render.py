"""Story text as sent to Jev (DESIGN §2.1, Rendering)."""

import hashlib
import re
from typing import Literal

from tesselint.models import Story


def clean(text: str) -> str:
    """Strip markup, story refs, `§x`, `(G<n>)`, `(P<n>)` and extra whitespace (DESIGN §2.1)."""
    t = text.replace("**", "").replace("`", "").replace("_", "")
    t = re.sub(r"\[.*?\]", "", t)
    t = re.sub(
        r"\((?:(?:from\s+)?US-[0-9]+(?:\.[0-9]+)*|§\w+|G\d+|P\d+)(?:,\s*(?:(?:from\s+)?US-[0-9]+(?:\.[0-9]+)*|[0-9]+(?:\.[0-9]+)+|§\w+))*\)",
        "",
        t,
    )
    t = re.sub(r"\(G\d+\)", "", t)
    t = re.sub(r"\(P\d+\)", "", t)
    t = re.sub(r"\(§\w+\)", "", t)
    t = re.sub(r"\bUS-[0-9]+(?:\.[0-9]+)+\b", "", t)
    t = re.sub(r"§\w*", "", t)
    return re.sub(r"\s+", " ", t).strip()


def render(story: Story, mode: Literal["full", "core"] = "full") -> str:
    """Return clean(title + ". " + core) plus, in full mode, one `- ` line per sub (DESIGN §2.1)."""
    if story.core:
        first_line = clean(story.title + ". " + story.core)
        bullet_subs = story.subs
    elif story.subs:
        first_line = clean(story.title + ". " + story.subs[0])
        bullet_subs = story.subs[1:]
    else:
        first_line = clean(story.title + ".")
        bullet_subs = ()

    if mode == "core":
        return first_line

    lines = [first_line]
    for sub in bullet_subs:
        cleaned_sub = clean(sub)
        if cleaned_sub:
            lines.append(f"- {cleaned_sub}")
    return "\n".join(lines)


def text_hash(text: str) -> str:
    """Return the hex sha256 of a rendered text, for forward order and cache keys (DESIGN §2.1)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
