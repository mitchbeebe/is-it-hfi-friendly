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

import itertools
import math
from collections.abc import Sequence

from . import analyze, sugars
from .models import Item, Load, Result, Serving
from .sugars import Status, key

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


# Unless the person sets their own, under this fructose load in the amount eaten a food is tolerated: the
# lowest vegetable tier in HFI guidance, 0.5 g per 100 g, and the strictest cut-off Italian centres use for
# vegetables (Izquierdo-García 2026, doi:10.3390/nu18050771; Bruni 2026, doi:10.3390/nu18162609). It's half
# the low end of the 1 to 1.5 g daily limit guidance gives (40 mg/kg).
DEFAULT_OK = 0.5
# Where Avoid starts unless the person sets their own limit: the low end of that daily limit in one serving.
DEFAULT_LIMIT = 1.0
FRUCTOSE_SUCROSE = {"fructose", "sucrose"}


def _tier(load: float, ok: float, limit: float, serving: Serving | None, under: bool = False) -> Status:
    """Status for a fructose load per 100 g, judged in the amount eaten: the serving when it's known,
    else 100 g. Under `ok` is OK and under `limit` Caution; a limit under `ok` lowers OK to it.

    `under`: the true load is under `load`, as with a label's total sugars, so `load` itself reaching a
    cut-off still counts as under it."""
    # Rounded, so a label's 0.5 g, converted to per 100 g and back, is 0.5 g again.
    eaten = round(load * serving.grams / 100 if serving is not None else load, 9)

    def below(cut: float) -> bool:
        return eaten <= cut if under else eaten < cut

    return "tolerated" if below(min(ok, limit)) else "caution" if below(limit) else "not_tolerated"


def most_load(label: Sequence[Item], counted: Sequence[Item]) -> float:
    """The most fructose load (g per 100 g of the product) `counted` can add up to, given where they are on
    the `label`, which lists every ingredient with the most it can be of the product (`share`).

    Each ingredient's own bound, "listed 8th, so at most 1/8", can't hold for all of them at once: for the
    8th to be 1/8, so is each of the seven before it. Shares that fall down the list and sum to 1 are a
    mix of "the first m in equal parts", so the most is at one of those; with a "2% or less" section, at
    a mix of two that has the section at its most. Falls back to adding each one's own bound when the
    label doesn't fit that, as when the section starts with the first ingredient.
    """
    alone = fructose_load(counted)
    loads = [fructose_load([i]) / i.share if i in counted and i.share else 0.0 for i in label]
    on_label = [i for i in counted if i in label]
    # Where the "2% or less" section starts (0-based) and its share: a bound tighter than the position gives.
    caps = {n: i.share for n, i in enumerate(label) if i.share is not None and i.share < 1 / (n + 1) - 1e-9}
    if not on_label or len(set(caps.values())) > 1:
        return alone
    start, cap = next(iter(caps.items()), (len(label), 1.0))
    prefix = list(itertools.accumulate(loads))  # prefix[m - 1]: the first m together

    # Weights t[m] on "the first m in equal parts" (each 1/m), with sum of m * t[m] = 1, and the section's
    # share, the sum of t[m] for m past its start, at most `cap`.
    def value(t: dict[int, float]) -> float:
        return sum(w * prefix[m - 1] for m, w in t.items())

    n = len(label)
    found = [prefix[m - 1] / m for m in range(1, n + 1) if m <= start or 1 / m <= cap]
    for a in range(1, n + 1):
        for b in range(max(a + 1, start + 1), n + 1):
            # The section at its most: t[b] (and t[a], when a is in it too) adds up to `cap`.
            tb = cap if a <= start else (1 - a * cap) / (b - a)
            ta = (1 - b * tb) / a if a <= start else cap - tb
            if ta >= 0 and tb >= 0:
                found.append(value({a: ta, b: tb}))
    if not found:
        return alone
    off_label = fructose_load([i for i in counted if i not in label])
    return min(alone, max(found) + off_label)


def apply_amounts(
    items: list[Item], ok: float, limit: float, serving: Serving | None
) -> tuple[list[Item], Load | None]:
    """Weigh measured amounts: each item by its own amount, and the product by all of them together.

    Fructose and sucrose together are under the label's total sugars, so that caps them, including
    sugars an ingredient list names with no amount. Sorbitol isn't counted in total sugars, so it needs
    its own measurement. Returns the items and the product's load, or None when something flagged has
    no amount to weigh. A limit of 0 flags any amount, so nothing is downgraded.

    Foods the person tolerates don't count, but the load counting them is kept too (`with_allowed`).
    """
    cap = serving.max_sugars * 100 / serving.grams if serving and serving.max_sugars is not None else None
    label = [i for i in items if i.share is not None]
    every = [i for i in items if i.status == "not_tolerated"]
    flagged = [i for i in every if not i.allowed_by]
    fs = [i for i in flagged if i.sugars and set(i.sugars) <= FRUCTOSE_SUCROSE]
    sorbitol = [i for i in flagged if "sorbitol" in i.sugars]

    def capped(load: float) -> bool:
        return cap is not None and load >= cap

    def own(i: Item) -> float:
        load = fructose_load([i]) if i.grams is not None else math.inf
        return min(load, cap) if cap is not None and i in fs else load

    def tier(load: float, under: bool = False) -> Status:
        if limit <= 0:
            return "not_tolerated" if load > 0 else "tolerated"
        return _tier(load, ok, limit, serving, under)

    def total(flagged: list[Item]) -> Load | None:
        """The product's load from these flagged items, or None when one has no amount to weigh."""
        fs = [i for i in flagged if i.sugars and set(i.sugars) <= FRUCTOSE_SUCROSE]
        sorbitol = [i for i in flagged if "sorbitol" in i.sugars]
        if any(i not in fs and i not in sorbitol for i in flagged) or any(i.grams is None for i in sorbitol):
            return None
        fs_load = math.inf if any(i.grams is None for i in fs) else most_load(label, fs)
        # Each bound holds, so the lower one does: fructose and sucrose under total sugars, plus sorbitol,
        # or all of them together on the label.
        with_cap = (min(fs_load, cap) if cap is not None else fs_load) + most_load(label, sorbitol)
        together = most_load(label, fs + sorbitol) if fs_load < math.inf else math.inf
        grams = min(with_cap, together)
        if grams == math.inf:
            return None
        under = capped(fs_load) and with_cap <= together
        return Load(
            grams=round(grams * serving.grams / 100 if serving else grams, 4),
            per=serving.label if serving else "100 g",
            status=tier(grams, under),
            sugars=[s for s in analyze.SUGAR_ORDER if any(s in i.sugars for i in flagged)],
            capped=under,
        )

    weighed = [i for i in fs + sorbitol if own(i) < math.inf]
    out = []
    for i in items:
        if i in weighed and not i.avoided_by and tier(own(i), i in fs and capped(own(i))) != "not_tolerated":
            # It has the sugar, so it's never Tolerated itself; whether the product is depends on the load.
            i = i.model_copy(update={"status": "caution", "trace": True})
        out.append(i)

    load = total(flagged) if weighed else None
    allowed = [i for i in every if i.allowed_by and not i.avoided_by]
    if allowed and (full := total(every)) is not None and full.grams > (load.grams if load else 0):
        if load is None and not flagged:
            # Everything with sugar is a food they tolerate.
            load = Load(grams=0, per=full.per, status="tolerated", sugars=[])
        if load is not None:
            terms = list(dict.fromkeys(i.allowed_by for i in allowed if i.allowed_by))
            load = load.model_copy(update={"with_allowed": full, "allowed": terms})
    return out, load


def apply(
    result: Result,
    allow: Sequence[str] = (),
    avoid: Sequence[str] = (),
    query: str | None = None,
    trace_limit: float = 0,
    ok_limit: float = DEFAULT_OK,
    portion: int | None = None,
) -> Result:
    """`portion` picks one of a whole food's household measures to judge it by, like a label's serving."""
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

    serving = result.serving
    if serving is None and portion is not None and 0 <= portion < len(result.portions):
        p = result.portions[portion]
        serving = Serving(grams=p.grams, label=p.label)
    # Amounts are weighed after the lists, so allowed items don't count toward the load.
    items, load = apply_amounts(items, ok_limit, trace_limit, serving)
    notes = list(result.notes)
    if allowed := [i for i in items if i.allowed_by and not i.avoided_by and i.status != "tolerated"]:
        notes.append(f"Not counting {_count(allowed)} you've allowed in Settings.")
    if avoided := [i for i in items if i.avoided_by]:
        notes.append(f"Flagging {_count(avoided)} on your avoid list.")
    keep = {
        k: getattr(result, k)
        for k in ("food", "measured_food", "off_product", "ingredients_text", "portions", "alternatives")
    }
    return analyze.summarize(items, notes=notes, load=load, serving=serving, **keep)


def _count(items: list[Item]) -> str:
    return f"{len(items)} item{'s' if len(items) > 1 else ''}"
