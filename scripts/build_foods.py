"""Build src/is_it_sugar/data/foods.json: every food with measured sugars, for full-text search.

    uv run python scripts/build_foods.py [ciqual.xls] [ingredients.txt]

Rows come from three food composition tables, keeping foods whose fructose and sucrose are both
measured, or whose total sugars are 0:
- AFCD, the Australian table: src/is_it_sugar/data/afcd.json (run scripts/build_afcd.py first).
- CIQUAL, the French table: ANSES, Ciqual 2020, Licence Ouverte / Open Licence (Etalab).
- USDA FoodData Central, SR Legacy and Foundation foods (public domain). Needs FOODDATA_CENTRAL_API_KEY
  (read from .env).

Open Food Facts' ingredient taxonomy (ODbL) adds the English names and synonyms it links to CIQUAL and
USDA foods, so "red bell pepper" also finds "Sweet pepper, red, raw". Pass local copies of the CIQUAL
spreadsheet and the taxonomy file when downloading them fails.
"""

import io
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

import httpx
import xlrd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src/is_it_sugar/data/foods.json"
AFCD = ROOT / "src/is_it_sugar/data/afcd.json"
CIQUAL_URL = "https://ciqual.anses.fr/cms/sites/default/files/inline-files/Table%20Ciqual%202020_ENG_2020%2007%2007.xls"
# Pinned so a rebuild gives the same names.
TAXONOMY_COMMIT = "e521216912b7b999039709d2196aa8c4bcb7f0f9"
TAXONOMY_URL = (
    "https://raw.githubusercontent.com/openfoodfacts/openfoodfacts-server/"
    f"{TAXONOMY_COMMIT}/taxonomies/food/ingredients.txt"
)
FDC_LIST = "https://api.nal.usda.gov/fdc/v1/foods/list"
FDC_SEARCH = "https://api.nal.usda.gov/fdc/v1/foods/search"
FDC_NUTRIENTS = {"fructose": "212", "sucrose": "210", "total_sugars": "269"}
# US names for the Australian ones AFCD uses.
AFCD_ALIASES = {
    "capsicum": "bell pepper",
    "rockmelon": "cantaloupe",
    "rocket": "arugula",
    "coriander": "cilantro",
    "chickpea": "garbanzo bean",
    "prawn": "shrimp",
    "swede": "rutabaga",
    "beetroot": "beet",
    "cos lettuce": "romaine",
    "sultana": "golden raisin",
    "kiwifruit": "kiwi",
    "silverbeet": "swiss chard",
    "beef mince": "ground beef",
    "pumpkin, butternut": "butternut squash",
    "honey dew melon": "honeydew",
    "pawpaw": "papaya",
    "cornflour": "cornstarch",
}


def _row(id_: str, source: str, name: str, aliases: list[str], note: str, group: str, **values) -> dict | None:
    """A food, or None when its fructose and sucrose aren't known. Total sugars of 0 means none of either."""
    if values["total_sugars"] == 0:
        # No sugar at all, including any reported only as under a detection limit.
        values["fructose"] = values["sucrose"] = 0.0
        values["below"] = [s for s in values["below"] if s not in ("fructose", "sucrose")]
    if values["fructose"] is None or values["sucrose"] is None:
        return None
    return {"id": id_, "source": source, "name": name, "aliases": aliases, "note": note, "group": group, **values}


def afcd() -> list[dict]:
    rows = []
    for f in json.loads(AFCD.read_text())["foods"]:
        aliases = [us for au, us in AFCD_ALIASES.items() if au in f["name"].lower()]
        values = {k: f[k] for k in ("fructose", "sucrose", "sorbitol", "total_sugars")}
        rows.append(
            _row(f"afcd:{f['id']}", "afcd", f["name"], aliases, f["derivation"], f["group"], below=[], **values)
        )
    return [r for r in rows if r]


def _ciqual_value(raw: str) -> tuple[float | None, bool]:
    """CIQUAL cell -> (grams, below_detection). "2,6" -> 2.6; "< 0,2" -> (0.2, True); "-" -> None."""
    raw = str(raw).strip().replace(",", ".")
    if raw in ("", "-", "nan"):
        return None, False
    if raw.lower() == "traces":
        return 0.0, True
    if raw.startswith("<"):
        return float(raw[1:]), True
    return float(raw), False


def ciqual(data: bytes, aliases: dict[str, list[str]]) -> list[dict]:
    sheet = xlrd.open_workbook(file_contents=data).sheet_by_index(0)
    header = [str(c.value) for c in sheet.row(0)]
    columns = {
        "fructose": "fructose (g/100g)",
        "sucrose": "sucrose (g/100g)",
        "sorbitol": "Polyols (g/100g)",  # mostly sorbitol in the fruits that have it; foods.py decides
        "total_sugars": "Sugars (g/100g)",
    }
    col = {k: header.index(v) for k, v in columns.items()}
    code_col, name_col = header.index("alim_code"), header.index("alim_nom_eng")
    group_cols = header.index("alim_grp_nom_eng"), header.index("alim_ssgrp_nom_eng")
    rows = []
    for r in range(1, sheet.nrows):
        row = [str(c.value) for c in sheet.row(r)]
        parsed = {k: _ciqual_value(row[i]) for k, i in col.items()}
        code = f"ciqual:{int(float(row[code_col]))}"
        below = [k for k, (_, b) in parsed.items() if b and k != "total_sugars"]
        values = {k: v for k, (v, _) in parsed.items()}
        group = " / ".join(row[c].strip() for c in group_cols if row[c].strip())  # "starters and dishes / soups"
        rows.append(
            _row(code, "ciqual", row[name_col].strip(), aliases.get(code, []), "", group, below=below, **values)
        )
    return [r for r in rows if r]


def usda_categories(http: httpx.Client, data_type: str) -> dict[int, str]:
    """fdcId -> USDA food category ("Baked Products"). The list endpoint leaves categories out; search has them."""
    found: dict[int, str] = {}
    page = 1
    while True:
        r = http.post(
            FDC_SEARCH,
            params={"api_key": os.environ["FOODDATA_CENTRAL_API_KEY"]},
            json={"query": "*", "dataType": [data_type], "pageSize": 200, "pageNumber": page},
        )
        r.raise_for_status()
        foods = r.json().get("foods", [])
        if not foods:
            return found
        found |= {f["fdcId"]: f.get("foodCategory") or "" for f in foods}
        page += 1


def usda(aliases: dict[str, list[str]]) -> list[dict]:
    load_dotenv(ROOT / ".env")
    rows = []
    with httpx.Client(timeout=60) as http:
        for data_type in ("Foundation", "SR Legacy"):
            categories = usda_categories(http, data_type)
            page = 1
            while True:
                r = http.post(
                    FDC_LIST,
                    params={"api_key": os.environ["FOODDATA_CENTRAL_API_KEY"]},
                    json={"dataType": [data_type], "pageSize": 200, "pageNumber": page},
                )
                r.raise_for_status()
                foods = r.json()
                if not foods:
                    break
                for f in foods:
                    by_number = {n.get("number"): n.get("amount") for n in f.get("foodNutrients", [])}
                    values = {k: by_number.get(n) for k, n in FDC_NUTRIENTS.items()}
                    code = f"fdc:{f['fdcId']}"
                    row = _row(
                        code,
                        "fdc",
                        f["description"],
                        aliases.get(code, []),
                        data_type,
                        categories.get(f["fdcId"], ""),
                        sorbitol=None,
                        below=[],
                        **values,
                    )
                    if row:
                        rows.append(row)
                page += 1
    return rows


def taxonomy(text: str) -> dict[str, list[str]]:
    """Food id ("ciqual:20087", "fdc:170108") -> the English names and synonyms Open Food Facts links to it."""
    aliases: dict[str, list[str]] = {}
    for block in re.split(r"\n\s*\n", text):
        lines = block.splitlines()
        english = next((ln[3:] for ln in lines if ln.startswith("en:")), None)
        if not english:
            continue
        names = [n.strip().lower() for n in english.split(",") if n.strip()]
        for prop, prefix in (("ciqual_food_code", "ciqual"), ("usda_fdc_code", "fdc")):
            if code := next((m.group(1) for ln in lines if (m := re.match(rf"{prop}:en:\s*(\d+)", ln))), None):
                aliases.setdefault(f"{prefix}:{code}", []).extend(
                    n for n in names if n not in aliases.get(f"{prefix}:{code}", [])
                )
    return aliases


def main(ciqual_path: str | None = None, taxonomy_path: str | None = None) -> None:
    data = Path(ciqual_path).read_bytes() if ciqual_path else urllib.request.urlopen(CIQUAL_URL).read()
    text = (
        Path(taxonomy_path).read_text()
        if taxonomy_path
        else io.TextIOWrapper(urllib.request.urlopen(TAXONOMY_URL), encoding="utf-8").read()
    )
    aliases = taxonomy(text)
    rows = afcd() + ciqual(data, aliases) + usda(aliases)
    OUT.write_text(
        json.dumps(
            {
                "sources": {
                    "afcd": "Food Standards Australia New Zealand, AFCD Release 3 (CC BY 4.0)",
                    "ciqual": "ANSES, Ciqual French food composition table 2020 (Licence Ouverte / Open Licence)",
                    "fdc": "USDA FoodData Central, SR Legacy and Foundation foods (public domain)",
                    "aliases": f"Open Food Facts ingredient taxonomy, commit {TAXONOMY_COMMIT} (ODbL)",
                },
                "foods": rows,
            },
            separators=(",", ":"),
        )
    )
    counts = {s: sum(1 for r in rows if r["source"] == s) for s in ("afcd", "ciqual", "fdc")}
    print(f"wrote {len(rows)} foods ({counts}) to {OUT.relative_to(ROOT)}, {OUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main(*sys.argv[1:])
