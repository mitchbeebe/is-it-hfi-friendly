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
# "contains 2% or less of", "less than 2% of": everything after is at most that share of the product.
_AT_MOST = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*or\s+less\s+of|less\s+than\s+(\d+(?:\.\d+)?)\s*%\s+of", re.IGNORECASE)
# A share the label declares for one ingredient: "Wholewheat Flour (80%)", "tomatoes 45%".
_DECLARED = re.compile(r"^(?:.*\s)?\(?(\d+(?:\.\d+)?)\s*%\)?$")


@dataclass(frozen=True)
class Ingredient:
    name: str
    share: float = 1.0  # the most this ingredient can be of the product, from 0 to 1
    why: str = ""  # how `share` is known, e.g. "listed 3rd"


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


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


def parse(text: str) -> list[Ingredient]:
    """Top-level ingredients. Each keeps its bracketed part, which describes it: "vegetable oil (canola,
    soybean)" and "chocolate chips (sugar, cocoa butter)" stay whole.

    Labels list ingredients by weight, most first, so the nth is at most 1/n of the product. A
    "2% or less" section, or a declared percentage, can bound it more tightly.
    """
    text = _balance(_TRAILER.sub("", text.strip()).rstrip(" ."))
    found: dict[str, Ingredient] = {}
    at_most: float | None = None
    for part in _split_top(text):
        if m := _AT_MOST.search(part):
            at_most = float(m.group(1) or m.group(2))
        name = _clean(part)
        if not name or name in found:
            continue
        n = len(found) + 1
        share, why = 1 / n, "the first ingredient" if n == 1 else f"listed {_ordinal(n)}"
        if at_most is not None and at_most / 100 < share:
            share, why = at_most / 100, f"listed under {at_most:g}% or less"
        base, inner = split(name)
        declared = _DECLARED.match(base) or (len(inner) == 1 and _DECLARED.match(f"({inner[0]})"))
        if declared:
            share, why = float(declared.group(1)) / 100, "as declared"
        found[name] = Ingredient(name, min(share, 1.0), why)
    return list(found.values())


def split(ingredient: str) -> tuple[str, list[str]]:
    """ "enriched flour (wheat flour, niacin)" -> ("enriched flour", ["wheat flour", "niacin"])."""
    base, groups, buf, depth = [], [], [], 0
    for ch in _balance(ingredient):
        if ch in _BRACKETS:
            depth += 1
            if depth == 1:
                continue
        elif ch in _BRACKETS.values() and depth:
            depth -= 1
            if depth == 0:
                groups.append("".join(buf))
                buf = []
                continue
        (buf if depth else base).append(ch)
    inner = [name for g in groups for part in _split_top(g) if (name := _clean(part))]
    return _clean("".join(base)), inner
