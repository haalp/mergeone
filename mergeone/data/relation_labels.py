"""Run 5 human labels for PR relations (PROTOCOL R5.1-B). Frozen before any merged-PR comment was fetched.

A PR is `related_to = N` with `relation = conflicts` when a non-bot comment matches CONFLICT_RE and captures N,
`relation = depends` when it matches DEPENDS_RE. Supersedes are run 4's labels and are not re-labelled here;
a PR that matches SUPERSEDE_RE (run 4) is skipped for that match. A match without a number is `relation_unknown`.
Anything else: `not_marked`.
"""
from __future__ import annotations

import re

from .supersede_labels import SUPERSEDE_RE, is_human

_CONFLICT = (
    r"conflicts? with", r"conflicting with", r"(?:will|would|is going to) conflict with", r"merge conflicts? (?:with|against)",
    r"collides? with", r"clashes? with", r"incompatible with", r"breaks? (?:when|if) .{0,30}merges?", r"can'?t (?:both )?(?:land|merge) (?:with|alongside)",
)
_DEPENDS = (
    r"depends? on", r"dependent on", r"blocked (?:by|on)", r"blocks? on", r"rebase (?:on(?:to)?|after|once)", r"needs? to (?:be )?rebased? (?:on(?:to)?|after)",
    r"(?:after|once|when) .{0,30}(?:lands|merges|is merged|is in)", r"wait(?:ing)? (?:for|on|until)", r"needs? .{0,10}first", r"requires?",
    r"based on", r"stacked on", r"built on(?: top of)?", r"follow-?up (?:to|of)", r"prerequisite",
)
CONFLICT_RE = re.compile(r"(?i)(?:" + "|".join(_CONFLICT) + r")[^#\n]{0,60}#(\d{2,7})")
DEPENDS_RE = re.compile(r"(?i)(?:" + "|".join(_DEPENDS) + r")[^#\n]{0,60}#(\d{2,7})")
UNKNOWN_RE = re.compile(r"(?i)(?:" + "|".join(_CONFLICT + _DEPENDS[:6]) + r")")


def label(comments: list[dict], self_number: int) -> tuple[str, str | None, int | None]:
    """comments: [{body, author}] in time order. Returns (label, relation, number).
    label ∈ {related_to, relation_unknown, not_marked}; relation ∈ {conflicts, depends, None}."""
    for c in comments:
        if not is_human(c.get("author")):
            continue
        body = c.get("body") or ""
        superseded = {int(m.group(1)) for m in SUPERSEDE_RE.finditer(body)}
        for rel, rx in (("conflicts", CONFLICT_RE), ("depends", DEPENDS_RE)):
            for m in rx.finditer(body):
                n = int(m.group(1))
                if n != self_number and n not in superseded:
                    return "related_to", rel, n
    for c in comments:
        if is_human(c.get("author")) and UNKNOWN_RE.search(c.get("body") or ""):
            return "relation_unknown", None, None
    return "not_marked", None, None
