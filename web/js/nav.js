const ROOT = location.pathname.includes("/admin/") ? "../" : "";

function navLink(href, label) {
  const current = location.pathname.split("/").pop() || "index.html";
  const active = href.split("/").pop() === current ? " active" : "";
  return `<a class="nav-link${active}" href="${ROOT}${href}">${label}</a>`;
}

function ensureNavToggle(nav) {
  if (document.getElementById("navToggle")) return;
  const toggle = document.createElement("button");
  toggle.id = "navToggle";
  toggle.className = "nav-toggle";
  toggle.type = "button";
  toggle.setAttribute("aria-label", "Меню");
  toggle.textContent = "☰";
  toggle.addEventListener("click", () => nav.classList.toggle("open"));
  nav.parentNode.insertBefore(toggle, nav);
}

async function renderNav() {
  const nav = document.getElementById("nav");
  if (!nav) return;
  ensureNavToggle(nav);

  const guestNav = `
    ${navLink("masters.html", "Найти мастера")}
    ${navLink("login.html", "Войти")}
    <a class="btn small" href="${ROOT}register.html">Регистрация</a>
  `;

  if (!getToken()) {
    nav.innerHTML = guestNav;
    return;
  }
  try {
    const user = await api.me();
    const links = [];
    if (user.role === "master") {
      links.push(navLink("feed.html", "Новые заказы"));
      links.push(navLink("orders.html", "Мои работы"));
      links.push(navLink("master-profile-edit.html", "Профиль"));
    } else {
      links.push(navLink("masters.html", "Найти мастера"));
      links.push(navLink("orders.html", "Мои заказы"));
    }
    if (user.role === "admin") links.push(navLink("admin/index.html", "Админка"));

    nav.innerHTML = `
      ${links.join("")}
      <a class="nav-bell" href="${ROOT}notifications.html" title="Уведомления">🔔<span class="count" id="unreadCount" hidden></span></a>
      <span class="nav-user">${esc(user.name)}${avatarHtml(user.name, "avatar-xs")}</span>
      <a class="nav-link" href="#" onclick="logout(); return false;">Выйти</a>
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
