"""Foods with measured sugars from AFCD, CIQUAL and USDA, found by full-text search.

Search only finds candidates; Jev decides which one, if any, is the food. Built by scripts/build_foods.py.
"""

import hashlib
import itertools
import json
import re
import sqlite3
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal

from . import sugars

DATA = Path(__file__).parent / "data" / "foods.json"
Source = Literal["afcd", "ciqual", "fdc"]
SOURCE_NAMES: dict[str, str] = {
    "afcd": "Australian data",
    "ciqual": "French data",
    "fdc": "USDA data",
}
_STOP = {"and", "or", "of", "with", "from", "in", "the", "a", "an", "to", "added", "for", "as"}
# Each table's groups of foods prepared from other ingredients: dishes, meals and baked goods. An ingredient on a
# label isn't one of these, so they aren't offered for it: potato starch isn't a roll made with potato starch.
# (AFCD's data keeps only whole and minimally processed foods already.)
PREPARED = {
    "Baby Foods",
    "Baked Products",
    "Fast Foods",
    "Meals, Entrees, and Side Dishes",
    "Restaurant Foods",
    "baby food",
    "starters and dishes",
    "cereal products / breads and similar",
    "cereal products / savoury biscuits",
    "sugar and confectionery / Viennese pastries",
    "sugar and confectionery / cakes and pastry",
    "sugar and confectionery / sweet biscuits",
}
# Retires cached ingredient answers when the data or what counts as prepared changes.
VERSION = hashlib.sha256(DATA.read_bytes() + repr(sorted(PREPARED)).encode()).hexdigest()[:12]


@dataclass(frozen=True)
class Food:
    id: str  # "afcd:F000110", "ciqual:9231", "fdc:170567"
    source: Source
    name: str
    fructose: float
    sucrose: float
    sorbitol: float | None  # None when not measured
    total_sugars: float | None
    below: tuple[str, ...]  # sugars reported only as under a detection limit; the limit is used
    note: str  # AFCD derivation or USDA dataset
    group: str = ""  # the table's food group: "Baked Products", "starters and dishes / soups"

    def amount(self, sugar: str) -> float | None:
        """Grams per 100 g, or None if not measured. A value only reported as under a detection limit
        counts as 0: the lab found none it could measure."""
        value = getattr(self, sugar)
        return 0.0 if value is not None and sugar in self.below else value

    @property
    def label(self) -> str:
        return f"{self.name}"

    @property
    def prepared(self) -> bool:
        """Made from other ingredients, as a dish, meal or baked good."""
        return self.group in PREPARED or self.group.split(" / ")[0] in PREPARED


def _food(f: dict) -> Food:
    # CIQUAL reports all polyols together, not sorbitol alone.
    sorbitol = None if f["source"] == "ciqual" else f.get("sorbitol")
    return Food(
        f["id"],
        f["source"],
        f["name"],
        f["fructose"],
        f["sucrose"],
        sorbitol,
        f.get("total_sugars"),
        tuple(f.get("below", ())),
        f.get("note", ""),
        f.get("group", ""),
    )


@cache
def _all() -> tuple[Food, ...]:
    return tuple(_food(f) for f in json.loads(DATA.read_text())["foods"])


@cache
def _by_id() -> dict[str, Food]:
    return {f.id: f for f in _all()}


@cache
def _index() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.execute("create virtual table f using fts5(name, aliases, tokenize='porter unicode61')")
    rows = json.loads(DATA.read_text())["foods"]
    db.executemany(
        "insert into f(rowid, name, aliases) values (?, ?, ?)",
        # Hyphens dropped as well as split, so "popcorn" finds "Pop-corn".
        ((i, f"{f['name']} {f['name'].replace('-', '')}", " ; ".join(f["aliases"])) for i, f in enumerate(rows)),
    )
    return db


def url(food_id: str) -> str:
    """The source's own page for a food: "fdc:170567", "ciqual:20047" or "afcd:F005972"."""
    source, code = food_id.split(":", 1)
    return {
        "afcd": "https://www.foodstandards.gov.au/science-data/food-nutrient-databases/afcd/search/food/{}",
        "ciqual": "https://ciqual.anses.fr/#/aliments/{}",
        "fdc": "https://fdc.nal.usda.gov/food-details/{}/nutrients",
    }[source].format(code)


def get(food_id: str) -> Food | None:
    return _by_id().get(food_id)


def _match(expr: str, sources: tuple[Source, ...], limit: int, prepared: bool = True) -> list[Food]:
    rows = (
        _index()
        .execute("select rowid from f where f match ? order by bm25(f, 2.0, 1.0) limit ?", (expr, limit * 10))
        .fetchall()
    )
    foods = _all()
    return [foods[r[0]] for r in rows if foods[r[0]].source in sources and (prepared or not foods[r[0]].prepared)][
        :limit
    ]


def search(
    query: str, sources: tuple[Source, ...] = ("afcd", "ciqual", "fdc"), limit: int = 12, prepared: bool = True
) -> list[Food]:
    """Candidate foods for `query`. Jev picks the actual match, so this aims to include it, not to rank it first.
    `prepared=False` leaves out dishes, meals and baked goods, for an ingredient on a label.

    Candidates are the best matches for each pair of adjacent words ("tomato concentrate"), then for all
    the words, then for each word alone. The last keeps a label's extra words from crowding out the food:
    in "organic non-GMO popcorn", foods matching "organic" rank first, but popcorn's own matches are offered too.
    """
    words = [w for w in re.findall(r"[a-z0-9]+", query.lower().replace("-", " ")) if w not in _STOP and len(w) > 1]
    words = list(dict.fromkeys(words))
    if not words:
        return []
    if len(words) == 1:
        return _match(f'"{words[0]}"', sources, limit, prepared)
    found: list[Food] = []
    for a, b in itertools.pairwise(words):
        found += _match(f'"{a} {b}"', sources, 2, prepared)
    found += _match(" OR ".join(f'"{w}"' for w in words), sources, 4, prepared)
    for w in words:
        found += _match(f'"{w}"', sources, 2, prepared)
    return list(dict.fromkeys(found))[:limit]


def load(f: Food) -> float:
    """Fructose load per 100 g: fructose + half of sucrose + sorbitol."""
    return (f.amount("fructose") or 0.0) + (f.amount("sucrose") or 0.0) / 2 + (f.amount("sorbitol") or 0.0)


@cache
def _names() -> tuple[dict[str, tuple[int, ...]], dict[str, tuple[int, ...]]]:
    """Normalized names -> rows: full names and Open Food Facts synonyms, and main names ("Garlic" for
    "Garlic, roasted/baked")."""
    exact: dict[str, list[int]] = {}
    main: dict[str, list[int]] = {}
    for i, f in enumerate(json.loads(DATA.read_text())["foods"]):
        for n in dict.fromkeys(sugars.key(n) for n in (f["name"], *f["aliases"])):
            exact.setdefault(n, []).append(i)
        main.setdefault(sugars.key(re.split(r"[,(]", f["name"])[0]), []).append(i)
    return {k: tuple(v) for k, v in exact.items()}, {k: tuple(v) for k, v in main.items()}


def exact(name: str) -> Food | None:
    """The food `name` is, when a table's own name or an Open Food Facts synonym is exactly that name.

    When several foods share it ("Onion, raw" in CIQUAL and "Onions, raw" in USDA), the one with the
    most sugar, so a tie errs toward caution.
    """
    rows = _names()[0].get(sugars.key(name))
    return max((_all()[r] for r in rows), key=load) if rows else None


def candidates(name: str, limit: int = 12) -> list[Food]:
    """Likely foods for an ingredient with no exact match, most likely first: foods with `name` as their
    main name ("garlic" -> "Garlic, roasted/baked"), foods named like the end of `name` ("organic non-GMO
    popcorn" -> popcorn), then full-text matches. Dishes, meals and baked goods aren't ingredients, so
    they're left out."""
    exact_names, main_names = _names()
    words = sugars.key(name).split()
    rows: list[int] = list(main_names.get(" ".join(words), ())[:6])
    for i in range(1, len(words)):
        end = " ".join(words[i:])
        rows += exact_names.get(end, ()) + main_names.get(end, ())[:4]
    found = [f for f in (_all()[r] for r in rows) if not f.prepared] + search(name, limit=limit, prepared=False)
    return list(dict.fromkeys(found))[:limit]
