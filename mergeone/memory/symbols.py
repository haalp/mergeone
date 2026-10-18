"""Exploratory, reporting only (PROTOCOL R5.1-A, "Exploratory, reported, not a hypothesis"): the A2 re-rank with
tree-sitter function-level overlap instead of identifier overlap. Not used in any verdict.

Symbols of a diff: for each changed file in a language with an installed parser (Python, TypeScript/TSX,
JavaScript, Go, C#, Java, Ruby), each hunk is rebuilt as its new side (context + added lines) and its old side
(context + removed lines); both are parsed with tree-sitter (error-tolerant on fragments) and the names of
definition nodes (functions, methods, classes, types…) whose span covers a changed line are collected, plus the
definitions named in the hunk header's function context. Re-rank: 0.5·A1_norm + 0.5·jaccard(symbols), the same
rule as A2, with unfetched candidates keeping their A1 position.
"""
from __future__ import annotations

import re
from functools import lru_cache

from . import retrieval_v2 as R

EXT = {".py": "python", ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
       ".ts": "typescript", ".mts": "typescript", ".cts": "typescript", ".tsx": "tsx", ".go": "go", ".cs": "c_sharp", ".java": "java", ".rb": "ruby"}
DEF_TYPES = {
    "function_definition", "class_definition",  # python
    "function_declaration", "generator_function_declaration", "method_definition", "class_declaration", "abstract_class_declaration",
    "interface_declaration", "type_alias_declaration", "enum_declaration",  # js/ts (+ shared names)
    "method_declaration", "type_spec",  # go
    "constructor_declaration", "struct_declaration", "record_declaration", "property_declaration",  # c#/java
    "method", "singleton_method", "class", "module",  # ruby
}
_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@ ?(.*)$")


@lru_cache(maxsize=None)
def _parser(lang: str):
    from tree_sitter import Language, Parser
    if lang == "python":
        import tree_sitter_python as m; L = m.language()
    elif lang == "javascript":
        import tree_sitter_javascript as m; L = m.language()
    elif lang == "typescript":
        import tree_sitter_typescript as m; L = m.language_typescript()
    elif lang == "tsx":
        import tree_sitter_typescript as m; L = m.language_tsx()
    elif lang == "go":
        import tree_sitter_go as m; L = m.language()
    elif lang == "c_sharp":
        import tree_sitter_c_sharp as m; L = m.language()
    elif lang == "java":
        import tree_sitter_java as m; L = m.language()
    elif lang == "ruby":
        import tree_sitter_ruby as m; L = m.language()
    else:
        raise KeyError(lang)
    return Parser(Language(L))


def available() -> bool:
    try:
        for lang in set(EXT.values()):
            _parser(lang)
        return True
    except Exception:
        return False


def describe() -> dict:
    return {"status": "available" if available() else "not available", "languages": sorted(set(EXT.values())),
            "method": __doc__.strip().split("\n\n")[1].replace("\n", " ")}


def _lang(path: str) -> str | None:
    for ext, lang in EXT.items():
        if path.endswith(ext):
            return lang
    return None


def _defs(lang: str, text: str, rows: set[int] | None) -> set[str]:
    tree = _parser(lang).parse(text.encode("utf-8", "replace"))
    out, stack = set(), [tree.root_node]
    while stack:
        n = stack.pop()
        if n.type in DEF_TYPES:
            name = n.child_by_field_name("name")
            if name is not None and (rows is None or any(n.start_point[0] <= r <= n.end_point[0] for r in rows)):
                out.add(name.text.decode("utf-8", "replace"))
        if n.type == "variable_declarator":  # const f = () => … / function () …
            v, name = n.child_by_field_name("value"), n.child_by_field_name("name")
            if v is not None and name is not None and v.type in ("arrow_function", "function_expression", "function") and (
                    rows is None or any(n.start_point[0] <= r <= n.end_point[0] for r in rows)):
                out.add(name.text.decode("utf-8", "replace"))
        stack.extend(n.children)
    return out


def symbols_of_patch(path: str, patch: str) -> set[str]:
    lang = _lang(path or "")
    if lang is None or not patch:
        return set()
    out = set()
    hunks, cur = [], None
    for line in patch.split("\n"):
        m = _HUNK.match(line)
        if m:
            cur = {"ctx": m.group(3), "lines": []}
            hunks.append(cur)
        elif cur is not None:
            cur["lines"].append(line)
    for h in hunks:
        if h["ctx"].strip():
            out |= _defs(lang, h["ctx"], None)
        for side, mark in (("new", "+"), ("old", "-")):
            text, rows = [], set()
            for line in h["lines"]:
                if not line:
                    text.append(""); continue
                c = line[0]
                if c == " " or c == mark:
                    if c == mark:
                        rows.add(len(text))
                    text.append(line[1:])
            if rows:
                out |= _defs(lang, "\n".join(text), rows)
    return out


def symbols_of_diff(diff: str) -> set[str]:
    """Rendered `git diff` text (the dataset row's DIFF block) → symbols, file by file."""
    out, path, buf = set(), None, []
    for line in (diff or "").split("\n"):
        if line.startswith("diff --git "):
            if path:
                out |= symbols_of_patch(path, "\n".join(buf))
            m = re.match(r"diff --git a/(.*) b/(.*)$", line)
            path, buf = (m.group(2) if m else None), []
        elif line.startswith(("--- ", "+++ ", "index ", "new file", "deleted file", "similarity", "rename ", "old mode", "new mode")) and not buf:
            continue
        else:
            buf.append(line)
    if path:
        out |= symbols_of_patch(path, "\n".join(buf))
    return out


def symbols_of_files(files: list[dict]) -> set[str]:
    out = set()
    for f in files:
        out |= symbols_of_patch(f.get("filename") or "", (f.get("patch") or "")[:4000])
    return out


_cache: dict = {}


def rerank_numbers(top20: list[dict], diff: str, repo: str) -> list[int]:
    from ..data import patches as P
    qs = frozenset(symbols_of_diff(diff))
    cs = {}
    for c in top20:
        key = (repo, c["number"])
        if key not in _cache:
            rec = P.load_patch(repo, c["number"])
            _cache[key] = frozenset(symbols_of_files(rec["files"])) if rec and rec.get("status") == "ok" else None
        cs[c["number"]] = _cache[key]
    return [c["number"] for c in R.rerank_a2(top20, qs, cs)]
