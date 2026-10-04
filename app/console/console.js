"use strict";
/* ForgeAPI developer portal: static, dependency-free. Every API string is rendered with
   textContent (never as markup). The access token lives only in the `token` variable below. */
(function () {
  const OP_STATES = ["queued", "planning", "planned", "apply_queued", "applying", "succeeded", "failed", "uncertain"];
  const LIVE_OPS = ["queued", "planning", "planned", "apply_queued", "applying"];
  const DONE_OPS = ["succeeded", "failed", "uncertain"];
  const RES_STATES = ["pending", "ready", "destroyed"];
  const ROUTES = ["home", "infrastructure", "fleet", "zones", "jobs", "history", "catalog", "apps", "rollouts", "resources", "operations", "teams", "deploy"];
  const ALIASES = { overview: "home", patterns: "catalog" };
  const PAGE_CAP = 20;

  let token = null;
  let gen = 0;
  let refresher = null;
  let confirming = null;
  let approveFlash = null;
  let failoverNote = null;
  const actionKeys = new Map();
  const ui = { env: "", zone: null, mine: true, group: "project", q: "", rstate: "live" };
  let cache = {};

  const view = document.getElementById("view");
  const banner = document.getElementById("banner");

  /* ---------- DOM helpers ---------- */
  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (v === true) el.setAttribute(k, "");
      else el.setAttribute(k, String(v));
    }
    add(el, kids);
    return el;
  }
  function add(el, kids) {
    for (const kid of kids) {
      if (kid === null || kid === undefined || kid === false) continue;
      if (Array.isArray(kid)) add(el, kid);
      else if (kid instanceof Node) el.appendChild(kid);
      else el.appendChild(document.createTextNode(String(kid)));
    }
  }
  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }
  function fill(el, ...nodes) { clear(el); add(el, nodes); }
  function mount(...nodes) { fill(view, ...nodes); }
  const cls = (s) => String(s == null ? "unknown" : s).replace(/[^a-z_]/g, "") || "unknown";
  const enc = encodeURIComponent;
  const text = (v) => (v === null || v === undefined ? "" : typeof v === "string" ? v : JSON.stringify(v));
  const dash = () => h("span", { class: "muted", text: "-" });
  const num = (v) => (typeof v === "number" && isFinite(v) ? v : null);
  const sum = (list, f) => list.reduce((a, x) => a + (num(f(x)) || 0), 0);

  function pill(state) { return h("span", { class: "pill st-" + cls(state), text: String(state == null ? "unknown" : state).replace("_", " ") }); }
  function chips(list, extra) {
    return h("div", { class: "chips" }, (list || []).map((x) => h("span", { class: "chip " + (extra || ""), text: String(x) })));
  }
  const CLOUD_NAMES = { aws: "AWS", azure: "Azure", gcp: "GCP" };
  function cloudBadge(cloud) {
    if (!cloud) return dash();
    return h("span", { class: "cloud cloud-" + cls(cloud), text: CLOUD_NAMES[cloud] || String(cloud) });
  }
  function kv(rows) {
    const dl = h("dl", { class: "kv" });
    for (const [k, v] of rows) {
      if (v === null || v === undefined || v === "" || v === false) continue;
      dl.appendChild(h("dt", { text: k }));
      dl.appendChild(h("dd", null, v));
    }
    return dl;
  }
  function fmtTime(ts) {
    const d = new Date(ts);
    return isNaN(d) ? text(ts) : d.toLocaleString();
  }
  function ageText(ts) {
    const t = Date.parse(ts);
    if (isNaN(t)) return "";
    const s = Math.max(0, Math.round((Date.now() - t) / 1000));
    if (s < 5) return "just now";
    if (s < 60) return s + "s ago";
    if (s < 3600) return Math.floor(s / 60) + "m ago";
    if (s < 86400) return Math.floor(s / 3600) + "h ago";
    if (s < 86400 * 60) return Math.floor(s / 86400) + "d ago";
    return Math.floor(s / 86400 / 30) + "mo ago";
  }
  function when(ts) { return ts ? h("span", { class: "nowrap", title: fmtTime(ts), text: ageText(ts) }) : dash(); }
  function untilText(ts) {
    const t = Date.parse(ts);
    if (isNaN(t)) return "";
    const s = Math.round((t - Date.now()) / 1000);
    if (s <= 0) return "expired";
    if (s < 3600) return "in " + Math.max(1, Math.floor(s / 60)) + "m";
    return "in " + Math.floor(s / 3600) + "h";
  }
  function money(v) {
    const n = num(v);
    return n === null ? "-" : n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function cost(v) {
    const n = num(v);
    return n === null ? h("span", { class: "muted", title: "No estimate reported", text: "-" }) : h("span", { class: "nowrap", text: money(n) });
  }
  function dayLabel(ts) {
    const d = new Date(ts);
    if (isNaN(d)) return "Unknown date";
    const a = new Date(); a.setHours(0, 0, 0, 0);
    const b = new Date(d); b.setHours(0, 0, 0, 0);
    const diff = Math.round((a - b) / 86400000);
    if (diff === 0) return "Today";
    if (diff === 1) return "Yesterday";
    return d.toLocaleDateString(undefined, { weekday: "short", year: "numeric", month: "short", day: "numeric" });
  }
  function table(headers, rows, emptyText) {
    if (!rows.length) return h("div", { class: "scroll" }, empty(emptyText || "Nothing to show."));
    return h("div", { class: "scroll" }, h("table", null,
      h("thead", null, h("tr", null, headers.map((x) => h("th", { scope: "col", text: x })))),
      h("tbody", null, rows)));
  }
  function empty(title, hint) {
    return h("div", { class: "empty" }, h("b", { text: title }), hint ? h("div", { class: "muted", text: hint }) : null);
  }
  function skeleton(rows) {
    const out = h("div", { class: "skel-wrap", "aria-busy": "true", "aria-label": "Loading" });
    for (let i = 0; i < (rows || 4); i++) out.appendChild(h("div", { class: "skel" }));
    return out;
  }
  function segmented(options, current, onPick, label) {
    return h("div", { class: "seg", role: "group", "aria-label": label }, options.map(([v, t]) =>
      h("button", { type: "button", class: "seg-b", "aria-pressed": v === current ? "true" : "false", text: t, onclick: () => onPick(v) })));
  }
  function meter(reserved, limit, available, label) {
    const lim = num(limit) || 0;
    const res = num(reserved) || 0;
    const frac = lim > 0 ? Math.min(1, res / lim) : 1;
    const fillEl = h("div", { class: "meter-fill" + (frac >= 1 ? " full" : frac >= 0.8 ? " hot" : "") });
    fillEl.style.width = Math.round(frac * 100) + "%";
    return h("div", { class: "budget" },
      h("div", { class: "meter", role: "meter", "aria-valuemin": 0, "aria-valuemax": lim, "aria-valuenow": res, "aria-label": label }, fillEl),
      h("div", { class: "muted small", text: "reserved " + res.toLocaleString() + " of " + lim.toLocaleString() + " | " + (num(available) === null ? "" : "available " + available.toLocaleString()) + (lim > 0 ? " | " + Math.round(frac * 100) + "% used" : "") }));
  }
  function miniSummary(s) {
    if (!s) return null;
    const parts = [["+", s.create, "b-create"], ["~", s.update, "b-update"], ["-", s.delete, "b-delete"], ["replace ", s.replace, "b-replace"]];
    const shown = parts.filter((p) => p[1]);
    if (!shown.length) return h("span", { class: "muted small", text: "no changes" });
    return h("span", { class: "mini" }, shown.map((p) => h("span", { class: p[2], text: p[0] + p[1] })), s.destructive ? h("span", { class: "tag bad", text: "destructive" }) : null);
  }
  function section(title, ...kids) {
    return h("section", { class: "card" }, h("h2", { text: title }), kids);
  }

  /* ---------- API ---------- */
  class ApiError extends Error {
    constructor(status, body) { super("api"); this.status = status; this.body = body; }
  }
  function safePath(p) {
    if (typeof p !== "string" || p[0] !== "/" || p[1] === "/" || p.indexOf("\\") >= 0) throw new Error("unsafe link");
    return p;
  }
  async function api(path, opts) {
    const o = opts || {};
    let url = path;
    if (o.query) {
      const q = new URLSearchParams();
      for (const [k, v] of Object.entries(o.query)) if (v !== "" && v !== null && v !== undefined) q.set(k, String(v));
      const s = q.toString();
      if (s) url += "?" + s;
    }
    const headers = { Accept: "application/json" };
    if (o.key) headers["Idempotency-Key"] = o.key;
    if (o.ifMatch) headers["If-Match"] = o.ifMatch;
    if (token) headers.Authorization = "Bearer " + token;
    let body;
    if (o.body !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(o.body); }
    let res;
    try {
      res = await fetch(url, { method: o.method || "GET", headers, body, credentials: "omit", cache: "no-store" });
    } catch (e) {
      throw new ApiError(0, null);
    }
    let data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (res.status === 401) needToken();
    if (!res.ok) throw new ApiError(res.status, data);
    return o.withHeaders ? { data, etag: res.headers.get("ETag") } : data;
  }
  // Plain-language next steps for refusals a person can act on.
  const REASON_HINTS = {
    placement_stale: "The team's configuration changed while you were submitting. Check it again and resubmit.",
    team_archived: "This team is archived: new deployments are not accepted. Ask an operator.",
    app_member: "This resource belongs to an app. Change it through the app (failover, destroy) instead.",
    requester_access_revoked: "The person who started this no longer has access to the team.",
    revision_stale: "A team changed since your dry run. Run the dry run again, then apply.",
    expected_revisions_required: "Run a dry run first so the import knows which revisions it is changing.",
  };
  function describeError(e) {
    if (!(e instanceof ApiError)) return "Unexpected error in the portal.";
    if (e.status === 0) return "Cannot reach the API (network error).";
    if (e.status === 401) return "Authentication required (401). Open Access token above and paste a valid bearer token.";
    const err = e.body && e.body.error;
    if (!err) return "Request failed (" + e.status + ").";
    const parts = [e.status + " " + text(err.code)];
    if (err.reason) parts.push("reason: " + text(err.reason));
    if (err.detail !== undefined && err.detail !== null) parts.push(text(err.detail));
    if (err.next_action) parts.push("next: " + text(err.next_action));
    const hint = REASON_HINTS[err.reason];
    if (hint) parts.push(hint);
    return parts.join(" | ");
  }
  function errorBox(e, title) { return h("div", { class: "alert", role: "alert" }, h("h2", { text: title || "Request failed" }), h("div", { text: describeError(e) })); }
  function needToken() {
    document.getElementById("auth").open = true;
    banner.hidden = false;
    banner.textContent = "Authentication required (401). Paste a valid access token under Access token. The token stays in this page's memory only.";
  }
  function clearBanner() { banner.hidden = true; banner.textContent = ""; }
  async function pages(path, query, cursorParam, nextKey, cap) {
    const out = [];
    let cursor = null;
    let truncated = false;
    for (let i = 0; ; i++) {
      if (i >= (cap || PAGE_CAP)) { truncated = true; break; }
      const q = Object.assign({}, query);
      if (cursor) q[cursorParam] = cursor;
      const page = await api(path, { query: q });
      out.push(...(page.items || []));
      cursor = page[nextKey];
      if (!cursor) break;
    }
    return { items: out, truncated };
  }
  function cached(key, ttl, fn) {
    const c = cache[key];
    if (c && Date.now() - c.t < ttl) return c.p;
    const p = fn();
    cache[key] = { t: Date.now(), p };
    p.catch(() => { if (cache[key] && cache[key].p === p) delete cache[key]; });
    return p;
  }
  const loadAgent = (ttl) => cached("agent", ttl === undefined ? 30000 : ttl, () => api("/agent"));
  const loadResources = (ttl) => cached("resources", ttl === undefined ? 3000 : ttl, () => pages("/resources", { limit: 100 }, "after", "next_after", PAGE_CAP));
  async function loadJobs(withAttention) {
    const reqs = LIVE_OPS.map((s) => pages("/operations", { state: s, limit: 100 }, "before", "next_before", 5));
    reqs.push(api("/operations", { query: { limit: 50 } }));
    if (withAttention) {
      reqs.push(api("/operations", { query: { state: "failed", limit: 20 } }));
      reqs.push(api("/operations", { query: { state: "uncertain", limit: 20 } }));
    }
    const got = await Promise.all(reqs);
    const live = [];
    LIVE_OPS.forEach((s, i) => live.push(...got[i].items));
    const recent = got[LIVE_OPS.length].items || [];
    const attention = withAttention ? got[LIVE_OPS.length + 1].items.concat(got[LIVE_OPS.length + 2].items) : [];
    const byTime = (a, b) => String(b.created_at).localeCompare(String(a.created_at));
    return { live: live.sort(byTime), recent, attention: attention.sort(byTime) };
  }

  /* ---------- shared data helpers ---------- */
  const isMine = (r) => r.owned_by_caller !== false;
  const isLive = (r) => r.state !== "destroyed";
  const resName = (r) => (r.labels && r.labels.name) || r.pattern;
  const zoneName = (r) => (r.business_unit || "-") + " · " + (r.environment || "-");
  function inScope(r) {
    if (ui.env && r.environment !== ui.env) return false;
    if (ui.zone && (r.business_unit !== ui.zone.bu || r.environment !== ui.zone.env)) return false;
    return true;
  }
  function byIdMap(list) { const m = {}; for (const r of list) m[r.id] = r; return m; }
  function opVisible(op, resMap) {
    if (!ui.env && !ui.zone) return true;
    const r = resMap[op.resource_id];
    return Boolean(r) && inScope(r);
  }
  function opTitle(op, resMap) {
    const r = resMap[op.resource_id];
    return r ? resName(r) : op.resource_id;
  }
  function allEnvs(agent, resources) {
    const set = new Set();
    for (const u of (agent && agent.business_units) || []) for (const e of u.environments || []) set.add(e);
    for (const r of resources || []) if (r.environment) set.add(r.environment);
    return Array.from(set).sort();
  }
  function populateEnv(envs) {
    const sel = document.getElementById("env-filter");
    const want = ui.env;
    clear(sel);
    sel.appendChild(h("option", { value: "", text: "all environments" }));
    for (const e of envs) sel.appendChild(h("option", { value: e, text: e }));
    if (want && !envs.includes(want)) sel.appendChild(h("option", { value: want, text: want }));
    sel.value = want;
  }
  function syncZoneChip() {
    const chip = document.getElementById("zone-chip");
    chip.hidden = !ui.zone;
    document.getElementById("zone-chip-text").textContent = ui.zone ? "Zone: " + ui.zone.bu + " · " + ui.zone.env : "";
    document.getElementById("env-filter").value = ui.env;
  }
  function filterNote(extra) {
    const bits = [];
    if (ui.zone) bits.push("zone " + ui.zone.bu + " · " + ui.zone.env);
    else if (ui.env) bits.push("environment " + ui.env);
    if (extra) bits.push(extra);
    return bits.length ? h("p", { class: "muted small", text: "Filtered by " + bits.join(", ") + "." }) : null;
  }
  function truncNote(truncated, n) {
    return truncated ? h("p", { class: "muted small", text: "Showing first " + n + " items (page limit reached)." }) : null;
  }
  function noInfraEmpty() {
    return empty("No infrastructure yet", "Agents create it with POST /operations. Once an apply succeeds it shows up here.");
  }

  /* ---------- routing ---------- */
  function route() {
    const raw = (location.hash || "#home").slice(1);
    const [first, ...rest] = raw.split("/");
    let name = ALIASES[first] || first;
    if (!ROUTES.includes(name)) name = "home";
    let id = null;
    if (rest.length) { try { id = decodeURIComponent(rest.join("/")); } catch (e) { id = null; } }
    if (!id && name === "resources") name = "infrastructure";
    if (!id && name === "operations") name = "history";
    if (!id && name === "rollouts") name = "apps";
    return { name, id };
  }
  function render() {
    gen++;
    refresher = null;
    confirming = null;
    approveFlash = null;
    failoverNote = null;
    if (!fleet.running) { fleet.results = {}; fleet.total = 0; }
    lastCheck = null;
    actionKeys.clear();
    const r = route();
    const navKey = r.name === "resources" ? "infrastructure" : r.name === "operations" ? "history" : r.name === "rollouts" ? "apps" : r.name;
    for (const a of document.querySelectorAll("#nav a")) {
      if (a.dataset.route === navKey) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    }
    syncZoneChip();
    const g = gen;
    loadAgent(30000).then(syncTeamsNav, () => {});
    const run = {
      home: viewHome, deploy: viewDeploy, infrastructure: viewInfra, fleet: viewFleet, jobs: viewJobs, history: viewHistory,
      catalog: r.id ? viewPattern : viewPatterns, zones: r.id ? viewZone : viewZones, apps: r.id ? viewApp : viewApps, rollouts: viewRollout, resources: viewResource, operations: viewOperation, teams: viewTeams,
    }[r.name];
    mount(skeleton(5));
    Promise.resolve(run(r.id, g)).catch((e) => { if (g === gen) mount(errorBox(e)); });
    view.focus({ preventScroll: true });
  }
  window.addEventListener("hashchange", render);

  /* ---------- shared components ---------- */
  function resLink(r, id) {
    return h("a", { href: "#resources/" + enc(id), text: r ? resName(r) : id, title: id });
  }
  function jobCard(op, resMap) {
    const active = op.state !== "planned";
    const sub = [op.action, ageText(op.updated_at || op.created_at)];
    if (op.state === "planned" && op.plan_expires_at) sub.push("plan expires " + untilText(op.plan_expires_at));
    return h("div", { class: "job st-edge-" + cls(op.state) },
      h("div", { class: "job-top" }, pill(op.state), h("a", { class: "job-title", href: "#operations/" + enc(op.id), text: opTitle(op, resMap) })),
      h("div", { class: "muted small", text: op.pattern + (op.version ? "@" + op.version : "") + " | " + sub.filter(Boolean).join(" | ") }),
      op.state === "planned" ? [miniSummary(op.change_summary), h("a", { class: "btn primary sm", href: "#operations/" + enc(op.id), text: "Review plan" })] : null,
      active && LIVE_OPS.includes(op.state) ? h("div", { class: "bar-anim", "aria-hidden": "true" }, h("i")) : null,
      op.next_action && op.state !== "planned" ? h("div", { class: "muted small", text: "next: " + op.next_action }) : null);
  }
  function attentionCard(op, resMap) {
    const note = op.diagnostic || op.error || "";
    return h("div", { class: "job st-edge-" + cls(op.state) },
      h("div", { class: "job-top" }, pill(op.state), h("a", { class: "job-title", href: "#operations/" + enc(op.id), text: opTitle(op, resMap) }), h("span", { class: "muted small", text: ageText(op.updated_at || op.created_at) })),
      h("div", { class: "muted small", text: op.pattern + (op.version ? "@" + op.version : "") + " | " + op.action }),
      note ? h("pre", { class: "snippet", text: note.length > 220 ? note.slice(0, 220) + "..." : note }) : null);
  }
  function feedItem(op, resMap) {
    return h("li", { class: "feed-i" },
      pill(op.state),
      h("div", { class: "feed-main" },
        h("a", { href: "#operations/" + enc(op.id), text: op.action + " " + opTitle(op, resMap) }),
        h("div", { class: "muted small", text: op.pattern + (op.version ? "@" + op.version : "") })),
      miniSummary(op.change_summary),
      when(op.created_at));
  }

  /* ---------- home ---------- */
  function tile(label, value, sub, href, tone) {
    return h(href ? "a" : "div", { class: "tile" + (tone ? " tone-" + tone : ""), href: href || null },
      h("span", { class: "tile-v", text: String(value) }), h("span", { class: "tile-l", text: label }), sub ? h("span", { class: "muted small", text: sub }) : null);
  }
  async function viewHome(_id, g, silent) {
    const [agentR, resR, jobsR, roR] = await Promise.allSettled([loadAgent(silent ? 30000 : 5000), loadResources(silent ? 0 : 3000), loadJobs(true), loadRollouts(silent ? 0 : 3000)]);
    if (g !== gen) return;
    const out = [];
    const resources = resR.status === "fulfilled" ? resR.value.items : [];
    const resMap = byIdMap(resources);
    const mine = resources.filter((r) => isMine(r) && inScope(r));
    const liveMine = mine.filter(isLive);
    const jobs = jobsR.status === "fulfilled" ? jobsR.value : { live: [], recent: [], attention: [] };
    const liveOps = jobs.live.filter((o) => opVisible(o, resMap));
    const attention = jobs.attention.filter((o) => {
      const r = resMap[o.resource_id];
      return opVisible(o, resMap) && !(r && r.latest_operation_id && r.latest_operation_id !== o.id);
    }).slice(0, 8);
    const recent = jobs.recent.filter((o) => opVisible(o, resMap)).slice(0, 12);
    const fleetDrifted = liveMine.filter((r) => r.drift_status === "drifted").length;
    const fleetUpgrades = liveMine.filter((r) => r.upgrade_available === true).length;
    const totalCost = liveMine.some((r) => num(r.estimated_monthly_cost) !== null) ? sum(liveMine, (r) => r.estimated_monthly_cost) : null;
    if (agentR.status === "fulfilled") populateEnv(allEnvs(agentR.value, resources));

    out.push(h("div", { class: "page-head" }, h("h1", { text: "My infrastructure" }),
      h("span", { class: "muted small", text: "Updated " + new Date().toLocaleTimeString() })));
    const note = filterNote();
    if (note) out.push(note);
    if (resR.status === "rejected") out.push(errorBox(resR.reason, "Could not load infrastructure"));
    out.push(h("div", { class: "tiles" },
      tile("Ready", liveMine.filter((r) => r.state === "ready").length, "my resources", "#infrastructure", "ok"),
      tile("Pending", liveMine.filter((r) => r.state === "pending").length, "never applied", "#infrastructure", "warn"),
      tile("Jobs in flight", liveOps.length, liveOps.filter((o) => o.state === "planned").length + " awaiting approval", "#jobs", "run"),
      tile("Needs attention", attention.length, "failed or uncertain", attention.length ? "#history" : null, attention.length ? "bad" : ""),
      tile("Est. monthly cost", totalCost === null ? "-" : money(totalCost), "my live resources", "#infrastructure"),
      tile("Fleet health", fleetDrifted, fleetUpgrades + " upgrade" + (fleetUpgrades === 1 ? "" : "s") + " available", "#fleet", fleetDrifted ? "bad" : "ok")));

    const trend = spendTrend(agentR.status === "fulfilled" ? agentR.value : null, g);
    if (trend) out.push(trend);
    const active = (roR.status === "fulfilled" ? roR.value.items : []).filter((a) => inScope(a) && !ROLLOUT_DONE.includes(a.state));
    if (active.length) out.push(rolloutsPanel(active));
    const inflight = h("div", { class: "stack" });
    if (jobsR.status === "rejected") inflight.appendChild(errorBox(jobsR.reason, "Could not load jobs"));
    else if (!liveOps.length) inflight.appendChild(empty("Nothing running", "New work appears here the moment an agent calls POST /operations."));
    else for (const op of liveOps.slice(0, 8)) inflight.appendChild(jobCard(op, resMap));
    const attn = h("div", { class: "stack" });
    if (jobsR.status === "fulfilled") {
      if (!attention.length) attn.appendChild(empty("All clear", "No failed or uncertain operations on current resources."));
      else for (const op of attention) attn.appendChild(attentionCard(op, resMap));
    }
    out.push(h("div", { class: "grid2" },
      section("In progress now", inflight, liveOps.length > 8 ? h("a", { href: "#jobs", text: "See all " + liveOps.length + " jobs" }) : null),
      section("Needs attention", attn)));

    const units = agentR.status === "fulfilled" ? agentR.value.business_units || [] : [];
    const budgetRows = [];
    for (const u of units) for (const e of Object.keys(u.budgets || {}).sort()) {
      if (ui.env && e !== ui.env) continue;
      if (ui.zone && (ui.zone.bu !== u.name || ui.zone.env !== e)) continue;
      const b = u.budgets[e];
      budgetRows.push(h("div", { class: "budget-row" }, h("div", { class: "row" }, h("b", { text: u.name + " · " + e }),
        h("span", { class: "muted small", text: "available " + (num(b.available) === null ? "-" : b.available.toLocaleString()) })),
        meter(b.reserved, b.monthly_budget, b.available, "Budget reserved for " + u.name + " " + e)));
    }
    const clouds = {};
    for (const r of liveMine) { const c = r.cloud || "unknown"; clouds[c] = clouds[c] || { n: 0, cost: 0 }; clouds[c].n++; clouds[c].cost += num(r.estimated_monthly_cost) || 0; }
    const cloudNames = Object.keys(clouds).sort();
    const maxN = Math.max(1, ...cloudNames.map((c) => clouds[c].n));
    out.push(h("div", { class: "grid2" },
      section("Budget burn", budgetRows.length ? budgetRows : empty("No budgets", "Budgets appear per business unit and environment when tenancy defines them.")),
      section("Where it runs", cloudNames.length ? cloudNames.map((c) => {
        const bar = h("i"); bar.style.width = Math.round((clouds[c].n / maxN) * 100) + "%";
        return h("div", { class: "hbar" }, cloudBadge(c === "unknown" ? null : c), h("div", { class: "hbar-track" }, bar),
          h("span", { class: "small", text: clouds[c].n + " | " + money(clouds[c].cost) }));
      }) : empty("Nothing deployed", "Your live resources are broken down by cloud here."))));

    const feed = jobsR.status === "fulfilled"
      ? (recent.length ? h("ul", { class: "feed" }, recent.map((o) => feedItem(o, resMap))) : empty("No activity yet", "Operations created through the API are listed here."))
      : null;
    out.push(appsPanel(liveVisible(resources)));
    out.push(section("Recent activity", feed, h("a", { href: "#history", text: "Full history" })));
    if (!resources.length && resR.status === "fulfilled") out.push(noInfraEmpty());
    if (resR.status === "fulfilled") out.push(truncNote(resR.value.truncated, resources.length));
    mount(out);
    refresher = () => viewHome(null, gen, true).catch(() => {});
  }

  /* ---------- infrastructure ---------- */
  const GROUPS = [["project", "Project"], ["zone", "Landing zone"], ["cloud", "Cloud"], ["pattern", "Pattern"]];
  function groupKey(r) {
    if (ui.group === "zone") return zoneName(r);
    if (ui.group === "cloud") return r.cloud ? (CLOUD_NAMES[r.cloud] || r.cloud) : "Unknown cloud";
    if (ui.group === "pattern") return r.pattern;
    return (r.labels && r.labels.project) || "Ungrouped";
  }
  function matchesQuery(r, q) {
    if (!q) return true;
    const hay = [r.id, r.pattern, r.version, r.cloud, r.region, r.business_unit, r.environment, r.state]
      .concat(Object.entries(r.labels || {}).map(([k, v]) => k + "=" + v)).join(" ").toLowerCase();
    return q.toLowerCase().split(/\s+/).every((t) => hay.includes(t));
  }
  function objectsCell(r) {
    if (!Array.isArray(r.managed_objects)) return h("span", { class: "muted", title: "Not reported by this server", text: "?" });
    return h("span", { text: String(r.managed_objects.length) });
  }
  function resRow(r) {
    const go = () => { location.hash = "#resources/" + enc(r.id); };
    return h("tr", { class: "click", onclick: go },
      h("td", null, pill(r.state)),
      h("td", null, h("a", { href: "#resources/" + enc(r.id), text: resName(r), onclick: (e) => e.stopPropagation() }), " ", indicators(r),
        h("div", { class: "mono muted small", text: r.id + (r.version ? " | " + r.pattern + "@" + r.version : "") })),
      h("td", null, cloudBadge(r.cloud)),
      h("td", { text: r.region || "" }),
      h("td", { class: "nowrap", text: zoneName(r) }),
      h("td", { class: "num" }, cost(r.estimated_monthly_cost)),
      h("td", { class: "num" }, objectsCell(r)),
      h("td", null, when(r.created_at || r.updated_at)));
  }
  async function viewInfra(_id, g, silent) {
    const search = h("input", { type: "search", class: "search", placeholder: "Search name, label, region, id...", "aria-label": "Search infrastructure", value: ui.q, autocomplete: "off" });
    const ownerSeg = h("span"); const groupSeg = h("span"); const stateSel = h("select", { "aria-label": "State filter" },
      h("option", { value: "live", text: "live (hide destroyed)" }), h("option", { value: "all", text: "all states" }), RES_STATES.map((s) => h("option", { value: s, text: s })));
    stateSel.value = ui.rstate;
    const body = h("div", { class: "groups" }, skeleton(5));
    const head = h("div", { class: "page-head" }, h("h1", { text: "Infrastructure" }));
    const bar = h("div", { class: "bar" }, ownerSeg, h("label", { class: "inline" }, "Group by", groupSeg), stateSel, search);
    mount(head, bar, body);
    let data = null;
    function draw() {
      if (!data) return;
      const scoped = data.items.filter((r) => inScope(r));
      const nMine = scoped.filter(isMine).length;
      fill(ownerSeg, segmented([[true, "Mine (" + nMine + ")"], [false, "All (" + scoped.length + ")"]], ui.mine, (v) => { ui.mine = v; draw(); }, "Ownership"));
      fill(groupSeg, segmented(GROUPS, ui.group, (v) => { ui.group = v; draw(); }, "Group by"));
      const list = scoped.filter((r) => (!ui.mine || isMine(r)) && (ui.rstate === "all" ? true : ui.rstate === "live" ? isLive(r) : r.state === ui.rstate) && matchesQuery(r, ui.q.trim()));
      const groups = new Map();
      for (const r of list) { const k = groupKey(r); if (!groups.has(k)) groups.set(k, []); groups.get(k).push(r); }
      const keys = Array.from(groups.keys()).sort((a, b) => (a === "Ungrouped") - (b === "Ungrouped") || a.localeCompare(b));
      const out = [filterNote(), h("p", { class: "muted small", text: list.length + " resource" + (list.length === 1 ? "" : "s") + " | est. " + money(sum(list.filter(isLive), (r) => r.estimated_monthly_cost)) + " per month" })];
      if (!list.length) out.push(data.items.length ? empty("Nothing matches", "Try All instead of Mine, a different state, or clear the search.") : noInfraEmpty());
      for (const k of keys) {
        const rs = groups.get(k).sort((a, b) => resName(a).localeCompare(resName(b)));
        out.push(h("details", { class: "group", open: true },
          h("summary", null, h("b", { text: k }), h("span", { class: "muted", text: rs.length + " resource" + (rs.length === 1 ? "" : "s") }),
            h("span", { class: "grow" }), h("span", { class: "small", text: money(sum(rs.filter(isLive), (r) => r.estimated_monthly_cost)) + " per month" })),
          table(["State", "Name", "Cloud", "Region", "Zone", "Cost", "Objects", "Age"], rs.map(resRow))));
      }
      out.push(truncNote(data.truncated, data.items.length));
      fill(body, out);
    }
    async function load() {
      const [a, r] = await Promise.allSettled([loadAgent(30000), loadResources(silent ? 0 : 3000)]);
      if (g !== gen) return;
      if (r.status === "rejected") { fill(body, errorBox(r.reason)); return; }
      data = r.value;
      populateEnv(allEnvs(a.status === "fulfilled" ? a.value : null, data.items));
      draw();
    }
    search.addEventListener("input", () => { ui.q = search.value; draw(); });
    stateSel.addEventListener("change", () => { ui.rstate = stateSel.value; draw(); });
    refresher = () => { silent = true; load().catch(() => {}); };
    await load();
  }

  /* ---------- landing zones ---------- */
  async function viewZones(_id, g, silent) {
    const [agentR, resR] = await Promise.allSettled([loadAgent(silent ? 0 : 5000), loadResources(silent ? 0 : 3000)]);
    if (g !== gen) return;
    if (agentR.status === "rejected") { mount(errorBox(agentR.reason)); return; }
    const agent = agentR.value;
    const resources = resR.status === "fulfilled" ? resR.value.items : [];
    populateEnv(allEnvs(agent, resources));
    const units = agent.business_units || [];
    const out = [h("div", { class: "page-head" }, h("h1", { text: "Landing zones" }), h("span", { class: "muted small", text: "business unit · environment" }))];
    if (resR.status === "rejected") out.push(errorBox(resR.reason, "Could not load resources for zone totals"));
    const cards = [];
    const withCost = capOn(agent, "cost_history");
    for (const u of units) for (const env of u.environments || []) {
      if (ui.env && env !== ui.env) continue;
      const here = resources.filter((r) => r.business_unit === u.name && r.environment === env && isLive(r));
      const gr = (u.guardrails || {})[env];
      const b = (u.budgets || {})[env];
      const clouds = u.clouds && u.clouds[env];
      const open = () => { ui.zone = { bu: u.name, env }; ui.env = env; ui.mine = false; location.hash = "#infrastructure"; };
      cards.push(h("section", { class: "card zone" },
        h("div", { class: "zone-head" }, h("h2", { text: u.name }), h("span", { class: "zone-env", text: env })),
        h("div", { class: "zone-nums" },
          h("div", null, h("b", { text: String(here.length) }), h("span", { class: "muted small", text: " resources" })),
          h("div", null, h("b", { text: money(sum(here, (r) => r.estimated_monthly_cost)) }), h("span", { class: "muted small", text: " est. per month" }))),
        withCost ? sparkHost(u.name, env, g) : null,
        kv([
          ["Clouds", Array.isArray(clouds) && clouds.length ? h("div", { class: "chips" }, clouds.map((c) => cloudBadge(c))) : null],
          ["Default region", u.default_region],
          ["Allowed regions", u.regions && u.regions.length ? chips(u.regions) : null],
          ["Destroy", gr ? h("span", { class: "chip " + (gr.allow_destroy ? "on" : "off"), text: gr.allow_destroy ? "allowed" : "blocked" }) : null],
          ["Protected types", gr && gr.protected_resource_types && gr.protected_resource_types.length ? chips(gr.protected_resource_types) : null],
          ["Patterns", u.patterns && u.patterns.length ? chips(u.patterns) : null],
        ]),
        b ? [h("h2", { class: "sub", text: "Monthly budget" }), meter(b.reserved, b.monthly_budget, b.available, "Budget reserved for " + u.name + " " + env)] : null,
        h("div", { class: "row" },
          deployableNames(u, env).length ? h("button", { class: "btn primary", type: "button", text: "Deploy here", onclick: () => startDeploy({ bu: u.name, env }) }) : null,
          h("button", { class: "btn", type: "button", text: "View infrastructure here", onclick: open }),
          withCost ? h("a", { class: "btn", href: zoneHref(u.name, env), text: "Cost trend" }) : null)));
    }
    if (!units.length) out.push(empty("Tenancy is not enabled", "Without business units there are no landing zones. Set FORGEAPI_TENANTS_PATH to define them."));
    else if (!cards.length) out.push(empty("No zones match the environment filter"));
    else out.push(h("div", { class: "grid" }, cards));
    mount(out);
    refresher = () => viewZones(null, gen, true).catch(() => {});
  }

  /* ---------- jobs ---------- */
  const PHASES = [
    ["Queued", ["queued"]],
    ["Planning", ["planning"]],
    ["Awaiting approval", ["planned"]],
    ["Applying", ["apply_queued", "applying"]],
  ];
  async function viewJobs(_id, g) {
    const [resR, jobsR, agentR] = await Promise.allSettled([loadResources(3000), loadJobs(false), loadAgent(30000)]);
    if (g !== gen) return;
    if (jobsR.status === "rejected") { mount(errorBox(jobsR.reason)); return; }
    const resources = resR.status === "fulfilled" ? resR.value.items : [];
    populateEnv(allEnvs(agentR.status === "fulfilled" ? agentR.value : null, resources));
    const resMap = byIdMap(resources);
    const jobs = jobsR.value;
    const live = jobs.live.filter((o) => opVisible(o, resMap));
    const done = jobs.recent.filter((o) => DONE_OPS.includes(o.state) && opVisible(o, resMap)).slice(0, 12);
    const cols = PHASES.map(([title, states]) => {
      const items = live.filter((o) => states.includes(o.state));
      return h("section", { class: "col" },
        h("h2", null, title, " ", h("span", { class: "count", text: String(items.length) })),
        items.length ? items.map((o) => jobCard(o, resMap)) : h("div", { class: "muted small col-empty", text: "none" }));
    });
    mount(
      h("div", { class: "page-head" }, h("h1", { text: "Jobs" }), h("span", { class: "muted small", text: "Updated " + new Date().toLocaleTimeString() + " | auto-refresh every 5s while visible" })),
      filterNote(),
      !live.length ? empty("No jobs in flight", "Agents start work with POST /operations; planning, approval and apply show up as columns here.") : null,
      h("div", { class: "board" }, cols),
      h("section", { class: "card" }, h("h2", { text: "Recently finished" }),
        done.length ? h("ul", { class: "feed" }, done.map((o) => feedItem(o, resMap))) : empty("Nothing finished recently")));
    refresher = () => viewJobs(null, gen).catch(() => {});
  }

  /* ---------- history ---------- */
  const hist = { state: "", resource_id: "", action: "", pattern: "" };
  async function viewHistory(_id, g) {
    let cursor = null;
    let items = [];
    let loadedPages = 0;
    let resMap = {};
    const status = h("div", { class: "muted small", "aria-live": "polite" });
    const more = h("button", { class: "btn more", type: "button", text: "Load more", hidden: true });
    const list = h("div", { class: "days" });
    const stateSel = h("select", { "aria-label": "State" }, h("option", { value: "", text: "any state" }), OP_STATES.map((s) => h("option", { value: s, text: s })));
    stateSel.value = hist.state;
    const actionIn = h("input", { placeholder: "action", size: 10, value: hist.action, "aria-label": "Action" });
    const patternIn = h("input", { placeholder: "pattern", size: 14, value: hist.pattern, "aria-label": "Pattern" });
    const resIn = h("input", { placeholder: "res_...", size: 16, value: hist.resource_id, "aria-label": "Resource id" });
    const form = h("form", { class: "bar" }, h("label", { class: "inline" }, "State", stateSel), h("label", { class: "inline" }, "Action", actionIn),
      h("label", { class: "inline" }, "Pattern", patternIn), h("label", { class: "inline" }, "Resource", resIn), h("button", { class: "btn", type: "submit", text: "Apply" }));
    mount(h("div", { class: "page-head" }, h("h1", { text: "History" })), form, list, status, more);
    function visible() {
      return items.filter((o) => opVisible(o, resMap)
        && (!hist.action || String(o.action).toLowerCase().includes(hist.action.toLowerCase()))
        && (!hist.pattern || String(o.pattern).toLowerCase().includes(hist.pattern.toLowerCase())));
    }
    function draw() {
      const vis = visible();
      const out = [];
      let day = null;
      let ul = null;
      for (const op of vis) {
        const d = dayLabel(op.created_at);
        if (d !== day) { day = d; out.push(h("h2", { class: "day", text: d })); ul = h("ul", { class: "feed card-feed" }); out.push(ul); }
        ul.appendChild(h("li", { class: "feed-i" },
          pill(op.state),
          h("div", { class: "feed-main" },
            h("a", { href: "#operations/" + enc(op.id), text: op.action + " " + opTitle(op, resMap) }),
            h("div", { class: "muted small", text: op.pattern + (op.version ? "@" + op.version : "") + " | " + new Date(op.created_at).toLocaleTimeString() }),
            op.state === "failed" || op.state === "uncertain" ? h("div", { class: "small bad-t", text: (op.error || op.diagnostic || "").slice(0, 160) }) : null),
          miniSummary(op.change_summary),
          when(op.created_at)));
      }
      fill(list, out);
      more.hidden = !cursor;
      clear(status);
      if (!vis.length) status.appendChild(items.length ? empty("No loaded operations match", "Load more or loosen the filters.") : empty("No operations yet", "Operations appear when agents call POST /operations."));
      else status.appendChild(h("span", { text: "Showing " + vis.length + " of " + items.length + " loaded" + (loadedPages >= PAGE_CAP && cursor ? " (first " + items.length + " only, page limit reached)" : "") }));
    }
    async function load(reset) {
      if (reset) { cursor = null; items = []; loadedPages = 0; }
      try {
        const page = await api("/operations", { query: { limit: 50, state: hist.state, resource_id: hist.resource_id, before: cursor, action: hist.action === "drift_check" ? "drift_check" : "" } });
        if (g !== gen) return;
        items = items.concat(page.items || []);
        loadedPages++;
        cursor = loadedPages >= PAGE_CAP ? null : page.next_before;
        draw();
      } catch (e) {
        if (g !== gen) return;
        fill(status, errorBox(e));
      }
    }
    form.addEventListener("submit", (ev) => {
      ev.preventDefault();
      hist.state = stateSel.value; hist.action = actionIn.value.trim(); hist.pattern = patternIn.value.trim(); hist.resource_id = resIn.value.trim();
      load(true);
    });
    more.addEventListener("click", () => load(false));
    loadResources(3000).then((r) => { if (g === gen) { resMap = byIdMap(r.items); draw(); } }, () => {});
    refresher = () => load(true);
    await load(true);
  }

  /* ---------- operation detail ---------- */
  function summaryBadges(s) {
    if (!s) return h("span", { class: "muted", text: "no plan summary" });
    return h("div", { class: "row" },
      ["create", "update", "delete", "replace"].map((k) => h("span", { class: "badge" }, h("span", { class: "b-" + k, text: k }), h("b", { text: String(s[k]) }))),
      s.destructive ? h("span", { class: "badge destructive", text: "destructive" }) : null);
  }
  function changesTable(list) {
    const list2 = list || [];
    return table(["Address", "Type", "Actions", "Changed attributes", "Replace paths"], list2.map((c) => h("tr", null,
      h("td", { class: "mono nowrap", text: c.address }),
      h("td", { text: c.type }),
      h("td", { text: (c.actions || []).join(", ") }),
      h("td", { class: "mono", text: (c.changed_attributes || []).join(", ") }),
      h("td", { class: "mono", text: (c.replace_paths || []).join(", ") }))), "No changes recorded.");
  }
  function outputsBlock(outputs, withheld) {
    const keys = Object.keys(outputs || {}).sort();
    return h("div", null,
      keys.length ? kv(keys.map((k) => [k, h("span", { class: "mono", text: text(outputs[k]) })])) : h("span", { class: "muted", text: "no outputs" }),
      withheld && withheld.length ? h("div", { class: "row" }, h("span", { class: "muted small", text: "withheld (sensitive, names only):" }), chips(withheld)) : null);
  }
  async function fetchEvents(op) {
    return await pages(safePath(op.links.events), { limit: 100 }, "after", "next_after", PAGE_CAP);
  }
  async function viewOperation(id, g) {
    let state = null;
    async function load() {
      const op = await api(`/operations/${enc(id)}`);
      const events = await fetchEvents(op).then((x) => x, (e) => e);
      if (g !== gen) return;
      state = { op, events, flash: state ? state.flash : null };
      draw();
    }
    function setOp(op) { state.op = op; confirming = null; state.flash = null; draw(); load().catch(() => {}); }
    async function act(kind) {
      const op = state.op;
      try {
        const next = kind === "apply"
          ? await api(safePath(op.links.execute), { method: "POST", body: { plan_digest: op.plan_digest } })
          : await api(safePath(op.links.discard), { method: "POST" });
        if (g !== gen) return;
        cache = {};
        setOp(next);
      } catch (e) {
        if (g !== gen) return;
        state.flash = e;
        draw();
      }
    }
    function actions(op) {
      if (op.state !== "planned" || !op.links) return null;
      const canApply = Boolean(op.links.execute && op.plan_digest);
      const canDiscard = Boolean(op.links.discard);
      if (!canApply && !canDiscard) return null;
      if (confirming) {
        const apply = confirming === "apply";
        return h("div", { class: "confirm", role: "group", "aria-label": "Confirm" },
          h("b", { text: apply ? "Apply exactly this plan?" : "Discard this plan without applying?" }),
          summaryBadges(op.change_summary),
          apply && op.change_summary && op.change_summary.destructive ? h("div", { class: "b-delete", text: "This plan deletes or replaces resources." }) : null,
          apply ? h("div", { class: "mono muted", text: "digest " + op.plan_digest }) : null,
          h("div", { class: "row" },
            h("button", { class: "btn " + (apply ? "primary" : "danger"), type: "button", text: apply ? "Confirm apply" : "Confirm discard", onclick: () => act(confirming) }),
            h("button", { class: "btn", type: "button", text: "Cancel", onclick: () => { confirming = null; draw(); } })));
      }
      return h("div", { class: "row" },
        canApply ? h("button", { class: "btn primary", type: "button", text: "Apply this plan", onclick: () => { confirming = "apply"; state.flash = null; draw(); } }) : null,
        canDiscard ? h("button", { class: "btn", type: "button", text: "Discard plan", onclick: () => { confirming = "discard"; state.flash = null; draw(); } }) : null);
    }
    function draw() {
      const op = state.op;
      const out = [h("div", { class: "crumbs" }, h("a", { href: "#history", text: "History" }), " / ", h("span", { class: "mono", text: op.id }))];
      out.push(h("h1", null, pill(op.state), " ", op.pattern + (op.version ? "@" + op.version : ""), " ", h("span", { class: "muted", text: op.action })));
      if (state.flash) out.push(errorBox(state.flash));
      if (op.state === "failed" || op.state === "uncertain") {
        out.push(h("div", { class: "alert" + (op.state === "uncertain" ? " unc" : ""), role: "alert" },
          h("h2", { text: op.state === "uncertain" ? "Outcome uncertain: operator reconciliation required" : "Operation failed" }),
          op.error ? h("pre", { text: op.error }) : null,
          op.diagnostic ? [h("h2", { text: "Diagnostic" }), h("pre", { text: op.diagnostic })] : null));
      }
      const act1 = actions(op);
      out.push(h("section", { class: "card" }, h("h2", { text: "Operation" }),
        kv([
          ["State", pill(op.state)],
          ["Next action", op.next_action],
          ["Terminal", op.terminal ? "yes" : "no"],
          ["Resource", h("a", { class: "mono", href: "#resources/" + enc(op.resource_id), text: op.resource_id })],
          ["Pattern", op.pattern], ["Version", op.version], ["Commit", op.commit ? h("span", { class: "mono", text: op.commit }) : null],
          ["Created", fmtTime(op.created_at)], ["Updated", fmtTime(op.updated_at)],
          ["Plan expires", op.plan_expires_at ? fmtTime(op.plan_expires_at) + " (" + untilText(op.plan_expires_at) + ")" : null],
          ["Plan digest", op.plan_digest ? h("span", { class: "mono", text: op.plan_digest }) : null],
          ["Poll after", op.poll_after_seconds ? op.poll_after_seconds + "s" : null],
        ]), act1));
      if (op.action === "drift_check") {
        const found = Array.isArray(op.drift) ? op.drift : [];
        out.push(section("Drift result", op.state === "succeeded"
          ? (found.length ? [h("p", { class: "bad-t", text: found.length + " object" + (found.length === 1 ? "" : "s") + " changed outside the API."}), driftTable(found)]
            : h("p", { text: "In sync: nothing changed outside the API." }))
          : h("p", { class: "muted", text: DONE_OPS.includes(op.state) ? "No result: the check did not finish." : "Checking... this is a read-only refresh; nothing is applied." })));
      } else {
        out.push(section("Change summary", summaryBadges(op.change_summary)));
        out.push(section("Changes", changesTable(op.changes)));
        if (op.drift && op.drift.length) out.push(section("Drift", changesTable(op.drift)));
        out.push(section("Outputs", outputsBlock(op.outputs, op.withheld_outputs)));
      }
      const ev = state.events;
      out.push(section("Event timeline",
        ev instanceof Error || ev instanceof ApiError ? errorBox(ev) : ev.items.length
          ? [h("ol", { class: "timeline" }, ev.items.map((e) => h("li", null,
              h("span", { class: "muted", text: "#" + e.seq }),
              h("div", null, h("b", { text: e.action }), " ", h("span", { class: "pill st-" + cls(e.outcome), text: e.outcome }), " ",
                h("span", { class: "muted small", title: fmtTime(e.timestamp), text: e.actor + " | " + ageText(e.timestamp) }))))),
             truncNote(ev.truncated, ev.items.length)]
          : h("div", { class: "muted", text: "No events." })));
      mount(out);
    }
    refresher = () => { if (!confirming) load().catch(() => {}); };
    await load();
  }

  /* ---------- resource page ---------- */
  function labelChips(labels) {
    const keys = Object.keys(labels || {}).sort();
    return keys.length ? chips(keys.map((k) => k + "=" + labels[k])) : dash();
  }
  async function viewResource(id, g, silent) {
    const [rR, allR, opsR, agentR] = await Promise.allSettled([
      api(`/resources/${enc(id)}`),
      loadResources(silent ? 0 : 3000),
      pages("/operations", { resource_id: id, limit: 100, include_checks: "true" }, "before", "next_before", PAGE_CAP),
      loadAgent(30000),
    ]);
    if (g !== gen) return;
    if (rR.status === "rejected") { mount(errorBox(rR.reason)); return; }
    const r = rR.value;
    const all = allR.status === "fulfilled" ? allR.value.items : [];
    const resMap = byIdMap(all);
    const refs = Object.keys(r.input_refs || {}).sort();
    const usedBy = all.filter((x) => x.id !== r.id && Object.values(x.input_refs || {}).some((ref) => ref && ref.resource_id === r.id));
    const ops = opsR.status === "fulfilled" ? opsR.value.items : [];
    const latest = ops.find((o) => o.id === r.latest_operation_id) || ops[0];
    const owner = r.owned_by_caller === true ? "You" : r.owned_by_caller === false ? "Another member of " + (r.business_unit || "your unit") : null;
    const contractSlot = h("span", { class: "row" });
    const out = [
      h("div", { class: "crumbs" }, h("a", { href: "#infrastructure", text: "Infrastructure" }), " / ", h("span", { class: "mono", text: r.id })),
      h("div", { class: "res-head" },
        h("h1", null, pill(r.state), " ", resName(r)),
        h("div", { class: "row" }, cloudBadge(r.cloud), r.region ? h("span", { class: "chip", text: r.region }) : null,
          r.business_unit || r.environment ? h("span", { class: "chip", text: zoneName(r) }) : null,
          h("span", { class: "chip", text: r.pattern + (r.version ? "@" + r.version : "") }),
          driftBadge(r), upgradeBadge(r), contractSlot,
          r.labels && r.labels.app ? h("a", { class: "chip app-chip", href: "#apps/" + enc(r.labels.app), title: "Open the app this resource belongs to", text: "app: " + r.labels.app }) : null)),
    ];
    if (latest && (latest.state === "failed" || latest.state === "uncertain") && latest.id === r.latest_operation_id) {
      out.push(h("div", { class: "alert" + (latest.state === "uncertain" ? " unc" : ""), role: "alert" },
        h("h2", { text: "Latest operation " + latest.state }),
        latest.diagnostic || latest.error ? h("pre", { text: latest.diagnostic || latest.error }) : null,
        h("a", { href: "#operations/" + enc(latest.id), text: "Open operation" })));
    }
    out.push(h("div", { class: "tiles" },
      tile("Est. monthly cost", money(r.estimated_monthly_cost), num(r.estimated_monthly_cost) === null ? "no estimate reported" : "per month"),
      tile("Managed objects", Array.isArray(r.managed_objects) ? r.managed_objects.length : "?", Array.isArray(r.managed_objects) ? "in Terraform state" : "not reported"),
      tile("Operations", opsR.status === "fulfilled" ? ops.length : "?", "for this resource")));
    const agentV = agentR.status === "fulfilled" ? agentR.value : null;
    if (capOn(agentV, "pattern_checks") && r.pattern && r.version) addContractChip(contractSlot, r, g);
    if (isLive(r)) out.push(driftSection(r, g, capOn(agentV, "drift_checks")));
    if (r.upgrade_available === true && isLive(r)) out.push(upgradePanel(r, g, capOn(agentV, "resource_upgrade"), capOn(agentV, "pattern_changes")));
    if (isLive(r) && capOn(agentV, "promotion")) out.push(promotePanel(r, agentV, g));
    out.push(h("div", { class: "grid2" },
      section("Details", kv([
        ["Owner", owner], ["Business unit", r.business_unit], ["Environment", r.environment], ["Cloud", r.cloud ? (CLOUD_NAMES[r.cloud] || r.cloud) : null], ["Region", r.region],
        ["Commit", r.commit ? h("span", { class: "mono", text: r.commit }) : null],
        ["Created", r.created_at ? h("span", { title: fmtTime(r.created_at), text: fmtTime(r.created_at) + " (" + ageText(r.created_at) + ")" }) : null],
        ["Updated", r.updated_at ? h("span", { title: fmtTime(r.updated_at), text: fmtTime(r.updated_at) + " (" + ageText(r.updated_at) + ")" }) : null],
        ["Latest operation", r.latest_operation_id ? h("a", { class: "mono", href: "#operations/" + enc(r.latest_operation_id), text: r.latest_operation_id }) : null],
        ["Labels", labelChips(r.labels)],
      ])),
      section("Outputs", outputsBlock(r.outputs, r.withheld_outputs))));
    out.push(section("What actually exists", Array.isArray(r.managed_objects)
      ? table(["Address", "Type"], r.managed_objects.map((o) => h("tr", null, h("td", { class: "mono", text: o.address }), h("td", { text: o.type }))), "No managed objects: nothing is applied yet or it was destroyed.")
      : empty("Not reported", "This server does not list managed objects for the resource.")));
    out.push(h("div", { class: "grid2" },
      section("Uses", refs.length ? table(["Input", "Resource", "Output"], refs.map((k) => {
        const ref = r.input_refs[k];
        return h("tr", null, h("td", { text: k }), h("td", null, resLink(resMap[ref.resource_id], ref.resource_id)), h("td", { text: ref.output }));
      })) : h("span", { class: "muted", text: "No dependencies on other resources." })),
      section("Used by", allR.status === "rejected" ? errorBox(allR.reason) : usedBy.length ? table(["Resource", "State"], usedBy.map((x) =>
        h("tr", null, h("td", null, resLink(x, x.id)), h("td", null, pill(x.state))))) : h("span", { class: "muted", text: "Nothing depends on this resource." }))));
    out.push(section("Operation history", opsR.status === "rejected" ? errorBox(opsR.reason) : ops.length
      ? [h("ol", { class: "timeline ops" }, ops.map((o) => h("li", null,
          h("span", { class: "dot st-" + cls(o.state) }),
          h("div", null, h("div", { class: "row" }, pill(o.state), h("a", { href: "#operations/" + enc(o.id), text: o.action + " " + o.pattern + (o.version ? "@" + o.version : "") }), miniSummary(o.change_summary), when(o.created_at)),
            o.error ? h("div", { class: "small bad-t", text: o.error.slice(0, 200) }) : null)))),
         truncNote(opsR.value.truncated, ops.length)]
      : empty("No operations recorded")));
    mount(out);
    refresher = () => { if (!confirming) viewResource(id, gen, true).catch(() => {}); };
  }


  /* ---------- fleet: drift, upgrades, promotion ---------- */
  const STALE_MS = 7 * 86400000;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const capOn = (agent, name) => Boolean(agent && agent.capabilities && agent.capabilities[name]);
  const fleet = { results: {}, running: false, done: 0, total: 0 };
  let lastCheck = null;
  const checkable = (r) => isLive(r) && r.state === "ready";
  const driftStale = (r) => { const t = Date.parse(r.drift_checked_at); return isNaN(t) || Date.now() - t > STALE_MS; };
  function driftBadge(r) {
    if (!r.drift_status) return null;
    return h("span", { class: "pill st-" + cls(r.drift_status), text: r.drift_status.replace("_", " ") });
  }
  function upgradeBadge(r) {
    if (r.upgrade_available !== true) return null;
    return h("span", { class: "pill st-upgrade", text: "v" + String(r.version || "?").replace(/^v/, "") + " → v" + String(r.latest_version || "?").replace(/^v/, "") + " available" });
  }
  // An object Terraform would recreate (delete in resource_drift) is gone; update-only drift is
  // a changed attribute, often provider normalisation. Show them differently.
  function driftKind(r) {
    if (r.drift_status !== "drifted") return null;
    return (r.drift || []).some((d) => (d.actions || []).includes("delete")) ? "missing" : "changed";
  }
  function driftPill(r) {
    const kind = driftKind(r);
    if (!kind) return null;
    return h("span", { class: "pill " + (kind === "missing" ? "st-failed" : "st-planned"), title: kind === "missing" ? "An object no longer exists" : "Attributes changed outside the API", text: kind });
  }
  function indicators(r) {
    const out = [];
    const kind = driftKind(r);
    if (kind === "missing") out.push(h("span", { class: "ind bad", title: "Drifted: an object no longer exists", "aria-label": "drift: missing", text: "⚠ missing" }));
    if (kind === "changed") out.push(h("span", { class: "ind warn", title: "Drifted: attributes changed outside the API", "aria-label": "drift: changed", text: "~ changed" }));
    if (r.upgrade_available === true) out.push(h("span", { class: "ind info", title: "Upgrade available: " + text(r.latest_version), "aria-label": "upgrade available", text: "↑ " + text(r.latest_version) }));
    return out;
  }
  function addrChips(list) {
    const items = Array.isArray(list) ? list : [];
    const shown = items.slice(0, 4).map((c) => h("span", { class: "chip mono", title: (c.actions || []).join(", "), text: c.address }));
    if (items.length > 4) shown.push(h("span", { class: "muted small", text: "+" + (items.length - 4) + " more" }));
    return h("div", { class: "chips" }, shown);
  }
  function driftTable(list) {
    return table(["Address", "Type", "Actions"], (list || []).map((c) => h("tr", null,
      h("td", { class: "mono nowrap", text: c.address }), h("td", { text: c.type }), h("td", { text: (c.actions || []).join(", ") }))), "Nothing drifted.");
  }
  /* one POST per call; the key is dropped afterwards so a later click is a new check */
  async function postCheck(r) {
    const name = "drift:" + r.id;
    try {
      return await api(`/resources/${enc(r.id)}/drift-check`, { method: "POST", key: actionKey(name) });
    } finally {
      actionKeys.delete(name);
      cache = {};
    }
  }
  async function viewFleet(_id, g, silent) {
    const [agentR, resR] = await Promise.allSettled([loadAgent(silent ? 0 : 5000), loadResources(silent ? 0 : 3000)]);
    if (g !== gen) return;
    if (resR.status === "rejected") { mount(errorBox(resR.reason, "Could not load infrastructure")); return; }
    const agent = agentR.status === "fulfilled" ? agentR.value : null;
    const data = resR.value;
    populateEnv(allEnvs(agent, data.items));
    const body = h("div", { class: "stack" });
    mount(h("div", { class: "page-head" }, h("h1", { text: "Fleet" }), h("span", { class: "muted small", text: "drift and upgrades across your resources" })), body);
    function resultNode(r) {
      const x = fleet.results[r.id];
      if (!x) return null;
      if (x.state === "error") return h("div", { class: "small bad-t", text: x.msg });
      if (x.state === "running") return h("div", { class: "muted small", text: "starting..." });
      return h("a", { class: "small", href: "#operations/" + enc(x.op), text: "check queued: open operation" });
    }
    function checkCell(r, label) {
      return h("td", null, h("button", { class: "btn sm", type: "button", text: label, disabled: fleet.running, onclick: () => single(r) }), resultNode(r));
    }
    async function single(r) {
      fleet.results[r.id] = { state: "running" };
      draw();
      await runOne(r);
      draw();
    }
    async function runOne(r) {
      try {
        const op = await postCheck(r);
        fleet.results[r.id] = { state: "queued", op: op.id };
      } catch (e) {
        fleet.results[r.id] = { state: "error", msg: describeError(e) };
      }
    }
    async function bulk(list) {
      fleet.running = true; fleet.done = 0; fleet.total = list.length;
      draw();
      for (const r of list) {
        if (g !== gen) break;
        fleet.results[r.id] = { state: "running" };
        draw();
        await runOne(r);
        fleet.done++;
        draw();
      }
      fleet.running = false;
      if (g === gen) draw();
    }
    function draw() {
      if (g !== gen) return;
      const all = data.items.filter((r) => inScope(r) && checkable(r));
      const drifted = all.filter((r) => r.drift_status === "drifted");
      const unknown = all.filter((r) => r.drift_status === "unknown");
      const never = all.filter((r) => !r.drift_status);
      const checked = all.filter((r) => r.drift_status);
      const upgrades = data.items.filter((r) => inScope(r) && isLive(r) && r.upgrade_available === true);
      const stale = all.filter((r) => r.drift_status !== "drifted" && (driftStale(r) || r.drift_status === "unknown"));
      const times = checked.map((r) => Date.parse(r.drift_checked_at)).filter((t) => !isNaN(t));
      const last = times.length ? new Date(Math.max(...times)).toISOString() : null;
      const out = [filterNote()];
      if (agent && !capOn(agent, "drift_checks")) out.push(h("p", { class: "muted small", text: "This server does not report drift checks (capability drift_checks is off)." }));
      out.push(h("div", { class: "tiles" },
        tile("Resources checked", checked.length, "of " + all.length + " ready", null, "ok"),
        tile("Missing", drifted.filter((r) => driftKind(r) === "missing").length, "objects gone outside the API", null, drifted.some((r) => driftKind(r) === "missing") ? "bad" : "ok"),
        tile("Changed", drifted.filter((r) => driftKind(r) === "changed").length, "attributes changed outside the API", null, drifted.some((r) => driftKind(r) === "changed") ? "warn" : "ok"),
        tile("Unknown", unknown.length, "last check failed", null, unknown.length ? "warn" : ""),
        tile("Never checked", never.length, "no drift verdict yet", null, never.length ? "warn" : ""),
        tile("Upgrades available", upgrades.length, "newer pattern version", null, upgrades.length ? "run" : ""),
        tile("Last check", last ? ageText(last) : "-", last ? fmtTime(last) : "no checks yet")));
      drifted.sort((a, b) => (driftKind(a) === "missing" ? 0 : 1) - (driftKind(b) === "missing" ? 0 : 1));
      out.push(section("Drifted", table(["Resource", "Zone", "Kind", "Drifted objects", "Checked", "Check"], drifted.map((r) => h("tr", null,
        h("td", null, resLink(r, r.id)), h("td", { class: "nowrap", text: zoneName(r) }), h("td", null, driftPill(r)), h("td", null, addrChips(r.drift)), h("td", null, when(r.drift_checked_at)),
        checkCell(r, "Check again"))), "Nothing has drifted.")));
      out.push(section("Upgrades available", table(["Resource", "Zone", "Version", ""], upgrades.map((r) => h("tr", null,
        h("td", null, resLink(r, r.id)), h("td", { class: "nowrap", text: zoneName(r) }),
        h("td", null, upgradeBadge(r)),
        h("td", null, h("a", { class: "btn sm", href: "#resources/" + enc(r.id), text: "Plan upgrade" })))), "Everything is on the latest version.")));
      const progress = fleet.running || fleet.total ? h("span", { class: "muted small", role: "status", text: (fleet.running ? "Checking " : "Checked ") + fleet.done + " of " + fleet.total }) : null;
      out.push(section("Never or long unchecked",
        h("p", { class: "muted small", text: "Never checked, last check failed, or not checked in 7 days." }),
        h("div", { class: "row" },
          h("button", { class: "btn primary", type: "button", disabled: fleet.running || !stale.length, text: "Check all visible (" + stale.length + ")", onclick: () => bulk(stale.slice()) }), progress),
        table(["Resource", "Zone", "Status", "Last checked", "Check"], stale.map((r) => h("tr", null,
          h("td", null, resLink(r, r.id)), h("td", { class: "nowrap", text: zoneName(r) }),
          h("td", null, driftBadge(r) || h("span", { class: "muted", text: "never checked" })), h("td", null, when(r.drift_checked_at)),
          checkCell(r, "Check now"))), "Every ready resource has a recent check.")));
      out.push(truncNote(data.truncated, data.items.length));
      fill(body, out);
    }
    draw();
    refresher = () => { if (!fleet.running) viewFleet(null, gen, true).catch(() => {}); };
  }
  function driftSection(r, g, canCheck) {
    const note = h("div", { class: "stack" });
    const btn = h("button", { class: "btn primary", type: "button", text: "Check drift now", disabled: !(r.state === "ready" && canCheck) });
    function showCheck() {
      if (!lastCheck || lastCheck.id !== r.id) { clear(note); return; }
      fill(note, h("div", { class: "row" }, pill(lastCheck.state), h("a", { href: "#operations/" + enc(lastCheck.op), text: "Open drift_check operation" }),
        lastCheck.state !== "succeeded" && lastCheck.state !== "failed" && lastCheck.state !== "uncertain" ? h("span", { class: "muted small", text: "this page refreshes when it finishes" }) : null));
    }
    async function watch(op) {
      for (let i = 0; i < 90 && g === gen; i++) {
        lastCheck = { id: r.id, op: op.id, state: op.state };
        showCheck();
        if (op.terminal || DONE_OPS.includes(op.state)) { cache = {}; viewResource(r.id, g, true).catch(() => {}); return; }
        await sleep(2000);
        if (g !== gen) return;
        try { op = await api(`/operations/${enc(op.id)}`); } catch (e) { return; }
      }
    }
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      fill(note, h("span", { class: "muted small", text: "Starting drift check..." }));
      try {
        const op = await postCheck(r);
        if (g !== gen) return;
        watch(op);
      } catch (e) {
        if (g !== gen) return;
        fill(note, errorBox(e, "Drift check refused"));
        btn.disabled = false;
      }
    });
    showCheck();
    if (lastCheck && lastCheck.id === r.id && !DONE_OPS.includes(lastCheck.state)) btn.disabled = true;
    const items = Array.isArray(r.drift) ? r.drift : [];
    return section("Drift",
      h("div", { class: "row" }, driftBadge(r) || h("span", { class: "muted", text: "never checked" }),
        r.drift_checked_at ? h("span", { class: "muted small", title: fmtTime(r.drift_checked_at), text: "checked " + ageText(r.drift_checked_at) }) : null, btn),
      note,
      r.drift_status === "drifted" ? [h("h2", { class: "sub", text: "Changed outside the API" }), driftTable(items)]
        : r.drift_status === "unknown" ? h("p", { class: "muted small", text: "The last check failed; run it again." })
        : r.drift_status === "in_sync" ? h("p", { class: "muted small", text: "Live state matches what the API applied." }) : null);
  }
  function upgradePanel(r, g, canUpgrade, canChanges) {
    const body = h("div", { class: "stack" });
    let blockUpgrade = () => {};  // set below once the button exists
    const changes = canChanges && r.version && r.latest_version
      ? changesBlock({ name: r.pattern, from: r.version, to: r.latest_version, bu: r.business_unit, env: r.environment,
          onLoaded: (c) => blockUpgrade(Array.isArray(c.new_required_inputs) ? c.new_required_inputs : []) }, g) : null;
    const sec = section("Upgrade available", h("div", { class: "row" }, upgradeBadge(r)), changes, body);
    if (canUpgrade) {
      const flash = h("div", { class: "stack" });
      const btn = h("button", { class: "btn primary", type: "button", text: "Plan upgrade" });
      btn.addEventListener("click", async () => {
        const name = "upgrade:" + r.id + ":" + r.latest_version;
        btn.disabled = true;
        try {
          const op = await api(safePath(r.links.self) + "/" + "upgrade", { method: "POST", key: actionKey(name), body: { version: r.latest_version } });
          actionKeys.delete(name);
          cache = {};
          if (g === gen) location.hash = "#operations/" + enc(op.id);
        } catch (e) {
          if (g !== gen) return;
          fill(flash, errorBox(e, "Upgrade refused"));
          btn.disabled = false;
        }
      });
      const why = h("p", { class: "muted small" });
      // The server would refuse an upgrade missing new required inputs; don't offer the click.
      blockUpgrade = (needed) => {
        if (!needed.length) return;
        btn.disabled = true;
        why.textContent = "Disabled: v" + String(r.latest_version).replace(/^v/, "") + " needs new inputs (" + needed.join(", ") + ") that this resource does not have.";
      };
      const known = changesMemo.get(changesKey({ name: r.pattern, from: r.version, to: r.latest_version, bu: r.business_unit, env: r.environment }));
      if (known) blockUpgrade(Array.isArray(known.new_required_inputs) ? known.new_required_inputs : []);
      fill(body,
        h("p", { class: "muted small", text: "Plans " + r.pattern + "@" + text(r.latest_version) + " with this resource's stored inputs. The plan is reviewed before anything is applied." }),
        h("div", { class: "row" }, btn), why, flash);
      return sec;
    }
    fill(body, h("span", { class: "muted small", text: "Loading the pattern's inputs..." }));
    api(`/patterns/${enc(r.pattern)}`, { query: { version: r.latest_version, business_unit: r.business_unit, environment: r.environment } }).then((p) => {
      if (g !== gen) return;
      const schema = p.input_schema || {};
      const req = Array.isArray(schema.required) ? schema.required : [];
      const payload = { action: "deploy", resource_id: r.id, pattern: r.pattern, version: r.latest_version, business_unit: r.business_unit, environment: r.environment, inputs: {} };
      if (r.labels && Object.keys(r.labels).length) payload.labels = r.labels;
      if (r.input_refs && Object.keys(r.input_refs).length) payload.input_refs = r.input_refs;
      fill(body,
        h("p", { class: "muted small", text: "This server cannot plan an upgrade from the portal. An agent or the CLI sends POST /operations with the new version and the resource's current inputs. The plan is reviewed before anything is applied." }),
        h("pre", { text: JSON.stringify(payload, null, 2) }),
        h("div", { class: "muted small", text: "Fill inputs with the resource's current values" + (req.length ? " (required: " + req.join(", ") + ")." : ".") }));
    }, (e) => { if (g === gen) fill(body, errorBox(e, "Could not load the pattern")); });
    return sec;
  }
  function promotePanel(r, agent, g) {
    const unit = ((agent && agent.business_units) || []).find((u) => u.name === r.business_unit);
    const targets = ((unit && unit.environments) || []).filter((e) => e !== r.environment);
    if (!targets.length) return section("Promote", h("p", { class: "muted small", text: "No other environment is available to you in " + (r.business_unit || "this business unit") + "." }));
    const sel = h("select", { "aria-label": "Target environment" }, targets.map((e) => h("option", { value: e, text: e })));
    const flash = h("div", { class: "stack" });
    const row = h("div", { class: "row" });
    function step1() {
      confirming = null;
      fill(row, h("label", { class: "inline" }, "Promote to", sel), h("button", { class: "btn primary", type: "button", text: "Promote...", onclick: step2 }));
    }
    function step2() {
      const env = sel.value;
      confirming = "promote";
      fill(row, h("b", { text: "Promote " + resName(r) + " from " + r.environment + " to " + env + "?" }),
        h("span", { class: "muted small", text: "This plans a deployment; nothing is applied until you review and approve it." }),
        h("button", { class: "btn primary", type: "button", text: "Confirm promote", onclick: () => go(env) }),
        h("button", { class: "btn", type: "button", text: "Cancel", onclick: () => { clear(flash); step1(); } }));
    }
    async function go(env) {
      const name = "promote:" + r.id + ":" + env;
      try {
        const op = await api(safePath(r.links.self) + "/" + "promote", { method: "POST", key: actionKey(name), body: { environment: env } });
        actionKeys.delete(name);
        cache = {};
        confirming = null;
        if (g !== gen) return;
        fill(row, h("span", { text: "Promotion requested: " }), op && op.id ? h("a", { class: "btn primary sm", href: "#operations/" + enc(op.id), text: "Review and approve" }) : h("span", { text: "see History." }));
        clear(flash);
      } catch (e) {
        if (g !== gen) return;
        fill(flash, errorBox(e, "Promotion refused"));
        step1();
      }
    }
    step1();
    return section("Promote", h("p", { class: "muted small", text: "Deploy this resource's pattern and version to another environment." }), row, flash);
  }

  /* ---------- app rollouts (POST /apps) ---------- */
  const ROLLOUT_STEPS = [
    ["planning_replicas", "Replicas planned"], ["awaiting_replica_approval", "Approve replicas"], ["applying_replicas", "Replicas applying"],
    ["planning_router", "Router planned"], ["awaiting_router_approval", "Approve router"], ["applying_router", "Router applying"], ["ready", "Ready"],
  ];
  const TEARDOWN_STEPS = [
    ["planning_router_destroy", "Router destroy planned"], ["awaiting_router_destroy_approval", "Approve router destroy"], ["destroying_router", "Router destroying"],
    ["planning_replica_destroy", "Replica destroys planned"], ["awaiting_replica_destroy_approval", "Approve replica destroys"], ["destroying_replicas", "Replicas destroying"], ["destroyed", "Destroyed"],
  ];
  const isTeardown = (a) => TEARDOWN_STEPS.some((x) => x[0] === a.state);
  const ROLLOUT_DONE = ["ready", "failed", "uncertain", "destroyed"];
  const GATES = {
    awaiting_replica_approval: "replica", awaiting_router_approval: "router",
    awaiting_router_destroy_approval: "router_destroy", awaiting_replica_destroy_approval: "replica_destroy",
  };
  const DESTROY_GATES = ["router_destroy", "replica_destroy"];
  const memberLabel = (m) => (m.role === "router" ? "router" : m.role === "router_destroy" ? "router destroy" : (m.role === "replica_destroy" ? "replica destroy " : "replica ") + (m.index == null ? "" : m.index));
  function gateMembers(ro, gate) {
    const td = ro.teardown || [];
    if (gate === "router") return ro.router ? [ro.router] : [];
    if (gate === "router_destroy") return td.filter((m) => m.role === "router_destroy");
    if (gate === "replica_destroy") return td.filter((m) => m.role === "replica_destroy");
    return ro.members || [];
  }
  const newKey = () => {
    if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
    const b = new Uint8Array(16); crypto.getRandomValues(b);
    return Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  };
  /* one key per pending action: a retry of the same click reuses it; a new click gets a fresh one */
  const actionKey = (name) => { if (!actionKeys.has(name)) actionKeys.set(name, newKey()); return actionKeys.get(name); };
  const sibling = (link, name) => safePath(link).replace(/[^/]+$/, name);
  const STARTED = ["apply_queued", "applying", "succeeded", "failed", "uncertain"];
  const NEXT_TEXT = {
    poll: "Working: this page refreshes by itself.",
    approve_with_plan_digests: "Review the plans, then approve them.",
    done: "Rolled out.",
    inspect_failure: "Inspect the member operation that failed.",
    reconcile_with_operator: "Outcome uncertain: an operator must reconcile.",
  };
  const loadRollouts = (ttl) => cached("rollouts", ttl === undefined ? 3000 : ttl,
    () => pages("/apps", { limit: 100 }, "after", "next_after", PAGE_CAP).catch(() => ({ items: [], truncated: false })));
  function rolloutPill(state) {
    const s = String(state == null ? "unknown" : state);
    const k = s.startsWith("planning_") ? "planning" : s.startsWith("awaiting_") ? "planned" : s.startsWith("applying_") || s.startsWith("destroying_") ? "applying" : cls(s);
    return h("span", { class: "pill st-" + k, text: s.replace(/_/g, " ") });
  }
  function rolloutStep(a) {
    const i = (isTeardown(a) ? TEARDOWN_STEPS : ROLLOUT_STEPS).findIndex((x) => x[0] === a.state);
    if (i >= 0) return i;
    const members = a.members || [];
    if (a.router) return STARTED.includes(a.router.state) ? 5 : a.router.state === "planned" ? 4 : 3;
    if (members.some((m) => STARTED.includes(m.state))) return 2;
    return members.some((m) => m.state === "planned") ? 1 : 0;
  }
  function stepper(a, compact) {
    const cur = rolloutStep(a);
    const td = isTeardown(a);
    const ended = a.state === "ready" || a.state === "destroyed";
    return h("ol", { class: "stepper" + (compact ? " compact" : "") + (td ? " teardown" : ""), "aria-label": td ? "Teardown progress" : "Rollout progress" }, (td ? TEARDOWN_STEPS : ROLLOUT_STEPS).map(([, label], i) => {
      let k = i < cur ? "done" : i === cur ? (ended ? "done" : a.state === "failed" ? "bad" : a.state === "uncertain" ? "unc" : GATES[a.state] ? "gate" : "current") : "todo";
      const here = i === cur;
      const word = k === "done" ? "done" : k === "todo" ? "upcoming" : k === "bad" ? "failed" : k === "unc" ? "uncertain" : here ? "current" : "";
      return h("li", { class: "step " + k + (here ? " here" : ""), "aria-current": here && !ended ? "step" : null },
        h("span", { class: "step-n", "aria-hidden": "true", text: k === "done" ? "✓" : String(i + 1) }),
        h("span", { class: "step-l", text: label }), h("span", { class: "sr", text: " (" + word + ")" }));
    }));
  }
  const rolloutZone = (a) => (a.business_unit || a.environment ? zoneName(a) : "");
  function rolloutBrief(ro) {
    return h("div", { class: "rollout-brief" },
      h("div", { class: "row" }, h("span", { class: "muted small", text: isTeardown(ro) ? "Teardown" : "Rollout" }), rolloutPill(ro.state), ro.next_action === "approve_with_plan_digests" ? h("span", { class: "tag warn", text: "needs approval" }) : null),
      stepper(ro, true));
  }
  function latestRollouts(list) {
    const byKey = new Map();
    for (const a of list) {
      const k = a.name + "|" + zoneName(a);
      const cur = byKey.get(k);
      const better = !cur || (ROLLOUT_DONE.includes(cur.state) && !ROLLOUT_DONE.includes(a.state))
        || (ROLLOUT_DONE.includes(cur.state) === ROLLOUT_DONE.includes(a.state) && String(a.created_at) > String(cur.created_at));
      if (better) byKey.set(k, a);
    }
    return Array.from(byKey.values()).sort((x, y) => x.name.localeCompare(y.name));
  }
  function rolloutCard(ro) {
    const n = (ro.members || []).length;
    return h("a", { class: "card app-card" + (ro.state === "destroyed" ? " destroyed" : ""), href: "#rollouts/" + enc(ro.id) },
      h("div", { class: "row" }, h("b", { class: "app-name", text: ro.name }), h("span", { class: "grow" }), rolloutPill(ro.state)),
      h("div", { class: "muted small", text: rolloutZone(ro) }),
      stepper(ro, true),
      h("div", { text: ro.state === "destroyed" ? "Torn down: the router and all " + n + " replica" + (n === 1 ? "" : "s") + " were destroyed" : n + " replica" + (n === 1 ? "" : "s") + (ro.router ? " + router" : "") + ": rollout record, nothing deployed yet" }),
      ro.next_action ? h("div", { class: "small muted", text: NEXT_TEXT[ro.next_action] || ro.next_action }) : null);
  }
  function rolloutsPanel(active) {
    return section("Rollouts in progress", h("ul", { class: "feed" }, active.map((a) =>
      h("li", { class: "feed-i rollout-i" },
        rolloutPill(a.state),
        h("div", { class: "feed-main" }, h("a", { href: "#rollouts/" + enc(a.id), text: a.name }), h("div", { class: "muted small", text: rolloutZone(a) }), stepper(a, true)),
        GATES[a.state] ? h("a", { class: "btn primary sm", href: "#rollouts/" + enc(a.id), text: "Review and approve" }) : null))),
      h("a", { href: "#apps", text: "All apps" }));
  }
  const shortDigest = (d) => (d ? h("span", { class: "mono", title: d, text: String(d).slice(0, 12) }) : dash());
  function membersTable(ro, opsById) {
    const all = (ro.members || []).concat(ro.router ? [ro.router] : [], ro.teardown || []);
    return table(["Member", "Operation state", "Operation", "Resource", "Plan digest"], all.map((m) => {
      const op = opsById[m.operation_id];
      return h("tr", null,
        h("td", null, memberLabel(m), m.role === "replica" && ro.primary != null && ro.members.length > 1
          ? h("span", { class: "topo-tag " + (m.index === ro.primary ? "tp" : "ts"), text: m.index === ro.primary ? "PRIMARY" : "SECONDARY" }) : null),
        h("td", null, pill(m.state)),
        h("td", null, h("a", { class: "mono", href: "#operations/" + enc(m.operation_id), text: m.operation_id, title: "Open the operation and review its plan changes" }),
          op && op.pattern ? h("div", { class: "muted small", text: op.pattern + (op.version ? "@" + op.version : "") }) : null),
        h("td", null, h("a", { class: "mono", href: "#resources/" + enc(m.resource_id), text: m.resource_id })),
        h("td", null, shortDigest(m.plan_digest)));
    }), "No members yet.");
  }
  function approvePanel(ro, gate, opsById) {
    const members = gateMembers(ro, gate);
    const isDestroy = DESTROY_GATES.includes(gate);
    const rows = members.map((m) => {
      const op = opsById[m.operation_id];
      let problem = null;
      if (!op || op instanceof Error) problem = "Could not load this operation, so its plan cannot be reviewed here.";
      else if (op.state !== "planned") problem = "Operation is " + op.state + ", not planned.";
      else if (!m.plan_digest || op.plan_digest !== m.plan_digest) problem = "The plan digest changed; refresh and review again.";
      return { m, op: problem ? null : op, problem, raw: op };
    });
    const ready = rows.length > 0 && rows.every((x) => !x.problem) && Boolean(ro.links && ro.links.approve);
    const destructive = rows.filter((x) => x.op && x.op.change_summary && x.op.change_summary.destructive);
    const box = h("section", { class: "card approve", "aria-labelledby": "approve-h" });
    let flash = approveFlash && approveFlash.id === ro.id ? approveFlash.err : null;
    let busy = false;
    const label = gate === "router" ? "router" : gate === "router_destroy" ? "router destroy" : isDestroy ? members.length + " replica destroy plan" + (members.length === 1 ? "" : "s") : members.length + " replica plan" + (members.length === 1 ? "" : "s");
    const title = { router: "Approve the router", replica: "Approve the replicas", router_destroy: "Approve the router destroy", replica_destroy: "Approve the replica destroys" }[gate];
    async function submit() {
      busy = true; draw();
      const digests = {};
      for (const x of rows) digests[x.m.operation_id] = x.m.plan_digest;
      try {
        await api(safePath(ro.links.approve), { method: "POST", body: { plan_digests: digests } });
        cache = {}; approveFlash = null; confirming = null;
        render();
      } catch (e) {
        busy = false; confirming = null;
        flash = e; approveFlash = { id: ro.id, err: e };
        draw();
      }
    }
    function draw() {
      const open = confirming === "approve";
      fill(box,
        h("h2", { id: "approve-h", text: title }),
        isDestroy ? h("div", { class: "alert", role: "alert" }, h("h2", { text: "Destructive: this deletes real infrastructure" }),
          h("div", { text: "Approving applies these destroy plans and removes the " + (gate === "router_destroy" ? "router, so traffic stops routing to the app" : "replicas and everything in them") + ". This cannot be undone." })) : null,
        h("p", { class: "small muted", text: "Approving applies exactly the plans listed here, with the digests shown. Nothing else is applied." }),
        flash ? errorBox(flash, "Approval refused") : null,
        destructive.length ? h("div", { class: "alert", role: "alert" }, h("h2", { text: "Destructive changes" }),
          h("div", { text: "These plans delete or replace resources: " + destructive.map((x) => x.m.operation_id).join(", ") + ". Open each plan and read the changes before approving." })) : null,
        h("ul", { class: "approve-list" }, rows.map((x) => h("li", { class: "approve-i" },
          h("div", { class: "row" }, h("b", { text: memberLabel(x.m) }),
            x.op ? miniSummary(x.op.change_summary) : null,
            x.op && x.op.pattern ? h("span", { class: "muted small", text: x.op.pattern + (x.op.version ? "@" + x.op.version : "") }) : null,
            h("a", { class: "small", href: "#operations/" + enc(x.m.operation_id), text: "Review plan changes" })),
          h("div", { class: "muted small" }, "digest ", shortDigest(x.m.plan_digest)),
          x.problem ? h("div", { class: "small bad-t", text: x.problem }) : null))),
        open
          ? h("div", { class: "confirm", role: "group", "aria-label": "Confirm approval" },
            h("b", { text: "Approve exactly " + label + "?" }),
            h("div", { class: "mono muted", text: rows.map((x) => x.m.operation_id + " " + String(x.m.plan_digest).slice(0, 12)).join(" | ") }),
            destructive.length || isDestroy ? h("div", { class: "b-delete", text: isDestroy ? "These plans destroy resources permanently." : "Some of these plans delete or replace resources." }) : null,
            h("div", { class: "row" },
              h("button", { class: "btn " + (isDestroy ? "danger" : "primary"), type: "button", disabled: busy, text: busy ? "Approving..." : isDestroy ? "Confirm approve destroy" : "Confirm approve", onclick: submit }),
              h("button", { class: "btn", type: "button", disabled: busy, text: "Cancel", onclick: () => { confirming = null; draw(); } })))
          : h("div", { class: "row" }, h("button", { class: "btn " + (isDestroy ? "danger" : "primary"), type: "button", disabled: !ready, text: "Approve " + (gate === "router" ? "router" : gate === "router_destroy" ? "router destroy" : gate === "replica_destroy" ? "replica destroys" : "replicas"), onclick: () => { confirming = "approve"; flash = null; approveFlash = null; draw(); } })));
    }
    draw();
    return box;
  }
  const replicaWhere = (ro, m, resById) => {
    const r = resById[m.resource_id];
    return h("span", { class: "row" }, r ? cloudBadge(r.cloud) : null, r && r.region ? h("span", { class: "chip", text: r.region }) : null);
  };
  function failoverPanel(ro, resById) {
    const box = h("section", { class: "card failover", "aria-labelledby": "failover-h" });
    const target = () => (confirming && confirming.startsWith("failover:") ? Number(confirming.slice(9)) : null);
    let flash = null;
    let busy = false;
    async function submit(index) {
      busy = true; draw();
      try {
        await api(sibling(ro.links.approve, "failover"), { method: "POST", body: { primary: index }, key: actionKey("failover:" + ro.id + ":" + index) });
        actionKeys.delete("failover:" + ro.id + ":" + index);
        cache = {}; confirming = null; failoverNote = ro.id;
        render();
      } catch (e) {
        busy = false; flash = e; draw();
      }
    }
    function draw() {
      const t = target();
      const others = (ro.members || []).filter((m) => m.index !== ro.primary);
      fill(box,
        h("h2", { id: "failover-h", text: "Fail over" }),
        h("p", { class: "small muted", text: "Replica " + ro.primary + " is primary. Making another replica primary creates a router update plan; nothing changes until you approve that plan." }),
        flash ? errorBox(flash, "Failover refused") : null,
        t !== null
          ? h("div", { class: "confirm", role: "group", "aria-label": "Confirm failover" },
            h("b", { text: "Make replica " + t + " primary?" }),
            h("div", { class: "small", text: "The router's primary and secondary inputs are swapped in a new plan. You review and approve it next." }),
            h("div", { class: "row" },
              h("button", { class: "btn primary", type: "button", disabled: busy, text: busy ? "Submitting..." : flash ? "Retry failover" : "Confirm failover", onclick: () => submit(t) }),
              h("button", { class: "btn", type: "button", disabled: busy, text: "Cancel", onclick: () => { actionKeys.delete("failover:" + ro.id + ":" + t); confirming = null; flash = null; draw(); } })))
          : h("div", { class: "stack" }, others.map((m) => h("div", { class: "row" },
            h("button", { class: "btn", type: "button", text: "Make replica " + m.index + " primary", onclick: () => { confirming = "failover:" + m.index; flash = null; draw(); } }),
            replicaWhere(ro, m, resById), h("span", { class: "topo-tag ts", text: "SECONDARY" })))));
    }
    draw();
    return box;
  }
  function teardownPanel(ro) {
    const box = h("section", { class: "card danger-zone", "aria-labelledby": "destroy-h" });
    let flash = null;
    let busy = false;
    async function submit() {
      busy = true; draw();
      try {
        await api(sibling(ro.links.approve, "destroy"), { method: "POST", key: actionKey("destroy:" + ro.id) });
        actionKeys.delete("destroy:" + ro.id);
        cache = {}; confirming = null;
        render();
      } catch (e) {
        busy = false; flash = e; draw();
      }
    }
    function draw() {
      const open = confirming === "destroy";
      let go = null;
      const input = h("input", { id: "destroy-confirm", type: "text", autocomplete: "off", spellcheck: "false", "aria-label": "Type the app name to confirm", placeholder: ro.name,
        oninput: () => { if (go) go.disabled = busy || input.value !== ro.name; } });
      go = h("button", { class: "btn danger", type: "button", disabled: true, text: busy ? "Submitting..." : flash ? "Retry tear down" : "Confirm tear down", onclick: submit });
      fill(box,
        h("h2", { id: "destroy-h", text: "Tear down app" }),
        h("p", { class: "small", text: "This destroys the app in order: first the router, then the replicas. Each step is planned and waits for your approval before anything is deleted." }),
        flash ? errorBox(flash, "Tear down refused") : null,
        open
          ? h("div", { class: "confirm", role: "group", "aria-label": "Confirm tear down" },
            h("b", { text: "Type the app name to confirm: " + ro.name }),
            h("label", { class: "small", for: "destroy-confirm", text: "App name" }), input,
            h("div", { class: "row" }, go,
              h("button", { class: "btn", type: "button", disabled: busy, text: "Cancel", onclick: () => { actionKeys.delete("destroy:" + ro.id); confirming = null; flash = null; draw(); } })))
          : h("div", { class: "row" }, h("button", { class: "btn danger", type: "button", text: "Tear down app...", onclick: () => { confirming = "destroy"; flash = null; draw(); } })));
    }
    draw();
    return box;
  }
  async function rolloutNodes(ro, g) {
    const gate = GATES[ro.state] || null;
    const opsById = {};
    const resById = {};
    if (ro.state === "ready" && (ro.members || []).length > 1) {
      try { for (const r of (await loadResources(3000)).items) resById[r.id] = r; } catch (e) { /* cloud and region are optional here */ }
    }
    if (gate) {
      const members = gateMembers(ro, gate);
      const got = await Promise.allSettled(members.map((m) => api(`/operations/${enc(m.operation_id)}`)));
      members.forEach((m, i) => { opsById[m.operation_id] = got[i].status === "fulfilled" ? got[i].value : new Error("load"); });
    }
    if (g !== gen) return null;
    const nodes = [];
    if (ro.state === "failed" || ro.state === "uncertain" || ro.error) {
      nodes.push(h("div", { class: "alert" + (ro.state === "uncertain" ? " unc" : ""), role: "alert" },
        h("h2", { text: ro.state === "uncertain" ? "Rollout outcome uncertain" : (ro.teardown ? "Teardown " : "Rollout ") + (ro.state === "failed" ? "failed" : "error") }),
        h("div", { text: ro.error || (NEXT_TEXT[ro.next_action] || "See the member operations below.") })));
    }
    if (ro.notice) nodes.push(h("div", { class: "note", role: "status", text: ro.notice }));
    if (failoverNote === ro.id && ["planning_router", "awaiting_router_approval"].includes(ro.state)) nodes.push(h("div", { class: "note", role: "status", text: "Failover plan: review and approve below." }));
    nodes.push(h("section", { class: "card" + (ro.state === "destroyed" ? " destroyed" : "") }, h("h2", { text: isTeardown(ro) ? "Teardown" : "Rollout" }),
      h("div", { class: "row" }, rolloutPill(ro.state), h("span", { class: "muted small", text: NEXT_TEXT[ro.next_action] || "" })),
      stepper(ro, false),
      kv([
        ["Rollout id", h("span", { class: "mono", text: ro.id })], ["Zone", rolloutZone(ro)], ["Next action", ro.next_action],
        ["Primary replica", (ro.members || []).length > 1 && num(ro.primary) !== null && ro.state !== "destroyed" ? "replica " + ro.primary : null],
        ["Created", ro.created_at ? fmtTime(ro.created_at) : null], ["Updated", ro.updated_at ? fmtTime(ro.updated_at) : null],
        ["Poll after", ro.poll_after_seconds ? ro.poll_after_seconds + "s" : null],
      ])));
    if (gate) nodes.push(approvePanel(ro, gate, opsById));
    if (ro.state === "ready" && ro.links && ro.links.approve && (ro.members || []).length > 1) nodes.push(failoverPanel(ro, resById));
    if ((ro.state === "ready" || ro.state === "failed") && ro.links && ro.links.approve) nodes.push(teardownPanel(ro));
    nodes.push(section("Members", membersTable(ro, opsById)));
    return nodes;
  }
  async function viewRollout(id, g) {
    const crumbs = h("div", { class: "crumbs" }, h("a", { href: "#apps", text: "Apps" }), " / ", h("span", { class: "mono", text: id }));
    const ro = await api(`/apps/${enc(id)}`);
    if (g !== gen) return;
    const nodes = await rolloutNodes(ro, g);
    if (!nodes) return;
    mount(crumbs, h("div", { class: "res-head" }, h("h1", null, rolloutPill(ro.state), " ", ro.name),
      h("div", { class: "row" }, h("a", { class: "chip app-chip", href: appHref(ro.name), text: "app view: " + ro.name }))), nodes);
    refresher = () => { if (!confirming && !ROLLOUT_DONE.includes(ro.state)) viewRollout(id, gen).catch(() => {}); };
  }

  /* ---------- apps ---------- */
  /* Convention: resources sharing label app=<name> form one app. app_role=replica serves it (one per
     cloud/region); app_role=router is the global failover whose input_refs primary_endpoint and
     secondary_endpoint point at replica outputs. */
  const APP_HINT = "Label resources app=<name> and app_role=replica|router";
  const liveVisible = (list) => list.filter((r) => isLive(r) && inScope(r));
  const appRole = (r) => ((r.labels && r.labels.app_role) === "router" ? "router" : "replica");
  function collectApps(resources) {
    const apps = new Map();
    for (const r of resources) {
      const name = r.labels && r.labels.app;
      if (!name) continue;
      if (!apps.has(name)) apps.set(name, { name, members: [], replicas: [], routers: [] });
      const a = apps.get(name);
      a.members.push(r);
      (appRole(r) === "router" ? a.routers : a.replicas).push(r);
    }
    return Array.from(apps.values()).sort((x, y) => x.name.localeCompare(y.name));
  }
  function appTargets(app) {
    const router = app.routers[0] || null;
    const refs = (router && router.input_refs) || {};
    const idOf = (k) => (refs[k] && refs[k].resource_id) || null;
    return { router, primary: idOf("primary_endpoint"), secondary: idOf("secondary_endpoint") };
  }
  function appHealth(app) {
    const ready = app.replicas.filter((r) => r.state === "ready").length;
    const routerReady = app.routers.length > 0 && app.routers.every((r) => r.state === "ready");
    let status = "degraded";
    if (!ready) status = "down";
    else if (ready === app.replicas.length && routerReady) status = "healthy";
    return { status, ready, total: app.replicas.length, routerReady };
  }
  const appHref = (name) => "#apps/" + enc(name);
  const where = (r) => (r ? [r.cloud ? (CLOUD_NAMES[r.cloud] || r.cloud) : null, r.region].filter(Boolean).join(" / ") : "");
  function appCard(app, ro) {
    const health = appHealth(app);
    const t = appTargets(app);
    const byId = byIdMap(app.members);
    const zones = Array.from(new Set(app.members.map(zoneName))).sort();
    const clouds = Array.from(new Set(app.members.map((r) => r.cloud).filter(Boolean))).sort();
    const fqdn = t.router && t.router.outputs ? t.router.outputs.app_fqdn : null;
    const tgt = (label, id) => (id ? h("div", { class: "small" }, h("b", { text: label }), " ", byId[id] ? resName(byId[id]) + " (" + where(byId[id]) + ")" : id) : null);
    return h("a", { class: "card app-card", href: appHref(app.name) },
      h("div", { class: "row" }, h("b", { class: "app-name", text: app.name }), h("span", { class: "grow" }), pill(health.status)),
      h("div", { class: "chips" }, clouds.map((c) => cloudBadge(c))),
      h("div", { class: "muted small", text: zones.join(", ") }),
      h("div", { text: health.ready + " of " + health.total + " replicas ready" }),
      h("div", { class: "small muted", text: t.router ? "Router: " + resName(t.router) + " (" + t.router.state + ")" : "No router yet" }),
      ro ? rolloutBrief(ro) : null,
      tgt("PRIMARY", t.primary), tgt("SECONDARY", t.secondary),
      fqdn ? h("div", { class: "small" }, h("span", { class: "muted", text: "app_fqdn " }), h("span", { class: "mono", text: text(fqdn) })) : null,
      h("div", { class: "small" }, h("span", { class: "muted", text: "est. " }), money(sum(app.members, (r) => r.estimated_monthly_cost)), " per month"));
  }
  function appsEmpty() { return empty("No apps yet", APP_HINT + ". Replicas serve the app; the router fails over between them."); }
  function appsPanel(resources) {
    const apps = collectApps(resources);
    return section("Apps", apps.length
      ? h("ul", { class: "feed" }, apps.map((a) => {
        const hl = appHealth(a);
        return h("li", { class: "feed-i app-i" }, pill(hl.status), h("div", { class: "feed-main" }, h("a", { href: appHref(a.name), text: a.name })),
          h("span", { class: "muted small", text: hl.ready + " of " + hl.total + " replicas" }));
      }))
      : appsEmpty(), h("a", { href: "#apps", text: "All apps" }));
  }
  async function viewApps(_id, g, silent) {
    const [agentR, resR, roR] = await Promise.allSettled([loadAgent(30000), loadResources(silent ? 0 : 3000), loadRollouts(silent ? 0 : 3000)]);
    if (g !== gen) return;
    if (resR.status === "rejected") { mount(errorBox(resR.reason)); return; }
    populateEnv(allEnvs(agentR.status === "fulfilled" ? agentR.value : null, resR.value.items));
    const apps = collectApps(liveVisible(resR.value.items));
    const picks = latestRollouts((roR.status === "fulfilled" ? roR.value.items : []).filter(inScope));
    const used = new Set();
    const cards = apps.map((a) => {
      const zones = new Set(a.members.map(zoneName));
      const ro = picks.find((x) => x.name === a.name && zones.has(zoneName(x)));
      if (ro) used.add(ro.id);
      return appCard(a, ro);
    });
    for (const ro of picks) if (!used.has(ro.id)) cards.push(rolloutCard(ro));
    mount(h("div", { class: "page-head" }, h("h1", { text: "Apps" }), h("span", { class: "muted small", text: cards.length + " app" + (cards.length === 1 ? "" : "s") + " | " + APP_HINT })),
      filterNote(),
      cards.length ? h("div", { class: "grid" }, cards) : appsEmpty(),
      truncNote(resR.value.truncated, resR.value.items.length));
    refresher = () => viewApps(null, gen, true).catch(() => {});
  }
  function topoNode(r, tag, tagClass) {
    return h("a", { class: "topo-node st-edge-" + cls(r.state), href: "#resources/" + enc(r.id), title: r.id },
      h("div", { class: "row" }, tag ? h("span", { class: "topo-tag " + tagClass, text: tag }) : null, pill(r.state)),
      h("b", { text: resName(r) }),
      h("div", { class: "row" }, cloudBadge(r.cloud), r.region ? h("span", { class: "chip", text: r.region }) : null),
      h("div", { class: "muted small" }, "objects ", objectsCell(r)));
  }
  function topology(app, t) {
    const groups = new Map();
    for (const r of app.replicas) {
      const k = where(r) || "Unknown";
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(r);
    }
    const cols = Array.from(groups.keys()).sort().map((k) => {
      const rs = groups.get(k);
      const isPrimary = rs.some((r) => r.id === t.primary);
      return h("div", { class: "topo-group" + (isPrimary ? " primary" : t.primary || t.secondary ? " secondary" : "") },
        h("div", { class: "topo-gh muted small", text: k }),
        rs.map((r) => topoNode(r, r.id === t.primary ? "PRIMARY" : r.id === t.secondary ? "SECONDARY" : null, r.id === t.primary ? "tp" : "ts")));
    });
    return h("div", { class: "topo", role: "group", "aria-label": "Topology" },
      t.router ? h("div", { class: "topo-top" }, topoNode(t.router, "ROUTER", "tr")) : h("div", { class: "topo-top" }, empty("No router", "Add a resource labelled app_role=router with primary_endpoint and secondary_endpoint input_refs.")),
      app.replicas.length ? h("div", { class: "topo-groups" }, cols) : empty("No replicas", "Label resources app=" + app.name + " and app_role=replica."));
  }
  async function viewApp(name, g, silent) {
    const [resR, roR] = await Promise.allSettled([loadResources(silent ? 0 : 3000), loadRollouts(silent ? 0 : 3000)]);
    if (g !== gen) return;
    if (resR.status === "rejected") { mount(errorBox(resR.reason)); return; }
    const app = collectApps(resR.value.items.filter(isLive)).find((a) => a.name === name);
    const crumbs = h("div", { class: "crumbs" }, h("a", { href: "#apps", text: "Apps" }), " / ", h("span", { text: name }));
    const zones = app ? new Set(app.members.map(zoneName)) : null;
    const ros = (roR.status === "fulfilled" ? roR.value.items : []).filter((a) => a.name === name && (!zones || zones.has(zoneName(a))));
    if (!app && !ros.length) { mount(crumbs, empty("App not found", "No live resource carries the label app=" + name + " and no rollout has that name. " + APP_HINT + ".")); return; }
    const main = ros.length ? latestRollouts(ros).sort((x, y) => (ROLLOUT_DONE.includes(x.state) - ROLLOUT_DONE.includes(y.state)) || String(y.created_at).localeCompare(String(x.created_at)))[0] : null;
    const roNodes = main ? await rolloutNodes(main, g) : null;
    if (g !== gen) return;
    const others = ros.filter((a) => main && a.id !== main.id);
    const roExtra = [roNodes, others.length ? section("Other rollouts of " + name, h("ul", { class: "feed" }, others.map((a) =>
      h("li", { class: "feed-i app-i" }, rolloutPill(a.state), h("div", { class: "feed-main" }, h("a", { href: "#rollouts/" + enc(a.id), text: a.id }), h("div", { class: "muted small", text: rolloutZone(a) })), when(a.created_at))))) : null];
    const roLive = Boolean(main) && !ROLLOUT_DONE.includes(main.state);
    if (!app) {
      mount(crumbs, h("div", { class: "res-head" }, h("h1", null, rolloutPill(main.state), " ", name), h("div", { class: "row" }, h("span", { class: "chip", text: rolloutZone(main) }))), roExtra);
      refresher = () => { if (!confirming && roLive) viewApp(name, gen, true).catch(() => {}); };
      return;
    }
    const health = appHealth(app);
    const t = appTargets(app);
    const byId = byIdMap(app.members);
    const opsR = await Promise.allSettled(app.members.map((m) => api("/operations", { query: { resource_id: m.id, limit: 20 } })));
    if (g !== gen) return;
    const ops = [];
    for (const x of opsR) if (x.status === "fulfilled") ops.push(...(x.value.items || []));
    ops.sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
    const router = t.router;
    const planned = router ? ops.find((o) => o.resource_id === router.id && o.state === "planned" && (!router.latest_operation_id || o.id === router.latest_operation_id)) || ops.find((o) => o.resource_id === router.id && o.state === "planned") : null;
    const refs = router ? Object.keys(router.input_refs || {}).sort() : [];
    const role = (r) => (r.id === t.primary ? "PRIMARY" : r.id === t.secondary ? "SECONDARY" : "");
    const out = [crumbs,
      h("div", { class: "res-head" }, h("h1", null, pill(health.status), " ", name),
        h("div", { class: "row" }, Array.from(new Set(app.members.map((r) => r.cloud).filter(Boolean))).sort().map((c) => cloudBadge(c)),
          Array.from(new Set(app.members.map(zoneName))).sort().map((z) => h("span", { class: "chip", text: z })))),
      roExtra,
      h("div", { class: "tiles" },
        tile("Replicas ready", health.ready + " of " + health.total, "redundancy", null, health.status === "healthy" ? "ok" : health.status === "down" ? "bad" : "warn"),
        tile("Router", router ? router.state : "none", router ? resName(router) : "failover not configured", null, health.routerReady ? "ok" : "warn"),
        tile("Est. monthly cost", money(sum(app.members, (r) => r.estimated_monthly_cost)), "all members")),
      section("Topology", topology(app, t))];
    out.push(section("Replicas", table(["Role", "State", "Name", "Cloud", "Region", "Zone", "Cost", "Objects"], app.replicas.map((r) => h("tr", null,
      h("td", null, role(r) ? h("span", { class: "topo-tag " + (role(r) === "PRIMARY" ? "tp" : "ts"), text: role(r) }) : dash()),
      h("td", null, pill(r.state)),
      h("td", null, resLink(r, r.id)),
      h("td", null, cloudBadge(r.cloud)),
      h("td", { text: r.region || "" }),
      h("td", { class: "nowrap", text: zoneName(r) }),
      h("td", { class: "num" }, cost(r.estimated_monthly_cost)),
      h("td", { class: "num" }, objectsCell(r)))), "No replicas labelled app_role=replica.")));
    out.push(section("Router", router ? [
      h("div", { class: "row" }, pill(router.state), resLink(router, router.id), router.pattern ? h("span", { class: "chip", text: router.pattern + (router.version ? "@" + router.version : "") }) : null),
      h("h2", { class: "sub", text: "Input refs" }),
      refs.length ? table(["Input", "Replica", "Output"], refs.map((k) => {
        const ref = router.input_refs[k];
        return h("tr", null, h("td", { text: k }), h("td", null, resLink(byId[ref.resource_id] || resMap2(resR.value.items)[ref.resource_id], ref.resource_id), " ", role(byId[ref.resource_id] || {}) ? h("span", { class: "muted small", text: role(byId[ref.resource_id]) }) : null), h("td", { text: ref.output }));
      })) : h("span", { class: "muted", text: "The router has no input_refs." }),
      h("h2", { class: "sub", text: "Outputs" }), outputsBlock(router.outputs, router.withheld_outputs)]
      : empty("No router", "A router is a member labelled app_role=router.")));
    out.push(section("Failover", h("p", { text: "To fail over, submit an update to the router with primary/secondary input_refs swapped; review the plan here and apply." }),
      planned ? h("div", { class: "row" }, h("span", { class: "muted small", text: "A router plan is waiting:" }), miniSummary(planned.change_summary), h("a", { class: "btn primary sm", href: "#operations/" + enc(planned.id), text: "Review plan" }))
        : router && router.latest_operation_id ? h("a", { class: "small", href: "#operations/" + enc(router.latest_operation_id), text: "Router latest operation" }) : null));
    out.push(section("Latest operations", ops.length ? h("ul", { class: "feed" }, ops.slice(0, 20).map((o) => feedItem(o, byId))) : empty("No operations recorded")));
    mount(out);
    refresher = () => { if (!confirming) viewApp(name, gen, true).catch(() => {}); };
  }
  const resMap2 = byIdMap;

  /* ---------- cost charts: SVG built with createElementNS, drawn to scale, no libraries ---------- */
  const SVG_NS = "http://www.w3.org/2000/svg"; // an XML namespace name, never fetched
  function svgEl(tag, attrs, ...kids) {
    const el = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "text") el.textContent = String(v); else el.setAttribute(k, String(v));
    }
    for (const kid of kids) if (kid) el.appendChild(kid);
    return el;
  }
  const zoneHref = (bu, env) => "#zones/" + enc(bu) + "/" + enc(env);
  const loadCostHistory = (bu, env, days) => cached("cost:" + bu + "|" + env + "|" + days, 60000,
    () => api("/budgets/history", { query: { business_unit: bu, environment: env, days } }));
  function seriesOf(hist) {
    return ((hist && hist.days) || []).map((d) => ({ date: String(d.date), v: num(d.reserved) || 0 }));
  }
  function niceStep(raw) {
    const p = Math.pow(10, Math.floor(Math.log10(raw)));
    const f = raw / p;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
  }
  function sparkline(series, budget) {
    const W = 160, H = 40, padX = 4, padY = 4;
    const top = Math.max(1e-9, ...series.map((p) => p.v), budget || 0) * 1.05;
    const n = series.length;
    const x = (i) => (n > 1 ? padX + (i * (W - 2 * padX)) / (n - 1) : W / 2);
    const y = (v) => H - padY - (v / top) * (H - 2 * padY);
    const last = series[n - 1];
    const svg = svgEl("svg", { class: "spark", viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": n + " day spend trend, now " + money(last.v) + (budget ? " of budget " + money(budget) : "") },
      svgEl("title", { text: n + " days: " + series[0].date + " to " + last.date }));
    if (budget) svg.appendChild(svgEl("line", { class: "ch-budget", x1: padX, x2: W - padX, y1: y(budget), y2: y(budget) }));
    svg.appendChild(svgEl("polyline", { class: "ch-line", points: series.map((p, i) => x(i).toFixed(1) + "," + y(p.v).toFixed(1)).join(" ") }));
    svg.appendChild(svgEl("circle", { class: "ch-dot", cx: x(n - 1), cy: y(last.v), r: 3 }));
    return svg;
  }
  function sparkBlock(hist) {
    const series = seriesOf(hist);
    if (!series.length) return h("span", { class: "muted small", text: "No cost history yet." });
    const budget = num(hist.monthly_budget);
    const last = series[series.length - 1].v;
    return h("div", { class: "spark-wrap" }, sparkline(series, budget),
      h("div", { class: "spark-label small" }, h("b", { text: money(last) }), h("span", { class: "muted", text: " reserved now, last " + series.length + " days" + (budget ? " | budget " + money(budget) : "") })));
  }
  function sparkHost(bu, env, g) {
    const host = h("div", { class: "spark-host" });
    loadCostHistory(bu, env, 30).then((hist) => { if (g === gen) fill(host, sparkBlock(hist)); }, () => {});
    return host;
  }
  function trendZone(agent) {
    for (const u of (agent && agent.business_units) || []) for (const e of u.environments || []) {
      if (ui.zone && (ui.zone.bu !== u.name || ui.zone.env !== e)) continue;
      if (ui.env && e !== ui.env) continue;
      return { bu: u.name, env: e };
    }
    return null;
  }
  function spendTrend(agent, g) {
    if (!capOn(agent, "cost_history")) return null;
    const z = trendZone(agent);
    if (!z) return null;
    return section("Spend trend", h("div", { class: "row" }, h("b", { text: z.bu + " · " + z.env }), h("a", { class: "small", href: zoneHref(z.bu, z.env), text: "Cost detail" })), sparkHost(z.bu, z.env, g));
  }
  function costChart(series, budget) {
    const W = 640, H = 260, L = 56, R = 18, T = 14, B = 30;
    const n = series.length;
    const maxV = Math.max(0, ...series.map((p) => p.v), budget || 0);
    const step = niceStep((maxV > 0 ? maxV : 1) / 4);
    const yTop = Math.max(step, Math.ceil(maxV / step) * step);
    const x = (i) => (n > 1 ? L + (i * (W - L - R)) / (n - 1) : (L + W - R) / 2);
    const y = (v) => H - B - (v / yTop) * (H - B - T);
    const svg = svgEl("svg", { class: "chart", viewBox: "0 0 " + W + " " + H, role: "img", tabindex: "0",
      "aria-label": "Reserved spend, " + n + " days, from " + series[0].date + " to " + series[n - 1].date + ". Use left and right arrow keys to read values." });
    for (let v = 0; v <= yTop + step / 2; v += step) {
      svg.appendChild(svgEl("line", { class: "ch-grid", x1: L, x2: W - R, y1: y(v), y2: y(v) }));
      svg.appendChild(svgEl("text", { class: "ch-tick", x: L - 6, y: y(v) + 4, "text-anchor": "end", text: v.toLocaleString(undefined, { maximumFractionDigits: 2 }) }));
    }
    const ticks = new Set();
    for (let k = 0; k <= 4; k++) ticks.add(Math.round(((n - 1) * k) / 4));
    for (const i of ticks) {
      svg.appendChild(svgEl("text", { class: "ch-tick", x: x(i), y: H - B + 16, "text-anchor": i === 0 && n > 1 ? "start" : i === n - 1 && n > 1 ? "end" : "middle", text: series[i].date.slice(5) }));
    }
    const pts = series.map((p, i) => x(i).toFixed(1) + " " + y(p.v).toFixed(1));
    svg.appendChild(svgEl("path", { class: "ch-area", d: "M" + x(0).toFixed(1) + " " + y(0) + " L" + pts.join(" L") + " L" + x(n - 1).toFixed(1) + " " + y(0) + " Z" }));
    if (budget) {
      svg.appendChild(svgEl("line", { class: "ch-budget", x1: L, x2: W - R, y1: y(budget), y2: y(budget) }));
      svg.appendChild(svgEl("text", { class: "ch-tick ch-budget-l", x: W - R, y: y(budget) - 4, "text-anchor": "end", text: "budget " + money(budget) }));
    }
    svg.appendChild(svgEl("path", { class: "ch-line", d: "M" + pts.join(" L") }));
    const cursor = svgEl("line", { class: "ch-cursor", y1: T, y2: H - B });
    const marker = svgEl("circle", { class: "ch-dot", r: 5 });
    svg.appendChild(cursor);
    svg.appendChild(marker);
    const readout = h("div", { class: "chart-readout", "aria-live": "polite" });
    let active = n - 1;
    function show(i) {
      active = Math.max(0, Math.min(n - 1, i));
      const p = series[active];
      cursor.setAttribute("x1", x(active)); cursor.setAttribute("x2", x(active));
      marker.setAttribute("cx", x(active)); marker.setAttribute("cy", y(p.v));
      readout.textContent = p.date + (active === n - 1 ? " (latest)" : "") + ": reserved " + money(p.v) + (budget ? " of " + money(budget) + " budget (" + Math.round((p.v / budget) * 100) + "%)" : "");
    }
    const colW = n > 1 ? (W - L - R) / (n - 1) : W - L - R;
    series.forEach((p, i) => {
      const col = svgEl("rect", { class: "ch-hit", x: x(i) - colW / 2, y: T, width: colW, height: H - B - T }, svgEl("title", { text: p.date + ": " + money(p.v) }));
      col.addEventListener("mouseenter", () => show(i));
      svg.appendChild(col);
    });
    svg.addEventListener("keydown", (ev) => {
      const d = { ArrowLeft: active - 1, ArrowRight: active + 1, Home: 0, End: n - 1 }[ev.key];
      if (d === undefined) return;
      ev.preventDefault();
      show(d);
    });
    show(n - 1);
    return h("div", { class: "chart-wrap" }, svg, readout);
  }
  const signed = (v) => { const n = num(v) || 0; return (n > 0 ? "+" : n < 0 ? "-" : "") + money(Math.abs(n)); };
  const deltaCell = (v) => { const n = num(v) || 0; return h("span", { class: "delta " + (n > 0 ? "up" : n < 0 ? "down" : "flat"), text: signed(n) }); };
  const ZONE_PERIODS = [[7, "7 days"], [30, "30 days"], [90, "90 days"]];
  let zoneDays = 30;
  async function viewZone(id, g) {
    const cut = id.indexOf("/");
    const bu = cut < 0 ? id : id.slice(0, cut);
    const env = cut < 0 ? "" : id.slice(cut + 1);
    const agent = await loadAgent(30000);
    if (g !== gen) return;
    const crumbs = h("div", { class: "crumbs" }, h("a", { href: "#zones", text: "Landing zones" }), " / ", h("span", { text: bu + " · " + env }));
    const unit = (agent.business_units || []).find((u) => u.name === bu);
    if (!unit || !(unit.environments || []).includes(env)) { mount(crumbs, empty("Unknown landing zone", bu + " · " + env + " is not one of your zones.")); return; }
    if (!capOn(agent, "cost_history")) { mount(crumbs, empty("Cost history is not available", "This server does not report it (capability cost_history is off).")); return; }
    const body = h("div", { class: "stack" }, skeleton(3));
    const seg = h("span");
    mount(crumbs, h("div", { class: "page-head" }, h("h1", { text: bu + " · " + env }), seg), body);
    async function draw() {
      fill(seg, segmented(ZONE_PERIODS, zoneDays, (v) => { zoneDays = v; draw(); }, "Period"));
      const days = zoneDays;
      let hist;
      try { hist = await loadCostHistory(bu, env, days); } catch (e) { if (g === gen && days === zoneDays) fill(body, errorBox(e, "Could not load cost history")); return; }
      if (g !== gen || days !== zoneDays) return;
      const series = seriesOf(hist);
      const budget = num(hist.monthly_budget);
      const out = [];
      if (!series.length) out.push(empty("No cost history yet", "History appears once resources are planned or applied here."));
      else {
        const first = series[0].v;
        const now = series[series.length - 1].v;
        out.push(section("Reserved spend, last " + days + " days", costChart(series, budget),
          kv([["Now", money(now)], ["Budget", budget ? money(budget) : null], ["Used", budget ? Math.round((now / budget) * 100) + "%" : null], ["Change over period", deltaCell(now - first)]]),
          hist.currency_note ? h("p", { class: "muted small", text: hist.currency_note }) : null));
      }
      out.push(section("By pattern", table(["Pattern", "Reserved now", "Change over period"], (hist.by_pattern || []).map((b) => h("tr", null,
        h("td", { text: b.pattern }), h("td", { class: "num", text: money(b.reserved_now) }), h("td", { class: "num" }, deltaCell(b.change_over_period)))), "No pattern spend in this period.")));
      out.push(section("Top movers", table(["Resource", "Pattern", "Change", "When"], (hist.movers || []).map((m) => h("tr", null,
        h("td", null, h("a", { href: "#resources/" + enc(m.resource_id), title: m.resource_id, text: (m.labels && m.labels.name) || m.resource_id })),
        h("td", { text: m.pattern }), h("td", { class: "num" }, deltaCell(m.delta)), h("td", null, when(m.at)))), "No resource changed its reservation in this period.")));
      if (hist.truncated) out.push(h("p", { class: "muted small", text: "More resources exist than were scanned; totals may be incomplete." }));
      fill(body, out);
    }
    await draw();
  }

  /* ---------- what changes between two pattern versions ---------- */
  const shortCommit = (c) => String(c || "").slice(0, 8);
  function fieldChange(k, v) {
    if (Array.isArray(v) && v.length === 2) return k + ": " + text(v[0]) + " → " + text(v[1]);
    if (v === true) return k.replace(/_/g, " ");
    return k + ": " + text(v);
  }
  function changesView(c) {
    const newReq = Array.isArray(c.new_required_inputs) ? c.new_required_inputs : [];
    const inputs = c.inputs || {};
    const added = inputs.added || [];
    const removed = inputs.removed || [];
    const changed = inputs.changed || [];
    const out = [];
    if (newReq.length) {
      out.push(h("div", { class: "warn-box", role: "alert" },
        h("b", { text: "New required inputs: " + newReq.join(", ") }),
        h("div", { text: "The one-click upgrade cannot supply these. Submit the upgrade through POST /operations with a value for each." })));
    }
    out.push(h("div", { class: "muted small", text: text(c.from) + " (" + shortCommit(c.from_commit) + ") → " + text(c.to) + " (" + shortCommit(c.to_commit) + ")" }));
    const commits = c.commits || [];
    out.push(commits.length
      ? h("ul", { class: "commits" }, commits.map((m) => h("li", null, h("span", { class: "mono", text: shortCommit(m.commit) }), " ", h("span", { text: m.subject }))))
      : h("p", { class: "muted small", text: "No commits between these tags." }));
    if (!added.length && !removed.length && !changed.length) out.push(h("p", { class: "muted small", text: "No input changes." }));
    if (added.length) {
      out.push(h("h2", { class: "sub", text: "Inputs added" }), table(["Input", "Required", "Type", "Description"], added.map((a) => h("tr", null,
        h("td", { class: "mono", text: a.name }),
        h("td", null, a.required ? h("span", { class: "tag bad", text: "required" }) : "no"),
        h("td", { text: Array.isArray(a.type) ? a.type.join(" | ") : text(a.type) }),
        h("td", { text: text(a.description) })))));
    }
    if (removed.length) out.push(h("h2", { class: "sub", text: "Inputs removed" }), chips(removed));
    if (changed.length) {
      out.push(h("h2", { class: "sub", text: "Inputs changed" }), table(["Input", "What changed"], changed.map((x) => h("tr", null,
        h("td", { class: "mono", text: x.name }),
        h("td", { text: Object.entries(x.fields || {}).map(([k, v]) => fieldChange(k, v)).join("; ") })))));
    }
    return out;
  }
  // Changes between two tags never change; remember them so auto-refresh re-renders don't flicker.
  const changesMemo = new Map();
  const changesKey = (o) => [o.name, o.from, o.to, o.bu || "", o.env || ""].join("|");
  function loadChanges(body, o, g) {
    const memo = changesMemo.get(changesKey(o));
    if (memo) {
      fill(body, changesView(memo));
      if (o.onLoaded) o.onLoaded(memo);
      return;
    }
    const seq = String(Number(body.dataset.seq || 0) + 1);
    body.dataset.seq = seq;
    fill(body, h("span", { class: "muted small", text: "Loading changes..." }));
    api(`/patterns/${enc(o.name)}/changes`, { query: { from: o.from, to: o.to, business_unit: o.bu, environment: o.env } }).then((c) => {
      changesMemo.set(changesKey(o), c);
      if (g === gen && body.dataset.seq === seq) {
        fill(body, changesView(c));
        if (o.onLoaded) o.onLoaded(c);
      }
    }, (e) => { if (g === gen && body.dataset.seq === seq) fill(body, errorBox(e, "Could not load changes")); });
  }
  function changesBlock(o, g) {
    const body = h("div", { class: "stack" });
    loadChanges(body, o, g);
    return h("div", { class: "changes" }, h("h2", { class: "sub", text: "What changes: " + o.from + " → " + o.to }), body);
  }

  /* ---------- pattern contract check ---------- */
  // A version's commit never changes, so a result is kept for the page's lifetime.
  const checkMemo = new Map();
  const checkKey = (name, version) => name + "@" + (version || "");
  async function loadCheck(name, version) {
    const key = checkKey(name, version);
    if (checkMemo.has(key)) return checkMemo.get(key);
    const c = await api(`/patterns/${enc(name)}/check`, { query: { version } });
    checkMemo.set(key, c);
    return c;
  }
  const countOf = (n, word) => n + " " + word + (n === 1 ? "" : "s");
  function contractPill(c) {
    const errors = Number(c.errors) || 0;
    const warnings = Number(c.warnings) || 0;
    if (errors) return h("span", { class: "pill st-failed", text: countOf(errors, "error") + (warnings ? ", " + countOf(warnings, "warning") : "") });
    if (warnings) return h("span", { class: "pill st-planned", text: countOf(warnings, "warning") });
    return h("span", { class: "pill st-ready", text: "passes" });
  }
  const FINDING_LEVELS = [["error", "Errors"], ["warning", "Warnings"], ["info", "Notes"]];
  function checkView(c) {
    const findings = Array.isArray(c.findings) ? c.findings : [];
    const out = [h("div", { class: "row" }, contractPill(c),
      h("span", { class: "muted small", text: text(c.name) + "@" + text(c.version) + (c.commit ? " (" + shortCommit(c.commit) + ")" : "") }))];
    FINDING_LEVELS.forEach(([level, title]) => {
      const list = findings.filter((f) => f.level === level);
      if (!list.length) return;
      out.push(h("h2", { class: "sub", text: title + " (" + list.length + ")" }),
        h("ul", { class: "findings" }, list.map((f) => h("li", { class: "finding lv-" + level },
          h("div", { class: "row" }, h("span", { class: "chip mono code", text: f.code }),
            f.file ? h("span", { class: "mono loc", text: f.file + (f.line ? ":" + f.line : "") }) : null),
          h("div", { text: f.message })))));
    });
    if (!findings.length) out.push(h("p", { class: "muted small", text: "No findings." }));
    out.push(h("p", { class: "muted small", text: "Static check of the pattern's Terraform against the platform contract; see docs/pattern-onboarding.md." }));
    return out;
  }
  function contractPanel(p, versions, g) {
    const body = h("div", { class: "stack" });
    const list = versions.length ? versions : [p.version].filter(Boolean);
    const sel = h("select", { "aria-label": "Contract version" }, list.map((v) => h("option", { value: v, text: v })));
    if (list.includes(p.version)) sel.value = p.version;
    let seq = 0;
    const go = () => {
      const mine = ++seq;
      const version = sel.value || undefined;
      fill(body, h("span", { class: "muted small", text: "Checking..." }));
      loadCheck(p.name, version).then((c) => { if (g === gen && mine === seq) fill(body, checkView(c)); },
        (e) => { if (g === gen && mine === seq) fill(body, errorBox(e, "Could not check the contract")); });
    };
    sel.addEventListener("change", go);
    go();
    return section("Contract", list.length > 1 ? h("div", { class: "row" }, h("label", { class: "inline" }, "Version", sel)) : null, body);
  }
  // Small badge per catalog card, filled in after the list is on screen. Failures are ignored.
  const CHECK_BADGE_MAX = 20;
  async function fillContractBadges(items, g) {
    for (const p of items.slice(0, CHECK_BADGE_MAX)) {
      if (g !== gen) return;
      const slot = document.querySelector('[data-contract="' + CSS.escape(p.name) + '"]');
      if (!slot) continue;
      const version = p.versions && p.versions.length ? p.versions[p.versions.length - 1] : undefined;
      try {
        const c = await loadCheck(p.name, version);
        if (g !== gen) return;
        const errors = Number(c.errors) || 0;
        const warnings = Number(c.warnings) || 0;
        slot.appendChild(h("span", { class: "pill st-" + (errors ? "failed" : warnings ? "planned" : "ready"), title: "Contract check of " + text(c.version),
          text: errors ? "contract: " + errors + " err" : warnings ? "contract: " + warnings + " warn" : "contract ok" }));
      } catch (e) { /* a failed check shows no badge */ }
    }
  }
  // Resource page: warn when its own pattern@version fails the contract.
  async function addContractChip(slot, r, g) {
    try {
      const c = await loadCheck(r.pattern, r.version);
      if (g !== gen || !(Number(c.errors) > 0)) return;
      slot.appendChild(h("a", { class: "chip contract-chip", href: "#catalog/" + enc(r.pattern),
        title: "This pattern version has contract errors; open its Contract panel", text: "⚠ contract: " + countOf(Number(c.errors), "error") }));
    } catch (e) { /* no chip when the check is unavailable */ }
  }

  /* ---------- catalog ---------- */
  async function viewPatterns(_id, g) {
    const [listR, agentR] = await Promise.allSettled([api("/patterns"), loadAgent(30000)]);
    if (g !== gen) return;
    if (listR.status === "rejected") throw listR.reason;
    const agent = agentR.status === "fulfilled" ? agentR.value : null;
    if (agent) populateEnv(allEnvs(agent, []));
    let items = listR.value.items || [];
    let hidden = 0;
    if (ui.env && capOn(agent, "deployable_patterns")) {
      const lists = (agent.business_units || []).filter((u) => !ui.zone || u.name === ui.zone.bu)
        .map((u) => u.deployable_patterns && u.deployable_patterns[ui.env]).filter(Array.isArray);
      if (lists.length) {
        const ok = new Set([].concat(...lists));
        const kept = items.filter((p) => ok.has(p.name));
        hidden = items.length - kept.length;
        items = kept;
      }
    }
    const withChecks = capOn(agent, "pattern_checks");
    mount(h("div", { class: "page-head" }, h("h1", { text: "Catalog" }),
      h("span", { class: "muted small", text: items.length + (ui.env ? " patterns deployable in " + ui.env : " patterns available to you") + (hidden ? " (" + hidden + " hidden: their cloud is not targeted there)" : "") })),
      items.length ? h("div", { class: "grid" }, items.map((p) => h("a", { class: "card pat", href: "#catalog/" + enc(p.name) },
        h("div", { class: "row" }, h("b", { text: p.name }), cloudBadge(p.cloud)),
        p.description ? h("div", { class: "muted small", text: p.description }) : null,
        p.versions && p.versions.length ? h("div", { class: "muted small", text: "versions: " + p.versions.join(", ") }) : null,
        withChecks ? h("div", { class: "row", "data-contract": p.name }) : null)))
        : empty(ui.env ? "No patterns deployable in " + ui.env : "No patterns available to you", "Patterns are registered in patterns.yaml and limited per business unit."));
    if (withChecks && items.length) fillContractBadges(items, g);
  }
  // Describing a pattern under tenancy needs a placement (inputs depend on it). Use the selected
  // zone/environment, or else the first unit offering the pattern and its first environment
  // where it is deployable.
  function placeFor(agent, name) {
    const units = (agent && agent.business_units) || [];
    const unit = (ui.zone && units.find((u) => u.name === ui.zone.bu)) || units.find((u) => (u.patterns || []).includes(name));
    if (!unit) return {};
    const envs = unit.environments || [];
    const deployable = (e) => !unit.deployable_patterns || (unit.deployable_patterns[e] || []).includes(name);
    const env = (ui.zone && ui.zone.bu === unit.name && ui.zone.env) || (ui.env && envs.includes(ui.env) ? ui.env : envs.find(deployable) || envs[0]);
    return { bu: unit.name, env };
  }
  async function viewPattern(name, g) {
    const agentR = await loadAgent(30000).catch(() => null);
    const where = placeFor(agentR, name);
    const bu = where.bu;
    const p = await api(`/patterns/${enc(name)}`, { query: { business_unit: where.bu, environment: where.env } });
    if (g !== gen) return;
    const schema = p.input_schema || {};
    const props = schema.properties || {};
    const required = new Set(schema.required || []);
    const rows = Object.keys(props).sort().map((k) => {
      const s = props[k] || {};
      return h("tr", null,
        h("td", { class: "mono", text: k }),
        h("td", { text: Array.isArray(s.type) ? s.type.join(" | ") : text(s.type) }),
        h("td", { text: required.has(k) ? "yes" : "" }),
        h("td", { class: "mono", text: s.default === undefined ? "" : text(s.default) }),
        h("td", { class: "mono", text: s.enum ? s.enum.map(text).join(", ") : "" }),
        h("td", { text: text(s.description) }));
    });
    const versions = Array.isArray(p.versions) ? p.versions : [];
    let compare = null;
    if (capOn(agentR, "pattern_changes") && versions.length >= 2) {
      const to0 = versions.includes(p.version) ? p.version : versions[versions.length - 1];
      const at = versions.indexOf(to0);
      const from0 = at > 0 ? versions[at - 1] : versions[at + 1];
      const fromSel = h("select", { "aria-label": "From version" }, versions.map((v) => h("option", { value: v, text: v })));
      const toSel = h("select", { "aria-label": "To version" }, versions.map((v) => h("option", { value: v, text: v })));
      fromSel.value = from0;
      toSel.value = to0;
      const body = h("div", { class: "stack" });
      const go = () => {
        if (fromSel.value === toSel.value) { body.dataset.seq = String(Number(body.dataset.seq || 0) + 1); fill(body, h("p", { class: "muted small", text: "Pick two different versions." })); return; }
        loadChanges(body, { name: p.name, from: fromSel.value, to: toSel.value, bu, env: ui.env }, g);
      };
      fromSel.addEventListener("change", go);
      toSel.addEventListener("change", go);
      compare = section("What changes between versions",
        h("div", { class: "row" }, h("label", { class: "inline" }, "From", fromSel), h("label", { class: "inline" }, "To", toSel)), body);
      go();
    }
    mount(
      h("div", { class: "crumbs" }, h("a", { href: "#catalog", text: "Catalog" }), " / ", h("span", { text: p.name })),
      h("h1", null, p.name, " ", cloudBadge(p.cloud)),
      where.bu && where.env ? h("div", { class: "row" }, h("button", { class: "btn primary", type: "button", text: "Deploy this pattern", onclick: () => startDeploy({ bu: where.bu, env: where.env, pattern: p.name }) }),
        h("span", { class: "muted small", text: "into " + where.bu + " · " + where.env })) : null,
      section("Pattern", kv([
        ["Cloud", p.cloud], ["Version", p.version], ["Commit", p.commit ? h("span", { class: "mono", text: p.commit }) : null],
        ["Versions", p.versions && p.versions.length ? chips(p.versions) : null],
      ])),
      compare,
      capOn(agentR, "pattern_checks") ? contractPanel(p, versions, g) : null,
      section("Input schema", table(["Input", "Type", "Required", "Default", "Allowed", "Description"], rows, "No inputs.")),
      section("Example inputs", h("pre", { text: JSON.stringify(p.example, null, 2) })));
  }

    /* ---------- teams (operators only) ---------- */
  // Team documents hold cloud target identifiers: they are kept in memory only (never stored,
  // never logged) and only rendered for operators.
  const TEAM_NAME_RE = /^[a-z][a-z0-9-]{1,40}$/;
  const ENV_NAME_RE = /^[a-z][a-z0-9_-]{0,30}$/;
  const TCLOUDS = [
    { id: "azure", label: "Azure", key: "subscription_id", field: "Subscription ID", hint: "a GUID such as 00000000-0000-0000-0000-000000000000",
      ok: (v) => /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/.test(v), regions: ["eastus", "eastus2", "westus2", "westeurope", "northeurope", "uksouth"] },
    { id: "aws", label: "AWS", key: "aws_account_id", field: "AWS account ID", hint: "12 digits",
      ok: (v) => /^[0-9]{12}$/.test(v), regions: ["us-east-1", "us-east-2", "us-west-2", "eu-west-1", "eu-central-1", "ap-southeast-2"] },
    { id: "gcp", label: "GCP", key: "project_id", field: "GCP project ID", hint: "6 to 30 characters: lowercase letters, digits and -",
      ok: (v) => /^[a-z][a-z0-9-]{4,28}[a-z0-9]$/.test(v), regions: ["us-central1", "us-east1", "us-west1", "europe-west1", "europe-west4", "asia-southeast1"] },
  ];
  const TSTEPS = ["Basics", "Landing zones", "Regions and patterns", "Platform inputs", "Review"];
  const UNIT_HANDLED = ["groups", "inject", "patterns", "regions", "environments"];
  const ENV_HANDLED = ["budget_monthly", "allow_destroy", "protected_resource_types", "groups", "network", "targets"];
  const TFLEVELS = [["error", "Errors"], ["warning", "Warnings"], ["info", "Notes"]];
  const tm = { saved: null, flash: null, lf: { q: "", state: "all" } };

  const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
  const strs = (v) => (Array.isArray(v) ? v.filter((x) => typeof x === "string") : []);
  const canon = (v) => (Array.isArray(v) ? v.map(canon) : isObj(v) ? Object.keys(v).sort().reduce((o, k) => { o[k] = canon(v[k]); return o; }, {}) : v);
  const sameDoc = (a, b) => JSON.stringify(canon(a)) === JSON.stringify(canon(b));
  const isOperator = (agent) => capOn(agent, "team_admin") && Boolean(agent.caller && agent.caller.operator === true);
  const nOf = (n, word) => n + " " + word + (n === 1 ? "" : "s");

  function errParts(e) {
    const err = e instanceof ApiError && e.body && e.body.error;
    const d = err && isObj(err.detail) ? err.detail : {};
    return {
      status: e instanceof ApiError ? e.status : -1, reason: (err && err.reason) || "", detail: d,
      findings: Array.isArray(d.findings) ? d.findings : [], codes: Array.isArray(d.warning_codes) ? d.warning_codes : [], report: isObj(d.report) ? d.report : null,
    };
  }
  const opsOnly = (agent) => empty("Operators only", agent && !capOn(agent, "team_admin")
    ? "This server does not offer team administration (capability team_admin is off)."
    : "Team administration needs the operator role. Ask a platform operator.");
  function sourceNote() {
    return h("div", { class: "note", role: "note" },
      h("b", { text: "Teams are read-only on this server." }),
      h("p", { class: "small", text: "Teams come from a tenants file (FORGEAPI_TENANTS_PATH), so writes are refused with 409 tenants_source_file. To manage teams here: set the setting below, restart the API, then use Import from YAML to load your tenants.yaml into the database." }),
      h("pre", { class: "snippet", text: "FORGEAPI_TENANTS_SOURCE=db" }),
      h("a", { class: "btn sm", href: "#teams/import", text: "Import from YAML" }));
  }
  const flashNote = () => { const f = tm.flash; tm.flash = null; return f ? h("div", { class: "note ok-note", role: "status" }, f) : null; };
  const sourceBadge = (s) => h("span", { class: "chip src-" + cls(s), title: s === "file" ? "Read from a tenants file (read-only)" : "Stored in the database", text: s === "file" ? "file (read-only)" : s || "unknown" });

  /* API wrappers: every literal path is checked against /openapi.json by the tests */
  const getTeams = () => api("/admin/teams");
  const getTeam = (name) => api(`/admin/teams/${enc(name)}`, { withHeaders: true });
  const validateTeam = (name, team) => api("/admin/teams/validate", { method: "POST", body: { name, team } });

  /* ---------- clipboard ---------- */
  function fallbackCopy(value) {
    const ta = h("textarea", { readonly: true, "aria-hidden": "true", tabindex: "-1", class: "sr" });
    ta.value = value;
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    return ok;
  }
  function copyBtn(value, label) {
    const b = h("button", { type: "button", class: "btn sm copy", text: "Copy", "aria-label": "Copy " + label });
    b.addEventListener("click", () => {
      const done = (ok) => { b.textContent = ok ? "Copied" : "Copy failed"; setTimeout(() => { b.textContent = "Copy"; }, 1500); };
      let p = null;
      try { p = navigator.clipboard && navigator.clipboard.writeText ? navigator.clipboard.writeText(value) : null; } catch (e) { p = null; }
      if (p) p.then(() => done(true), () => done(fallbackCopy(value)));
      else done(fallbackCopy(value));
    });
    return b;
  }
  const idRow = (value, label) => h("div", { class: "idrow" }, h("span", { class: "mono", text: value }), copyBtn(value, label));

  /* ---------- findings, impact, diff, acknowledgements ---------- */
  function findingsList(findings, onJump) {
    const list = Array.isArray(findings) ? findings : [];
    if (!list.length) return h("p", { class: "muted small", text: "No findings." });
    return TFLEVELS.map(([level, title]) => {
      const rows = list.filter((f) => f.level === level);
      if (!rows.length) return null;
      return h("div", null, h("h2", { class: "sub", text: title + " (" + rows.length + ")" }),
        h("ul", { class: "findings" }, rows.map((f) => h("li", { class: "finding lv-" + cls(level) },
          h("div", { class: "row" }, h("span", { class: "chip mono code", text: f.code }),
            f.path ? (onJump ? h("button", { type: "button", class: "linkish mono", title: "Go to this field", text: f.path, onclick: () => onJump(f.path) }) : h("span", { class: "mono loc", text: f.path })) : null),
          h("div", { text: f.message })))));
    });
  }
  function budgetBar(label, reserved, budget) {
    const res = num(reserved) || 0;
    if (budget === null || budget === undefined) {
      return h("div", { class: "bbar" }, h("span", { class: "bbar-l small", text: label }), h("span", { class: "muted small", text: "unlimited (reserved " + money(res) + ")" }));
    }
    const lim = num(budget) || 0;
    const frac = lim > 0 ? Math.min(1, res / lim) : 1;
    const fillEl = h("div", { class: "meter-fill" + (res > lim ? " full" : frac >= 0.8 ? " hot" : "") });
    fillEl.style.width = Math.round(frac * 100) + "%";
    return h("div", { class: "bbar" }, h("span", { class: "bbar-l small", text: label }),
      h("div", { class: "meter", role: "meter", "aria-valuemin": 0, "aria-valuemax": lim, "aria-valuenow": res, "aria-label": label + " budget" }, fillEl),
      h("span", { class: "small" + (res > lim ? " bad-t" : ""), text: money(res) + " of " + money(lim) + (res > lim ? ": over budget" : "") }));
  }
  function impactPanel(impact) {
    if (!impact) return null;
    const res = impact.resources || {};
    const bud = impact.budgets || {};
    const envs = Array.from(new Set(Object.keys(res).concat(Object.keys(bud)))).sort();
    if (!envs.length) return h("div", { class: "impact" }, h("h2", { class: "sub", text: "Impact preview" }), h("p", { class: "muted small", text: "No environments to compare." }));
    return h("div", { class: "impact" }, h("h2", { class: "sub", text: "Impact preview" }),
      envs.map((env) => {
        const n = num(res[env]) || 0;
        const b = bud[env];
        return h("div", { class: "impact-env" },
          h("div", { class: "row" }, h("span", { class: "zone-env", text: env }),
            h("span", { class: n ? "b-update" : "muted small", text: n ? nOf(n, "resource") + " in " + env + " would be affected" : "no resources in " + env })),
          b ? [budgetBar("Now", b.reserved, b.current_budget), budgetBar("New", b.reserved, b.new_budget)] : null);
      }));
  }
  function diffView(c) {
    const groups = [["added", "Added", "diff-add", "+"], ["removed", "Removed", "diff-rem", "-"], ["changed", "Changed", "diff-chg", "~"]];
    const ch = c || {};
    if (!groups.some(([k]) => (ch[k] || []).length)) return h("p", { class: "muted small", text: "No document changes (a state change only)." });
    return h("div", { class: "diff" }, groups.map(([k, title, klass, sym]) => (ch[k] || []).length
      ? h("div", { class: "diff-g " + klass }, h("h2", { class: "sub", text: title + " (" + ch[k].length + ")" }),
        h("ul", { class: "diff-l" }, ch[k].map((p) => h("li", null, h("span", { class: "diff-s", "aria-hidden": "true", text: sym }), h("span", { class: "mono", text: p })))))
      : null));
  }
  function changeBadges(c) {
    const ch = c || {};
    const parts = [["+", (ch.added || []).length, "b-create", "added"], ["~", (ch.changed || []).length, "b-update", "changed"], ["-", (ch.removed || []).length, "b-delete", "removed"]].filter((p) => p[1]);
    if (!parts.length) return h("span", { class: "muted small", text: "no document change" });
    return h("span", { class: "mini" }, parts.map((p) => h("span", { class: p[2], title: p[3], text: p[0] + p[1] })));
  }
  function warningCodes(findings) {
    return Array.from(new Set((findings || []).filter((f) => f.level === "warning").map((f) => f.code))).sort();
  }
  function ackPanel(codes, findings, ticked, onChange) {
    if (!codes.length) return null;
    return h("fieldset", { class: "ack" },
      h("legend", { text: "Acknowledge warnings" }),
      h("p", { class: "muted small", text: "Warnings do not block the change, but each one must be acknowledged before it is saved." }),
      codes.map((c, i) => {
        const id = "ack-" + i;
        const cb = h("input", { type: "checkbox", id, checked: ticked.has(c) });
        cb.addEventListener("change", () => { if (cb.checked) ticked.add(c); else ticked.delete(c); onChange(); });
        return h("div", { class: "ack-i" }, cb, h("label", { for: id }, h("span", { class: "chip mono code", text: c }),
          findings.filter((f) => f.code === c).map((f) => h("div", { class: "small", text: f.message + (f.path ? " (" + f.path + ")" : "") }))));
      }));
  }
  const allTicked = (codes, ticked) => codes.every((c) => ticked.has(c));

  /* ---------- form widgets ---------- */
  function setPath(el, p) { el._path = typeof p === "function" ? p : () => p; return el; }
  function field(id, label, control, hint) {
    return h("div", { class: "field" }, h("label", { for: id, text: label }), control, hint ? h("div", { class: "muted small", id: id + "-hint", text: hint }) : null);
  }
  function textIn(id, value, onInput, attrs) {
    const el = h("input", Object.assign({ id, type: "text", value: value || "", autocomplete: "off", spellcheck: "false" }, attrs || {}));
    el.addEventListener("input", () => onInput(el.value));
    return el;
  }
  function chipEditor(o) {
    const list = h("div", { class: "chips chip-ed", role: "list", "aria-label": o.label });
    const input = h("input", { id: o.id, type: "text", placeholder: o.placeholder || "", autocomplete: "off", spellcheck: "false" });
    if (o.path) setPath(input, o.path);
    function draw() {
      fill(list, o.values.map((v, i) => h("span", { class: "chip rm" + (o.mono ? " mono" : ""), role: "listitem" }, v,
        h("button", { type: "button", class: "x", "aria-label": "Remove " + v, text: "x", onclick: () => { o.values.splice(i, 1); draw(); o.onChange(); input.focus(); } }))));
      if (!o.values.length) list.appendChild(h("span", { class: "muted small", text: o.empty || "none yet" }));
    }
    function addFrom(raw) {
      const parts = raw.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);
      let added = 0;
      for (const p of parts) if (!o.values.includes(p)) { o.values.push(p); added++; }
      input.value = "";
      if (added) { draw(); o.onChange(); }
    }
    input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); addFrom(input.value); } });
    input.addEventListener("blur", () => { if (input.value.trim()) addFrom(input.value); });
    draw();
    const el = h("div", { class: "chip-field" }, list, h("div", { class: "row" }, input,
      h("button", { type: "button", class: "btn sm", text: "Add", "aria-label": "Add to " + o.label, onclick: () => { addFrom(input.value); input.focus(); } })));
    el.redraw = draw;
    return el;
  }
  function kvEditor(o) {
    const host = h("div", { class: "kv-ed" });
    function draw() {
      fill(host, o.rows.map((r, i) => {
        const ki = h("input", { id: o.idp + "-k" + i, type: "text", value: r.k, placeholder: o.keyPh || "name", "aria-label": o.label + " name " + (i + 1), list: o.list || null, autocomplete: "off", spellcheck: "false" });
        const vi = h("input", { id: o.idp + "-v" + i, type: "text", value: r.v, placeholder: o.valPh || "value", "aria-label": o.label + " value " + (i + 1), autocomplete: "off", spellcheck: "false" });
        const at = () => (typeof o.path === "function" ? o.path() : o.path) + "." + r.k;
        setPath(ki, at);
        setPath(vi, at);
        ki.addEventListener("input", () => { r.k = ki.value; o.onChange(); });
        vi.addEventListener("input", () => { r.v = vi.value; o.onChange(); });
        return h("div", { class: "kv-row" }, ki, vi,
          h("button", { type: "button", class: "btn sm", text: "Remove", "aria-label": "Remove " + o.label + " row " + (i + 1), onclick: () => { o.rows.splice(i, 1); draw(); o.onChange(); } }));
      }));
      if (!o.rows.length) host.appendChild(h("span", { class: "muted small", text: o.empty || "none yet" }));
    }
    draw();
    return h("div", { class: "kv-field" }, host,
      h("button", { type: "button", class: "btn sm", text: "Add row", onclick: () => { o.rows.push({ k: "", v: "", t: "string" }); draw(); o.onChange(); const last = host.querySelector(".kv-row:last-of-type input"); if (last) last.focus(); } }));
  }

  /* ---------- draft model <-> team document ---------- */
  const emptyTarget = () => ({ on: false, id: "", region: "", extra: {} });
  function emptyEnv(name) {
    return { name, budget: "", allowDestroy: true, hadAllow: false, protectedTypes: [], groups: [], network: [], targets: { azure: emptyTarget(), aws: emptyTarget(), gcp: emptyTarget() }, extraTargets: {}, extra: {} };
  }
  function emptyDraft() {
    return { name: "", groups: [], patterns: [], allowed: [], defaultRegion: "", inject: [{ k: "business_unit", v: "", t: "string" }, { k: "cost_center", v: "", t: "string" }], envs: [], extra: {} };
  }
  const toRows = (o) => (isObj(o) ? Object.entries(o).map(([k, v]) => ({ k, v: v === null || v === undefined ? "" : String(v), t: typeof v })) : []);
  function docToDraft(name, doc) {
    const d = emptyDraft();
    const D = isObj(doc) ? doc : {};
    d.name = name;
    d.inject = toRows(D.inject);
    d.groups = strs(D.groups);
    d.patterns = strs(D.patterns);
    const rg = isObj(D.regions) ? D.regions : {};
    d.allowed = strs(rg.allowed);
    d.defaultRegion = typeof rg.default === "string" ? rg.default : "";
    for (const [en, raw] of Object.entries(isObj(D.environments) ? D.environments : {})) {
      const spec = isObj(raw) ? raw : {};
      const e = emptyEnv(en);
      e.budget = spec.budget_monthly === undefined || spec.budget_monthly === null ? "" : String(spec.budget_monthly);
      if ("allow_destroy" in spec) { e.hadAllow = true; e.allowDestroy = spec.allow_destroy !== false; }
      e.protectedTypes = strs(spec.protected_resource_types);
      e.groups = strs(spec.groups);
      e.network = toRows(spec.network);
      for (const [c, t] of Object.entries(isObj(spec.targets) ? spec.targets : {})) {
        const meta = TCLOUDS.find((m) => m.id === c);
        if (!meta || !isObj(t)) { e.extraTargets[c] = t; continue; }
        const extra = Object.assign({}, t);
        delete extra[meta.key];
        delete extra.region;
        e.targets[c] = { on: true, id: t[meta.key] === undefined || t[meta.key] === null ? "" : String(t[meta.key]), region: t.region === undefined || t.region === null ? "" : String(t.region), extra };
      }
      for (const k of Object.keys(spec)) if (!ENV_HANDLED.includes(k)) e.extra[k] = spec[k];
      d.envs.push(e);
    }
    for (const k of Object.keys(D)) if (!UNIT_HANDLED.includes(k)) d.extra[k] = D[k];
    return d;
  }
  function rowsToObj(rows) {
    const o = {};
    for (const r of rows) {
      const k = r.k.trim();
      if (!k || r.v.trim() === "") continue;
      let v = r.v;
      if (r.t === "number" && isFinite(Number(v))) v = Number(v);
      else if (r.t === "boolean" && (v === "true" || v === "false")) v = v === "true";
      o[k] = v;
    }
    return o;
  }
  function draftToDoc(d) {
    const doc = Object.assign({}, d.extra);
    if (d.groups.length) doc.groups = d.groups.slice();
    if (d.patterns.length) doc.patterns = d.patterns.slice();
    if (d.allowed.length || d.defaultRegion) {
      doc.regions = {};
      if (d.allowed.length) doc.regions.allowed = d.allowed.slice();
      if (d.defaultRegion) doc.regions.default = d.defaultRegion;
    }
    const inj = rowsToObj(d.inject);
    if (Object.keys(inj).length) doc.inject = inj;
    const envs = {};
    for (const e of d.envs) {
      const n = e.name.trim();
      if (!n) continue;
      const spec = Object.assign({}, e.extra);
      if (e.groups.length) spec.groups = e.groups.slice();
      const targets = Object.assign({}, e.extraTargets);
      for (const c of TCLOUDS) {
        const t = e.targets[c.id];
        if (t.on) targets[c.id] = Object.assign({}, t.extra, { [c.key]: t.id.trim(), region: t.region.trim() });
      }
      if (Object.keys(targets).length) spec.targets = targets;
      const b = e.budget.trim();
      if (b !== "") spec.budget_monthly = isFinite(Number(b)) ? Number(b) : b;
      if (e.hadAllow || !e.allowDestroy) spec.allow_destroy = e.allowDestroy;
      if (e.protectedTypes.length) spec.protected_resource_types = e.protectedTypes.slice();
      const net = rowsToObj(e.network);
      if (Object.keys(net).length) spec.network = net;
      envs[n] = spec;
    }
    if (d.envs.length) doc.environments = envs;
    return doc;
  }

  /* ---------- YAML preview (generated, read-only, for reference) ---------- */
  const YAML_PLAIN = /^[A-Za-z_][A-Za-z0-9_.-]*$/;
  const YAML_WORDS = ["true", "false", "null", "yes", "no", "on", "off", "y", "n"];
  function yScalar(v) {
    if (typeof v === "string") return YAML_PLAIN.test(v) && !YAML_WORDS.includes(v.toLowerCase()) ? v : JSON.stringify(v);
    return v === null || v === undefined ? "null" : String(v);
  }
  function yamlLines(obj, ind) {
    const pad = "  ".repeat(ind);
    const out = [];
    for (const [k, v] of Object.entries(obj)) {
      const key = yScalar(k);
      if (isObj(v)) {
        if (!Object.keys(v).length) out.push(pad + key + ": {}");
        else { out.push(pad + key + ":"); out.push(...yamlLines(v, ind + 1)); }
      } else if (Array.isArray(v)) {
        out.push(pad + key + ": " + (v.every((x) => x === null || typeof x !== "object") ? "[" + v.map(yScalar).join(", ") + "]" : JSON.stringify(v)));
      } else out.push(pad + key + ": " + yScalar(v));
    }
    return out;
  }
  function teamYaml(name, doc) {
    const lines = ["business_units:"];
    if (!Object.keys(doc).length) lines.push("  " + yScalar(name || "team") + ": {}");
    else { lines.push("  " + yScalar(name || "team") + ":"); lines.push(...yamlLines(doc, 2)); }
    return lines.join("\n") + "\n";
  }
  function yamlCard(title, yaml) {
    return section(title, h("p", { class: "muted small", text: "Generated from the form, read-only, for reference. The API stores the document, not this text." }),
      h("pre", { class: "yaml", tabindex: "0", "aria-label": title, text: yaml }), h("div", { class: "row" }, copyBtn(yaml, "YAML")));
  }

  /* ---------- team form: onboarding wizard and editor ---------- */
  const uniqStrings = (a) => Array.from(new Set(a.filter(Boolean)));
  function stepForPath(p) {
    const first = String(p || "").split(".")[0];
    if (first === "environments") return 2;
    if (first === "regions" || first === "patterns") return 3;
    if (first === "inject") return 4;
    return 1;
  }
  async function viewTeamForm(g, name) {
    const edit = Boolean(name);
    let base = null;
    let etag = null;
    let source = null;
    if (edit) {
      const r = await getTeam(name);
      if (g !== gen) return;
      base = r.data;
      etag = r.etag;
      source = base.source;
    } else {
      const l = await getTeams().then((x) => x, () => null);
      source = l ? l.source : null;
    }
    const agent = await loadAgent(30000).catch(() => null);
    if (g !== gen) return;
    const withChecks = capOn(agent, "pattern_checks");
    let readOnly = source === "file";
    let draft;
    let resumed = false;
    let step = 1;
    if (edit) draft = docToDraft(name, base.team);
    else if (tm.saved) { draft = tm.saved.draft; step = tm.saved.step; resumed = true; }
    else draft = emptyDraft();
    let maxStep = edit ? TSTEPS.length : step;
    let baseDoc = edit ? base.team : null;
    let validation = { state: "idle" };
    let vseq = 0;
    let timer = null;
    let reason = "";
    const ticked = new Set();
    let extraWarn = null;
    let saving = false;
    let saveErr = null;
    let conflict = null;

    const stepper = h("ol", { class: "stepper tsteps", "aria-label": "Steps" });
    const body = h("div", { class: "tbody" });
    const checks = h("aside", { class: "card tchecks", "aria-label": "Live checks" });
    const dyn = {};

    const docNow = () => draftToDoc(draft);
    const dirtyDoc = () => !edit || !sameDoc(docNow(), baseDoc);
    function remember() { if (!edit) tm.saved = { draft, step }; }
    function changed() {
      remember();
      if (edit || step >= 2) schedule();
    }
    function schedule(delay) {
      clearTimeout(timer);
      validation = Object.assign({}, validation, { state: "pending" });
      drawChecks();
      drawDyn();
      timer = setTimeout(runValidate, delay === undefined ? 450 : delay);
    }
    async function runValidate() {
      if (g !== gen) return;
      if (!TEAM_NAME_RE.test(draft.name)) { validation = { state: "idle", note: "Enter a valid team name in step 1 to run checks." }; drawChecks(); drawDyn(); return; }
      const mine = ++vseq;
      validation = Object.assign({}, validation, { state: "loading" });
      drawChecks();
      try {
        const r = await validateTeam(draft.name, docNow());
        if (g !== gen || mine !== vseq) return;
        validation = { state: "done", report: r };
      } catch (e) {
        if (g !== gen || mine !== vseq) return;
        validation = { state: "error", err: e };
      }
      drawChecks();
      drawDyn();
    }
    function jumpTo(path) {
      const want = stepForPath(path);
      if (want !== step) goStep(want);
      let best = null;
      let score = -1;
      for (const el of view.querySelectorAll("input,select,textarea,button")) {
        if (!el._path) continue;
        const p = el._path();
        const s = p === path ? 10000 : path.startsWith(p + ".") ? p.length : p.startsWith(path + ".") ? 1 : -1;
        if (s > score) { score = s; best = el; }
      }
      if (best) { best.focus(); best.scrollIntoView({ block: "center" }); }
    }

    /* live checks panel (aside) */
    function drawChecks() {
      const v = validation;
      const out = [h("h2", { text: "Live checks" })];
      if (!edit && step < 2) {
        out.push(h("p", { class: "muted small", text: "Checks run from step 2 on, after every change. Errors block saving; warnings are shown." }));
      } else if (v.state === "idle") {
        out.push(h("p", { class: "muted small", text: v.note || "Waiting for a change to check." }));
      } else {
        const r = v.report;
        const busy = v.state === "pending" || v.state === "loading";
        out.push(h("div", { class: "row", role: "status" },
          busy ? h("span", { class: "pill st-planning", text: "checking..." }) : null,
          r ? (r.valid ? h("span", { class: "pill st-ready", text: "valid" }) : h("span", { class: "pill st-failed", text: nOf(r.errors, "error") })) : null,
          r && r.warnings ? h("span", { class: "pill st-planned", text: nOf(r.warnings, "warning") }) : null,
          r ? h("span", { class: "muted small", text: "mode: " + r.mode + (r.current_revision ? ", based on revision " + r.current_revision : "") }) : null));
        if (v.state === "error") out.push(errorBox(v.err, "Could not validate"));
        if (r) out.push(h("div", { class: busy ? "dim" : "" }, findingsList(r.findings, jumpTo), edit ? impactPanel(r.impact) : null));
      }
      fill(checks, out);
    }

    /* stepper and navigation */
    function drawStepper() {
      fill(stepper, TSTEPS.map((label, i) => {
        const n = i + 1;
        return h("li", { class: "step" + (n === step ? " current" : n < step || edit ? " done" : "") },
          h("button", { type: "button", class: "step-btn", disabled: n > maxStep, "aria-current": n === step ? "step" : null, onclick: () => goStep(n) },
            h("span", { class: "step-n", text: String(n) }), h("span", { class: "step-l", text: label })));
      }));
    }
    function goStep(n) {
      step = n;
      maxStep = Math.max(maxStep, n);
      remember();
      drawStepper();
      drawBody();
      if (step >= 2 && validation.state === "idle") schedule(0);
      else drawChecks();
      const hd = body.querySelector(".tstep-h");
      if (hd) hd.focus();
    }
    function navRow(canNext) {
      const next = h("button", { type: "button", class: "btn primary", text: "Next: " + (TSTEPS[step] || ""), disabled: !canNext, onclick: () => goStep(step + 1) });
      dyn.next = next;
      return h("div", { class: "row tnav" },
        h("button", { type: "button", class: "btn", text: "Back", disabled: step === 1, onclick: () => goStep(step - 1) }),
        step < TSTEPS.length ? next : null);
    }
    const stepHead = (n, label) => h("h2", { class: "tstep-h", tabindex: "-1", text: n + ". " + label });

    /* step 1 */
    function stepBasics() {
      const note = h("div", { id: "tf-name-note", class: "small", role: "status" });
      const nameIn = h("input", { id: "tf-name", type: "text", value: draft.name, autocomplete: "off", spellcheck: "false", maxlength: 41, disabled: edit, "aria-describedby": "tf-name-note" });
      setPath(nameIn, "name");
      const nameOk = () => TEAM_NAME_RE.test(nameIn.value);
      function paint() {
        const v = nameIn.value;
        note.className = "small " + (edit || !v ? "muted" : nameOk() ? "ok-t" : "bad-t");
        note.textContent = edit ? "A team's name cannot be changed." : !v ? "2 to 41 characters: lowercase letters, digits and -, starting with a letter." : nameOk() ? "Looks good." : "Not valid: use 2 to 41 lowercase letters, digits or -, starting with a letter.";
        if (dyn.next) dyn.next.disabled = !nameOk();
      }
      nameIn.addEventListener("input", () => { draft.name = nameIn.value.trim(); paint(); changed(); });
      const groups = chipEditor({ id: "tf-groups", label: "Entra group IDs", values: draft.groups, onChange: changed, placeholder: "Entra group object ID, then Enter", mono: true, path: "groups", empty: "No groups yet. Without groups nobody is placed in this team." });
      fill(body, stepHead(1, TSTEPS[0]),
        field("tf-name", "Team name", nameIn),
        note,
        h("p", { class: "muted small", text: "Teams carry no description field in this API. Record context in the reason when you save." }),
        field("tf-groups", "Groups: Entra group object IDs whose members belong to this team", groups, "Paste several IDs at once; separate with spaces or commas. Press Enter or Add."),
        navRow(edit || nameOk()));
      paint();
    }

    /* step 2 */
    function cloudCard(env, idx, c) {
      const t = env.targets[c.id];
      const host = h("fieldset", { class: "cloud-card" });
      const pre = "tf-e" + idx + "-" + c.id;
      function draw() {
        host.className = "cloud-card" + (t.on ? " on" : "");
        const cb = h("input", { type: "checkbox", id: pre + "-on", checked: t.on });
        setPath(cb, () => "environments." + env.name + ".targets");
        cb.addEventListener("change", () => { t.on = cb.checked; draw(); changed(); });
        const out = [h("legend", null, h("label", { class: "inline", for: pre + "-on" }, cb, " ", cloudBadge(c.id), " target"))];
        if (t.on) {
          const hint = h("div", { class: "small warn-t", id: pre + "-hint" });
          const paint = () => { hint.textContent = t.id.trim() && !c.ok(t.id.trim()) ? "Unusual format: expected " + c.hint + "." : ""; };
          const idIn = textIn(pre + "-id", t.id, (v) => { t.id = v; paint(); changed(); }, { "aria-describedby": pre + "-hint", class: "mono" });
          setPath(idIn, () => "environments." + env.name + ".targets." + c.id + "." + c.key);
          const rIn = textIn(pre + "-region", t.region, (v) => { t.region = v; changed(); }, { list: "tf-regions-" + c.id });
          setPath(rIn, () => "environments." + env.name + ".targets." + c.id + ".region");
          out.push(field(pre + "-id", c.field, idIn), hint, field(pre + "-region", "Region", rIn));
          paint();
        } else out.push(h("p", { class: "muted small", text: "Not targeted: " + c.label + " patterns cannot be placed here." }));
        fill(host, out);
      }
      draw();
      return host;
    }
    function envCard(env, idx, redraw) {
      let removing = false;
      const card = h("section", { class: "card env-card", "aria-label": "Environment " + env.name });
      function draw() {
        const nameIn = textIn("tf-e" + idx + "-name", env.name, (v) => { env.name = v.trim(); changed(); }, { maxlength: 31 });
        setPath(nameIn, () => "environments." + env.name);
        const budget = h("input", { id: "tf-e" + idx + "-budget", type: "number", min: "0", step: "any", inputmode: "decimal", value: env.budget, placeholder: "blank = unlimited" });
        setPath(budget, () => "environments." + env.name + ".budget_monthly");
        budget.addEventListener("input", () => { env.budget = budget.value; changed(); });
        const destroy = h("input", { id: "tf-e" + idx + "-destroy", type: "checkbox", checked: env.allowDestroy });
        setPath(destroy, () => "environments." + env.name + ".allow_destroy");
        destroy.addEventListener("change", () => { env.allowDestroy = destroy.checked; changed(); });
        const prot = chipEditor({ id: "tf-e" + idx + "-prot", label: "Protected resource types in " + env.name, values: env.protectedTypes, onChange: changed, placeholder: "e.g. azurerm_key_vault", mono: true, path: () => "environments." + env.name + ".protected_resource_types", empty: "none" });
        const eg = chipEditor({ id: "tf-e" + idx + "-groups", label: "Environment groups for " + env.name, values: env.groups, onChange: changed, placeholder: "Entra group object ID", mono: true, path: () => "environments." + env.name + ".groups", empty: "none: the team's groups apply" });
        const net = kvEditor({ idp: "tf-e" + idx + "-net", label: "Network value in " + env.name, rows: env.network, onChange: changed, path: () => "environments." + env.name + ".network", keyPh: "key, e.g. subnet_id", valPh: "value", empty: "none" });
        fill(card,
          h("div", { class: "row env-head" }, h("span", { class: "zone-env", text: env.name || "new" }),
            removing
              ? h("span", { class: "row" }, h("span", { class: "bad-t small", text: "Remove " + (env.name || "this environment") + " from the form?" }),
                h("button", { type: "button", class: "btn sm danger", text: "Confirm remove", onclick: () => { draft.envs.splice(idx, 1); changed(); redraw(); } }),
                h("button", { type: "button", class: "btn sm", text: "Keep", onclick: () => { removing = false; draw(); } }))
              : h("button", { type: "button", class: "btn sm", text: "Remove environment", onclick: () => { removing = true; draw(); } })),
          h("div", { class: "fgrid" },
            field("tf-e" + idx + "-name", "Environment name", nameIn, ENV_NAME_RE.test(env.name) || !env.name ? null : "Use lowercase letters, digits, - or _."),
            field("tf-e" + idx + "-budget", "Monthly budget", budget, "Reserved cost may not exceed it.")),
          h("div", { class: "field" }, h("label", { class: "inline", for: "tf-e" + idx + "-destroy" }, destroy, " Allow destroy plans in this environment")),
          h("h2", { class: "sub", text: "Clouds and targets" }),
          h("div", { class: "cloud-grid" }, TCLOUDS.map((c) => cloudCard(env, idx, c))),
          Object.keys(env.extraTargets).length ? h("p", { class: "muted small", text: "Other targets kept as they are: " + Object.keys(env.extraTargets).join(", ") }) : null,
          field("tf-e" + idx + "-prot", "Protected resource types (destroy is refused for these)", prot),
          field("tf-e" + idx + "-groups", "Environment groups (optional)", eg),
          h("div", { class: "field" }, h("div", { class: "lbl", text: "Network values (optional): keys supplied to patterns in this environment" }), net));
      }
      draw();
      return card;
    }
    function stepZones() {
      const list = h("div", { class: "stack" });
      const regionLists = TCLOUDS.map((c) => h("datalist", { id: "tf-regions-" + c.id }, c.regions.map((r) => h("option", { value: r }))));
      function drawEnvs() {
        fill(list, draft.envs.length ? draft.envs.map((e, i) => envCard(e, i, drawEnvs)) : empty("No landing zones yet", "Add at least one environment. Each environment targets one or more clouds."));
      }
      const addIn = h("input", { id: "tf-newenv", type: "text", placeholder: "dev, test, prod...", autocomplete: "off", spellcheck: "false", maxlength: 31 });
      const msg = h("div", { class: "small bad-t", role: "status" });
      function addEnv(raw) {
        const n = raw.trim();
        if (!ENV_NAME_RE.test(n)) { msg.textContent = "Environment names are lowercase letters, digits, - or _, starting with a letter."; return; }
        if (draft.envs.some((e) => e.name === n)) { msg.textContent = n + " is already in the form."; return; }
        msg.textContent = "";
        draft.envs.push(emptyEnv(n));
        addIn.value = "";
        changed();
        drawEnvs();
        const first = list.lastElementChild && list.lastElementChild.querySelector("input");
        if (first) first.focus();
      }
      addIn.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); addEnv(addIn.value); } });
      const quick = ["dev", "test", "prod"].filter((n) => !draft.envs.some((e) => e.name === n));
      fill(body, stepHead(2, TSTEPS[1]),
        h("p", { class: "muted small", text: "A landing zone is one environment of this team. Give each its cloud targets (identifiers are shown to operators only), budget and guardrails." }),
        list, regionLists,
        h("div", { class: "card add-env" }, field("tf-newenv", "Add an environment", addIn),
          h("div", { class: "row" }, h("button", { type: "button", class: "btn primary", text: "Add environment", onclick: () => addEnv(addIn.value) }),
            quick.map((n) => h("button", { type: "button", class: "btn", text: "Add " + n, onclick: () => addEnv(n) }))), msg),
        navRow(true));
      drawEnvs();
    }

    /* step 3 */
    async function stepRegions() {
      const allowed = chipEditor({ id: "tf-allowed", label: "Allowed regions", values: draft.allowed, onChange: () => { drawDefault(); changed(); }, placeholder: "e.g. eastus", path: "regions.allowed", empty: "none: any region of the targets" });
      const defHost = h("div", { class: "field" });
      function drawDefault() {
        const sel = h("select", { id: "tf-default" }, h("option", { value: "", text: "(none)" }), draft.allowed.map((r) => h("option", { value: r, text: r })));
        if (draft.defaultRegion && !draft.allowed.includes(draft.defaultRegion)) sel.appendChild(h("option", { value: draft.defaultRegion, text: draft.defaultRegion + " (not in the allowed list)" }));
        sel.value = draft.defaultRegion;
        setPath(sel, "regions.default");
        sel.addEventListener("change", () => { draft.defaultRegion = sel.value; changed(); });
        fill(defHost, h("label", { for: "tf-default", text: "Default region" }), sel, h("div", { class: "muted small", text: "Must be one of the allowed regions." }));
      }
      const used = () => uniqStrings(draft.envs.flatMap((e) => TCLOUDS.map((c) => (e.targets[c.id].on ? e.targets[c.id].region.trim() : ""))));
      const addUsed = h("button", { type: "button", class: "btn sm", text: "Add regions used by the targets", onclick: () => {
        for (const r of used()) if (!draft.allowed.includes(r)) draft.allowed.push(r);
        allowed.redraw();
        drawDefault();
        changed();
      } });
      const grid = h("div", { class: "grid" }, skeleton(2));
      const search = h("input", { id: "tf-psearch", type: "search", class: "search", placeholder: "Filter patterns...", "aria-label": "Filter patterns", autocomplete: "off" });
      fill(body, stepHead(3, TSTEPS[2]),
        field("tf-allowed", "Allowed regions", allowed), addUsed, defHost,
        h("h2", { class: "sub", text: "Allowed patterns" }),
        h("p", { class: "muted small", text: "Pick the patterns this team may deploy. The badges show which of your environments can place each one, based on the targets you entered." }),
        h("div", { class: "bar" }, search), grid, navRow(true));
      drawDefault();
      let items = [];
      function placement(p) {
        const envs = draft.envs.filter((e) => e.name);
        if (!envs.length) return h("span", { class: "muted small", text: "Add landing zones to see placement." });
        const can = [];
        const no = [];
        for (const e of envs) {
          const any = TCLOUDS.some((c) => e.targets[c.id].on) || Object.keys(e.extraTargets).length;
          if (!p.cloud || !any || (e.targets[p.cloud] && e.targets[p.cloud].on)) can.push(e.name); else no.push(e.name);
        }
        return h("div", { class: "stack" },
          can.length ? h("div", { class: "row" }, h("span", { class: "small ok-t", text: "Can place in" }), chips(can, "on")) : null,
          no.length ? h("div", { class: "row" }, h("span", { class: "small warn-t", text: "No " + (CLOUD_NAMES[p.cloud] || p.cloud) + " target in" }), chips(no)) : null);
      }
      function drawGrid() {
        const q = search.value.trim().toLowerCase();
        const known = new Set(items.map((p) => p.name));
        const all = items.concat(draft.patterns.filter((n) => !known.has(n)).map((n) => ({ name: n, missing: true })));
        const shown = all.filter((p) => !q || (p.name + " " + (p.cloud || "") + " " + (p.description || "")).toLowerCase().includes(q));
        fill(grid, shown.length ? shown.map((p, i) => {
          const id = "tf-pat-" + i;
          const cb = h("input", { type: "checkbox", id, checked: draft.patterns.includes(p.name) });
          setPath(cb, "patterns");
          cb.addEventListener("change", () => {
            const at = draft.patterns.indexOf(p.name);
            if (cb.checked && at < 0) draft.patterns.push(p.name);
            if (!cb.checked && at >= 0) draft.patterns.splice(at, 1);
            changed();
          });
          return h("div", { class: "card pat tpat" },
            h("label", { class: "inline", for: id }, cb, h("b", { text: p.name }), p.cloud ? cloudBadge(p.cloud) : null),
            p.missing ? h("div", { class: "small bad-t", text: "Not in the catalog: the API will flag it." }) : null,
            p.description ? h("div", { class: "muted small", text: p.description }) : null,
            p.versions && p.versions.length ? h("div", { class: "muted small", text: "versions: " + p.versions.join(", ") }) : null,
            withChecks && !p.missing ? h("div", { class: "row", "data-tcheck": p.name }) : null,
            p.missing ? null : placement(p));
        }) : empty("No patterns", "Nothing in the catalog matches."));
        if (withChecks) fillChecks(shown.filter((p) => !p.missing).slice(0, CHECK_BADGE_MAX));
      }
      async function fillChecks(list) {
        for (const p of list) {
          if (g !== gen || step !== 3) return;
          const slot = Array.from(grid.querySelectorAll("[data-tcheck]")).find((el) => el.dataset.tcheck === p.name);
          if (!slot || slot.firstChild) continue;
          try {
            const c = await loadCheck(p.name, p.versions && p.versions.length ? p.versions[p.versions.length - 1] : undefined);
            if (g !== gen || step !== 3) return;
            if (!slot.firstChild) { slot.appendChild(contractPill(c)); slot.appendChild(h("span", { class: "muted small", text: "contract " + text(c.version) })); }
          } catch (e) { /* no status when the check is unavailable */ }
        }
      }
      search.addEventListener("input", drawGrid);
      try {
        const cat = await cached("catalog-list", 30000, () => api("/patterns"));
        if (g !== gen || step !== 3) return;
        items = cat.items || [];
      } catch (e) {
        if (g !== gen || step !== 3) return;
        fill(grid, errorBox(e, "Could not load the catalog"));
        return;
      }
      drawGrid();
    }

    /* step 4 */
    function stepInject() {
      const bu = draft.inject.find((r) => r.k === "business_unit");
      if (bu && !bu.v && draft.name) { bu.v = draft.name; remember(); }
      fill(body, stepHead(4, TSTEPS[3]),
        h("p", { class: "muted small", text: "Platform inputs are injected into every pattern run for this team. Callers can never set them. The environment is always injected. Rows with an empty value are left out." }),
        h("datalist", { id: "tf-inject-keys" }, ["business_unit", "cost_center"].map((k) => h("option", { value: k }))),
        h("div", { class: "field" }, h("div", { class: "lbl", text: "Injected inputs" }),
          kvEditor({ idp: "tf-inject", label: "Injected input", rows: draft.inject, onChange: changed, path: "inject", keyPh: "business_unit", valPh: "value", list: "tf-inject-keys", empty: "none" })),
        navRow(true));
    }

    /* step 5 */
    function stepReview() {
      const reasonIn = h("input", { id: "tf-reason", type: "text", maxlength: 500, value: reason, autocomplete: "off", placeholder: edit ? "Why is this change made?" : "Why is this team onboarded?" });
      reasonIn.addEventListener("input", () => { reason = reasonIn.value; drawDyn(); });
      dyn.sum = h("div");
      dyn.yaml = h("div");
      dyn.ack = h("div");
      dyn.submit = h("div", { class: "stack" });
      dyn.conflict = h("div");
      fill(body, stepHead(5, TSTEPS[4]), dyn.sum, dyn.conflict, dyn.yaml, dyn.ack,
        field("tf-reason", "Reason (kept in the audit trail)", reasonIn), dyn.submit,
        h("div", { class: "row tnav" }, h("button", { type: "button", class: "btn", text: "Back", onclick: () => goStep(4) })));
      drawDyn();
    }
    const currentWarnings = () => {
      const r = validation.report;
      const codes = new Set(r ? warningCodes(r.findings) : []);
      if (extraWarn) for (const c of extraWarn.codes) codes.add(c);
      return Array.from(codes).sort();
    };
    const warnFindings = () => ((validation.report && validation.report.findings) || []).concat(extraWarn ? extraWarn.findings : []);
    function whyNot() {
      const r = validation.report;
      if (readOnly) return "Teams are read-only on this server.";
      if (!TEAM_NAME_RE.test(draft.name)) return "The team name is not valid (step 1).";
      if (validation.state === "pending" || validation.state === "loading") return "Checking...";
      if (validation.state !== "done" || !r) return validation.state === "error" ? "The check failed; change something or try again." : "Waiting for the check.";
      if (r.errors > 0) return nOf(r.errors, "error") + " must be fixed first.";
      if (edit && !dirtyDoc()) return "Nothing has changed yet.";
      if (!reason.trim()) return "Enter a reason.";
      if (edit && !allTicked(currentWarnings(), ticked)) return "Acknowledge each warning.";
      return "";
    }
    function drawDyn() {
      if (step !== 5 || !dyn.submit) return;
      const r = validation.report;
      const names = Object.keys(rowsToObj(draft.inject));
      fill(dyn.sum, section("Summary", kv([
        ["Team", draft.name || h("span", { class: "muted", text: "(no name yet)" })],
        ["Groups", String(draft.groups.length)],
        ["Landing zones", draft.envs.length ? h("div", { class: "chips" }, draft.envs.map((e) => { const cl = TCLOUDS.filter((c) => e.targets[c.id].on).map((c) => c.label); return h("span", { class: "chip", text: e.name + (cl.length ? ": " + cl.join(", ") : "") }); })) : null],
        ["Allowed regions", draft.allowed.length ? chips(draft.allowed) : null],
        ["Default region", draft.defaultRegion],
        ["Patterns", draft.patterns.length ? chips(draft.patterns) : null],
        ["Injected inputs", names.length ? chips(names) : null],
        ["Checks", r ? (r.valid ? "valid" : nOf(r.errors, "error")) + (r.warnings ? ", " + nOf(r.warnings, "warning") : "") : "not run yet"],
      ])));
      fill(dyn.yaml, yamlCard("Generated YAML", teamYaml(draft.name, docNow())));
      fill(dyn.ack, edit ? ackPanel(currentWarnings(), warnFindings(), ticked, drawDyn) : null);
      const why = whyNot();
      fill(dyn.submit,
        readOnly ? sourceNote() : null,
        saveErr ? saveMessage(saveErr) : null,
        h("div", { class: "row" },
          h("button", { type: "button", class: "btn primary", id: "tf-submit", text: saving ? "Saving..." : edit ? "Save changes" : "Create team", disabled: Boolean(why) || saving, onclick: () => submit() }),
          why ? h("span", { class: "muted small", text: why }) : null));
      fill(dyn.conflict, conflict ? conflictBox() : null);
    }
    function saveMessage(e) {
      const p = errParts(e);
      if (p.reason === "warnings_not_acknowledged") return h("div", { class: "warn-box", role: "alert" }, h("b", { text: "Warnings need acknowledgement" }), h("span", { text: "Tick each warning above, then save again. Nothing was written." }));
      if (p.reason === "team_exists") return h("div", { class: "alert", role: "alert" }, h("h2", { text: "That team already exists" }), h("div", null, "Open ", h("a", { href: "#teams/" + enc(draft.name), text: draft.name }), " and edit it instead."));
      if (p.reason === "team_unchanged") return h("div", { class: "warn-box", role: "alert" }, h("b", { text: "Nothing changed" }), h("span", { text: "The document equals the current revision." }));
      return errorBox(e, "Could not save");
    }
    async function submit() {
      if (saving || whyNot()) return;
      saving = true;
      saveErr = null;
      drawDyn();
      try {
        if (!edit) {
          const v = await api("/admin/teams", { method: "POST", body: { name: draft.name, team: docNow(), reason: reason.trim() } });
          if (g !== gen) return;
          tm.saved = null;
          tm.flash = "Created team " + draft.name + " at revision " + v.revision + ".";
        } else {
          const r = await api(`/admin/teams/${enc(name)}`, { method: "PUT", ifMatch: etag, withHeaders: true, body: { team: docNow(), reason: reason.trim(), acknowledge_warnings: Array.from(ticked) } });
          if (g !== gen) return;
          tm.flash = "Saved " + name + " at revision " + r.data.revision + ".";
        }
        location.hash = "#teams/" + enc(draft.name);
      } catch (e) {
        if (g !== gen) return;
        saving = false;
        saveErr = e;
        const p = errParts(e);
        if (p.status === 412) { saveErr = null; await loadConflict(); return; }
        if (p.reason === "tenants_source_file") readOnly = true;
        if (p.reason === "warnings_not_acknowledged") extraWarn = { codes: p.codes, findings: p.detail.findings || [] };
        if (p.reason === "team_invalid" && p.findings.length) {
          const errs = p.findings.filter((f) => f.level === "error").length;
          validation = { state: "done", report: { valid: false, mode: edit ? "update" : "create", current_revision: validation.report ? validation.report.current_revision : null, findings: p.findings, errors: errs, warnings: p.findings.length - errs, impact: validation.report ? validation.report.impact : null } };
          drawChecks();
        }
        drawDyn();
      }
    }
    async function loadConflict() {
      conflict = { loading: true };
      drawDyn();
      try {
        const latest = await getTeam(name);
        const rev = await api(`/admin/teams/${enc(name)}/revisions/${latest.data.revision}`);
        conflict = { latest, rev };
      } catch (e) { conflict = { err: e }; }
      if (g === gen) drawDyn();
    }
    function conflictBox() {
      const c = conflict;
      if (c.loading) return h("div", { class: "note", text: "Checking what changed..." });
      if (c.err) return errorBox(c.err, "This team changed since you opened it");
      const latest = c.latest.data;
      return h("div", { class: "alert conflict", role: "alert" },
        h("h2", { text: "This team changed since you opened it" }),
        h("p", { text: "You opened revision " + base.revision + "; revision " + latest.revision + " is now current (" + text(c.rev.action) + " by " + text(c.rev.actor) + ", " + fmtTime(c.rev.at) + "). Reason given: " + text(c.rev.reason) }),
        h("h2", { class: "sub", text: "What the latest revision changed" }), diffView(c.rev.change),
        h("div", { class: "row" },
          h("button", { type: "button", class: "btn primary", text: "Reload the latest (discard my edits)", onclick: () => {
            base = latest; etag = c.latest.etag; baseDoc = latest.team; draft = docToDraft(name, latest.team); conflict = null; ticked.clear(); extraWarn = null; saveErr = null; goStep(step); schedule(0);
          } }),
          h("button", { type: "button", class: "btn", text: "Keep my edits on top of it", onclick: () => {
            base = latest; etag = c.latest.etag; baseDoc = latest.team; conflict = null; saveErr = null; schedule(0);
          } })));
    }

    function drawBody() {
      dyn.sum = dyn.yaml = dyn.ack = dyn.submit = dyn.conflict = null;
      dyn.next = null;
      if (step === 1) stepBasics();
      else if (step === 2) stepZones();
      else if (step === 3) stepRegions();
      else if (step === 4) stepInject();
      else stepReview();
    }

    mount(
      h("div", { class: "crumbs" }, h("a", { href: "#teams", text: "Teams" }), " / ", edit ? [h("a", { href: "#teams/" + enc(name), text: name }), " / "] : null, h("span", { text: edit ? "Edit" : "Onboard" })),
      h("h1", { text: edit ? "Edit " + name : "Onboard a team" }),
      readOnly ? sourceNote() : null,
      resumed ? h("div", { class: "note", role: "status" }, "Resumed your unsaved draft (kept in this page's memory only). ",
        h("button", { type: "button", class: "btn sm", text: "Discard draft", onclick: () => { tm.saved = null; render(); } })) : null,
      stepper,
      h("div", { class: "tlayout" }, body, checks));
    drawStepper();
    drawBody();
    drawChecks();
    if (edit || step >= 2) schedule(0);
  }

  /* ---------- team list ---------- */
  async function viewTeamList(g) {
    const data = await getTeams();
    if (g !== gen) return;
    const items = data.items || [];
    const ro = data.source === "file";
    const f = tm.lf;
    const search = h("input", { id: "tm-search", type: "search", class: "search", placeholder: "Search team, environment, state...", "aria-label": "Search teams", value: f.q, autocomplete: "off" });
    const stateSeg = h("span");
    const body = h("div");
    const totalRes = sum(items, (t) => t.resources);
    const errs = sum(items, (t) => (t.findings && t.findings.errors) || 0);
    const warns = sum(items, (t) => (t.findings && t.findings.warnings) || 0);
    function row(t) {
      const fi = t.findings || { errors: 0, warnings: 0 };
      const buds = Object.keys(t.budgets || {}).sort();
      return h("tr", { class: t.state === "archived" ? "archived" : "" },
        h("td", null, h("a", { class: "team-name", href: "#teams/" + enc(t.name), text: t.name }), h("div", { class: "row" }, pill(t.state), sourceBadge(t.source))),
        h("td", { class: "num", text: String(t.groups) }),
        h("td", null, (t.environments || []).length ? h("div", { class: "chips" }, t.environments.map((e) => h("span", { class: "zone-env", text: e }))) : dash()),
        h("td", { class: "num", text: String(t.resources) }),
        h("td", { class: "budgets" }, buds.length ? buds.map((e) => budgetBar(e, t.budgets[e].reserved, t.budgets[e].monthly_budget)) : dash()),
        h("td", null, h("div", { class: "row" },
          fi.errors ? h("span", { class: "pill st-failed", text: nOf(fi.errors, "error") }) : null,
          fi.warnings ? h("span", { class: "pill st-planned", text: nOf(fi.warnings, "warning") }) : null,
          !fi.errors && !fi.warnings ? h("span", { class: "pill st-ready", text: "clean" }) : null)),
        h("td", { "data-upd": t.name }, ro ? dash() : h("span", { class: "muted small", text: "..." })),
        h("td", null, ro ? h("span", { class: "muted small", title: "Read-only source", text: "read-only" }) : h("a", { class: "btn sm", href: `#teams/${enc(t.name)}/edit`, text: "Edit", "aria-label": "Edit " + t.name })));
    }
    let shown = [];
    function draw() {
      fill(stateSeg, segmented([["all", "All (" + items.length + ")"], ["active", "Active (" + items.filter((t) => t.state === "active").length + ")"], ["archived", "Archived (" + items.filter((t) => t.state === "archived").length + ")"]], f.state, (v) => { f.state = v; draw(); }, "State"));
      const q = f.q.trim().toLowerCase();
      shown = items.filter((t) => (f.state === "all" || t.state === f.state) && (!q || (t.name + " " + t.state + " " + (t.environments || []).join(" ")).toLowerCase().includes(q)));
      fill(body, !items.length ? empty("No teams yet", ro ? "Teams come from a tenants file on this server." : "Onboard the first team, or import an existing tenants.yaml.")
        : table(["Team", "Groups", "Zones", "Resources", "Budget used", "Findings", "Updated", ""], shown.map(row), "No team matches the filter."));
      fillUpdated(shown);
    }
    async function fillUpdated(list) {
      if (ro) return;
      for (const t of list.slice(0, 30)) {
        if (g !== gen) return;
        const cell = Array.from(body.querySelectorAll("[data-upd]")).find((el) => el.dataset.upd === t.name);
        if (!cell || cell.dataset.done) continue;
        try {
          const r = await getTeam(t.name);
          if (g !== gen) return;
          cell.dataset.done = "1";
          fill(cell, h("div", { class: "small", text: text(r.data.updated_by) || "-" }), when(r.data.updated_at));
        } catch (e) { cell.dataset.done = "1"; fill(cell, dash()); }
      }
    }
    search.addEventListener("input", () => { f.q = search.value; draw(); });
    mount(
      h("div", { class: "page-head" }, h("h1", { text: "Teams" }), h("span", { class: "muted small", text: "Operators only | source: " + data.source }),
        h("span", { class: "grow" }),
        ro ? h("button", { type: "button", class: "btn primary", disabled: true, title: "Read-only: teams come from a tenants file", text: "Onboard a team" }) : h("a", { class: "btn primary", href: "#teams/new", text: "Onboard a team" }),
        h("a", { class: "btn", href: "#teams/import", text: "Import from YAML" })),
      flashNote(),
      ro ? sourceNote() : null,
      h("div", { class: "tiles" },
        tile("Teams", items.length, items.filter((t) => t.state === "active").length + " active"),
        tile("Resources", totalRes, "not destroyed"),
        tile("Errors", errs, "across all teams", null, errs ? "bad" : "ok"),
        tile("Warnings", warns, "across all teams", null, warns ? "warn" : "ok")),
      h("div", { class: "bar" }, stateSeg, search), body);
    draw();
  }

  /* ---------- team detail ---------- */
  async function viewTeamDetail(g, name) {
    const [tR, lR] = await Promise.allSettled([getTeam(name), getTeams()]);
    if (g !== gen) return;
    if (tR.status === "rejected") throw tR.reason;
    const view0 = tR.value.data;
    let etag = tR.value.etag;
    const summary = lR.status === "fulfilled" ? (lR.value.items || []).find((t) => t.name === name) : null;
    const vR = await validateTeam(name, view0.team).then((x) => x, (e) => e);
    if (g !== gen) return;
    const report = vR instanceof Error || vR instanceof ApiError ? null : vR;
    const doc = isObj(view0.team) ? view0.team : {};
    const ro = view0.source === "file";
    const archived = view0.state === "archived";
    const impact = report && report.impact ? report.impact : null;
    const live = impact ? sum(Object.keys(impact.resources || {}), (e) => impact.resources[e]) : summary ? summary.resources : 0;
    const envs = isObj(doc.environments) ? doc.environments : {};
    const flash = flashNote();

    /* archive / unarchive */
    const actionHost = h("div");
    let mode = null;
    let reasonVal = "";
    let force = false;
    let needForce = false;
    let err = null;
    let busy = false;
    function drawActions() {
      if (!mode) {
        fill(actionHost, h("div", { class: "row" },
          ro ? h("button", { type: "button", class: "btn primary", disabled: true, title: "Read-only source", text: "Edit" }) : h("a", { class: "btn primary", href: `#teams/${enc(name)}/edit`, text: "Edit" }),
          ro ? null : h("a", { class: "btn", href: `#teams/${enc(name)}/history`, text: "History" }),
          ro ? null : h("button", { type: "button", class: "btn" + (archived ? "" : " danger-o"), text: archived ? "Unarchive" : "Archive", onclick: () => { mode = archived ? "unarchive" : "archive"; err = null; drawActions(); const r = actionHost.querySelector("input[type=text]"); if (r) r.focus(); } })),
          err ? errorBox(err, "Could not complete") : null);
        return;
      }
      const isArch = mode === "archive";
      const reasonIn = h("input", { id: "tm-reason", type: "text", maxlength: 500, value: reasonVal, autocomplete: "off" });
      const go = h("button", { type: "button", class: "btn " + (isArch ? "danger" : "primary"), text: busy ? "Working..." : isArch ? "Confirm archive" : "Confirm unarchive", onclick: () => run() });
      const forceCb = h("input", { id: "tm-force", type: "checkbox", checked: force });
      function paint() { go.disabled = busy || !reasonVal.trim() || (isArch && (live > 0 || needForce) && !force); }
      reasonIn.addEventListener("input", () => { reasonVal = reasonIn.value; paint(); });
      forceCb.addEventListener("change", () => { force = forceCb.checked; paint(); });
      fill(actionHost, h("div", { class: "confirm", role: "group", "aria-label": isArch ? "Confirm archive" : "Confirm unarchive" },
        h("b", { text: isArch ? "Archive " + name + "?" : "Unarchive " + name + "?" }),
        h("p", { class: "small", text: isArch
          ? "An archived team takes no new operations or placements. Its existing resources stay visible and can still be destroyed. You can unarchive it later."
          : "The team becomes active again and accepts new operations and placements." }),
        isArch && (live > 0 || needForce) ? h("div", { class: "warn-box" },
          h("b", { text: nOf(live, "resource") + " in this team " + (live === 1 ? "is" : "are") + " not destroyed." }),
          h("span", { text: "Destroy them first, or archive anyway with force archive. Force archive does not touch the resources." }),
          h("label", { class: "inline", for: "tm-force" }, forceCb, " Force archive although resources still exist")) : null,
        field("tm-reason", "Reason (kept in the audit trail)", reasonIn),
        err ? errorBox(err, "Could not complete") : null,
        h("div", { class: "row" }, go, h("button", { type: "button", class: "btn", text: "Cancel", onclick: () => { mode = null; err = null; drawActions(); } }))));
      paint();
    }
    async function run() {
      busy = true;
      err = null;
      drawActions();
      try {
        const body = mode === "archive" ? { reason: reasonVal.trim(), force_archive: force } : { reason: reasonVal.trim() };
        const r = mode === "archive"
          ? await api(`/admin/teams/${enc(name)}/archive`, { method: "POST", ifMatch: etag, withHeaders: true, body })
          : await api(`/admin/teams/${enc(name)}/unarchive`, { method: "POST", ifMatch: etag, withHeaders: true, body });
        if (g !== gen) return;
        tm.flash = (mode === "archive" ? "Archived " : "Unarchived ") + name + " at revision " + r.data.revision + ".";
        render();
      } catch (e) {
        if (g !== gen) return;
        busy = false;
        const p = errParts(e);
        if (p.reason === "team_has_resources") { needForce = true; err = null; }
        else err = e;
        drawActions();
        if (p.status === 412) actionHost.appendChild(h("div", { class: "row" }, h("span", { class: "bad-t small", text: "This team changed since you opened it." }), h("button", { type: "button", class: "btn sm", text: "Reload", onclick: () => render() })));
      }
    }

    /* zones */
    function zoneCard(en) {
      const spec = isObj(envs[en]) ? envs[en] : {};
      const targets = isObj(spec.targets) ? spec.targets : {};
      const rows = Object.keys(targets).sort().map((c) => {
        const t = isObj(targets[c]) ? targets[c] : {};
        const meta = TCLOUDS.find((m) => m.id === c);
        const idv = meta ? t[meta.key] : null;
        return h("tr", null, h("td", null, cloudBadge(c)), h("td", null, idv ? idRow(String(idv), (meta ? meta.field : "identifier") + " for " + en + " " + c) : dash()), h("td", { class: "mono", text: text(t.region) }));
      });
      const b = impact && impact.budgets ? impact.budgets[en] : null;
      const n = impact && impact.resources ? num(impact.resources[en]) || 0 : null;
      const open = () => { ui.zone = { bu: name, env: en }; ui.env = en; ui.mine = false; location.hash = "#infrastructure"; };
      return h("section", { class: "card zone" },
        h("div", { class: "zone-head" }, h("h2", { text: en }), h("span", { class: "zone-env", text: en })),
        rows.length ? table(["Cloud", "Identifier", "Region"], rows) : h("p", { class: "muted small", text: "No cloud targets" + (spec.subscription_id ? " (legacy subscription only)." : ".") }),
        h("h2", { class: "sub", text: "Budget" }),
        b ? [budgetBar("Reserved", b.reserved, b.current_budget)] : spec.budget_monthly !== undefined ? h("span", { text: money(spec.budget_monthly) + " per month" }) : h("span", { class: "muted small", text: "unlimited" }),
        kv([
          ["Destroy", h("span", { class: "chip " + (spec.allow_destroy === false ? "off" : "on"), text: spec.allow_destroy === false ? "blocked" : "allowed" })],
          ["Protected types", strs(spec.protected_resource_types).length ? chips(strs(spec.protected_resource_types)) : null],
          ["Environment groups", strs(spec.groups).length ? h("div", { class: "stack" }, strs(spec.groups).map((x) => idRow(x, "environment group"))) : null],
          ["Network", isObj(spec.network) && Object.keys(spec.network).length ? h("div", { class: "stack" }, Object.keys(spec.network).sort().map((k) => h("div", { class: "mono", text: k + " = " + text(spec.network[k]) }))) : null],
          ["Legacy subscription", spec.subscription_id ? idRow(String(spec.subscription_id), "legacy subscription ID") : null],
        ]),
        h("div", { class: "row" },
          h("button", { type: "button", class: "btn", text: n === null ? "View infrastructure here" : "View " + nOf(n, "resource") + " here", onclick: open })));
    }
    const rg = isObj(doc.regions) ? doc.regions : {};
    mount(
      h("div", { class: "crumbs" }, h("a", { href: "#teams", text: "Teams" }), " / ", h("span", { text: name })),
      h("h1", null, name, " ", pill(view0.state), " ", sourceBadge(view0.source)),
      flash, ro ? sourceNote() : null, actionHost,
      section("Overview", kv([
        ["State", pill(view0.state)], ["Revision", ro ? "none (file source)" : String(view0.revision)], ["Source", sourceBadge(view0.source)],
        ["Created", view0.created_by ? view0.created_by + " | " + fmtTime(view0.created_at) : null],
        ["Last updated", view0.updated_by ? view0.updated_by + " | " + fmtTime(view0.updated_at) : null],
        ["Resources", summary ? String(summary.resources) + " not destroyed" : String(live)],
      ])),
      section("Groups (" + strs(doc.groups).length + ")", strs(doc.groups).length ? h("div", { class: "stack" }, strs(doc.groups).map((x) => idRow(x, "group ID"))) : h("p", { class: "muted small", text: "No groups: nobody is placed in this team." })),
      h("h2", { class: "sub", text: "Landing zones" }),
      Object.keys(envs).length ? h("div", { class: "grid" }, Object.keys(envs).sort().map(zoneCard)) : empty("No landing zones", "Edit the team to add environments."),
      h("div", { class: "grid2" },
        section("Regions", kv([["Allowed", strs(rg.allowed).length ? chips(strs(rg.allowed)) : null], ["Default", rg.default]]), !strs(rg.allowed).length && !rg.default ? h("span", { class: "muted small", text: "Not restricted." }) : null),
        section("Patterns", strs(doc.patterns).length ? h("div", { class: "chips" }, strs(doc.patterns).map((p) => h("a", { class: "chip", href: "#catalog/" + enc(p), text: p }))) : h("span", { class: "muted small", text: "No patterns allowed." }))),
      section("Injected inputs", isObj(doc.inject) && Object.keys(doc.inject).length ? kv(Object.keys(doc.inject).sort().map((k) => [k, h("span", { class: "mono", text: text(doc.inject[k]) })])) : h("span", { class: "muted small", text: "None." })),
      section("Findings", report ? [h("div", { class: "row" }, report.valid ? h("span", { class: "pill st-ready", text: "valid" }) : h("span", { class: "pill st-failed", text: nOf(report.errors, "error") }), report.warnings ? h("span", { class: "pill st-planned", text: nOf(report.warnings, "warning") }) : null), findingsList(report.findings)]
        : h("p", { class: "muted small", text: "Findings could not be computed." })),
      yamlCard("Document as YAML", teamYaml(name, doc)));
    drawActions();
  }

  /* ---------- history ---------- */
  async function viewTeamHistory(g, name) {
    let cursor = null;
    let first = true;
    const list = h("ol", { class: "revs" });
    const more = h("button", { type: "button", class: "btn more", text: "Load more", hidden: true });
    const status = h("div", { class: "muted small", "aria-live": "polite" });
    mount(h("div", { class: "crumbs" }, h("a", { href: "#teams", text: "Teams" }), " / ", h("a", { href: "#teams/" + enc(name), text: name }), " / ", h("span", { text: "History" })),
      h("h1", { text: "History of " + name }), list, status, more);
    async function load() {
      more.disabled = true;
      try {
        const page = await api(`/admin/teams/${enc(name)}/revisions`, { query: { limit: 20, before: cursor } });
        if (g !== gen) return;
        for (const r of page.items || []) list.appendChild(revItem(r));
        cursor = page.next_before;
        more.hidden = !cursor;
        if (first && !(page.items || []).length) fill(status, empty("No revisions", "Revisions appear when the team is created or changed in the database."));
        first = false;
      } catch (e) {
        if (g !== gen) return;
        fill(status, errorBox(e));
      }
      more.disabled = false;
    }
    function revItem(r) {
      const paths = [].concat(r.change.added || [], r.change.changed || [], r.change.removed || []);
      return h("li", { class: "rev" },
        h("div", { class: "row" }, h("a", { class: "rev-n", href: `#teams/${enc(name)}/history/${r.revision}`, text: "Revision " + r.revision }), h("span", { class: "pill st-" + cls(r.action === "archive" ? "destroyed" : "queued"), text: text(r.action) }), pill(r.state), changeBadges(r.change), h("span", { class: "grow" }), when(r.at)),
        h("div", { class: "small", text: text(r.actor) + ": " + text(r.reason) }),
        paths.length ? h("div", { class: "mono muted small", text: paths.slice(0, 4).join(", ") + (paths.length > 4 ? " and " + (paths.length - 4) + " more" : "") }) : null);
    }
    more.addEventListener("click", load);
    await load();
  }

  async function viewTeamRevision(g, name, rev) {
    if (!Number.isInteger(rev) || rev < 1) { mount(empty("Unknown revision")); return; }
    const [rR, tR] = await Promise.allSettled([api(`/admin/teams/${enc(name)}/revisions/${rev}`), getTeam(name)]);
    if (g !== gen) return;
    if (rR.status === "rejected") throw rR.reason;
    const r = rR.value;
    if (tR.status === "rejected") throw tR.reason;
    let cur = tR.value.data;
    let etag = tR.value.etag;
    const isCurrent = cur.revision === r.revision;
    const ro = cur.source === "file";
    const vR = isCurrent ? null : await validateTeam(name, r.team).then((x) => x, () => null);
    if (g !== gen) return;
    const ticked = new Set();
    let extraWarn = null;
    let reason = "";
    let confirm = false;
    let busy = false;
    let err = null;
    const host = h("div");
    const reasonIn = h("input", { id: "rv-reason", type: "text", maxlength: 500, autocomplete: "off" });
    reasonIn.addEventListener("input", () => { reason = reasonIn.value; paint(); });
    const codes = () => Array.from(new Set(warningCodes(vR ? vR.findings : []).concat(extraWarn ? extraWarn.codes : []))).sort();
    const warnF = () => (vR ? vR.findings : []).concat(extraWarn ? extraWarn.findings : []);
    const ready = () => !busy && reason.trim() && allTicked(codes(), ticked) && (!vR || vR.errors === 0);
    function paint() { const b = host.querySelector("[data-go]"); if (b) b.disabled = !ready(); }
    function draw() {
      if (isCurrent) { fill(host, h("p", { class: "muted small", text: "This is the current revision." })); return; }
      if (ro) { fill(host, sourceNote()); return; }
      fill(host,
        h("h2", { class: "sub", text: "What reverting would do" }),
        vR ? [h("div", { class: "row" }, vR.valid ? h("span", { class: "pill st-ready", text: "valid" }) : h("span", { class: "pill st-failed", text: nOf(vR.errors, "error") }), vR.warnings ? h("span", { class: "pill st-planned", text: nOf(vR.warnings, "warning") }) : null), findingsList(vR.findings), impactPanel(vR.impact)]
          : h("p", { class: "muted small", text: "The revision could not be checked in advance." }),
        ackPanel(codes(), warnF(), ticked, paint),
        field("rv-reason", "Reason (kept in the audit trail)", reasonIn),
        err ? errBlock(err) : null,
        confirm
          ? h("div", { class: "confirm", role: "group", "aria-label": "Confirm revert" },
            h("b", { text: "Revert " + name + " to revision " + r.revision + "?" }),
            h("p", { class: "small", text: "This writes revision " + (cur.revision + 1) + " with the document of revision " + r.revision + ". Nothing is deleted from the history." }),
            h("div", { class: "row" }, h("button", { type: "button", class: "btn primary", "data-go": "1", disabled: !ready(), text: busy ? "Reverting..." : "Confirm revert", onclick: () => go() }),
              h("button", { type: "button", class: "btn", text: "Cancel", onclick: () => { confirm = false; draw(); } })))
          : h("div", { class: "row" }, h("button", { type: "button", class: "btn primary", text: "Revert to this revision", onclick: () => { confirm = true; err = null; draw(); } })));
    }
    function errBlock(e) {
      const p = errParts(e);
      if (p.reason === "warnings_not_acknowledged") return h("div", { class: "warn-box", role: "alert" }, h("b", { text: "Warnings need acknowledgement" }), h("span", { text: "Tick each warning above, then confirm again. Nothing was written." }));
      if (p.status === 412) return h("div", { class: "alert", role: "alert" }, h("h2", { text: "This team changed since you opened it" }), h("button", { type: "button", class: "btn sm", text: "Reload", onclick: () => render() }));
      if (p.reason === "team_unchanged") return h("div", { class: "warn-box", role: "alert" }, h("b", { text: "Nothing to revert" }), h("span", { text: "The current document already equals this revision." }));
      if (p.reason === "tenants_source_file") return sourceNote();
      return errorBox(e, "Could not revert");
    }
    async function go() {
      if (!ready()) return;
      busy = true;
      err = null;
      draw();
      try {
        const res = await api(`/admin/teams/${enc(name)}/revert`, { method: "POST", ifMatch: etag, withHeaders: true, body: { revision: r.revision, reason: reason.trim(), acknowledge_warnings: Array.from(ticked) } });
        if (g !== gen) return;
        tm.flash = "Reverted " + name + " to revision " + r.revision + ", now revision " + res.data.revision + ".";
        location.hash = "#teams/" + enc(name);
      } catch (e) {
        if (g !== gen) return;
        busy = false;
        err = e;
        confirm = true;
        const p = errParts(e);
        if (p.reason === "warnings_not_acknowledged") extraWarn = { codes: p.codes, findings: p.detail.findings || [] };
        draw();
      }
    }
    mount(
      h("div", { class: "crumbs" }, h("a", { href: "#teams", text: "Teams" }), " / ", h("a", { href: "#teams/" + enc(name), text: name }), " / ", h("a", { href: `#teams/${enc(name)}/history`, text: "History" }), " / ", h("span", { text: "Revision " + r.revision })),
      h("h1", null, "Revision " + r.revision, " ", pill(r.state), " ", h("span", { class: "pill st-queued", text: text(r.action) })),
      section("Revision", kv([["Actor", r.actor], ["When", fmtTime(r.at)], ["Reason", r.reason], ["Action", r.action], ["State after", r.state], ["Current revision", String(cur.revision)]])),
      section("Changes from the previous revision", diffView(r.change)),
      section("Revert", host),
      yamlCard("Document at revision " + r.revision, teamYaml(name, isObj(r.team) ? r.team : {})));
    draw();
  }

  /* ---------- import ---------- */
  async function viewTeamImport(g) {
    const l = await getTeams().then((x) => x, () => null);
    if (g !== gen) return;
    const ro = l && l.source === "file";
    let report = null;
    let result = null;
    let err = null;
    let busy = false;
    let confirm = false;
    let reason = "";
    let extraWarn = null;
    const ticked = new Set();
    const ta = h("textarea", { id: "import-yaml", rows: 14, spellcheck: "false", autocomplete: "off", placeholder: "business_units:\n  finance:\n    groups: [00000000-0000-0000-0000-000000000001]\n    environments:\n      dev: {budget_monthly: 100}" });
    const reasonIn = h("input", { id: "import-reason", type: "text", maxlength: 500, autocomplete: "off" });
    const out = h("div", { class: "stack" });
    const dry = h("button", { type: "button", class: "btn primary", text: "Dry run", onclick: () => doDry() });
    ta.addEventListener("input", () => { report = null; result = null; confirm = false; err = null; ticked.clear(); extraWarn = null; draw(); });
    reasonIn.addEventListener("input", () => { reason = reasonIn.value; paintApply(); });
    const changing = () => (report ? report.teams.filter((t) => t.action !== "unchanged") : []);
    const codes = () => {
      if (!report) return [];
      const all = [].concat(report.findings || [], ...changing().map((t) => t.findings || []));
      return Array.from(new Set(warningCodes(all).concat(extraWarn ? extraWarn.codes : []))).sort();
    };
    const warnF = () => [].concat(report ? report.findings || [] : [], ...changing().map((t) => t.findings || []), extraWarn ? extraWarn.findings : []);
    const canApply = () => Boolean(report) && !busy && !ro && report.errors === 0 && changing().length > 0 && reason.trim() && allTicked(codes(), ticked);
    function whyApply() {
      if (!report) return "";
      return report.errors ? "Fix the errors first." : !changing().length ? "Nothing would change." : ro ? "Read-only source." : !reason.trim() ? "Enter a reason." : !allTicked(codes(), ticked) ? "Acknowledge each warning." : "";
    }
    function paintApply() {
      const b = out.querySelector("[data-apply]");
      if (b) b.disabled = !canApply();
      const w = out.querySelector("[data-why]");
      if (w) w.textContent = whyApply();
      dry.disabled = busy || !ta.value.trim();
    }
    async function call(apply) {
      busy = true;
      err = null;
      draw();
      try {
        // Apply pins every team to the revision the dry run saw, so a concurrent edit is a 412,
        // never silently overwritten.
        const expected = {};
        if (apply && report && Array.isArray(report.teams)) for (const t of report.teams) expected[t.name] = t.current_revision === undefined ? null : t.current_revision;
        const rep = await api("/admin/teams/import", { method: "POST", body: { yaml: ta.value, apply, reason: reason.trim(), acknowledge_warnings: apply ? Array.from(ticked) : [], expected_revisions: apply ? expected : {} } });
        if (g !== gen) return;
        busy = false;
        if (apply) { result = rep; report = null; confirm = false; cache = {}; } else { report = rep; result = null; confirm = false; }
      } catch (e) {
        if (g !== gen) return;
        busy = false;
        err = e;
        const p = errParts(e);
        if (p.report) { report = p.report; if (p.reason === "warnings_not_acknowledged") extraWarn = { codes: p.codes, findings: [] }; }
      }
      draw();
    }
    const doDry = () => call(false);
    const entryRow = (t) => h("tr", null,
      h("td", { class: "mono", text: t.name }),
      h("td", null, h("span", { class: "pill st-" + cls(t.action === "unchanged" ? "queued" : t.action === "create" ? "ready" : "planned"), text: t.action })),
      h("td", { class: "num", text: String(t.errors) }), h("td", { class: "num", text: String(t.warnings) }),
      h("td", { class: "small", text: t.impact ? Object.keys(t.impact.resources || {}).sort().map((e) => e + ": " + t.impact.resources[e]).join(", ") || "no resources" : "-" }),
      h("td", null, (t.findings || []).length ? h("details", null, h("summary", { text: nOf(t.findings.length, "finding") }), findingsList(t.findings)) : dash()),
      h("td", { class: "num", text: t.revision ? String(t.revision) : "-" }));
    function draw() {
      dry.disabled = busy || !ta.value.trim();
      const blocks = [];
      if (ro) blocks.push(sourceNote());
      if (err) {
        const p = errParts(err);
        blocks.push(p.reason === "tenants_source_file" ? sourceNote()
          : p.reason === "warnings_not_acknowledged" ? h("div", { class: "warn-box", role: "alert" }, h("b", { text: "Warnings need acknowledgement" }), h("span", { text: "Tick each warning below, then apply again. Nothing was written." }))
          : errorBox(err, p.reason === "team_invalid" ? "The import has errors; nothing was written" : "Import failed"));
      }
      if (busy) blocks.push(h("div", { class: "note", role: "status", text: "Working..." }));
      if (result) {
        blocks.push(h("div", { class: "note ok-note", role: "status" }, h("b", { text: result.applied ? "Applied." : "Nothing to apply: every team is unchanged." }),
          h("ul", { class: "revs" }, result.teams.map((t) => h("li", null, t.name + ": " + t.action + (t.revision ? ", revision " + t.revision : ""), " ", t.action !== "unchanged" ? h("a", { href: "#teams/" + enc(t.name), text: "open" }) : null)))));
      }
      if (report) {
        const ch = changing();
        blocks.push(section("Dry run result",
          h("div", { class: "row" }, report.errors ? h("span", { class: "pill st-failed", text: nOf(report.errors, "error") }) : h("span", { class: "pill st-ready", text: "no errors" }),
            report.warnings ? h("span", { class: "pill st-planned", text: nOf(report.warnings, "warning") }) : null,
            h("span", { class: "muted small", text: ch.length + " of " + report.teams.length + " teams would change" })),
          (report.findings || []).length ? [h("h2", { class: "sub", text: "File-level findings" }), findingsList(report.findings)] : null,
          table(["Team", "Action", "Errors", "Warnings", "Affects", "Findings", "Revision"], report.teams.map(entryRow), "No teams in the text."),
          ackPanel(codes(), warnF(), ticked, paintApply),
          field("import-reason", "Reason (kept in the audit trail)", reasonIn),
          confirm
            ? h("div", { class: "confirm", role: "group", "aria-label": "Confirm apply" },
              h("b", { text: "Apply this import?" }),
              h("p", { class: "small", text: ch.filter((t) => t.action === "create").length + " create, " + ch.filter((t) => t.action === "update").length + " update. All changed teams are written together or not at all." }),
              h("div", { class: "row" }, h("button", { type: "button", class: "btn primary", "data-apply": "1", disabled: !canApply(), text: "Confirm apply", onclick: () => call(true) }),
                h("button", { type: "button", class: "btn", text: "Cancel", onclick: () => { confirm = false; draw(); } })))
            : h("div", { class: "row" }, h("button", { type: "button", class: "btn primary", "data-apply": "1", disabled: !canApply(), text: "Apply to " + nOf(ch.length, "team"), onclick: () => { confirm = true; draw(); } }),
              h("span", { class: "muted small", "data-why": "1", text: whyApply() }))));
      }
      fill(out, blocks);
    }
    mount(
      h("div", { class: "crumbs" }, h("a", { href: "#teams", text: "Teams" }), " / ", h("span", { text: "Import" })),
      h("h1", { text: "Import from YAML" }),
      h("p", { class: "muted small", text: "Paste the text of a tenants.yaml. A dry run reports what each team would do without writing anything. Applying writes every changed team in one transaction, or nothing." }),
      section("Teams YAML", field("import-yaml", "tenants.yaml text", ta), h("div", { class: "row" }, dry)),
      out);
    draw();
  }

  /* ---------- teams router ---------- */
  async function viewTeams(id, g) {
    const agent = await loadAgent(30000).catch(() => null);
    if (g !== gen) return;
    if (!isOperator(agent)) { mount(h("h1", { text: "Teams" }), opsOnly(agent)); return; }
    const parts = id ? id.split("/") : [];
    try {
      if (!parts.length) await viewTeamList(g);
      else if (parts[0] === "new" && parts.length === 1) await viewTeamForm(g, null);
      else if (parts[0] === "import" && parts.length === 1) await viewTeamImport(g);
      else if (parts[1] === "edit") await viewTeamForm(g, parts[0]);
      else if (parts[1] === "history") await (parts[2] ? viewTeamRevision(g, parts[0], Number(parts[2])) : viewTeamHistory(g, parts[0]));
      else await viewTeamDetail(g, parts[0]);
    } catch (e) {
      if (g !== gen) return;
      if (e instanceof ApiError && e.status === 403) mount(h("h1", { text: "Teams" }), opsOnly(agent));
      else throw e;
    }
  }
  function syncTeamsNav(agent) {
    const nav = document.getElementById("nav");
    const existing = nav.querySelector('a[data-route="teams"]');
    if (isOperator(agent) && !existing) {
      const a = h("a", { href: "#teams", "data-route": "teams", text: "Teams" });
      if (route().name === "teams") a.setAttribute("aria-current", "page");
      nav.appendChild(a);
    } else if (!isOperator(agent) && existing) existing.remove();
  }

  /* ---------- deploy (self-service) ---------- */
  // The draft lives in memory only: never stored, never put in the URL. The request it builds is
  // exactly what agents send: POST /intents/validate while drafting, POST /operations to submit.
  const LABEL_KEY_RE = /^[a-z][a-z0-9_.-]{0,62}$/;
  const LABEL_SEED = ["name", "project", "app"];
  const DSTEPS = ["Where", "What", "Configure", "Review"];
  const freshDeploy = () => ({
    step: 1, maxStep: 1, bu: null, env: null, pattern: null, version: null, desc: null,
    vals: {}, refs: {}, size: "", labels: LABEL_SEED.map((k) => ({ k, v: "", t: "string" })), keyed: null, preset: null,
  });
  let dep = freshDeploy();
  function startDeploy(preset) {
    dep = freshDeploy();
    dep.preset = preset || null;
    if (location.hash === "#deploy") render(); else location.hash = "#deploy";
  }
  const domId = (name) => "dp-" + String(name).replace(/[^A-Za-z0-9_-]/g, "_");
  function schemaKind(s) {
    const t = Array.isArray(s.type) ? s.type.find((x) => x !== "null") : s.type;
    return t === "boolean" ? "bool" : t === "number" || t === "integer" ? "number" : t === "array" ? "list" : t === "object" ? "map" : "string";
  }
  const zoneUnits = (agent) => ((agent && agent.business_units) || []).filter((u) => !u.archived && (u.environments || []).length);
  const deployableNames = (u, env) => (u.deployable_patterns && Array.isArray(u.deployable_patterns[env]) ? u.deployable_patterns[env] : u.patterns || []);
  const loadDesc = (name, version, bu, env) => cached("desc:" + [name, version || "", bu || "", env || ""].join("|"), 60000,
    () => api(`/patterns/${enc(name)}`, { query: { version, business_unit: bu, environment: env } }));
  const BAD_CHARS = /[\u0000-\u001f\u007f-\u009f]/;

  function constraintText(s) {
    const bits = [];
    if (s.minimum !== undefined) bits.push("min " + s.minimum);
    if (s.exclusiveMinimum !== undefined) bits.push("more than " + s.exclusiveMinimum);
    if (s.maximum !== undefined) bits.push("max " + s.maximum);
    if (s.exclusiveMaximum !== undefined) bits.push("less than " + s.exclusiveMaximum);
    if (s.minLength !== undefined) bits.push("at least " + s.minLength + " characters");
    if (s.maxLength !== undefined) bits.push("at most " + s.maxLength + " characters");
    if (s.minItems !== undefined) bits.push("at least " + s.minItems + " items");
    if (s.maxItems !== undefined) bits.push("at most " + s.maxItems + " items");
    if (s.pattern) bits.push("pattern " + s.pattern);
    if (s.default !== undefined) bits.push("default " + text(s.default));
    return bits.join(" | ");
  }
  // A courtesy hint only: the API is the authority and still validates every request.
  function clientProblem(s, kind, raw) {
    if (kind === "string") {
      if (raw === "") return "";
      if (s.minLength !== undefined && raw.length < s.minLength) return "Needs at least " + s.minLength + " characters.";
      if (s.maxLength !== undefined && raw.length > s.maxLength) return "At most " + s.maxLength + " characters.";
      if (s.pattern) { try { if (!new RegExp(s.pattern).test(raw)) return "Does not match the pattern " + s.pattern; } catch (e) { /* not a JS pattern */ } }
    } else if (kind === "number") {
      if (String(raw).trim() === "") return "";
      const n = Number(raw);
      if (!isFinite(n)) return "Enter a number.";
      if (s.type === "integer" && !Number.isInteger(n)) return "Enter a whole number.";
      if (s.minimum !== undefined && n < s.minimum) return "Must be at least " + s.minimum + ".";
      if (s.maximum !== undefined && n > s.maximum) return "Must be at most " + s.maximum + ".";
      if (s.exclusiveMinimum !== undefined && n <= s.exclusiveMinimum) return "Must be more than " + s.exclusiveMinimum + ".";
      if (s.exclusiveMaximum !== undefined && n >= s.exclusiveMaximum) return "Must be less than " + s.exclusiveMaximum + ".";
    } else if (kind === "list") {
      if (s.minItems !== undefined && raw.length < s.minItems) return "Needs at least " + s.minItems + " items.";
      if (s.maxItems !== undefined && raw.length > s.maxItems) return "At most " + s.maxItems + " items.";
    }
    return "";
  }
  function labelProblems(rows) {
    const out = [];
    const seen = new Set();
    let count = 0;
    for (const r of rows) {
      const k = r.k.trim();
      if (!k && !r.v) continue;
      if (!k) { out.push("A label with a value needs a key."); continue; }
      if (!r.v) continue;
      count++;
      if (!LABEL_KEY_RE.test(k)) out.push("Label key \"" + k + "\" must match ^[a-z][a-z0-9_.-]{0,62}$.");
      if (seen.has(k)) out.push("Label key \"" + k + "\" is used twice.");
      seen.add(k);
      if (r.v.length > 128) out.push("The value of \"" + k + "\" is over 128 characters.");
      if (BAD_CHARS.test(r.v)) out.push("The value of \"" + k + "\" has control characters.");
    }
    if (count > 16) out.push("At most 16 labels (you have " + count + ").");
    return out;
  }
  function readVal(name, s) {
    const kind = schemaKind(s);
    const v = dep.vals[name];
    if (kind === "bool") return v && (v.touched || (s._required && v)) ? v.v : undefined;
    if (kind === "list") {
      if (!Array.isArray(v) || !v.length) return undefined;
      const items = s.items && s.items.type;
      return items === "number" || items === "integer" ? v.map((x) => (isFinite(Number(x)) ? Number(x) : x)) : v.slice();
    }
    if (kind === "map") {
      const o = {};
      for (const r of Array.isArray(v) ? v : []) if (r.k.trim()) o[r.k.trim()] = r.v;
      return Object.keys(o).length ? o : undefined;
    }
    const raw = typeof v === "string" ? v : "";
    if (kind === "number") {
      if (raw.trim() === "") return undefined;
      const n = Number(raw);
      return isFinite(n) ? n : raw;
    }
    return raw === "" ? undefined : raw;
  }
  function buildRequest() {
    const d = dep.desc;
    const schema = (d && d.input_schema) || {};
    const props = schema.properties || {};
    const required = new Set(schema.required || []);
    const inputs = {};
    const refs = {};
    for (const name of Object.keys(props).sort()) {
      const r = dep.refs[name];
      if (r && r.on) { if (r.id && r.out) refs[name] = { resource_id: r.id, output: r.out }; continue; }
      const v = readVal(name, Object.assign({ _required: required.has(name) }, props[name]));
      if (v !== undefined) inputs[name] = v;
    }
    const body = { pattern: dep.pattern };
    if (dep.version) body.version = dep.version;
    if (d && d.commit) body.expected_commit = d.commit;
    body.business_unit = dep.bu;
    body.environment = dep.env;
    if (dep.size) body.size = dep.size;
    body.inputs = inputs;
    const labels = {};
    for (const r of dep.labels) if (r.k.trim() && r.v) labels[r.k.trim()] = r.v;
    if (Object.keys(labels).length) body.labels = labels;
    if (Object.keys(refs).length) body.input_refs = refs;
    return body;
  }
  // Sorts what the API said into per-field messages and general ones.
  function problemsOf(e, props) {
    const out = { fields: {}, general: [], reason: null };
    if (!(e instanceof ApiError) || !e.body || !e.body.error) { out.general.push(describeError(e)); return out; }
    const err = e.body.error;
    out.reason = err.reason || null;
    if (e.status === 422 && Array.isArray(err.detail)) {
      for (const item of err.detail) {
        const msg = item && item.message ? String(item.message) : "invalid" + (item && item.code ? " (" + item.code + ")" : "");
        let loc = item ? item.field : null;
        let key = null;
        if (typeof loc === "string") key = Object.prototype.hasOwnProperty.call(props, loc) ? loc : null;
        else if (Array.isArray(loc)) {
          const l = loc.filter((x) => x !== "body");
          if (l[0] === "inputs" && typeof l[1] === "string" && Object.prototype.hasOwnProperty.call(props, l[1])) key = l[1];
          else if (l[0] === "labels") key = "@labels";
          else if (l[0] === "size") key = "@size";
          loc = l.join(".");
        }
        if (key) out.fields[key] = (out.fields[key] ? out.fields[key] + " " : "") + msg;
        else out.general.push((loc ? String(loc) + ": " : "") + msg);
      }
      if (!out.general.length && !Object.keys(out.fields).length) out.general.push("422 invalid request");
      return out;
    }
    out.general.push(describeError(e));
    return out;
  }

  async function viewDeploy(_id, g) {
    const agent = await loadAgent(5000);
    if (g !== gen) return;
    const zones = zoneUnits(agent);
    if (!zones.length) {
      mount(h("div", { class: "page-head" }, h("h1", { text: "Deploy" })),
        empty("No landing zone to deploy to", (agent.business_units || []).length ? "None of your business units has an environment you may deploy to." : "Tenancy is not enabled, so there are no landing zones. Set FORGEAPI_TENANTS_PATH to define them."));
      return;
    }
    const unitOf = (bu, env) => zones.find((u) => u.name === bu && (u.environments || []).includes(env)) || null;
    const curUnit = () => unitOf(dep.bu, dep.env);
    // Preselection from a catalog page or a landing-zone card.
    if (dep.preset) {
      const p = dep.preset;
      dep.preset = null;
      const u = unitOf(p.bu, p.env);
      if (u) {
        dep.bu = p.bu; dep.env = p.env; dep.step = 2; dep.maxStep = 2;
        if (p.pattern && deployableNames(u, p.env).includes(p.pattern)) { dep.pattern = p.pattern; dep.step = 3; dep.maxStep = 3; }
      }
    }
    if (dep.bu && !curUnit()) { dep.bu = null; dep.env = null; dep.pattern = null; dep.desc = null; dep.step = 1; dep.maxStep = 1; }
    if (!dep.bu && ui.zone && unitOf(ui.zone.bu, ui.zone.env)) { dep.bu = ui.zone.bu; dep.env = ui.zone.env; }
    if (!dep.bu) {
      const only = zones.length === 1 && zones[0].environments.length === 1 ? zones[0] : null;
      if (only) { dep.bu = only.name; dep.env = only.environments[0]; }
    }
    if (dep.step > 1 && !dep.bu) { dep.step = 1; }
    if (dep.step >= 3 && !dep.pattern) dep.step = 2;
    if (dep.step === 4 && !dep.desc) dep.step = 3;

    let vs = { state: "idle", sig: null, result: null, fields: {}, general: [], reason: null };
    let vtimer = null;
    let vseq = 0;
    let bseq = 0;
    let fields = {};
    let submitting = false;
    let submitErr = null;
    const dyn = {};
    const stepper = h("ol", { class: "stepper tsteps", "aria-label": "Deploy steps" });
    const body = h("div", { class: "tbody" });
    const checksHost = h("aside", { class: "card tchecks", "aria-label": "Live check" });
    const layout = h("div", { class: "tlayout" }, body, checksHost);
    mount(h("div", { class: "page-head" }, h("h1", { text: "Deploy" }), h("span", { class: "muted small", text: "pick a zone and a pattern, fill it in, review the plan" })), stepper, layout);

    const sigNow = () => (dep.desc ? JSON.stringify(buildRequest()) : null);
    const validNow = () => vs.state === "ok" && vs.sig === sigNow();
    // One step past the furthest reached is allowed when its prerequisites hold.
    const canGo = (n) => n <= dep.maxStep + 1 && (n === 1 || (n === 2 && Boolean(dep.bu)) || (n === 3 && Boolean(dep.bu && dep.pattern)) || (n === 4 && Boolean(dep.pattern && validNow())));
    const budgetOf = () => { const u = curUnit(); return u && u.budgets ? u.budgets[dep.env] : null; };

    /* validation */
    function schedule(delay) {
      clearTimeout(vtimer);
      vtimer = setTimeout(runValidate, delay);
      if (vs.state !== "busy") { vs.state = "busy"; drawChecks(); }
    }
    function applyFieldErrors() {
      for (const [name, f] of Object.entries(fields)) {
        const msg = vs.fields[name] || "";
        f.err.textContent = msg;
        if (f.ctl) { if (msg) f.ctl.setAttribute("aria-invalid", "true"); else f.ctl.removeAttribute("aria-invalid"); }
      }
    }
    async function runValidate() {
      if (g !== gen || !dep.desc) return;
      const mine = ++vseq;
      const req = buildRequest();
      const sig = JSON.stringify(req);
      const lp = labelProblems(dep.labels);
      if (lp.length) {
        vs = { state: "bad", sig, result: null, fields: { "@labels": lp.join(" ") }, general: [], reason: null };
        applyFieldErrors(); drawChecks();
        return;
      }
      vs.state = "busy"; drawChecks();
      try {
        const r = await api("/intents/validate", { method: "POST", body: req });
        if (g !== gen || mine !== vseq) return;
        vs = { state: "ok", sig, result: r, fields: {}, general: [], reason: null };
      } catch (e) {
        if (g !== gen || mine !== vseq) return;
        const p = problemsOf(e, (dep.desc.input_schema || {}).properties || {});
        vs = { state: "bad", sig, result: null, fields: p.fields, general: p.general, reason: p.reason };
      }
      applyFieldErrors();
      drawChecks();
    }
    function budgetImpact(cost) {
      const b = budgetOf();
      if (!b) return h("p", { class: "muted small", text: "No monthly budget limit is configured for this zone." });
      const c = num(cost);
      const avail = num(b.available);
      const out = [meter(b.reserved, b.monthly_budget, b.available, "Budget reserved in " + dep.bu + " " + dep.env)];
      if (c === null) out.push(h("div", { class: "warn-box", role: "status" }, h("b", { text: "No estimated cost" }),
        h("span", { text: "This zone has a budget, so the API refuses a pattern that declares no estimated cost here (403)." })));
      else {
        out.push(kv([["This deployment", money(c) + " per month"], ["Available now", avail === null ? null : money(avail)], ["Left after", avail === null ? null : money(avail - c)]]));
        if (avail !== null && c > avail) out.push(h("div", { class: "warn-box", role: "status" }, h("b", { text: "Over budget" }),
          h("span", { text: "The estimate " + money(c) + " is more than the " + money(avail) + " still available. Submitting will be refused (budget_exceeded)." })));
      }
      return out;
    }
    function drawChecks() {
      if (dyn.syncNav) dyn.syncNav();
      syncStepper();
      const r = vs.result;
      const out = [h("h2", { text: "Live check" })];
      const pills = [];
      if (vs.state === "busy") pills.push(h("span", { class: "pill st-planning", text: "checking..." }));
      else if (vs.state === "ok") pills.push(h("span", { class: "pill st-ready", text: "valid" }));
      else if (vs.state === "bad") pills.push(h("span", { class: "pill st-failed", text: "needs attention" }));
      else pills.push(h("span", { class: "pill st-queued", text: "not checked yet" }));
      out.push(h("div", { class: "row" }, pills));
      if (vs.state === "idle") out.push(h("p", { class: "muted small", text: "The API checks your inputs as you type. Nothing is reserved or planned yet." }));
      const probs = vs.general.concat(Object.entries(vs.fields).filter(([k]) => k === "@size" || k === "@labels").map(([k, v]) => (k === "@size" ? "Size: " : "Labels: ") + v));
      const loose = Object.entries(vs.fields).filter(([k]) => k[0] !== "@" && !fields[k]).map(([k, v]) => k + ": " + v);
      const all = probs.concat(loose);
      if (all.length && vs.state !== "busy") out.push(h("div", { class: "alert", role: "alert" }, h("b", { text: "Problems" }), h("ul", { class: "plain" }, all.map((x) => h("li", { text: x })))));
      const fieldCount = Object.keys(vs.fields).filter((k) => fields[k]).length;
      if (fieldCount && vs.state !== "busy") out.push(h("p", { class: "muted small", text: nOf(fieldCount, "field") + " need attention (marked in the form)." }));
      if (r) {
        out.push(h("h2", { class: "sub", text: "Resolved" }), kv([
          ["Pattern", text(r.pattern) + (r.version ? " @ " + r.version : "")],
          ["Commit", r.commit ? h("span", { class: "mono", title: r.commit, text: shortCommit(r.commit) }) : null],
          ["Estimated cost", num(r.estimated_monthly_cost) === null ? h("span", { class: "muted", text: "not declared" }) : money(r.estimated_monthly_cost) + " per month"],
        ]), h("h2", { class: "sub", text: "Budget impact" }), budgetImpact(r.estimated_monthly_cost));
      } else if (vs.state === "bad" && vs.reason === "budget_exceeded") out.push(h("h2", { class: "sub", text: "Budget impact" }), budgetImpact(null));
      out.push(h("p", { class: "muted small", text: "Schema and placement are checked here. The plan itself comes after you submit." }));
      fill(checksHost, out);
    }

    /* stepper and navigation */
    let stepBtns = [];
    function syncStepper() { stepBtns.forEach((b, i) => { b.disabled = !canGo(i + 1); }); }
    function drawStepper() {
      stepBtns = [];
      fill(stepper, DSTEPS.map((label, i) => {
        const n = i + 1;
        const b = h("button", { type: "button", class: "step-btn", "aria-current": n === dep.step ? "step" : null, onclick: () => goStep(n) },
          h("span", { class: "step-n", text: String(n) }), h("span", { class: "step-l", text: label }));
        stepBtns.push(b);
        return h("li", { class: "step" + (n === dep.step ? " current" : n < dep.step ? " done" : "") }, b);
      }));
      syncStepper();
    }
    function goStep(n) {
      if (!canGo(n) && n !== dep.step) return;
      dep.step = n;
      dep.maxStep = Math.max(dep.maxStep, n);
      drawStepper();
      layout.classList.toggle("solo", n < 3);
      checksHost.hidden = n < 3;
      drawBody();
    }
    const stepHead = (n, label) => h("h2", { class: "tstep-h", tabindex: "-1", text: n + ". " + label });
    function navRow(canNext, nextLabel, onNext) {
      const next = h("button", { type: "button", class: "btn primary", text: nextLabel, disabled: !canNext || null, onclick: onNext });
      return { next, row: h("div", { class: "row tnav" }, dep.step > 1 ? h("button", { type: "button", class: "btn", text: "Back", onclick: () => goStep(dep.step - 1) }) : null, nextLabel ? next : null) };
    }
    function drawBody() {
      const mine = ++bseq;
      dyn.syncNav = null;
      const n = dep.step;
      const hd = stepHead(n, ["Where do you want it?", "What do you want to deploy?", "Configure it", "Review and submit"][n - 1]);
      const rest = h("div", { class: "stack" });
      fill(body, hd, rest);
      hd.focus({ preventScroll: false });
      const run = [step1, step2, step3, step4][n - 1];
      Promise.resolve(run(rest, () => g !== gen || mine !== bseq)).catch((e) => { if (g === gen && mine === bseq) fill(rest, errorBox(e)); });
    }

    /* step 1: where */
    function step1(rest) {
      const cards = [];
      const radios = [];
      const nav = navRow(Boolean(dep.bu), "Next: " + DSTEPS[1], () => goStep(2));
      for (const u of zones) for (const env of u.environments) {
        const gr = (u.guardrails || {})[env];
        const b = (u.budgets || {})[env];
        const clouds = u.clouds && u.clouds[env];
        const count = deployableNames(u, env).length;
        const id = domId("zone-" + u.name + "-" + env);
        const radio = h("input", { type: "radio", name: "dp-zone", id, value: u.name + "|" + env, checked: dep.bu === u.name && dep.env === env });
        const card = h("label", { class: "card pick" + (dep.bu === u.name && dep.env === env ? " sel" : ""), for: id },
          h("div", { class: "zone-head" }, radio, h("b", { text: u.name }), h("span", { class: "zone-env", text: env })),
          kv([
            ["Clouds", Array.isArray(clouds) && clouds.length ? h("div", { class: "chips" }, clouds.map((c) => cloudBadge(c))) : null],
            ["Patterns", count + " deployable"],
            ["Default region", u.default_region],
            ["Destroy", gr ? h("span", { class: "chip " + (gr.allow_destroy ? "on" : "off"), text: gr.allow_destroy ? "allowed" : "blocked" }) : null],
            ["Protected types", gr && gr.protected_resource_types && gr.protected_resource_types.length ? chips(gr.protected_resource_types) : null],
          ]),
          b ? meter(b.reserved, b.monthly_budget, b.available, "Budget reserved in " + u.name + " " + env) : h("span", { class: "muted small", text: "No budget limit" }));
        radio.addEventListener("change", () => {
          if (dep.bu !== u.name || dep.env !== env) {
            dep.bu = u.name; dep.env = env; dep.desc = null; dep.refs = {}; dep.size = ""; dep.keyed = null;
            if (dep.pattern && !deployableNames(u, env).includes(dep.pattern)) { dep.pattern = null; dep.version = null; }
            dep.maxStep = Math.min(dep.maxStep, 2);
            vs = { state: "idle", sig: null, result: null, fields: {}, general: [], reason: null };
          }
          radios.forEach((c) => c.card.classList.toggle("sel", c.radio.checked));
          nav.next.disabled = false;
          drawStepper();
        });
        radios.push({ radio, card });
        cards.push(card);
      }
      fill(rest, h("div", { class: "grid", role: "radiogroup", "aria-label": "Landing zone" }, cards),
        h("p", { class: "muted small", text: "Only zones you may deploy to are shown. The business unit decides the cloud account, region and budget; you never enter those." }), nav.row);
    }

    /* step 2: what */
    async function step2(rest, stale) {
      const u = curUnit();
      const names = deployableNames(u, dep.env).slice().sort();
      if (!names.length) {
        fill(rest, empty("No patterns deployable in " + dep.bu + " / " + dep.env, "The environment's clouds do not match any pattern you may use."), navRow(false, "", null).row);
        return;
      }
      fill(rest, skeleton(3));
      const clouds = {};
      try { const l = await api("/patterns", { query: { environment: dep.env } }); for (const p of l.items || []) clouds[p.name] = p.cloud; } catch (e) { /* cloud badges are optional */ }
      if (stale()) return;
      const withChecks = capOn(agent, "pattern_checks");
      const slots = {};
      const cards = [];
      const nav = navRow(Boolean(dep.pattern), "Next: " + DSTEPS[2], () => goStep(3));
      const versionHost = h("div", { class: "card" });
      let vseq2 = 0;
      async function drawVersion() {
        const mine = ++vseq2;
        if (!dep.pattern) { fill(versionHost, h("p", { class: "muted small", text: "Choose a pattern to pick its version." })); return; }
        fill(versionHost, h("span", { class: "muted small", text: "Loading versions..." }));
        let d;
        try { d = await loadDesc(dep.pattern, dep.version, dep.bu, dep.env); } catch (e) { if (!stale() && mine === vseq2) fill(versionHost, errorBox(e, "Could not load the pattern")); return; }
        if (stale() || mine !== vseq2) return;
        if (!dep.version) dep.version = d.version;
        const versions = Array.isArray(d.versions) && d.versions.length ? d.versions : [d.version].filter(Boolean);
        const sel = h("select", { id: "dp-version" }, versions.map((v) => h("option", { value: v, text: v })));
        if (versions.includes(dep.version)) sel.value = dep.version;
        sel.addEventListener("change", () => { dep.version = sel.value; dep.desc = null; dep.keyed = null; dep.maxStep = Math.min(dep.maxStep, 3); drawVersion(); drawStepper(); });
        fill(versionHost, h("div", { class: "field" }, h("label", { for: "dp-version", text: "Version of " + dep.pattern }), sel,
          h("div", { class: "muted small", text: "Default: " + text(d.version) + ". The commit is pinned when you submit." + (d.commit ? " Resolved commit " + shortCommit(d.commit) + "." : "") })));
      }
      for (const name of names) {
        const id = domId("pat-" + name);
        const radio = h("input", { type: "radio", name: "dp-pattern", id, value: name, checked: dep.pattern === name });
        const slot = h("div", { class: "muted small" });
        slots[name] = slot;
        const card = h("label", { class: "card pick pat" + (dep.pattern === name ? " sel" : ""), for: id },
          h("div", { class: "row" }, radio, h("b", { text: name }), cloudBadge(clouds[name])), slot,
          withChecks ? h("div", { class: "row", "data-contract": name }) : null);
        radio.addEventListener("change", () => {
          if (dep.pattern !== name) {
            dep.pattern = name; dep.version = null; dep.desc = null; dep.vals = {}; dep.refs = {}; dep.size = ""; dep.keyed = null;
            dep.maxStep = Math.min(dep.maxStep, 2);
            vs = { state: "idle", sig: null, result: null, fields: {}, general: [], reason: null };
          }
          cards.forEach((c) => c.card.classList.toggle("sel", c.radio.checked));
          nav.next.disabled = false;
          drawStepper();
          drawVersion();
        });
        cards.push({ radio, card });
      }
      fill(rest, h("div", { class: "grid", role: "radiogroup", "aria-label": "Pattern" }, cards.map((c) => c.card)), versionHost, nav.row);
      drawVersion();
      if (withChecks) fillContractBadges(names.map((name) => ({ name })), g);
      // descriptions arrive after the list is on screen; a failure just leaves a card bare
      for (let i = 0; i < names.length; i += 4) {
        const got = await Promise.allSettled(names.slice(i, i + 4).map((n) => loadDesc(n, undefined, dep.bu, dep.env)));
        if (stale()) return;
        got.forEach((r, k) => {
          if (r.status !== "fulfilled") return;
          const about = r.value.about || {};
          const slot = slots[names[i + k]];
          if (typeof about.description === "string") slot.appendChild(h("div", { text: about.description }));
          if (typeof about.category === "string") slot.appendChild(h("span", { class: "chip", text: about.category }));
        });
      }
    }

    /* step 3: configure */
    function inputField(name, s, required, refCandidates) {
      const id = domId("in-" + name);
      const kind = schemaKind(s);
      const hint = h("div", { class: "fhint small", id: id + "-hint", "aria-live": "polite" });
      const err = h("div", { class: "ferr small", id: id + "-err", "aria-live": "polite" });
      const f = { err, ctl: null };
      fields[name] = f;
      const changed = () => {
        const v = dep.vals[name];
        hint.textContent = kind === "string" || kind === "number" || kind === "list" ? clientProblem(s, kind, v === undefined ? (kind === "list" ? [] : "") : v) : "";
        schedule(400);
      };
      let control;
      if (kind === "bool") {
        const o = dep.vals[name] || (dep.vals[name] = { v: s.default === true, touched: false });
        control = h("input", { id, type: "checkbox", checked: o.v });
        control.addEventListener("change", () => { o.v = control.checked; o.touched = true; changed(); });
      } else if (kind === "list") {
        const values = Array.isArray(dep.vals[name]) ? dep.vals[name] : (dep.vals[name] = []);
        control = chipEditor({ id, label: name, values, placeholder: "type a value, press Enter", empty: "no items", onChange: changed });
      } else if (kind === "map") {
        const rows = Array.isArray(dep.vals[name]) ? dep.vals[name] : (dep.vals[name] = []);
        control = kvEditor({ rows, idp: id, label: name, path: name, keyPh: "key", valPh: "value", empty: "no entries", onChange: changed });
      } else {
        if (typeof dep.vals[name] !== "string") dep.vals[name] = "";
        const ph = s.default !== undefined ? "default " + text(s.default) : null;
        if (Array.isArray(s.enum)) {
          control = h("select", { id }, h("option", { value: "", text: required ? "(choose)" : s.default !== undefined ? "(default: " + text(s.default) + ")" : "(not set)" }),
            s.enum.map((v) => h("option", { value: String(v), text: String(v) })));
          control.value = dep.vals[name];
          control.addEventListener("change", () => { dep.vals[name] = control.value; changed(); });
        } else if (kind === "number") {
          control = h("input", { id, type: "number", step: s.type === "integer" ? "1" : "any", min: s.minimum, max: s.maximum, value: dep.vals[name], placeholder: ph, autocomplete: "off" });
          control.addEventListener("input", () => { dep.vals[name] = control.value; changed(); });
        } else {
          control = h("input", { id, type: "text", value: dep.vals[name], placeholder: ph, maxlength: s.maxLength, autocomplete: "off", spellcheck: "false" });
          control.addEventListener("input", () => { dep.vals[name] = control.value; changed(); });
        }
      }
      f.ctl = control.matches && control.matches("input,select") ? control : control.querySelector("input");
      if (f.ctl) { f.ctl.setAttribute("aria-describedby", id + "-help " + id + "-hint " + id + "-err"); if (required) f.ctl.setAttribute("aria-required", "true"); }
      const direct = h("div", { class: "dp-ctl" }, control);
      let refBox = null;
      let refToggle = null;
      if (refCandidates.length) {
        const r = dep.refs[name] || (dep.refs[name] = { on: false, id: "", out: "" });
        const resSel = h("select", { id: id + "-res", "aria-label": "Resource to read " + name + " from" }, h("option", { value: "", text: "(choose a resource)" }),
          refCandidates.map((x) => h("option", { value: x.id, text: resName(x) + " (" + x.pattern + ")" })));
        const outSel = h("select", { id: id + "-out", "aria-label": "Output to use for " + name });
        const fillOut = () => {
          const src = refCandidates.find((x) => x.id === r.id);
          const keys = src ? Object.keys(src.outputs).sort() : [];
          if (!keys.includes(r.out)) r.out = keys[0] || "";
          fill(outSel, h("option", { value: "", text: keys.length ? "(choose an output)" : "(choose a resource first)" }), keys.map((k) => h("option", { value: k, text: k })));
          outSel.value = r.out;
        };
        resSel.value = r.id;
        fillOut();
        resSel.addEventListener("change", () => { r.id = resSel.value; r.out = ""; fillOut(); changed(); });
        outSel.addEventListener("change", () => { r.out = outSel.value; changed(); });
        refBox = h("div", { class: "row dp-ref" }, resSel, outSel);
        refBox.hidden = !r.on;
        direct.hidden = r.on;
        refToggle = h("label", { class: "inline small" }, h("input", { type: "checkbox", id: id + "-ref", checked: r.on }), "Take it from another resource's output");
        refToggle.querySelector("input").addEventListener("change", (ev) => {
          r.on = ev.target.checked; refBox.hidden = !r.on; direct.hidden = r.on; changed();
        });
      }
      const help = [s.description ? text(s.description) : "", constraintText(s), kind === "bool" && !required ? "Only sent once you change it." : "", kind === "list" ? "Items are split on spaces and commas." : ""].filter(Boolean).join(" | ");
      return h("div", { class: "field dp-field", "data-name": name },
        kind === "map" ? h("span", { class: "lbl", id: id + "-lbl" }, name, required ? h("span", { class: "req", text: " required" }) : null)
          : h("label", { for: id }, name, required ? h("span", { class: "req", text: " required" }) : null),
        direct, refBox, refToggle,
        h("div", { class: "muted small", id: id + "-help", text: help || (kind === "string" ? "Text." : kind === "number" ? "Number." : kind === "bool" ? "Yes or no." : kind === "list" ? "List." : "Key and value pairs.") }),
        hint, err);
    }
    async function step3(rest, stale) {
      fill(rest, skeleton(4));
      const [descR, resR] = await Promise.allSettled([loadDesc(dep.pattern, dep.version, dep.bu, dep.env), loadResources(3000)]);
      if (stale()) return;
      if (descR.status === "rejected") {
        fill(rest, errorBox(descR.reason, "Could not load the pattern"), navRow(false, "", null).row);
        return;
      }
      dep.desc = descR.value;
      if (!dep.version) dep.version = dep.desc.version;
      const candidates = resR.status === "fulfilled" ? resR.value.items.filter((r) => r.state === "ready" && r.business_unit === dep.bu && r.environment === dep.env
        && isObj(r.outputs) && Object.keys(r.outputs).length).sort((a, b) => resName(a).localeCompare(resName(b))) : [];
      const schema = dep.desc.input_schema || {};
      const props = schema.properties || {};
      const required = new Set(schema.required || []);
      const names = Object.keys(props).sort((a, b) => (required.has(b) - required.has(a)) || a.localeCompare(b));
      fields = {};
      const form = h("div", { class: "dp-form" }, names.map((n) => inputField(n, props[n] || {}, required.has(n), candidates)));
      const sizes = (Array.isArray(dep.desc.sizes) ? dep.desc.sizes : []).map((x) => (isObj(x) ? x.name : x)).filter((x) => typeof x === "string");
      let sizeBlock = null;
      if (sizes.length) {
        const serr = h("div", { class: "ferr small", "aria-live": "polite" });
        const sel = h("select", { id: "dp-size", "aria-describedby": "dp-size-help dp-size-err" }, h("option", { value: "", text: "(none)" }), sizes.map((x) => h("option", { value: x, text: x })));
        serr.id = "dp-size-err";
        sel.value = dep.size;
        sel.addEventListener("change", () => { dep.size = sel.value; schedule(0); });
        fields["@size"] = { err: serr, ctl: sel };
        sizeBlock = section("Size", h("div", { class: "field" }, h("label", { for: "dp-size", text: "Named size" }), sel,
          h("div", { class: "muted small", id: "dp-size-help", text: "Sizes are defined by the pattern for this environment; the estimated cost depends on the one you pick." }), serr));
      }
      const lerr = h("div", { class: "ferr small", id: "dp-labels-err", "aria-live": "polite" });
      fields["@labels"] = { err: lerr, ctl: null };
      const lhint = h("div", { class: "fhint small", "aria-live": "polite" });
      const labelsChanged = () => { const p = labelProblems(dep.labels); lhint.textContent = p.join(" "); schedule(400); };
      const labelsBlock = section("Labels",
        h("p", { class: "muted small", id: "dp-labels-help", text: "Free-form tags to find this later (up to 16). Keys: lowercase letters, digits, _ . -; values up to 128 characters. Never put secrets here. Rows with an empty value are not sent. 'name' is shown in lists." }),
        h("datalist", { id: "dp-label-keys" }, LABEL_SEED.map((k) => h("option", { value: k }))),
        kvEditor({ rows: dep.labels, idp: "dp-lab", label: "Label", path: "labels", list: "dp-label-keys", keyPh: "key", valPh: "value", empty: "no labels", onChange: labelsChanged }), lhint, lerr);
      const note = candidates.length ? null : h("p", { class: "muted small", text: "No ready resource with outputs exists in " + dep.bu + " / " + dep.env + ", so inputs cannot be taken from another resource yet." });
      const nav = navRow(false, "Next: " + DSTEPS[3], () => goStep(4));
      dyn.syncNav = () => { nav.next.disabled = !validNow(); dyn.hint.textContent = validNow() ? "" : vs.state === "busy" ? "Checking..." : "Fix the marked fields to continue."; };
      dyn.hint = h("span", { class: "muted small", role: "status" });
      nav.row.appendChild(dyn.hint);
      fill(rest,
        section("Inputs", names.length ? form : h("p", { class: "muted", text: "This pattern takes no inputs." }), note,
          h("p", { class: "muted small", text: "Placement (account, region, network) is injected by your business unit and cannot be set here." })),
        sizeBlock, labelsBlock, nav.row);
      applyFieldErrors();
      schedule(0);
    }

    /* step 4: review and submit */
    function step4(rest) {
      const req = buildRequest();
      const json = JSON.stringify(req, null, 2);
      const sig = JSON.stringify(req);
      if (!dep.keyed || dep.keyed.sig !== sig) dep.keyed = { sig, key: newKey() };
      const key = dep.keyed.key;
      const r = vs.result || {};
      const inputsList = Object.entries(req.inputs).map(([k, v]) => h("div", null, h("span", { class: "mono", text: k }), " = ", h("span", { class: "mono", text: text(v) })));
      const refList = Object.entries(req.input_refs || {}).map(([k, v]) => {
        const src = loadedRes && loadedRes[v.resource_id];
        return h("div", null, h("span", { class: "mono", text: k }), " from ", h("span", { text: src ? resName(src) : v.resource_id }), " . ", h("span", { class: "mono", text: v.output }));
      });
      const cost = num(r.estimated_monthly_cost);
      const submitHost = h("div", { class: "stack" });
      const btn = h("button", { type: "button", class: "btn primary", id: "dp-submit", text: "Submit and plan", disabled: !validNow() || null, onclick: submit });
      dyn.syncNav = () => { btn.disabled = submitting || !validNow(); };
      async function submit() {
        if (submitting || !validNow()) return;
        const sent = buildRequest();
        const keyNow = (() => { const s = JSON.stringify(sent); if (!dep.keyed || dep.keyed.sig !== s) dep.keyed = { sig: s, key: newKey() }; return dep.keyed.key; })();
        submitting = true; submitErr = null; btn.disabled = true; btn.textContent = "Submitting...";
        fill(submitHost);
        try {
          const op = await api("/operations", { key: keyNow, method: "POST", body: sent });
          cache = {};
          dep = freshDeploy();
          location.hash = "#operations/" + enc(op.id);
        } catch (e) {
          submitting = false;
          if (g !== gen) return;
          btn.textContent = "Submit and plan";
          btn.disabled = !validNow();
          const p = problemsOf(e, {});
          if (p.reason === "revision_moved") { cache = {}; dep.desc = null; }
          fill(submitHost, h("div", { class: "alert", role: "alert" }, h("h2", { text: "Submission refused" }),
            p.general.map((x) => h("div", { text: x })),
            e instanceof ApiError && e.status === 503 ? h("div", { class: "muted small", text: "Nothing is lost: the same Idempotency-Key is reused if you submit again." }) : null,
            p.reason === "revision_moved" ? h("div", null, h("div", { class: "muted small", text: "The pattern's tag moved since you looked. Reload it and review again." }),
              h("button", { type: "button", class: "btn", text: "Reload the pattern", onclick: () => goStep(3) })) : null));
        }
      }
      fill(rest,
        section("Summary", kv([
          ["Zone", dep.bu + " · " + dep.env],
          ["Pattern", dep.pattern + (dep.version ? " @ " + dep.version : "")],
          ["Cloud", dep.desc && dep.desc.cloud ? cloudBadge(dep.desc.cloud) : null],
          ["Commit", r.commit ? h("span", { class: "mono", title: r.commit, text: shortCommit(r.commit) }) : null],
          ["Size", req.size],
          ["Estimated cost", cost === null ? null : money(cost) + " per month"],
          ["Inputs", inputsList.length ? h("div", { class: "stack" }, inputsList) : h("span", { class: "muted", text: "none" })],
          ["From other resources", refList.length ? h("div", { class: "stack" }, refList) : null],
          ["Labels", req.labels ? h("div", { class: "chips" }, Object.entries(req.labels).map(([k, v]) => h("span", { class: "chip", text: k + "=" + v }))) : null],
        ]), cost !== null || budgetOf() ? budgetImpact(cost) : null),
        section("Request", h("p", { class: "muted small", text: "Exactly what is sent: POST /operations. Agents and the CLI can send the same body; the Idempotency-Key makes a retry safe." }),
          h("div", { class: "idrow" }, h("span", { class: "mono", text: "Idempotency-Key: " + key }), copyBtn(key, "Idempotency-Key")),
          h("pre", { class: "snippet dp-json", id: "dp-request", tabindex: "0", "aria-label": "Request JSON", text: json }), h("div", { class: "row" }, copyBtn(json, "request JSON"))),
        h("div", { class: "row tnav" }, h("button", { type: "button", class: "btn", text: "Back", onclick: () => goStep(3) }), btn,
          h("span", { class: "muted small", text: "Nothing is applied. A plan is created and shown for your review." })),
        submitHost);
      drawChecks();
      if (!validNow() && dep.desc) schedule(0);
    }

    let loadedRes = null;
    loadResources(3000).then((x) => { loadedRes = byIdMap(x.items); }, () => {});
    drawStepper();
    layout.classList.toggle("solo", dep.step < 3);
    checksHost.hidden = dep.step < 3;
    drawChecks();
    drawBody();
  }

  /* ---------- chrome ---------- */
  const auto = document.getElementById("auto");
  setInterval(() => { if (auto.checked && !document.hidden && refresher) refresher(); }, 5000);
  document.getElementById("env-filter").addEventListener("change", (ev) => { ui.env = ev.target.value; ui.zone = null; render(); });
  document.getElementById("zone-chip-clear").addEventListener("click", () => { ui.zone = null; ui.env = ""; render(); });
  const tokenIn = document.getElementById("token");
  function tokenState() {
    document.getElementById("auth").classList.toggle("has-token", Boolean(token));
    document.getElementById("token-state").textContent = token ? "Token held in memory. It is lost when you reload this page." : "No token set. Not needed when the API runs without authentication.";
  }
  document.getElementById("token-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    token = tokenIn.value.trim() || null;
    tokenIn.value = "";
    cache = {};
    clearBanner();
    tokenState();
    document.getElementById("auth").open = false;
    render();
  });
  document.getElementById("token-clear").addEventListener("click", () => { token = null; tokenIn.value = ""; cache = {}; tokenState(); });
  render();
})();
