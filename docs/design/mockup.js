/* Mockup behaviour only: icons, theme, command palette, shortcut sheet, list movement, tabs, toasts.
   Nothing here talks to a server. The real app builds these with shadcn components (see README). */
(function () {
  "use strict";

  /* ---------- icons (Lucide paths, injected so pages also work from file://) ---------- */
  var P = {
    today: '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/><path d="m9 16 2 2 4-4"/>',
    outbox: '<path d="M14.536 21.686a.5.5 0 0 0 .937-.024l6.5-19a.496.496 0 0 0-.635-.635l-19 6.5a.5.5 0 0 0-.024.937l7.93 3.18a2 2 0 0 1 1.112 1.11z"/><path d="m21.854 2.147-10.94 10.939"/>',
    linkedin: '<path d="M16 8a6 6 0 0 1 6 6v7h-4v-7a2 2 0 0 0-2-2 2 2 0 0 0-2 2v7h-4v-7a6 6 0 0 1 6-6z"/><rect x="2" y="9" width="4" height="12"/><circle cx="4" cy="4" r="2"/>',
    alert: '<circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/>',
    ban: '<circle cx="12" cy="12" r="10"/><path d="m4.9 4.9 14.2 14.2"/>',
    building: '<path d="M6 22V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v18Z"/><path d="M6 12H4a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2"/><path d="M18 9h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-2"/><path d="M10 6h4M10 10h4M10 14h4M10 18h4"/>',
    done: '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
    search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2m-7.07-17.07 1.41 1.41m11.32 11.32 1.41 1.41M2 12h2m16 0h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>',
    moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
    pause: '<rect x="14" y="4" width="4" height="16" rx="1"/><rect x="6" y="4" width="4" height="16" rx="1"/>',
    play: '<polygon points="6 3 20 12 6 21 6 3"/>',
    copy: '<rect x="8" y="8" width="14" height="14" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
    external: '<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    x: '<path d="M18 6 6 18M6 6l12 12"/>',
    chevron: '<path d="m9 18 6-6-6-6"/>',
    down: '<path d="m6 9 6 6 6-6"/>',
    more: '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
    lock: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    warn: '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4M12 17h.01"/>',
    block: '<path d="M2.586 16.726A2 2 0 0 1 2 15.312V8.688a2 2 0 0 1 .586-1.414l4.688-4.688A2 2 0 0 1 8.688 2h6.624a2 2 0 0 1 1.414.586l4.688 4.688A2 2 0 0 1 22 8.688v6.624a2 2 0 0 1-.586 1.414l-4.688 4.688a2 2 0 0 1-1.414.586H8.688a2 2 0 0 1-1.414-.586z"/><path d="m15 9-6 6M9 9l6 6"/>',
    clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    reply: '<polyline points="9 17 4 12 9 7"/><path d="M20 18v-2a4 4 0 0 0-4-4H4"/>',
    archive: '<rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8M10 12h4"/>',
    trash: '<path d="M3 6h18M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>',
    pencil: '<path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/>',
    refresh: '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
    panel: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18"/>',
    user: '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    mail: '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>',
    terminal: '<polyline points="4 17 10 11 4 5"/><path d="M12 19h8"/>',
    activity: '<path d="M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2"/>',
    filter: '<polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/>',
    undo: '<path d="M3 7v6h6"/><path d="M21 17a9 9 0 0 0-9-9 9 9 0 0 0-6 2.3L3 13"/>',
    keyboard: '<path d="M10 8h.01M12 12h.01M14 8h.01M16 12h.01M18 8h.01M6 8h.01M7 16h10M8 12h.01"/><rect x="2" y="4" width="20" height="16" rx="2"/>',
    help: '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3M12 17h.01"/>',
    plus: '<path d="M5 12h14M12 5v14"/>',
    chart: '<path d="M5 21v-6M12 21V3M19 21V9"/>',
    award: '<path d="m15.477 12.89 1.515 8.526a.5.5 0 0 1-.81.47l-3.58-2.687a1 1 0 0 0-1.197 0l-3.586 2.686a.5.5 0 0 1-.81-.469l1.514-8.526"/><circle cx="12" cy="8" r="6"/>',
    flag: '<path d="M4 22V4a1 1 0 0 1 .4-.8A6 6 0 0 1 8 2c3 0 5 2 7.333 2q2 0 3.067-.8A1 1 0 0 1 20 4v10a1 1 0 0 1-.4.8A6 6 0 0 1 16 16c-3 0-5-2-8-2a6 6 0 0 0-4 1.528"/>',
    differs: '<path d="M5 9h14M5 15h14M19 5 5 19"/>',
    compass: '<circle cx="12" cy="12" r="10"/><path d="m16.24 7.76-1.804 5.411a2 2 0 0 1-1.265 1.265L7.76 16.24l1.804-5.411a2 2 0 0 1 1.265-1.265z"/>',
    download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5M12 15V3"/>'
  };
  var sprite = '<svg xmlns="http://www.w3.org/2000/svg" style="display:none">' +
    Object.keys(P).map(function (k) { return '<symbol id="' + k + '" viewBox="0 0 24 24">' + P[k] + '</symbol>'; }).join("") + "</svg>";
  document.body.insertAdjacentHTML("afterbegin", sprite);
  var icon = function (k) { return '<svg class="i" aria-hidden="true"><use href="#' + k + '"/></svg>'; };

  /* ---------- theme ---------- */
  var root = document.documentElement;
  var saved = null;
  try { saved = localStorage.getItem("mock-theme"); } catch (e) {}
  var forced = /theme=(dark|light)/.exec(location.search);
  if (forced) saved = forced[1];
  if (saved === "dark" || (!saved && matchMedia("(prefers-color-scheme: dark)").matches)) root.classList.add("dark");
  var setThemeIcons = function () {
    document.querySelectorAll("[data-theme-toggle] use").forEach(function (u) { u.setAttribute("href", root.classList.contains("dark") ? "#sun" : "#moon"); });
  };
  var toggleTheme = function () {
    root.classList.toggle("dark");
    try { localStorage.setItem("mock-theme", root.classList.contains("dark") ? "dark" : "light"); } catch (e) {}
    setThemeIcons();
  };
  document.querySelectorAll("[data-theme-toggle]").forEach(function (b) { b.addEventListener("click", toggleTheme); });
  setThemeIcons();

  /* ---------- toasts (Sonner) ---------- */
  var toaster = document.createElement("div");
  toaster.className = "toaster"; toaster.setAttribute("role", "status"); toaster.setAttribute("aria-live", "polite");
  document.body.appendChild(toaster);
  function toast(msg, undo) {
    var t = document.createElement("div");
    t.className = "toast";
    t.innerHTML = '<span class="grow">' + msg + "</span>" + (undo ? '<button class="btn btn-outline btn-sm" type="button">Undo <span class="kbd">Z</span></button>' : "");
    toaster.appendChild(t);
    setTimeout(function () { t.remove(); }, undo ? 8000 : 3000);
  }
  window.mockToast = toast; /* #33 pages script their own one-key flows on top of this */
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-toast]");
    if (b) toast(b.getAttribute("data-toast"), b.hasAttribute("data-undo"));
  });

  /* ---------- dialogs: any [data-dialog="id"] opens the overlay with that id ---------- */
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-dialog]");
    if (b) { var d = document.getElementById(b.getAttribute("data-dialog")); if (d) { d.hidden = false; var f = d.querySelector("input, select, textarea, button"); if (f) f.focus(); } return; }
    var c = e.target.closest(".overlay [data-close]");
    if (c) c.closest(".overlay").hidden = true;
    if (e.target.classList && e.target.classList.contains("overlay") && e.target.hasAttribute("data-modal")) e.target.hidden = true;
  });

  /* ---------- tabs ---------- */
  /* [data-when="panelA panelB"] shows only while one of those tab panels is shown (a sidebar count per mockup state). */
  function syncWhen() {
    document.querySelectorAll("[data-when]").forEach(function (el) {
      el.hidden = !el.getAttribute("data-when").split(" ").some(function (id) { var p = document.getElementById(id); return p && !p.hidden; });
    });
  }
  document.querySelectorAll('[role="tablist"]').forEach(function (list) {
    list.addEventListener("click", function (e) {
      var tab = e.target.closest('[role="tab"]'); if (!tab) return;
      list.querySelectorAll('[role="tab"]').forEach(function (t) {
        var on = t === tab; t.setAttribute("aria-selected", on);
        var p = document.getElementById(t.getAttribute("aria-controls")); if (p) p.hidden = !on;
      });
      syncWhen();
    });
  });
  /* review helper: ?tab=<panel id> opens that tab on load */
  var tabq = /[?&]tab=([\w-]+)/.exec(location.search);
  if (tabq) { var tq = document.querySelector('[role="tab"][aria-controls="' + tabq[1] + '"]'); if (tq) tq.click(); }
  syncWhen();

  /* ---------- list movement: j/k, arrows, enter, x ---------- */
  var rows = function () { return Array.prototype.slice.call(document.querySelectorAll("[data-row]")).filter(function (r) { return r.offsetParent !== null; }); };
  function activate(r) {
    if (!r) return;
    rows().forEach(function (x) { x.classList.toggle("is-active", x === r); x.setAttribute("aria-selected", x === r); });
    r.scrollIntoView({ block: "nearest" });
    var pane = r.getAttribute("data-pane");
    if (pane) document.querySelectorAll(".detail").forEach(function (d) { d.hidden = d.id !== pane; });
  }
  document.addEventListener("click", function (e) {
    var r = e.target.closest("[data-row]");
    if (r && !e.target.closest("button, a, input")) activate(r);
  });

  /* ---------- command palette ---------- */
  var pages = [
    ["Today", "today.html", "today", "G T"], ["Outbox", "outbox.html", "outbox", "G O"], ["LinkedIn list", "linkedin.html", "linkedin", "G L"],
    ["Needs you", "needs-you.html", "alert", "G N"], ["No-go list", "no-go.html", "ban", "G X"], ["Done companies", "done.html", "done", "G D"],
    ["Today as a plan", "today-plan.html", "flag", ""], ["Progress", "progress.html", "chart", "G P"], ["Wins", "wins.html", "award", "G W"],
    ["Insights", "insights.html", "compass", "G I"]
  ];
  var companies = [
    ["Browserbase", "Held: reply from Priya Natarajan"], ["Mintlify", "Emailing, day 2 of 3"], ["Resend", "Picked today"],
    ["Trigger.dev", "Picked today"], ["Graphite", "Emailing, day 2 of 3"], ["Inngest", "Proposed"], ["Retell AI", "Done: interviewed"],
    ["Distyl AI", "Done: interviewed"], ["Crusoe", "Done: interviewing"], ["Halberd Systems", "No-go: no sponsorship"]
  ];
  var people = [
    ["Priya Natarajan", "Engineering Manager, Browserbase"], ["Marcus Feld", "Software Engineer, Mintlify"],
    ["Dana Whitcomb", "Technical Recruiter, Mintlify"], ["Omar Haddad", "Co-founder and CTO, Resend"], ["Lena Szabo", "Staff Engineer, Browserbase"]
  ];
  var actions = [
    ["Pause sending", "Stops the sender after the email in flight", "pause"], ["Toggle theme", "Light or dark", "moon"],
    ["Keyboard shortcuts", "Every key in one sheet", "keyboard"]
  ];
  var ov = document.createElement("div");
  ov.className = "overlay"; ov.hidden = true;
  ov.innerHTML = '<div class="dialog" role="dialog" aria-modal="true" aria-label="Search and commands">' +
    '<div class="cmd-input">' + icon("search") + '<input id="cmd-q" placeholder="Search companies, people, views and actions" autocomplete="off" aria-label="Search"></div>' +
    '<div class="cmd-list" id="cmd-list" role="listbox"></div>' +
    '<div class="cmd-foot"><span><span class="kbd">&uarr;</span><span class="kbd">&darr;</span> move</span><span><span class="kbd">Enter</span> open</span><span><span class="kbd">Esc</span> close</span></div></div>';
  document.body.appendChild(ov);
  var q = ov.querySelector("#cmd-q"), list = ov.querySelector("#cmd-list"), sel = 0;
  function renderCmd() {
    var s = q.value.trim().toLowerCase(), html = "", n = 0;
    var m = function (t) { return !s || t.toLowerCase().indexOf(s) !== -1; };
    var grp = function (title, items, fn) {
      items = items.filter(function (x) { return m(x[0] + " " + x[1]); });
      if (!items.length) return;
      html += '<div class="cmd-group">' + title + "</div>";
      items.forEach(function (x) { html += fn(x, n++); });
    };
    grp("Companies", companies, function (x, i) { return '<a class="cmd-item" role="option" href="company.html" aria-selected="' + (i === sel) + '">' + icon("building") + "<span>" + x[0] + '</span><span class="sub">' + x[1] + "</span></a>"; });
    grp("People", people, function (x, i) { return '<a class="cmd-item" role="option" href="company.html" aria-selected="' + (i === sel) + '">' + icon("user") + "<span>" + x[0] + '</span><span class="sub">' + x[1] + "</span></a>"; });
    grp("Go to", pages, function (x, i) { return '<a class="cmd-item" role="option" href="' + x[1] + '" aria-selected="' + (i === sel) + '">' + icon(x[2]) + "<span>" + x[0] + '</span><span class="kbd-row">' + x[3].split(" ").filter(Boolean).map(function (k) { return '<span class="kbd">' + k + "</span>"; }).join("") + "</span></a>"; });
    grp("Actions", actions, function (x, i) { return '<a class="cmd-item" role="option" href="#" data-cmd="' + x[0] + '" aria-selected="' + (i === sel) + '">' + icon(x[2]) + "<span>" + x[0] + '</span><span class="sub">' + x[1] + "</span></a>"; });
    list.innerHTML = html || '<div class="cmd-empty">Nothing matches. Try a company, a person or a view name.</div>';
  }
  function openCmd() { ov.hidden = false; q.value = ""; sel = 0; renderCmd(); q.focus(); }
  function closeAll() { document.querySelectorAll(".overlay").forEach(function (o) { o.hidden = true; }); }
  q.addEventListener("input", function () { sel = 0; renderCmd(); });
  q.addEventListener("keydown", function (e) {
    var items = list.querySelectorAll(".cmd-item");
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault(); sel = (sel + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      items.forEach(function (it, i) { it.setAttribute("aria-selected", i === sel); if (i === sel) it.scrollIntoView({ block: "nearest" }); });
    } else if (e.key === "Enter" && items[sel]) { e.preventDefault(); items[sel].click(); }
  });
  list.addEventListener("click", function (e) {
    var it = e.target.closest("[data-cmd]"); if (!it) return;
    e.preventDefault(); closeAll();
    var c = it.getAttribute("data-cmd");
    if (c === "Toggle theme") toggleTheme(); else if (c === "Keyboard shortcuts") openKeys(); else toast("Sending paused. Nothing sends until you resume.");
  });
  ov.addEventListener("mousedown", function (e) { if (e.target === ov) closeAll(); });
  document.querySelectorAll("[data-palette]").forEach(function (b) { b.addEventListener("click", openCmd); });

  /* ---------- shortcut sheet ---------- */
  var kov = document.createElement("div");
  kov.className = "overlay"; kov.hidden = true;
  var tpl = document.getElementById("page-keys");
  kov.innerHTML = '<div class="dialog" role="dialog" aria-modal="true" aria-label="Keyboard shortcuts" style="width:min(640px,calc(100vw - 32px))"><div class="dialog-body">' +
    '<div class="row"><h2 class="t-lg">Keyboard shortcuts</h2><button class="btn btn-ghost btn-icon btn-sm" style="margin-left:auto" data-close aria-label="Close">' + icon("x") + "</button></div>" +
    '<div class="keys-grid"><h4>Everywhere</h4>' +
    [["Search and commands", "⌘ K"], ["Search", "/"], ["Today", "G T"], ["Outbox", "G O"], ["LinkedIn list", "G L"], ["Needs you", "G N"],
     ["No-go list", "G X"], ["Done companies", "G D"], ["Progress", "G P"], ["Wins", "G W"], ["Insights", "G I"], ["Move down, up", "J K"], ["Open", "Enter"], ["Undo last action", "Z"],
     ["Close, go back", "Esc"], ["This sheet", "?"], ["Toggle sidebar", "⌘ B"]].map(function (k) {
      return "<div><span>" + k[0] + '</span><span class="kbd-row">' + k[1].split(" ").map(function (x) { return '<span class="kbd">' + x + "</span>"; }).join("") + "</span></div>";
    }).join("") + (tpl ? tpl.innerHTML : "") + "</div></div></div>";
  document.body.appendChild(kov);
  function openKeys() { kov.hidden = false; kov.querySelector("[data-close]").focus(); }
  kov.addEventListener("mousedown", function (e) { if (e.target === kov || e.target.closest("[data-close]")) closeAll(); });
  document.querySelectorAll("[data-keys]").forEach(function (b) { b.addEventListener("click", openKeys); });

  /* review helper: ?open=palette or ?open=keys opens that overlay on load */
  var want = /open=(palette|keys)/.exec(location.search);
  if (want) (want[1] === "palette" ? openCmd : openKeys)();
  var pane = /pane=([\w-]+)/.exec(location.search);
  if (pane) activate(document.querySelector('[data-pane="' + pane[1] + '"]'));

  /* ---------- global keys ---------- */
  var chord = false, chordT = 0;
  var go = { t: "today.html", o: "outbox.html", l: "linkedin.html", n: "needs-you.html", x: "no-go.html", d: "done.html", c: "company.html", p: "progress.html", w: "wins.html", i: "insights.html" };
  document.addEventListener("keydown", function (e) {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); ov.hidden ? openCmd() : closeAll(); return; }
    if (e.key === "Escape") { closeAll(); return; }
    var t = e.target;
    if (t.closest && t.closest("input, textarea, select, [contenteditable]")) return;
    if (e.metaKey || e.ctrlKey || e.altKey || document.querySelector(".overlay:not([hidden])")) return;
    var k = e.key;
    if (chord) { chord = false; clearTimeout(chordT); if (go[k.toLowerCase()]) location.href = go[k.toLowerCase()]; return; }
    if (k === "g") { chord = true; chordT = setTimeout(function () { chord = false; }, 900); return; }
    if (k === "/") { e.preventDefault(); openCmd(); return; }
    if (k === "?") { openKeys(); return; }
    /* any visible [data-key] control answers its key (#33 pages: one key per step) */
    var keyed = Array.prototype.slice.call(document.querySelectorAll("[data-key]")).filter(function (b) {
      return b.getAttribute("data-key") === k && b.offsetParent !== null && b.getAttribute("aria-disabled") !== "true";
    });
    if (keyed.length) { e.preventDefault(); keyed[0].click(); return; }
    var rs = rows(); if (!rs.length) return;
    var i = rs.findIndex(function (r) { return r.classList.contains("is-active"); });
    if (k === "j" || k === "ArrowDown") { e.preventDefault(); activate(rs[Math.min(rs.length - 1, i + 1)]); }
    else if (k === "k" || k === "ArrowUp") { e.preventDefault(); activate(rs[Math.max(0, i - 1)]); }
    else if (k === "Enter" && i >= 0 && rs[i].getAttribute("data-href")) location.href = rs[i].getAttribute("data-href");
    else if (k === "x" && i >= 0) { var cb = rs[i].querySelector(".checkbox"); if (cb) cb.checked = !cb.checked; }
  });
})();
