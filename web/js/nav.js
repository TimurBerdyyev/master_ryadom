async function renderNav() {
  const nav = document.getElementById("nav");
  if (!nav) return;
  const token = getToken();
  if (!token) {
    nav.innerHTML = `<a href="login.html">Войти</a>`;
    return;
  }
  try {
    const user = await api.me();
    nav.innerHTML = `<span>${user.name}</span> · <a href="#" onclick="logout(); return false;">Выйти</a>`;
  } catch (e) {
    clearToken();
    nav.innerHTML = `<a href="login.html">Войти</a>`;
  }
}

function logout() {
  clearToken();
  location.href = "index.html";
}
