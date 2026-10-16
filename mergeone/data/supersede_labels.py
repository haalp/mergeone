"""Run 4 human labels for "superseded" (PROTOCOL R4.2). Frozen before any comment was fetched.

A closed-unmerged PR is `superseded_by = N` when a non-bot comment on it matches SUPERSEDE_RE and
captures a PR number N. A match without a number is `superseded_unknown`. Anything else: `not_marked`.
"""
from __future__ import annotations

import re

# phrases a maintainer uses when closing in favour of other work, followed (within 60 chars) by #N
_PHRASES = (
    r"duplicate(?:s|d)? of", r"dup(?:e)? of", r"\bdup\b", r"superseded by", r"supersedes", r"superceded by",
    r"already (?:fixed|done|merged|addressed|landed|handled|implemented|covered|resolved)(?: in| by)?",
    r"closing (?:this )?in favou?r of", r"in favou?r of", r"replaced by", r"covered by", r"obsoleted by",
    r"(?:was|has been|is) (?:fixed|resolved|merged|done) (?:in|by|via)", r"same as", r"overlaps? with",
    r"landed (?:in|via|as)", r"merged (?:in|via|as)",
)
SUPERSEDE_RE = re.compile(r"(?i)(?:" + "|".join(_PHRASES) + r")[^#\n]{0,60}#(\d{2,7})")
SUPERSEDE_NO_NUMBER_RE = re.compile(r"(?i)(?:" + "|".join(_PHRASES[:12]) + r")")

BOT_MARKERS = ("[bot]", "-bot", "bot-", "github-actions", "copilot", "devin-ai", "cursor", "codex", "claude")


def is_human(login: str | None) -> bool:
    l = (login or "").lower()
    return bool(l) and not any(m in l for m in BOT_MARKERS)


def label(comments: list[dict], self_number: int) -> tuple[str, int | None]:
    """comments: [{body, author}] in time order. Returns (label, superseding_number)."""
    for c in comments:
        if not is_human(c.get("author")):
            continue
        body = c.get("body") or ""
        for m in SUPERSEDE_RE.finditer(body):
            n = int(m.group(1))
            if n != self_number:
                return "superseded_by", n
    for c in comments:
        if is_human(c.get("author")) and SUPERSEDE_NO_NUMBER_RE.search(c.get("body") or ""):
            return "superseded_unknown", None
    return "not_marked", None
