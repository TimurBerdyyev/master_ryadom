const ROOT = location.pathname.includes("/admin/") ? "../" : "";

function navLink(href, label, iconName) {
  const current = location.pathname.split("/").pop() || "index.html";
  const isActive = href.split("/").pop() === current && location.pathname.includes("/admin/") === href.startsWith("admin/");
  return `<a class="nav-link${isActive ? " active" : ""}" href="${ROOT}${href}">${iconName ? icon(iconName) : ""}${label}</a>`;
}

function ensureNavToggle(nav) {
  if (document.getElementById("navToggle")) return;
  const toggle = document.createElement("button");
  toggle.id = "navToggle";
  toggle.className = "nav-toggle";
  toggle.type = "button";
  toggle.setAttribute("aria-label", t("nav.menu"));
  toggle.innerHTML = icon("menu", "lg");
  toggle.addEventListener("click", () => {
    const open = nav.classList.toggle("open");
    toggle.innerHTML = icon(open ? "x" : "menu", "lg");
    toggle.setAttribute("aria-expanded", String(open));
  });
  nav.parentNode.insertBefore(toggle, nav);
}

function ensureLangSwitch(nav) {
  if (document.getElementById("langSwitch")) return;
  const current = LANGS.find(l => l.code === LANG);
  const box = document.createElement("div");
  box.id = "langSwitch";
  box.className = "lang-switch";
  box.innerHTML = `
    <button type="button" class="lang-btn" aria-haspopup="true" aria-expanded="false" aria-label="${t("lang.label")}">
      ${icon("globe")}<span>${current.short}</span>${icon("chevron-down", "sm")}
    </button>
    <div class="lang-menu" role="menu" hidden>
      ${LANGS.map(l => `
        <button type="button" role="menuitemradio" aria-checked="${l.code === LANG}" class="${l.code === LANG ? "active" : ""}" data-lang="${l.code}">
          <span class="code">${l.short}</span>${l.label}${l.code === LANG ? icon("check", "sm") : ""}
        </button>`).join("")}
    </div>`;
  const button = box.querySelector(".lang-btn");
  const menu = box.querySelector(".lang-menu");
  const close = () => { menu.hidden = true; button.setAttribute("aria-expanded", "false"); };
  button.addEventListener("click", (e) => {
    e.stopPropagation();
    menu.hidden = !menu.hidden;
    button.setAttribute("aria-expanded", String(!menu.hidden));
  });
  menu.addEventListener("click", (e) => {
    const item = e.target.closest("[data-lang]");
    if (item) setLang(item.dataset.lang);
  });
  document.addEventListener("click", close);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  nav.parentNode.insertBefore(box, nav);
}

async function renderNav() {
  const nav = document.getElementById("nav");
  if (!nav) return;
  ensureLangSwitch(nav);
  ensureNavToggle(nav);

  // Clients have no account: they search, call, leave requests and come back via "Мои заявки".
  const guestNav = `
    ${navLink("masters.html", t("nav.find"), "search")}
    ${navLink("my-requests.html", t("nav.myRequests"), "clipboard-list")}
    ${navLink("login.html", t("nav.forMasters"), "briefcase")}
    <a class="btn small accent" href="${ROOT}order.html">${icon("plus")}${t("nav.createOrder")}</a>
  `;

  if (!getToken()) {
    nav.innerHTML = guestNav;
    return;
  }
  try {
    const user = await api.me();
    const links = [];
    if (user.role === "master") {
      links.push(navLink("feed.html", t("nav.feed"), "inbox"));
      links.push(navLink("orders.html", t("nav.myJobs"), "clipboard-list"));
      links.push(navLink("master-profile-edit.html", t("common.profile"), "settings-2"));
      links.push(`<span id="planLink"></span>`);
    } else {
      links.push(navLink("masters.html", t("nav.find"), "search"));
      links.push(navLink("my-requests.html", t("nav.myRequests"), "clipboard-list"));
    }
    if (user.role === "admin") links.push(navLink("admin/index.html", t("nav.admin"), "shield-check"));

    nav.innerHTML = `
      ${links.join("")}
      <a class="nav-bell" href="${ROOT}notifications.html" title="${t("nav.notifications")}" aria-label="${t("nav.notifications")}">${icon("bell", "lg")}<span class="label">${t("nav.notifications")}</span><span class="count" id="unreadCount" hidden></span></a>
      <span class="nav-user">${esc(user.name)}${avatarHtml(user.name, "avatar-xs", user.photo)}</span>
      <a class="nav-link" href="#" onclick="logout(); return false;" title="${t("nav.logout")}">${icon("log-out")}<span class="nav-logout-label">${t("nav.logout")}</span></a>
    `;
    refreshUnread();
    if (user.role === "master") renderPlanLink();
  } catch (e) {
    clearToken();
    nav.innerHTML = guestNav;
  }
}

// "Тариф" appears only once paid plans are switched on; a dot warns about an expired or ending plan.
async function renderPlanLink() {
  const slot = document.getElementById("planLink");
  if (!slot) return;
  try {
    const sub = await api.mySubscription();
    if (!sub.enabled) return;
    const warn = sub.state === "expired" || sub.days_left <= 5;
    slot.outerHTML = navLink("subscription.html", t("nav.plan") + (warn ? ' <span class="nav-dot"></span>' : ""), "wallet");
  } catch (e) {
    /* no plan link */
  }
}

async function refreshUnread() {
  const badge = document.getElementById("unreadCount");
  if (!badge) return;
  try {
    const { unread } = await api.unreadCount();
    badge.textContent = unread > 99 ? "99+" : String(unread);
    badge.hidden = unread === 0;
  } catch (e) {
    badge.hidden = true;
  }
}

function logout() {
  clearToken();
  location.href = `${ROOT}index.html`;
}
