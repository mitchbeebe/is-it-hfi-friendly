"""Build src/is_it_hfi_friendly/data/afcd.json from the Australian Food Composition Database (AFCD).

    uv run python scripts/build_afcd.py

Source: Food Standards Australia New Zealand (2025). Australian Food Composition Database - Release 3.
https://www.foodstandards.gov.au/science-data/food-nutrient-databases/afcd/data-files
Licensed CC BY 4.0. Rows FSANZ "borrowed" from other countries' tables are third-party data the licence
doesn't cover, so they're left out.

Only whole and minimally processed foods are kept. Processed foods and dishes follow regional recipes
(Australian ketchup is sweetened with sucrose, US ketchup with HFCS), so USDA stays the source for those.
"""

import io
import json
import re
import sys
import urllib.request
from pathlib import Path

from openpyxl import load_workbook

URL = "https://www.foodstandards.gov.au/sites/default/files/2025-12/AFCD%20Release%203%20-%20Nutrient%20profiles.xlsx"
RELEASE = "AFCD Release 3 (December 2025)"
OUT = Path(__file__).resolve().parents[1] / "src/is_it_hfi_friendly/data/afcd.json"

# 3-digit AFCD food groups that are whole or minimally processed foods.
WHOLE_GROUPS = {
    "113": "fruit and vegetable juices",
    "118": "waters",
    "121": "grains, flours and rice",
    "141": "butter",
    "144": "plant oils",
    "145": "other fats and oils",
    "151": "fish",
    "152": "seafood",
    "153": "other fish",
    "161": "pome fruit",
    "162": "berry fruit",
    "163": "citrus fruit",
    "164": "stone fruit",
    "165": "tropical fruit",
    "166": "other fruit",
    "167": "mixed fruit",
    "168": "dried fruit",
    "171": "eggs",
    "181": "beef, lamb and pork",
    "182": "other meats",
    "183": "poultry",
    "184": "organ meats",
    "191": "milk",
    "193": "cream",
    "194": "cheese",
    "221": "seeds",
    "222": "nuts",
    "240": "other vegetables",
    "241": "potatoes",
    "242": "cabbage and greens",
    "243": "root vegetables",
    "244": "herbs and leaves",
    "245": "beans and peas",
    "246": "tomatoes",
    "247": "other fruiting vegetables",
    "248": "other vegetables",
    "251": "legumes",
    "271": "sugars, honey and syrups",
    "313": "spices",
    "331": "reptiles",
}
COLUMNS = {"fructose": "Fructose", "sucrose": "Sucrose", "sorbitol": "Sorbitol", "total_sugars": "Total sugars"}


def main(path: str | None = None) -> None:
    data = Path(path).read_bytes() if path else urllib.request.urlopen(URL).read()
    ws = load_workbook(io.BytesIO(data), read_only=True)["All solids & liquids per 100 g"]
    rows = ws.iter_rows(values_only=True)
    header = next(r for r in rows if r and r[0] == "Public Food Key")
    names = [re.sub(r"\s+", " ", str(h or "")).split(" (")[0].strip() for h in header]
    col = {key: names.index(name) for key, name in COLUMNS.items()}

    foods = []
    for r in rows:
        if not r[0] or r[2] == "Borrowed" or str(r[1])[:3] not in WHOLE_GROUPS:
            continue
        values = {key: (None if r[i] is None else float(r[i])) for key, i in col.items()}
        if all(values[s] is None for s in ("fructose", "sucrose", "sorbitol")):
            continue
        foods.append({"id": r[0], "name": r[3], "group": str(r[1])[:3], "derivation": r[2], **values})

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "source": f"Food Standards Australia New Zealand. {RELEASE}. CC BY 4.0.",
                "url": "https://www.foodstandards.gov.au/science-data/food-nutrient-databases/afcd",
                "foods": foods,
            },
            indent=0,
        )
    )
    print(f"wrote {len(foods)} foods to {OUT}")


if __name__ == "__main__":
    main(*sys.argv[1:])
