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

function categoryStyle(name) {
  const [iconName, tint] = CATEGORY_STYLE[name] || ["wrench", "#2d5bff"];
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

const AVATAR_COLORS = ["#2563eb", "#7c3aed", "#db2777", "#ea580c", "#0d9488", "#16a34a", "#0891b2", "#4f46e5"];

function avatarColor(name) {
  let hash = 0;
  for (const ch of String(name || "")) hash = (hash * 31 + ch.codePointAt(0)) >>> 0;
  return AVATAR_COLORS[hash % AVATAR_COLORS.length];
}

function initials(name) {
  return esc(String(name || "?").trim().split(/\s+/).map(p => p[0]).join("").slice(0, 2).toUpperCase());
}

function avatarHtml(name, cls = "avatar") {
  return `<div class="${cls}" style="background:${avatarColor(name)}">${initials(name)}</div>`;
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
  return `<span class="badge">${icon("badge-check")}${t("common.verified")}</span>`;
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

function getToken() {
  return localStorage.getItem("token");
}

function setToken(token) {
  localStorage.setItem("token", token);
}

function clearToken() {
  localStorage.removeItem("token");
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

async function apiUpload(path, files) {
  const formData = new FormData();
  for (const file of files) formData.append("files", file);

  const headers = {};
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

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
  categories: () => apiRequest("/categories"),
  searchMasters: (params) => apiRequest(`/masters?${new URLSearchParams(params)}`),
  getMaster: (id) => apiRequest(`/masters/${id}`),
  masterReviews: (id) => apiRequest(`/masters/${id}/reviews`),
  createOrder: (data) => apiRequest("/orders", { method: "POST", body: JSON.stringify(data) }),
  myOrders: () => apiRequest("/orders"),
  getOrder: (id) => apiRequest(`/orders/${id}`),
  uploadOrderPhotos: (orderId, files) => apiUpload(`/orders/${orderId}/photos`, files),
  deleteOrderPhoto: (orderId, photoId) => apiRequest(`/orders/${orderId}/photos/${photoId}`, { method: "DELETE" }),
  uploadMyPhotos: (files) => apiUpload("/masters/me/photos", files),
  deleteMyPhoto: (id) => apiRequest(`/masters/me/photos/${id}`, { method: "DELETE" }),
  myMasterProfile: () => apiRequest("/masters/me"),
  updateMasterProfile: (data) => apiRequest("/masters/me", { method: "PATCH", body: JSON.stringify(data) }),
  addMyService: (data) => apiRequest("/masters/me/services", { method: "POST", body: JSON.stringify(data) }),
  deleteMyService: (id) => apiRequest(`/masters/me/services/${id}`, { method: "DELETE" }),
  fileComplaint: (data) => apiRequest("/complaints", { method: "POST", body: JSON.stringify(data) }),
  orderFeed: () => apiRequest("/orders/feed"),
  orderOffers: (id) => apiRequest(`/orders/${id}/offers`),
  makeOffer: (id, data) => apiRequest(`/orders/${id}/offer`, { method: "POST", body: JSON.stringify(data) }),
  acceptOffer: (orderId, offerId) => apiRequest(`/orders/${orderId}/accept?offer_id=${encodeURIComponent(offerId)}`, { method: "POST" }),
  setOrderStatus: (id, status) => apiRequest(`/orders/${id}/status`, { method: "POST", body: JSON.stringify({ status }) }),
  cancelOrder: (id) => apiRequest(`/orders/${id}/cancel`, { method: "POST" }),
  leaveReview: (id, data) => apiRequest(`/orders/${id}/review`, { method: "POST", body: JSON.stringify(data) }),
  notifications: () => apiRequest("/notifications"),
  unreadCount: () => apiRequest("/notifications/unread-count"),
  readAllNotifications: () => apiRequest("/notifications/read-all", { method: "POST" }),
  publicConfig: () => apiRequest("/config"),
  mySubscription: () => apiRequest("/subscription/me"),
  checkoutSubscription: (months) => apiRequest("/subscription/me/checkout", { method: "POST", body: JSON.stringify({ months }) }),
  adminSubscriptions: () => apiRequest("/admin/subscriptions"),
  adminSubscriptionPayments: (params = {}) => apiRequest(`/admin/subscription-payments?${new URLSearchParams(params)}`),
  adminConfirmPayment: (id) => apiRequest(`/admin/subscription-payments/${id}/confirm`, { method: "POST" }),
  adminExtendSubscription: (masterId, months) => apiRequest(`/admin/subscriptions/${masterId}/extend`, { method: "POST", body: JSON.stringify({ months }) }),

  adminStats: () => apiRequest("/admin/stats"),
  adminUsers: (params = {}) => apiRequest(`/admin/users?${new URLSearchParams(params)}`),
  adminSetUserStatus: (id, status) => apiRequest(`/admin/users/${id}/status`, { method: "PATCH", body: JSON.stringify({ status }) }),
  adminMasters: (params = {}) => apiRequest(`/admin/masters?${new URLSearchParams(params)}`),
  adminVerifyMaster: (id, verified) => apiRequest(`/admin/masters/${id}/verify`, { method: "PATCH", body: JSON.stringify({ verified }) }),
  adminOrders: (params = {}) => apiRequest(`/admin/orders?${new URLSearchParams(params)}`),
  adminComplaints: (params = {}) => apiRequest(`/admin/complaints?${new URLSearchParams(params)}`),
  adminSetComplaintStatus: (id, status) => apiRequest(`/admin/complaints/${id}`, { method: "PATCH", body: JSON.stringify({ status }) }),
  adminCategories: () => apiRequest("/categories"),
  adminAddCategory: (data) => apiRequest("/admin/categories", { method: "POST", body: JSON.stringify(data) }),
  adminDeleteCategory: (id) => apiRequest(`/admin/categories/${id}`, { method: "DELETE" }),
};
