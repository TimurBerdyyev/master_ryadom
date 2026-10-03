"""Cities masters work in and orders are placed in.

Stored as these canonical Russian names (the web client translates them for display),
so "бишкек", "Бишкек " and the select value all match the same masters.
Add a city here and its translations in web/js/i18n.js (keys "city.<name>").
"""

CITIES = [
    "Бишкек", "Ош", "Джалал-Абад", "Каракол", "Токмок", "Нарын", "Талас", "Баткен",
    "Кара-Балта", "Кант", "Балыкчы", "Узген", "Кызыл-Кия", "Майлуу-Суу", "Чолпон-Ата",
    "Кара-Суу", "Кара-Куль", "Исфана", "Таш-Кумыр", "Кемин", "Арван",
]

_BY_LOWER = {c.lower(): c for c in CITIES}


def normalize_city(value: str | None) -> str | None:
    """Canonical city name for user input, or None if it isn't a known city."""
    if not value:
        return None
    return _BY_LOWER.get(" ".join(value.split()).lower())
