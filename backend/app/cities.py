"""Where masters work and requests are placed: region (oblast) → town, district or village.

Places are stored as these canonical Russian names (the web client translates them for display), so
"бишкек", "Бишкек " and the select value all mean the same place. The region is always derived from the
place on the server — clients never send it, so the two can't disagree.

Matching (app/dispatch.py, routers/orders.py): a request reaches masters in the same place, plus masters
of the same region who accept requests from the whole region (Master.serves_region).

Adding a place: put it into the right group below (names must stay unique across the country).
Districts carry their Kyrgyz and English names; towns and villages are shown as is (the site transliterates
them for English).
"""

# (name, Kyrgyz, English) for regions and districts; towns and villages are plain names.
REGIONS = [
    {
        "name": ("г. Бишкек", "Бишкек шаары", "Bishkek city"),
        "cities": ["Бишкек"], "districts": [], "villages": [],
    },
    {
        "name": ("г. Ош", "Ош шаары", "Osh city"),
        "cities": ["Ош"], "districts": [], "villages": [],
    },
    {
        "name": ("Чуйская область", "Чүй облусу", "Chuy Region"),
        "cities": ["Токмок", "Кара-Балта", "Кант", "Кемин", "Шопоков", "Орловка"],
        "districts": [
            ("Аламудунский район", "Аламүдүн району", "Alamudun District"),
            ("Жайылский район", "Жайыл району", "Jayil District"),
            ("Кеминский район", "Кемин району", "Kemin District"),
            ("Московский район", "Москва району", "Moskva District"),
            ("Панфиловский район", "Панфилов району", "Panfilov District"),
            ("Сокулукский район", "Сокулук району", "Sokuluk District"),
            ("Чуйский район", "Чүй району", "Chuy District"),
            ("Ысык-Атинский район", "Ысык-Ата району", "Ysyk-Ata District"),
        ],
        "villages": ["Лебединовка", "Васильевка", "Аламедин", "Новопавловка", "Сокулук", "Беловодское",
                     "Каинды", "Чуй", "Ивановка", "Военно-Антоновка", "Ленинское", "Байтик", "Таш-Дёбё"],
    },
    {
        "name": ("Ошская область", "Ош облусу", "Osh Region"),
        "cities": ["Кара-Суу", "Ноокат", "Узген"],
        "districts": [
            ("Алайский район", "Алай району", "Alay District"),
            ("Араванский район", "Араван району", "Aravan District"),
            ("Кара-Кульджинский район", "Кара-Кулжа району", "Kara-Kulja District"),
            ("Кара-Сууский район", "Кара-Суу району", "Kara-Suu District"),
            ("Ноокатский район", "Ноокат району", "Nookat District"),
            ("Узгенский район", "Өзгөн району", "Uzgen District"),
            ("Чон-Алайский район", "Чоң-Алай району", "Chon-Alay District"),
        ],
        "villages": ["Араван", "Гульча", "Кара-Кульджа", "Дароот-Коргон", "Сары-Таш", "Нариман", "Мырза-Аке",
                     "Кызыл-Кыштак"],
    },
    {
        "name": ("Джалал-Абадская область", "Жалал-Абад облусу", "Jalal-Abad Region"),
        "cities": ["Джалал-Абад", "Кербен", "Кок-Жангак", "Кара-Куль", "Майлуу-Суу", "Таш-Кумыр", "Токтогул",
                   "Кочкор-Ата"],
        "districts": [
            ("Аксыйский район", "Аксы району", "Aksy District"),
            ("Ала-Букинский район", "Ала-Бука району", "Ala-Buka District"),
            ("Базар-Коргонский район", "Базар-Коргон району", "Bazar-Korgon District"),
            ("Ноокенский район", "Ноокен району", "Nooken District"),
            ("Сузакский район", "Сузак району", "Suzak District"),
            ("Тогуз-Тороуский район", "Тогуз-Торо району", "Toguz-Toro District"),
            ("Токтогульский район", "Токтогул району", "Toktogul District"),
            ("Чаткальский район", "Чаткал району", "Chatkal District"),
        ],
        "villages": ["Базар-Коргон", "Ала-Бука", "Масы", "Сузак", "Казарман", "Каныш-Кыя", "Барпы",
                     "Шамалды-Сай", "Кызыл-Жар"],
    },
    {
        "name": ("Баткенская область", "Баткен облусу", "Batken Region"),
        "cities": ["Баткен", "Исфана", "Кызыл-Кия", "Сулюкта", "Кадамжай"],
        "districts": [
            ("Баткенский район", "Баткен району", "Batken District"),
            ("Кадамжайский район", "Кадамжай району", "Kadamjay District"),
            ("Лейлекский район", "Лейлек району", "Leylek District"),
        ],
        "villages": ["Пульгон", "Самаркандек", "Халмион", "Кара-Булак"],
    },
    {
        "name": ("Нарынская область", "Нарын облусу", "Naryn Region"),
        "cities": ["Нарын"],
        "districts": [
            ("Ак-Талинский район", "Ак-Талаа району", "Ak-Talaa District"),
            ("Ат-Башинский район", "Ат-Башы району", "At-Bashy District"),
            ("Жумгальский район", "Жумгал району", "Jumgal District"),
            ("Кочкорский район", "Кочкор району", "Kochkor District"),
            ("Нарынский район", "Нарын району", "Naryn District"),
        ],
        "villages": ["Баетов", "Ат-Башы", "Чаек", "Кочкор", "Эки-Нарын", "Достук"],
    },
    {
        "name": ("Иссык-Кульская область", "Ысык-Көл облусу", "Issyk-Kul Region"),
        "cities": ["Каракол", "Балыкчы", "Чолпон-Ата"],
        "districts": [
            ("Ак-Суйский район", "Ак-Суу району", "Ak-Suu District"),
            ("Джети-Огузский район", "Жети-Өгүз району", "Jeti-Oguz District"),
            ("Иссык-Кульский район", "Ысык-Көл району", "Issyk-Kul District"),
            ("Тонский район", "Тоң району", "Ton District"),
            ("Тюпский район", "Түп району", "Tup District"),
        ],
        "villages": ["Теплоключенка", "Кызыл-Суу", "Боконбаево", "Тюп", "Ананьево", "Бостери", "Тамга",
                     "Барскоон", "Тору-Айгыр"],
    },
    {
        "name": ("Таласская область", "Талас облусу", "Talas Region"),
        "cities": ["Талас"],
        "districts": [
            ("Бакай-Атинский район", "Бакай-Ата району", "Bakay-Ata District"),
            ("Кара-Бууринский район", "Кара-Буура району", "Kara-Buura District"),
            ("Манасский район", "Манас району", "Manas District"),
            ("Таласский район", "Талас району", "Talas District"),
        ],
        "villages": ["Бакай-Ата", "Кызыл-Адыр", "Покровка", "Кок-Ой", "Шекер"],
    },
]

# Old spellings → current names (data saved before the regions existed).
ALIASES = {"арван": "Араван"}


def _place_names(region: dict) -> list[str]:
    return region["cities"] + [d[0] for d in region["districts"]] + region["villages"]


REGION_NAMES = [r["name"][0] for r in REGIONS]
REGION_OF = {place: r["name"][0] for r in REGIONS for place in _place_names(r)}
# Every place, for validation; kept as CITIES for code that only needs the flat list.
CITIES = list(REGION_OF)

if len(CITIES) != len(set(CITIES)):  # a duplicate name would silently move masters between regions
    raise RuntimeError("cities.py: place names must be unique")

_BY_LOWER = {c.lower(): c for c in CITIES}


def normalize_city(value: str | None) -> str | None:
    """Canonical place name for user input, or None if it isn't a known place."""
    if not value:
        return None
    key = " ".join(value.split()).lower()
    return _BY_LOWER.get(key) or ALIASES.get(key)


def region_of(place: str | None) -> str | None:
    return REGION_OF.get(place) if place else None


def regions_for_config() -> list[dict]:
    """What the site needs for the region → place picker (and the translations of regions/districts)."""
    return [
        {
            "name": r["name"][0], "ky": r["name"][1], "en": r["name"][2],
            "cities": r["cities"],
            "districts": [{"name": d[0], "ky": d[1], "en": d[2]} for d in r["districts"]],
            "villages": r["villages"],
        }
        for r in REGIONS
    ]
