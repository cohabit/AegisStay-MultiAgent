"""Approved catalog metadata, separate from live stock and model suggestions."""
from __future__ import annotations

import json
from pathlib import Path

CATALOG_PATH = Path(__file__).resolve().parent / "data" / "service_catalog.json"


def load_service_catalog() -> dict:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    names = set()
    for item in catalog.get("items", []):
        if (not item.get("name") or item["name"] in names or type(item.get("initial_stock")) is not int
                or item["initial_stock"] < 0 or not item.get("unit") or not item.get("group")):
            raise ValueError("Invalid inventory catalog")
        names.add(item["name"])
    for service in catalog.get("services", []):
        if service.get("status") not in {"available", "unavailable", "unconfirmed"}:
            raise ValueError("Invalid service catalog status")
        if service["status"] != "unconfirmed" and not service.get("source"):
            raise ValueError("Confirmed service facts require a source")
    return catalog


ITEM_CATALOG = {item["name"]: item for item in load_service_catalog()["items"]}
