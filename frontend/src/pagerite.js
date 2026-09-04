// Fetch-navigation: swap dynamic regions (#nav, #main) instead of full
// page loads. Real <a href> links are used throughout, so this is pure
// progressive enhancement - without JS every link does a normal load.
//
// Also: scroll-reveal effects and code copy buttons. These need no
// support from the article itself and are re-applied after each swap.
import { OverlayScrollbars } from "overlayscrollbars";
import "overlayscrollbars/overlayscrollbars.css";
import { reconnectPolicy, socketSlot, watchConnecting } from "./reconnect";

(() => {
  // Overlay scrollbars: the native kind reserves a strip of layout (or
  // shifts the layout when it appears; overflow: overlay is dead), so we
  // replace it with floating ones that cover content instead. The body
  // remains the native viewport scroller — window scroll events, scrollY
  // and scroll restoration are unaffected; only the scrollbar UI is
  // custom. Theme variables are in pagerite.css.
  OverlayScrollbars(document.body, {
    scrollbars: { autoHide: "scroll" },
  });

  if (import.meta.env.DEV) {
    // In dev the base stylesheet is injected by Vite from JS (linking the
    // raw module would pull in its HMR wrapper). Theme and banner-design
    // stylesheets are plain files served by the backend (/_themes/...), so
    // the backend renders their <link>s in both dev and prod. The injected
    // base styles land at the end of <head> — after them, restore the
    // canonical order: base < theme < banner design < custom CSS (whose
    // equal-specificity :root rules — font variables — must win by order).
    import("./assets/pagerite.css").then(() => {
      for (const id of ["pagerite-theme", "pagerite-banner", "pagerite-transition", "pagerite-user"]) {
        const el = document.getElementById(id);
        if (el) document.head.append(el);
      }
    });
  }

  // --- Language override (?lang=) ---------------------------------------
  // /page?lang=fi serves a translated, indexable version (each language is
  // its own canonical). The chosen language sticks for the session of
  // clicks: the server replicates ?lang= onto the navigation links it
  // renders (nav, sidebar, cards — in-article links are content and stay
  // as authored), and pageUrl adds it to internal fetches that lack one.
  // The address bar keeps the pretty URL: the query is stripped on load
  // and never pushed into history. A full refresh or a shared link resets
  // to automatic selection (the browser's own Accept-Language — every
  // plain fetch carries it by default). See docs/localization.md.
  const langParam = new URL(location.href).searchParams.get("lang");
  if (langParam) {
    const url = new URL(location.href);
    url.searchParams.delete("lang");
    history.replaceState(history.state, "", url);
  }
  // The session language: the user's explicit pick (initial ?lang=, public
  // selector, editor dropdown) is kept in chosenLang; while the editor is
  // open its selection overrides it (swapdoc.setLangOverride), and closing
  // falls back to chosenLang. JS state only — pretty URLs, no reloads.
  // window.__pageriteLang is the pin for swapdoc.loadPlain's fetches.
  let chosenLang = langParam;
  let sessionLang = langParam;
  window.__pageriteLang = sessionLang;
  addEventListener("pagerite:session-lang", (ev) => {
    if (ev.detail?.lang) chosenLang = ev.detail.lang;
    sessionLang = ev.detail?.lang || chosenLang;
    window.__pageriteLang = sessionLang;
  });
  // An internal URL as fetched: carries the session's ?lang= unless the
  // link already pins a language of its own. With no ?lang= on the initial
  // load nothing is ever added.
  const pageUrl = (url) => {
    const u = new URL(url, location.href);
    if (sessionLang && u.origin === location.origin && !u.searchParams.has("lang")) {
      u.searchParams.set("lang", sessionLang);
    }
    return u;
  };
  // The in-memory page cache is keyed by path + query: the same pathname
  // holds different HTML for each language version.
  const rawKey = (url) => {
    const u = new URL(url, location.href);
    return u.pathname + u.search;
  };
  const cacheKey = (url) => rawKey(pageUrl(url));
  // What goes into the address bar and history: the pretty URL, no ?lang=.
  const prettyUrl = (url) => {
    const u = pageUrl(url);
    u.searchParams.delete("lang");
    return u;
  };

  // Regions every page has. #sidebar is NOT among them: it is omitted
  // entirely when the section has no sub-navigation, and handled below.
  const REGIONS = ["page-banner", "nav", "main"];
  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");
  let editorModule = null;

  // --- Auth-gated edit pens ---------------------------------------------
  // Pages render identically for everyone; the 🖊️ pens are injected by JS
  // only after we know the user has pagerite:admin access. We probe our own
  // /_api/settings endpoint: the same reverse proxy that gates /_api returns
  // 401/403 here, and a 200 means the permission is present.
  //
  // When Paskia SSO is in use (probed via /auth/api/settings), the banner
  // corner gets a plain link to /auth/ — 🔑 log in for anonymous visitors,
  // 🔐 profile when logged in. Normal navigation: Paskia does not support
  // being iframed, and history.back() returns to the page as-is (the
  // pageshow handler below re-probes auth to refresh the pens).
  let ssoAvailable = false;
  let isAdmin = false;
  let authReady = false;
  let editorMeta = null;

  // Asset URLs for the on-demand bundles. Dev renders them as
  // pagerite:* meta tags (Vite dev-server URLs); production inlines all
  // page assets and carries the on-demand URLs in a JSON script instead.
  const assets = (() => {
    const el = document.getElementById("pagerite-assets");
    if (el) return JSON.parse(el.textContent);
    const map = {};
    for (const m of document.querySelectorAll('meta[name^="pagerite:"]')) {
      map[m.name] = m.content;
    }
    return map;
  })();

  function makePen(mode, line) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.dataset.editorSrc = editorMeta.src;
    btn.dataset.editorCss = editorMeta.css || "";
    btn.dataset.editorMode = mode;
    if (line != null) {
      // Section pen on an anchored h2: opens the page editor at the
      // section's markdown source line (data-line, from the backend).
      btn.className = "edit-link edit-section";
      btn.title = "edit section";
      btn.textContent = "🖊️";
      btn.dataset.editorLine = line;
    } else if (mode === "page") {
      btn.className = "edit-link edit-page";
      btn.title = "edit page";
      btn.textContent = "🖊️";
    } else {
      btn.className = "edit-link site-edit-link";
      btn.title = "site settings";
      btn.textContent = "⚙️";
    }
    return btn;
  }

  function injectPagePen() {
    const article = document.querySelector("#main article");
    if (!article) return;
    if (!article.querySelector("button.edit-page")) {
      article.prepend(makePen("page"));
    }
    // Section pens on the anchored h2s (long articles only — the backend
    // adds data-line to those headings), for editor access mid-document.
    for (const h2 of article.querySelectorAll("h2[data-line]")) {
      if (!h2.querySelector("button.edit-section")) {
        h2.append(makePen("page", h2.dataset.line));
      }
    }
  }

  function makeAuthLink(admin) {
    const a = document.createElement("a");
    a.className = admin ? "profile-link" : "login-link";
    a.href = "/auth/";
    a.title = admin ? "profile" : "log in";
    a.textContent = admin ? "\u{1F510}" : "\u{1F511}";
    return a;
  }

  // The banner top-right corner container: the language selector (first
  // item) plus the admin pens and auth links. renderAuthUi rebuilds it from
  // scratch; the selector's state lives in the shared store, not the DOM,
  // so the langselect bundle re-mounts it into the fresh container.
  function pensContainer() {
    let pens = document.querySelector(".editor-pens");
    if (!pens) {
      const banner = document.getElementById("page-banner");
      if (!banner) return null;
      pens = document.createElement("div");
      pens.className = "editor-pens";
      banner.after(pens);
    }
    return pens;
  }

  function removePens() {
    document.querySelectorAll(".editor-pens, #main article button.edit-link")
      .forEach((el) => el.remove());
  }

  function renderAuthUi() {
    // Always start from a clean slate: if the auth probe is still running we
    // must not show any admin UI, and if it came back negative we must drop
    // pens that may have been injected while the browser cache made us look
    // authenticated.
    removePens();
    if (authReady) {
      // Editing is open for admins and, as a dev/no-proxy fallback, when no
      // Paskia SSO is detected at all.
      const canEdit = isAdmin || !ssoAvailable;
      // The analytics page is a read-only dashboard: editing pens and the side
      // panel do not apply there. Login/logout links are still useful.
      const onAnalytics = currentPath === "/_a";
      if (document.getElementById("page-banner")) {
        const pens = pensContainer();
        if (canEdit && !onAnalytics) {
          // Analytics viewer is now a normal page at /_a.
          const a = document.createElement("a");
          a.className = "edit-link analytics-link";
          a.href = "/_a";
          a.title = "analytics";
          a.textContent = "📊";
          pens.append(a);
          pens.append(makePen("site"));
        }
        if (ssoAvailable) pens.append(makeAuthLink(isAdmin));
        if (!pens.firstElementChild) pens.remove();
      }
      if (canEdit && !onAnalytics) injectPagePen();
    }
    // Re-mount the selector into the fresh container (no-op until the
    // bundle has been loaded once).
    langselectMod?.ensureMounted(document.querySelector(".editor-pens"));
  }

  async function setupAuth() {
    authReady = false;
    renderAuthUi();

    const src = assets["pagerite:editor-src"];
    if (!src) { authReady = true; renderAuthUi(); pingEntryOnce(); return; }
    editorMeta = {
      src,
      css: assets["pagerite:editor-css"],
    };

    // Detect whether Paskia SSO is available on this site.
    try {
      const ssoRes = await fetch("/auth/api/settings");
      ssoAvailable = ssoRes.ok;
    } catch {
      ssoAvailable = false;
    }

    // Check whether the current session has pagerite:admin.
    isAdmin = false;
    try {
      isAdmin = (await fetch("/_api/settings")).status === 200;
    } catch {
      // No auth proxy / dev.
    }

    if (isAdmin) {
      // Warm the cache with the editor bundle: the hashed asset is
      // immutable, so preloading costs nothing and the pens then open
      // instantly. The analytics page has no editor.
      if (currentPath !== "/_a" && !import.meta.env.DEV) {
        const preload = document.createElement("link");
        preload.rel = "modulepreload";
        preload.href = src;
        document.head.append(preload);
      }
    }

    authReady = true;
    renderAuthUi();
    placeEditPen();
    pingEntryOnce();
  }

  // Returning to the page via history back/forward may restore a cached
  // copy whose auth UI predates a login/logout — re-probe and re-render.
  addEventListener("pageshow", (ev) => {
    if (ev.persisted) setupAuth();
  });

  function runScripts(root) {
    // Scripts inserted via DOM swapping do not execute; re-create them.
    for (const old of root.querySelectorAll("script")) {
      const s = document.createElement("script");
      for (const a of old.attributes) s.setAttribute(a.name, a.value);
      s.textContent = old.textContent;
      old.replaceWith(s);
    }
  }

  // --- Scroll reveal + code block copy buttons -------------------------
  const observer = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (e.isIntersecting) {
        e.target.classList.add("in");
        observer.unobserve(e.target);
      }
    }
  }, { rootMargin: "0px 0px -8% 0px" });

  function addCopyButtons(main) {
    for (const pre of main.querySelectorAll("pre")) {
      if (pre.querySelector(".copy")) continue;
      const btn = document.createElement("button");
      btn.className = "copy";
      btn.type = "button";
      btn.textContent = "copy";
      btn.addEventListener("click", async () => {
        const code = pre.querySelector("code");
        const text = (code || pre).textContent.replace(/\n$/, "");
        // navigator.clipboard exists only in secure contexts (https or
        // localhost); viewing over plain http needs the textarea fallback.
        try {
          if (navigator.clipboard) {
            await navigator.clipboard.writeText(text);
          } else {
            const ta = document.createElement("textarea");
            ta.value = text;
            ta.style.cssText = "position:fixed;opacity:0";
            document.body.append(ta);
            ta.select();
            document.execCommand("copy");
            ta.remove();
          }
        } catch {
          btn.textContent = "failed";
          setTimeout(() => (btn.textContent = "copy"), 1500);
          return;
        }
        btn.textContent = "copied";
        btn.classList.add("copied");
        setTimeout(() => {
          btn.textContent = "copy";
          btn.classList.remove("copied");
        }, 1500);
      });
      pre.append(btn);
    }
  }

  // --- Figure lightbox (click to enlarge) --------------------------------
  // Clicking an article figure's image opens it in a full-viewport box:
  // the image as large as fits with its caption below; click or Esc
  // closes. The zoomed <img> reuses the source URL — /_f/ is immutable
  // and content-negotiated, so the large view comes from the browser
  // cache at no extra cost.
  let lightbox = null;
  function closeLightbox() {
    lightbox?.remove();
    lightbox = null;
  }
  function openLightbox(figure) {
    const img = figure.querySelector("img");
    if (!img) return;
    closeLightbox();
    lightbox = document.createElement("div");
    lightbox.id = "lightbox";
    const big = document.createElement("img");
    big.src = img.currentSrc || img.src;
    big.alt = img.alt;
    lightbox.append(big);
    const cap = figure.querySelector("figcaption");
    if (cap) {
      const c = document.createElement("div");
      c.className = "caption";
      c.textContent = cap.textContent;
      lightbox.append(c);
    }
    lightbox.addEventListener("click", closeLightbox);
    document.body.append(lightbox);
  }
  // Any key dismisses the lightbox — Esc included, and the rest would
  // only scroll the page behind it anyway.
  addEventListener("keydown", () => closeLightbox());

  // Tuck the article edit pen at the end of the first h1 (which may come
  // from the markdown itself). Re-runs when the editor replaces the
  // previewed article, since that wipes elements inside it.
  function placeEditPen() {
    const article = document.querySelector("#main article");
    const btn = article?.querySelector("button.edit-page");
    const h1 = article?.querySelector("h1");
    if (btn && h1 && btn.parentElement !== h1) h1.append(btn);
  }

  addEventListener("pagerite:preview", () => {
    // The editors' in-place swaps (SiteEditor.loadPlain) replace #main
    // without applyEffects, discarding the injected pens; re-add them
    // before tucking the article pen into the h1. Without this a freshly
    // created page has no pen for commitPending's handover click.
    renderAuthUi();
    placeEditPen();
  });

  function applyEffects() {
    (window.requestIdleCallback || setTimeout)(preload);
    const main = document.getElementById("main");
    addCopyButtons(main);
    // Fetch-navigation swaps #main, discarding the article pen and corner
    // buttons; re-add whichever auth UI is appropriate for this session.
    renderAuthUi();
    placeEditPen();
    fitNav();
    if (reduceMotion.matches) return;
    for (const el of main.querySelectorAll(
      "h2, h3, figure, img, pre, blockquote, table, dl, .task-list-item",
    )) {
      if (!el.classList.contains("reveal")) {
        el.classList.add("reveal");
        observer.observe(el);
      }
    }
  }

  // --- Page cache / preloading ------------------------------------------
  // Articles are deliberately NOT HTTP-cacheable, so speed comes from an
  // in-memory cache instead: at load (and after each swap) every visible
  // internal link is fetched exactly once, and navigation is served from
  // memory with no fetch at all. Editor re-renders (swapdoc.loadPlain)
  // announce their fresh copies via pagerite:page-fetched, keeping the
  // cache in sync after edits. The current page is NOT preloaded: we just
  // received it as the document (re-fetching would be redundant, and
  // browser heuristics may send it without if-none-match, defeating the
  // conditional request); it enters the cache when navigated to.
  const pageCache = new Map(); // rawKey/cacheKey(url) -> HTML text
  addEventListener("pagerite:page-fetched", (ev) => {
    // Key by the URL as announced, exactly as the editor fetched it: a
    // copy pinned to a language (?lang=) caches under its own key, where
    // navigation with the same session language finds it.
    pageCache.set(rawKey(ev.detail.url), ev.detail.html);
    // Editor-driven swaps don't go through load(): re-evaluate the
    // language selector from the fresh copy too.
    mountLangselect(new DOMParser().parseFromString(ev.detail.html, "text/html"));
  });

  // Editors mutate site-wide state (theme, structure, headings, banners),
  // which can change the rendered HTML of every cached page. Drop the whole
  // cache so stale prefetches are never served; the current page is re-fetched
  // by the editor's own loadPlain and re-cached afterwards. Re-preloading is
  // deferred until the editor panel closes to avoid hammering the server.
  addEventListener("pagerite:drop-page-cache", () => {
    pageCache.clear();
  });

  // When the editor panel closes, warm the cache again for the visible links
  // on the (now final) page so subsequent navigation stays instant.
  addEventListener("pagerite:preload-pages", () => {
    preload();
  });

  function preload() {
    const urls = new Map(); // cache key -> URL, deduped (hashes collapse)
    for (const a of document.querySelectorAll(
      '#nav a[href^="/"], #sidebar a[href^="/"], #main a[href^="/"]',
    )) {
      const u = pageUrl(a.href);
      urls.set(rawKey(u), u);
    }
    for (const [key, u] of urls) {
      if (pageCache.has(key)) continue;
      // x-pagerite-preload: idle cache warm-up, not a page view — the
      // server excludes these GETs from analytics (the navigation message
      // sent on actual navigation does the counting).
      fetch(u, { headers: { "x-pagerite-preload": "1" } })
        .then((r) => (r.ok && (r.headers.get("content-type") || "").includes("text/html")
          ? r.text() : ""))
        .then((html) => { if (html) pageCache.set(key, html); })
        .catch(() => {});
    }
  }

  // The path we are currently showing. location.pathname is unusable for
  // this on popstate (it has already changed to the target); the editors
  // signal their replaceState navigation with pagerite:preview.
  let currentPath = location.pathname;
  addEventListener("pagerite:preview", () => {
    currentPath = location.pathname;
  });

  // History position marker: every entry we create carries an incrementing
  // idx so popstate can tell forward navigation from back (needed for the
  // mirrored cube transition). replaceState calls below must preserve this
  // state object instead of passing null.
  let historyIdx = history.state?.idx ?? 0;
  history.replaceState({ idx: historyIdx }, "");

  // --- Banner parallax ----------------------------------------------------
  // The banner artwork stays windowed in place while its contents drift
  // against the scroll. The --pry scroll parameter is also available to
  // themes for their own effects (e.g. the purple sun rising faster than
  // the drift). Event-driven only: perfectly still when the page is idle.
  if (!reduceMotion.matches) {
    // rAF loop that eases the value toward the live scroll position. Reading
    // scrollY every frame (rather than only on scroll events) also picks up
    // the in-between positions of Chrome/macOS momentum scrolling, whose
    // scroll events fire late and coarsely. The loop idles once settled.
    let value = Math.min(scrollY * 0.1, 30);
    let running = false;
    const drift = () => {
      const target = Math.min(scrollY * 0.1, 30);
      value += (target - value) * 0.12;
      if (Math.abs(target - value) < 0.05) {
        value = target;
        running = false;
      }
      document.documentElement.style.setProperty("--pry", `${value}px`);
      if (running) requestAnimationFrame(drift);
    };
    addEventListener("scroll", () => {
      if (!running) {
        running = true;
        requestAnimationFrame(drift);
      }
    }, { passive: true });
  }

  // --- Section hash -------------------------------------------------------
  // Reflect the section being read in the location hash: the last h1/h2
  // above the middle of the viewport is current, even when already
  // scrolled out of view. Above the first tagged heading the hash is
  // removed — including at the very top of the document, where an early
  // heading may sit in the top half. Pages too short to scroll never get
  // a hash, and articles with few headings have no ids at all (the
  // backend only anchors h1/h2 in bodies of 3+ such headings).
  // replaceState keeps this out of history; fetch-navigation already
  // handles anchor scrolling itself.
  let hashQueued = false;
  addEventListener("scroll", () => {
    if (hashQueued) return;
    hashQueued = true;
    requestAnimationFrame(() => {
      hashQueued = false;
      const mid = innerHeight / 2;
      let current = null;
      for (const h of document.querySelectorAll("article :is(h1, h2)[id]")) {
        if (h.getBoundingClientRect().top < mid) current = h;
        else break;
      }
      const scrollable = document.documentElement.scrollHeight > innerHeight;
      const want = !scrollable || scrollY === 0 || !current ? "" : `#${current.id}`;
      if (want !== location.hash) {
        history.replaceState(history.state, "", want || location.pathname + location.search);
      }
    });
  }, { passive: true });

  // --- Analytics over WebSocket ------------------------------------------
  // One /_ws connection follows the whole browsing session: the initial page
  // load (starts the visit — the server counts nothing from the document GET
  // alone), internal fetch-navigations, external https exits, and frequent
  // active reading-time updates. Messages are JSON text frames matching the
  // server's msgspec Ping struct: {fr?, to?, read?, hide?} — falsy fields
  // are omitted. ``read`` is the active time (ms) accumulated on ``fr`` since
  // the last report; reading time pauses after 1 minute of inactivity and
  // resumes on the next mouse/touch/scroll/keyboard event. While the user is
  // active, accumulated reading time is flushed every few seconds, so a
  // disconnect simply leaves the last reported time on the server — no close
  // beacon is needed. After 5 minutes without any activity the client closes
  // the channel itself (a sleeping tab would lose it anyway); the next
  // activity reconnects and the server sees a new session.
  // Excluded: back/forward (popstate never reports), everything while the
  // editor is open (body.editing — admin noise, not visits), and
  // navigations TO the analytics page (/_a — admin machinery). Navigations
  // AWAY from /_a must report: load() already fetched the target page
  // without the preload header, and without the message that GET would flush
  // to the crawler list.
  // Admins (when SSO is actually in use — with no auth proxy "admin" is
  // everyone's state) report normally but with hide: the server then flags
  // the client record, scrubbing everything it ever did from the statistics,
  // so admins never show up as visits or crawlers.
  // See docs/analytics.md.

  // The activity WebSocket. Messages sent before the connection opens are
  // queued (the queue keeps the interim activity). Reconnects are driven by
  // user activity only — never by timers while the page sits idle — with an
  // exponential falloff between attempts so a failing endpoint cannot make
  // us hammer the server (or trip its security limits). After a longer
  // stretch without any activity we close the socket proactively: the user
  // has moved on and left the tab open (a sleeping browser tab would lose
  // the connection anyway), so the next activity reconnects and registers
  // as a fresh session. Analytics must never break navigation: every send
  // is wrapped, and a server without the endpoint just leaves the socket
  // failing in the background.
  let ws = null;
  const wsQueue = [];
  const wsPolicy = reconnectPolicy({ min: 1000 });
  // The first attempt is staggered too: page load opens several sockets at
  // once (Vite's HMR socket, the editors), and the burst trips the browser's
  // WebSocket throttling (sockets then sit "pending" for minutes).
  let wsNotBefore = Date.now() + socketSlot();
  let wsWatchdog = null;

  function activityWs() {
    if (ws || Date.now() < wsNotBefore) return;
    const url = new URL("/_ws", location.href);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    try {
      ws = new WebSocket(url);
    } catch {
      return;
    }
    clearTimeout(wsWatchdog);
    wsWatchdog = watchConnecting(ws, "activity");
    ws.onopen = () => {
      wsPolicy.opened();
      for (const msg of wsQueue.splice(0)) ws.send(JSON.stringify(msg));
    };
    ws.onclose = () => {
      ws = null;
      // No timer here: the next user activity retries, after the backoff.
      wsNotBefore = Date.now() + wsPolicy.closed();
    };
    ws.onerror = () => ws.close();
  }

  function report(msg) {
    if (document.body.classList.contains("editing")) return;
    if (msg.to === "/_a") return;
    if (ssoAvailable && isAdmin) msg.hide = true;
    activityWs();
    if (ws?.readyState === WebSocket.OPEN) {
      try {
        ws.send(JSON.stringify(msg));
        return;
      } catch { /* fall through to queueing */ }
    }
    wsQueue.push(msg);
  }

  function ping({ to, fr = currentPath, read = 0 } = {}) {
    // Reading-time updates from the analytics page itself are not tracked
    // (/_a is admin machinery; the server would reject the path anyway).
    if (!to && currentPath === "/_a") return;
    const msg = {};
    if (fr) msg.fr = fr;
    if (to) msg.to = to;
    const secs = Math.round(read / 1000);
    if (secs > 0) msg.read = secs;
    if (!msg.to && !msg.read) return;
    report(msg);
  }

  // Active reading time for the current page. The clock stops after 1 minute
  // without activity and restarts on the next mouse/touch/scroll/keyboard
  // event. Every READ_FLUSH_MS of accumulated activity is reported. After
  // IDLE_MS with no activity at all, the remaining read time is flushed and
  // the WebSocket is closed: the user has moved on, and the next activity
  // reconnects as a new session.
  const INACTIVE_MS = 60_000;
  const READ_FLUSH_MS = 5_000;
  const IDLE_MS = 5 * 60_000;
  let readStart = performance.now();
  let readElapsed = 0;
  let reading = true;
  let readInactivityTimer = null;
  let idleTimer = null;

  function markReadActivity() {
    if (!reading) {
      reading = true;
      readStart = performance.now();
    }
    clearTimeout(readInactivityTimer);
    readInactivityTimer = setTimeout(() => {
      if (reading) {
        readElapsed += performance.now() - readStart;
        reading = false;
      }
    }, INACTIVE_MS);
    // Any activity is a sign of life: (re)connect the channel if it was
    // dropped or idle-closed (not while editing — admin noise), and push
    // the idle disconnect forward.
    if (!document.body.classList.contains("editing")) activityWs();
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
      if (ws) {
        // Flush what is left unsent, then hang up. report() would try to
        // reconnect a dead socket, which is exactly what we avoid here.
        const left = takeReadTime();
        if (Math.round(left / 1000) > 0) ping({ read: left });
        ws.close();
      }
    }, IDLE_MS);
    // Frequently update the article read time on the server.
    if (readElapsed + performance.now() - readStart >= READ_FLUSH_MS) {
      ping({ read: takeReadTime() });
    }
  }

  function takeReadTime() {
    if (reading) {
      readElapsed += performance.now() - readStart;
      readStart = performance.now();
    }
    const ms = Math.max(0, Math.round(readElapsed));
    readElapsed = 0;
    return ms;
  }

  function resetReadTime() {
    readElapsed = 0;
    reading = true;
    readStart = performance.now();
    clearTimeout(readInactivityTimer);
  }

  for (const ev of ["mousemove", "mousedown", "touchstart", "touchmove", "scroll", "keydown"]) {
    addEventListener(ev, markReadActivity, { passive: true });
  }

  // The initial page load reports too — it is what starts the visit and
  // counts the entry page view (the document GET alone records nothing).
  // It carries only ``to``: the server attributes the entry to the referer
  // it saw on the document GET (unavailable to JS once loaded), and an
  // ``fr`` equal to ``to`` would log a bogus self-transition when a
  // session already exists (e.g. a second tab).
  // Sent once per load, after the auth probes so the admin gate applies;
  // the pageshow re-probe must not report again. Reloads are not visits:
  // reporting them would double-count the view and log a self-transition.
  let entryPinged = false;
  function pingEntryOnce() {
    if (entryPinged) return;
    entryPinged = true;
    const nav = performance.getEntriesByType?.("navigation")[0];
    if (nav ? nav.type === "reload" : performance.navigation?.type === 1) return;
    ping({ to: currentPath, fr: "" });
  }

  // --- Analytics page mount/unmount --------------------------------------
  // The analytics page is a normal page whose body is rendered by the server
  // but whose content is a Vue app. In dev the entry module is imported from
  // the Vite dev server on demand; in production it is inlined into the /_a
  // page as script#pagerite-js-analytics, which a fetch-navigation swap does
  // not execute — re-create the element so the fresh module auto-mounts on
  // #analytics-app (see analytics-main.js). The module exposes its unmount
  // as window.__pageriteAnalyticsUnmount.
  function teardownAnalytics() {
    // Remove even the server-rendered script element so a later return to
    // /_a re-mounts from a fresh copy (the module has torn itself down).
    document.getElementById("pagerite-js-analytics")?.remove();
    window.__pageriteAnalyticsUnmount?.();
    window.__pageriteAnalyticsUnmount = null;
  }

  async function mountAnalytics(doc) {
    if (!doc.getElementById("analytics-app")) return;
    // Already mounted: on a full /_a load the inline script has run.
    if (document.getElementById("pagerite-js-analytics")) return;
    const inline = doc.getElementById("pagerite-js-analytics");
    if (inline) {
      const s = document.createElement("script");
      for (const a of inline.attributes) s.setAttribute(a.name, a.value);
      s.textContent = inline.textContent;
      document.body.append(s);
      return;
    }
    try {
      // Dev: the cached module auto-mounts only on its first evaluation,
      // so call mount() explicitly for repeat visits (it no-ops when the
      // app is already up).
      const mod = await import(/* @vite-ignore */ assets["pagerite:analytics-src"]);
      const container = document.getElementById("analytics-app");
      if (container) mod.mount(container);
    } catch (e) {
      console.error("analytics mount failed:", e);
    }
  }

  // --- Public language selector ------------------------------------------
  // Pages translated into more than one language advertise it via hreflang
  // alternates (x-default + one link per language). Those pages get the
  // editors' flag dropdown as the first item of the corner container; its
  // bundle (Vue + the flag SVG set) loads on demand. Re-evaluated from the
  // fresh document on every swap (the head's own alternates stay stale).
  let langselectMod = null;
  async function mountLangselect(doc) {
    const links = [...doc.head.querySelectorAll('link[rel="alternate"][hreflang]')];
    const dflt = links.find((l) => l.hreflang === "x-default");
    const langs = links.filter((l) => l.hreflang && l.hreflang !== "x-default");
    if (!dflt || langs.length <= 1) return langselectMod?.hide();
    try {
      langselectMod ??= await import(/* @vite-ignore */ assets["pagerite:langselect-src"]);
      for (const css of (assets["pagerite:langselect-css"] || "").split(",")) {
        if (css && !document.querySelector(`link[href="${css}"]`)) {
          const link = document.createElement("link");
          link.rel = "stylesheet";
          link.href = css;
          link.dataset.pagerite = "langselect-css";
          document.head.append(link);
        }
      }
      langselectMod.setLanguages(
        // The original's alternate is the plain URL — x-default's href —
        // which also marks it as the primary option.
        langs.map((l) => ({ tag: l.hreflang, href: l.href, primary: l.href === dflt.href })),
        doc.documentElement.lang,
      );
      langselectMod.ensureMounted(pensContainer());
    } catch (e) {
      console.error("language selector mount failed:", e);
    }
  }

  // The selector's pick (LangSelector dispatches this): make it the
  // session language and swap the page in place. With the editor open the
  // pick already landed in the shared store — the editor's watch re-renders
  // the page itself, so there is nothing to do here.
  addEventListener("pagerite:set-session-lang", async (ev) => {
    const tag = ev.detail?.lang;
    if (!tag || tag === sessionLang) return;
    if (document.body.classList.contains("editing")) return;
    chosenLang = sessionLang = tag;
    window.__pageriteLang = tag;
    const y = scrollY; // a language switch is not a navigation: keep scroll
    await load(currentPath, false);
    scrollTo(0, y);
  });

  // --- Fetch navigation ------------------------------------------------
  async function load(url, push = true, back = false) {
    // Navigating with the editor open closes it; unsaved edits are lost
    // (the region swap discards the previewed changes anyway). Cache must be
    // bypassed for this navigation because the editor may have invalidated
    // the prefetched copies of other pages.
    const editing = document.body.classList.contains("editing");
    if (editing) {
      editorModule?.then((m) => m.closeEditor());
    }
    teardownAnalytics();
    let doc;
    let finalUrl = url;
    const cached = !editing && pageCache.get(cacheKey(url));
    if (cached) {
      doc = new DOMParser().parseFromString(cached, "text/html");
    } else {
      try {
        const res = await fetch(pageUrl(url));
        const type = res.headers.get("content-type") || "";
        if (!res.ok || !type.includes("text/html")) throw new Error("not a page");
        // Reflect any redirect the server issued.
        if (res.redirected) finalUrl = res.url;
        const html = await res.text();
        // Populate the cache too, so returning here (back/forward, or a
        // self-link in the nav) is served from memory.
        pageCache.set(cacheKey(finalUrl), html);
        doc = new DOMParser().parseFromString(html, "text/html");
      } catch {
        location.href = pageUrl(url); // fall back to a normal navigation
        return false;
      }
    }
    if (REGIONS.some((id) => !doc.getElementById(id))) {
      location.href = pageUrl(url);
      return false;
    }
    const doit = () => {
      for (const id of REGIONS) {
        const el = document.getElementById(id);
        el.replaceWith(document.importNode(doc.getElementById(id), true));
      }
      // #sidebar is omitted entirely when the section has no
      // sub-navigation, so it may be absent on either side of the swap:
      // replace, insert (as #main's preceding sibling), or remove.
      const oldSidebar = document.getElementById("sidebar");
      const newSidebar = doc.getElementById("sidebar");
      if (oldSidebar && newSidebar) {
        oldSidebar.replaceWith(document.importNode(newSidebar, true));
      } else if (newSidebar) {
        document.getElementById("main").before(document.importNode(newSidebar, true));
      } else if (oldSidebar) {
        oldSidebar.remove();
      }
      // Stylesheets live in <head> with stable ids — links in dev, inline
      // <style> elements in production — and must follow the swap: the
      // analytics sheet exists on /_a only, and theme/banner/custom CSS
      // may have changed since this page was loaded. Diff by id, keeping
      // the fresh document's order; unchanged sheets keep their elements
      // so their @keyframes are never torn down. Editor-injected sheets
      // (data-pagerite, no id) and Vite's dev styles (no id) are left
      // alone. Mirrors the head sync in swapdoc.js.
      const sel = 'link[rel="stylesheet"][id], style[id]';
      const fresh = [...doc.head.querySelectorAll(sel)];
      const freshIds = new Set(fresh.map((el) => el.id));
      for (const el of [...document.head.querySelectorAll(sel)]) {
        if (!freshIds.has(el.id)) el.remove();
      }
      let anchor = null;
      for (const el of fresh) {
        const cur = document.getElementById(el.id);
        if (cur && cur.outerHTML === el.outerHTML) {
          anchor = cur;
          continue;
        }
        const imported = document.importNode(el, true);
        if (cur) cur.replaceWith(imported);
        else if (anchor) anchor.after(imported);
        else {
          const base = document.getElementById("pagerite-base");
          if (base) base.after(imported);
          else document.head.append(imported);
        }
        anchor = imported;
      }
      // Custom CSS must stay last: equal-specificity :root rules (font
      // variables) are decided by order, and in dev Vite injects the base
      // stylesheet after the server-rendered tag.
      const userStyle = document.getElementById("pagerite-user");
      if (userStyle) document.head.appendChild(userStyle);
      // The served language rides on <html> (lang + dir, rtl for e.g.
      // Arabic) — follow the swapped page (the editor panel carries its
      // own lang="en" dir="ltr", so it is unaffected).
      document.documentElement.lang = doc.documentElement.lang;
      document.documentElement.dir = doc.documentElement.dir;
      document.title = doc.title;
      // Banners may contain scripts (canvas etc.), content pages may too.
      runScripts(document.getElementById("page-banner"));
      runScripts(document.getElementById("main"));
      applyEffects();
      mountAnalytics(doc);
      mountLangselect(doc);
    };
    // Rotating cube page transition (styles injected as #pagerite-transition
    // from the selected design's transition.css, e.g. themes/cube/);
    // mirrored when navigating back through history. Navigation within the
    // same top-level section crossfades instead, in either direction.
    if (document.startViewTransition && !reduceMotion.matches) {
      const seg = (u) => new URL(u, location.href).pathname.split("/")[1];
      const fade = seg(finalUrl) === seg(currentPath);
      const root = document.documentElement.classList;
      root.toggle("nav-fade", fade);
      root.toggle("nav-back", back && !fade);
      document.startViewTransition(doit).finished.finally(() => {
        root.remove("nav-fade", "nav-back");
      });
    } else {
      doit();
    }
    currentPath = new URL(finalUrl, location.href).pathname;
    if (push) history.pushState({ idx: ++historyIdx }, "", prettyUrl(finalUrl));
    // The open editor follows the URL: retarget the per-page tabs to the
    // navigated-to page (unsaved text of the previous page is discarded —
    // the article it previewed into is gone).
    if (document.body.classList.contains("editing")) {
      const p = currentPath.replace(/^\/+|\/+$/g, "");
      dispatchEvent(new CustomEvent("pagerite:switch-editor", { detail: { path: p } }));
    }
    // Cross-page anchor links scroll to the section after the swap (the
    // browser only does this itself on full page loads).
    const hash = new URL(finalUrl, location.href).hash;
    const target = hash && document.getElementById(hash.slice(1));
    if (target) target.scrollIntoView();
    else scrollTo(0, 0);
    return true;
  }

  addEventListener("click", (ev) => {
    if (ev.defaultPrevented || ev.button !== 0
        || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
    // Edit buttons toggle the tabbed editor panel docked on this page: load
    // the Vue app on demand (with any extra styles) and mount it in place.
    // Clicking the pen of the already-open tab closes the shell; clicking
    // another pen switches the shell to that tab.
    const editBtn = ev.target.closest("button.edit-link");
    if (editBtn && editBtn.dataset.editorSrc) {
      ev.preventDefault();
      const mode = editBtn.dataset.editorMode || "page";
      const line = editBtn.dataset.editorLine;
      // A section pen remembers its markdown source line: the page editor
      // opens (or jumps, when already open) scrolled to that section.
      // Clicking the plain page pen of the open tab closes the shell.
      if (line != null) window.__pageriteEditLine = +line;
      else delete window.__pageriteEditLine;
      if (document.body.classList.contains("editing")
          && document.body.dataset.editorMode === mode) {
        if (line != null) {
          dispatchEvent(new CustomEvent("pagerite:edit-section"));
        } else {
          editorModule?.then((m) => m.closeEditor());
        }
        return;
      }
      for (const css of (editBtn.dataset.editorCss || "").split(",")) {
        if (css && !document.querySelector(`link[href="${css}"]`)) {
          const link = document.createElement("link");
          link.rel = "stylesheet";
          link.href = css;
          link.dataset.pagerite = "editor-css";
          document.head.append(link);
        }
      }
      const path = location.pathname.replace(/^\/+|\/+$/g, "");
      editorModule = import(/* @vite-ignore */ editBtn.dataset.editorSrc);
      editorModule
        .then((m) => m.openEditor(path, { mode }))
        .catch((e) => console.error("editor load failed:", e));
      return;
    }
    // Article figure images enlarge into the lightbox.
    const fig = ev.target.closest("#main article figure");
    if (fig && ev.target.closest("img")) {
      ev.preventDefault();
      openLightbox(fig);
      return;
    }
    const a = ev.target.closest("a[href]");
    if (!a || a.target || a.hasAttribute("download")) return;
    const url = new URL(a.href, location.href);
    if (url.origin !== location.origin) {
      // External link: the browser navigates; record the full https URL so
      // different links to the same domain stay distinct in analytics.
      if (url.protocol === "https:") {
        ping({ to: url.href, read: takeReadTime() });
      }
      return;
    }
    // Same-page anchor links (footnotes etc.): let the browser handle them
    if (url.pathname === location.pathname && url.hash) return;
    // The first in-body h1 self-links with href="": scroll to the top and
    // drop the section hash — not a navigation, no analytics ping.
    if (a.getAttribute("href") === "" && url.pathname === location.pathname) {
      ev.preventDefault();
      history.replaceState(history.state, "", location.pathname + location.search);
      scrollTo({ top: 0, behavior: reduceMotion.matches ? "instant" : "smooth" });
      return;
    }
    // Machinery and auth endpoints are never fetch-navigated, except the
    // public analytics viewer page at /_a.
    if ((url.pathname.startsWith("/_") && url.pathname !== "/_a")
        || url.pathname.startsWith("/auth")) return;
    ev.preventDefault();
    // Capture the source now: load() updates currentPath before pinging.
    const from = currentPath;
    load(url).then((ok) => {
      if (!ok) return;
      ping({ to: url.pathname, fr: from, read: takeReadTime() });
      resetReadTime();
    });
  });

  addEventListener("popstate", (ev) => {
    // Hash-only history entries are not navigations.
    if (location.pathname === currentPath) return;
    // Direction from the entry idx: forward navigation animates like an
    // ordinary navigation; only actually going back mirrors the cube.
    const target = ev.state?.idx ?? historyIdx - 1;
    const back = target < historyIdx;
    historyIdx = target;
    load(location.href, false, back);
  });

  // --- Task-list checkboxes ------------------------------------------------
  // Checkboxes in the rendered article are live: toggling them edits the
  // Markdown source. If the page editor is open, its CodeMirror document is
  // updated directly; otherwise the server copy is toggled and saved.
  async function toggleTask(checkbox, index) {
    const editor = window.__pageritePageEditor;
    const pagePath = editor ? editor.path() : currentPath;
    const path = pagePath.replace(/^\/+|\/+$/g, "");
    const originalChecked = !checkbox.checked;
    try {
      const body = { path, index };
      if (editor) body.markdown = editor.getMarkdown();
      const res = await fetch("/_api/toggle-task", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || res.statusText);
      }
      const { markdown } = await res.json();
      if (editor) editor.setMarkdown(markdown);
    } catch {
      checkbox.checked = originalChecked;
    }
  }

  addEventListener("change", (ev) => {
    const checkbox = ev.target.closest(".task-list-item-checkbox");
    if (!checkbox) return;
    const index = Number(checkbox.dataset.taskIndex);
    if (Number.isNaN(index)) return;
    toggleTask(checkbox, index);
  });

  // --- Brand shrink-to-fit ------------------------------------------------
  // The themed brand size is the maximum: when the text is long or the
  // viewport narrow, reduce the font size so the brand always fits the
  // banner on one line. Re-run on resize, webfont load, and brand text
  // edits (the site editor previews them live).
  const brand = document.getElementById("brand");
  if (brand) {
    const fit = () => {
      brand.style.fontSize = ""; // restore the themed size
      const avail = brand.clientWidth;
      const need = brand.scrollWidth;
      if (need > avail && need > 0) {
        brand.style.fontSize =
          `${parseFloat(getComputedStyle(brand).fontSize) * avail / need}px`;
      }
    };
    new ResizeObserver(fit).observe(brand);
    new MutationObserver(fit).observe(brand, {
      childList: true,
      characterData: true,
      subtree: true,
    });
    document.fonts?.ready.then(fit);
    fit();
  }

  // --- Nav condense-to-fit -------------------------------------------------
  // The top nav stays on one row even on too-narrow screens: first the link
  // gaps shrink, then the nav's side padding, and only in extreme cases the
  // font size. #nav is replaced on fetch-navigation swaps, so this re-runs
  // from applyEffects (fresh elements each time); CSS keeps flex-wrap: wrap
  // as the no-JS fallback.
  function fitNav() {
    const nav = document.getElementById("nav");
    const ul = nav?.querySelector("ul");
    if (!ul) return;
    // Restore the themed defaults before measuring.
    nav.style.fontSize = "";
    nav.style.paddingInline = "";
    ul.style.columnGap = "";
    ul.style.flexWrap = "nowrap";
    const overflow = () => ul.scrollWidth - ul.clientWidth;
    if (overflow() <= 0) return;
    // 1) shrink the gaps between items (down to a fifth of the themed gap)
    const gap = parseFloat(getComputedStyle(ul).columnGap) || 0;
    const joints = Math.max(ul.children.length - 1, 1);
    if (gap > 0) {
      ul.style.columnGap = `${Math.max(0.2 * gap, gap - overflow() / joints)}px`;
    }
    // 2) shrink the nav's side padding (down to 0.4x)
    if (overflow() > 0) {
      const pad = parseFloat(getComputedStyle(nav).paddingInlineStart) || 0;
      nav.style.paddingInline = `${Math.max(0.4 * pad, pad - overflow() / 2)}px`;
    }
    // 3) shrink the font to fit what remains
    if (overflow() > 0) {
      const fs = parseFloat(getComputedStyle(nav).fontSize);
      nav.style.fontSize = `${fs * ul.clientWidth / ul.scrollWidth}px`;
    }
  }

  addEventListener("resize", fitNav);
  document.fonts?.ready.then(fitNav);

  setupAuth();
  applyEffects();
  mountAnalytics(document);
  mountLangselect(document);
})();
