const API_BASE = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1"
  ? "http://localhost:8000"
  : "/api";

function esc(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function uploadUrl(path) {
  return path.startsWith("/uploads/") ? `${API_BASE}${path}` : path;
}

// Only allow redirects to pages of this site: "?next=https://evil" or "?next=javascript:..."
// would otherwise send a freshly logged-in user anywhere / run script.
function safeNext(value, fallback = "index.html") {
  if (!value) return fallback;
  if (!/^[A-Za-z0-9_\-./]+\.html(\?[^#\s]*)?$/.test(value) || value.includes("//") || value.includes("..")) {
    return fallback;
  }
  return value.replace(/^\/+/, "");
}

// Lucide icon + tint per category (tint = icon color, background is a light wash of it).
const CATEGORY_STYLE = {
  "Сантехника": ["shower-head", "#0ea5e9"],
  "Электрика": ["zap", "#f59e0b"],
  "Строительство": ["brick-wall", "#ea580c"],
  "Ремонт": ["hammer", "#7c3aed"],
  "Уборка": ["spray-can", "#14b8a6"],
  "Компьютеры": ["monitor", "#2d5bff"],
  "Ремонт телефонов": ["smartphone", "#6366f1"],
  "Автомастера": ["car", "#ef4444"],
  "Красота": ["flower-2", "#ec4899"],
  "Парикмахеры": ["scissors", "#d946ef"],
  "Грузчики": ["package", "#a16207"],
  "Перевозки": ["truck", "#0891b2"],
  "Ремонт бытовой техники": ["washing-machine", "#16a34a"],
  "Другое": ["layout-grid", "#64748b"],
};

// Most requested first (the API returns categories alphabetically); unknown ones keep their place after these.
const CATEGORY_ORDER = ["Сантехника", "Электрика", "Ремонт", "Уборка", "Грузчики", "Перевозки", "Ремонт бытовой техники",
  "Строительство", "Компьютеры", "Ремонт телефонов", "Автомастера", "Красота", "Парикмахеры", "Другое"];

function sortCategories(list) {
  const rank = name => { const i = CATEGORY_ORDER.indexOf(name); return i === -1 ? CATEGORY_ORDER.length - 1 : i; };
  return [...list].sort((a, b) => rank(a.name) - rank(b.name));
}

function categoryStyle(name) {
  const [iconName, tint] = CATEGORY_STYLE[name] || ["wrench", "#12875a"];
  return { iconName, tint, style: `--tint:${tint};--tint-bg:${tint}17` };
}

// Inline icon for a category (used next to its name in chips/selects-free text).
function categoryIcon(name) {
  return icon(categoryStyle(name).iconName);
}

// Tinted rounded square with the category icon.
function categoryBadge(name, cls = "cat-dot") {
  const c = categoryStyle(name);
  return `<span class="${cls}" style="${c.style}">${icon(c.iconName)}</span>`;
}

// Cities are stored as canonical Russian names; show them in the interface language.
function cityName(name) {
  return name && I18N[`city.${name}`] ? t(`city.${name}`) : (name || "");
}

let _citiesPromise = null;

function getCities() {
  _citiesPromise = _citiesPromise || api.publicConfig().then(cfg => cfg.cities || []);
  return _citiesPromise;
}

// <option>s for a city <select>; the first option is a placeholder with an empty value.
async function cityOptionsHtml(selected, placeholderKey = "city.choose") {
  const cities = await getCities();
  // Keep a legacy free-text value visible instead of silently dropping it.
  const list = selected && !cities.includes(selected) ? [selected, ...cities] : cities;
  return `<option value="">${t(placeholderKey)}</option>` + list
    .map(c => `<option value="${esc(c)}" ${c === selected ? "selected" : ""}>${esc(cityName(c))}</option>`)
    .join("");
}

const AVATAR_COLORS = ["#2563eb", "#7c3aed", "#db2777", "#ea580c", "#0d9488", "#16a34a", "#0891b2", "#4f46e5"];

function avatarColor(name) {
  let hash = 0;
  for (const ch of String(name || "")) hash = (hash * 31 + ch.codePointAt(0)) >>> 0;
  return AVATAR_COLORS[hash % AVATAR_COLORS.length];
}

function initials(name) {
  return esc(String(name || "?").trim().split(/\s+/).map(p => p[0]).join("").slice(0, 2).toUpperCase());
}

// The master's own photo when there is one, otherwise coloured initials.
function avatarHtml(name, cls = "avatar", photo = null) {
  if (photo) return `<img class="${cls} photo" src="${esc(uploadUrl(photo))}" alt="${esc(name)}" loading="lazy">`;
  return `<div class="${cls}" style="background:${avatarColor(name)}">${initials(name)}</div>`;
}

// Compact master card (home page "top masters"): photo, name, rating, jobs, price.
function taskerMiniHtml(m) {
  const prices = m.services.map(s => s.price_from);
  return `
    <a class="tasker-mini" href="master.html?id=${m.id}">
      ${avatarHtml(m.user.name, "avatar", m.user.photo)}
      <div class="tasker-mini-body">
        <div class="tasker-name">${esc(m.user.name)} ${m.verified ? `<span class="tasker-check" title="${t("guarantee.badge")}">${icon("badge-check")}</span>` : ""}</div>
        <div class="tasker-rating">${icon("star", "sm")}<b>${Number(m.rating).toFixed(1)}</b><span>· ${plural(m.completed_orders, "p.orders")}</span></div>
        ${prices.length ? `<div class="tasker-price">${priceFromHtml(Math.min(...prices))}</div>` : ""}
      </div>
    </a>`;
}

function starsHtml(rating) {
  const full = Math.round(Number(rating) || 0);
  const star = on => `<svg class="i ${on ? "" : "off"}" aria-hidden="true"><use href="#i-star"></use></svg>`;
  return `<span class="stars" aria-label="${t("common.ratingOf", { n: full })}">${[1, 2, 3, 4, 5].map(n => star(n <= full)).join("")}</span>`;
}

function ratingHtml(rating) {
  return `<span class="rating">${icon("star", "sm")}${Number(rating).toFixed(1)}</span>`;
}

function verifiedBadge() {
  return `<span class="badge guarantee" title="${t("guarantee.tip")}">${icon("shield-check")}${t("guarantee.badge")}</span>`;
}

function emptyState(iconName, title, text, actionHtml = "") {
  return `
    <div class="empty">
      <div class="empty-icon">${icon(iconName)}</div>
      <h3>${title}</h3>
      ${text ? `<p>${text}</p>` : ""}
      ${actionHtml}
    </div>`;
}

function formatPrice(value) {
  const locale = LANG === "en" ? "en-GB" : "ru-RU";  // ky has no browser locale data; it groups digits like ru
  return `${Number(value).toLocaleString(locale, { maximumFractionDigits: 0 })} ${t("cur")}`;
}

// "от 600 сом" / "600 сомдон баштап" / "from 600 som"
function priceFrom(value) {
  return t("price.from", { price: formatPrice(value) });
}

function priceFromHtml(value) {
  return t("price.fromHtml", { price: formatPrice(value) });
}

function formatDate(value, withTime = false) {
  if (!value) return "";
  const d = new Date(value.endsWith("Z") || value.includes("+") ? value : value + "Z");
  if (LANG === "ky") {
    // Browsers ship no Kyrgyz date data (they fall back to Russian), so format by hand: "1-октябрь, 12:52"
    const months = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];
    const time = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    return `${d.getDate()}-${months[d.getMonth()]}${withTime ? ", " + time : ""}`;
  }
  const opts = { day: "numeric", month: "long" };
  if (withTime) Object.assign(opts, { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleString(langLocale(), opts);
}

function waLink(phone) {
  return `https://wa.me/${String(phone).replace(/[^\d]/g, "")}`;
}

function telLink(phone) {
  return `tel:${String(phone).replace(/[^\d+]/g, "")}`;
}

const ORDER_STATUS_CLASS = {
  searching: "info",
  offers_received: "warn",
  master_selected: "info",
  master_confirmed: "info",
  master_en_route: "info",
  in_progress: "info",
  completed: "ok",
  reviewed: "ok",
  cancelled: "bad",
};

function statusLabel(status) {
  return I18N[`status.${status}`] ? t(`status.${status}`) : status;
}

function statusPillHtml(status) {
  return `<span class="status-pill ${ORDER_STATUS_CLASS[status] || ""}">${esc(statusLabel(status))}</span>`;
}

function toast(message) {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    el.className = "toast";
    document.body.appendChild(el);
  }
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(el._timer);
  el._timer = setTimeout(() => el.classList.remove("show"), 2600);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    // Older browsers / insecure context: fall back to a temporary textarea.
    const area = document.createElement("textarea");
    area.value = text;
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
  toast(t("common.copied"));
}

// Admin: permanently delete an account after a confirmation that names the person.
async function adminDeleteAccount(userId, name, onDone) {
  if (!confirm(t("admin.deleteConfirm", { name }))) return;
  try {
    await api.adminDeleteUser(userId);
    toast(t("admin.deleted"));
    onDone();
  } catch (e) {
    toast(t("common.error") + e.message);
  }
}

function skeletons(count, height = 92) {
  return Array.from({ length: count }, () => `<div class="skeleton" style="height:${height}px"></div>`).join("");
}

// Redirects guests to the login page (coming back to `next` afterwards).
// Returns false when it redirected, so the page can skip API calls that would just 401.
function requireLogin(next) {
  if (getToken()) return true;
  location.replace(`login.html?next=${encodeURIComponent(next)}`);
  return false;
}

// Clients have no account: each request is opened by a private link with a secret token.
// We remember those links in this browser so "Мои заявки" can list them.
const MY_REQUESTS_KEY = "myRequests";

function myRequests() {
  try {
    const list = JSON.parse(localStorage.getItem(MY_REQUESTS_KEY) || "[]");
    return Array.isArray(list) ? list.filter(r => r && typeof r.token === "string") : [];
  } catch (e) {
    return [];
  }
}

function rememberRequest(token, id) {
  const list = myRequests().filter(r => r.token !== token);
  list.unshift({ token, id, savedAt: new Date().toISOString() });
  localStorage.setItem(MY_REQUESTS_KEY, JSON.stringify(list.slice(0, 50)));
}

function forgetRequest(token) {
  localStorage.setItem(MY_REQUESTS_KEY, JSON.stringify(myRequests().filter(r => r.token !== token)));
}

function requestLink(token) {
  // A #fragment is never sent to the server, so the token stays out of logs and Referer headers.
  return `${location.origin}${location.pathname.replace(/[^/]*$/, "")}order-detail.html#token=${encodeURIComponent(token)}`;
}

// The request token from "#token=..." (or "?token=..." if a messenger dropped the fragment).
function tokenFromUrl() {
  const fromHash = new URLSearchParams(location.hash.slice(1)).get("token");
  return fromHash || new URLSearchParams(location.search).get("token");
}

// Seconds left until an ISO deadline (negative when it has passed).
function secondsLeft(deadline) {
  const d = new Date(deadline.endsWith("Z") || deadline.includes("+") ? deadline : deadline + "Z");
  return Math.round((d - Date.now()) / 1000);
}

function formatCountdown(seconds) {
  const s = Math.max(0, seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

// Clients have no account: this random key, kept only in this browser, is how the server recognises
// their chats and where to send their notifications (it stores only a hash of it).
function getClientKey() {
  try {
    let key = localStorage.getItem("clientKey");
    if (!key || key.length < 32) {
      const bytes = crypto.getRandomValues(new Uint8Array(32));
      key = btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
      localStorage.setItem("clientKey", key);
    }
    return key;
  } catch (e) {
    return null;
  }
}

// Endpoints where a client (no account) is identified by the key above.
function needsClientKey(path) {
  return /^\/(chats|requests|notifications\/web-push)(\/|\?|$)/.test(path);
}

function getToken() {
  return localStorage.getItem("token");
}

function setToken(token) {
  localStorage.setItem("token", token);
}

function clearToken() {
  localStorage.removeItem("token");
  localStorage.removeItem("role");
}

// The logged-in role, remembered so the home page can send a master to their orders before it renders.
function setRole(role) {
  try { localStorage.setItem("role", role); } catch (e) { /* ignore */ }
}

// Where a logged-in user starts: masters work from the order feed, admins from the admin panel.
function homeFor(role) {
  return role === "master" ? "feed.html" : role === "admin" ? "admin/index.html" : "index.html";
}

// FastAPI returns a string for our own errors but a list of {loc, msg} for validation errors.
function errorDetail(body) {
  if (!body || !body.detail) return null;
  if (typeof body.detail === "string") return tServer(body.detail);
  if (Array.isArray(body.detail)) {
    return body.detail.map(d => tServer(String(d.msg || "").replace(/^Value error, /, ""))).join("; ");
  }
  return null;
}

async function apiRequest(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (needsClientKey(path) && getClientKey()) headers["X-Client-Key"] = getClientKey();

  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  } catch (e) {
    throw new Error(t("srv.network"));
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = errorDetail(await res.json()) || detail;
    } catch (e) {
      /* ignore */
    }
    const error = new Error(detail);
    error.status = res.status;  // e.g. 402 — the master's subscription has run out
    throw error;
  }
  if (res.status === 204) return null;
  return res.json();
}

async function apiUpload(path, files, field = "files") {
  const formData = new FormData();
  for (const file of files) formData.append(field, file);

  const headers = {};
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (needsClientKey(path) && getClientKey()) headers["X-Client-Key"] = getClientKey();

  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, { method: "POST", headers, body: formData });
  } catch (e) {
    throw new Error(t("srv.network"));
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = errorDetail(await res.json()) || detail;
    } catch (e) {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json();
}

const api = {
  register: (data) => apiRequest("/auth/register", { method: "POST", body: JSON.stringify(data) }),
  login: (data) => apiRequest("/auth/login", { method: "POST", body: JSON.stringify(data) }),
  me: () => apiRequest("/auth/me"),
  deleteAccount: (password) => apiRequest("/auth/delete-account", { method: "POST", body: JSON.stringify({ password }) }),
  categories: () => apiRequest("/categories"),
  searchMasters: (params) => apiRequest(`/masters?${new URLSearchParams(params)}`),
  getMaster: (id) => apiRequest(`/masters/${id}`),
  masterReviews: (id) => apiRequest(`/masters/${id}/reviews`),
  // Client requests (no account; the token is the private link)
  createRequest: (data) => apiRequest("/requests", { method: "POST", body: JSON.stringify(data) }),
  getRequest: (token) => apiRequest(`/requests/${encodeURIComponent(token)}`),
  acceptRequestOffer: (token, offerId) => apiRequest(`/requests/${encodeURIComponent(token)}/accept?offer_id=${encodeURIComponent(offerId)}`, { method: "POST" }),
  completeRequest: (token) => apiRequest(`/requests/${encodeURIComponent(token)}/complete`, { method: "POST" }),
  cancelRequest: (token) => apiRequest(`/requests/${encodeURIComponent(token)}/cancel`, { method: "POST" }),
  reviewRequest: (token, data) => apiRequest(`/requests/${encodeURIComponent(token)}/review`, { method: "POST", body: JSON.stringify(data) }),
  uploadRequestPhotos: (token, files) => apiUpload(`/requests/${encodeURIComponent(token)}/photos`, files),
  requestComplaint: (token, text) => apiRequest(`/requests/${encodeURIComponent(token)}/complaint`, { method: "POST", body: JSON.stringify({ text }) }),
  // Chat (clients by key, masters by login)
  startChat: (masterId, name, text) => apiRequest("/chats", { method: "POST", body: JSON.stringify({ master_id: masterId, name, text }) }),
  chats: () => apiRequest("/chats"),
  chat: (id) => apiRequest(`/chats/${id}`),
  chatMessages: (id, afterId = 0) => apiRequest(`/chats/${id}/messages?after_id=${afterId}`),
  sendChatMessage: (id, text) => apiRequest(`/chats/${id}/messages`, { method: "POST", body: JSON.stringify({ text }) }),
  sendChatPhoto: (id, file) => apiUpload(`/chats/${id}/photos`, [file], "file"),
  blockChat: (id, blocked) => apiRequest(`/chats/${id}/block?blocked=${blocked}`, { method: "POST" }),
  chatsUnread: () => apiRequest("/chats/unread"),
  // Map and live location
  nearbyMasters: (params) => apiRequest(`/masters/nearby?${new URLSearchParams(params)}`),
  shareLocation: (lat, lon, accuracy) => apiRequest("/masters/me/location", { method: "PUT", body: JSON.stringify({ lat, lon, accuracy }) }),
  stopLocation: () => apiRequest("/masters/me/location", { method: "DELETE" }),
  // Browser notifications (Web Push)
  webPushKey: () => apiRequest("/notifications/web-push/key"),
  webPushSubscribe: (sub) => apiRequest("/notifications/web-push/subscribe", { method: "POST", body: JSON.stringify({ ...sub, lang: LANG }) }),
  // Master side
  myOrders: () => apiRequest("/orders"),
  getOrder: (id) => apiRequest(`/orders/${id}`),
  uploadAvatar: (file) => apiUpload("/masters/me/avatar", [file], "file"),
  deleteAvatar: () => apiRequest("/masters/me/avatar", { method: "DELETE" }),
  acceptAgreement: () => apiRequest("/masters/me/accept-agreement", { method: "POST" }),
  uploadMyPhotos: (files) => apiUpload("/masters/me/photos", files),
  deleteMyPhoto: (id) => apiRequest(`/masters/me/photos/${id}`, { method: "DELETE" }),
  myMasterProfile: () => apiRequest("/masters/me"),
  updateMasterProfile: (data) => apiRequest("/masters/me", { method: "PATCH", body: JSON.stringify(data) }),
  addMyService: (data) => apiRequest("/masters/me/services", { method: "POST", body: JSON.stringify(data) }),
  deleteMyService: (id) => apiRequest(`/masters/me/services/${id}`, { method: "DELETE" }),
  orderFeed: () => apiRequest("/orders/feed"),
  orderOffers: (id) => apiRequest(`/orders/${id}/offers`),
  makeOffer: (id, data) => apiRequest(`/orders/${id}/offer`, { method: "POST", body: JSON.stringify(data) }),
  declineOrder: (id) => apiRequest(`/orders/${id}/decline`, { method: "POST" }),
  setOrderStatus: (id, status) => apiRequest(`/orders/${id}/status`, { method: "POST", body: JSON.stringify({ status }) }),
  notifications: () => apiRequest("/notifications"),
  unreadCount: () => apiRequest("/notifications/unread-count"),
  readAllNotifications: () => apiRequest("/notifications/read-all", { method: "POST" }),
  publicConfig: () => apiRequest("/config"),
  sendCode: (email, purpose, extra = {}) => apiRequest("/auth/send-code", { method: "POST", body: JSON.stringify({ email, purpose, lang: LANG, ...extra }) }),
  changeEmail: (email, code) => apiRequest("/auth/change-email", { method: "POST", body: JSON.stringify({ email, code }) }),
  resetPassword: (data) => apiRequest("/auth/reset-password", { method: "POST", body: JSON.stringify(data) }),
  registerDevice: (token) => apiRequest("/notifications/devices", { method: "POST", body: JSON.stringify({ token, platform: "android" }) }),
  testPush: () => apiRequest("/notifications/devices/test", { method: "POST" }),
  adminPushStatus: () => apiRequest("/admin/push"),
  removeDevice: (token) => apiRequest("/notifications/devices/remove", { method: "POST", body: JSON.stringify({ token, platform: "android" }) }),
  notificationSettings: () => apiRequest("/notifications/settings"),
  saveNotificationSettings: (data) => apiRequest("/notifications/settings", { method: "PUT", body: JSON.stringify(data) }),
  telegramLink: () => apiRequest("/notifications/telegram/link", { method: "POST" }),
  mySubscription: () => apiRequest("/subscription/me"),
  checkoutSubscription: (months) => apiRequest("/subscription/me/checkout", { method: "POST", body: JSON.stringify({ months }) }),
  adminSubscriptions: () => apiRequest("/admin/subscriptions"),
  adminSubscriptionPayments: (params = {}) => apiRequest(`/admin/subscription-payments?${new URLSearchParams(params)}`),
  adminConfirmPayment: (id) => apiRequest(`/admin/subscription-payments/${id}/confirm`, { method: "POST" }),
  adminExtendSubscription: (masterId, months) => apiRequest(`/admin/subscriptions/${masterId}/extend`, { method: "POST", body: JSON.stringify({ months }) }),

  adminStats: () => apiRequest("/admin/stats"),
  adminUsers: (params = {}) => apiRequest(`/admin/users?${new URLSearchParams(params)}`),
  adminDeleteUser: (id) => apiRequest(`/admin/users/${id}`, { method: "DELETE" }),
  adminSetUserStatus: (id, status) => apiRequest(`/admin/users/${id}/status`, { method: "PATCH", body: JSON.stringify({ status }) }),
  adminMasters: (params = {}) => apiRequest(`/admin/masters?${new URLSearchParams(params)}`),
  adminVerifyMaster: (id, verified) => apiRequest(`/admin/masters/${id}/verify`, { method: "PATCH", body: JSON.stringify({ verified }) }),
  adminUnanswered: () => apiRequest("/admin/unanswered"),
  adminViolations: () => apiRequest("/admin/violations"),
  adminOrders: (params = {}) => apiRequest(`/admin/orders?${new URLSearchParams(params)}`),
  adminComplaints: (params = {}) => apiRequest(`/admin/complaints?${new URLSearchParams(params)}`),
  adminSetComplaintStatus: (id, status) => apiRequest(`/admin/complaints/${id}`, { method: "PATCH", body: JSON.stringify({ status }) }),
  adminCategories: () => apiRequest("/categories"),
  adminAddCategory: (data) => apiRequest("/admin/categories", { method: "POST", body: JSON.stringify(data) }),
  adminDeleteCategory: (id) => apiRequest(`/admin/categories/${id}`, { method: "DELETE" }),
};
