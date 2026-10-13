const ROOT = location.pathname.includes("/admin/") ? "../" : "";

// Opened as an app: the Android app (its WebView adds "MasterRyadomApp" to the user agent) or the site
// installed to the home screen (iPhone "На экран Домой", Android Chrome "Установить").
const IS_APP = /MasterRyadomApp/.test(navigator.userAgent)
  || window.matchMedia("(display-mode: standalone)").matches
  || window.navigator.standalone === true;
if (IS_APP) document.documentElement.classList.add("in-app");

// Installable site + offline page (sw.js). Not in the Android app: it always loads the live site.
if ("serviceWorker" in navigator && !/MasterRyadomApp/.test(navigator.userAgent) && location.protocol !== "file:") {
  window.addEventListener("load", () => navigator.serviceWorker.register(`${ROOT}sw.js`).catch(() => {}));
}

// ---------- push notifications in the Android app ----------
// The app (Capacitor) injects window.Capacitor into the site; the push plugin is native.
const PUSH = window.Capacitor && window.Capacitor.isNativePlatform && window.Capacitor.isNativePlatform()
  ? (window.Capacitor.Plugins || {}).PushNotifications : null;

if (PUSH) {
  // Tapping a notification opens its page (also when the tap started the app).
  PUSH.addListener("pushNotificationActionPerformed", (action) => {
    const link = ((action.notification || {}).data || {}).link || "";
    location.href = ROOT + safeNext(link.replace(/^\/+/, ""), "index.html");
  });
  // A notification while the app is open: show it on the page instead of the system banner.
  PUSH.addListener("pushNotificationReceived", (notification) => {
    toast(notification.title || t("nav.notifications"));
    refreshUnread();
  });
  PUSH.addListener("registration", ({ value }) => {
    try { localStorage.setItem("pushToken", value); localStorage.removeItem("pushError"); } catch (e) { /* ignore */ }
    if (getToken()) {
      api.registerDevice(value)
        .then(() => setPushError(""))
        .catch((e) => setPushError(t("push.errServer") + e.message));
    }
  });
  PUSH.addListener("registrationError", (e) => setPushError(t("push.errFirebase") + ((e && e.error) || "")));
}

// ---------- links to the site open the app (Android App Links, /.well-known/assetlinks.json) ----------
// The app starts on its home page; show the page of the link that opened it instead (same site only).
const APP_PLUGIN = PUSH ? (window.Capacitor.Plugins || {}).App : null;

function openAppLink(url) {
  try {
    const target = new URL(url);
    if (target.origin !== location.origin || target.href === location.href) return;
    location.href = target.pathname + target.search + target.hash;
  } catch (e) { /* not a URL */ }
}

if (APP_PLUGIN) {
  APP_PLUGIN.addListener("appUrlOpen", ({ url }) => openAppLink(url));
  APP_PLUGIN.getLaunchUrl().then((launch) => {
    const url = launch && launch.url;
    let seen = "";
    try { seen = sessionStorage.getItem("launchUrl") || ""; } catch (e) { /* ignore */ }
    if (!url || url === seen) return;  // handled once: don't pull the user back on every page
    try { sessionStorage.setItem("launchUrl", url); } catch (e) { /* ignore */ }
    openAppLink(url);
  }).catch(() => {});
}

// The last push problem on this phone — shown in the master's profile next to the test button.
function setPushError(text) {
  try { text ? localStorage.setItem("pushError", text) : localStorage.removeItem("pushError"); } catch (e) { /* ignore */ }
}

function pushError() {
  try { return localStorage.getItem("pushError") || ""; } catch (e) { return ""; }
}

// Masters: ask once for permission and register this phone for new-order notifications.
async function setupPush(user) {
  if (!PUSH || user.role !== "master") return;
  try {
    await PUSH.createChannel({
      id: "orders", name: t("push.channel"), description: t("push.channelText"),
      importance: 5, visibility: 1, sound: "default", vibration: true, lights: true,
    });
    let permission = await PUSH.checkPermissions();
    if (permission.receive === "prompt" || permission.receive === "prompt-with-rationale") {
      permission = await PUSH.requestPermissions();
    }
    if (permission.receive === "granted") await PUSH.register();
  } catch (e) {
    setPushError(t("push.errFirebase") + (e && e.message ? e.message : e));
  }
}

// Bottom tab bar for the app on phones: the main sections one tap away.
function renderTabbar(role) {
  if (!IS_APP) return;
  const items = role === "master"
    ? [["feed.html", "inbox", t("tab.feed")], ["orders.html", "clipboard-list", t("tab.jobs")],
       ["chats.html", "message-circle", t("tab.messages"), false, "chatBadge"], ["master-profile-edit.html", "user", t("tab.profile")]]
    : role === "admin"
    ? [["index.html", "house", t("tab.home")], ["masters.html", "search", t("tab.masters")],
       ["admin/index.html", "shield-check", t("nav.admin")]]
    : [["index.html", "house", t("tab.home")], ["masters.html", "search", t("tab.masters")],
       ["order.html", "plus", t("tab.request"), true], ["chats.html", "message-circle", t("tab.messages"), false, "chatBadge"],
       ["my-requests.html", "clipboard-list", t("tab.myRequests")]];
  const current = location.pathname.replace(/^.*?\/((admin\/)?[^/]*)$/, "$1") || "index.html";
  let bar = document.getElementById("tabbar");
  if (!bar) {
    bar = document.createElement("nav");
    bar.id = "tabbar";
    bar.className = "tabbar";
    bar.setAttribute("aria-label", t("nav.menu"));
    document.body.appendChild(bar);
  }
  bar.innerHTML = items.map(([href, iconName, label, main, badge]) => `
    <a class="tab${main ? " main" : ""}${(current || "index.html") === href ? " active" : ""}" href="${ROOT}${href}">
      <span class="tab-icon">${icon(iconName)}${badge ? `<span class="tab-badge" data-chat-badge hidden></span>` : ""}</span><span class="tab-label">${label}</span>
    </a>`).join("");
  refreshChatBadge();
}

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
    ${navLink("chats.html", t("nav.messages") + '<span class="nav-count" data-chat-badge hidden></span>', "message-circle")}
    ${navLink("my-requests.html", t("nav.myRequests"), "clipboard-list")}
    ${navLink("login.html", t("nav.forMasters"), "briefcase")}
    ${IS_APP ? "" : `<span class="nav-app-link">${navLink("app.html", t("nav.app"), "smartphone")}</span>`}
    <a class="btn small accent" href="${ROOT}order.html">${icon("plus")}${t("nav.createOrder")}</a>
  `;

  if (!getToken()) {
    nav.innerHTML = guestNav;
    renderTabbar("guest");
    syncWebPush();
    refreshChatBadge();
    return;
  }
  try {
    const user = await api.me();
    setRole(user.role);
    // Masters don't use the clients' home page: their start screen is the order feed.
    if (user.role === "master") {
      if (/(^|\/)(index\.html)?$/.test(location.pathname) && !location.pathname.includes("/admin/")) {
        location.replace(`${ROOT}feed.html`);
        return;
      }
      document.querySelectorAll("a.logo").forEach(a => { a.href = `${ROOT}feed.html`; });
    }
    const links = [];
    if (user.role === "master") {
      links.push(navLink("feed.html", t("nav.feed"), "inbox"));
      links.push(navLink("orders.html", t("nav.myJobs"), "clipboard-list"));
      links.push(navLink("chats.html", t("nav.messages") + '<span class="nav-count" data-chat-badge hidden></span>', "message-circle"));
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
    renderTabbar(user.role);
    setupPush(user);
    syncWebPush();
    refreshChatBadge();
    if (user.role === "master") {
      window.__isMaster = true;
      shareLiveLocation();
    }
  } catch (e) {
    clearToken();
    nav.innerHTML = guestNav;
    renderTabbar("guest");
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

async function logout() {
  // Stop push notifications to this phone for the account that is logging out.
  let pushToken = null;
  try { pushToken = localStorage.getItem("pushToken"); } catch (e) { /* ignore */ }
  if (PUSH && pushToken && getToken()) {
    await Promise.race([api.removeDevice(pushToken).catch(() => {}), new Promise(r => setTimeout(r, 2000))]);
  }
  clearToken();
  location.href = `${ROOT}index.html`;
}


// ---------- chat unread badge ----------
async function refreshChatBadge() {
  const badges = document.querySelectorAll("[data-chat-badge]");
  if (!badges.length) return;
  let unread = 0;
  try {
    // Clients only ask once they have chatted (they then have a key); masters always.
    let hasKey = false;
    try { hasKey = Boolean(localStorage.getItem("clientKey")); } catch (e) { /* ignore */ }
    if (getToken() || hasKey) unread = (await api.chatsUnread()).unread;
  } catch (e) {
    /* no badge */
  }
  badges.forEach(b => { b.textContent = unread > 99 ? "99+" : String(unread); b.hidden = unread === 0; });
}

// ---------- browser notifications (Web Push) ----------
const WEB_PUSH = !PUSH && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

function urlBase64ToUint8Array(base64) {
  const padded = (base64 + "=".repeat((4 - base64.length % 4) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(padded), c => c.charCodeAt(0));
}

// Asks for permission (call from a button: browsers, especially iPhone, require a tap) and subscribes.
async function enableWebPush() {
  if (!WEB_PUSH) throw new Error(t("wp.unsupported"));
  if (Notification.permission !== "granted" && (await Notification.requestPermission()) !== "granted") {
    throw new Error(t("wp.denied"));
  }
  await subscribeWebPush();
  return true;
}

async function subscribeWebPush() {
  const registration = await navigator.serviceWorker.ready;
  let subscription = await registration.pushManager.getSubscription();
  if (!subscription) {
    const { public_key } = await api.webPushKey();
    subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(public_key) });
  }
  getClientKey();  // make sure the browser has its key: clients are found by it
  await api.webPushSubscribe(subscription.toJSON());
}

// Already allowed earlier: quietly keep the subscription tied to this login / browser key.
function syncWebPush() {
  if (WEB_PUSH && Notification.permission === "granted") subscribeWebPush().catch(() => {});
}

function webPushState() {
  if (PUSH) return "app";
  if (!WEB_PUSH) {
    const ios = /iPhone|iPad|iPod/.test(navigator.userAgent);
    return ios && !IS_APP ? "ios-install" : "unsupported";
  }
  return Notification.permission;  // "granted" | "denied" | "default"
}

// A small "turn on notifications" banner for pages where an answer is awaited (chat, my requests).
function webPushBanner(container, textKey) {
  const state = webPushState();
  if (!container || state === "granted" || state === "app" || state === "unsupported") return;
  container.innerHTML = state === "ios-install"
    ? `<div class="notice">${icon("bell")}<span>${t("wp.iosInstall")} <a class="link" href="${ROOT}app.html">${t("nav.app")} →</a></span></div>`
    : state === "denied"
    ? `<div class="notice warn">${icon("bell")}<span>${t("wp.deniedHelp")}</span></div>`
    : `<div class="notice wp-banner">${icon("bell-ring")}<span style="flex:1">${t(textKey)}</span><button class="btn small" type="button">${t("wp.enable")}</button></div>`;
  const button = container.querySelector("button");
  if (button) button.addEventListener("click", async () => {
    try {
      await enableWebPush();
      container.innerHTML = `<div class="notice success">${icon("circle-check")}<span>${t("wp.enabled")}</span></div>`;
    } catch (e) {
      toast(e.message);
    }
  });
}

// ---------- masters: position on the clients' map ----------
// No background tracking: the map shows where the master last opened the app. While "Показывать меня на карте"
// is on, the position is refreshed when the app/site opens and when it comes back from the background
// (at most once every 2 minutes, so moving between pages doesn't ask the GPS each time).
async function shareLiveLocation(force = false) {
  if (!navigator.geolocation) return;
  let last = 0;
  try { last = Number(localStorage.getItem("locationSentAt") || 0); } catch (e) { /* ignore */ }
  if (!force && Date.now() - last < 120000) return;
  try {
    const me = await api.myMasterProfile();
    if (!me.location_shared && !force) return;
  } catch (e) {
    return;
  }
  navigator.geolocation.getCurrentPosition(async (pos) => {
    try {
      await api.shareLocation(pos.coords.latitude, pos.coords.longitude, pos.coords.accuracy);
      localStorage.setItem("locationSentAt", String(Date.now()));
    } catch (e) { /* ignore */ }
  }, () => {}, { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 });
}

// Back to the app after it was minimized: refresh the master's position too.
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && getToken() && window.__isMaster) shareLiveLocation();
});
