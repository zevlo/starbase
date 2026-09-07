/* STARBASE mission control — vanilla JS, no build step.
 *
 * Data flow: fetch /api/v1/board every ~45 s from OUR API (same origin via
 * CloudFront). The countdown ticks locally from the stored NET; the browser
 * never talks to Launch Library directly.
 *
 * Dev switches (query string):
 *   ?mock=go|hold|inflight      load web/mock/board-<name>.json instead of the API
 *   ?api=https://xyz.execute-api.us-east-1.amazonaws.com   use a different API base
 */
(() => {
  "use strict";

  const REFRESH_MS = 45_000;
  const REFRESH_JITTER_MS = 5_000;
  const STALE_AFTER_S = 1800;
  const STRIP_LIMIT = 8;
  const COARSE_PRECISION = new Set(["DAY", "WEEK", "MONTH", "QUARTER", "HALF", "YEAR", "DECADE", "FUZ"]);
  const TERMINAL = new Set(["Success", "Failure", "Partial Failure"]);

  const qs = new URLSearchParams(location.search);
  const MOCK = qs.get("mock");
  const API_BASE = (qs.get("api") || "").replace(/\/$/, "");

  const $ = (id) => document.getElementById(id);
  const el = {
    hero: $("hero"), bg: $("hero-bg"), clock: $("clock"), clockLabel: $("clock-label"), clockSub: $("clock-sub"),
    pill: $("status-pill"), net: $("net-text"), title: $("title"),
    provider: $("f-provider"), vehicle: $("f-vehicle"), pad: $("f-pad"), orbit: $("f-orbit"),
    mission: $("f-mission"), window: $("f-window"),
    watch: $("watch"), watchText: $("watch-text"), autoBtn: $("auto-btn"),
    strip: $("strip"), stripCount: $("strip-count"), stale: $("stale-chip"), mock: $("mock-chip"),
    utc: $("utc-clock"), updated: $("updated"), cardTpl: $("card-tpl"),
  };

  const state = {
    board: null,
    fetchedAt: 0,
    selectedId: null, // manual selection from the strip; null = follow the API hero
    lastError: null,
    timer: null,
  };

  // ---------------------------------------------------------------------------
  // formatting helpers
  const pad2 = (n) => String(n).padStart(2, "0");
  const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

  function fmtHMS(ms) {
    const total = Math.max(0, Math.floor(ms / 1000));
    const d = Math.floor(total / 86400);
    const h = Math.floor((total % 86400) / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    return d > 0 ? `${d}d ${pad2(h)}:${pad2(m)}:${pad2(s)}` : `${pad2(h)}:${pad2(m)}:${pad2(s)}`;
  }

  function fmtUTC(iso, withSeconds = false) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "—";
    const base = `${pad2(d.getUTCDate())} ${MONTHS[d.getUTCMonth()]} ${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}`;
    return withSeconds ? `${base}:${pad2(d.getUTCSeconds())} UTC` : `${base} UTC`;
  }

  function fmtDate(iso) {
    const d = new Date(iso);
    return `${pad2(d.getUTCDate())} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
  }

  function fmtRelative(iso) {
    const diff = new Date(iso).getTime() - Date.now();
    const abs = Math.abs(diff);
    const days = Math.floor(abs / 86400000);
    const hours = Math.floor((abs % 86400000) / 3600000);
    const mins = Math.floor((abs % 3600000) / 60000);
    let text;
    if (days > 0) text = `${days}d ${hours}h`;
    else if (hours > 0) text = `${hours}h ${pad2(mins)}m`;
    else text = `${mins}m`;
    return diff >= 0 ? `in ${text}` : `${text} ago`;
  }

  function statusKey(abbrev) {
    switch (abbrev) {
      case "Go": return "go";
      case "TBC": return "tbc";
      case "TBD": return "tbd";
      case "Hold": return "hold";
      case "In Flight": return "in-flight";
      case "Success": return "success";
      case "Failure":
      case "Partial Failure": return "failure";
      default: return "tbd";
    }
  }

  function statusLabel(abbrev) {
    switch (abbrev) {
      case "Go": return "GO";
      case "TBC": return "TBC";
      case "TBD": return "TBD";
      case "Hold": return "HOLD";
      case "In Flight": return "IN FLIGHT";
      case "Success": return "SUCCESS";
      case "Failure": return "FAILURE";
      case "Partial Failure": return "PARTIAL FAILURE";
      default: return (abbrev || "TBD").toUpperCase();
    }
  }

  function isCoarse(launch) {
    if (!launch || !launch.net) return true;
    const p = (launch.net_precision || "").toUpperCase();
    if (COARSE_PRECISION.has(p)) return true;
    // A midnight NET with no fine precision is a placeholder, not a T-0.
    return launch.net.endsWith("T00:00:00Z") && !["SEC", "MIN", "HR"].includes(p);
  }

  // ---------------------------------------------------------------------------
  // data
  function boardUrl() {
    if (MOCK) return `mock/board-${encodeURIComponent(MOCK)}.json`;
    return `${API_BASE}/api/v1/board?limit=${STRIP_LIMIT}`;
  }

  async function refresh() {
    clearTimeout(state.timer);
    try {
      const res = await fetch(boardUrl(), { cache: "no-store", headers: { Accept: "application/json" } });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const board = await res.json();
      if (MOCK) rebaseMock(board);
      state.board = board;
      state.fetchedAt = Date.now();
      state.lastError = null;
      render();
    } catch (err) {
      state.lastError = err;
      console.warn("board refresh failed", err);
      renderChrome();
    } finally {
      schedule();
    }
  }

  // Mock boards are static files; shift every timestamp so the hero NET lands a
  // fixed offset from "now" (mock.hero_offset_seconds, default +15 min, In Flight -90 s).
  function rebaseMock(board) {
    if (!board.hero || !board.hero.net) return;
    const inFlight = board.hero.status && board.hero.status.abbrev === "In Flight";
    const offset = (board.mock && board.mock.hero_offset_seconds) ?? (inFlight ? -90 : 15 * 60);
    const shift = Date.now() + offset * 1000 - new Date(board.hero.net).getTime();
    const bump = (iso) => (iso ? new Date(new Date(iso).getTime() + shift).toISOString().replace(/\.\d{3}Z$/, "Z") : iso);
    for (const l of [board.hero, ...(board.strip || [])]) {
      l.net = bump(l.net);
      l.window_start = bump(l.window_start);
      l.window_end = bump(l.window_end);
    }
    board.generated_at = new Date().toISOString();
    if (board.ingest) { board.ingest.last_ok_at = board.generated_at; board.ingest.stale_seconds = 0; board.ingest.stale = false; }
  }

  function schedule() {
    clearTimeout(state.timer);
    if (document.hidden || MOCK) return;
    const jitter = (Math.random() * 2 - 1) * REFRESH_JITTER_MS;
    state.timer = setTimeout(refresh, REFRESH_MS + jitter);
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { clearTimeout(state.timer); return; }
    if (Date.now() - state.fetchedAt > 20_000) refresh(); else schedule();
  });

  // ---------------------------------------------------------------------------
  // rendering
  function currentHero() {
    const b = state.board;
    if (!b) return null;
    if (state.selectedId) {
      const all = [b.hero, ...(b.strip || [])].filter(Boolean);
      const found = all.find((l) => l.id === state.selectedId);
      if (found) return found;
      state.selectedId = null; // selection fell off the board
    }
    return b.hero || null;
  }

  function render() {
    renderChrome();
    const hero = currentHero();
    renderHero(hero);
    renderStrip(hero);
    tick();
  }

  function renderChrome() {
    const b = state.board;
    const ingestStale = b && b.ingest && (b.ingest.stale || (b.ingest.stale_seconds ?? Infinity) > STALE_AFTER_S);
    const fetchStale = state.lastError && Date.now() - state.fetchedAt > 3 * REFRESH_MS;
    el.stale.hidden = !(ingestStale || fetchStale || (!b && state.lastError));
    el.mock.hidden = !MOCK;
    el.updated.textContent = b
      ? `data ${fmtUTC(b.ingest && b.ingest.last_ok_at, true)} · fetched ${fmtUTC(new Date(state.fetchedAt).toISOString(), true)}`
      : state.lastError ? `api unreachable (${state.lastError.message})` : "updated —";
  }

  function renderHero(hero) {
    el.autoBtn.hidden = !state.selectedId;
    if (!hero) {
      el.hero.dataset.status = "none";
      el.bg.style.backgroundImage = "";
      el.bg.classList.add("no-image");
      el.title.textContent = state.board ? "No upcoming launches in the manifest." : "Acquiring launch manifest…";
      el.pill.textContent = state.board ? "STANDBY" : "LOADING";
      el.pill.dataset.status = "tbd";
      el.net.textContent = "";
      for (const k of ["provider", "vehicle", "pad", "orbit", "mission", "window"]) el[k].textContent = "—";
      el.watch.hidden = true;
      return;
    }

    const key = statusKey(hero.status && hero.status.abbrev);
    el.hero.dataset.status = key;
    el.pill.textContent = statusLabel(hero.status && hero.status.abbrev);
    el.pill.dataset.status = key;

    setBackground(el.bg, hero.image_url);

    el.title.textContent = hero.name || "Unnamed launch";
    el.net.textContent = isCoarse(hero) ? `NET ${fmtDate(hero.net)} (${hero.net_precision || "TBD"})` : `NET ${fmtUTC(hero.net, true)}`;
    el.provider.textContent = hero.provider || "—";
    el.vehicle.textContent = hero.vehicle || "—";
    el.pad.textContent = [hero.pad && hero.pad.name, hero.pad && hero.pad.location].filter(Boolean).join(" · ") || "—";
    const orbit = hero.mission && hero.mission.orbit;
    el.orbit.textContent = orbit && (orbit.abbrev || orbit.name)
      ? `${orbit.abbrev || ""}${orbit.abbrev && orbit.name ? " · " : ""}${orbit.name || ""}` : "—";
    el.mission.textContent = [hero.mission && hero.mission.name, hero.mission && hero.mission.type].filter(Boolean).join(" · ") || "—";
    el.window.textContent = hero.window_start && hero.window_end
      ? `${fmtUTC(hero.window_start)} → ${fmtUTC(hero.window_end)}` : "—";

    // WATCH LIVE: only when there is somewhere to send the viewer.
    const url = hero.webcast_url;
    if (url && /^https?:\/\//i.test(url)) {
      el.watch.hidden = false;
      el.watch.href = url;
      const live = hero.webcast_live === true;
      el.watch.classList.toggle("scheduled", !live);
      el.watchText.textContent = live ? "WATCH LIVE" : "WEBCAST";
    } else {
      el.watch.hidden = true;
      el.watch.removeAttribute("href");
    }
  }

  function setBackground(node, url) {
    if (!url) {
      node.style.backgroundImage = "";
      node.classList.add("no-image");
      return;
    }
    const img = new Image();
    img.onload = () => { node.classList.remove("no-image"); node.style.backgroundImage = `url("${url}")`; };
    img.onerror = () => { node.style.backgroundImage = ""; node.classList.add("no-image"); };
    img.src = url;
  }

  function renderStrip(hero) {
    const b = state.board;
    const items = b ? [b.hero, ...(b.strip || [])].filter(Boolean) : [];
    // Show the API hero in the strip only while a manual selection is active.
    const visible = state.selectedId ? items : items.filter((l) => !hero || l.id !== hero.id);
    el.strip.replaceChildren();
    el.stripCount.textContent = visible.length ? `${visible.length} SCHEDULED` : "";
    for (const l of visible.slice(0, STRIP_LIMIT)) {
      const node = el.cardTpl.content.firstElementChild.cloneNode(true);
      node.dataset.id = l.id;
      node.classList.toggle("selected", hero && l.id === hero.id);
      const pill = node.querySelector(".pill");
      pill.textContent = statusLabel(l.status && l.status.abbrev);
      pill.dataset.status = statusKey(l.status && l.status.abbrev);
      node.querySelector(".card-when").textContent = isCoarse(l) ? `NET ${fmtDate(l.net)}` : fmtRelative(l.net);
      node.querySelector(".card-when").dataset.net = l.net;
      node.querySelector(".card-when").dataset.coarse = isCoarse(l) ? "1" : "";
      node.querySelector(".card-name").textContent = l.name || "Unnamed launch";
      node.querySelector(".card-meta").textContent = [l.provider, l.pad && l.pad.location].filter(Boolean).join(" · ");
      const img = node.querySelector(".card-img");
      if (l.image_url) img.style.backgroundImage = `url("${l.image_url}")`; else img.classList.add("no-image");
      node.addEventListener("click", () => { state.selectedId = l.id; render(); });
      el.strip.appendChild(node);
    }
  }

  el.autoBtn.addEventListener("click", () => { state.selectedId = null; render(); });

  // ---------------------------------------------------------------------------
  // the clock: driven entirely by the stored NET and the browser's clock
  let lastSecond = -1;
  function tick() {
    const now = Date.now();
    const sec = Math.floor(now / 1000);
    if (sec === lastSecond) return;
    lastSecond = sec;

    const d = new Date(now);
    el.utc.textContent = `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}:${pad2(d.getUTCSeconds())} UTC`;

    const hero = currentHero();
    if (!hero) {
      el.clock.textContent = "--:--:--";
      el.clock.classList.remove("word");
      el.clockLabel.textContent = "NEXT LAUNCH";
      el.clockSub.textContent = "";
      return;
    }

    const abbrev = hero.status && hero.status.abbrev;
    const net = new Date(hero.net).getTime();
    const diff = net - now;
    el.clockLabel.textContent = state.selectedId ? "SELECTED LAUNCH" : "NEXT LAUNCH";

    if (abbrev === "Hold") {
      word("HOLD", hero.holdreason ? hero.holdreason : `T− ${fmtHMS(Math.max(diff, 0))} · COUNTDOWN PAUSED`);
    } else if (abbrev === "In Flight") {
      word("IN FLIGHT", diff <= 0 ? `T+ ${fmtHMS(-diff)}` : "LIFTOFF");
    } else if (TERMINAL.has(abbrev)) {
      word(statusLabel(abbrev), `T+ ${fmtHMS(Math.max(-diff, 0))}${hero.failreason ? " · " + hero.failreason : ""}`);
    } else if (isCoarse(hero)) {
      word(`NET ${fmtDate(hero.net)}`, `DATE PRECISION: ${hero.net_precision || "UNKNOWN"} · NO T-0 YET`);
    } else if (diff > 0) {
      digits(`T− ${fmtHMS(diff)}`, "");
    } else {
      digits(`T+ ${fmtHMS(-diff)}`, "AWAITING STATUS UPDATE");
    }

    // keep strip relative times fresh without re-rendering cards
    for (const when of el.strip.querySelectorAll(".card-when")) {
      if (!when.dataset.coarse && when.dataset.net) when.textContent = fmtRelative(when.dataset.net);
    }
  }

  function word(text, sub) {
    el.clock.textContent = text;
    el.clock.classList.add("word");
    el.clockSub.textContent = sub || "";
  }
  function digits(text, sub) {
    el.clock.textContent = text;
    el.clock.classList.remove("word");
    el.clockSub.textContent = sub || "";
  }

  function loop() { tick(); requestAnimationFrame(loop); }

  // ---------------------------------------------------------------------------
  refresh();
  requestAnimationFrame(loop);
})();
