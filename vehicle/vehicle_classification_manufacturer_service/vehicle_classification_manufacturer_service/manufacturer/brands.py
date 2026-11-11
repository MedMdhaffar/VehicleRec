"""Curated manufacturer vocabulary for local VLM recognition.

This is inference configuration, not a training taxonomy or dataset. Canonical IDs
remain separate from the human-readable names used in text prompts.
"""

from __future__ import annotations

MANUFACTURER_DISPLAY_NAMES: dict[str, str] = {
    "alfa_romeo": "Alfa Romeo",
    "audi": "Audi",
    "bmw": "BMW",
    "byd": "BYD",
    "chery": "Chery",
    "chevrolet": "Chevrolet",
    "citroen": "Citroën",
    "cupra": "Cupra",
    "dacia": "Dacia",
    "dfsk": "DFSK",
    "dongfeng": "Dongfeng",
    "fiat": "Fiat",
    "ford": "Ford",
    "geely": "Geely",
    "gmc": "GMC",
    "great_wall": "Great Wall",
    "haval": "Haval",
    "honda": "Honda",
    "hyundai": "Hyundai",
    "isuzu": "Isuzu",
    "jaguar": "Jaguar",
    "jeep": "Jeep",
    "kia": "Kia",
    "land_rover": "Land Rover",
    "lexus": "Lexus",
    "mazda": "Mazda",
    "mercedes": "Mercedes-Benz",
    "mg": "MG",
    "mini": "MINI",
    "mitsubishi": "Mitsubishi",
    "nissan": "Nissan",
    "opel": "Opel",
    "peugeot": "Peugeot",
    "porsche": "Porsche",
    "renault": "Renault",
    "seat": "SEAT",
    "skoda": "Škoda",
    "subaru": "Subaru",
    "suzuki": "Suzuki",
    "toyota": "Toyota",
    "volkswagen": "Volkswagen",
    "volvo": "Volvo",
}

REGIONAL_MANUFACTURERS: tuple[str, ...] = tuple(MANUFACTURER_DISPLAY_NAMES)

