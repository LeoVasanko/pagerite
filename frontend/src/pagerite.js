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
    const src = document.querySelector('meta[name="pagerite:editor-src"]')?.content;
    if (!src) { pingEntryOnce(); return; }
    editorMeta = {
      src,
      css: document.querySelector('meta[name="pagerite:editor-css"]')?.content,
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
  // cache in sync after edits.
  const pageCache = new Map(); // pathname -> HTML text
  addEventListener("pagerite:page-fetched", (ev) => {
    pageCache.set(new URL(ev.detail.url, location.href).pathname, ev.detail.html);
  });

  function preload() {
    const urls = new Set([location.pathname]);
    for (const a of document.querySelectorAll(
      '#nav a[href^="/"], #sidebar a[href^="/"], #main a[href^="/"]',
    )) {
      urls.add(a.pathname);
    }
    for (const url of urls) {
      if (pageCache.has(url)) continue;
      fetch(url)
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
  // Fire-and-forget POST /_a {fr, to}: on the initial page load (starts the
  // visit — the server counts nothing from the document GET alone), for
  // internal fetch-navigations and for external https exits. Excluded:
  // back/forward (popstate never pings) and everything while we know the
  // user is an admin — but only when SSO is actually in use; with no auth
  // (dev/test) "admin" is everyone's state and nothing would be recorded —
  // or has the editor open (admin noise, not visits). The analytics page
  // itself (/_a) is also excluded even though fetch-navigation treats it like
  // a normal article.
  // See docs/analytics.md.
  function ping(to, fr = currentPath) {
    if ((ssoAvailable && isAdmin) || document.body.classList.contains("editing")
        || to === "/_a" || fr === "/_a") return;
    try {
      fetch("/_a", {
        method: "POST",
        keepalive: true,
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ fr, to }),
      });
    } catch { /* analytics must never break navigation */ }
  }

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
  // but whose content is a Vue app. We load the entry module on demand so the
  // analytics bundle is only fetched when visiting /_a, and unmount the app
  // before swapping away so Vue teardown runs cleanly.
  let analyticsUnmount = null;

  function teardownAnalytics() {
    analyticsUnmount?.();
    analyticsUnmount = null;
  }

  async function mountAnalytics(doc) {
    const src = doc.querySelector('meta[name="pagerite:analytics-src"]')?.content;
    if (!src) {
      teardownAnalytics();
      return;
    }
    try {
      const mod = await import(/* @vite-ignore */ src);
      const container = document.getElementById("analytics-app");
      if (container) {
        mod.mount(container);
        analyticsUnmount = mod.unmount;
      }
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
        // Populate the cache too, or the post-swap preload (which includes
        // location.pathname) would fetch the very page we just loaded again.
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
      // Site-wide custom CSS lives in <head id="pagerite-user"> and must be
      // kept in sync across fetch-navigations. It is kept last in <head>:
      // in dev Vite injects the base stylesheet after the server-rendered
      // tag, and equal-specificity :root rules are decided by order.
      const oldUserStyle = document.getElementById("pagerite-user");
      const newUserStyle = doc.getElementById("pagerite-user");
      if (oldUserStyle && newUserStyle) {
        oldUserStyle.textContent = newUserStyle.textContent;
        document.head.appendChild(oldUserStyle);
      } else if (newUserStyle) {
        document.head.appendChild(document.importNode(newUserStyle, true));
      } else if (oldUserStyle) {
        oldUserStyle.remove();
      }
      document.title = doc.title;
      // Banners may contain scripts (canvas etc.), content pages may too.
      runScripts(document.getElementById("page-banner"));
      runScripts(document.getElementById("main"));
      applyEffects();
      // The fetched doc carries the analytics meta; the live document's
      // <head> is never swapped, so querying it would never find the entry.
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
      // External link: the browser navigates; just record the exit (https
      // origins only, stripped to the origin part server-side anyway).
      if (url.protocol === "https:") ping(url.origin);
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
    load(url).then((ok) => { if (ok) ping(url.pathname, from); });
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

  setupAuth();
  applyEffects();
  mountAnalytics(document);
})();
