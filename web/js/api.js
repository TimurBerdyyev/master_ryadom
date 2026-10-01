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

function getToken() {
  return localStorage.getItem("token");
}

function setToken(token) {
  localStorage.setItem("token", token);
}

function clearToken() {
  localStorage.removeItem("token");
}

async function apiRequest(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
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
      const body = await res.json();
      detail = body.detail || detail;
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
