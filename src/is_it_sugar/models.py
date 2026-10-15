from typing import Literal

from pydantic import BaseModel

from .sugars import Status, Sugar

Verdict = Literal["avoid", "caution", "ok"]
Basis = Literal["bu_table", "jev", "fdc", "afcd", "ciqual", "known", "label", "settings"]


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
    trace: bool = False  # measured, but under the person's fructose-load limit
    # Why a product label caps this item at a trace ("The label lists 0 g total sugars per serving...").
    # Settings decide whether that makes it Caution.
    label_cap: str | None = None


class Food(BaseModel):
    fdc_id: int
    description: str
    brand: str | None = None
    data_type: str


class AfcdMatch(BaseModel):
    """The Australian Food Composition Database entry used for sugar values."""

    id: str
    name: str
    derivation: str
    kinds: int  # how many varieties the ranges span


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
    afcd_food: AfcdMatch | None = None
    off_product: OffMatch | None = None
    ingredients_text: str | None = None
    alternatives: list[Alternative] = []
    notes: list[str] = []
