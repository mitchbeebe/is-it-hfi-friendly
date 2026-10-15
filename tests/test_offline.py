"""Tests for the parts that don't call TypeSafe or FoodData Central."""

import io

import pytest
from PIL import Image

from is_it_sugar import analyze, images, ingredients, known, off, prefs, sugars
from is_it_sugar.models import Food, Item


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


@pytest.mark.parametrize(
    ("ingredient", "status", "basis"),
    [
        ("chocolate chips (sugar, cocoa butter)", "not_tolerated", "bu_table"),  # sugar inside brackets counts
        ("vegetable oil (includes one of the following: canola, soybean)", "tolerated", "afcd"),
        ("enriched flour (wheat flour, niacin, reduced iron)", "not_tolerated", "afcd"),  # measured sucrose
        ("Wholewheat Flour (80%)", "not_tolerated", "afcd"),
        ("ONIONS", "not_tolerated", "afcd"),
        ("Organic whole grain oats", "not_tolerated", "afcd"),
        ("SALT", "tolerated", "afcd"),
        ("VITAMIN B12", "tolerated", "known"),
        ("TRIPOTASSIUM PHOSPHATE", "tolerated", "known"),
        ("sodium acid pyrophosphate", "tolerated", "known"),
        ("modified corn starch", "tolerated", "bu_table"),
        ("palm oil", "tolerated", "known"),
        ("raising agents (ammonium bicarbonate, monocalcium phosphate)", "tolerated", "known"),
        ("BROCCOLI", "not_tolerated", "afcd"),
        ("ORGANIC GREEN PEPPER", "not_tolerated", "afcd"),  # bell pepper, not peppercorns
        ("ORGANIC PAPRIKA", "not_tolerated", "ciqual"),
        ("VINEGAR", "not_tolerated", "ciqual"),  # under detection limits; the limits count
        ("ORGANIC CHICKEN", "tolerated", "ciqual"),
        ("tomato concentrate", "not_tolerated", "ciqual"),
        ("ORGANIC BLACK PEPPER", "not_tolerated", "ciqual"),
        ("IN A NATURAL PORK CASING", "tolerated", "known"),
        ("DISTILLED VINEGAR", "not_tolerated", "ciqual"),
    ],
)
def test_decide_uses_bu_table_then_measured_foods(ingredient, status, basis):
    item = analyze._decide(ingredients.Ingredient(ingredient))
    assert item is not None and (item.status, item.basis) == (status, basis)


@pytest.mark.parametrize(
    "ingredient",
    [
        "natural smoke flavor",
        "natural flavor",
        "chocolate (dextrose, cocoa butter)",
        "yeast extract",
        "dried cranberries",
    ],
)
def test_decide_leaves_the_rest_to_jev(ingredient):
    assert analyze._decide(ingredients.Ingredient(ingredient)) is None


def test_measured_ingredient_is_an_upper_bound_for_the_trace_rule():
    flour = analyze._decide(ingredients.Ingredient("wheat flour"))
    assert flour is not None and flour.grams == 0.7 and flour.sugars == ["sucrose"]
    onion = analyze._decide(ingredients.Ingredient("onion"))
    assert onion is not None and onion.grams == pytest.approx(1.7 + 1.9 / 2)
    # Only measured amounts: flour alone is under 0.5 g, flour and onion under neither limit.
    assert prefs.apply(analyze.summarize([flour]), trace_limit=1.0).verdict == "ok"
    assert prefs.apply(analyze.summarize([flour, onion]), trace_limit=1.0).verdict == "avoid"


def test_parse_bounds_each_ingredients_share_of_the_product():
    text = "POPCORN, SUNFLOWER OIL, SEA SALT, CONTAINS 2% OR LESS OF: PAPRIKA, tomatoes 45%"
    shares = {i.name: (round(i.share, 3), i.why) for i in ingredients.parse(text)}
    assert shares == {
        "POPCORN": (1.0, "the first ingredient"),
        "SUNFLOWER OIL": (0.5, "listed 2nd"),
        "SEA SALT": (0.333, "listed 3rd"),
        "PAPRIKA": (0.02, "in the 2% or less part of the list"),
        "tomatoes 45%": (0.45, "declared as 45%"),
    }


def test_measured_ingredient_counts_at_its_share():
    paprika = ingredients.parse("popcorn, salt, contains 2% or less of: paprika")[2]
    item = analyze._decide(paprika)
    assert item is not None and item.basis == "ciqual"
    assert item.grams == pytest.approx((6.71 + 0.81 / 2) * 0.02)
    assert "in the 2% or less part of the list, so at most 2% of the product" in item.reason
    # Popcorn (0.43 g load) and up to 2% paprika (0.14 g): all measured, just over 0.5 g, so small amounts.
    items = [
        analyze._decide(i)
        for i in ingredients.parse("ORGANIC NON-GMO POPCORN, SEA SALT, CONTAINS 2% OR LESS OF: PAPRIKA")
    ]
    assert all(items) and items[0].basis == "ciqual"
    r = prefs.apply(analyze.summarize(items), trace_limit=1.0)
    assert (r.verdict, r.headline) == ("caution", "Small amounts of fructose and sucrose")
    assert prefs.apply(analyze.summarize(items[:2]), trace_limit=1.0).verdict == "ok"  # popcorn and salt alone


def test_known_foods_exist_and_are_measured():
    for label, name in known.FOODS.items():
        f = known._afcd(name)
        if known.is_sorbitol_fruit(f.name):
            assert f.sorbitol is not None, label


def test_label_note_needs_confidence():
    note = analyze._jev_item(
        "Dried", {"fructose": 0.05, "sucrose": 0, "sorbitol": 0, "unsure": 0.05, "none": 0.1, "label_note": 0.8}
    )
    assert (note.status, note.sugars) == ("tolerated", [])
    assert note.reason.startswith("Looks like a note on the label")
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
    assert r.items[0].status == "not_tolerated"


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
    assert [(i.name, i.allowed_by) for i in out.items][:2] == [("RAISINS", None), ("SALT", None)]  # unallowed first
    assert {i.allowed_by for i in out.items[2:]} == {"Tomatoes", "onion"}
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
    assert prefs.apply(r, []) is r
    assert prefs.apply(r, ["  ", "!!"]) is r


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


def test_afcd_data_is_whole_foods_without_borrowed_rows():
    from is_it_sugar import afcd

    data = afcd.foods()
    assert len(data) > 800
    assert all(f.derivation != "Borrowed" for f in data)
    assert not [f for f in data if f.name.lower().startswith("sauce, tomato")]  # processed: regional recipes


@pytest.mark.parametrize(
    ("query", "expected"),
    [("apples", "Apple"), ("bell pepper", "Capsicum"), ("cantaloupe", "Melon, rockmelon"), ("arugula", "Rocket")],
)
def test_afcd_search_with_us_names(query, expected):
    from is_it_sugar import afcd

    assert afcd.search(query)[0].name.startswith(expected)


def test_afcd_search_skips_processed_foods():
    from is_it_sugar import afcd

    assert afcd.search("ketchup") == []


def test_measured_sugars_shows_variety_ranges_and_beats_usda():
    items, measured = analyze.measured_sugars({"Fructose": 99.0}, au("Apple, fuji, unpeeled, raw"))
    by_sugar = {i.sugars[0]: i for i in items}
    assert measured == ["fructose", "sucrose", "sorbitol"]
    assert by_sugar["fructose"].name == "Fructose: 5.4–7.7 g per 100 g"
    assert by_sugar["fructose"].basis == "afcd"
    assert by_sugar["fructose"].reason.startswith(
        "Range across 10 kinds of apple (unpeeled, raw). Closest match: Apple, fuji"
    )
    assert by_sugar["sorbitol"].name.startswith("Sorbitol: 0.2–0.5 g")


def test_measured_sugars_zero_and_missing_fall_through():
    items, measured = analyze.measured_sugars({"Sucrose": 2.0}, au("Grape, green, raw"))
    # AFCD reports grape sucrose as 0, which wins over USDA's 2 g; zeros are listed as tolerated.
    assert [(i.name, i.status) for i in items] == [
        ("Fructose: 6–9.5 g per 100 g", "not_tolerated"),
        ("Sucrose: 0 g per 100 g", "tolerated"),
        ("Sorbitol: 0 g per 100 g", "tolerated"),
    ]
    assert all(i.basis == "afcd" for i in items)
    items, measured = analyze.measured_sugars({"Sucrose": 2.0}, None)
    assert measured == ["sucrose"] and items[0].basis == "fdc"


def test_prefs_keeps_afcd_match():
    from is_it_sugar.models import AfcdMatch

    match = AfcdMatch(id="F1", name="Broccoli, fresh, raw", derivation="Analysed", kinds=1)
    items = [
        Item(name="Fructose: 0.7 g per 100 g", status="not_tolerated", sugars=["fructose"], basis="afcd", reason="")
    ]
    out = prefs.apply(analyze.summarize(items, afcd_food=match), ["broccoli"], query="broccoli")
    assert out.verdict == "ok" and out.afcd_food == match


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
        analyze.unidentified_sugars({}, au("Milk, cow, fluid, regular fat (~3.5%)"), ["fructose", "sucrose"], []) == []
    )


def test_varieties_differ_only_in_variety():
    from is_it_sugar import afcd

    apples = afcd.varieties(au("Apple, fuji, unpeeled, raw"))
    assert all(a.form == "unpeeled, raw" for a in apples) and len(apples) >= 9
    assert [c.name for c in afcd.varieties(au("Cheese, cheddar, natural, regular fat"))] == [
        "Cheese, cheddar, natural, regular fat"
    ]


def test_zero_total_sugars_is_one_line():
    items, _ = analyze.measured_sugars({}, au("Chicken, breast, lean flesh, raw"))
    assert [(i.name, i.status) for i in items] == [("Total sugars: 0 g per 100 g", "tolerated")]


def measured_item(sugar, grams):
    return Item(
        name=f"{sugar}: {grams} g", status="not_tolerated", sugars=[sugar], basis="afcd", reason="r", grams=grams
    )


def test_trace_rule_downgrades_small_measured_amounts():
    oats = analyze.summarize([measured_item("sucrose", 1.2)])  # load 0.6
    out = prefs.apply(oats, trace_limit=1.0)
    assert (out.verdict, out.headline, out.sugars) == ("caution", "Small amounts of sucrose", ["sucrose"])
    assert out.items[0].trace and out.items[0].reason.startswith(
        "About 0.6 g fructose load per 100 g, under your 1 g limit."
    )
    assert prefs.apply(oats, trace_limit=0).verdict == "avoid"  # 0 turns it off
    assert prefs.apply(oats, trace_limit=0.5).verdict == "avoid"


def test_very_low_load_is_tolerated():
    rice = analyze.summarize([measured_item("fructose", 0.1), measured_item("sucrose", 0.6)])  # load 0.4
    out = prefs.apply(rice, trace_limit=1.0)
    assert (out.verdict, out.headline) == ("ok", "Very low in fructose and sucrose")
    assert all(i.status == "tolerated" and i.trace for i in out.items)
    assert out.items[0].reason.startswith("About 0.4 g fructose load per 100 g, under 0.5 g.")
    # A stricter personal limit wins: under 0.3 g only.
    assert prefs.apply(rice, trace_limit=0.3).verdict == "avoid"
    assert prefs.apply(rice, trace_limit=0).verdict == "avoid"


def test_label_with_zero_sugar_makes_fructose_and_sucrose_sources_caution():
    sausage = {"servingSize": 85.0, "servingSizeUnit": "GRM", "householdServingFullText": "1 link"}
    items = [
        analyze._decide(ingredients.Ingredient("RED PEPPER")),
        analyze._decide(ingredients.Ingredient("sugar")),
        analyze._decide(ingredients.Ingredient("sorbitol")),
        analyze._decide(ingredients.Ingredient("SALT")),
        *analyze.measured_sugars({"Total Sugars": 0.0}, None)[0],
    ]
    marked = analyze.label_zero_sugar([i for i in items if i], sausage)
    assert prefs.apply(analyze.summarize(marked), trace_limit=0).verdict == "avoid"  # 0 flags any amount
    out = prefs.apply_label_cap(marked)
    by_name = {i.name: i for i in out}
    assert by_name["RED PEPPER"].status == by_name["sugar"].status == "caution"
    assert by_name["sugar"].reason.startswith(
        "The label lists 0 g total sugars per serving (1 link, 85 g), so under 0.5 g."
    )
    assert by_name["sorbitol"].status == "not_tolerated"  # not counted in total sugars
    assert by_name["Total sugars: 0 g per serving"].status == "tolerated"
    r = analyze.summarize([i for i in out if i.name != "sorbitol"])
    assert (r.verdict, r.headline) == ("caution", "Small amounts of fructose and sucrose")


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
