"""Measured sugars for whole foods from the Australian Food Composition Database (AFCD).

Built by scripts/build_afcd.py. Source: Food Standards Australia New Zealand, AFCD Release 3, CC BY 4.0.
"""

import json
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from .sugars import key

DATA = Path(__file__).parent / "data" / "afcd.json"

# US names -> the Australian names AFCD uses, applied to the query before searching.
SYNONYMS = {
    "bell pepper": "capsicum",
    "cantaloupe": "rockmelon",
    "arugula": "rocket",
    "cilantro": "coriander",
    "garbanzo": "chickpea",
    "shrimp": "prawn",
    "rutabaga": "swede",
    "beet": "beetroot",
    "romaine": "cos lettuce",
    "golden raisin": "sultana",
    "kiwi": "kiwifruit",
    "courgette": "zucchini",
    "aubergine": "eggplant",
    "swiss chard": "silverbeet",
    "chard": "silverbeet",
    "ground beef": "beef mince",
    "butternut squash": "pumpkin butternut",
    "kumara": "sweet potato",
    "honeydew": "honey dew melon",
    "papaya": "pawpaw papaya",
}


@dataclass(frozen=True)
class AfcdFood:
    id: str
    name: str
    group: str
    derivation: str
    fructose: float | None
    sucrose: float | None
    sorbitol: float | None
    total_sugars: float | None

    @property
    def parts(self) -> list[str]:
        """ "Apple, fuji, unpeeled, raw" -> ["apple", "fuji", "unpeeled", "raw"]."""
        return [p.strip().lower() for p in self.name.split(",")]

    @property
    def first(self) -> str:
        return self.parts[0]

    @property
    def last(self) -> str:
        return self.parts[-1]

    @property
    def form(self) -> str:
        """How it's prepared, shared by its varieties: "unpeeled, raw"."""
        return ", ".join(self.parts[2:])


@cache
def foods() -> tuple[AfcdFood, ...]:
    return tuple(AfcdFood(**f) for f in json.loads(DATA.read_text())["foods"])


@cache
def _tokens(name: str) -> frozenset[str]:
    return frozenset(key(name).split())


def search(query: str, limit: int = 20) -> list[AfcdFood]:
    """Foods sharing words with `query`, best first. Jev picks the actual match from these."""
    q = key(query)
    for us, au in SYNONYMS.items():
        q = re.sub(rf"\b{us}\b", au, q)
    words = set(q.split())
    if not words:
        return []
    scored = []
    for f in foods():
        hits = len(words & _tokens(f.name))
        if hits:
            # More shared words first, then foods whose main name matches, then raw, then shorter names.
            scored.append((-hits, not (words & _tokens(f.first)), f.last != "raw", len(f.name), f))
    return [s[-1] for s in sorted(scored, key=lambda s: s[:-1])[:limit]]


def varieties(food: AfcdFood) -> list[AfcdFood]:
    """Varieties of the same food prepared the same way. AFCD names put the variety second, so these
    differ only there: "Apple, fuji, unpeeled, raw" and "Apple, gala, unpeeled, raw", but not
    "Apple, dried" or "Cheese, cheddar, processed, regular fat" next to the natural cheddar."""
    mine = food.parts
    return [
        f
        for f in foods()
        if f.group == food.group and len(f.parts) == len(mine) and f.first == mine[0] and f.parts[2:] == mine[2:]
    ]
