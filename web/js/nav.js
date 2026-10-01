const ROOT = location.pathname.includes("/admin/") ? "../" : "";

async function renderNav() {
  const nav = document.getElementById("nav");
  if (!nav) return;
  const token = getToken();
  if (!token) {
    nav.innerHTML = `<a href="${ROOT}login.html">Войти</a>`;
    return;
  }
  try {
    const user = await api.me();
    const links = [];
    if (user.role === "master") links.push(`<a href="${ROOT}master-profile-edit.html">Мой профиль</a>`);
    if (user.role === "admin") links.push(`<a href="${ROOT}admin/index.html">Админка</a>`);
    const extra = links.length ? links.join(" · ") + " · " : "";
    nav.innerHTML = `${extra}<span>${esc(user.name)}</span> · <a href="#" onclick="logout(); return false;">Выйти</a>`;
  } catch (e) {
    clearToken();
    nav.innerHTML = `<a href="${ROOT}login.html">Войти</a>`;
  }
}

function logout() {
  clearToken();
  location.href = `${ROOT}index.html`;
}
