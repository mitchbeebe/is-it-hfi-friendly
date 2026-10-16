"""Measured sugars for whole foods from the Australian Food Composition Database (AFCD).

Built by scripts/build_afcd.py. Source: Food Standards Australia New Zealand, AFCD Release 3, CC BY 4.0.
"""

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path

DATA = Path(__file__).parent / "data" / "afcd.json"


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
def _by_id() -> dict[str, AfcdFood]:
    return {f.id: f for f in foods()}


def by_id(food_id: str) -> AfcdFood | None:
    """The AFCD food for an id from foods.py ("afcd:F000110") or AFCD itself ("F000110")."""
    return _by_id().get(food_id.removeprefix("afcd:"))


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
