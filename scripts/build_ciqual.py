"""Build src/is_it_sugar/data/ciqual.json: label ingredient names -> measured sugars from CIQUAL.

    uv run python scripts/build_ciqual.py [ciqual.xls] [ingredients.txt]

Open Food Facts' ingredient taxonomy links ingredient names (with synonyms and plurals) to CIQUAL foods.
CIQUAL, the French food composition table, measures fructose and sucrose for many of them, including
spices, meats and vegetables that AFCD doesn't cover.

Sources:
- ANSES. Ciqual French food composition table, 2020. Licence Ouverte / Open Licence (Etalab).
  https://ciqual.anses.fr
- Open Food Facts ingredient taxonomy, ODbL. https://github.com/openfoodfacts/openfoodfacts-server
"""

import io
import json
import re
import sys
import urllib.request
from pathlib import Path

import xlrd

CIQUAL_URL = "https://ciqual.anses.fr/cms/sites/default/files/inline-files/Table%20Ciqual%202020_ENG_2020%2007%2007.xls"
# Pinned so a rebuild gives the same names.
TAXONOMY_COMMIT = "e521216912b7b999039709d2196aa8c4bcb7f0f9"
TAXONOMY_URL = (
    "https://raw.githubusercontent.com/openfoodfacts/openfoodfacts-server/"
    f"{TAXONOMY_COMMIT}/taxonomies/food/ingredients.txt"
)
OUT = Path(__file__).resolve().parents[1] / "src/is_it_sugar/data/ciqual.json"
# Common label names the taxonomy doesn't link to a CIQUAL food directly.
EXTRA_NAMES = {"black pepper": "11015", "ground black pepper": "11015"}
COLUMNS = {"fructose": "fructose (g/100g)", "sucrose": "sucrose (g/100g)", "polyols": "Polyols (g/100g)"}


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
    sheet = xlrd.open_workbook(file_contents=data).sheet_by_index(0)
    header = [str(c.value) for c in sheet.row(0)]
    col = {k: header.index(v) for k, v in COLUMNS.items()}
    code, name = header.index("alim_code"), header.index("alim_nom_eng")
    foods = {}
    for r in range(1, sheet.nrows):
        row = [str(c.value) for c in sheet.row(r)]
        values = {k: _value(row[i]) for k, i in col.items()}
        if values["fructose"][0] is None or values["sucrose"][0] is None:
            continue  # only foods with both sugars measured
        foods[str(int(float(row[code])))] = {
            "name": row[name].strip(),
            **{k: v for k, (v, _) in values.items()},
            "below": [k for k, (_, below) in values.items() if below],
        }
    return foods


def taxonomy(text: str) -> dict[str, str]:
    """English names and synonyms -> CIQUAL code, for entries that have one."""
    names: dict[str, str] = {}
    for block in re.split(r"\n\s*\n", text):
        lines = block.splitlines()
        code = next((m.group(1) for ln in lines if (m := re.match(r"ciqual_food_code:en:\s*(\d+)", ln))), None)
        english = next((ln[3:] for ln in lines if ln.startswith("en:") and not ln.startswith("en: <")), None)
        if code and english:
            for n in english.split(","):
                names.setdefault(n.strip().lower(), code)
    return names


def main(ciqual_path: str | None = None, taxonomy_path: str | None = None) -> None:
    data = Path(ciqual_path).read_bytes() if ciqual_path else urllib.request.urlopen(CIQUAL_URL).read()
    text = (
        Path(taxonomy_path).read_text()
        if taxonomy_path
        else io.TextIOWrapper(urllib.request.urlopen(TAXONOMY_URL), encoding="utf-8").read()
    )
    foods = ciqual(data)
    names = {n: c for n, c in sorted((taxonomy(text) | EXTRA_NAMES).items()) if c in foods}
    used = {c: foods[c] for c in sorted(set(names.values()))}
    OUT.write_text(
        json.dumps(
            {
                "source": "ANSES. Ciqual French food composition table, 2020. Licence Ouverte / Open Licence. "
                "Ingredient names from the Open Food Facts taxonomy (ODbL).",
                "url": "https://ciqual.anses.fr",
                "names": names,
                "foods": used,
            },
            indent=0,
        )
    )
    print(f"wrote {len(names)} names for {len(used)} foods to {OUT}")


if __name__ == "__main__":
    main(*sys.argv[1:])
