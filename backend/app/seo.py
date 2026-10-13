"""Pages for search engines: rendered on the server, so Google and Yandex see the whole text without JavaScript.

- /uslugi                      — all services and towns
- /uslugi/{category}           — a service across Kyrgyzstan, links to every region and town
- /uslugi/{category}/{place}   — «Сантехник в Бишкеке»: masters, prices, popular jobs, FAQ
- /master/{id}                 — public master profile (master.html is the interactive one, kept out of the index)
- /sitemap.xml, /robots.txt, /.well-known/assetlinks.json (Android App Links)

Pages with nothing to show (a village without masters) are served but marked noindex, so the index holds
no empty doorway pages. Links: no trailing slash (nav.js treats "…/" as the home page).
"""
import json
from datetime import datetime
from html import escape
from xml.sax.saxutils import escape as xml_escape

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.cities import REGIONS, REGION_OF
from app.config import settings
from app.database import get_db
from app.models import Category, Master, Review, Service, User, UserStatus
from app.seo_content import (
    KEY_CITIES, PLACE_BY_SLUG, PLACE_SLUG, SEO_REGIONS, category_info, locative, place_kind,
)
from app.subscriptions import filter_masters_with_access

router = APIRouter(include_in_schema=False)

SITE_NAME = "Мастер рядом"
MASTERS_PER_PAGE = 20


def e(value) -> str:
    return escape(str(value), quote=True)


def site() -> str:
    return settings.site_url.rstrip("/")


def money(value) -> str:
    return f"{int(value):,}".replace(",", " ") + " сом"


def json_ld(data: dict) -> str:
    # "</" inside a string would close the <script> early
    return ('<script type="application/ld+json">'
            + json.dumps(data, ensure_ascii=False).replace("</", "<\\/") + "</script>")


def media(url: str | None) -> str | None:
    if not url:
        return None
    return f"/api{url}" if url.startswith("/uploads/") else url


# ---------- data ----------

def _categories(db: Session) -> list[Category]:
    return db.query(Category).order_by(Category.id).all()


def _category_by_slug(db: Session, slug: str) -> Category:
    for cat in _categories(db):
        if category_info(cat.name)["slug"] == slug:
            return cat
    raise HTTPException(404)


def _visible_masters(db: Session):
    query = (
        db.query(Master).join(User, Master.user_id == User.id)
        .filter(User.status == UserStatus.active)
    )
    query = filter_masters_with_access(query)
    if settings.masters_require_verification:
        query = query.filter(Master.verified.is_(True))
    return query


def _masters_for(db: Session, category: Category | None, place: str | None) -> list[Master]:
    query = _visible_masters(db).options(selectinload(Master.user), selectinload(Master.services))
    if category is not None:
        query = query.filter(Master.services.any(Service.category_id == category.id))
    if place:
        if place_kind(place) == "region":
            query = query.filter(Master.region == place)
        else:  # same rule as the search and the request dispatch
            query = query.filter((Master.city == place) | (Master.serves_region.is_(True) & (Master.region == REGION_OF[place])))
    return query.order_by(Master.rating.desc(), Master.completed_orders.desc(), Master.id).limit(MASTERS_PER_PAGE).all()


def _review_counts(db: Session, masters: list[Master]) -> dict[int, int]:
    if not masters:
        return {}
    rows = (db.query(Review.master_id, func.count(Review.id))
            .filter(Review.master_id.in_([m.id for m in masters])).group_by(Review.master_id).all())
    return dict(rows)


def _min_price(master: Master, category: Category | None) -> float | None:
    prices = [s.price_from for s in master.services if category is None or s.category_id == category.id]
    return min(prices) if prices else None


def _price_rows(masters: list[Master], category: Category) -> list[tuple[str, float]]:
    """Lowest "from" price per service title among the shown masters."""
    best: dict[str, tuple[str, float]] = {}
    for m in masters:
        for s in m.services:
            if s.category_id != category.id:
                continue
            key = s.title.strip().lower()
            if key not in best or s.price_from < best[key][1]:
                best[key] = (s.title.strip(), s.price_from)
    return sorted(best.values(), key=lambda row: row[1])[:12]


def _indexable(place: str, masters: list[Master]) -> bool:
    return bool(masters) or place in KEY_CITIES


# ---------- layout ----------

def page(*, path: str, title: str, description: str, body: str, ld: list[dict] = (), index: bool = True,
         og_image: str = "/og-image.jpg") -> HTMLResponse:
    url = site() + path
    head = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <base href="/" />
  <title>{e(title)}</title>
  <meta name="description" content="{e(description)}" />
  <meta name="robots" content="{'index, follow' if index else 'noindex, follow'}" />
  <link rel="canonical" href="{e(url)}" />
  <meta property="og:type" content="website" />
  <meta property="og:site_name" content="{SITE_NAME}" />
  <meta property="og:locale" content="ru_RU" />
  <meta property="og:title" content="{e(title)}" />
  <meta property="og:description" content="{e(description)}" />
  <meta property="og:url" content="{e(url)}" />
  <meta property="og:image" content="{e(og_image if og_image.startswith('http') else site() + og_image)}" />
  <meta name="twitter:card" content="summary_large_image" />
  <link rel="icon" href="favicon.svg" type="image/svg+xml" />
  <link rel="manifest" href="manifest.webmanifest" />
  <meta name="theme-color" content="#ffffff" />
  <link rel="apple-touch-icon" href="apple-touch-icon.png" />
  <link rel="stylesheet" href="css/style.css" />
  {''.join(json_ld(d) for d in ld)}
</head>
<body>
  <header class="topbar">
    <a class="logo" href="index.html"><span class="logo-mark"><svg class="i" aria-hidden="true"><use href="#i-wrench"></use></svg></span> {SITE_NAME}</a>
    <nav id="nav"></nav>
  </header>
  <main class="seo-page">
{body}
  </main>
{_footer()}
  <script src="js/icons.js"></script>
  <script src="js/i18n.js"></script>
  <script src="js/api.js"></script>
  <script src="js/nav.js"></script>
  <script>renderNav();</script>
</body>
</html>"""
    return HTMLResponse(head)


def _footer() -> str:
    popular = [("Сантехника", "Бишкек"), ("Электрика", "Бишкек"), ("Ремонт", "Бишкек"), ("Уборка", "Бишкек"),
               ("Сантехника", "Ош"), ("Электрика", "Ош"), ("Грузчики", "Бишкек"), ("Ремонт бытовой техники", "Бишкек")]
    links = "".join(
        f'<li><a href="/uslugi/{category_info(c)["slug"]}/{PLACE_SLUG[p]}">'
        f'{e(category_info(c)["who"])} {e(locative(p))}</a></li>' for c, p in popular)
    return f"""  <footer class="site-footer">
    <div class="footer-grid">
      <div>
        <a class="logo" href="index.html"><span class="logo-mark"><svg class="i" aria-hidden="true"><use href="#i-wrench"></use></svg></span> {SITE_NAME}</a>
        <p>Сервис, который помогает быстро найти проверенного мастера рядом с домом и договориться о честной цене.</p>
      </div>
      <div>
        <h4>Популярные услуги</h4>
        <ul>{links}<li><a href="/uslugi">Все услуги и города</a></li></ul>
      </div>
      <div>
        <h4>Клиентам и мастерам</h4>
        <ul>
          <li><a href="order.html">Оставить заявку</a></li>
          <li><a href="masters.html">Найти мастера</a></li>
          <li><a href="app.html">Приложение</a></li>
          <li><a href="register.html">Стать мастером</a></li>
        </ul>
      </div>
    </div>
    <div class="footer-bottom">© {SITE_NAME} · <a href="privacy.html">Политика конфиденциальности</a></div>
  </footer>"""


def _crumbs(items: list[tuple[str, str]]) -> tuple[str, dict]:
    """Visible breadcrumbs + BreadcrumbList for search results."""
    html = '<nav class="seo-crumbs" aria-label="Навигация">' + " <span>›</span> ".join(
        f'<a href="{e(href)}">{e(name)}</a>' if i < len(items) - 1 else f"<span>{e(name)}</span>"
        for i, (name, href) in enumerate(items)) + "</nav>"
    ld = {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": i + 1, "name": name, "item": site() + href}
        for i, (name, href) in enumerate(items)]}
    return html, ld


def _master_card(m: Master, category: Category | None, reviews: int) -> str:
    photo = media(m.user.photo)
    avatar = (f'<img class="seo-avatar" src="{e(photo)}" alt="{e(m.user.name)}" loading="lazy" width="56" height="56" />'
              if photo else f'<span class="seo-avatar">{e(m.user.name[:1].upper())}</span>')
    price = _min_price(m, category)
    rating = (f'<span class="seo-rating">★ {m.rating:.1f}</span> · {reviews} отзыв{_plural(reviews, "", "а", "ов")}'
              if reviews else '<span class="muted">Новый мастер</span>')
    titles = ", ".join(dict.fromkeys(s.title for s in m.services if category is None or s.category_id == category.id))
    return f"""<li class="seo-master">
      <a href="/master/{m.id}">{avatar}</a>
      <div class="seo-master-body">
        <a class="seo-master-name" href="/master/{m.id}">{e(m.user.name)}</a>
        <div class="seo-meta">{rating}{' · ' + e(m.city) if m.city else ''}{' · проверен' if m.verified else ''}</div>
        {f'<div class="seo-meta">{e(titles[:160])}</div>' if titles else ''}
      </div>
      {f'<div class="seo-price">от {money(price)}</div>' if price is not None else ''}
    </li>"""


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _order_link(category: Category | None, place: str | None, task: str | None = None) -> str:
    from urllib.parse import urlencode
    params = {}
    if category is not None:
        params["category_id"] = category.id
    if place and place_kind(place) != "region":
        params["city"] = place
    if task:
        params["task"] = task
    return "order.html" + ("?" + urlencode(params) if params else "")


HOW_IT_WORKS = """<section class="seo-section">
      <h2>Как это работает</h2>
      <ol class="seo-steps">
        <li><b>Опишите задачу.</b> Имя, телефон и что нужно сделать — регистрация не нужна.</li>
        <li><b>Получите цены за 20 минут.</b> Мастера рядом с вами отвечают на заявку и называют цену.</li>
        <li><b>Выберите мастера.</b> Сравните цены, рейтинг и отзывы, договоритесь в чате или по телефону.</li>
      </ol>
    </section>"""


# ---------- pages ----------

@router.api_route("/uslugi", methods=["GET", "HEAD"])
def services_index(db: Session = Depends(get_db)):
    cats = _categories(db)
    crumbs, crumbs_ld = _crumbs([("Главная", "/"), ("Услуги", "/uslugi")])
    blocks = []
    for cat in cats:
        info = category_info(cat.name)
        towns = " · ".join(f'<a href="/uslugi/{info["slug"]}/{PLACE_SLUG[c]}">{e(c)}</a>' for c in KEY_CITIES[:8])
        blocks.append(f'<li><h3><a href="/uslugi/{info["slug"]}">{e(info["who"])}</a></h3><p>{e(info["about"])}</p>'
                      f'<p class="seo-towns">{towns}</p></li>')
    body = f"""    {crumbs}
    <h1>Услуги мастеров в Кыргызстане</h1>
    <p class="seo-lead">Сантехники, электрики, ремонт, уборка, грузчики и другие мастера в Бишкеке, Оше и по всей стране.
      Оставьте заявку — проверенные мастера пришлют цены за 20 минут.</p>
    <p><a class="btn large" href="order.html">Оставить заявку</a></p>
    <ul class="seo-cats">{''.join(blocks)}</ul>
"""
    return page(path="/uslugi", title="Услуги мастеров в Бишкеке, Оше и по Кыргызстану | Мастер рядом",
                description="Найдите сантехника, электрика, мастера по ремонту, уборке и другим работам рядом с домом. "
                            "Цены от мастеров за 20 минут, без регистрации.",
                body=body, ld=[crumbs_ld])


def _regions_block(category: Category) -> str:
    """Every region with its towns, districts and villages — links for people and crawlers."""
    prefix = f"/uslugi/{category_info(category.name)['slug']}/"
    parts = []
    for r in REGIONS:
        region = r["name"][0]
        places = r["cities"] + [d[0] for d in r["districts"]] + r["villages"]
        head = (f'<a href="{prefix}{PLACE_SLUG[region]}">{e(region)}</a>' if region in SEO_REGIONS else e(region))
        links = " · ".join(f'<a href="{prefix}{PLACE_SLUG[p]}">{e(p)}</a>' for p in places)
        parts.append(f"<li><h3>{head}</h3><p>{links}</p></li>")
    return f'<section class="seo-section"><h2>Где искать</h2><ul class="seo-regions">{"".join(parts)}</ul></section>'


@router.api_route("/uslugi/{category_slug}", methods=["GET", "HEAD"])
def category_page(category_slug: str, db: Session = Depends(get_db)):
    cat = _category_by_slug(db, category_slug)
    info = category_info(cat.name)
    masters = _masters_for(db, cat, None)
    reviews = _review_counts(db, masters)
    path = f"/uslugi/{info['slug']}"
    crumbs, crumbs_ld = _crumbs([("Главная", "/"), ("Услуги", "/uslugi"), (info["who"], path)])
    title = f"{info['who']} — Бишкек, Ош и весь Кыргызстан, цены | Мастер рядом"
    description = f"{info['who']} рядом с вами. {info['about']} Цены от мастеров за 20 минут, без регистрации."
    body = f"""    {crumbs}
    <h1>{e(info['who'])} в Кыргызстане</h1>
    <p class="seo-lead">{e(info['about'])} Оставьте заявку — проверенные мастера пришлют цены за 20 минут.</p>
    <p class="seo-actions"><a class="btn large" href="{e(_order_link(cat, None))}">Оставить заявку</a>
      <a class="btn secondary large" href="masters.html?category_id={cat.id}">Смотреть мастеров</a></p>
    <section class="seo-section"><h2>Популярные города</h2><p class="seo-towns">{' · '.join(
        f'<a href="{path}/{PLACE_SLUG[c]}">{e(info["who"])} {e(locative(c))}</a>' for c in KEY_CITIES)}</p></section>
    {_masters_section(masters, cat, reviews, "Лучшие мастера")}
    {_jobs_section(cat, None)}
    {HOW_IT_WORKS}
    {_regions_block(cat)}"""
    return page(path=path, title=title, description=description, body=body, ld=[crumbs_ld])


def _masters_section(masters: list[Master], cat: Category | None, reviews: dict[int, int], heading: str) -> str:
    if not masters:
        return ""
    cards = "".join(_master_card(m, cat, reviews.get(m.id, 0)) for m in masters)
    return f'<section class="seo-section"><h2>{e(heading)}</h2><ul class="seo-masters">{cards}</ul></section>'


def _jobs_section(cat: Category, place: str | None) -> str:
    jobs = category_info(cat.name)["jobs"]
    if not jobs:
        return ""
    items = "".join(f'<li><a href="{e(_order_link(cat, place, job))}">{e(job)}</a></li>' for job in jobs)
    return f'<section class="seo-section"><h2>Частые задачи</h2><ul class="seo-jobs">{items}</ul></section>'


@router.api_route("/uslugi/{category_slug}/{place_slug}", methods=["GET", "HEAD"])
def place_page(category_slug: str, place_slug: str, db: Session = Depends(get_db)):
    cat = _category_by_slug(db, category_slug)
    place = PLACE_BY_SLUG.get(place_slug)
    if not place:
        raise HTTPException(404)
    info = category_info(cat.name)
    loc = locative(place)
    kind = place_kind(place)
    masters = _masters_for(db, cat, place)
    reviews = _review_counts(db, masters)
    prices = _price_rows(masters, cat)
    cheapest = min((p for _, p in prices), default=None)
    path = f"/uslugi/{info['slug']}/{place_slug}"

    trail = [("Главная", "/"), ("Услуги", "/uslugi"), (info["who"], f"/uslugi/{info['slug']}")]
    region = place if kind == "region" else REGION_OF[place]
    if kind != "region" and region in SEO_REGIONS:
        trail.append((region, f"/uslugi/{info['slug']}/{PLACE_SLUG[region]}"))
    trail.append((place, path))
    crumbs, crumbs_ld = _crumbs(trail)

    price_part = f", цены от {money(cheapest)}" if cheapest is not None else ", цены"
    title = f"{info['who']} {loc} — вызов на дом{price_part} | Мастер рядом"
    count = len(masters)
    description = (f"{info['who']} {loc}: {count} проверенн{_plural(count, 'ый мастер', 'ых мастера', 'ых мастеров')}"
                   if count else f"{info['who']} {loc}") + \
        f". {info['about']} Цены за 20 минут, без регистрации."

    if masters:
        masters_html = _masters_section(masters, cat, reviews, f"Мастера {loc}")
    else:
        wide = "" if kind == "region" or region.startswith("г. ") else f" — её получат и мастера, которые работают по всей области ({e(region)})"
        masters_html = (f'<section class="seo-section"><h2>Мастера {e(loc)}</h2>'
                        f'<p>Здесь пока мало мастеров с анкетой на сайте. Оставьте заявку{wide}. '
                        f'Если никто не ответит за 20 минут, мы сами найдём вам мастера.</p></section>')

    price_html = ""
    if prices:
        rows = "".join(f"<tr><td>{e(t)}</td><td>от {money(p)}</td></tr>" for t, p in prices)
        price_html = (f'<section class="seo-section"><h2>Цены на услуги {e(info["gen"])} {e(loc)}</h2>'
                      f'<div class="seo-table-wrap"><table class="seo-prices"><thead><tr><th>Услуга</th><th>Цена</th></tr></thead>'
                      f'<tbody>{rows}</tbody></table></div><p class="muted">Цены указали сами мастера; точную стоимость '
                      f'мастер назовёт после описания задачи.</p></section>')

    faq = [
        (f"Сколько стоят услуги {info['gen']} {loc}?",
         (f"Мастера на сайте указывают цены от {money(cheapest)}. " if cheapest is not None else "")
         + "Опишите задачу в заявке — мастера пришлют свои цены в течение 20 минут, и вы выберете подходящую."),
        ("Как быстро мастер ответит?",
         "Мастера обязаны ответить на заявку в течение 20 минут — так прописано в договоре с сервисом. "
         "Если никто не ответил, мы сами найдём вам мастера."),
        ("Нужно ли регистрироваться?",
         "Нет. Достаточно указать имя и телефон. Ссылку на заявку мы покажем сразу после отправки."),
        ("Мастера проверены?",
         "Каждого мастера мы проверяем перед тем, как показать клиентам, и заключаем с ним договор. "
         "После работы вы можете оставить отзыв — он влияет на рейтинг мастера."),
    ]
    faq_html = "".join(f"<details><summary>{e(q)}</summary><p>{e(a)}</p></details>" for q, a in faq)
    faq_ld = {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
        {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faq]}
    service_ld = {
        "@context": "https://schema.org", "@type": "Service", "name": f"{info['who']} {loc}",
        "serviceType": info["who"], "description": info["about"],
        "areaServed": {"@type": "AdministrativeArea" if kind in ("region", "district") else "City",
                       "name": place, "containedInPlace": {"@type": "Country", "name": "Кыргызстан"}},
        "provider": {"@type": "Organization", "name": SITE_NAME, "url": site() + "/"},
    }
    if cheapest is not None:
        service_ld["offers"] = {"@type": "AggregateOffer", "priceCurrency": "KGS", "lowPrice": int(cheapest),
                                "offerCount": len(prices)}

    other_places = [c for c in KEY_CITIES if c != place][:10]
    nearby = []
    if kind != "region":
        r = next(r for r in REGIONS if r["name"][0] == region)
        nearby = [p for p in r["cities"] + [d[0] for d in r["districts"]] + r["villages"] if p != place][:12]
    other_cats = [c for c in _categories(db) if c.id != cat.id]

    def place_links(places: list[str]) -> str:
        return " · ".join(f'<a href="/uslugi/{info["slug"]}/{PLACE_SLUG[p]}">{e(p)}</a>' for p in places)

    cat_links = " · ".join(
        f'<a href="/uslugi/{category_info(c.name)["slug"]}/{place_slug}">{e(category_info(c.name)["who"])}</a>'
        for c in other_cats)
    links_html = (
        '<section class="seo-section seo-links">'
        + (f'<div><h2>Рядом</h2><p class="seo-towns">{place_links(nearby)}</p></div>' if nearby else "")
        + f'<div><h2>{e(info["who"])} в других городах</h2><p class="seo-towns">{place_links(other_places)}</p></div>'
        + f'<div><h2>Другие услуги {e(loc)}</h2><p class="seo-towns">{cat_links}</p></div></section>'
    )

    city_param = f"&city={e(place)}" if kind != "region" else f"&region={e(place)}"
    body = f"""    {crumbs}
    <h1>{e(info['who'])} {e(loc)}</h1>
    <p class="seo-lead">{e(info['about'])} Опишите задачу — проверенные мастера {e(loc)} пришлют цены за 20 минут.
      Без регистрации и посредников: договаривайтесь с мастером напрямую.</p>
    <p class="seo-actions"><a class="btn large" href="{e(_order_link(cat, place))}">Оставить заявку</a>
      <a class="btn secondary large" href="masters.html?category_id={cat.id}{city_param}">Мастера на карте</a></p>
    {masters_html}
    {price_html}
    {_jobs_section(cat, place)}
    {HOW_IT_WORKS}
    <section class="seo-section seo-faq"><h2>Вопросы и ответы</h2>{faq_html}</section>
    {links_html}"""
    return page(path=path, title=title, description=description, body=body,
                ld=[crumbs_ld, service_ld, faq_ld], index=_indexable(place, masters))


@router.api_route("/master/{master_id}", methods=["GET", "HEAD"])
def master_page(master_id: int, db: Session = Depends(get_db)):
    master = (_visible_masters(db).filter(Master.id == master_id)
              .options(selectinload(Master.user), selectinload(Master.services).selectinload(Service.category),
                       selectinload(Master.photos), selectinload(Master.reviews))
              .first())
    if not master:
        raise HTTPException(404)
    name = master.user.name
    cats = list(dict.fromkeys(s.category.name for s in master.services if s.category))
    whos = [category_info(c)["who"] for c in cats]
    loc = locative(master.city) if master.city in REGION_OF else ""
    reviews = sorted(master.reviews, key=lambda r: r.created_at, reverse=True)
    title = f"{name} — {', '.join(whos[:2]).lower() if whos else 'мастер'}{' ' + loc if loc else ''} | Мастер рядом"
    about = (master.description or "").strip()
    description = (about[:150] + ("…" if len(about) > 150 else "")) if about else \
        f"{name}: {', '.join(whos).lower() or 'мастер'}{' ' + loc if loc else ''}. Цены, отзывы, связь напрямую."

    first_cat = master.services[0].category if master.services and master.services[0].category else None
    trail = [("Главная", "/"), ("Услуги", "/uslugi")]
    if first_cat is not None:
        info = category_info(first_cat.name)
        trail.append((info["who"], f"/uslugi/{info['slug']}"))
        if master.city in PLACE_SLUG:
            trail.append((master.city, f"/uslugi/{info['slug']}/{PLACE_SLUG[master.city]}"))
    trail.append((name, f"/master/{master.id}"))
    crumbs, crumbs_ld = _crumbs(trail)

    photo = media(master.user.photo)
    avatar = (f'<img class="seo-avatar xl" src="{e(photo)}" alt="{e(name)}" width="96" height="96" />'
              if photo else f'<span class="seo-avatar xl">{e(name[:1].upper())}</span>')
    facts = []
    if reviews:
        facts.append(f'<span class="seo-rating">★ {master.rating:.1f}</span> · {len(reviews)} отзыв{_plural(len(reviews), "", "а", "ов")}')
    if master.city:
        facts.append(e(master.city) + (f", {e(master.district)}" if master.district else ""))
    if master.experience_years:
        facts.append(f"опыт {master.experience_years} {_plural(master.experience_years, 'год', 'года', 'лет')}")
    if master.completed_orders:
        facts.append(f"{master.completed_orders} заказ{_plural(master.completed_orders, '', 'а', 'ов')} через сервис")
    if master.verified:
        facts.append("проверен сервисом")

    services = "".join(f"<tr><td>{e(s.title)}</td><td>от {money(s.price_from)}</td></tr>" for s in master.services)
    photos = "".join(f'<img src="{e(media(p.url))}" alt="Работа мастера {e(name)}" loading="lazy" />' for p in master.photos[:12])
    review_html = "".join(
        f'<li><div class="seo-rating">{"★" * r.rating}{"☆" * (5 - r.rating)}</div>'
        f'{f"<p>{e(r.text)}</p>" if r.text else ""}<div class="muted">{r.created_at:%d.%m.%Y}</div></li>'
        for r in reviews[:20])

    body = f"""    {crumbs}
    <section class="seo-profile">
      {avatar}
      <div>
        <h1>{e(name)}</h1>
        <p class="seo-meta">{e(', '.join(whos))}</p>
        <p class="seo-meta">{' · '.join(facts)}</p>
      </div>
    </section>
    <p class="seo-actions">
      <a class="btn large" href="master.html?id={master.id}">Написать или позвонить</a>
      <a class="btn secondary large" href="{e(_order_link(first_cat, master.city if master.city in REGION_OF else None))}">Оставить заявку</a>
    </p>
    {f'<section class="seo-section"><h2>О мастере</h2><p class="seo-about">{e(about)}</p></section>' if about else ''}
    {f'<section class="seo-section"><h2>Услуги и цены</h2><div class="seo-table-wrap"><table class="seo-prices"><tbody>{services}</tbody></table></div></section>' if services else ''}
    {f'<section class="seo-section"><h2>Фото работ</h2><div class="seo-photos">{photos}</div></section>' if photos else ''}
    {f'<section class="seo-section"><h2>Отзывы</h2><ul class="seo-reviews">{review_html}</ul></section>' if review_html else ''}"""

    schema_type = category_info(first_cat.name)["schema"] if first_cat is not None else "LocalBusiness"
    master_ld = {
        "@context": "https://schema.org", "@type": schema_type, "name": name, "url": site() + f"/master/{master.id}",
        "description": about or description,
        "address": {"@type": "PostalAddress", "addressLocality": master.city or "", "addressCountry": "KG"},
        "areaServed": master.region or master.city or "Кыргызстан",
    }
    if photo:
        master_ld["image"] = site() + photo
    if master.services:
        master_ld["priceRange"] = f"от {money(min(s.price_from for s in master.services))}"
    if reviews:
        master_ld["aggregateRating"] = {"@type": "AggregateRating", "ratingValue": round(sum(r.rating for r in reviews) / len(reviews), 1),
                                        "reviewCount": len(reviews), "bestRating": 5, "worstRating": 1}
        master_ld["review"] = [
            {"@type": "Review", "reviewRating": {"@type": "Rating", "ratingValue": r.rating, "bestRating": 5},
             "author": {"@type": "Person", "name": "Клиент"}, "datePublished": r.created_at.date().isoformat(),
             **({"reviewBody": r.text} if r.text else {})}
            for r in reviews[:5]]
    return page(path=f"/master/{master.id}", title=title, description=description, body=body,
                ld=[crumbs_ld, master_ld], og_image=photo or "/og-image.jpg")


# ---------- static pages ----------

def fill_placeholders(html: str) -> str:
    """web/*.html: __SITE_URL__ → the public address; <!--SEO_VERIFY--> → Search Console / Webmaster tags."""
    tags = ""
    if settings.google_site_verification:
        tags += f'<meta name="google-site-verification" content="{e(settings.google_site_verification)}" />'
    if settings.yandex_verification:
        tags += f'<meta name="yandex-verification" content="{e(settings.yandex_verification)}" />'
    return html.replace("__SITE_URL__", site()).replace("<!--SEO_VERIFY-->", tags)


# ---------- files for crawlers and the app ----------

STATIC_PAGES = ["/", "/masters.html", "/order.html", "/app.html", "/register.html", "/master-agreement.html",
                "/privacy.html"]


@router.api_route("/sitemap.xml", methods=["GET", "HEAD"])
def sitemap(db: Session = Depends(get_db)):
    urls: list[tuple[str, datetime | None]] = [(p, None) for p in STATIC_PAGES] + [("/uslugi", None)]
    cats = _categories(db)
    masters = _visible_masters(db).options(selectinload(Master.services)).all()
    # Place pages with at least one master (same rule as the page itself), plus the key towns.
    with_masters: set[tuple[int, str]] = set()
    for m in masters:
        for s in m.services:
            if m.city in REGION_OF:
                with_masters.add((s.category_id, m.city))
            if m.region in SEO_REGIONS:
                with_masters.add((s.category_id, m.region))
    for cat in cats:
        slug = category_info(cat.name)["slug"]
        urls.append((f"/uslugi/{slug}", None))
        places = list(KEY_CITIES) + sorted(p for c, p in with_masters if c == cat.id and p not in KEY_CITIES)
        urls += [(f"/uslugi/{slug}/{PLACE_SLUG[p]}", None) for p in places]
    urls += [(f"/master/{m.id}", m.created_at) for m in masters]
    body = "".join(
        f"<url><loc>{xml_escape(site() + path)}</loc>"
        + (f"<lastmod>{when.date().isoformat()}</lastmod>" if when else "") + "</url>"
        for path, when in urls)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>'
    return Response(xml, media_type="application/xml")


ROBOTS = """# Private pages (request links carry a secret token, chats, admin) must not be indexed.
User-agent: *
Disallow: /admin/
Disallow: /api/
Disallow: /order-detail.html
Disallow: /order-success.html
Disallow: /chat.html
Disallow: /chats.html
Disallow: /my-requests.html
Disallow: /master-profile-edit.html
Disallow: /feed.html
Disallow: /orders.html
Disallow: /notifications.html
Disallow: /subscription.html
Disallow: /reset.html

Sitemap: {site}/sitemap.xml
"""


@router.api_route("/robots.txt", methods=["GET", "HEAD"])
def robots():
    return PlainTextResponse(ROBOTS.format(site=site()))


@router.api_route("/.well-known/assetlinks.json", methods=["GET", "HEAD"])
def assetlinks():
    prints = [p.strip().upper() for p in settings.android_cert_sha256.split(",") if p.strip()]
    return JSONResponse([{
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {"namespace": "android_app", "package_name": settings.android_package,
                   "sha256_cert_fingerprints": prints},
    }])
