from typing import Literal

from pydantic import BaseModel, computed_field

from . import foods
from .sugars import Status, Sugar

Verdict = Literal["avoid", "caution", "ok"]
Basis = Literal["bu_table", "research", "jev", "fdc", "afcd", "ciqual", "label", "settings"]


class Item(BaseModel):
    """One thing found in the food, and what it means for HFI."""

    name: str
    status: Status
    sugars: list[Sugar] = []
    basis: Basis
    reason: str
    bu_name: str | None = None
    allowed_by: str | None = None  # the Settings term that allowed it
    avoided_by: str | None = None  # the Settings term that flagged it; wins over allowed_by
    grams: float | None = None  # measured amount per 100 g (top of the range), for the trace rule
    url: str | None = None  # the source's page for the food the amounts come from
    trace: bool = False  # measured, but under the person's fructose-load limit


class Food(BaseModel):
    fdc_id: int
    description: str
    brand: str | None = None
    data_type: str

    @computed_field
    @property
    def url(self) -> str:
        return foods.url(f"fdc:{self.fdc_id}")


class MeasuredMatch(BaseModel):
    """The food composition table entry used for sugar values."""

    source: Literal["afcd", "ciqual", "fdc"]
    id: str
    name: str
    note: str  # how AFCD derived the values, or the USDA dataset
    kinds: int  # how many varieties the ranges span

    @computed_field
    @property
    def url(self) -> str:
        return foods.url(self.id)


class Serving(BaseModel):
    """A packaged product's serving, from its label."""

    grams: float
    label: str  # "1 tsp (5 g)"
    # The most total sugars a serving can have: labels round to the nearest gram and list under 0.5 g as 0 g.
    max_sugars: float | None = None


class Portion(BaseModel):
    """A household measure of a whole food, from USDA, that the person can judge it by."""

    label: str  # "1 clove (3 g)"
    grams: float


class Load(BaseModel):
    """The product's fructose load: the most its measured ingredients, and its label's total sugars, allow."""

    grams: float
    per: str  # "1 tsp (5 g)", or "100 g" when there's no serving size
    status: Status
    sugars: list[Sugar]
    capped: bool = False  # by the label's total sugars


class OffMatch(BaseModel):
    """The Open Food Facts product whose ingredient list was used."""

    code: str
    name: str
    brand: str | None = None
    countries: list[str] = []
    url: str


class Alternative(BaseModel):
    """Another match to offer under "Not the right food?": a USDA food, or an Open Food Facts barcode."""

    fdc_id: int | None = None
    barcode: str | None = None
    label: str


class Result(BaseModel):
    verdict: Verdict
    headline: str
    sugars: list[Sugar]
    items: list[Item]
    food: Food | None = None
    measured_food: MeasuredMatch | None = None
    off_product: OffMatch | None = None
    ingredients_text: str | None = None
    serving: Serving | None = None
    load: Load | None = None
    portions: list[Portion] = []  # a whole food's household measures; the person picks one to judge by
    alternatives: list[Alternative] = []
    notes: list[str] = []
