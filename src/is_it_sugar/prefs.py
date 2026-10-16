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

import math
from collections.abc import Sequence

from . import analyze, sugars
from .models import Item, Load, Result, Serving
from .sugars import Status, Sugar, key

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
# Per serving, when the label gives one. Guidance puts the daily limit at 1 to 1.5 g fructose load, so a
# serving under a tenth of the low end is tolerated, and up to half of it is Caution.
SERVING_LOW = 0.1
SERVING_MAX = 0.5
FRUCTOSE_SUCROSE = {"fructose", "sucrose"}


def _tier(load: float, limit: float, serving: Serving | None) -> Status:
    """Status for a fructose load per 100 g: judged per serving when the serving is known."""
    if serving is not None:
        per_serving = load * serving.grams / 100
        return (
            "tolerated" if per_serving < SERVING_LOW else "caution" if per_serving <= SERVING_MAX else "not_tolerated"
        )
    return "tolerated" if load < min(LOW_LOAD, limit) else "caution" if load < limit else "not_tolerated"


def apply_amounts(items: list[Item], limit: float, serving: Serving | None) -> tuple[list[Item], Load | None]:
    """Weigh measured amounts: each item by its own amount, and the product by all of them together.

    Fructose and sucrose together can't exceed the label's total sugars, so that caps them, including
    sugars an ingredient list names with no amount. Sorbitol isn't counted in total sugars, so it needs
    its own measurement. Returns the items and the product's load, or None when something flagged has
    no amount to weigh. A limit of 0 flags any amount, so nothing is downgraded.
    """
    cap = serving.max_sugars * 100 / serving.grams if serving and serving.max_sugars is not None else None
    flagged = [i for i in items if i.status == "not_tolerated" and not i.allowed_by]
    fs = [i for i in flagged if i.sugars and set(i.sugars) <= FRUCTOSE_SUCROSE]
    sorbitol = [i for i in flagged if "sorbitol" in i.sugars]

    def own(i: Item) -> float:
        load = fructose_load([i]) if i.grams is not None else math.inf
        return min(load, cap) if cap is not None and i in fs else load

    def tier(load: float) -> Status:
        return _tier(load, limit, serving) if limit > 0 else "not_tolerated" if load > 0 else "tolerated"

    weighed = [i for i in fs + sorbitol if own(i) < math.inf]
    out = []
    for i in items:
        if i in weighed and not i.avoided_by and (status := tier(own(i))) != "not_tolerated":
            i = i.model_copy(update={"status": status, "trace": True})
        out.append(i)

    fs_load = math.inf if any(i.grams is None for i in fs) else fructose_load(fs)
    total = (min(fs_load, cap) if cap is not None else fs_load) + fructose_load(sorbitol)
    unweighable = [i for i in flagged if i not in fs and i not in sorbitol] + [i for i in sorbitol if i.grams is None]
    if unweighable or not weighed or total == math.inf:
        return out, None
    sugars: list[Sugar] = [s for s in analyze.SUGAR_ORDER if any(s in i.sugars for i in weighed)]
    return out, Load(
        grams=round(total * serving.grams / 100 if serving else total, 4),
        per=serving.label if serving else "100 g",
        status=tier(total),
        sugars=sugars,
        capped=cap is not None and fs_load > cap,
    )


def apply(
    result: Result,
    allow: Sequence[str] = (),
    avoid: Sequence[str] = (),
    query: str | None = None,
    trace_limit: float = 0,
) -> Result:
    allow_terms, avoid_terms = terms(allow), terms(avoid)

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
        for i in result.items
    ]
    term = food_name and (_contains(food_name, avoid_terms) or _contains(query or "", avoid_terms))
    if term and not any(i.avoided_by == term for i in items):
        reason = "This food is on your avoid list."
        items.append(Item(name=food_name, status="not_tolerated", basis="settings", avoided_by=term, reason=reason))

    # Amounts are weighed after the lists, so allowed items don't count toward the load.
    items, load = apply_amounts(items, trace_limit, result.serving)
    notes = list(result.notes)
    if allowed := [i for i in items if i.allowed_by and not i.avoided_by and i.status != "tolerated"]:
        notes.append(f"Not counting {_count(allowed)} you've allowed in Settings.")
    if avoided := [i for i in items if i.avoided_by]:
        notes.append(f"Flagging {_count(avoided)} on your avoid list.")
    keep = {
        k: getattr(result, k)
        for k in ("food", "measured_food", "off_product", "ingredients_text", "serving", "alternatives")
    }
    return analyze.summarize(items, notes=notes, load=load, **keep)


def _count(items: list[Item]) -> str:
    return f"{len(items)} item{'s' if len(items) > 1 else ''}"
