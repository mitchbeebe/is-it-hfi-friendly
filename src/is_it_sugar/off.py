"""Open Food Facts: packaged foods from anywhere in the world, by barcode or name.

The data is crowd-sourced and recipes differ by country, so results say where a product is sold.
Open Food Facts data: Open Database License (ODbL).
"""

import re
from dataclasses import dataclass, field
from typing import Any

import httpx

PRODUCT = "https://world.openfoodfacts.org/api/v2/product/{code}.json"
SEARCH = "https://search.openfoodfacts.org/search"
# Open Food Facts asks every app to identify itself.
HEADERS = {"User-Agent": "is-it-hfi-friendly/0.1 (+https://is-it-hfi-friendly.fly.dev)"}
FIELDS = (
    "code,product_name,product_name_en,brands,countries_tags,ingredients_text,ingredients_text_en,lang,"
    "serving_size,serving_quantity,serving_quantity_unit"
)
SEARCH_FIELDS = "code,product_name,product_name_en,brands,countries_tags,lang"
SEARCH_SIZE = 5


@dataclass(frozen=True)
class Product:
    code: str
    name: str
    brand: str | None
    countries: list[str] = field(default_factory=list)
    ingredients: str | None = None
    english: bool = True  # whether `ingredients` is in English
    serving_grams: float | None = None
    serving_size: str | None = None  # as written on the label: "1 portion (28.349 g)", "30 g"

    @property
    def url(self) -> str:
        return f"https://world.openfoodfacts.org/product/{self.code}"

    @property
    def serving_label(self) -> str:
        size = self.serving_size or ""
        if re.search(r"\d\s*(g|ml)\b", size, re.IGNORECASE):
            return size  # "30 g", "1 portion (28.349 g)"
        return f"{size} ({self.serving_grams:g} g)" if size else f"{self.serving_grams:g} g"

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
    # Products list brands as "A, B"; search hits as ["A", "B"].
    brands = p.get("brands") or ""
    brand = (brands.split(",") if isinstance(brands, str) else brands or [""])[0].strip() or None
    try:
        grams = float(p.get("serving_quantity") or 0) or None
    except TypeError, ValueError:
        grams = None
    if (p.get("serving_quantity_unit") or "g").lower() not in ("g", "ml"):
        grams = None  # ml is close enough to g for the drinks and sauces that use it
    return Product(
        code=p["code"],
        name=(p.get("product_name_en") or p.get("product_name") or "").strip() or "Unnamed product",
        brand=brand,
        countries=[_country(t) for t in p.get("countries_tags") or []],
        ingredients=english or other or None,
        english=bool(english) or p.get("lang") == "en",
        serving_grams=grams,
        serving_size=(p.get("serving_size") or "").strip() or None,
    )


async def product(http: httpx.AsyncClient, code: str) -> Product | None:
    r = await http.get(PRODUCT.format(code=code), params={"fields": FIELDS}, headers=HEADERS)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    body = r.json()
    return _product(body["product"]) if body.get("status") == 1 else None


async def search(http: httpx.AsyncClient, query: str) -> list[Product]:
    """Products matching a name that have an ingredient list, English-language ones only when there are any.

    Search results don't include ingredients, so `ingredients` is None until the chosen one is
    fetched with `product`. That keeps it to one product request, since Open Food Facts rate-limits those.
    """
    words = re.sub(r"[^\w\s'&.]", " ", query)  # the search takes Lucene syntax, e.g. field:value
    q = f'{words} states_tags:"en:ingredients-completed"'
    params = {"q": q, "page_size": SEARCH_SIZE, "langs": "en", "fields": SEARCH_FIELDS}
    r = await http.get(SEARCH, params=params, headers=HEADERS)
    r.raise_for_status()
    found = [_product(h) for h in r.json().get("hits", []) if h.get("code")]
    english = [p for p in found if p.english]
    return english or found
