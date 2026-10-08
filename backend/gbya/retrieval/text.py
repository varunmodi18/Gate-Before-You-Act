"""Tokenisation and the technique-ID hold-out shared by the indexes and the query (plan §D.3).

Indexed text and queries go through the same ``tokenise``: lower-case, split on every
non-alphanumeric character (so paths split into their components). ``strip_technique_ids``
removes ATT&CK tags (``attack.*``) and literal technique IDs (``T1234``, ``T1234.001``) from any
text before it is indexed, so retrieval can only work by content (§L.4 item 10).
"""

from __future__ import annotations

import re

# Literal technique IDs, case-insensitive, not glued to other alphanumerics ("xt1003" is kept).
TECHNIQUE_ID = re.compile(r"(?<![A-Za-z0-9])[Tt]\d{4}(?:\.\d{3})?(?![0-9])")
ATTACK_TAG = re.compile(r"(?i)(?<![A-Za-z0-9])attack\.[A-Za-z0-9_.-]+")
# ATT&CK descriptions: markdown links keep their text; citations are dropped.
MD_LINK = re.compile(r"\[([^\]]*)\]\((?:https?://)[^)]*\)")
CITATION = re.compile(r"\(Citation:[^)]*\)")
URL = re.compile(r"https?://\S+")
# Brackets left empty by removed IDs, e.g. "( )" or "(, )", with the space before them.
EMPTY_PARENS = re.compile(r"[ \t]*\([ \t]*[,;]?[ \t]*\)")
_TOKEN = re.compile(r"[a-z0-9]+")


def strip_technique_ids(text: str) -> str:
    text = ATTACK_TAG.sub(" ", text)
    return TECHNIQUE_ID.sub(" ", text)


def clean_attack_text(text: str) -> str:
    """ATT&CK prose: links reduced to their text, citations and bare URLs removed."""
    text = MD_LINK.sub(r"\1", text)
    text = CITATION.sub("", text)
    return URL.sub(" ", text)


def tidy(text: str) -> str:
    """Drop empty brackets and whitespace runs left by the removals; keep line breaks single."""
    text = EMPTY_PARENS.sub("", text)
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def tokenise(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def held_out_violations(text: str) -> list[str]:
    """Technique tags or IDs still present in ``text`` (the hold-out test, T3.1)."""
    return [m.group(0) for m in (*ATTACK_TAG.finditer(text), *TECHNIQUE_ID.finditer(text))]
