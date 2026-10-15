"""Open Food Facts: packaged foods from anywhere in the world, by barcode or name.

The data is crowd-sourced and recipes differ by country, so results say where a product is sold.
Open Food Facts data: Open Database License (ODbL).
"""

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

PRODUCT = "https://world.openfoodfacts.org/api/v2/product/{code}.json"
SEARCH = "https://search.openfoodfacts.org/search"
# Open Food Facts asks every app to identify itself.
HEADERS = {"User-Agent": "is-it-hfi-friendly/0.1 (+https://is-it-hfi-friendly.fly.dev)"}
FIELDS = "code,product_name,product_name_en,brands,countries_tags,ingredients_text,ingredients_text_en,lang"
# Each hit costs a product request, and Open Food Facts rate-limits those.
SEARCH_SIZE = 5


@dataclass(frozen=True)
class Product:
    code: str
    name: str
    brand: str | None
    countries: list[str] = field(default_factory=list)
    ingredients: str | None = None
    english: bool = True  # whether `ingredients` is in English

    @property
    def url(self) -> str:
        return f"https://world.openfoodfacts.org/product/{self.code}"

    @property
    def label(self) -> str:
        return " | ".join(x for x in (self.name, self.brand, ", ".join(self.countries)) if x)


def is_barcode(text: str) -> bool:
    return re.fullmatch(r"\d{8,14}", text) is not None


def gtins(code: str) -> list[str]:
    """A barcode as databases may store it: USDA keeps UPCs as 12, 13 or 14 digits with leading zeros."""
    core = code.lstrip("0")
    return list(dict.fromkeys([code] + [core.zfill(n) for n in (12, 13, 14) if n >= len(core)]))


def _country(tag: str) -> str:
    """ "en:united-kingdom" -> "United Kingdom"."""
    return tag.split(":", 1)[-1].replace("-", " ").title()


def _product(p: dict[str, Any]) -> Product:
    english = (p.get("ingredients_text_en") or "").strip()
    other = (p.get("ingredients_text") or "").strip()
    brand = (p.get("brands") or "").split(",")[0].strip() or None
    return Product(
        code=p["code"],
        name=(p.get("product_name_en") or p.get("product_name") or "").strip() or "Unnamed product",
        brand=brand,
        countries=[_country(t) for t in p.get("countries_tags") or []],
        ingredients=english or other or None,
        english=bool(english) or p.get("lang") == "en",
    )


async def product(http: httpx.AsyncClient, code: str) -> Product | None:
    r = await http.get(PRODUCT.format(code=code), params={"fields": FIELDS}, headers=HEADERS)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    body = r.json()
    return _product(body["product"]) if body.get("status") == 1 else None


async def search(http: httpx.AsyncClient, query: str) -> list[Product]:
    """Products matching a name that have an ingredient list, English ones only when there are any.

    Search results don't include ingredients, so each hit is fetched.
    """
    words = re.sub(r"[^\w\s'&.]", " ", query)  # the search takes Lucene syntax, e.g. field:value
    q = f'{words} states_tags:"en:ingredients-completed"'
    r = await http.get(SEARCH, params={"q": q, "page_size": SEARCH_SIZE, "langs": "en"}, headers=HEADERS)
    r.raise_for_status()
    codes = [h["code"] for h in r.json().get("hits", []) if h.get("code")]
    found = await asyncio.gather(*(product(http, c) for c in codes))
    listed = [p for p in found if p is not None and p.ingredients]
    english = [p for p in listed if p.english]
    return english or listed
