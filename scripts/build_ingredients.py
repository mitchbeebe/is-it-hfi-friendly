"""Build src/is_it_sugar/data/ingredients.json: label ingredient names -> measured sugars.

    uv run python scripts/build_ingredients.py [ciqual.xls] [ingredients.txt]

Open Food Facts' ingredient taxonomy links ingredient names (with synonyms and plurals) to foods in
CIQUAL, the French food composition table, and in USDA FoodData Central. Each name keeps the CIQUAL
food when it has fructose and sucrose measured, otherwise the USDA one when that does. Needs
FOODDATA_CENTRAL_API_KEY (read from .env) to fetch the USDA foods.

Sources:
- ANSES. Ciqual French food composition table, 2020. Licence Ouverte / Open Licence (Etalab).
  https://ciqual.anses.fr
- USDA FoodData Central (public domain). https://fdc.nal.usda.gov
- Open Food Facts ingredient taxonomy, ODbL. https://github.com/openfoodfacts/openfoodfacts-server
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

CIQUAL_URL = "https://ciqual.anses.fr/cms/sites/default/files/inline-files/Table%20Ciqual%202020_ENG_2020%2007%2007.xls"
# Pinned so a rebuild gives the same names.
TAXONOMY_COMMIT = "e521216912b7b999039709d2196aa8c4bcb7f0f9"
TAXONOMY_URL = (
    "https://raw.githubusercontent.com/openfoodfacts/openfoodfacts-server/"
    f"{TAXONOMY_COMMIT}/taxonomies/food/ingredients.txt"
)
FDC_FOODS = "https://api.nal.usda.gov/fdc/v1/foods"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src/is_it_sugar/data/ingredients.json"
# Common label names the taxonomy doesn't link to a measured food: name -> food key.
EXTRA_NAMES = {
    "black pepper": "ciqual:11015",
    "ground black pepper": "ciqual:11015",
    "popcorn": "ciqual:9231",
    "popcorn kernels": "ciqual:9231",
    "popping corn": "ciqual:9231",
}
COLUMNS = {"fructose": "fructose (g/100g)", "sucrose": "sucrose (g/100g)", "polyols": "Polyols (g/100g)"}
FDC_NUTRIENTS = {"fructose": 212, "sucrose": 210}


def _value(raw: str) -> tuple[float | None, bool]:
    """CIQUAL cell -> (grams, below_detection). "2,6" -> 2.6; "< 0,2" -> (0.2, True); "-" -> None."""
    raw = str(raw).strip().replace(",", ".")
    if raw in ("", "-", "nan"):
        return None, False
    if raw.lower() == "traces":
        return 0.0, True
    if raw.startswith("<"):
        return float(raw[1:]), True
    return float(raw), False


def ciqual(data: bytes) -> dict[str, dict]:
    """CIQUAL foods with fructose and sucrose both measured, keyed "ciqual:<code>"."""
    sheet = xlrd.open_workbook(file_contents=data).sheet_by_index(0)
    header = [str(c.value) for c in sheet.row(0)]
    col = {k: header.index(v) for k, v in COLUMNS.items()}
    code, name = header.index("alim_code"), header.index("alim_nom_eng")
    foods = {}
    for r in range(1, sheet.nrows):
        row = [str(c.value) for c in sheet.row(r)]
        values = {k: _value(row[i]) for k, i in col.items()}
        if values["fructose"][0] is None or values["sucrose"][0] is None:
            continue
        foods[f"ciqual:{int(float(row[code]))}"] = {
            "source": "ciqual",
            "name": row[name].strip(),
            **{k: v for k, (v, _) in values.items()},
            "below": [k for k, (_, below) in values.items() if below],
        }
    return foods


def usda(ids: list[int]) -> dict[str, dict]:
    """USDA foods with fructose and sucrose both measured, keyed "fdc:<id>"."""
    load_dotenv(ROOT / ".env")
    foods = {}
    with httpx.Client(timeout=60) as http:
        for i in range(0, len(ids), 20):
            r = http.post(
                FDC_FOODS,
                params={"api_key": os.environ["FOODDATA_CENTRAL_API_KEY"]},
                json={"fdcIds": ids[i : i + 20], "format": "abridged", "nutrients": list(FDC_NUTRIENTS.values())},
            )
            r.raise_for_status()
            for f in r.json():
                by_number = {int(n["number"]): n["amount"] for n in f.get("foodNutrients", []) if "amount" in n}
                values = {k: by_number.get(n) for k, n in FDC_NUTRIENTS.items()}
                if None in values.values():
                    continue
                foods[f"fdc:{f['fdcId']}"] = {
                    "source": "fdc",
                    "name": f["description"],
                    **values,
                    "polyols": None,
                    "below": [],
                }
    return foods


def taxonomy(text: str) -> tuple[dict[str, str], dict[str, str]]:
    """English names and synonyms -> CIQUAL food key, and -> USDA food key, for entries linked to one."""
    to_ciqual: dict[str, str] = {}
    to_usda: dict[str, str] = {}
    for block in re.split(r"\n\s*\n", text):
        lines = block.splitlines()
        english = next((ln[3:] for ln in lines if ln.startswith("en:")), None)
        if not english:
            continue
        names = [n.strip().lower() for n in english.split(",")]
        for prop, prefix, out in (("ciqual_food_code", "ciqual", to_ciqual), ("usda_fdc_code", "fdc", to_usda)):
            code = next((m.group(1) for ln in lines if (m := re.match(rf"{prop}:en:\s*(\d+)", ln))), None)
            if code:
                for n in names:
                    out.setdefault(n, f"{prefix}:{code}")
    return to_ciqual, to_usda


def main(ciqual_path: str | None = None, taxonomy_path: str | None = None) -> None:
    data = Path(ciqual_path).read_bytes() if ciqual_path else urllib.request.urlopen(CIQUAL_URL).read()
    text = (
        Path(taxonomy_path).read_text()
        if taxonomy_path
        else io.TextIOWrapper(urllib.request.urlopen(TAXONOMY_URL), encoding="utf-8").read()
    )
    to_ciqual, to_usda = taxonomy(text)
    foods = ciqual(data) | usda(sorted({int(k[4:]) for k in to_usda.values()}))
    names: dict[str, str] = {}
    for mapping in (EXTRA_NAMES, to_ciqual, to_usda):  # first measured link wins
        for n, k in sorted(mapping.items()):
            if k in foods:
                names.setdefault(n, k)
    used = {k: foods[k] for k in sorted(set(names.values()))}
    OUT.write_text(
        json.dumps(
            {
                "source": "ANSES, Ciqual French food composition table 2020 (Licence Ouverte / Open Licence); "
                "USDA FoodData Central (public domain). Ingredient names from the Open Food Facts taxonomy (ODbL).",
                "names": names,
                "foods": used,
            },
            indent=0,
        )
    )
    counts = {s: sum(1 for f in used.values() if f["source"] == s) for s in ("ciqual", "fdc")}
    print(f"wrote {len(names)} names for {counts['ciqual']} CIQUAL and {counts['fdc']} USDA foods to {OUT}")


if __name__ == "__main__":
    main(*sys.argv[1:])
