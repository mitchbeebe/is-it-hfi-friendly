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
  verdict. Matching errs toward not allowing: an ingredient is allowed when its name contains the term, ignoring plurals
  ("onion" covers "onion powder"). A whole food without an ingredient list is allowed only by its main name ("broccoli"
  covers "Broccoli, raw", but "tomato" doesn't cover "Tomato soup"). A BU table sugar is allowed only by one of its own
  names, so "corn" never allows "corn syrup". Adding a sugar BU marks not tolerated shows a warning.
- **Ingredients you avoid** (e.g. natural flavors). Anything matching makes the result "Avoid", even when BU rates it as
  tolerated. Matching errs toward flagging: any ingredient or food whose name contains the term, BU sugars included. For
  photos, each avoided term becomes an extra question about the label.

- **Small amounts.** When a food's measured fructose load (fructose + half of sucrose + sorbitol, per 100 g, using the
  top of each range) is under a limit, it shows Caution instead of Avoid. The default is 1 g, so cheddar (0.2), oats
  (0.6) and broccoli (0.6) are Caution while tomato (1.9) and apple (10.5) stay Avoid. Set 0 to flag any amount. Only
  measured amounts count: ingredient lists and AI estimates are never downgraded, and the avoid list still wins.

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
   sugars, and "natural flavor" is described instead.

Labels list ingredients by weight, most first, so the nth ingredient is at most 1/n of the product, anything after
"contains 2% or less of" is at most 2%, and a declared percentage ("tomatoes 45%") is used as given. A measured
ingredient counts at its amount per 100 g times that share: paprika in the 2% part adds at most 0.14 g fructose load
per 100 g of the product. Values a table reports only as under a detection limit count as 0.

For a USDA branded product, the label's serving and total sugars bound things further. US labels round sugars to the
nearest gram and list under 0.5 g as 0 g, so a serving has at most the listed amount plus 0.5 g. That caps fructose
and sucrose together, including sugars an ingredient list names with no amount. Sorbitol and other sugar alcohols
aren't counted in total sugars, so they still need their own measurement.

**Small amounts**: measured amounts are weighed by fructose load (fructose + half of sucrose + sorbitol, the formula
HFI studies use), each item by its own amount and the product by all of them together. The product's load is shown
under the verdict ("Up to 0.062 g fructose load per 1 tsp (5 g)") and counts toward it. Ingredients are listed in
label order. Per 100 g, under 0.5 g is OK (the lowest tier in Spanish HFI guidance and the strictest
cut-off Italian centres use) and under your limit (1 g by default) is Caution. When the label gives a serving size,
the load is judged per serving instead: under 0.1 g is OK and up to 0.5 g is Caution, against a daily limit of 1 to
1.5 g in HFI guidance. A sugar with no amount, or an AI estimate, keeps the food at Avoid. A limit of 0 flags any
amount.

**Food names**: searched in [USDA FoodData Central](https://fdc.nal.usda.gov/), generic and branded foods separately so
brands don't crowd out generic foods, and in the bundled AFCD and CIQUAL foods. One Jev call picks the USDA result that
matches what you typed (you can pick another under "Not it?") and the AFCD or CIQUAL food that is the same whole food.
Then the app uses, in order of preference:

1. the product's ingredient list (branded foods), checked as above;
2. for whole foods, measured sugars from the
   [Australian Food Composition Database](https://www.foodstandards.gov.au/science-data/food-nutrient-databases/afcd)
   (AFCD) or [CIQUAL](https://ciqual.anses.fr/), the French table. Both are mostly lab-analysed, and AFCD reports
   sorbitol. Where AFCD lists several kinds of a food (11 raw apples), amounts are shown as a range, for example
   "Fructose: 5.4–7.7 g per 100 g";
3. measured fructose and sucrose from USDA, for any sugar those don't report;
4. for sorbitol in a whole food: present in apples, pears and stone fruits (the only foods AFCD measures it in), and
   otherwise not expected;
5. otherwise, a Jev estimate for each remaining sugar, labelled as an AI judgment. A food with no sugar data at all is
   Caution, not OK.

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
Legacy or Foundation food with fructose and sucrose measured (or total sugars of 0), about 6,900 in all. Open Food
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
- Generic foods in FoodData Central (Survey/FNDDS) usually have no ingredient list or per-sugar breakdown, so their result is
  an estimate. Packaged versions vary.
- HFI tolerance depends on the amount. When USDA reports under 1 g per 100 g, the app says so but still reports the
  sugar as present.
