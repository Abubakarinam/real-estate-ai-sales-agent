"""Property listings (structured), so prices and sizes are never guessed from text."""
import json
from functools import lru_cache

from . import config


@lru_cache(maxsize=1)
def all_listings() -> list[dict]:
    return json.loads(config.LISTINGS_FILE.read_text(encoding="utf-8"))


def get(listing_id: str) -> dict | None:
    return next((l for l in all_listings() if l["id"].lower() == str(listing_id).lower()), None)


def search(purpose=None, area=None, property_type=None, bedrooms_min=None, budget_max=None,
           budget_min=None, limit: int = 5) -> list[dict]:
    out = []
    for l in all_listings():
        if purpose and l["purpose"] != purpose:
            continue
        if area and area.lower().replace("the ", "") not in l["area"].lower():
            continue
        if property_type and property_type.lower() not in l["type"]:
            continue
        if bedrooms_min is not None and l["bedrooms"] < bedrooms_min:
            continue
        # allow 10% stretch above budget, buyers often negotiate or stretch
        if budget_max and l["price_aed"] > budget_max * 1.1:
            continue
        if budget_min and l["price_aed"] < budget_min * 0.7:
            continue
        out.append(l)
    out.sort(key=lambda l: l["price_aed"])
    return out[:limit]


def short(l: dict) -> str:
    price = f"AED {l['price_aed']:,}" + (" a year" if l["purpose"] == "rent" else "")
    beds = "studio" if l["bedrooms"] == 0 else f"{l['bedrooms']}-bed"
    return f"{l['id']}: {l['title']} | {beds} {l['type']}, {l['size_sqft']} sq ft | {price} | {l['status']}"
