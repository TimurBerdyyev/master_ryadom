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

const CATEGORY_ICONS = {
  "Сантехника": "🚰",
  "Электрика": "⚡",
  "Строительство": "🧱",
  "Ремонт": "🛠️",
  "Уборка": "🧹",
  "Компьютеры": "💻",
  "Ремонт телефонов": "📱",
  "Автомастера": "🚗",
  "Красота": "💅",
  "Парикмахеры": "✂️",
  "Грузчики": "💪",
  "Перевозки": "🚚",
  "Ремонт бытовой техники": "🔌",
  "Другое": "✨",
};

function categoryIcon(name) {
  return CATEGORY_ICONS[name] || "🔧";
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
  return `<span class="stars">${"★".repeat(full)}${"☆".repeat(5 - full)}</span>`;
}

function formatPrice(value) {
  return `${Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 0 })} сом`;
}

function formatDate(value, withTime = false) {
  if (!value) return "";
  const d = new Date(value.endsWith("Z") || value.includes("+") ? value : value + "Z");
  const opts = { day: "numeric", month: "long" };
  if (withTime) Object.assign(opts, { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleString("ru-RU", opts);
}

function pluralize(n, one, few, many) {
  const mod10 = n % 10, mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

function waLink(phone) {
  return `https://wa.me/${String(phone).replace(/[^\d]/g, "")}`;
}

function telLink(phone) {
  return `tel:${String(phone).replace(/[^\d+]/g, "")}`;
}

const ORDER_STATUS = {
  created: { label: "Создан", cls: "" },
  searching: { label: "Ищем мастера", cls: "info" },
  offers_received: { label: "Есть предложения", cls: "warn" },
  master_selected: { label: "Мастер выбран", cls: "info" },
  master_confirmed: { label: "Мастер подтвердил", cls: "info" },
  master_en_route: { label: "Мастер в пути", cls: "info" },
  in_progress: { label: "В работе", cls: "info" },
  completed: { label: "Выполнен", cls: "ok" },
  reviewed: { label: "Завершён", cls: "ok" },
  cancelled: { label: "Отменён", cls: "bad" },
};

function statusPillHtml(status) {
  const s = ORDER_STATUS[status] || { label: status, cls: "" };
  return `<span class="status-pill ${s.cls}">${esc(s.label)}</span>`;
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
  if (typeof body.detail === "string") return body.detail;
  if (Array.isArray(body.detail)) {
    return body.detail.map(d => String(d.msg || "").replace(/^Value error, /, "")).join("; ");
  }
  return null;
}

async function apiRequest(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = errorDetail(await res.json()) || detail;
    } catch (e) {
      /* ignore */
    }
    throw new Error(detail);
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

  const res = await fetch(`${API_BASE}${path}`, { method: "POST", headers, body: formData });
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
