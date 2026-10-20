# Is it HFI-friendly?

A small, mobile-first web app that checks whether a food contains **fructose, sucrose, or sorbitol**, for people on a
hereditary fructose intolerance (HFI) diet.

- **Type** a food ("ketchup", "Cheerios", "apple") or paste an ingredient list.
- **Scan** a package's barcode with the camera.
- **Photo**: take or upload a picture of a package's ingredient list.

Results show a verdict (Avoid / Caution / Looks OK), which sugars were found, and each ingredient with the reason for its
rating.

**Settings** (`#settings`) keeps two lists, since people with HFI differ, especially with vegetables:

- **Foods you tolerate.** Matching items still appear in results, marked "Allowed by you", but don't count toward the
  verdict. The fructose load line gives both amounts ("Up to 0.62 g …; up to 0.37 g not counting potato, which you
  tolerate"), and the headline says "OK with your allowed foods" when counting them would have changed the verdict. Matching errs toward not allowing: an ingredient is allowed when its name contains the term, ignoring plurals
  ("onion" covers "onion powder"). A whole food without an ingredient list is allowed only by its main name ("broccoli"
  covers "Broccoli, raw", but "tomato" doesn't cover "Tomato soup"). A BU table sugar is allowed only by one of its own
  names, so "corn" never allows "corn syrup". Adding a sugar BU marks not tolerated shows a warning.
- **Ingredients you avoid** (e.g. natural flavors). Anything matching makes the result "Avoid", even when BU rates it as
  tolerated. Matching errs toward flagging: any ingredient or food whose name contains the term, BU sugars included. For
  photos, each avoided term becomes an extra question about the label.

- **Small amounts.** A food is judged by the measured fructose load (fructose + half of sucrose + sorbitol, using the
  top of each range) in a serving: the label's, an amount you pick for a whole food ("Judged per 1 clove"), or else 100
  g. A slider with two handles sets the tiers, in g per serving: under the first is OK, between them Caution, and from
  the second up Avoid. The defaults are 0.5 g and 1 g, so 100 g of cheddar (0.2) is OK, oats (0.6) and broccoli (0.8)
  are Caution, and tomato (1.9) and apple (10.5) are Avoid. A handle pushes the other along when they meet; both at 0
  flags any amount. Only measured amounts count:
  ingredient lists and AI estimates are never downgraded, and the avoid list still wins.

**Recent** lists the last 10 food names and barcodes checked, under the Type and Scan tabs. Tapping one re-runs it
(answered from the cache, with your current lists), and "‹ Back" above a result returns to the list. Photos aren't
kept. Like the lists, it's stored only in this browser.

A term can be on only one list; adding it to one removes it from the other, and avoid wins on the server either way. The
lists are stored in the browser (localStorage) and sent with each check; `prefs.py` applies them.

> The results are a reading aid, not medical advice. Always read the label and check with your doctor or dietitian.

## Run it

```sh
uv sync
uv run is-it-sugar            # http://localhost:8000
# or, with auto-reload:
uv run fastapi dev src/is_it_sugar/app.py
```

`.env` must define `TYPESAFE_API_KEY` and `FOODDATA_CENTRAL_API_KEY`. `uv run fastapi dev` also works (with auto-reload),
via `[tool.fastapi]` in `pyproject.toml`.

**Cost controls**

- Results are cached for 7 days in SQLite (`.cache.sqlite3`, or set `IS_IT_SUGAR_CACHE`), keyed by the normalized text or
  the photo's hash. Jev's rating for each ingredient is cached for 90 days, so new products reuse ratings for "salt",
  "sugar", "natural flavors" and so on. Your lists are applied after the cache, so changing them never re-runs a check.
- Photo checks skip sugars BU rates simply "Tolerated", since they can't change the verdict (108 questions per photo).
- Each IP gets 30 checks a minute and 300 a day (`LIMITS` in `app.py`), in memory. Behind Tailscale Funnel or another
  local proxy, uvicorn takes the client address from `X-Forwarded-For`. Requests made directly to localhost all share
  127.0.0.1.

To try it on a phone, open `http://<your-computer's-LAN-IP>:8000` while on the same network. Browsers only allow the
camera on https pages (or localhost), so the Scan tab needs the deployed app or an https tunnel such as Tailscale Funnel.

## How it decides

The [Boston University HFI sugar table](https://www.bu.edu/aldolase/HFI/treatment/sugar_table.htm) is the source of
truth. Its tolerance column maps to a status:

| BU tolerance | Status |
| --- | --- |
| Not Tolerated | not tolerated → **Avoid** |
| Tolerated depending on purity, Caution, Tolerance Varies | **Caution** |
| Tolerated, Tolerated (Questionable safety) | tolerated |

Where published research disagrees with BU or gives an amount, the table follows the research instead, and those
results are marked "Research" with a link to it:

| Sweetener | BU | Here | Source |
| --- | --- | --- | --- |
| Maltitol, mannitol | Tolerated depending on purity | not tolerated | prohibited by all 15 (maltitol) and 14 of 15 (mannitol) Italian metabolic centres; maltitol releases sorbitol, and the liver turns some mannitol into fructose ([Bruni 2026](https://doi.org/10.3390/nu18162609)) |
| Isomalt | not listed | not tolerated | prohibited by 14 of 15 centres; releases sorbitol (Bruni 2026) |
| Isomaltulose (Palatinose) | not listed | not tolerated | digested into glucose and fructose ([Health Canada](https://www.canada.ca/en/health-canada/services/food-nutrition/genetically-modified-foods-other-novel-foods/approved-products/isomaltulose-palatinose.html)) |
| Stevia | Not Tolerated | caution | steviol glycosides don't involve fructose; centres differ over the purity of blends (Bruni 2026) |
| Steviol glycosides, stevia leaf extract, Reb A/M | (under Stevia) | tolerated | as above |
| Polydextrose, Litesse | Not Tolerated | not tolerated, weighed at up to 6 g sorbitol per 100 g | the sorbitol is mostly bound into the polymer, free glucose and sorbitol are at most 6% combined ([JECFA](https://www.fao.org/fileadmin/user_upload/jecfa_additives/docs/Monograph1/Additive-314.pdf)), and polydextrose is barely absorbed (Bruni 2026) |

The worst status among the ingredients sets the verdict.

**Ingredient lists** (any text with a comma, or starting with "Ingredients"): split on top-level commas in code
(`ingredients.py`). Brackets stay with their ingredient, so "vegetable oil (canola, soybean)" is one ingredient. Each
one is checked in this order:

1. the BU table and common label spellings (`sugars.py`), across the whole ingredient including its brackets, so the
   sugar in "chocolate chips (sugar, cocoa butter)" counts. The longest name wins, so "high fructose corn syrup" isn't
   read as "corn syrup";
2. an earlier answer for the same ingredient name, kept for 90 days;
3. everything else goes to TypeSafe's Jev model in one call per list, with a question per ingredient. Each question
   offers up to 12 measured foods that full-text search finds for it (`foods.py`), plus descriptions: contains
   fructose / sucrose / sorbitol, unsure, none, or a note on the label. Jev picks the food when it's the same
   ingredient in the same form, so "organic non-GMO popcorn" becomes "Snacks, popcorn, air-popped" with measured
   sugars, and "natural flavor" is described instead. Its likeliest answer wins, "not listed" included; candidates'
   probabilities aren't added up, since different foods aren't one answer. Foods each table groups as prepared from
   other ingredients (dishes, meals, baked goods and baby food: `PREPARED` in `foods.py`) aren't offered, so potato
   starch isn't matched to a roll made with it. A food named exactly like the ingredient still matches ("bread
   crumbs" is "Breadcrumbs"), and food names typed in still find dishes.

Labels list ingredients by weight, most first, so the nth ingredient is at most 1/n of the product, anything after
"contains 2% or less of" is at most 2%, and a declared percentage ("tomatoes 45%") is used as given. A measured
ingredient counts at its amount per 100 g times that share: paprika in the 2% part adds at most 0.14 g fructose load
per 100 g of the product. Values a table reports only as under a detection limit count as 0.

Those bounds can't all hold at once: for the 8th ingredient to be 1/8 of the product, so are the seven before it. Any
shares that fall down the list and add up to 1 are a mix of "the first m ingredients in equal parts", so the product's
load is at most the largest average of the first m ingredients' loads (with a "2% or less" part, of mixes of two such
recipes that have that part at its most). Potato gnocchi with flakes 2nd and turmeric 8th comes to 0.51 g per 100 g
rather than the 1.01 g its ingredients' own bounds add up to. Each ingredient still shows its own bound. Lists with
a declared percentage, and labels that don't fit (a "2% or less" part from the first ingredient), add up the
ingredients' own bounds instead.

For a USDA branded product, the label's serving and total sugars bound things further. US labels round sugars to the
nearest gram and list under 0.5 g as 0 g, so a serving has at most the listed amount plus 0.5 g. That caps fructose
and sucrose together, including sugars an ingredient list names with no amount. Sorbitol and other sugar alcohols
aren't counted in total sugars, so they still need their own measurement. Open Food Facts products use the serving
size their page records, when it gives one in grams, and its total sugars the same way: per serving for a product sold
only in the US, whose label gives them per serving, and otherwise per 100 g, as EU and Australian labels give them. A
label's "0 g" means under 0.5 g, so a load capped there is OK at the default 0.5 g cut-off.

**Small amounts**: measured amounts are weighed by fructose load (fructose + half of sucrose + sorbitol, the formula HFI
studies use), each item by its own amount and the product by all of them together. The product's load is shown under the
verdict ("Up to 0.062 g fructose load per 1 tsp (5 g)") and decides the verdict for them: each item with a measured
sugar shows Caution, since it has the sugar, even when the product as a whole is OK. Ingredients are listed in label
order. One rule judges the load in the amount eaten: the label's serving, or an amount you pick, or else 100 g. Under
0.5 g is OK: the lowest tier in Spanish HFI guidance (0.5 g per 100 g) and the strictest cut-off Italian centres use,
and half the low end of the 1 to 1.5 g daily limit guidance gives. From 0.5 g up to your limit (1 g per serving by
default, the low end of that daily limit) is Caution, and above it Avoid. So a 42 g pouch of Cheez-It (up to 0.19 g) and
100 g of guacamole (0.28 g) are both OK, 100 g of chili with meat and beans (0.73 g) is Caution, and a 250 g serving of
a food with 0.5 g per 100 g (1.25 g) is Avoid. A sugar with no amount, or an AI estimate, keeps the food at Avoid.
Polydextrose has a known most (6 g free sorbitol per 100 g), so on an ingredient list it's weighed by its share like a
measured food: 7th on the label of a 10 g serving, it adds at most 0.086 g. A limit of 0 flags any amount.

**Food names**: searched in [USDA FoodData Central](https://fdc.nal.usda.gov/), generic and branded foods separately so
brands don't crowd out generic foods, and in the bundled AFCD and CIQUAL foods. One Jev call picks the USDA result that
matches what you typed (you can pick another under "Not it?") and the AFCD or CIQUAL food that is the same whole food.

A whole food matched to an SR Legacy or Foundation food offers that food's household measures in a "Judged per"
dropdown: garlic has 1 tsp (2.8 g), 1 clove (3 g), 3 cloves (9 g) and 1 cup (136 g). Picking one judges the load per
that amount, like a label's serving, so a clove of garlic or a tablespoon of chives is OK. Search results don't
include the measures, so they take one more USDA request. RACC is left out: it's FDA's amount for a whole category,
85 g for any vegetable, garlic included. Per 100 g stays the default, since nothing says which measure is usual.

In results, USDA's data types are named for what they are: Foundation is "USDA lab-tested", SR Legacy "USDA reference
values", Survey (FNDDS) "USDA typical recipe", and Branded "Manufacturer's label".

Then the app uses, in order of preference:

1. the product's ingredient list (branded foods), checked as above;
2. for a dish, USDA's typical recipe (Survey, FNDDS), which gives each ingredient's weight. Each ingredient is
   checked like one on a label, but at its known share rather than the "listed 3rd, so at most a third" limit: Pad
   Thai with chicken is 2.5% brown sugar, about 1.2 g fructose load per 100 g. Unlike on a label, a sugar in a recipe
   is weighed by its measured amount rather than deciding the verdict by name, so chili with 0.49% sugar comes to
   about 0.73 g. The weights are before cooking, so water lost in cooking isn't counted. A recipe of one food is
   judged as that food, and so is a whole food Jev matches to measured data: USDA's "Apple, raw" is a recipe of five
   apple varieties, but the measured apple has its sorbitol. Search results include the recipe, so it costs no extra
   request. Survey foods "not further specified" ("Taco, NFS") are left out of the search: their recipes list other
   dishes ("Taco, fish", "Taco, meat, no cheese") rather than ingredients, so a specific dish is matched instead, and
   can be changed under "Not the right food?";
3. for whole foods, measured sugars from the
   [Australian Food Composition Database](https://www.foodstandards.gov.au/science-data/food-nutrient-databases/afcd)
   (AFCD) or [CIQUAL](https://ciqual.anses.fr/), the French table. Both are mostly lab-analysed, and AFCD reports
   sorbitol. Where AFCD lists several kinds of a food (11 raw apples), amounts are shown as a range, for example
   "Fructose: 5.4–7.7 g per 100 g";
4. measured fructose and sucrose from USDA, for any sugar those don't report;
5. otherwise, a Jev estimate for each remaining sugar, labelled as an AI judgment. A food with no sugar data at all is
   Caution, not OK. Sorbitol is the exception: without a measurement, a whole food is taken to have none. It's mostly
   added to prepared foods, and the whole foods that have it (apples, pears, stone fruits) have far more fructose.
   CIQUAL reports polyols together, so only AFCD's sorbitol is used.

When Jev says the name is a brand and USDA has no ingredient list for it (common for non-US products such as Twiglets
or Jaffa Cakes), the app searches [Open Food Facts](https://world.openfoodfacts.org/) for products that have one, and
Jev picks the match. Open Food Facts is crowd-sourced and recipes differ by country, so the result says where the
product is sold, and other countries' versions are offered under "Not the right food?".

**Barcodes**: the Scan tab reads EAN-13, EAN-8, UPC-A and UPC-E codes with the browser's `BarcodeDetector`, or with
[barcode-detector](https://github.com/Sec-ant/barcode-detector) (ZXing in WebAssembly, loaded from jsDelivr) where the
browser has none, such as iPhone Safari. The server looks the code up in USDA's branded foods and Open Food Facts at
the same time. USDA wins when it has the ingredient list, since manufacturers supply it; otherwise Open Food Facts'
list is used, and failing both, an estimate from the product name. Typing 8–14 digits does the same lookup.

The AFCD data ships with the app as `src/is_it_sugar/data/afcd.json`, built by `uv run python scripts/build_afcd.py`
(re-run it for a new release). It keeps only whole and minimally processed foods: processed foods follow regional
recipes (Australian ketchup uses sucrose, US ketchup uses HFCS), so USDA stays the source for those. Rows FSANZ borrowed
from other countries' tables are left out, since the licence doesn't cover them. AFCD data: Food Standards Australia New
Zealand, AFCD Release 3, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

`src/is_it_sugar/data/foods.json` is built by `uv run python scripts/build_foods.py`: every AFCD, CIQUAL and USDA SR
Legacy or Foundation food with fructose and sucrose measured (or total sugars of 0), about 6,900 in all, with each
table's food group (USDA's category comes from its search endpoint, since the list endpoint leaves it out). Open Food
Facts' ingredient taxonomy (pinned to a commit) adds the English names and synonyms it links to CIQUAL and USDA foods,
so "red bell pepper" finds "Sweet pepper, red, raw". The app indexes them with SQLite full-text search at startup.
CIQUAL values below a detection limit ("< 0.2") count at the limit. CIQUAL data: ANSES, Ciqual 2020,
[Licence Ouverte](https://www.etalab.gouv.fr/licence-ouverte-open-licence/). USDA data is public domain.

Open Food Facts data is under the [Open Database License](https://opendatacommons.org/licenses/odbl/1-0/), credited in
the page footer. Products are queried per request. Open Food Facts rate-limits product reads, so a name search lets Jev
pick from up to 5 search results that have ingredients, then fetches only that product. If Open Food Facts is
unreachable, name searches fall back to the USDA result.

**Photos**: resized to fit TypeSafe's image limits (`images.py`). Then one request asks Jev about the label: whether each BU
sugar name BU doesn't fully tolerate appears, plus common fruits and vegetables and catch-alls for other fruit, syrups, and sugar alcohols. Where one
name is part of another (corn syrup / high fructose corn syrup), a follow-up Choice in the same request checks whether the
shorter name is listed on its own.

## Deploy (Fly.io)

```sh
fly apps create is-it-hfi-friendly
fly secrets import < .env      # TYPESAFE_API_KEY, FOODDATA_CENTRAL_API_KEY
fly deploy --ha=false
```

The app runs at https://is-it-hfi-friendly.fly.dev. `fly.toml` runs one small machine that suspends when idle (waking
takes a few hundred ms, and costs the same as stopping), with a 1 GB volume at `/data` for the cache. Rate limits read
the client address from `Fly-Client-IP` (set by `CLIENT_IP_HEADER`), since `X-Forwarded-For` can be forged.

## Layout

```
src/is_it_sugar/
  app.py          FastAPI routes: GET /, POST /api/check/text, POST /api/check/image, GET /api/sugars
  prefs.py        applies the Settings lists (tolerate / avoid) to a result
  afcd.py         Australian Food Composition Database variety ranges
  off.py          Open Food Facts product lookup (barcode) and search (name)
  foods.py        full-text search over measured foods (AFCD, CIQUAL, USDA)
  data/afcd.json  the AFCD subset, built by scripts/build_afcd.py
  data/foods.json measured foods for search, built by scripts/build_foods.py
  cache.py        SQLite cache for results and ingredient ratings
  ratelimit.py    per-IP limits
  analyze.py      FoodData Central lookups, TypeSafe questions, verdicts
  sugars.py       the BU table and name matching
  ingredients.py  ingredient-list parsing
  images.py       photo normalization
  models.py       response schema
  static/index.html  the whole UI (no build step)
tests/            offline tests: uv run pytest
```

## Notes and limits

- TypeSafe image support is marked "dreamer-ready" (preview). It worked on the printed labels and Open Food Facts photos I
  tested, but blurry or curved text can cause misses. The app warns when the text looks hard to read.
- Generic foods in FoodData Central have no ingredient list, and Survey (FNDDS) foods no per-sugar breakdown, so a
  dish is judged by USDA's typical recipe and a plain food partly by estimate. Recipes and packaged versions vary.
- HFI tolerance depends on the amount. When USDA reports under 1 g per 100 g, the app says so but still reports the
  sugar as present.
