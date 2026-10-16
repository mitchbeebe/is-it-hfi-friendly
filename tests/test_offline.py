"""Tests for the parts that don't call TypeSafe or FoodData Central."""

import io

import pytest
from PIL import Image

from is_it_sugar import analyze, foods, images, ingredients, off, prefs, sugars
from is_it_sugar.models import Food, Item, Serving


def test_parse_strips_lead_ins_and_allergens():
    text = "INGREDIENTS: SUGAR, SALT. CONTAINS 2% OR LESS OF: CITRIC ACID, NATURAL FLAVOR. CONTAINS: MILK, SOY."
    assert [i.name for i in ingredients.parse(text)] == ["SUGAR", "SALT", "CITRIC ACID", "NATURAL FLAVOR"]


def test_parse_keeps_brackets_with_their_ingredient():
    text = (
        "flour, chocolate chips (sugar, cocoa butter), vegetable oil (includes one of the following: canola, soybean)"
    )
    assert [i.name for i in ingredients.parse(text)] == [
        "flour",
        "chocolate chips (sugar, cocoa butter)",
        "vegetable oil (includes one of the following: canola, soybean)",
    ]


def test_parse_closes_unclosed_brackets():
    text = "Wholewheat Flour (80%), Flavouring [Yeast Extract (Barley), Salt, Vegetable Extract (Carrot), White Pepper."
    assert [i.name for i in ingredients.parse(text)] == [
        "Wholewheat Flour (80%)",
        "Flavouring [Yeast Extract (Barley), Salt, Vegetable Extract (Carrot), White Pepper]",
    ]


def test_split_separates_what_brackets_say():
    assert ingredients.split("enriched flour (wheat flour, niacin)") == ("enriched flour", ["wheat flour", "niacin"])
    assert ingredients.split("vegetable oil (includes one of the following: canola, soybean)") == (
        "vegetable oil",
        ["canola", "soybean"],
    )
    assert ingredients.split("salt") == ("salt", [])


def food(name, source=None):
    return next(f for f in foods._all() if f.name == name and source in (None, f.source))


@pytest.mark.parametrize(
    ("ingredient", "status"),
    [
        ("chocolate chips (sugar, cocoa butter)", "not_tolerated"),  # a sugar inside brackets counts
        ("HIGH FRUCTOSE CORN SYRUP", "not_tolerated"),
        ("modified corn starch", "tolerated"),
        ("dextrose", "tolerated"),
    ],
)
def test_bu_table_decides_sugar_names(ingredient, status):
    item = analyze._bu_decide(ingredient)
    assert item is not None and (item.status, item.basis) == (status, "bu_table")


@pytest.mark.parametrize("ingredient", ["onion", "natural flavor", "chocolate (dextrose, cocoa butter)", "salt"])
def test_everything_else_goes_to_search_and_jev(ingredient):
    assert analyze._bu_decide(ingredient) is None


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("ORGANIC NON-GMO POPCORN", "Snacks, popcorn"),
        ("ORGANIC GREEN PEPPER", "Sweet pepper, green, raw"),
        ("TOMATO CONCENTRATE FROM RED RIPE TOMATOES", "Tomato paste, concentrated"),
        ("WHOLE GRAIN OATS", "Oats"),
        ("ENRICHED FLOUR", "Wheat flour"),
        ("ORGANIC ROASTED GARLIC", "Garlic, roasted"),
        ("DISTILLED VINEGAR", "Vinegar"),
        ("onion powder", "Spices, onion powder"),
        ("sea salt", "sea salt"),
        ("PAPRIKA", "Paprika"),
    ],
)
def test_search_offers_the_food_among_its_candidates(query, expected):
    names = [f.name for f in foods.search(query)]
    assert any(expected.lower() in n.lower() for n in names), names


@pytest.mark.parametrize(("query", "expected"), [("bell pepper", "Capsicum"), ("cantaloupe", "Melon, rockmelon")])
def test_search_finds_afcd_foods_by_us_names(query, expected):
    assert any(f.name.startswith(expected) for f in foods.search(query, sources=("afcd",)))


def test_food_data_has_fructose_and_sucrose_for_every_food():
    data = foods._all()
    assert {f.source for f in data} == {"afcd", "ciqual", "fdc"} and len(data) > 5000
    assert all(f.fructose is not None and f.sucrose is not None for f in data)


def test_parse_bounds_each_ingredients_share_of_the_product():
    text = "POPCORN, SUNFLOWER OIL, SEA SALT, CONTAINS 2% OR LESS OF: PAPRIKA, tomatoes 45%"
    shares = {i.name: (round(i.share, 3), i.why) for i in ingredients.parse(text)}
    assert shares == {
        "POPCORN": (1.0, "the first ingredient"),
        "SUNFLOWER OIL": (0.5, "listed 2nd"),
        "SEA SALT": (0.333, "listed 3rd"),
        "PAPRIKA": (0.02, "listed under 2% or less"),
        "tomatoes 45%": (0.45, "as declared"),
    }


@pytest.mark.parametrize(
    ("ingredient", "expected"),
    [
        ("SALT", "Salt"),
        ("POTATOES", "Potatoes"),
        ("VEGETABLE OIL (SUNFLOWER, CORN AND/OR CANOLA OIL)", "Vegetable oil"),
        ("onions", "Onion"),
        ("tomato paste", "Tomato paste"),
    ],
)
def test_exact_names_match_without_jev(ingredient, expected):
    f = foods.exact(analyze._base(ingredient))
    assert f is not None and f.name.startswith(expected)


def test_exact_ties_take_the_food_with_most_sugar():
    onions = foods.exact("onions")
    assert onions is not None and foods.load(onions) == max(
        foods.load(f) for f in foods._all() if f.name in ("Onion, raw", "Onions, raw")
    )


@pytest.mark.parametrize(
    ("ingredient", "expected"),
    [
        ("ORGANIC POTATOES", "Potatoes, flesh and skin"),
        ("ORGANIC NON-GMO POPCORN", "Snacks, popcorn"),
        ("WHOLE GRAIN OATS", "Oats"),
        ("ORGANIC ROASTED GARLIC", "Garlic, roasted"),
    ],
)
def test_candidates_put_likely_foods_first(ingredient, expected):
    assert foods.exact(ingredient) is None
    assert any(f.name.startswith(expected) for f in foods.candidates(ingredient)[:8])


def test_measured_ingredient_counts_at_its_share():
    paprika = ingredients.parse("popcorn, salt, contains 2% or less of: paprika")[2]
    item = analyze._measured_item(paprika, food("Paprika", "ciqual"))
    assert (item.status, item.basis) == ("not_tolerated", "ciqual")
    assert item.grams == pytest.approx((6.71 + 0.81 / 2) * 0.02)
    assert item.reason == (
        "Paprika: 6.7 g fructose, 0.81 g sucrose per 100 g. "
        "At most 2% of the product (listed under 2% or less), so up to 0.14 g per 100 g."
    )
    salt = analyze._measured_item(ingredients.Ingredient("salt"), food("Salt, table, non-iodised"))
    assert (salt.status, salt.grams) == ("tolerated", None)
    # Popcorn (0.43 g load) and up to 2% paprika (0.14 g): all measured, just over 0.5 g, so small amounts.
    popcorn = analyze._measured_item(ingredients.Ingredient("popcorn"), food("Pop-corn or air-popped maize, unsalted"))
    r = prefs.apply(analyze.summarize([popcorn, salt, item]), trace_limit=1.0)
    assert (r.verdict, r.headline) == ("caution", "Small amounts of fructose and sucrose")
    assert prefs.apply(analyze.summarize([popcorn, salt]), trace_limit=1.0).verdict == "ok"


def test_items_link_to_the_food_in_their_source():
    paprika = food("Paprika", "ciqual")
    item = analyze._measured_item(ingredients.Ingredient("paprika"), paprika)
    assert item.url == f"https://ciqual.anses.fr/#/aliments/{paprika.id.removeprefix('ciqual:')}"
    mustard = analyze._measured_item(ingredients.Ingredient("mustard"), food("Mustard powder", "afcd"))
    assert (
        mustard.url == "https://www.foodstandards.gov.au/science-data/food-nutrient-databases/afcd/search/food/F005972"
    )
    items, _ = analyze.measured_sugars({"Sucrose": 2.0}, None, 170567)
    assert items[0].url == "https://fdc.nal.usda.gov/food-details/170567/nutrients"
    assert analyze.measured_sugars({"Sucrose": 2.0}, None)[0][0].url is None


def test_unmeasured_sorbitol_in_a_sorbitol_fruit_has_no_amount():
    apples = next(f for f in foods._all() if f.source == "fdc" and f.name.startswith("Apples, raw, with skin"))
    item = analyze._measured_item(ingredients.Ingredient("apples"), apples)
    assert "sorbitol" in item.sugars and item.grams is None  # so Small amounts can't downgrade it


def test_jev_answer_is_a_food_or_a_description():
    candidates = [food("Paprika", "ciqual"), food("Vinegar", "ciqual")]
    describe = {"fructose": 0.6, "none": 0.4}
    picked = analyze._answer(candidates, {"food_0": 0.1, "food_1": 0.6, "not_listed": 0.3}, describe)
    assert picked == {"food": "ciqual:11018"}
    # Near-duplicates together outvote "not listed", even when neither does alone.
    split = analyze._answer(candidates, {"food_0": 0.3, "food_1": 0.3, "not_listed": 0.4}, describe)
    assert split == {"food": "ciqual:11049"}
    described = analyze._answer(candidates, {"food_0": 0.1, "food_1": 0.1, "not_listed": 0.8}, describe)
    assert described["probs"]["fructose"] == pytest.approx(0.6)
    assert analyze._answer([], None, describe)["probs"]["none"] == pytest.approx(0.4)
    item = analyze._answer_item(ingredients.Ingredient("natural flavor"), described)
    assert (item.basis, item.sugars) == ("jev", ["fructose"])
    # An answer cached for a food that a data rebuild removed falls back to unsure.
    gone = analyze._answer_item(ingredients.Ingredient("x"), {"food": "ciqual:0"})
    assert gone.status == "caution"


def test_label_note_needs_confidence():
    note = analyze._jev_item(
        "Dried", {"fructose": 0.05, "sucrose": 0, "sorbitol": 0, "unsure": 0.05, "none": 0.1, "label_note": 0.8}
    )
    assert (note.status, note.sugars) == ("tolerated", [])
    assert note.reason.startswith("A note on the label")
    # Unsure it's a note: judged as an ingredient instead, and "none" isn't confident enough here.
    item = analyze._jev_item(
        "Dried", {"fructose": 0.1, "sucrose": 0, "sorbitol": 0, "unsure": 0.1, "none": 0.4, "label_note": 0.4}
    )
    assert item.status == "caution"
    # Ratings cached before the label_note option still work.
    old = analyze._jev_item("apple", {"fructose": 0.9, "sucrose": 0, "sorbitol": 0.3, "unsure": 0, "none": 0.1})
    assert (old.status, old.sugars) == ("not_tolerated", ["fructose", "sorbitol"])


def test_parse_keeps_decimals():
    assert [i.name for i in ingredients.parse("salt, vitamin b2 0.5%, sugar")] == ["salt", "vitamin b2 0.5%", "sugar"]


@pytest.mark.parametrize("text", ["sugar, salt", "Ingredients: water", "a; b"])
def test_looks_like_list(text):
    assert ingredients.looks_like_list(text)


@pytest.mark.parametrize("text", ["ketchup", "Heinz tomato ketchup", "apple pie"])
def test_food_name_is_not_a_list(text):
    assert not ingredients.looks_like_list(text)


@pytest.mark.parametrize(
    ("ingredient", "expected"),
    [
        ("HIGH FRUCTOSE CORN SYRUP", "High Fructose Corn Syrup (HFCS)"),
        ("glucose-fructose syrup", "High Fructose Corn Syrup (HFCS)"),
        ("corn syrup solids", "Corn Syrup Solids"),
        ("corn syrup", "Corn Syrup"),
        ("Organic Cane Sugar", "Cane Sugar"),
        ("maltodextrin", "Maltodextrin"),
        ("powdered sugar", "Confectioners Sugar"),
        ("allulose", "D-Psicose"),
        ("erythritol", "Erythritol"),
        ("apple juice concentrate", "Fruit Juice Sweeteners"),
        ("chicory root fiber", "Chicory"),
        ("sugar alcohol", "Sugar Alcohol"),
    ],
)
def test_match_prefers_longest_name(ingredient, expected):
    assert [e.name for e in sugars.match(ingredient)] == [expected]


@pytest.mark.parametrize("ingredient", ["salt", "honeydew melon", "locust bean gum", "wheat flour", "dextrinized"])
def test_match_ignores_non_sugars(ingredient):
    assert sugars.match(ingredient) == []


def test_table_statuses_follow_bu_wording():
    by_name = {e.name: e for e in sugars.TABLE}
    assert by_name["Sorbitol"].status == "not_tolerated"
    assert by_name["Xylitol"].status == "caution"  # "Tolerated depending on purity"
    assert by_name["Corn Syrup Solids"].status == "caution"
    assert by_name["Raffinose"].status == "caution"  # "Tolerance Varies"
    assert by_name["Acesulfame-K"].status == "tolerated"  # "Tolerated (Questionable safety)"
    assert by_name["Dextrose"].status == "tolerated"
    # Every entry BU marks not tolerated, apart from a few non-sugar sweeteners, carries a sugar.
    no_sugar = {e.name for e in sugars.TABLE if e.status == "not_tolerated" and not e.contains}
    assert no_sugar == {"Dulcitol", "Stevia", "Sugar Alcohol"}


def test_overlap_names():
    by_name = {e.name: e for e in sugars.TABLE}
    assert analyze._longer_names(by_name["Corn Syrup"])[:2] == ["corn syrup solids", "high fructose corn syrup"]
    assert analyze._longer_names(by_name["High Fructose Corn Syrup (HFCS)"]) == []
    assert analyze._printed_names(by_name["Castor/Caster Sugar"]) == ["caster sugar", "castor sugar"]


def item(status, sugars_=()):
    return Item(name="x", status=status, sugars=list(sugars_), basis="bu_table", reason="")


def test_verdicts():
    assert analyze.summarize([item("tolerated")]).verdict == "ok"
    assert analyze.summarize([item("tolerated"), item("caution")]).verdict == "caution"
    r = analyze.summarize([item("caution"), item("not_tolerated", ["sucrose", "fructose"])])
    assert (r.verdict, r.headline, r.sugars) == ("avoid", "Contains fructose and sucrose", ["fructose", "sucrose"])
    assert [i.status for i in r.items] == ["caution", "not_tolerated"]  # in the order given, as on the label


def jpeg(w, h):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, "JPEG")
    return buf.getvalue()


@pytest.mark.parametrize(("w", "h"), [(4000, 3000), (3001, 370), (488, 184), (100, 100), (1000, 700)])
def test_prepare_fits_typesafe_limits(w, h):
    with Image.open(io.BytesIO(images.prepare(jpeg(w, h)))) as out:
        assert out.format == "JPEG"
        assert 280 <= min(out.size) and max(out.size) <= 1568


def test_prepare_rejects_non_images():
    with pytest.raises(images.BadImage):
        images.prepare(b"not an image")


# ---- allowlist


def bu(name, display=None):
    e = sugars.BY_NAME[name]
    return analyze._bu_item(e, display or name)


def judged(name, status="not_tolerated", sugars_: tuple[sugars.Sugar, ...] = ("fructose",)):
    return Item(name=name, status=status, sugars=list(sugars_), basis="jev", reason="")


def test_allow_ingredient_by_containment_and_plural():
    r = analyze.summarize(
        [
            judged("TOMATO CONCENTRATE FROM RED RIPE TOMATOES"),
            judged("ONION POWDER"),
            judged("SALT", "tolerated", ()),
            judged("RAISINS"),
        ],
        ingredients_text="...",
    )
    out = prefs.apply(r, ["Tomatoes", "onion"])
    assert out.verdict == "avoid" and out.headline == "Contains fructose"  # raisins still count
    assert [(i.name, i.allowed_by) for i in out.items] == [  # label order
        ("TOMATO CONCENTRATE FROM RED RIPE TOMATOES", "Tomatoes"),
        ("ONION POWDER", "onion"),
        ("SALT", None),
        ("RAISINS", None),
    ]
    out = prefs.apply(r, ["Tomatoes", "onion", "raisin"])
    assert (out.verdict, out.headline) == ("ok", "OK with your allowed foods")
    assert "3 items" in out.notes[-1]


def test_allow_never_reaches_bu_sugars_by_partial_name():
    r = analyze.summarize(
        [bu("High Fructose Corn Syrup (HFCS)", "HIGH FRUCTOSE CORN SYRUP"), bu("Corn Syrup")], ingredients_text="..."
    )
    out = prefs.apply(r, ["corn", "syrup", "fructose corn"])
    assert out.verdict == "avoid" and all(i.allowed_by is None for i in out.items)


def test_allow_bu_sugar_by_its_own_name_or_alias():
    r = analyze.summarize([bu("Sucralose"), bu("D-Psicose", "allulose")], ingredients_text="...")
    out = prefs.apply(r, ["sucralose", "Allulose"])
    assert out.verdict == "ok" and [i.allowed_by for i in out.items] == ["sucralose", "Allulose"]


def test_allow_whole_food_needs_exact_main_name():
    food = Food(fdc_id=1, description="Broccoli, raw", data_type="SR Legacy")
    items = [
        Item(name="Fructose: 0.68 g per 100 g", status="not_tolerated", sugars=["fructose"], basis="fdc", reason="")
    ]
    assert prefs.apply(analyze.summarize(items, food=food), ["broccoli"], query="brocoli raw").verdict == "ok"
    soup = Food(fdc_id=2, description="Tomato soup", data_type="Survey (FNDDS)")
    estimate = [judged("Fructose"), judged("Sucrose", sugars_=("sucrose",))]
    assert prefs.apply(analyze.summarize(estimate, food=soup), ["tomato"], query="tomato soup").verdict == "avoid"


def test_allow_whole_food_ignored_when_food_has_ingredients():
    food = Food(fdc_id=3, description="KETCHUP", data_type="Branded")
    r = analyze.summarize([bu("High Fructose Corn Syrup (HFCS)")], food=food, ingredients_text="HFCS")
    assert prefs.apply(r, ["ketchup"], query="ketchup").verdict == "avoid"


def test_allow_empty_or_blank_is_a_no_op():
    r = analyze.summarize([judged("raisins")], ingredients_text="raisins")
    assert prefs.apply(r, []) == r
    assert prefs.apply(r, ["  ", "!!"]) == r


# ---- avoid list


def test_avoid_flags_tolerated_ingredient_and_wins_over_allow():
    r = analyze.summarize(
        [judged("NATURAL FLAVORS", "caution", ()), judged("SALT", "tolerated", ())], ingredients_text="..."
    )
    out = prefs.apply(r, allow=["natural flavor"], avoid=["Natural flavors"])
    assert (out.verdict, out.headline) == ("avoid", "Contains natural flavors, which you avoid")
    assert out.items[0].avoided_by == "Natural flavors"
    assert "Flagging 1 item on your avoid list." in out.notes


def test_avoid_reaches_bu_sugars_by_partial_name():
    r = analyze.summarize([bu("Corn Syrup Solids")], ingredients_text="...")
    assert prefs.apply(r, avoid=["corn"]).verdict == "avoid"


def test_avoid_whole_food_adds_an_item():
    food = Food(fdc_id=4, description="Chicken, breast, boneless, skinless, raw", data_type="Foundation")
    r = analyze.summarize([], food=food)
    assert r.verdict == "ok"
    out = prefs.apply(r, avoid=["chicken"], query="chicken breast")
    assert out.verdict == "avoid" and out.items[0].basis == "settings"


def test_avoid_and_sugars_headline_names_sugars():
    r = analyze.summarize([bu("Sugar"), judged("SPICE", "tolerated", ())], ingredients_text="...")
    out = prefs.apply(r, avoid=["spice"])
    assert out.headline == "Contains sucrose"


# ---- rate limit and cache


def test_rate_limiter_windows(monkeypatch):
    from is_it_sugar import ratelimit

    now = [1000.0]
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: now[0])
    rl = ratelimit.RateLimiter([(2, 60), (3, 3600)])
    assert rl.hit("a")[:2] == (True, 1)
    assert rl.hit("a")[:2] == (True, 0)
    ok, _, retry = rl.hit("a")
    assert not ok and retry == 60
    assert rl.hit("b")[0]  # other IPs unaffected
    now[0] += 61
    assert rl.hit("a")[0]  # minute window passed
    now[0] += 61
    ok, _, retry = rl.hit("a")
    assert not ok and 3400 < retry <= 3600  # hourly window still full


def test_cache_roundtrip_and_expiry(tmp_path, monkeypatch):
    from is_it_sugar import cache as cache_mod

    c = cache_mod.Cache(tmp_path / "c.sqlite3")
    c.set("ns", "k", {"a": 1})
    assert c.get("ns", "k", max_age=60) == {"a": 1}
    assert c.get("other", "k", max_age=60) is None
    real = cache_mod.time.time
    monkeypatch.setattr(cache_mod.time, "time", lambda: real() + 120)
    assert c.get("ns", "k", max_age=60) is None


# ---- Australian Food Composition Database


def au(name):
    from is_it_sugar import afcd

    return next(f for f in afcd.foods() if f.name == name)


def local(name):
    """The searchable AFCD food, as measured_sugars takes it."""
    return food(name, "afcd")


def test_afcd_data_is_whole_foods_without_borrowed_rows():
    from is_it_sugar import afcd

    data = afcd.foods()
    assert len(data) > 800
    assert all(f.derivation != "Borrowed" for f in data)
    assert not [f for f in data if f.name.lower().startswith("sauce, tomato")]  # processed: regional recipes


def test_measured_sugars_shows_variety_ranges_and_beats_usda():
    items, measured = analyze.measured_sugars({"Fructose": 99.0}, local("Apple, fuji, unpeeled, raw"))
    by_sugar = {i.sugars[0]: i for i in items}
    assert measured == ["fructose", "sucrose", "sorbitol"]
    assert by_sugar["fructose"].name == "Fructose: 5.4–7.7 g per 100 g"
    assert by_sugar["fructose"].basis == "afcd"
    assert by_sugar["fructose"].reason.startswith(
        "Range across 10 kinds of apple (unpeeled, raw). Closest: Apple, fuji"
    )
    assert by_sugar["sorbitol"].name.startswith("Sorbitol: 0.2–0.5 g")


def test_measured_sugars_zero_and_missing_fall_through():
    items, measured = analyze.measured_sugars({"Sucrose": 2.0}, local("Grape, green, raw"))
    # AFCD reports grape sucrose as 0, which wins over USDA's 2 g; zeros are listed as tolerated.
    assert [(i.name, i.status) for i in items] == [
        ("Fructose: 6–9.5 g per 100 g", "not_tolerated"),
        ("Sucrose: 0 g per 100 g", "tolerated"),
        ("Sorbitol: 0 g per 100 g", "tolerated"),
    ]
    assert all(i.basis == "afcd" for i in items)
    items, measured = analyze.measured_sugars({"Sucrose": 2.0}, None)
    assert measured == ["sucrose"] and items[0].basis == "fdc"


def test_measured_sugars_from_ciqual():
    items, measured = analyze.measured_sugars({}, food("Sweet pepper, red, raw", "ciqual"))
    assert measured == ["fructose", "sucrose"]
    assert [(i.name, i.basis) for i in items] == [
        ("Fructose: 2.6 g per 100 g", "ciqual"),
        ("Sucrose: under 0.2 g per 100 g", "ciqual"),
    ]


def test_prefs_keeps_measured_match():
    from is_it_sugar.models import MeasuredMatch

    match = MeasuredMatch(source="afcd", id="afcd:F1", name="Broccoli, fresh, raw", note="Analysed", kinds=1)
    items = [
        Item(name="Fructose: 0.7 g per 100 g", status="not_tolerated", sugars=["fructose"], basis="afcd", reason="")
    ]
    out = prefs.apply(analyze.summarize(items, measured_food=match), ["broccoli"], query="broccoli")
    assert out.verdict == "ok" and out.measured_food == match


# ---- sugar present but not identified


def test_unidentified_sugars_is_caution():
    items = analyze.unidentified_sugars({"Total Sugars": 10.49}, None, [], [])
    assert [(i.name, i.status) for i in items] == [("Sugars: 10.49 g per 100 g", "caution")]
    r = analyze.summarize(items)
    assert r.verdict == "caution"


def test_unidentified_sugars_skipped_when_explained_or_zero():
    assert analyze.unidentified_sugars({"Total Sugars": 0}, None, [], []) == []
    assert analyze.unidentified_sugars({"Total Sugars": 5.0}, None, ["fructose", "sucrose"], []) == []
    flagged = [judged("Sucrose", sugars_=("sucrose",))]
    assert analyze.unidentified_sugars({"Total Sugars": 5.0}, None, [], flagged) == []
    assert (
        analyze.unidentified_sugars({}, local("Milk, cow, fluid, regular fat (~3.5%)"), ["fructose", "sucrose"], [])
        == []
    )


def test_varieties_differ_only_in_variety():
    from is_it_sugar import afcd

    apples = afcd.varieties(au("Apple, fuji, unpeeled, raw"))
    assert all(a.form == "unpeeled, raw" for a in apples) and len(apples) >= 9
    assert [c.name for c in afcd.varieties(au("Cheese, cheddar, natural, regular fat"))] == [
        "Cheese, cheddar, natural, regular fat"
    ]


def test_zero_total_sugars_is_one_line():
    items, _ = analyze.measured_sugars({}, local("Chicken, breast, lean flesh, raw"))
    assert [(i.name, i.status) for i in items] == [("Total sugars: 0 g per 100 g", "tolerated")]


def measured_item(sugar, grams):
    return Item(
        name=f"{sugar}: {grams} g", status="not_tolerated", sugars=[sugar], basis="afcd", reason="r", grams=grams
    )


def test_trace_rule_downgrades_small_measured_amounts():
    oats = analyze.summarize([measured_item("sucrose", 1.2)])  # load 0.6
    out = prefs.apply(oats, trace_limit=1.0)
    assert (out.verdict, out.headline, out.sugars) == ("caution", "Small amounts of sucrose", ["sucrose"])
    assert out.items[0].trace and len(out.items) == 1  # one item: its own status is the product's
    assert prefs.apply(oats, trace_limit=0).verdict == "avoid"  # 0 turns it off
    assert prefs.apply(oats, trace_limit=0.5).verdict == "avoid"


def test_very_low_load_is_tolerated():
    rice = analyze.summarize([measured_item("fructose", 0.1), measured_item("sucrose", 0.6)])  # load 0.4
    out = prefs.apply(rice, trace_limit=1.0)
    assert (out.verdict, out.headline) == ("ok", "Very low in fructose and sucrose")
    assert all(i.status == "tolerated" and i.trace for i in out.items)
    assert out.load is not None and (out.load.grams, out.load.per, out.load.status) == (0.4, "100 g", "tolerated")
    # A stricter personal limit wins: under 0.3 g only.
    assert prefs.apply(rice, trace_limit=0.3).verdict == "avoid"
    assert prefs.apply(rice, trace_limit=0).verdict == "avoid"


def test_each_item_by_its_own_amount_and_the_product_by_the_total():
    # 0.4 + 0.4 g: each under 0.5 g alone, 0.8 g together.
    items = [measured_item("fructose", 0.4), measured_item("sorbitol", 0.4)]
    r = prefs.apply(analyze.summarize(items), trace_limit=1.0)
    assert [i.status for i in r.items] == ["tolerated", "tolerated"]
    assert r.load is not None and (r.load.grams, r.load.status) == (0.8, "caution")  # sets the verdict
    assert r.verdict == "caution"


DIJON = Serving(grams=5, label="1 tsp (5 g)", max_sugars=0.5)


def test_dijon_per_serving():
    text = "WATER, MUSTARD SEED, DISTILLED VINEGAR, SALT, WHITE WINE, CITRIC ACID, TURMERIC, TARTARIC ACID, SPICES"
    parts = {i.name: i for i in ingredients.parse(text)}
    seed = analyze._measured_item(parts["MUSTARD SEED"], food("Spices, mustard seed, ground"), DIJON)
    assert seed.reason.endswith("At most 50% of a 5 g serving (listed 2nd): 2.5 g, so up to 0.047 g.")
    vinegar = analyze._measured_item(parts["DISTILLED VINEGAR"], food("Vinegar", "ciqual"), DIJON)
    assert vinegar.status == "tolerated"  # under the detection limit: none found
    turmeric = analyze._measured_item(parts["TURMERIC"], food("Turmeric, powder", "ciqual"), DIJON)
    result = analyze.summarize([seed, vinegar, turmeric], serving=DIJON)
    r = prefs.apply(result, trace_limit=1.0)
    assert r.verdict == "ok" and all(i.status == "tolerated" for i in r.items)
    assert r.load is not None and r.load.per == "1 tsp (5 g)" and r.load.grams < 0.1
    assert prefs.apply(result, trace_limit=0).verdict == "avoid"


def test_load_without_a_serving_or_with_nothing_to_weigh():
    oats = prefs.apply(analyze.summarize([measured_item("sucrose", 1.2)]), trace_limit=1.0)
    assert oats.load is not None and (oats.load.grams, oats.load.per) == (0.6, "100 g")  # sucrose counts half
    named = prefs.apply(analyze.summarize([measured_item("fructose", 0.1), judged("Sucrose")]), trace_limit=1.0)
    assert named.load is None  # a sugar with no amount: no total to show
    strict = prefs.apply(analyze.summarize([measured_item("fructose", 0.1)]), trace_limit=0)
    assert strict.load is not None and strict.load.status == "not_tolerated"  # 0 flags any amount


def test_label_total_sugars_caps_named_sugars():
    sugar, sorbitol = analyze._bu_decide("sugar"), analyze._bu_decide("sorbitol")
    assert sugar is not None and sorbitol is not None
    zero = Serving(grams=85, label="1 link (85 g)", max_sugars=0.5)
    r = prefs.apply(analyze.summarize([sugar], serving=zero), trace_limit=1.0)
    assert (r.items[0].status, r.verdict) == ("caution", "caution")  # at most 0.5 g a serving
    assert prefs.apply(analyze.summarize([sugar]), trace_limit=1.0).verdict == "avoid"  # no label: no amount
    # Sorbitol isn't counted in total sugars.
    assert prefs.apply(analyze.summarize([sugar, sorbitol], serving=zero), trace_limit=1.0).verdict == "avoid"


def test_serving_from_usda_label():
    label = {"servingSize": 5.0, "servingSizeUnit": "GRM", "householdServingFullText": "1 tsp"}
    assert analyze._serving(label, {"Total Sugars": 0.0}) == DIJON
    # 3 g on the label (60 g per 100 g of a 5 g serving) means under 3.5 g.
    assert analyze._serving(label, {"Total Sugars": 60.0}).max_sugars == 3.5
    assert analyze._serving(label, {}).max_sugars is None
    assert analyze._serving(label | {"servingSizeUnit": "g"}, {}) is not None  # some records use plain units
    assert analyze._serving(label | {"householdServingFullText": "None"}, {}).label == "5 g"  # as Cheetos has it


def test_trace_rule_sums_the_load_and_ignores_unmeasured():
    tomato = analyze.summarize([measured_item("fructose", 0.6), measured_item("sorbitol", 0.5)])  # load 1.1
    assert prefs.apply(tomato, trace_limit=1.0).verdict == "avoid"
    with_estimate = analyze.summarize([measured_item("fructose", 0.2), judged("Sucrose", sugars_=("sucrose",))])
    assert prefs.apply(with_estimate, trace_limit=1.0).verdict == "avoid"
    listed = analyze.summarize([bu("Sugar")], ingredients_text="sugar")
    assert prefs.apply(listed, trace_limit=5).verdict == "avoid"


def test_avoid_list_beats_trace_rule():
    r = analyze.summarize(
        [measured_item("fructose", 0.2)], food=Food(fdc_id=9, description="Cheese, cheddar", data_type="SR Legacy")
    )
    assert prefs.apply(r, avoid=["cheddar"], query="cheddar", trace_limit=1.0).verdict == "avoid"


def test_cache_drops_old_result_versions(tmp_path):
    from is_it_sugar.cache import Cache

    c = Cache(tmp_path / "c.sqlite3")
    for ns in ("text", "text:old", "text:new", "image:old", "ingredient"):
        c.set(ns, "k", 1)
    c.drop_other_versions(("text", "image"), "new")
    assert [ns for ns in ("text", "text:old", "text:new", "image:old", "ingredient") if c.get(ns, "k", 60)] == [
        "text:new",
        "ingredient",
    ]


# ---- Open Food Facts


@pytest.mark.parametrize(
    ("text", "expected"),
    [("016000275287", True), ("12345670", True), ("1234567", False), ("Cheerios", False), ("0160 0027 5287", False)],
)
def test_is_barcode(text, expected):
    assert off.is_barcode(text) is expected


def test_gtins_cover_usda_padding():
    # USDA lists UPC-A as 12, 13 or 14 digits.
    assert off.gtins("016000275287") == ["016000275287", "0016000275287", "00016000275287"]
    assert off.gtins("5010029225501") == ["5010029225501", "05010029225501"]


def test_off_product_prefers_english_ingredients():
    p = off._product(
        {
            "code": "3608580038047",
            "product_name": "Compote en gourde",
            "brands": "Andros, Andros Kids",
            "countries_tags": ["en:france", "en:united-kingdom"],
            "ingredients_text": "Pommes 96%, sucre",
            "ingredients_text_en": "apple, sugar",
            "lang": "fr",
        }
    )
    assert (p.brand, p.countries, p.ingredients, p.english) == (
        "Andros",
        ["France", "United Kingdom"],
        "apple, sugar",
        True,
    )
    p = off._product({"code": "1", "product_name": "Compote", "ingredients_text": "Pommes, sucre", "lang": "fr"})
    assert (p.ingredients, p.english) == ("Pommes, sucre", False)
    assert off._product({"code": "2", "lang": "en"}).ingredients is None


def test_prefs_avoid_matches_off_product_name():
    from is_it_sugar.models import OffMatch

    match = OffMatch(code="5000137001968", name="Twiglets", brand="Jacob's", countries=["United Kingdom"], url="u")
    r = analyze.summarize([item("tolerated")], off_product=match, ingredients_text="wholewheat flour")
    out = prefs.apply(r, avoid=["twiglet"], query="5000137001968")
    assert out.verdict == "avoid" and out.off_product == match
