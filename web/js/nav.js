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
  toggle.setAttribute("aria-label", "Меню");
  toggle.innerHTML = icon("menu", "lg");
  toggle.addEventListener("click", () => {
    const open = nav.classList.toggle("open");
    toggle.innerHTML = icon(open ? "x" : "menu", "lg");
    toggle.setAttribute("aria-expanded", String(open));
  });
  nav.parentNode.insertBefore(toggle, nav);
}

async function renderNav() {
  const nav = document.getElementById("nav");
  if (!nav) return;
  ensureNavToggle(nav);

  const guestNav = `
    ${navLink("masters.html", "Найти мастера", "search")}
    ${navLink("register.html?role=master", "Стать мастером", "briefcase")}
    ${navLink("login.html", "Войти", "user")}
    <a class="btn small accent" href="${ROOT}order.html">${icon("plus")}Создать заказ</a>
  `;

  if (!getToken()) {
    nav.innerHTML = guestNav;
    return;
  }
  try {
    const user = await api.me();
    const links = [];
    if (user.role === "master") {
      links.push(navLink("feed.html", "Новые заказы", "inbox"));
      links.push(navLink("orders.html", "Мои работы", "clipboard-list"));
      links.push(navLink("master-profile-edit.html", "Профиль", "settings-2"));
    } else {
      links.push(navLink("masters.html", "Найти мастера", "search"));
      links.push(navLink("orders.html", "Мои заказы", "clipboard-list"));
    }
    if (user.role === "admin") links.push(navLink("admin/index.html", "Админка", "shield-check"));

    nav.innerHTML = `
      ${links.join("")}
      <a class="nav-bell" href="${ROOT}notifications.html" title="Уведомления" aria-label="Уведомления">${icon("bell", "lg")}<span class="label">Уведомления</span><span class="count" id="unreadCount" hidden></span></a>
      <span class="nav-user">${esc(user.name)}${avatarHtml(user.name, "avatar-xs")}</span>
      <a class="nav-link" href="#" onclick="logout(); return false;" title="Выйти">${icon("log-out")}<span class="nav-logout-label">Выйти</span></a>
    `;
    refreshUnread();
  } catch (e) {
    clearToken();
    nav.innerHTML = guestNav;
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
