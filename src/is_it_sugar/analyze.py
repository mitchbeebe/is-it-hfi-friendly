"""Decide whether a food, ingredient list, or label photo contains fructose, sucrose, or sorbitol.

Known sugar names are matched in code against the BU table. TypeSafe's Jev model handles
the judgments code can't make: classifying unfamiliar ingredients, picking the right
FoodData Central entry, estimating whole foods, and reading label photos.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
from collections.abc import Sequence
from typing import Any

import httpx
from typesafe_sdk import AsyncTypeSafeClient, Choice, Image, Noul

from . import afcd, known, off, sugars
from . import ingredients as ing
from .cache import Cache
from .models import AfcdMatch, Alternative, Basis, Food, Item, OffMatch, Result, Verdict
from .sugars import TABLE, Sugar, SugarEntry

FDC_SEARCH = "https://api.nal.usda.gov/fdc/v1/foods/search"
IMAGES_HEADER = {"TypeSafe-Danger-Zone-Images": "true"}
SUGAR_ORDER: list[Sugar] = ["fructose", "sucrose", "sorbitol"]
STATUS_ORDER = ["not_tolerated", "caution", "tolerated"]
GENERIC_TYPES = {"Foundation", "SR Legacy", "Survey (FNDDS)"}
log = logging.getLogger("uvicorn.error")


class NotAFood(ValueError):
    pass


# ---------------------------------------------------------------- ingredient lists

INGREDIENT_CRITERIA = {
    "fructose": "Fructose, or a food that naturally contains fructose: any fruit, dried fruit, fruit juice, "
    "fruit puree or concentrate; vegetables with natural sugars such as tomatoes, onions, carrots, peas, "
    "beets, sweet potatoes, corn; honey; agave; inulin.",
    "sucrose": "Sucrose (table sugar) or an ingredient made with it, e.g. sugar, syrups made from cane or "
    "beet sugar, chocolate, candy, sweetened products.",
    "sorbitol": "Sorbitol, or an ingredient made with sorbitol such as isomalt or polydextrose.",
    "unsure": "Might contain fructose, sucrose or sorbitol depending on how it was made, e.g. an unspecified "
    "flavoring blend or a sugar alcohol other than sorbitol.",
    "none": "Contains no fructose, sucrose or sorbitol, e.g. water, salt, plain flour, starch, oils, plain "
    "meat, eggs, plain milk and cheese, vinegar, acids, vitamins, minerals, leavening, colors, "
    "preservatives, gums.",
    "label_note": "Not a food ingredient: a footnote or other label text that got split off the list, e.g. "
    "'Dried', 'Organic', 'Milk chocolate contains milk solids 20% minimum' or 'Made in a facility that "
    "also processes nuts'.",
}
# Ratings are cached per ingredient, so changing the criteria retires them.
INGREDIENT_VERSION = hashlib.sha256(json.dumps(INGREDIENT_CRITERIA, sort_keys=True).encode()).hexdigest()[:12]
INGREDIENT_NS = f"ingredient:{INGREDIENT_VERSION}"
SAFE_MIN = 0.7  # P(none) needed to call an ingredient free of all three sugars
ALSO_MIN = 0.3  # a second sugar is listed when its probability is at least this


def _bu_item(entry: SugarEntry, name: str, basis: Basis = "bu_table") -> Item:
    return Item(
        name=name,
        status=entry.status,
        sugars=entry.contains,
        basis=basis,
        reason=f"BU table: {entry.name}, {entry.tolerance.lower()}. {entry.description}",
        bu_name=entry.name,
    )


def _known_item(k: known.Known, i: ing.Ingredient) -> Item:
    if k.food is None:
        return Item(
            name=k.name,
            status="tolerated",
            basis="known",
            reason="Contains no sugar: a vitamin, mineral, acid, fat or similar.",
        )
    f = k.food
    values = {"fructose": f.fructose, "sucrose": f.sucrose, "sorbitol": f.sorbitol or 0}
    found = [s for s in SUGAR_ORDER if values[s] > 0]
    if not found:
        return Item(
            name=k.name, status="tolerated", basis=f.source, reason=f"Measured: {f.name} has no fructose or sucrose."
        )
    amounts = ", ".join(f"{'under ' if s in f.below else ''}{values[s]:g} g {s}" for s in found)
    load = sum(values[s] * (0.5 if s == "sucrose" else 1) for s in found) * i.share
    share = "" if i.share == 1 else f" It's {i.why}, so at most {i.share:.0%} of the product:"
    return Item(
        name=k.name,
        status="not_tolerated",
        sugars=found,
        basis=f.source,
        reason=f"Measured: {f.name} has {amounts} per 100 g.{share} up to {load:.2g} g fructose load per 100 g "
        "of the product.",
        # An upper bound for the product. fructose_load halves sucrose itself.
        grams=values["sucrose"] * i.share if found == ["sucrose"] else load,
    )


def _jev_item(name: str, probs: dict[str, float]) -> Item:
    if (p := probs.get("label_note", 0)) >= SAFE_MIN:
        return Item(
            name=name,
            status="tolerated",
            basis="jev",
            reason=f"Looks like a note on the label, not an ingredient ({p:.0%}).",
        )
    # Not sure enough it's a note, so it's judged as an ingredient.
    probs = {k: v for k, v in probs.items() if k != "label_note"}
    choice = max(probs, key=probs.__getitem__)
    found = [s for s in SUGAR_ORDER if probs[s] >= ALSO_MIN or s == choice]
    if choice in SUGAR_ORDER:
        names = " and ".join(found)
        return Item(
            name=name,
            status="not_tolerated",
            sugars=found,
            basis="jev",
            reason=f"Not in the BU table; judged to contain {names} ({probs[choice]:.0%}).",
        )
    if choice == "none" and probs["none"] >= SAFE_MIN:
        return Item(
            name=name,
            status="tolerated",
            basis="jev",
            reason=f"Not in the BU table; judged free of fructose, sucrose and sorbitol ({probs['none']:.0%}).",
        )
    return Item(
        name=name,
        sugars=found if choice != "unsure" else [],
        basis="jev",
        status="caution",
        reason="Not in the BU table, and it may contain fructose, sucrose or sorbitol. Check the product or ask your dietitian.",
    )


INGREDIENT_TTL = 90 * 86400


def _decide(i: ing.Ingredient) -> Item | None:
    """An ingredient's item from the BU table or known foods, or None if it needs the AI model."""
    name = i.name
    # A sugar anywhere counts, including inside brackets: "chocolate chips (sugar, cocoa butter)".
    matches = sugars.match(name)
    flagged = [e for e in matches if e.status != "tolerated"]
    if flagged:
        # The worst match decides; e.g. "glucose-fructose syrup" beats "glucose".
        return _bu_item(min(flagged, key=lambda e: STATUS_ORDER.index(e.status)), name)
    # Otherwise what the ingredient is decides, and the brackets just describe it: "vegetable oil (canola,
    # soybean)", "wholewheat flour (80%)". A tolerated sugar only inside them ("chocolate (dextrose, ...)")
    # says nothing about the rest.
    base, _ = ing.split(name)
    base = re.sub(r"\s*\d+(?:\.\d+)?\s*%$", "", base)  # "tomatoes 45%"
    if base_matches := sugars.match(base):
        return _bu_item(base_matches[0], name)
    if k := known.lookup(base):
        return _known_item(known.Known(name, k.food), i)
    return None


async def classify_ingredients(ts: AsyncTypeSafeClient, text: str, cache: Cache | None = None) -> list[Item]:
    parsed = ing.parse(text)
    items: list[Item | None] = []
    unknown: dict[str, str] = {}
    for i, part in enumerate(parsed):
        name = part.name
        if item := _decide(part):
            items.append(item)
        elif cache and (probs := cache.get(INGREDIENT_NS, sugars.norm(name), INGREDIENT_TTL)):
            items.append(_jev_item(name, probs))
        else:
            items.append(None)
            unknown[f"i{i}"] = name
    if unknown:
        # One question per ingredient, named inline: Jev is far more accurate this way than
        # when asked to look up `ingredients[i]` in a shared list.
        r = await ts.system_one(
            state="Ingredients from a food label, checked one at a time for a hereditary fructose intolerance diet.",
            questions={
                k: Choice(
                    instructions={
                        "ingredient": name,
                        "question": "Which best describes the food ingredient `ingredient`? Anything in brackets "
                        "describes it or lists what it's made of.",
                    },
                    criteria=INGREDIENT_CRITERIA,
                )
                for k, name in unknown.items()
            },
        )
        for k, name in unknown.items():
            probs = dict(r.choices[k].probabilities)
            items[int(k[1:])] = _jev_item(name, probs)
            if cache:
                cache.set(INGREDIENT_NS, sugars.norm(name), probs)
    return [i for i in items if i is not None]


# ---------------------------------------------------------------- food names (FoodData Central)

ESTIMATES: dict[Sugar, str] = {
    "fructose": "Does `food` contain fructose? Fruits, fruit juices, honey, most vegetables and many sweetened foods do.",
    "sucrose": "Does `food` contain sucrose (table sugar)? Many fruits and vegetables contain it naturally, "
    "and most sweetened foods contain added sugar.",
    "sorbitol": "Does `food` contain sorbitol? Apples, pears, stone fruits, dried fruits and many sugar-free "
    "candies and gums do.",
}
LIKELY, POSSIBLE = 0.6, 0.45


def _food_label(f: dict[str, Any]) -> str:
    brand = f.get("brandName") or f.get("brandOwner")
    return " | ".join(x for x in (f["description"], brand, f["dataType"]) if x)


def _nutrients(f: dict[str, Any]) -> dict[str, float]:
    return {n["nutrientName"]: n["value"] for n in f.get("foodNutrients", []) if n.get("value") is not None}


def _has_measured(f: dict[str, Any]) -> bool:
    n = _nutrients(f)
    return "Fructose" in n or "Sucrose" in n


async def _search_type(http: httpx.AsyncClient, query: str, data_types: list[str], size: int) -> list[dict[str, Any]]:
    # POST, because GET with several data types is rejected with a 400 about half the time.
    r = await http.post(
        FDC_SEARCH,
        params={"api_key": os.environ["FOODDATA_CENTRAL_API_KEY"]},
        json={"query": query, "dataType": data_types, "pageSize": size},
    )
    r.raise_for_status()
    return r.json().get("foods", [])


async def _search(http: httpx.AsyncClient, query: str) -> list[dict[str, Any]]:
    """Generic foods, then branded ones. Searched separately because branded products far outnumber
    generic foods and would otherwise crowd them out: "lentils" finds 25 brands and no SR Legacy entry."""
    generic, branded = await asyncio.gather(
        _search_type(http, query, sorted(GENERIC_TYPES), 20),
        _search_type(http, query, ["Branded"], 10),
    )
    return generic + branded


def _measured_twin(chosen: dict[str, Any], foods: list[dict[str, Any]]) -> dict[str, Any]:
    """The same generic food from a dataset that measured its sugars, when the pick didn't.

    Survey (FNDDS) entries usually report only total sugars; SR Legacy often has "Onions, raw" too, with
    fructose and sucrose measured.
    """
    if chosen.get("ingredients") or chosen["dataType"] not in GENERIC_TYPES or _has_measured(chosen):
        return chosen
    name = sugars.norm(chosen["description"])
    twins = (f for f in foods if f["dataType"] in GENERIC_TYPES and sugars.norm(f["description"]) == name)
    return next((f for f in twins if _has_measured(f)), chosen)


async def _pick(
    ts: AsyncTypeSafeClient, query: str, foods: list[dict[str, Any]]
) -> tuple[dict | None, list[dict], bool, bool]:
    """Jev picks the database food the user meant. Returns (food, runners-up, is_food, is_brand)."""
    criteria = {
        f"food_{i}": _food_label(f) + (" | sugars measured" if _has_measured(f) else "") for i, f in enumerate(foods)
    }
    criteria["none"] = "None of these foods is what the user typed."
    questions: dict[str, Any] = {
        "is_food": Noul(instructions="Is `user_typed` the name of a food, drink, dish, or food ingredient?"),
        "is_brand": Noul(
            instructions="Does `user_typed` name a specific brand or branded product (for example Cheerios, Oreos, "
            "Coke or Heinz ketchup), rather than a kind of food?"
        ),
    }
    if foods:
        questions["pick"] = Choice(
            instructions="Which database food best matches the food described in `user_typed`? Prefer a plain, "
            "generic version (SR Legacy, Foundation, or Survey data) unless `user_typed` names a brand or product. "
            "If `user_typed` is a general name such as 'rice', pick its most common plain form, not a dish made with it. "
            "Among equally close matches, prefer one marked 'sugars measured'.",
            criteria=criteria,
        )
    r = await ts.system_one(state={"user_typed": query}, questions=questions)
    is_food = r.nouls["is_food"].noul >= 0.3
    is_brand = r.nouls["is_brand"].noul >= 0.5
    if not foods:
        return None, [], is_food, is_brand
    probs = r.choices["pick"].probabilities
    ranked = [k for k in sorted(probs, key=probs.__getitem__, reverse=True) if k != "none"]
    chosen = None if r.choices["pick"].choice == "none" else foods[int(r.choices["pick"].choice[5:])]
    if is_brand and chosen is not None and not chosen.get("ingredients"):
        # A brand-name product needs its own ingredient list. USDA also has generic-style entries for some
        # brands ("Cereals ready-to-eat, GENERAL MILLS, CHEERIOS") without one, which would hide its sugar.
        listed = [foods[int(k[5:])] for k in ranked if foods[int(k[5:])].get("ingredients")]
        chosen = listed[0] if listed else chosen
    if chosen is not None:
        chosen = _measured_twin(chosen, foods)
    others = [foods[int(k[5:])] for k in ranked if foods[int(k[5:])] is not chosen][:5]
    return chosen, others, is_food, is_brand


async def _estimate(ts: AsyncTypeSafeClient, food: str, which: list[Sugar]) -> list[Item]:
    if not which:
        return []
    r = await ts.system_one(
        state="A food checked for a hereditary fructose intolerance diet.",
        questions={s: Noul(instructions={"food": food, "question": ESTIMATES[s]}) for s in which},
    )
    items = []
    for s in which:
        p = r.nouls[s].noul
        if p >= LIKELY:
            items.append(
                Item(
                    name=s.capitalize(),
                    status="not_tolerated",
                    sugars=[s],
                    basis="jev",
                    reason=f"No ingredient data; judged likely to contain {s} ({p:.0%}).",
                )
            )
        elif p >= POSSIBLE:
            items.append(
                Item(
                    name=s.capitalize(),
                    status="caution",
                    sugars=[s],
                    basis="jev",
                    reason=f"No ingredient data; may contain {s} ({p:.0%}).",
                )
            )
    return items


async def _pick_afcd(ts: AsyncTypeSafeClient, name: str) -> afcd.AfcdFood | None:
    """Jev picks the Australian (AFCD) entry for a whole food, if there is one."""
    candidates = afcd.search(name)
    if not candidates:
        return None
    criteria: dict[str, str | None] = {f"au_{i}": f.name for i, f in enumerate(candidates)}
    criteria["none"] = "None of these is the same food, or `user_typed` is a packaged product or a dish."
    r = await ts.system_one(
        state={"user_typed": name},
        questions={
            "pick": Choice(
                instructions="Which entry from the Australian food composition database is the same food as "
                "`user_typed`? Pick the plain, raw version unless `user_typed` says how it is prepared, or the plain cooked "
                "one when only cooked versions are listed. If "
                "`user_typed` is a general name such as 'rice' or 'cheese', pick its most common plain form.",
                criteria=criteria,
            )
        },
    )
    choice = r.choices["pick"].choice
    return None if choice == "none" else candidates[int(choice[3:])]


def measured_sugars(nutrients: dict[str, float], au: afcd.AfcdFood | None) -> tuple[list[Item], list[Sugar]]:
    """One item per measured sugar (zeros included), and which sugars were measured.

    Per sugar, Australian (AFCD) values win over USDA ones: they're mostly lab-analysed and
    include sorbitol. AFCD reports every variety of a food, so amounts are shown as a range.
    """
    items: list[Item] = []
    measured: list[Sugar] = []
    no_sugar = nutrients.get("Total Sugars") == 0 or (au is not None and au.total_sugars == 0)
    for s in SUGAR_ORDER:
        label = s.capitalize()
        if au is not None and getattr(au, s) is not None:
            kinds = [v for v in afcd.varieties(au) if getattr(v, s) is not None]
            values = [getattr(v, s) for v in kinds]
            lo, hi = min(values), max(values)
            basis: Basis = "afcd"
            if lo != hi:
                amount = f"{lo:g}–{hi:g} g"
                form = f" ({au.form})" if au.form else ""
                reason = f"Range across {len(kinds)} kinds of {au.first}{form}. Closest match: {au.name}, {getattr(au, s):g} g."
            else:
                amount = f"{hi:g} g"
                reason = f"Measured: {au.name} ({au.derivation.lower()})."
        elif (grams := nutrients.get(label)) is not None:
            hi, amount, basis, reason = grams, f"{grams:g} g", "fdc", "Measured by USDA."
        else:
            continue
        measured.append(s)
        if hi > 0:
            items.append(
                Item(
                    name=f"{label}: {amount} per 100 g",
                    status="not_tolerated",
                    sugars=[s],
                    basis=basis,
                    reason=reason,
                    grams=hi,
                )
            )
        elif not no_sugar:
            items.append(Item(name=f"{label}: 0 g per 100 g", status="tolerated", basis=basis, reason="None measured."))
    if no_sugar:
        measured += [s for s in ("fructose", "sucrose") if s not in measured]
        items.insert(
            0,
            Item(
                name="Total sugars: 0 g per 100 g",
                status="tolerated",
                basis="afcd" if au is not None and au.total_sugars == 0 else "fdc",
                # Sugar alcohols such as sorbitol aren't counted in total sugars.
                reason="No fructose or sucrose. Sorbitol isn't counted in total sugars; it's checked separately.",
            ),
        )
    return items, measured


def known_sorbitol(names: Sequence[str]) -> list[Item]:
    """Sorbitol for a whole food with no sorbitol measurement, from which foods AFCD finds it in.

    `names` are the food's names in each database: AFCD calls apple juice "Juice, apple", USDA "Apple juice".
    """
    if not any(known.is_sorbitol_fruit(n) for n in names):
        return []
    return [
        Item(
            name="Sorbitol",
            status="not_tolerated",
            sugars=["sorbitol"],
            basis="afcd",
            reason="Not measured for this entry, but apples, pears and stone fruits contain sorbitol "
            "(measured in Australian data).",
        )
    ]


def unidentified_sugars(
    nutrients: dict[str, float], au: afcd.AfcdFood | None, measured: list[Sugar], items: list[Item]
) -> list[Item]:
    """A caution item when the data shows sugar but not which kind, or no sugar data at all, so "OK" never
    rests on a guess alone."""
    total = au.total_sugars if au is not None and au.total_sugars is not None else nutrients.get("Total Sugars")
    explained = all(s in measured for s in ("fructose", "sucrose")) or any(
        i.status == "not_tolerated" and {"fructose", "sucrose"} & set(i.sugars) for i in items
    )
    if total == 0 or explained:
        return []
    if total is None:
        return [
            Item(
                name="Sugars: not measured",
                status="caution",
                basis="afcd" if au is not None else "fdc",
                reason="This entry has no sugar measurements, so nothing confirms it's free of fructose and sucrose.",
            )
        ]
    return [
        Item(
            name=f"Sugars: {total:g} g per 100 g",
            status="caution",
            basis="afcd" if au is not None and au.total_sugars is not None else "fdc",
            reason="The data shows sugar but not which kinds, so some of it may be fructose or sucrose. "
            "Check the ingredient list on the package.",
        )
    ]


async def check_food(
    ts: AsyncTypeSafeClient, http: httpx.AsyncClient, query: str, fdc_id: int | None = None, cache: Cache | None = None
) -> Result:
    picked = None
    if fdc_id is not None:
        # The person chose this USDA food under "Not it?"; match AFCD to it rather than to what they typed.
        foods = await _search(http, query)
        picked = next((f for f in foods if f["fdcId"] == fdc_id), None)
    if picked is not None:
        chosen = picked
        others = [f for f in foods if f is not chosen][:5]
        au = None if chosen.get("ingredients") else await _pick_afcd(ts, chosen["description"])
    else:

        async def search_and_pick() -> tuple[dict | None, list[dict], bool, bool]:
            return await _pick(ts, query, await _search(http, query))

        # The AFCD pick doesn't need USDA's results, so it runs alongside the search.
        (chosen, others, is_food, is_brand), au = await asyncio.gather(search_and_pick(), _pick_afcd(ts, query))
        if not is_food:
            raise NotAFood(f"“{query}” doesn't look like a food. Try a food name or paste an ingredient list.")
        if is_brand and au is None and not (chosen or {}).get("ingredients"):
            usda = [f for f in (chosen, *others) if f is not None]
            if (result := await _off_by_name(ts, http, query, usda, cache)) is not None:
                return result
    alternatives = [Alternative(fdc_id=f["fdcId"], label=_food_label(f)) for f in others]
    return await _fdc_result(ts, query, chosen, au, alternatives, cache)


UNITS = {"GRM": "g", "MLT": "ml"}


def label_zero_sugar(items: list[Item], food: dict[str, Any]) -> list[Item]:
    """For a product whose label lists 0 g total sugars per serving, which means under 0.5 g: marks the
    fructose and sucrose sources in its ingredient list as capped at a trace. `prefs.apply_label_cap` makes
    them Caution unless the person flags any amount.

    Sorbitol and other sugar alcohols aren't counted in total sugars, so they still count in full.
    """
    size, unit = food.get("servingSize"), UNITS.get(food.get("servingSizeUnit") or "", "")
    serving = ", ".join(x for x in (food.get("householdServingFullText"), size and unit and f"{size:g} {unit}") if x)
    label = f"The label lists 0 g total sugars per serving{f' ({serving})' if serving else ''}, so under 0.5 g."
    out = []
    for i in items:
        if i.name.startswith("Total sugars: 0 g"):
            i = i.model_copy(
                update={
                    "name": "Total sugars: 0 g per serving",
                    "reason": f"{label} Sorbitol isn't counted in total sugars; it's checked separately.",
                }
            )
        elif i.status == "not_tolerated" and i.sugars and set(i.sugars) <= {"fructose", "sucrose"}:
            i = i.model_copy(update={"label_cap": label})
        out.append(i)
    return out


async def _fdc_result(
    ts: AsyncTypeSafeClient,
    query: str,
    chosen: dict[str, Any] | None,
    au: afcd.AfcdFood | None,
    alternatives: list[Alternative],
    cache: Cache | None,
) -> Result:
    if chosen is None and au is None:
        items = await _estimate(ts, query, list(SUGAR_ORDER))
        if not items:
            # "OK" shouldn't rest on a guess alone.
            items = [
                Item(
                    name="No measured data",
                    status="caution",
                    basis="jev",
                    reason="Judged unlikely to contain fructose, sucrose or sorbitol, but there's no data to confirm it.",
                )
            ]
        return summarize(
            items,
            alternatives=alternatives,
            notes=["No matching food in USDA FoodData Central, so this is an estimate from the name alone."],
        )

    food = None
    if chosen is not None:
        food = Food(
            fdc_id=chosen["fdcId"],
            description=chosen["description"],
            brand=chosen.get("brandName") or chosen.get("brandOwner"),
            data_type=chosen["dataType"],
        )
    items: list[Item] = []
    ingredients_text = ((chosen or {}).get("ingredients") or "").strip() or None
    if ingredients_text:
        # A packaged product's own label beats generic data about the food it's made from.
        au = None
        items += await classify_ingredients(ts, ingredients_text, cache)

    nutrients = _nutrients(chosen) if chosen else {}
    measured_items, measured = measured_sugars(nutrients, au)
    items += measured_items
    if chosen is not None and chosen["dataType"] == "Branded" and nutrients.get("Total Sugars") == 0:
        items = label_zero_sugar(items, chosen)
    notes: list[str] = []
    if not ingredients_text:
        if au is not None:
            name = au.name
        else:
            assert food is not None  # returned above when neither source matched
            name = food.description + (f" ({food.brand})" if food.brand else "")
        unmeasured = [s for s in SUGAR_ORDER if s not in measured]
        generic = au is not None or (food is not None and food.data_type in GENERIC_TYPES)
        if generic and "sorbitol" in unmeasured:
            # Whole foods: sorbitol comes from which foods contain it, not from a guess.
            unmeasured.remove("sorbitol")
            names = ([au.name] if au is not None else []) + ([food.description] if food is not None else [])
            items += known_sorbitol(names)
        items += await _estimate(ts, name, unmeasured)
        items += unidentified_sugars(nutrients, au, measured, items)
        if generic:
            notes.append(
                "This is a generic food without an ingredient list. Packaged versions vary, so check the label."
            )
    afcd_food = None
    if au is not None:
        afcd_food = AfcdMatch(id=au.id, name=au.name, derivation=au.derivation, kinds=len(afcd.varieties(au)))
    return summarize(
        items,
        food=food,
        afcd_food=afcd_food,
        ingredients_text=ingredients_text,
        alternatives=alternatives,
        notes=notes,
    )


async def check_text(
    ts: AsyncTypeSafeClient, http: httpx.AsyncClient, text: str, fdc_id: int | None = None, cache: Cache | None = None
) -> Result:
    text = text.strip()
    if off.is_barcode(text) and fdc_id is None:
        return await check_barcode(ts, http, text, cache)
    if ing.looks_like_list(text) and fdc_id is None:
        return summarize(await classify_ingredients(ts, text, cache), ingredients_text=text)
    return await check_food(ts, http, text, fdc_id, cache)


# ---------------------------------------------------------------- packaged foods (barcodes, Open Food Facts)


async def _search_gtin(http: httpx.AsyncClient, code: str) -> dict[str, Any] | None:
    """The USDA branded food with this barcode, if there is one."""
    wanted = off.gtins(code)
    r = await http.get(
        FDC_SEARCH,
        params={
            "api_key": os.environ["FOODDATA_CENTRAL_API_KEY"],
            "query": " OR ".join(f"gtinUpc:{g}" for g in wanted),
            "dataType": "Branded",
            "pageSize": 5,
        },
    )
    r.raise_for_status()
    return next((f for f in r.json().get("foods", []) if f.get("gtinUpc") in wanted), None)


async def check_barcode(
    ts: AsyncTypeSafeClient, http: httpx.AsyncClient, code: str, cache: Cache | None = None
) -> Result:
    # Either database being down shouldn't stop a lookup the other can answer.
    fdc_r, off_r = await asyncio.gather(_search_gtin(http, code), off.product(http, code), return_exceptions=True)
    errors = [x for x in (fdc_r, off_r) if isinstance(x, BaseException)]
    if any(not isinstance(x, httpx.HTTPError) for x in errors):
        raise errors[0]
    fdc = None if isinstance(fdc_r, BaseException) else fdc_r
    product = None if isinstance(off_r, BaseException) else off_r
    # USDA's branded data comes from manufacturers, so it wins whenever Open Food Facts can't add an ingredient list.
    if fdc is not None and (fdc.get("ingredients") or product is None or not product.ingredients):
        return await _fdc_result(ts, code, fdc, None, [], cache)
    if product is not None:
        note = "Ingredients from Open Food Facts, a volunteer-run database. Check them against the package."
        return await _off_result(ts, product, cache, notes=[note])
    if errors:
        raise errors[0]
    raise NotAFood(
        f"Couldn't find barcode {code} in USDA FoodData Central or Open Food Facts. "
        "Try a photo of the ingredient list instead."
    )


async def _pick_off(
    ts: AsyncTypeSafeClient, query: str, products: list[off.Product]
) -> tuple[off.Product | None, list[off.Product]]:
    """Jev picks the Open Food Facts product the user named. Returns (product, runners-up)."""
    if not products:
        return None, []
    criteria: dict[str, str | None] = {f"off_{i}": p.label for i, p in enumerate(products)}
    criteria["none"] = "None of these is the product named in `user_typed`."
    r = await ts.system_one(
        state={"user_typed": query},
        questions={
            "pick": Choice(
                instructions="Which product from Open Food Facts is the one named in `user_typed`? If several are "
                "the same product, pick the one whose name matches `user_typed` most closely.",
                criteria=criteria,
            )
        },
    )
    choice = r.choices["pick"].choice
    if choice == "none":
        return None, []
    chosen = products[int(choice[4:])]
    probs = r.choices["pick"].probabilities
    ranked = [products[int(k[4:])] for k in sorted(probs, key=probs.__getitem__, reverse=True) if k != "none"]
    return chosen, [p for p in ranked if p is not chosen][:4]


async def _off_by_name(
    ts: AsyncTypeSafeClient, http: httpx.AsyncClient, query: str, usda: list[dict[str, Any]], cache: Cache | None
) -> Result | None:
    """For a brand USDA has no ingredient list for: the product from Open Food Facts, if it has one."""
    try:
        products = await off.search(http, query)
    except httpx.HTTPError:
        log.warning("Open Food Facts search failed", exc_info=True)
        return None
    chosen, others = await _pick_off(ts, query, products)
    if chosen is None:
        return None
    try:
        chosen = await off.product(http, chosen.code) or chosen
    except httpx.HTTPError:
        log.warning("Open Food Facts product lookup failed", exc_info=True)
        return None
    alternatives = [Alternative(barcode=p.code, label=p.label) for p in others]
    alternatives += [Alternative(fdc_id=f["fdcId"], label=_food_label(f)) for f in usda[:3]]
    where = f"sold in {', '.join(chosen.countries)}" if chosen.countries else "from an unknown country"
    note = (
        f"USDA has no ingredient list for this product, so this one is from Open Food Facts ({where}). "
        "Recipes differ by country, so check your package."
    )
    return await _off_result(ts, chosen, cache, alternatives, [note])


async def _off_result(
    ts: AsyncTypeSafeClient,
    p: off.Product,
    cache: Cache | None,
    alternatives: Sequence[Alternative] = (),
    notes: Sequence[str] = (),
) -> Result:
    match = OffMatch(code=p.code, name=p.name, brand=p.brand, countries=p.countries, url=p.url)
    notes = list(notes)
    if p.ingredients:
        items = await classify_ingredients(ts, p.ingredients, cache)
        if not p.english:
            notes.append(
                "The ingredient list isn't in English, so some sugars may not match the BU table by name. "
                "Those were judged by the AI model."
            )
    else:
        items = await _estimate(ts, p.name + (f" ({p.brand})" if p.brand else ""), list(SUGAR_ORDER))
        # The other notes are about the ingredient list, which this product doesn't have.
        notes = [
            (
                "Open Food Facts has this product but no ingredient list, so this is an estimate from its name. "
                "Check the label."
            )
        ]
    return summarize(
        items, off_product=match, ingredients_text=p.ingredients, alternatives=list(alternatives), notes=notes
    )


# ---------------------------------------------------------------- label photos

# Common whole-food sources that aren't in the BU table: (label wording, sugars).
LABEL_FOODS: list[tuple[str, list[Sugar]]] = [
    ("apple, apple juice, or apple puree", ["fructose", "sorbitol"]),
    ("pear or pear juice", ["fructose", "sorbitol"]),
    ("grape or grape juice", ["fructose"]),
    ("raisins", ["fructose"]),
    ("dates", ["fructose", "sucrose"]),
    ("figs", ["fructose"]),
    ("prunes or plums", ["fructose", "sorbitol"]),
    ("cherries", ["fructose", "sorbitol"]),
    ("peaches or apricots", ["fructose", "sorbitol"]),
    ("strawberries, raspberries, blueberries, or cranberries", ["fructose"]),
    ("orange, lemon, or lime juice", ["fructose", "sucrose"]),
    ("pineapple, mango, or banana", ["fructose", "sucrose"]),
    ("tomato, tomato paste, or tomato concentrate", ["fructose"]),
    ("onion or onion powder", ["fructose"]),
    ("carrots", ["fructose", "sucrose"]),
    ("peas", ["fructose", "sucrose"]),
    ("sweet potatoes", ["fructose", "sucrose"]),
    ("beets", ["sucrose"]),
    ("chocolate", ["sucrose"]),
    ("isomalt", ["sorbitol"]),
]
FOUND_MIN = 0.5


def _printed_names(e: SugarEntry) -> list[str]:
    """How an entry might be printed on a label, e.g. "Castor/Caster Sugar" -> castor sugar, caster sugar."""
    names = [n for n in e.names if "/" not in n and "(" not in n]
    if "/" in e.name:
        first, rest = e.name.split("/", 1)
        names += [f"{first} {rest.split(' ', 1)[1]}", rest]
    if "(" in e.name:
        names.append(e.name.split(" (")[0])
    return list(dict.fromkeys(n.lower() for n in names))[:5]


COMMON_NAMES = {
    "cane sugar",
    "brown sugar",
    "high fructose corn syrup",
    "glucose syrup",
    "corn syrup solids",
    "sugar alcohol",
}


def _longer_names(entry: SugarEntry) -> list[str]:
    """BU names that contain this entry's primary name, e.g. "cane sugar" for "sugar"."""
    mine = sugars.norm(_printed_names(entry)[0])
    longer = {
        n
        for e in TABLE
        if e is not entry
        for n in _printed_names(e)
        if mine != sugars.norm(n) and mine in sugars.norm(n)
    }
    return sorted(longer, key=lambda n: (n not in COMMON_NAMES, len(n)))[:3]


def _label_question(names: Sequence[str]) -> Noul:
    return Noul(instructions=f"Is {' or '.join(repr(n) for n in names)} one of the ingredients listed in `label`?")


def _overlap_question(name: str, longer: Sequence[str]) -> Choice:
    """Jev handles "is X there?" well but not "X, unless only inside Y", so the distinction gets its own question."""
    return Choice(
        instructions=f"How does '{name}' appear in the ingredient list in `label`?",
        criteria={
            "own": f"'{name}' is listed as an ingredient by itself.",
            "part_of_longer": f"'{name}' only appears as part of a longer ingredient name, such as "
            + " or ".join(f"'{x}'" for x in longer)
            + ".",
            "absent": f"The words '{name}' don't appear in the ingredient list.",
        },
    )


def _split_names(names: str) -> list[str]:
    return [n for n in re.split(r",\s*(?:or\s+)?|\s+or\s+", names) if n]


def _food_question(names: str) -> Noul:
    return Noul(
        instructions=f"Does the ingredient list in `label` include {names}, in any form (fresh, dried, juice, "
        "concentrate, puree or powder)?"
    )


def _catch_all(kind: str, known: Sequence[str]) -> Noul:
    return Noul(instructions=f"Does the ingredient list in `label` include {kind} other than " + ", ".join(known) + "?")


SUGAR_ALCOHOLS = ["sorbitol", "mannitol", "xylitol", "maltitol", "lactitol", "erythritol", "isomalt"]
SYRUPS = sorted({n for e in TABLE for n in _printed_names(e) if "syrup" in n}, key=len)
FRUITS = [n for names, _ in LABEL_FOODS[:13] for n in _split_names(names)]  # through tomato
LABEL_CATCH_ALL: list[tuple[str, Noul]] = [
    ("A fruit ingredient", _catch_all("a fruit, fruit juice, fruit puree, or dried fruit", FRUITS)),
    ("A syrup", _catch_all("an ingredient with the word 'syrup' in its name", SYRUPS)),
    ("A sugar alcohol", _catch_all("a sugar alcohol (an ingredient whose name ends in '-itol')", SUGAR_ALCOHOLS)),
]


async def check_image(ts: AsyncTypeSafeClient, data: bytes, avoid: Sequence[str] = ()) -> Result:
    """`avoid` adds a question per term the person avoids, since Jev only reports what it's asked about."""
    questions: dict[str, Noul | Choice] = {
        "has_ingredients": Noul(instructions="Does `label` show a food product's ingredient list?"),
        "legible": Noul(instructions="Is the ingredient text in `label` sharp and readable?"),
        "has_panel": Noul(instructions="Does `label` show a Nutrition Facts panel?"),
        "zero_sugar": Noul(instructions="Does the Nutrition Facts panel in `label` show Total Sugars of 0 g?"),
    }
    for i, e in enumerate(TABLE):
        if e.status == "tolerated":
            continue  # can't change the verdict
        questions[f"bu{i}"] = _label_question(_printed_names(e))
        if longer := _longer_names(e):
            questions[f"bu{i}_alone"] = _overlap_question(_printed_names(e)[0], longer)
    for i, (names, _) in enumerate(LABEL_FOODS):
        questions[f"food{i}"] = _food_question(names)
    for i, (_, q) in enumerate(LABEL_CATCH_ALL):
        questions[f"other{i}"] = q
    for i, term in enumerate(avoid):
        questions[f"avoid{i}"] = Noul(
            instructions=f"Is '{term}' one of the ingredients listed in `label`, or part of an ingredient's name?"
        )

    r = await ts.system_one(state={"label": Image(data)}, questions=questions, extra_headers=IMAGES_HEADER)
    yes = {k: a.noul >= FOUND_MIN for k, a in r.nouls.items()}
    for k, a in r.choices.items():
        yes[k.removesuffix("_alone")] &= a.choice == "own"

    if not yes["has_ingredients"]:
        if yes["has_panel"]:
            note = "Sugar alcohols such as sorbitol aren't counted in Total Sugars."
            if yes["zero_sugar"]:
                return summarize(
                    [],
                    verdict="caution",
                    notes=[
                        "The Nutrition Facts panel shows 0 g total sugars, but there's no ingredient list in the photo.",
                        note,
                        "Take a photo of the ingredient list to be sure.",
                    ],
                )
            return summarize(
                [],
                verdict="caution",
                notes=[
                    "This photo shows a Nutrition Facts panel but no ingredient list.",
                    "Take a photo of the ingredient list to see which sugars are in it.",
                ],
            )
        raise NotAFood(
            "Couldn't find an ingredient list in that photo. Try a closer, well-lit shot of the ingredients."
        )

    items = [_bu_item(e, e.name.split(" (")[0], basis="label") for i, e in enumerate(TABLE) if yes.get(f"bu{i}")]
    items += [
        Item(name=term, status="not_tolerated", basis="label", avoided_by=term, reason="Listed on the label.")
        for i, term in enumerate(avoid)
        if yes[f"avoid{i}"]
    ]
    items += [
        Item(
            name=names.split(",")[0].split(" or ")[0].capitalize(),
            status="not_tolerated",
            sugars=s,
            basis="label",
            reason=f"Listed on the label: {names}. Not in the BU table, but it naturally contains {' and '.join(s)}.",
        )
        for i, (names, s) in enumerate(LABEL_FOODS)
        if yes[f"food{i}"]
    ]
    items += [
        Item(
            name=title,
            status="caution",
            basis="label",
            reason=f"{title} on the label isn't in the BU table and may contain fructose, sucrose, or sorbitol.",
        )
        for i, (title, _) in enumerate(LABEL_CATCH_ALL)
        if yes[f"other{i}"]
    ]
    notes = [] if yes["legible"] else ["The text in this photo is hard to read, so some ingredients may be missed."]
    return summarize(items, notes=notes)


# ---------------------------------------------------------------- verdict


def _effective(i: Item) -> str:
    return "not_tolerated" if i.avoided_by else i.status


def summarize(items: list[Item], verdict: Verdict | None = None, **kw: Any) -> Result:
    counted = [i for i in items if i.avoided_by or not i.allowed_by]
    found = [s for s in SUGAR_ORDER if any(s in i.sugars and _effective(i) == "not_tolerated" for i in counted)]
    avoided = list(dict.fromkeys(i.avoided_by for i in items if i.avoided_by))
    if verdict is None:
        if any(_effective(i) == "not_tolerated" for i in counted):
            verdict = "avoid"
        elif any(i.status == "caution" for i in counted):
            verdict = "caution"
        else:
            verdict = "ok"
    if verdict == "avoid":
        if found:
            headline = f"Contains {_join(found)}"
        elif avoided:
            headline = f"Contains {_join([_midsentence(t) for t in avoided[:3]])}, which you avoid"
        else:
            headline = "Contains sugars not tolerated with HFI"
    elif verdict == "caution":
        cautions = [i for i in counted if i.status == "caution"]
        if cautions and all(i.trace for i in cautions):
            found = [s for s in SUGAR_ORDER if any(s in i.sugars for i in cautions)]
            headline = f"Small amounts of {_join(found)}"
        else:
            headline = "Unclear: may contain fructose, sucrose, or sorbitol"
    elif low := [s for s in SUGAR_ORDER if any(i.trace and s in i.sugars for i in counted)]:
        headline = f"Very low in {_join(low)}"
    elif len(counted) < len(items) and any(i.status != "tolerated" for i in items):
        headline = "OK with your allowed foods"
    else:
        headline = "No fructose, sucrose, or sorbitol found"
    # Avoided first, then by status; allowed items last.
    items = sorted(
        items, key=lambda i: (bool(i.allowed_by and not i.avoided_by), not i.avoided_by, STATUS_ORDER.index(i.status))
    )
    return Result(verdict=verdict, headline=headline, sugars=found, items=items, **kw)


def _midsentence(term: str) -> str:
    """ "Natural flavor" -> "natural flavor", but "MSG" stays "MSG"."""
    first = term.split()[0]
    return term if first.isupper() and len(first) > 1 else term[:1].lower() + term[1:]


def _join(words: Sequence[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]
