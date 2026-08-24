// Fetch-navigation: swap dynamic regions (#nav, #main) instead of full
// page loads. Real <a href> links are used throughout, so this is pure
// progressive enhancement - without JS every link does a normal load.
//
// Also: scroll-reveal effects and code copy buttons. These need no
// support from the article itself and are re-applied after each swap.
import { OverlayScrollbars } from "overlayscrollbars";
import "overlayscrollbars/overlayscrollbars.css";

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
      for (const id of ["pagerite-theme", "pagerite-banner", "pagerite-user"]) {
        const el = document.getElementById(id);
        if (el) document.head.append(el);
      }
    });
  }

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

  function makePen(mode) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.dataset.editorSrc = editorMeta.src;
    btn.dataset.editorCss = editorMeta.css || "";
    btn.dataset.editorMode = mode;
    if (mode === "page") {
      btn.className = "edit-link";
      btn.title = "edit page";
      btn.textContent = "🖊️";
    } else if (mode === "banner") {
      btn.className = "edit-link";
      btn.title = "edit banner";
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
    if (article && !article.querySelector("button.edit-link")) {
      article.prepend(makePen("page"));
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

  function renderAuthUi() {
    // Editing is open for admins and, as a dev/no-proxy fallback, when no
    // Paskia SSO is detected at all.
    const canEdit = isAdmin || !ssoAvailable;
    // The analytics page is a read-only dashboard: editing pens and the side
    // panel do not apply there. Login/logout links are still useful.
    const onAnalytics = currentPath === "/_a";
    const banner = document.getElementById("page-banner");
    if (banner) {
      const old = banner.parentElement.querySelector(".editor-pens");
      if (old) old.remove();
      const pens = document.createElement("div");
      pens.className = "editor-pens";
      if (canEdit && !onAnalytics) {
        pens.append(makePen("banner"));
        pens.append(makePen("site"));
        // Analytics viewer is now a normal page at /_a.
        const a = document.createElement("a");
        a.className = "edit-link analytics-link";
        a.href = "/_a";
        a.title = "analytics";
        a.textContent = "📊";
        pens.append(a);
      }
      if (ssoAvailable) pens.append(makeAuthLink(isAdmin));
      banner.after(pens);
    }
    if (canEdit && !onAnalytics) injectPagePen();
  }

  async function setupAuth() {
    const src = assets["pagerite:editor-src"];
    if (!src) { pingEntryOnce(); return; }
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
      // Teach the backend the site's public origin (used for absolute
      // social/canonical URLs): unlike request headers, location.origin
      // reflects the real scheme and host even behind reverse proxies.
      fetch("/_api/site-url", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ url: location.origin }),
      }).catch(() => {});
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

    renderAuthUi();
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
        await navigator.clipboard.writeText(
          (code || pre).textContent.replace(/\n$/, ""),
        );
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

  // Tuck the article edit pen at the end of the first h1 (which may come
  // from the markdown itself). Re-runs when the editor replaces the
  // previewed body, since that wipes elements inside it.
  function placeEditPen() {
    const article = document.querySelector("#main article");
    const btn = article?.querySelector("button.edit-link");
    // First visible h1: the title h1 may be display:none when the
    // markdown owns its heading (editor preview state).
    const h1 = [...(article?.querySelectorAll("h1") || [])]
      .find((h) => h.offsetParent !== null);
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
    // Multi-column layout only when there is enough text to justify it.
    // Split the body into columned segments: h1s, h2s and wide figures are
    // full-width separators and never go inside columns.
    const article = main.querySelector("article");
    if (article) {
      const body = article.querySelector(".body");
      article.classList.toggle(
        "multicol",
        !!body && body.textContent.trim().length > 1800,
      );
      if (body && article.classList.contains("multicol")
          && !body.querySelector(".colseg")) {
        // h1s, h2s and anything holding a wide image are full-width
        // separators
        const isSeparator = (el) =>
          el.tagName === "H1" || el.tagName === "H2"
          || el.querySelector("img.wide") !== null;
        let seg = null;
        for (const el of [...body.children]) {
          if (isSeparator(el)) {
            seg = null;
            body.append(el);
          } else {
            if (!seg) {
              seg = document.createElement("div");
              seg.className = "colseg";
              body.append(seg);
            }
            seg.append(el);
          }
        }
        // Columns are per section: only segments with enough text get them,
        // so a short ingress or a brief section stays single-column.
        for (const s of body.querySelectorAll(".colseg")) {
          s.classList.toggle("cols", s.textContent.trim().length > 600);
        }
      }
    }
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
  const pageCache = new Map(); // pathname -> HTML text
  addEventListener("pagerite:page-fetched", (ev) => {
    pageCache.set(new URL(ev.detail.url, location.href).pathname, ev.detail.html);
  });

  function preload() {
    const urls = new Set();
    for (const a of document.querySelectorAll(
      '#nav a[href^="/"], #sidebar a[href^="/"], #main a[href^="/"]',
    )) {
      urls.add(a.pathname);
    }
    for (const url of urls) {
      if (pageCache.has(url)) continue;
      // x-pagerite-preload: idle cache warm-up, not a page view — the
      // server excludes these GETs from analytics (the ping sent on actual
      // navigation does the counting).
      fetch(url, { headers: { "x-pagerite-preload": "1" } })
        .then((r) => (r.ok && (r.headers.get("content-type") || "").includes("text/html")
          ? r.text() : ""))
        .then((html) => { if (html) pageCache.set(url, html); })
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

  // --- Banner parallax ----------------------------------------------------
  // The banner artwork stays windowed in place while its contents drift
  // against the scroll. The --pry scroll parameter is also available to
  // themes for their own effects (e.g. the purple sun rising faster than
  // the drift). Event-driven only: perfectly still when the page is idle.
  if (!reduceMotion.matches) {
    let ticking = false;
    const drift = () => {
      ticking = false;
      document.documentElement.style.setProperty(
        "--pry", `${Math.min(scrollY * 0.1, 30)}px`,
      );
    };
    addEventListener("scroll", () => {
      if (!ticking) {
        ticking = true;
        requestAnimationFrame(drift);
      }
    }, { passive: true });
  }

  // --- Analytics pings ---------------------------------------------------
  // Fire-and-forget POST /_a {fr, to, read}: on the initial page load
  // (starts the visit — the server counts nothing from the document GET
  // alone), for internal fetch-navigations, for external https exits, and
  // on window close. ``read`` is the active time (ms) spent on ``fr``.
  // Reading time pauses after 1 minute of inactivity and resumes on the
  // next mouse/touch/scroll/keyboard event.
  // Excluded: back/forward (popstate never pings), everything while the
  // editor is open (body.editing — admin noise, not visits), and
  // navigations TO the analytics page (/_a — admin machinery, and the
  // server rejects it as a ping target anyway). Navigations AWAY from /_a
  // must ping: load() already fetched the target page without the preload
  // header, and without the ping that GET would flush to the crawler list.
  // Admins (when SSO is actually in use — with no auth proxy "admin" is
  // everyone's state) ping normally but with hide=1: the server then
  // records nothing and scrubs any session the same browser accumulated
  // before logging in, so admins never show up as visits or crawlers.
  // See docs/analytics.md.
  function ping(to, fr = currentPath, read = 0) {
    if (document.body.classList.contains("editing")) return;
    if (to && to === "/_a") return;
    const hide = ssoAvailable && isAdmin ? 1 : 0;
    const body = JSON.stringify({
      fr, to, hide,
      read: Math.max(0, Math.round(read / 1000)),
    });
    try {
      fetch("/_a", {
        method: "POST",
        keepalive: true,
        headers: { "content-type": "application/json" },
        body,
      });
    } catch { /* analytics must never break navigation */ }
  }

  // Active reading time for the current page. The clock stops after 1 minute
  // without activity and restarts on the next mouse/touch/scroll/keyboard
  // event.
  const INACTIVE_MS = 60_000;
  let readStart = performance.now();
  let readElapsed = 0;
  let reading = true;
  let readInactivityTimer = null;
  let closePingedFor = null;

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

  function sendClosePing() {
    if (closePingedFor === currentPath) return;
    const read = Math.max(0, Math.round(takeReadTime() / 1000));
    if (read <= 0) return;
    const hide = ssoAvailable && isAdmin ? 1 : 0;
    const body = JSON.stringify({ fr: currentPath, hide, read });
    const blob = new Blob([body], { type: "application/json" });
    try {
      if (navigator.sendBeacon) {
        navigator.sendBeacon("/_a", blob);
      } else {
        fetch("/_a", {
          method: "POST",
          keepalive: true,
          headers: { "content-type": "application/json" },
          body,
        });
      }
    } catch { /* analytics must never break navigation */ }
    closePingedFor = currentPath;
  }

  for (const ev of ["mousemove", "mousedown", "touchstart", "touchmove", "scroll", "keydown"]) {
    addEventListener(ev, markReadActivity, { passive: true });
  }
  addEventListener("pagehide", sendClosePing);

  // The initial page load pings too — it is what starts the visit and
  // counts the entry page view (the document GET alone records nothing).
  // Sent once per load, after the auth probes so the admin gate applies;
  // the pageshow re-probe must not ping again. Reloads are not visits:
  // pinging them would double-count the view and log a self-transition.
  let entryPinged = false;
  function pingEntryOnce() {
    if (entryPinged) return;
    entryPinged = true;
    const nav = performance.getEntriesByType?.("navigation")[0];
    if (nav ? nav.type === "reload" : performance.navigation?.type === 1) return;
    ping(currentPath);
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

  // --- Fetch navigation ------------------------------------------------
  async function load(url, push = true, back = false) {
    // Navigating with the editor open closes it; unsaved edits are lost
    // (the region swap discards the previewed changes anyway).
    if (document.body.classList.contains("editing")) {
      editorModule?.then((m) => m.closeEditor());
    }
    teardownAnalytics();
    let doc;
    let finalUrl = url;
    const cached = pageCache.get(new URL(url, location.href).pathname);
    if (cached) {
      doc = new DOMParser().parseFromString(cached, "text/html");
    } else {
      try {
        const res = await fetch(url);
        const type = res.headers.get("content-type") || "";
        if (!res.ok || !type.includes("text/html")) throw new Error("not a page");
        // Reflect any redirect the server issued.
        if (res.redirected) finalUrl = res.url;
        const html = await res.text();
        // Populate the cache too, so returning here (back/forward, or a
        // self-link in the nav) is served from memory.
        pageCache.set(new URL(finalUrl, location.href).pathname, html);
        doc = new DOMParser().parseFromString(html, "text/html");
      } catch {
        location.href = url; // fall back to a normal navigation
        return false;
      }
    }
    if (REGIONS.some((id) => !doc.getElementById(id))) {
      location.href = url;
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
      document.title = doc.title;
      // Banners may contain scripts (canvas etc.), content pages may too.
      runScripts(document.getElementById("page-banner"));
      runScripts(document.getElementById("main"));
      applyEffects();
      mountAnalytics(doc);
    };
    // Rotating cube page transition (see the FRAGILE block in pagerite.css);
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
    if (push) history.pushState(null, "", finalUrl);
    scrollTo(0, 0);
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
      if (document.body.classList.contains("editing")
          && document.body.dataset.editorMode === mode) {
        editorModule?.then((m) => m.closeEditor());
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
    const a = ev.target.closest("a[href]");
    if (!a || a.target || a.hasAttribute("download")) return;
    const url = new URL(a.href, location.href);
    if (url.origin !== location.origin) {
      // External link: the browser navigates; record the full https URL so
      // different links to the same domain stay distinct in analytics.
      if (url.protocol === "https:") {
        closePingedFor = currentPath;
        ping(url.href, currentPath, takeReadTime());
      }
      return;
    }
    // Same-page anchor links (footnotes etc.): let the browser handle them
    if (url.pathname === location.pathname && url.hash) return;
    // Machinery and auth endpoints are never fetch-navigated, except the
    // public analytics viewer page at /_a.
    if ((url.pathname.startsWith("/_") && url.pathname !== "/_a")
        || url.pathname.startsWith("/auth")) return;
    ev.preventDefault();
    // Capture the source now: load() updates currentPath before pinging.
    const from = currentPath;
    load(url).then((ok) => {
      if (!ok) return;
      closePingedFor = null;
      ping(url.pathname, from, takeReadTime());
      resetReadTime();
    });
  });

  addEventListener("popstate", () => {
    // Hash-only history entries are not navigations.
    if (location.pathname === currentPath) return;
    load(location.href, false, true);
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
})();
