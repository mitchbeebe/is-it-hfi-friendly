"""Common label ingredients matched to measured foods, so ingredient lists rely on data rather than the AI model.

Whole foods map to one Australian (AFCD) entry each, chosen to be the plain form a label means. Where
the form is unclear, the entry with more sugar is used, e.g. dried rather than canned chickpeas, since
the amount in the product can only be lower. Concentrated forms ("onion powder", "dried apricots")
aren't listed unless AFCD measured that form; they're left to the AI model.

Additives with no carbohydrate at all (vitamins, minerals, acids, leavening, fats) match by name pattern.
"""

import re
from dataclasses import dataclass
from functools import cache

from . import afcd, sugars

# Label wording -> AFCD food name.
FOODS: dict[str, str] = {
    # water, salt
    "water": "Water, tap",
    "filtered water": "Water, tap",
    "purified water": "Water, tap",
    "carbonated water": "Water, soda",
    "sparkling water": "Water, soda",
    "salt": "Salt, table, non-iodised",
    "sea salt": "Salt, table, non-iodised",
    "iodized salt": "Salt, table, iodised",
    "iodised salt": "Salt, table, iodised",
    # oils
    "vegetable oil": "Oil, vegetable",
    "canola oil": "Oil, canola",
    "rapeseed oil": "Oil, canola",
    "sunflower oil": "Oil, sunflower",
    "soybean oil": "Oil, soybean",
    "corn oil": "Oil, maize",
    "olive oil": "Oil, olive",
    "extra virgin olive oil": "Oil, olive",
    "peanut oil": "Oil, peanut",
    "rice bran oil": "Oil, rice bran",
    "safflower oil": "Oil, safflower",
    # dairy, eggs
    "milk": "Milk, cow, fluid, regular fat (~3.5%)",
    "whole milk": "Milk, cow, fluid, regular fat (~3.5%)",
    "skim milk": "Milk, cow, fluid, skim (~0.15% fat)",
    "nonfat milk": "Milk, cow, fluid, skim (~0.15% fat)",
    "milk powder": "Milk, cow, powder, regular fat",
    "whole milk powder": "Milk, cow, powder, regular fat",
    "skim milk powder": "Milk, cow, powder, skim",
    "nonfat dry milk": "Milk, cow, powder, skim",
    "cream": "Cream, thickened, regular fat (~35%)",
    "sour cream": "Cream, sour, regular fat",
    "butter": "Butter, plain, salted",
    "unsalted butter": "Butter, plain, no added salt",
    "cheddar cheese": "Cheese, cheddar, natural, regular fat",
    "mozzarella cheese": "Cheese, mozzarella",
    "parmesan cheese": "Cheese, parmesan, fresh, regular fat",
    "egg": "Egg, chicken, whole, omega-3 polyunsaturate enriched, raw",
    "whole egg": "Egg, chicken, whole, omega-3 polyunsaturate enriched, raw",
    "egg white": "Egg, chicken, white (albumen), raw",
    "egg yolk": "Egg, chicken, yolk, raw",
    # grains, flours
    "flour": "Flour, wheat, white, plain",
    "wheat flour": "Flour, wheat, white, plain",
    "white flour": "Flour, wheat, white, plain",
    "plain flour": "Flour, wheat, white, plain",
    "all purpose flour": "Flour, wheat, white, plain",
    "enriched flour": "Flour, wheat, white, plain",
    "enriched wheat flour": "Flour, wheat, white, plain",
    "unbleached flour": "Flour, wheat, white, plain",
    "enriched bleached flour": "Flour, wheat, white, plain",
    "unbleached enriched flour": "Flour, wheat, white, plain",
    "bread flour": "Flour, wheat, white, high protein or bread making flour",
    "whole wheat flour": "Flour, wheat, wholemeal, plain",
    "whole grain wheat flour": "Flour, wheat, wholemeal, plain",
    "wholemeal flour": "Flour, wheat, wholemeal, plain",
    "wholewheat flour": "Flour, wheat, wholemeal, plain",
    "rye flour": "Flour, rye",
    "oat": "Oats, rolled, uncooked",
    "rolled oat": "Oats, rolled, uncooked",
    "whole grain oat": "Oats, rolled, uncooked",
    "whole grain rolled oat": "Oats, rolled, uncooked",
    "barley": "Barley, pearl, uncooked",
    "pearl barley": "Barley, pearl, uncooked",
    "semolina": "Semolina, uncooked",
    "durum wheat semolina": "Semolina, uncooked",
    "cornmeal": "Cornmeal (polenta), uncooked",
    "polenta": "Cornmeal (polenta), uncooked",
    "arrowroot": "Flour, arrowroot",
    "wheat bran": "Wheat bran, unprocessed, uncooked",
    "wheat germ": "Wheat germ",
    # nuts, seeds
    "almond": "Nut, almond, with skin, raw, unsalted",
    "almond flour": "Nut, almond meal",
    "almond meal": "Nut, almond meal",
    "brazil nut": "Nut, brazil, raw or blanched, unsalted",
    "cashew": "Nut, cashew, roasted, unsalted",
    "hazelnut": "Nut, hazelnut, raw, unsalted",
    "macadamia nut": "Nut, macadamia, raw, unsalted",
    "peanut": "Nut, peanut, with skin, raw, unsalted",
    "pecan": "Nut, pecan, raw, unsalted",
    "pine nut": "Nut, pine, raw, unsalted",
    "pistachio": "Nut, pistachio, raw, unsalted",
    "walnut": "Nut, walnut, raw, unsalted",
    "chia seed": "Seed, chia, dried",
    "flaxseed": "Seed, linseed or flaxseed",
    "linseed": "Seed, linseed or flaxseed",
    "pumpkin seed": "Seed, pumpkin, hulled & dried",
    "sunflower seed": "Seed, sunflower",
    "sesame seed": "Seed, sesame, unsalted",
    "desiccated coconut": "Coconut, grated & desiccated",
    "coconut milk": "Coconut, milk, canned, regular fat",
    "coconut cream": "Coconut, cream, regular fat",
    # legumes (dried: canned and cooked have less)
    "chickpea": "Chickpea, dried",
    "garbanzo bean": "Chickpea, dried",
    "soybean": "Bean, soya (soy), dried",
    "red kidney bean": "Bean, red kidney, dried",
    "kidney bean": "Bean, red kidney, dried",
    "haricot bean": "Bean, haricot, dried",
    "navy bean": "Bean, haricot, dried",
    "lima bean": "Bean, lima, dried",
    "split pea": "Pea, split, dried",
    # vegetables
    "onion": "Onion, mature, brown skinned, peeled, raw",
    "red onion": "Onion, mature, red skinned, raw",
    "garlic": "Garlic, peeled, fresh, raw",
    "tomato": "Tomato, common, raw",
    "tomato paste": "Tomato, paste, no added salt",
    "sundried tomato": "Tomato, sundried",
    "sun dried tomato": "Tomato, sundried",
    "carrot": "Carrot, mature, peeled, fresh, raw",
    "celery": "Celery, fresh, raw",
    "pea": "Pea, green, fresh, raw",
    "green pea": "Pea, green, fresh, raw",
    "corn": "Corn, fresh on cob, raw",
    "sweet corn": "Corn, fresh on cob, raw",
    "sweetcorn": "Corn, fresh on cob, raw",
    "potato": "Potato, desiree, peeled, raw",
    "spinach": "Spinach, Mature English, fresh, raw",
    "mushroom": "Mushroom, common, fresh, raw",
    "red bell pepper": "Capsicum, red, fresh, raw",
    "red pepper": "Capsicum, red, fresh, raw",
    "green bell pepper": "Capsicum, green, fresh, raw",
    "ginger": "Ginger, peeled, fresh, raw",
    "beetroot": "Beetroot, fresh, purple, peeled, raw",
    "beet": "Beetroot, fresh, purple, peeled, raw",
    "cabbage": "Cabbage, white, raw",
    "red cabbage": "Cabbage, red, raw",
    "green bean": "Bean, green, fresh, raw",
    "eggplant": "Eggplant, unpeeled, fresh, raw",
    "broccoli": "Broccoli, fresh, raw",
    "cauliflower": "Cauliflower, fresh, raw",
    "zucchini": "Zucchini, green skin, fresh, unpeeled, raw",
    "cucumber": "Cucumber, common, peeled, raw",
    "pumpkin": "Pumpkin, peeled, fresh, raw",
    "sweet potato": "Sweet potato, white flesh, peeled, raw",
    "kale": "Kale, raw",
    "leek": "Leek, fresh, raw",
    "asparagus": "Asparagus, green, raw",
    "lettuce": "Lettuce, iceberg, raw",
    "shallot": "Shallot, peeled, fresh, raw",
    "green onion": "Onion, spring, fresh, raw",
    "spring onion": "Onion, spring, fresh, raw",
    "scallion": "Onion, spring, fresh, raw",
    "avocado": "Avocado, hass, raw",
    "coriander": "Coriander, fresh, raw",
    "cilantro": "Coriander, fresh, raw",
    "basil": "Basil, green, fresh, raw",
    "parsley": "Parsley, continental, fresh, raw",
    "mustard powder": "Mustard powder",
    # fruit
    "apple": "Apple, red skin, unpeeled, raw",
    "pear": "Pear, William Bartlett, unpeeled, raw",
    "banana": "Banana, lady finger or sugar, peeled, raw",
    "blueberry": "Blueberry, raw",
    "strawberry": "Strawberry, raw",
    "raspberry": "Raspberry, raw",
    "raisin": "Raisin",
    "sultana": "Sultana",
    "golden raisin": "Sultana",
    "date": "Date, dried",
    "fig": "Fig, dried",
    "dried fig": "Fig, dried",
    "grape": "Grape, red globe, raw",
    "orange": "Orange, peeled, raw",
    "lemon": "Lemon, peeled, raw",
    "pineapple": "Pineapple, peeled, raw",
    "mango": "Mango, peeled, raw",
    "peach": "Peach, yellow, unpeeled, raw",
    "apricot": "Apricot, raw",
    "dried apricot": "Apricot, dried",
    "cherry": "Cherry, raw",
    "plum": "Plum, unpeeled, raw",
    "lemon juice": "Juice, lemon",
    "lime juice": "Juice, lime",
    "orange juice": "Juice, orange, commercial",
    "apple juice": "Juice, apple, commercial, added vitamin C",
}

# Words that don't change what a food is, dropped before matching.
QUALIFIERS = re.compile(r"\b(organic|fresh|pasteurized|pasteurised|natural|raw)\b")

_MINERAL = r"(mono|di|tri|tetra)?(sodium|potassium|calcium|magnesium|ammonium|ferric|ferrous|zinc|copper|manganese)"
_SALT = r"(phosphate|pyrophosphate|carbonate|bicarbonate|chloride|citrate|sulfate|sulphate|oxide|hydroxide|lactate|iodide|gluconate|propionate|sorbate|benzoate|silicate)"
NO_SUGAR = re.compile(
    "|".join(
        [
            r"vitamin( [a-z]?\d*)+",
            r"(a b vitamin|b vitamin)",
            r"niacin(amide)?",
            r"thiamin(e)?( mononitrate| hydrochloride)?",
            r"riboflavin",
            r"folic acid|folate",
            r"pyridoxine( hydrochloride)?",
            r"cyanocobalamin|biotin|cholecalciferol|retinyl palmitate",
            r"(calcium |d )?pantothenate",
            r"(mixed )?tocopherols?",
            r"(sodium |calcium )?ascorbate|ascorbic acid",
            r"(reduced |electrolytic )?iron|zinc|iodine|calcium|magnesium|potassium",
            rf"{_MINERAL}( (acid|aluminum|aluminium))? (mono|di|tri)?{_SALT}s?",
            r"(citric|lactic|malic|acetic|phosphoric|fumaric|tartaric|sorbic|benzoic|propionic) acid",
            r"baking soda|baking powder|cream of tartar|leavening( agents?)?|raising agents?",
            # Pure fats: palm, coconut, hydrogenated and other oils AFCD doesn't list.
            r"([a-z]+ ){0,3}(oil|shortening)",
            r"(soy |soya |sunflower )?lecithin",
            r"gelatine?",
        ]
    )
)


@dataclass(frozen=True)
class Known:
    name: str  # the ingredient as written
    food: afcd.AfcdFood | None  # None for a sugar-free additive


@cache
def _food(name: str) -> afcd.AfcdFood:
    return next(f for f in afcd.foods() if f.name == name)


def key(name: str) -> str:
    return afcd.key(QUALIFIERS.sub(" ", sugars.norm(name).replace("-", " ")))


_KEYS = {key(k): v for k, v in FOODS.items()}


def lookup(name: str) -> Known | None:
    """The measured food or sugar-free additive an ingredient name means, if it's a known one."""
    k = key(name)
    if k in _KEYS:
        return Known(name, _food(_KEYS[k]))
    if NO_SUGAR.fullmatch(sugars.norm(name)):
        return Known(name, None)
    return None
