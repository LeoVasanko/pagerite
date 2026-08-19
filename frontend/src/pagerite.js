// Fetch-navigation: swap dynamic regions (#nav, #main) instead of full
// page loads. Real <a href> links are used throughout, so this is pure
// progressive enhancement - without JS every link does a normal load.
//
// Also: scroll-reveal effects and code copy buttons. These need no
// support from the article itself and are re-applied after each swap.
import { showAuthIframe } from 'paskia'
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
  // When Paskia SSO is in use, 401/403 responses carry `auth.iframe`, which
  // we use to open the login/profile dialogs inline instead of navigating
  // away. A separate probe to /auth/api/settings tells us whether Paskia is
  // available at all; if it isn't, we treat the site as dev/no-proxy and
  // leave editing open.
  let ssoAvailable = false;
  let isAdmin = false;
  let loginIframeUrl = null;
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

  function makeLoginButton(url) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "login-link";
    btn.title = "log in";
    btn.textContent = "🔑";
    btn.addEventListener("click", async () => {
      try {
        await showAuthIframe(url);
        // Successful login: refresh the auth UI (may now show edit pens).
        setupAuth();
      } catch {
        // Cancelled or error: leave the button in place.
      }
    });
    return btn;
  }

  function makeProfileButton() {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "profile-link";
    btn.title = "profile";
    btn.textContent = "🔐";
    btn.addEventListener("click", () => {
      showAuthIframe("/auth/").catch(() => {});
    });
    return btn;
  }

  function renderAuthUi() {
    // Editing is open for admins and, as a dev/no-proxy fallback, when no
    // Paskia SSO is detected at all.
    const canEdit = isAdmin || !ssoAvailable;
    const banner = document.getElementById("page-banner");
    if (banner) {
      const old = banner.parentElement.querySelector(".editor-pens");
      if (old) old.remove();
      const pens = document.createElement("div");
      pens.className = "editor-pens";
      if (canEdit) {
        pens.append(makePen("banner"));
        pens.append(makePen("site"));
      }
      if (isAdmin && ssoAvailable) {
        pens.append(makeProfileButton());
      } else if (!isAdmin && ssoAvailable && loginIframeUrl) {
        pens.append(makeLoginButton(loginIframeUrl));
      } else if (!isAdmin && ssoAvailable) {
        pens.append(makeProfileButton());
      }
      banner.after(pens);
    }
    if (canEdit) injectPagePen();
  }

  async function setupAuth() {
    const src = document.querySelector('meta[name="pagerite:editor-src"]')?.content;
    if (!src) return;
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
    loginIframeUrl = null;
    let status = 0;
    try {
      const res = await fetch("/_api/settings");
      status = res.status;
      if (status === 401) {
        const data = await res.json().catch(() => ({}));
        loginIframeUrl = data.auth?.iframe || null;
      }
    } catch {
      // No auth proxy / dev.
    }
    if (status === 200) isAdmin = true;

    renderAuthUi();
  }

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

  // --- Preloading ------------------------------------------------------
  // Warm the HTTP cache with all linked pages and their resources, so
  // navigation (and the cube transition) is instant. Pages carry ETags,
  // so re-running this after each navigation revalidates cheaply (304)
  // and picks up changed content and images.
  function preload() {
    const urls = new Set();
    for (const a of document.querySelectorAll('#nav a[href^="/"], #main a[href^="/"]')) {
      urls.add(a.pathname);
    }
    for (const url of urls) {
      if (url === location.pathname) continue;
      fetch(url)
        .then((r) => (r.ok ? r.text() : ""))
        .then((html) => {
          if (!html) return;
          // Off-screen parse: load the page's images and other resources
          const doc = new DOMParser().parseFromString(html, "text/html");
          for (const img of doc.querySelectorAll("img")) {
            const i = new Image();
            i.src = img.src;
          }
        })
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

  // --- Fetch navigation ------------------------------------------------
  async function load(url, push = true, back = false) {
    // Navigating with the editor open closes it; unsaved edits are lost
    // (the region swap discards the previewed changes anyway).
    if (document.body.classList.contains("editing")) {
      editorModule?.then((m) => m.closeEditor());
    }
    let doc;
    let finalUrl = url;
    try {
      const res = await fetch(url);
      const type = res.headers.get("content-type") || "";
      if (!res.ok || !type.includes("text/html")) throw new Error("not a page");
      // Reflect any redirect the server issued.
      if (res.redirected) finalUrl = res.url;
      doc = new DOMParser().parseFromString(await res.text(), "text/html");
    } catch {
      location.href = url; // fall back to a normal navigation
      return;
    }
    if (REGIONS.some((id) => !doc.getElementById(id))) {
      location.href = url;
      return;
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
    if (url.origin !== location.origin) return;
    // Same-page anchor links (footnotes etc.): let the browser handle them
    if (url.pathname === location.pathname && url.hash) return;
    // Machinery and auth endpoints are never fetch-navigated.
    if (url.pathname.startsWith("/_") || url.pathname.startsWith("/auth")) return;
    ev.preventDefault();
    load(url);
  });

  addEventListener("popstate", () => load(location.href, false, true));

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
})();
