"""Texts and URL slugs for the search-engine pages (app/seo.py).

Pages are Russian: that is how people in Kyrgyzstan search for services («сантехник бишкек»).
A category from the database without an entry in SEO_CATEGORIES still gets pages, with generic texts.
"""
from app.cities import REGIONS, REGION_OF

# DB category name → how people search for it, the genitive for "услуги сантехника", schema.org type,
# a sentence about the work, popular jobs (the same as proj.* in web/js/i18n.js).
SEO_CATEGORIES = {
    "Сантехника": {
        "slug": "santehnik", "who": "Сантехник", "gen": "сантехника", "schema": "Plumber",
        "about": "Замена смесителей и унитазов, устранение протечек, прочистка засоров, установка стиральных "
                 "машин, бойлеров и счётчиков воды.",
        "jobs": ["Замена смесителя", "Установка унитаза", "Прочистка засора", "Течёт кран", "Установка стиральной машины"],
    },
    "Электрика": {
        "slug": "elektrik", "who": "Электрик", "gen": "электрика", "schema": "Electrician",
        "about": "Замена розеток и выключателей, монтаж люстр и светильников, проводка в квартире и доме, "
                 "поиск неисправностей, установка автоматов и счётчиков.",
        "jobs": ["Замена розетки", "Повесить люстру", "Проводка в квартире", "Выбивает автомат", "Установка счётчика"],
    },
    "Строительство": {
        "slug": "stroiteli", "who": "Строители", "gen": "строителей", "schema": "GeneralContractor",
        "about": "Кладка, стяжка, штукатурка, гипсокартон, фундамент, кровля и другие общестроительные работы.",
        "jobs": ["Кладка", "Стяжка пола", "Штукатурка стен", "Монтаж гипсокартона"],
    },
    "Ремонт": {
        "slug": "remont-kvartir", "who": "Ремонт квартир", "gen": "мастера по ремонту",
        "schema": "HomeAndConstructionBusiness",
        "about": "Косметический и капитальный ремонт, поклейка обоев, покраска, укладка ламината и плитки, "
                 "сборка мебели.",
        "jobs": ["Поклейка обоев", "Покраска стен", "Укладка ламината", "Сборка мебели", "Повесить полку или ТВ"],
    },
    "Уборка": {
        "slug": "uborka", "who": "Уборка квартир", "gen": "клинера", "schema": "LocalBusiness",
        "about": "Генеральная и поддерживающая уборка, уборка после ремонта, мытьё окон, химчистка мебели и ковров.",
        "jobs": ["Генеральная уборка", "Уборка после ремонта", "Мытьё окон", "Химчистка дивана"],
    },
    "Компьютеры": {
        "slug": "kompyuternyy-master", "who": "Компьютерный мастер", "gen": "компьютерного мастера",
        "schema": "LocalBusiness",
        "about": "Ремонт и настройка компьютеров и ноутбуков, установка Windows и программ, настройка Wi-Fi "
                 "и роутера, удаление вирусов.",
        "jobs": ["Настройка Wi-Fi", "Установка Windows", "Ремонт ноутбука", "Удаление вирусов"],
    },
    "Ремонт телефонов": {
        "slug": "remont-telefonov", "who": "Ремонт телефонов", "gen": "мастера по ремонту телефонов",
        "schema": "LocalBusiness",
        "about": "Замена экрана и батареи, ремонт разъёма зарядки, восстановление после воды, ремонт смартфонов "
                 "и планшетов.",
        "jobs": ["Замена экрана", "Замена батареи", "Не заряжается", "Перепрошивка"],
    },
    "Автомастера": {
        "slug": "avtomaster", "who": "Автомастер", "gen": "автомастера", "schema": "AutoRepair",
        "about": "Шиномонтаж, диагностика, замена масла, ремонт двигателя и ходовой, автоэлектрик, помощь на дороге.",
        "jobs": ["Шиномонтаж", "Диагностика", "Замена масла", "Автоэлектрик"],
    },
    "Красота": {
        "slug": "master-krasoty", "who": "Мастер красоты на дому", "gen": "мастера красоты", "schema": "BeautySalon",
        "about": "Маникюр и педикюр, макияж, наращивание ресниц, оформление бровей, массаж — с выездом на дом.",
        "jobs": ["Маникюр на дому", "Макияж", "Наращивание ресниц", "Массаж"],
    },
    "Парикмахеры": {
        "slug": "parikmaher", "who": "Парикмахер на дому", "gen": "парикмахера", "schema": "HairSalon",
        "about": "Мужские, женские и детские стрижки, окрашивание и укладка с выездом на дом.",
        "jobs": ["Стрижка на дому", "Окрашивание", "Укладка", "Детская стрижка"],
    },
    "Грузчики": {
        "slug": "gruzchiki", "who": "Грузчики", "gen": "грузчиков", "schema": "MovingCompany",
        "about": "Переезд квартиры и офиса, подъём мебели и стройматериалов, погрузка и разгрузка, вывоз мусора.",
        "jobs": ["Переезд квартиры", "Поднять мебель", "Вынос мусора", "Погрузка"],
    },
    "Перевозки": {
        "slug": "gruzoperevozki", "who": "Грузоперевозки", "gen": "грузового такси", "schema": "MovingCompany",
        "about": "Грузовое такси, перевозка мебели и техники, доставка стройматериалов, переезды по городу "
                 "и между городами.",
        "jobs": ["Грузовое такси", "Перевезти мебель", "Доставка стройматериалов", "Межгород"],
    },
    "Ремонт бытовой техники": {
        "slug": "remont-bytovoy-tehniki", "who": "Ремонт бытовой техники", "gen": "мастера по ремонту техники",
        "schema": "LocalBusiness",
        "about": "Ремонт стиральных машин, холодильников, кондиционеров, плит, духовок и водонагревателей на дому.",
        "jobs": ["Ремонт стиральной машины", "Ремонт холодильника", "Ремонт кондиционера", "Ремонт микроволновки"],
    },
    "Другое": {
        "slug": "master-na-chas", "who": "Мастер на час", "gen": "мастера на час",
        "schema": "HomeAndConstructionBusiness",
        "about": "Мелкий ремонт по дому: повесить полку или карниз, собрать мебель, заменить замок, починить дверь.",
        "jobs": ["Мастер на час", "Мелкий ремонт", "Помощь по дому"],
    },
}

# Pages of these towns are indexed even before masters sign up there (the biggest towns, regional centres).
KEY_CITIES = ["Бишкек", "Ош", "Джалал-Абад", "Каракол", "Токмок", "Кара-Балта", "Узген", "Нарын", "Талас",
              "Баткен", "Кызыл-Кия", "Балыкчы", "Чолпон-Ата", "Кара-Суу", "Кант"]

# "в Бишкеке": the prepositional case of towns (districts and regions follow a rule, villages aren't declined).
CITY_LOCATIVE = {
    "Бишкек": "Бишкеке", "Ош": "Оше", "Токмок": "Токмоке", "Кара-Балта": "Кара-Балте", "Кант": "Канте",
    "Кемин": "Кемине", "Шопоков": "Шопокове", "Орловка": "Орловке", "Кара-Суу": "Кара-Суу", "Ноокат": "Ноокате",
    "Узген": "Узгене", "Джалал-Абад": "Джалал-Абаде", "Кербен": "Кербене", "Кок-Жангак": "Кок-Жангаке",
    "Кара-Куль": "Кара-Куле", "Майлуу-Суу": "Майлуу-Суу", "Таш-Кумыр": "Таш-Кумыре", "Токтогул": "Токтогуле",
    "Кочкор-Ата": "Кочкор-Ате", "Баткен": "Баткене", "Исфана": "Исфане", "Кызыл-Кия": "Кызыл-Кие",
    "Сулюкта": "Сулюкте", "Кадамжай": "Кадамжае", "Нарын": "Нарыне", "Каракол": "Караколе",
    "Балыкчы": "Балыкчы", "Чолпон-Ата": "Чолпон-Ате", "Талас": "Таласе",
}

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya", "ө": "o", "ү": "u", "ң": "n",
}


def slugify(text: str) -> str:
    out = "".join(_TRANSLIT.get(ch, ch) for ch in text.lower().replace("г. ", ""))
    out = "".join(ch if ch.isascii() and ch.isalnum() else "-" for ch in out)
    return "-".join(part for part in out.split("-") if part)


def category_info(name: str) -> dict:
    info = SEO_CATEGORIES.get(name)
    if info:
        return info
    return {"slug": slugify(name), "who": name, "gen": "мастера", "schema": "LocalBusiness",
            "about": f"{name}: проверенные мастера рядом с вами.", "jobs": []}


def place_kind(place: str) -> str:
    """city | district | village | region."""
    if place in PLACE_KIND:
        return PLACE_KIND[place]
    return "region"


def locative(place: str) -> str:
    """"в Бишкеке", "в Аламудунском районе", "в Чуйской области", "в селе Лебединовка"."""
    kind = place_kind(place)
    if kind == "city":
        return "в " + CITY_LOCATIVE.get(place, place)
    if kind == "district":
        return "в " + place.replace("ский район", "ском районе")
    if kind == "region":
        return "в " + place.replace("ская область", "ской области")
    return "в селе " + place


PLACE_KIND: dict[str, str] = {}
for _r in REGIONS:
    PLACE_KIND.update({c: "city" for c in _r["cities"]})
    PLACE_KIND.update({d[0]: "district" for d in _r["districts"]})
    PLACE_KIND.update({v: "village" for v in _r["villages"]})

# Regions of one city (г. Бишкек, г. Ош) have no page of their own: the city page covers them.
SEO_REGIONS = [r["name"][0] for r in REGIONS if not r["name"][0].startswith("г. ")]

# slug → place or region name
PLACE_BY_SLUG: dict[str, str] = {}
for _name in list(REGION_OF) + SEO_REGIONS:
    _slug = slugify(_name)
    if _slug in PLACE_BY_SLUG:
        raise RuntimeError(f"seo_content: duplicate place slug {_slug}")
    PLACE_BY_SLUG[_slug] = _name
PLACE_SLUG = {name: slug for slug, name in PLACE_BY_SLUG.items()}
