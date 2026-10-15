"""The Boston University HFI sugar table, and matching ingredient text against it.

Source: https://www.bu.edu/aldolase/HFI/treatment/sugar_table.htm

Each entry keeps BU's name, description and tolerance. `contains` records which of
fructose / sucrose / sorbitol the entry is (or carries), read from BU's description.
`aliases` are other spellings found on real labels.
"""

import re
from dataclasses import dataclass, field
from typing import Literal

SOURCE_URL = "https://www.bu.edu/aldolase/HFI/treatment/sugar_table.htm"

Status = Literal["not_tolerated", "caution", "tolerated"]
Sugar = Literal["fructose", "sucrose", "sorbitol"]

F: list[Sugar] = ["fructose"]
S: list[Sugar] = ["sucrose"]
SF: list[Sugar] = ["sucrose", "fructose"]
SOR: list[Sugar] = ["sorbitol"]


@dataclass(frozen=True)
class SugarEntry:
    name: str
    tolerance: str  # BU's wording
    status: Status
    description: str
    contains: list[Sugar] = field(default_factory=list)
    aliases: tuple[str, ...] = ()

    @property
    def names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


def _e(name, tolerance, description, contains=(), aliases=()) -> SugarEntry:
    t = tolerance.lower()
    if t.startswith("not tolerated"):
        status: Status = "not_tolerated"
    elif t == "tolerated" or "questionable" in t:
        status = "tolerated"
    else:  # "depending on purity", "caution", "varies"
        status = "caution"
    return SugarEntry(name, tolerance, status, description, list(contains), tuple(aliases))


NT = "Not Tolerated"
T = "Tolerated"
PURITY = "Tolerated depending on purity"
CAUTION = "Caution, needs further clarification"

TABLE: list[SugarEntry] = [
    _e("Agave Syrup", NT, "From the blue agave cactus. High in fructose.", F, ("agave nectar", "agave")),
    _e("Aspartame", T, "Sugar substitute (Equal, NutraSweet). Derived from amino acids."),
    _e(
        "Acesulfame-K",
        "Tolerated (Questionable safety)",
        "Sugar substitute (Sunette, Sweet-One).",
        (),
        ("acesulfame k", "acesulfame potassium", "ace-k", "acesulfame"),
    ),
    _e("Baker's Sugar", NT, "Superfine granulated sucrose.", S, ("bakers sugar", "superfine sugar", "ultrafine sugar")),
    _e("Bar Sugar", NT, "Superfine granulated sucrose.", S),
    _e("Barbados Sugar", NT, "British brown sugar with strong molasses flavor.", SF),
    _e(
        "Barley Malt Syrup",
        T,
        "From sprouted barley, kiln dried and cooked with water.",
        (),
        ("barley malt", "malt syrup"),
    ),
    _e("Beet Sugar", NT, "Sucrose, same structure as cane sugar.", S),
    _e("Berry Sugar", NT, "Superfine granulated sucrose.", S),
    _e("Birch Sugar", PURITY, "Sugar alcohol: xylitol, derived from xylose."),
    _e("Brown Rice Syrup", NT, "Made from brown rice. Likely contains sucrose.", S, ("rice syrup",)),
    _e("Brown Sugar", NT, "Sucrose coated with molasses.", SF),
    _e("Cane Sugar", NT, "Sucrose. Table sugar.", S, ("cane juice", "evaporated cane juice", "organic cane sugar")),
    _e("Castor/Caster Sugar", NT, "Superfine granulated sucrose.", S, ("caster sugar", "castor sugar")),
    _e("Carob Powder", NT, "75% sucrose, plus glucose and fructose.", SF, ("carob flour",)),
    _e(
        "Chicory",
        NT,
        "Contains inulin. Used to make fructose syrup.",
        F,
        ("inulin", "chicory root", "chicory root fiber", "chicory root extract"),
    ),
    _e("Chinese Rock Sugar", NT, "Combination of honey and sugars.", SF),
    _e("Corn Starch", T, "Chains of glucose from corn.", (), ("cornstarch",)),
    _e("Corn Sugar", T, "Produced from corn starch. Glucose and maltose."),
    _e("Corn Syrup", NT, "Glucose syrup that may have maltose and/or fructose added.", F),
    _e("Corn Syrup Solids", CAUTION, "Dried glucose syrup."),
    _e(
        "Confectioners Sugar",
        NT,
        "Sucrose.",
        S,
        ("confectioner's sugar", "confectioners' sugar", "powdered sugar", "icing sugar"),
    ),
    _e("Date Sugar", NT, "Made from dried, pulverized dates. Likely contains sucrose.", SF),
    _e("Demerara", NT, "Sucrose. Another name for raw sugar.", S, ("demerara sugar",)),
    _e("Dextrin", T, "Chains of glucose molecules."),
    _e("Dextrose", T, "Single glucose molecule."),
    _e("Dextroglucose", T, "Single glucose molecule."),
    _e("Dextrose Monohydrate", T, "Pure dextrose."),
    _e("D-Allose", T, "Simple sugar, not common in diet."),
    _e("D-Altrose", T, "Simple sugar, not common in diet."),
    _e("D-Arabinose", T, "Simple sugar, not common in diet.", (), ("arabinose",)),
    _e("D-Erythrose", T, "Simple sugar, not common in diet.", (), ("erythrose",)),
    _e("D-Erythrulose", T, "Simple sugar, not common in diet.", (), ("erythrulose",)),
    _e("D-Galactose", T, "Simple sugar, part of lactose.", (), ("galactose",)),
    _e("D-Gulose", T, "Simple sugar, not common in diet."),
    _e("D-Idose", T, "Simple sugar, not common in diet."),
    _e("D-Lyxose", T, "Simple sugar, not common in diet."),
    _e("D-Psicose", PURITY, "Sweetener chemically related to fructose.", (), ("allulose", "psicose")),
    _e("D-Ribose", T, "Simple sugar, not common in diet.", (), ("ribose",)),
    _e("D-Ribulose", T, "Simple sugar, not common in diet."),
    _e("D-Sorbose", PURITY, "Sweetener chemically related to fructose.", (), ("sorbose",)),
    _e("D-Tagatose", PURITY, "Sweetener chemically related to fructose.", (), ("tagatose",)),
    _e("D-Talose", T, "Simple sugar, not common in diet."),
    _e("D-Threose", T, "Simple sugar, not common in diet."),
    _e("D-Xylose", T, "Simple sugar, not common."),
    _e("D-Xyulose", T, "Simple sugar, not common in diet."),
    _e("Dulcitol", NT, "Naturally occurring sugar alcohol.", (), ("galactitol",)),
    # BU spells this "Erythitol".
    _e("Erythritol", PURITY, "Sugar alcohol related to erythrose.", (), ("erythitol",)),
    _e("Evaporated Cane Sugar", NT, "Sucrose. Another name for sugar cane juice.", S),
    _e("Fructose", NT, "Simple sugar, sometimes called fruit sugar.", F, ("fruit sugar", "crystalline fructose")),
    _e(
        "Fruit Juice Sweeteners",
        NT,
        "Concentrated grape, apple or pear juice. Almost pure fructose.",
        F,
        ("fruit juice concentrate", "fruit juice", "juice concentrate", "fruit juice sweetener"),
    ),
    _e("Gemsugar", NT, "Colored sugar made from Thai sugarcane.", S),
    _e("Glucose", T, "Simple sugar; the structure of blood sugar."),
    _e("Glucose Polymers", T, "Chains of glucose molecules."),
    _e("Glucose Syrups", CAUTION, "Produced from starch, corn syrup or cane syrup.", (), ("glucose syrup",)),
    _e("Grape Syrup", NT, "Pure fructose.", F),
    _e("Granulated Sugar", NT, "Table sugar. Sucrose.", S),
    _e("Gur", NT, "Jaggery: 35% sucrose, 15% glucose plus fructose. Contains molasses.", SF),
    _e(
        "High Fructose Corn Syrup (HFCS)",
        NT,
        "Corn syrup converted to contain 42-90% fructose.",
        F,
        (
            "high fructose corn syrup",
            "high-fructose corn syrup",
            "hfcs",
            "glucose-fructose syrup",
            "glucose-fructose",
            "fructose-glucose syrup",
            "fructose syrup",
        ),
    ),
    _e("High Fructose Glucose Syrup", NT, "Contains fructose.", F),
    _e("Honey", NT, "About 35% glucose, 40% fructose, 25% water.", F),
    _e(
        "Hydrogenated Starch Hydrolysate",
        PURITY,
        "Sugar alcohol of glucose.",
        (),
        ("hydrogenated starch hydrolysates",),
    ),
    _e(
        "Invert Sugar",
        NT,
        "Sucrose broken down into glucose and fructose.",
        SF,
        ("invert sugar syrup", "inverted sugar", "invert syrup"),
    ),
    _e("Isoglucose", NT, "Another name for HFCS.", F),
    _e("Isomaltose", T, "Linked glucose molecules."),
    _e("Jaggery", NT, "35% sucrose, 15% glucose plus fructose. Contains molasses.", SF),
    _e("Lactitol", PURITY, "Sugar alcohol form of lactose."),
    _e("Lactose", T, "Milk sugar."),
    _e("Levulose", NT, "Contains fructose.", F),
    _e("Litesse", NT, "Polydextrose: sugar substitute containing sorbitol and glucose.", SOR),
    _e("Maltitol", PURITY, "Sugar alcohol form of maltose.", (), ("maltitol syrup",)),
    _e("Maltose", T, "Linked glucose molecules."),
    _e("Maltodextrin", T, "Dextrose processed from cornstarch."),
    _e("Mannitol", PURITY, "Sugar alcohol form of mannose."),
    _e("Mannose", T, "Simple sugar, not common."),
    _e("Maple Syrup", NT, "Mostly sucrose, with some invert sugar.", SF),
    _e("Maple Sugar", NT, "Mostly sucrose, with some invert sugar.", SF),
    _e("Moducal", T, "Glucose chains. A medical food."),
    _e("Molasses", NT, "By-product of sugar cane; fructose level varies.", SF, ("blackstrap molasses",)),
    _e("Molasses Sugar", NT, "Dark muscovado sugar with extra molasses.", SF),
    _e("Muscovado Sugar", NT, "British brown sugar with strong molasses flavor.", SF, ("muscovado",)),
    _e("Neotame", T, "Sugar substitute."),
    _e("Palm Sugar", NT, "Used in Thai cooking. Likely contains sucrose.", S, ("coconut palm sugar", "coconut sugar")),
    _e("Panella", NT, "35% sucrose, 15% glucose plus fructose. Contains molasses.", SF, ("panela",)),
    _e("Polincillo", NT, "Mexican brown sugar.", S, ("piloncillo",)),
    _e("Polycose", T, "Chains of dextrose."),
    _e("Polydextrin", T, "Chains of glucose molecules."),
    _e("Polydextrose", NT, "Made from dextrose plus about 10% sorbitol.", SOR),
    _e("Raffinose", "Tolerance Varies", "A trisaccharide in grains, legumes and some vegetables."),
    _e("Rapadura", NT, "35% sucrose, 15% glucose plus fructose. Contains molasses.", SF),
    _e("Raw Sugar", NT, "Sucrose.", S),
    _e("Reducing Sugar", NT, "Invert sugar (glucose and fructose).", F),
    _e("Rock Sugar", NT, "Crystallized cane sugar. Sucrose.", S),
    _e("Saccharin", T, "Sugar substitute (Sweet'N Low)."),
    _e("Saccharose", NT, "Sucrose.", S),
    _e("Sorbitol", NT, "Sugar alcohol. Converted back to fructose.", SOR),
    _e("Splenda", PURITY, "Sucralose; a modified sucrose molecule that cannot be digested."),
    _e(
        "Stevia",
        NT,
        "Natural sweetener from a South American plant (stevioside).",
        (),
        ("stevia leaf extract", "stevia extract", "stevioside", "steviol glycosides", "rebaudioside a", "reb a"),
    ),
    _e("Sucanat", NT, "Raw sugar, sometimes cane sugar plus molasses.", SF),
    _e("Sucralose", PURITY, "Chemical name for Splenda. Not digested."),
    _e("Sucrose", NT, "Table sugar. Glucose and fructose combined.", S),
    _e("Sucrose Syrups", NT, "Refiner's syrup: sucrose and invert sugar.", SF, ("sucrose syrup", "refiner's syrup")),
    _e("Sugar", NT, "Common name for sucrose.", S, ("sugars", "white sugar", "table sugar")),
    _e("Sugar Alcohol", NT, "May be metabolized back to fructose or other sugars.", (), ("sugar alcohols",)),
    _e("Trimoline", NT, "Up to 22% invert sugar, from beets.", SF),
    _e("Turbinado", NT, "Raw sugar. Sucrose.", S, ("turbinado sugar",)),
    _e("Vanilla Sugar", NT, "Cane sugar infused with vanilla beans.", S),
    _e("Wasanbon", NT, "Very pure Japanese cane sugar.", S),
    _e("Xylitol", PURITY, "Sugar alcohol from fruits, berries or birch."),
    _e("Xylose", T, "Simple sugar, not common.", (), ("d-xylose",)),
    _e("Xyulose", T, "Simple sugar, not common."),
]

BY_NAME = {e.name: e for e in TABLE}


def norm(text: str) -> str:
    text = text.lower().replace("’", "'")
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# (normalized name, entry), longest first so the most specific name wins.
_PATTERNS = sorted(
    ((norm(n), e) for e in TABLE for n in e.names),
    key=lambda p: -len(p[0]),
)
_REGEXES = [(re.compile(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])"), e) for n, e in _PATTERNS]


def match(ingredient: str) -> list[SugarEntry]:
    """BU table entries named in `ingredient`. Longer names win over names they contain."""
    text = norm(ingredient)
    taken: list[tuple[int, int]] = []
    found: list[SugarEntry] = []
    for rx, entry in _REGEXES:
        for m in rx.finditer(text):
            if any(m.start() < end and start < m.end() for start, end in taken):
                continue
            taken.append(m.span())
            if entry not in found:
                found.append(entry)
    return found
