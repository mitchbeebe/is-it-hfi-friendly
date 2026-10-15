"""Apply a person's own lists: foods they tolerate (allow) and ingredients they avoid.

Allowed items stay in the result, marked with `allowed_by`, but don't count toward the verdict.
Allow matching errs toward not allowing:

- A BU table sugar is allowed only by one of its own names, so allowing "corn" never allows
  "corn syrup" or "high fructose corn syrup".
- Any other ingredient is allowed when it contains the term: "onion" allows "onion powder".
- A whole food without an ingredient list is allowed only when its main name is the term:
  "broccoli" allows "Broccoli, raw", but "tomato" doesn't allow "Tomato soup".

Avoided items are marked with `avoided_by` and always count as not tolerated. Avoid matching
errs toward flagging: any item or food whose name contains the term, BU sugars included
("corn" flags "corn syrup"). When a term is on both lists, avoid wins.
"""

from collections.abc import Sequence

from . import analyze, sugars
from .models import Item, Result
from .sugars import key

MAX_TERMS, MAX_TERM_LEN = 200, 80


def terms(raw: Sequence[str]) -> dict[str, str]:
    """{matching key: term as the person wrote it}, capped and with blanks dropped."""
    return {key(t): t.strip() for t in (t[:MAX_TERM_LEN] for t in raw[:MAX_TERMS]) if key(t)}


def _contains(text: str, terms: dict[str, str]) -> str | None:
    padded = f" {key(text)} "
    return next((term for k, term in terms.items() if f" {k} " in padded), None)


def _allowed(item: Item, allow: dict[str, str]) -> str | None:
    if item.bu_name:
        entry = sugars.BY_NAME[item.bu_name]
        return next((allow[k] for k in map(key, entry.names) if k in allow), None)
    return _contains(item.name, allow)


def _avoided(item: Item, avoid: dict[str, str]) -> str | None:
    names = [item.name, *(sugars.BY_NAME[item.bu_name].names if item.bu_name else ())]
    return item.avoided_by or next((t for n in names if (t := _contains(n, avoid))), None)


def fructose_load(items: Sequence[Item]) -> float:
    """Fructose + half of sucrose (it splits into glucose and fructose) + sorbitol (converted to fructose),
    in g per 100 g, from measured amounts."""
    return sum((i.grams or 0) * (0.5 if i.sugars == ["sucrose"] else 1) for i in items)


# Under this fructose load per 100 g, a food is tolerated: the lowest tier in Spanish HFI guidance and
# the strictest cut-off Italian centres use (Izquierdo-García 2026, doi:10.3390/nu18050771; Bruni 2026,
# doi:10.3390/nu18162609).
LOW_LOAD = 0.5


def apply_trace(items: list[Item], limit: float) -> list[Item]:
    """Measured amounts whose combined fructose load is under `limit` become Caution instead of Avoid,
    and Tolerated when also under LOW_LOAD.

    Only measured values qualify. If any sugar is unmeasured but judged likely, or an ingredient
    list names a sugar, the food stays Avoid: there's no amount to weigh.
    """
    measured = [i for i in items if i.grams is not None and i.status == "not_tolerated"]
    unmeasured = [i for i in items if i.grams is None and i.status == "not_tolerated"]
    load = fructose_load(measured)
    if not measured or unmeasured or load >= limit:
        return items
    if load < min(LOW_LOAD, limit):
        status, note = "tolerated", f"About {load:.2g} g fructose load per 100 g, under {LOW_LOAD:g} g. "
    else:
        status, note = "caution", f"About {load:.2g} g fructose load per 100 g, under your {limit:g} g limit. "
    return [
        i.model_copy(update={"status": status, "trace": True, "reason": note + i.reason})
        if i.grams is not None and i.status == "not_tolerated"
        else i
        for i in items
    ]


def apply(
    result: Result,
    allow: Sequence[str] = (),
    avoid: Sequence[str] = (),
    query: str | None = None,
    trace_limit: float = 0,
) -> Result:
    allow_terms, avoid_terms = terms(allow), terms(avoid)
    traced = apply_trace(result.items, trace_limit) if trace_limit > 0 else result.items
    if not allow_terms and not avoid_terms and traced is result.items:
        return result

    # What the food itself is called, when the check was for a food rather than a pasted list.
    if result.food:
        food_name = result.food.description
    elif result.off_product:
        food_name = result.off_product.name
    else:
        food_name = query if result.ingredients_text is None else None

    whole_food_allowed = None
    if food_name and result.ingredients_text is None:
        # Survey/SR foods are named "Broccoli, raw", "Bananas, ripe and slightly ripe", ...
        names = [query or "", food_name.split(",")[0]]
        whole_food_allowed = next((allow_terms[key(n)] for n in names if key(n) in allow_terms), None)

    items = [
        i.model_copy(
            update={
                "allowed_by": whole_food_allowed or _allowed(i, allow_terms),
                "avoided_by": _avoided(i, avoid_terms),
            }
        )
        for i in traced
    ]
    term = food_name and (_contains(food_name, avoid_terms) or _contains(query or "", avoid_terms))
    if term and not any(i.avoided_by == term for i in items):
        reason = "This food is on your avoid list."
        items.append(Item(name=food_name, status="not_tolerated", basis="settings", avoided_by=term, reason=reason))

    if traced is result.items and not any(i.allowed_by or i.avoided_by for i in items):
        return result
    notes = list(result.notes)
    if allowed := [i for i in items if i.allowed_by and not i.avoided_by and i.status != "tolerated"]:
        notes.append(f"Not counting {_count(allowed)} you've allowed in Settings.")
    if avoided := [i for i in items if i.avoided_by]:
        notes.append(f"Flagging {_count(avoided)} on your avoid list.")
    keep = {k: getattr(result, k) for k in ("food", "afcd_food", "off_product", "ingredients_text", "alternatives")}
    return analyze.summarize(items, notes=notes, **keep)


def _count(items: list[Item]) -> str:
    return f"{len(items)} item{'s' if len(items) > 1 else ''}"
