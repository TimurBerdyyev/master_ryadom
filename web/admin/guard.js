async function requireAdmin() {
  if (!getToken()) {
    const page = location.pathname.split("/").pop();
    location.href = `../login.html?next=${encodeURIComponent(`admin/${page}`)}`;
    return null;
  }
  try {
    const user = await api.me();
    if (user.role !== "admin") {
      location.href = "../index.html";
      return null;
    }
    return user;
  } catch (e) {
    clearToken();
    location.href = "../login.html";
    return null;
  }
}
