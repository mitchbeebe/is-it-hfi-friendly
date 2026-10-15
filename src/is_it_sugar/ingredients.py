"""Split a label's ingredient statement into individual ingredients."""

import re
from dataclasses import dataclass

# Section labels that introduce more ingredients, e.g. "contains 2% or less of:".
_LEAD_IN = re.compile(
    r"^(?:[a-z0-9% ]{1,40}\bof\b|[a-z ]{1,30}):\s*|^(?:ingredients?|contains \d+% or less of)\s+", re.IGNORECASE
)
# Allergen statements and similar trailers aren't ingredients.
_TRAILER = re.compile(
    r"(?:^|[.;]\s*)(?:allergens?|contains|may contain|manufactured in|produced in)\b\s*:[^.]*\.?\s*$", re.IGNORECASE
)
_BRACKETS = {"(": ")", "[": "]", "{": "}"}


@dataclass(frozen=True)
class Ingredient:
    name: str
    parent: str | None = None


def looks_like_list(text: str) -> bool:
    t = text.strip().lower()
    return t.startswith("ingredient") or t.count(",") >= 1 or t.count(";") >= 1


def _split_top(text: str) -> list[str]:
    """Split on , ; and sentence periods that aren't inside brackets."""
    parts, buf, stack = [], [], []
    for i, ch in enumerate(text):
        if ch in _BRACKETS:
            stack.append(_BRACKETS[ch])
        elif stack and ch == stack[-1]:
            stack.pop()
        elif not stack and (ch in ",;" or (ch == "." and not text[i + 1 : i + 2].isdigit())):
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    return parts


def _balance(text: str) -> str:
    """Close brackets a label left open (common in OCR'd lists), so one can't swallow the rest of the list."""
    stack: list[str] = []
    for ch in text:
        if ch in _BRACKETS:
            stack.append(_BRACKETS[ch])
        elif stack and ch == stack[-1]:
            stack.pop()
    return text + "".join(reversed(stack))


def _clean(name: str) -> str:
    name = re.sub(r"\s+", " ", name).strip(" .*:†‡-")
    name = re.sub(r"^(and/or|and|or)\s+", "", name, flags=re.IGNORECASE)
    while (m := _LEAD_IN.match(name)) and m.end() < len(name):
        name = name[m.end() :]
    return name.strip(" .*:")


def parse(text: str, parent: str | None = None) -> list[Ingredient]:
    if parent is None:
        text = _balance(_TRAILER.sub("", text.strip()))
    out: list[Ingredient] = []
    for part in _split_top(text):
        m = re.match(r"^(.*?)[(\[{](.*)[)\]}]\s*(.*)$", part, re.DOTALL)
        if m:
            base = _clean(f"{m.group(1)} {m.group(3)}")
            inner = m.group(2)
            if base:
                out.append(Ingredient(base, parent))
            # "(color)" or "(to preserve freshness)" are notes, not sub-ingredients.
            if "," in inner or ";" in inner or not base:
                out.extend(parse(inner, base or parent))
            elif base:
                out[-1] = Ingredient(f"{base} ({inner.strip()})", parent)
        elif name := _clean(part):
            out.append(Ingredient(name, parent))
    return list(dict.fromkeys(out))
