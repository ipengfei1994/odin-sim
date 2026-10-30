/* ODIN-Sim workbench front-end.

   Two rules are enforced here because they encode scientific honesty, not taste:

   1. Nothing is hard-coded that the platform does not have. Model lists come from
      /api/models, column names from the sweep's own results.csv header.
   2. Every plotted or displayed number carries its own sample size. When n >= 2 a
      confidence band is drawn; when n == 1 the value is tagged as not usable for
      inference; when n == 0 the reason is shown instead of an empty chart.
*/

const state = {
  info: null,
  models: null,
  sweeps: [],
  view: "overview",
  lastPlots: [],
  results: { sweep: null, detail: null, agg: null, curves: null, runs: null, hidden: new Set() },
};

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};

/* ------------------------------------------------------------------ api */

async function api(path, params) {
  const url = new URL(path, window.location.origin);
  if (params) Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
  const res = await fetch(url);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

function toast(msg) {
  const node = $("#toast");
  node.textContent = msg;
  node.classList.add("is-on");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => node.classList.remove("is-on"), 5000);
}

/* -------------------------------------------------------------- format */

function fmt(value, digits = 3) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const num = Number(value);
  if (!Number.isFinite(num)) return "—";
  if (Number.isInteger(num) && Math.abs(num) < 1e15) return String(num);
  return num.toFixed(digits);
}

/* n-driven presentation: the sample size decides what we are allowed to show. */
function describe(row, metric, confidence) {
  const n = row[`${metric}.n`];
  if (n === undefined || n === null || n === 0) {
    return { value: "—", sub: "not computed", n: 0, warn: true };
  }
  const mean = row[`${metric}.mean`];
  const pct = Math.round(confidence * 100);
  if (n === 1) {
    return { value: fmt(mean), sub: `n = 1 · no ${pct}% CI · not usable for inference`, n: 1, warn: true };
  }
  const lo = fmt(row[`${metric}.ci_low`]);
  const hi = fmt(row[`${metric}.ci_high`]);
  return { value: fmt(mean), sub: `n = ${n} · ${pct}% CI ${lo}–${hi}`, n, warn: false };
}

function nTag(n) {
  if (n === undefined || n === null) return el("span", "tag-n", "n?");
  if (n === 0) return el("span", "tag-n is-warn", "n=0 not computed");
  if (n === 1) return el("span", "tag-n is-warn", "n=1 not for inference");
  return el("span", "tag-n", `n=${n}`);
}

/* --------------------------------------------------------------- theme */

const THEME_ORDER = ["system", "dark", "light"];

function applyTheme() {
  const pref = localStorage.getItem("odin-theme") || "system";
  const resolved = pref === "system"
    ? (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark")
    : pref;
  document.documentElement.dataset.theme = resolved;
  document.documentElement.dataset.pref = pref;
  const btn = $("#theme-toggle");
  if (btn) {
    btn.textContent = pref === "system" ? "◐ Theme · auto" : (resolved === "dark" ? "◑ Dark" : "◐ Light");
  }
}

function initTheme() {
  const onThemeChange = () => {
    applyTheme();
    /* re-render the active view so chart colours follow the new palette */
    setView(state.view);
  };
  $("#theme-toggle").addEventListener("click", () => {
    const cur = localStorage.getItem("odin-theme") || "system";
    const next = THEME_ORDER[(THEME_ORDER.indexOf(cur) + 1) % THEME_ORDER.length];
    localStorage.setItem("odin-theme", next);
    onThemeChange();
  });
  const mq = window.matchMedia("(prefers-color-scheme: light)");
  mq.addEventListener("change", onThemeChange);
}

/* -------------------------------------------------------------- charts */

function themeColors() {
  const s = getComputedStyle(document.documentElement);
  const v = (name) => s.getPropertyValue(name).trim();
  return { line: v("--chart-grid"), bg: v("--plot-bg"), text: v("--text"), muted: v("--muted"), accent: v("--accent") };
}

/* Series palette is theme-aware: the bright set reads on the dark canvas, the
   saturated-dark set reads on the light surface. Same hues, tuned for the ground. */
const SERIES_COLORS_DARK = ["#A7F36B", "#5FD68A", "#7FB3E8", "#E0A33E", "#C89BE8", "#E2685F", "#6FD4C8", "#B8B84A"];
const SERIES_COLORS_LIGHT = ["#2E7D32", "#1565C0", "#E65100", "#6A1B9A", "#00838F", "#C62828", "#AD1457", "#558B2F"];

function seriesColors() {
  return document.documentElement.dataset.theme === "light" ? SERIES_COLORS_LIGHT : SERIES_COLORS_DARK;
}

function plotLayout(title, yTitle, xTitle) {
  const c = themeColors();
  return {
    title: { text: title, font: { size: 13, color: c.text }, x: 0, xanchor: "left" },
    paper_bgcolor: c.bg,
    plot_bgcolor: c.bg,
    font: { family: getComputedStyle(document.body).fontFamily, size: 11, color: c.muted },
    margin: { l: 52, r: 16, t: 34, b: 42 },
    xaxis: { title: { text: xTitle, font: { size: 11 } }, gridcolor: c.line, zeroline: false, linecolor: c.line },
    yaxis: { title: { text: yTitle, font: { size: 11 } }, gridcolor: c.line, zeroline: false, linecolor: c.line },
    legend: { orientation: "h", y: -0.28, x: 0, font: { size: 10 } },
    hovermode: "x unified",
  };
}

function keep(nodeId, traces, layout, title, yTitle, xTitle) {
  state.lastPlots.push({ nodeId, traces, layout, title, yTitle, xTitle });
  Plotly.react(nodeId, traces, layout, { displayModeBar: false, responsive: true });
}

function renderPlots() {
  const plots = state.lastPlots;
  state.lastPlots = [];
  plots.forEach((p) => keep(p.nodeId, p.traces, p.layout, p.title, p.yTitle, p.xTitle));
}

/* Build traces for a mean with per-point confidence interval.
   A single observation gets a plain line and nothing else — drawing a band from
   one sample would misrepresent its uncertainty. */
function ciTraces(x, mean, lo, hi, label, color, n) {
  if (n >= 2 && lo && hi) {
    return [
      { x, y: hi, line: { width: 0 }, hoverinfo: "skip", showlegend: false, type: "scatter", mode: "lines" },
      { x, y: lo, fill: "tonexty", fillcolor: hexA(color, 0.20), line: { width: 0 }, hoverinfo: "skip", showlegend: false, type: "scatter", mode: "lines" },
      { x, y: mean, name: label, line: { color, width: 1.8 }, type: "scatter", mode: "lines", hovertemplate: `%{y:.4f}<extra>${label}</extra>` },
    ];
  }
  return [{ x, y: mean, name: n === 1 ? `${label} (n=1)` : label, line: { color, width: 1.8 }, type: "scatter", mode: "lines", hovertemplate: `%{y:.4f}<extra>${label}</extra>` }];
}

function hexA(hex, alpha) {
  const h = hex.replace("#", "");
  const big = parseInt(h.length === 3 ? h.split("").map((c) => c + c).join("") : h, 16);
  return `rgba(${(big >> 16) & 255}, ${(big >> 8) & 255}, ${big & 255}, ${alpha})`;
}

/* --------------------------------------------------------------- cards */

function kpi(label, value, sub, warn) {
  const card = el("div", "card");
  card.appendChild(el("p", "label", label));
  card.appendChild(el("p", "kpi-value", value));
  const subNode = el("p", "kpi-sub", sub);
  if (warn) subNode.style.color = "var(--status-warn)";
  card.appendChild(subNode);
  return card;
}

function panel(title, sub, bodyNode) {
  const card = el("div", "card");
  const head = el("div", "card-head");
  const left = el("div");
  left.appendChild(el("p", "card-title", title));
  if (sub) left.appendChild(el("p", "card-sub", sub));
  head.appendChild(left);
  card.appendChild(head);
  if (bodyNode) card.appendChild(bodyNode);
  return card;
}

function dataTable(columns, rows, caption) {
  const wrap = el("div", "table-wrap");
  const table = el("table");
  const thead = el("thead");
  const htr = el("tr");
  columns.forEach((c) => {
    const th = el("th", c.right ? "right" : "", c.label);
    htr.appendChild(th);
  });
  thead.appendChild(htr);
  table.appendChild(thead);
  const tbody = el("tbody");
  if (!rows.length) {
    const tr = el("tr");
    const td = el("td", "muted", "no rows");
    td.colSpan = columns.length;
    tr.appendChild(td);
    tbody.appendChild(tr);
  }
  rows.forEach((row) => {
    const tr = el("tr");
    columns.forEach((c) => {
      const td = el("td", c.cls || "");
      if (typeof c.render === "function") {
        const out = c.render(row);
        if (out instanceof Node) td.appendChild(out);
        else td.textContent = out === null || out === undefined ? "—" : String(out);
      } else {
        td.textContent = String(row[c.key] ?? "—");
      }
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
  if (caption) wrap.appendChild(el("p", "card-sub", caption));
  return wrap;
}

/* --------------------------------------------------------------- views */

const VIEWS = {
  overview: { title: "Research overview", sub: "A reproducible workspace for opinion dynamics experiments." },
  experiments: { title: "Experiments", sub: "Configure and launch reproducible sweeps." },
  network: { title: "Network explorer", sub: "Inspect generated topologies." },
  dynamics: { title: "Dynamics", sub: "Registered models and their parameters." },
  results: { title: "Experiment results", sub: "Compare runs, inspect aggregate metrics, and trace every result to its configuration." },
  reproducibility: { title: "Reproducibility", sub: "Environment, fingerprints and run metadata." },
};

function setView(view) {
  state.view = view;
  state.lastPlots = [];
  document.querySelectorAll(".nav-item").forEach((b) => b.classList.toggle("is-active", b.dataset.view === view));
  $("#page-title").textContent = VIEWS[view].title;
  $("#page-sub").textContent = VIEWS[view].sub;
  const content = $("#content");
  content.innerHTML = "";
  const renderer = {
    overview: renderOverview,
    experiments: renderExperiments,
    network: renderNetwork,
    dynamics: renderDynamics,
    results: renderResults,
    reproducibility: renderReproducibility,
  }[view];
  return Promise.resolve()
    .then(() => (renderer ? renderer(content) : renderPlanned(content, VIEWS[view].title)))
    .catch((err) => content.appendChild(el("p", "note", `render failed: ${err.message}`)));
}

function renderPlanned(root, title) {
  const note = panel(title, "not part of slice 1", null);
  const body = el("p", "card-sub");
  body.textContent =
    "This view is planned. The read-only results path (slice 1) is complete; experiment "
    + "submission and topology browsing need write endpoints, which come next. The page "
    + "is intentionally absent rather than filled with invented data.";
  note.appendChild(body);
  root.appendChild(note);
}

async function renderOverview(root) {
  const kpis = el("div", "kpis");
  root.appendChild(kpis);

  const info = state.info || (state.info = await api("/api/info").catch(() => null));
  const totalRuns = state.sweeps.reduce((acc, s) => acc + (s.counts.executed || 0), 0);
  const models = state.models?.models?.map((m) => m.name) || [];
  const commit = info?.git_commit ? String(info.git_commit).slice(0, 8) : null;

  kpis.appendChild(kpi("SWEEPS ON DISK", String(state.sweeps.length), `${totalRuns} recorded runs`));
  kpis.appendChild(kpi("REGISTERED MODELS", String(models.length), models.join(", ") || "none registered"));
  kpis.appendChild(kpi("PYTHON", info?.python_version ?? "—", info?.platform ?? "—"));
  kpis.appendChild(
    kpi(
      "REPRODUCIBILITY",
      commit ? "Tracked" : "—",
      commit ? `${commit}${info.git_dirty ? " · dirty" : ""} · seed + config recorded` : "no git metadata",
      Boolean(info?.git_dirty)
    )
  );

  const recent = panel("Recent sweeps", `${state.sweeps.length} sweep directories under the results root`, null);
  recent.appendChild(
    dataTable(
      [
        { label: "SWEEP", key: "id" },
        { label: "MODEL", key: "model" },
        { label: "RUNS", key: "counts", right: true, render: (r) => r.counts.executed ?? 0 },
        { label: "FAILED", key: "counts", right: true, render: (r) => r.counts.failed ?? 0 },
        { label: "METRICS", key: "metrics", right: true, render: (r) => r.metrics.length },
        { label: "UPDATED", key: "updated", render: (r) => String(r.updated).slice(0, 10) },
      ],
      state.sweeps.slice().sort((a, b) => String(b.updated).localeCompare(String(a.updated)))
    )
  );
  root.appendChild(recent);
}

async function renderResults(root) {
  if (!state.sweeps.length) {
    root.appendChild(el("div", "empty", "No sweep artefacts found in the results root."));
    return;
  }
  const holder = el("div", "content-inner");
  root.appendChild(holder);
  await drawResults(holder);
}

async function drawResults(root) {
  root.innerHTML = "";
  state.lastPlots = [];
  const r = state.results;
  if (!r.sweep) {
    /* open the richest sweep (most recorded runs) so the page is substantive */
    r.sweep = state.sweeps.slice()
      .sort((a, b) => (b.counts.executed || 0) - (a.counts.executed || 0))[0].id;
  }

  const toolbar = el("div", "toolbar");

  const sweepField = el("div", "field");
  sweepField.appendChild(el("label", "label", "sweep"));
  const sweepSel = el("select");
  state.sweeps.forEach((s) => {
    const opt = el("option", null, `${s.id} · ${s.counts.executed ?? 0} runs`);
    opt.value = s.id;
    sweepSel.appendChild(opt);
  });
  sweepSel.value = r.sweep;
  sweepSel.addEventListener("change", () => {
    r.sweep = sweepSel.value;
    r.detail = r.agg = r.curves = r.runs = null;
    drawResults(root);
  });
  sweepField.appendChild(sweepSel);
  toolbar.appendChild(sweepField);

  const detail = r.detail || (r.detail = await api(`/api/sweeps/${encodeURIComponent(r.sweep)}`));
  const params = detail.columns.params;
  const metrics = detail.columns.metrics;

  const groupField = el("div", "field");
  groupField.appendChild(el("label", "label", "group by"));
  const groupSel = el("select");
  params.forEach((p) => groupSel.appendChild(el("option", null, p)));
  groupSel.value = detail.default_group_by[0] || params[0];
  groupSel.addEventListener("change", () => drawResults(root).catch(toast));
  groupField.appendChild(groupSel);
  toolbar.appendChild(groupField);

  const metricField = el("div", "field");
  metricField.appendChild(el("label", "label", "metric"));
  const metricSel = el("select");
  metrics.forEach((m) => metricSel.appendChild(el("option", null, m)));
  /* prefer a metric that shows meaningful spread across conditions */
  const prefMetric = ["ord_extreme_final", "ord_joint_final", "ord_mean_opinion_final"]
    .find((m) => metrics.includes(m)) || metrics[0];
  metricSel.value = prefMetric;
  metricSel.addEventListener("change", () => drawResults(root).catch(toast));
  metricField.appendChild(metricSel);
  toolbar.appendChild(metricField);

  toolbar.appendChild(el("div", "spacer"));
  const exportBtn = el("button", "btn", "Export CSV ⇩");
  exportBtn.addEventListener("click", () => window.open(`/api/sweeps/${encodeURIComponent(r.sweep)}/runs?limit=2000`, "_blank"));
  toolbar.appendChild(exportBtn);
  root.appendChild(toolbar);

  const groupKey = groupSel.value;
  const metric = metricSel.value;
  const conf = 0.95;

  if (!r.agg || r.agg.group_by[0] !== groupKey || !r.agg.metrics.includes(metric)) {
    r.agg = await api(`/api/sweeps/${encodeURIComponent(r.sweep)}/aggregate`, { group_by: groupKey, metrics: metric, confidence: conf });
  }
  const agg = r.agg;
  const rows = agg.rows.slice().sort((a, b) => Number(a[groupKey]) - Number(b[groupKey]));
  const ns = rows.map((row) => row[`${metric}.n`]);
  const maxN = Math.max(...ns, 0);
  const zero = ns.filter((n) => !n).length;

  /* KPI row — every figure states its own n and interval */
  const kpis = el("div", "kpis");
  const latest = rows[rows.length - 1] || {};
  const shown = describe(latest, metric, conf);
  const seeds = agg.n_rows && rows.length ? Math.round(agg.n_rows / rows.length) : 0;

  kpis.appendChild(kpi("RUNS IN VIEW", String(agg.n_rows), `${rows.length} conditions · ~${seeds} seeds each`));
  kpis.appendChild(kpi(SHORT(metric, 22), shown.value, shown.sub, shown.warn));
  kpis.appendChild(kpi("MAX SAMPLE SIZE", String(maxN), zero ? `${zero} condition(s) have n = 0` : "all conditions replicated"));
  const commit = state.info?.git_commit ? String(state.info.git_commit).slice(0, 8) : null;
  kpis.appendChild(
    kpi("REPRODUCIBILITY", commit ? "Tracked" : "—",
      commit ? `${commit}${state.info.git_dirty ? " · dirty" : ""} · config + seed recorded` : "no git metadata",
      Boolean(state.info?.git_dirty))
  );
  root.appendChild(kpis);

  if (zero && detail.manifest?.skipped_metrics?.length) {
    const note = el("div", "note");
    note.textContent = "Skipped metric(s): " + detail.manifest.skipped_metrics
      .map((s) => `${s.name} — ${s.reason}`).join("; ");
    root.appendChild(note);
  }

  /* metric vs condition, with the confidence band that n >= 2 earns */
  const x = rows.map((row) => Number(row[groupKey]));
  const traces = [];
  const mean = rows.map((row) => row[`${metric}.mean`]);
  if (maxN >= 2) {
    traces.push({
      x, y: rows.map((row) => row[`${metric}.ci_high`]),
      line: { width: 0 }, hoverinfo: "skip", showlegend: false, type: "scatter", mode: "lines",
    });
    traces.push({
      x, y: rows.map((row) => row[`${metric}.ci_low`]),
      fill: "tonexty", fillcolor: hexA(seriesColors()[0], 0.20), line: { width: 0 },
      hoverinfo: "skip", showlegend: false, type: "scatter", mode: "lines",
    });
  }
  traces.push({
    x, y: mean, name: maxN >= 2 ? "mean with 95% CI" : "single observation per condition",
    line: { color: seriesColors()[0], width: 1.8 }, type: "scatter", mode: "lines+markers",
    hovertemplate: "%{y:.4f}<extra></extra>",
  });
  const pane1 = panel(SHORT(metric, 40), `mean ± ${Math.round(conf * 100)}% CI across seeds · grouped by ${groupKey}`, el("div", "plot"));
  const node1 = pane1.querySelector(".plot");
  node1.id = "plot-metric";
  node1.style.height = "340px";
  root.appendChild(pane1);
  keep(node1.id, traces, plotLayout("", SHORT(metric, 30), groupKey), "", SHORT(metric, 30), groupKey);

  /* evolution across simulation steps */
  if (!r.curves || r.curves.group_by[0] !== groupKey) {
    r.curves = await api(`/api/sweeps/${encodeURIComponent(r.sweep)}/curves`, { group_by: groupKey, confidence: conf }).catch(() => null);
  }
  const curves = r.curves;
  const pane2 = panel("Evolution across steps", "mean per simulation step, aggregated over seeds", null);
  if (curves && curves.available) {
    const available = Object.keys(curves.series[0] || {}).filter((k) => k.endsWith(".mean")).map((k) => k.slice(0, -5));
    const col = available[0];
    const chips = el("div", "toolbar");
    chips.style.marginTop = "8px";
    const labels = curves.series.map((s) => `${groupKey} = ${s[Object.keys(s)[0]]}`);
    chips.appendChild(el("span", "label", "series"));
    curves.series.forEach((s, i) => {
      const chip = el("button", "btn", `${groupKey} ${s[Object.keys(s)[0]]}`);
      if (r.hidden.has(i)) chip.style.opacity = "0.4";
      chip.addEventListener("click", () => {
        r.hidden.has(i) ? r.hidden.delete(i) : r.hidden.add(i);
        drawResults(root).catch(toast);
      });
      chips.appendChild(chip);
    });
    const steps = curves.series[0].step;
    const cTraces = [];
    curves.series.forEach((s, i) => {
      if (r.hidden.has(i)) return;
      const color = seriesColors()[i % seriesColors().length];
      const n = s[`${col}.n`];
      const label = `${groupKey} ${s[Object.keys(s)[0]]}`;
      cTraces.push(...ciTraces(steps, s[`${col}.mean`], s[`${col}.ci_low`], s[`${col}.ci_high`], label, color, n));
    });
    const node2 = el("div", "plot");
    node2.id = "plot-curve";
    node2.style.height = "360px";
    pane2.appendChild(node2);
    pane2.appendChild(chips);
    pane2.querySelector(".card-sub").textContent =
      `${col} · ${curves.n_runs} runs · ${labels.length} series · mean ± ${Math.round(conf * 100)}% CI`;
    root.appendChild(pane2);
    keep(node2.id, cTraces, plotLayout("", col, "simulation step"), "", col, "simulation step");
  } else {
    pane2.appendChild(el("p", "card-sub", curves?.reason || "no time series stored for this sweep"));
    root.appendChild(pane2);
  }

  /* per-run table */
  if (!r.runs) r.runs = await api(`/api/sweeps/${encodeURIComponent(r.sweep)}/runs`, { limit: 20 });
  const runsPane = panel("Run history", `showing ${r.runs.rows.length} of ${r.runs.total} runs · click a column-free row to see the raw artefact`, null);
  runsPane.appendChild(
    dataTable(
      [
        { label: "RUN ID", key: "run_id", cls: "mono" },
        { label: "CONFIG", key: "config_id", cls: "mono" },
        { label: "TOPOLOGY", key: "placement" },
        { label: "SEED", key: "seed", right: true },
        { label: "STATUS", key: "status", render: (row) => {
          const ok = row.status === "ok";
          const wrapS = el("span");
          wrapS.appendChild(el("span", ok ? "dot dot-ok" : "dot dot-fail"));
          wrapS.appendChild(document.createTextNode(" " + (ok ? "Completed" : String(row.status))));
          return wrapS;
        } },
        { label: SHORT(metric, 18).toUpperCase(), key: metric, right: true, render: (row) => fmt(row[metric], 4) },
        { label: "WALL S", key: "wall_seconds", right: true, render: (row) => fmt(row.wall_seconds, 2) },
      ],
      r.runs.rows
    )
  );
  root.appendChild(runsPane);
}

function SHORT(text, max) {
  const s = String(text);
  return s.length <= max ? s : "…" + s.slice(-(max - 1));
}

/* ====================================================== Experiments ===== */

function parseList(text, coerce) {
  return String(text).split(",").map((s) => s.trim()).filter(Boolean).map(coerce);
}

async function renderExperiments(root) {
  const schema = state.schema || (state.schema = await api("/api/schema/sweep").catch(() => null));
  if (!schema) {
    root.appendChild(el("div", "empty", "could not load sweep schema"));
    return;
  }

  const card = el("div", "card");
  card.appendChild(el("p", "card-title", "Configure a new sweep"));
  card.appendChild(el("p", "card-sub",
    "Fields are generated from the platform's own registries — only models, metrics and topologies that actually exist here can be selected. Submitting launches a real background run; when it finishes the sweep appears in Results."));
  const form = el("div", "form");
  card.appendChild(form);
  root.appendChild(card);

  const addField = (labelText, node) => {
    const f = el("div", "field");
    f.appendChild(el("label", "label", labelText));
    f.appendChild(node);
    form.appendChild(f);
    return f;
  };

  const nameInput = el("input");
  nameInput.value = "my-sweep";
  nameInput.placeholder = "directory name under results/";
  addField("sweep name", nameInput);

  const modelSel = el("select");
  schema.models.forEach((m) => {
    const o = el("option", null, m.name + (m.external_kernel ? "  (external kernel)" : "  (synthetic stand-in)"));
    o.value = m.name;
    modelSel.appendChild(o);
  });
  addField("model", modelSel);

  const modeInput = el("input");
  modeInput.value = "decisive";
  modeInput.placeholder = "e.g. decisive / deliberative";
  addField("mode (base)", modeInput);

  const placeSel = el("select");
  schema.topologies.forEach((t) => {
    const o = el("option", null, t);
    o.value = t;
    placeSel.appendChild(o);
  });
  placeSel.value = "ba";
  addField("placement (topology)", placeSel);

  const scenSel = el("select");
  schema.scenarios.forEach((s) => {
    const o = el("option", null, s);
    o.value = s;
    scenSel.appendChild(o);
  });
  addField("scenario", scenSel);

  const axisSel = el("select");
  schema.run_fields.forEach((k) => {
    const o = el("option", null, k);
    o.value = k;
    axisSel.appendChild(o);
  });
  axisSel.value = "beta_gov";
  addField("sweep axis", axisSel);

  const axisVals = el("input");
  axisVals.value = "0, 0.25, 0.5, 0.75, 1.0";
  axisVals.placeholder = "comma-separated values";
  addField("axis values", axisVals);

  const seedsInput = el("input");
  seedsInput.value = "1, 2, 3, 4, 5";
  seedsInput.placeholder = "comma-separated integer seeds";
  addField("seeds", seedsInput);

  const trajWrap = el("div", "field");
  const trajLabel = el("label", "label", "store trajectory");
  const trajChk = el("input");
  trajChk.type = "checkbox";
  trajChk.checked = true;
  trajWrap.appendChild(trajLabel);
  trajWrap.appendChild(trajChk);
  form.appendChild(trajWrap);

  /* metrics: checkbox group, default all */
  const metricWrap = el("div", "field");
  metricWrap.appendChild(el("label", "label", "metrics"));
  const metricBox = el("div", "checks");
  const metricChecks = schema.metrics.map((m) => {
    const id = "m_" + m.name;
    const lab = el("label", "chk");
    const cb = el("input");
    cb.type = "checkbox";
    cb.checked = true;
    cb.id = id;
    cb.value = m.name;
    lab.appendChild(cb);
    lab.appendChild(document.createTextNode(" " + m.name + (m.requires_control ? "  (needs control)" : "")));
    metricBox.appendChild(lab);
    return { name: m.name, cb };
  });
  metricWrap.appendChild(metricBox);
  form.appendChild(metricWrap);

  const submitBtn = el("button", "btn btn-primary", "Launch sweep");
  form.appendChild(submitBtn);

  const progress = el("div", "card", "");
  progress.style.marginTop = "16px";
  progress.style.display = "none";
  root.appendChild(progress);

  submitBtn.addEventListener("click", async () => {
    submitBtn.disabled = true;
    try {
      const axisKey = axisSel.value;
      const axisValues = axisKey === "seed" ? parseList(axisVals.value, Number)
        : (axisKey === "mode" || axisKey === "placement" || axisKey === "scenario"
          ? axisVals.value.split(",").map((s) => s.trim()).filter(Boolean)
          : parseList(axisVals.value, Number));
      const seeds = parseList(seedsInput.value, Number);
      if (!seeds.length) throw new Error("seeds required");
      const checked = metricChecks.filter((m) => m.cb.checked).map((m) => m.name);
      const allOn = checked.length === schema.metrics.length;
      const config = {
        name: nameInput.value.trim(),
        model: modelSel.value,
        base: { mode: modeInput.value.trim(), scenario: scenSel.value, placement: placeSel.value },
        axes: { [axisKey]: axisValues },
        seeds,
        store_trajectory: trajChk.checked,
      };
      const body = { config, metrics: allOn ? ["all"] : checked };
      // POST with JSON body
      const res = await fetch(new URL("/api/runs", window.location.origin), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `${res.status}`);
      }
      const task = await res.json();
      await pollTask(task.task_id, progress, root, config.name);
    } catch (err) {
      progress.style.display = "block";
      progress.innerHTML = "";
      progress.appendChild(el("p", "card-sub", "submit failed: " + err.message));
      toast(err.message);
    } finally {
      submitBtn.disabled = false;
    }
  });
}

async function pollTask(taskId, progress, root, sweepName) {
  progress.style.display = "block";
  const tick = async () => {
    const st = await api(`/api/runs/${taskId}`).catch(() => null);
    progress.innerHTML = "";
    const head = el("p", "card-title", `Running “${sweepName}”`);
    progress.appendChild(head);
    if (!st) { progress.appendChild(el("p", "card-sub", "polling…")); return; }
    const pct = st.total ? Math.round((st.done / st.total) * 100) : 0;
    const bar = el("div", "bar");
    const fill = el("div", "bar-fill");
    fill.style.width = pct + "%";
    bar.appendChild(fill);
    progress.appendChild(bar);
    const status = el("p", "card-sub", `${st.state} · ${st.done}/${st.total} runs (${pct}%)`);
    progress.appendChild(status);
    if (st.state === "done") {
      toast(`“${sweepName}” finished — open Results`);
      try {
        const list = await api("/api/sweeps");
        state.sweeps = list.sweeps || [];
      } catch (_) {}
      const go = el("button", "btn btn-primary", "Open in Results →");
      go.addEventListener("click", () => {
        state.results.sweep = sweepName;
        setView("results");
      });
      progress.appendChild(go);
      return;
    }
    if (st.state === "failed") {
      const pre = el("pre", "log");
      pre.textContent = (st.error || "no log") ;
      progress.appendChild(pre);
      return;
    }
    setTimeout(tick, 800);
  };
  await tick();
}

/* ====================================================== Network ========= */

async function renderNetwork(root) {
  const schema = state.schema || (state.schema = await api("/api/schema/sweep").catch(() => null));
  const kinds = schema ? schema.topologies : ["ba", "er", "ring", "ws", "sbm"];

  const card = el("div", "card");
  card.appendChild(el("p", "card-title", "Generate & inspect a topology"));
  card.appendChild(el("p", "card-sub",
    "Topologies are generated on demand from the platform's native generator with the parameters you give — the same inputs always yield the same graph (hash shown). Deterministic, offline, reproducible. This is a fresh generation, not a stored artefact."));
  const form = el("div", "toolbar");
  card.appendChild(form);
  root.appendChild(card);

  const mkSel = (labelText, opts, def) => {
    const f = el("div", "field");
    f.appendChild(el("label", "label", labelText));
    const s = el("select");
    opts.forEach((o) => { const op = el("option", null, o); op.value = o; s.appendChild(op); });
    s.value = def;
    f.appendChild(s);
    form.appendChild(f);
    return s;
  };
  const mkNum = (labelText, def) => {
    const f = el("div", "field");
    f.appendChild(el("label", "label", labelText));
    const i = el("input"); i.type = "number"; i.value = String(def);
    f.appendChild(i);
    form.appendChild(f);
    return i;
  };

  const kindSel = mkSel("kind", kinds, "ba");
  const nInput = mkNum("n (nodes)", 200);
  const seedInput = mkNum("seed", 1);
  const mInput = mkNum("m (avg degree / k)", 3);
  const betaInput = mkNum("ws beta", 0.2);
  const homoInput = mkNum("homophily", 0.0);
  const nbInput = mkNum("sbm n_blocks", 3);
  const ratioInput = mkNum("sbm ratio", 0.7);

  const genBtn = el("button", "btn btn-primary", "Generate");
  form.appendChild(genBtn);

  const out = panel("Network", "nodes coloured by degree", el("div", "plot"));
  out.querySelector(".plot").style.height = "520px";
  const plotNode = out.querySelector(".plot");
  plotNode.id = "plot-net";
  root.appendChild(out);
  const meta = el("p", "card-sub");
  out.appendChild(meta);

  genBtn.addEventListener("click", async () => {
    const kind = kindSel.value;
    const params = {
      kind, n: Number(nInput.value) || 200, seed: Number(seedInput.value) || 1,
      m: Number(mInput.value) || 3, homophily: Number(homoInput.value) || 0,
    };
    if (kind === "ws") params.beta = Number(betaInput.value) || 0.2;
    if (kind === "sbm") { params.n_blocks = Number(nbInput.value) || 3; params.ratio = Number(ratioInput.value) || 0.7; }
    const net = await api("/api/network", params).catch((e) => { toast(e.message); return null; });
    if (!net) return;
    drawNetwork(plotNode, net);
    const extra = net.extra || {};
    meta.textContent = `kind=${net.kind} · n=${net.n} · edges=${net.n_edges} · hash=${net.hash}` +
      (extra.cross_edge_ratio != null ? ` · cross_edge_ratio=${Number(extra.cross_edge_ratio).toFixed(3)}` : "");
  });
}

function drawNetwork(node, net) {
  const c = themeColors();
  const accent = c.accent;
  const edgeTraces = [{
    x: [].concat(...net.edges.map((e) => [net.nodes[e[0]].x, net.nodes[e[1]].x, null])),
    y: [].concat(...net.edges.map((e) => [net.nodes[e[0]].y, net.nodes[e[1]].y, null])),
    mode: "lines", line: { color: c.line, width: 0.6 }, hoverinfo: "skip", type: "scatter",
  }];
  const xs = net.nodes.map((n) => n.x);
  const ys = net.nodes.map((n) => n.y);
  const degs = net.nodes.map((n) => n.deg);
  const maxDeg = Math.max(1, ...degs);
  const nodeTrace = {
    x: xs, y: ys, mode: "markers", type: "scatter",
    marker: {
      size: degs.map((d) => 4 + (d / maxDeg) * 14),
      color: degs, colorscale: [[0, c.muted], [1, accent]],
      line: { width: 0.5, color: c.text }, showscale: false,
    },
    hovertemplate: "node %{customdata[0]}<br>degree %{customdata[1]}<extra></extra>",
    customdata: net.nodes.map((n) => [n.id, n.deg]),
  };
  const layout = {
    paper_bgcolor: c.bg, plot_bgcolor: c.bg, margin: { l: 8, r: 8, t: 8, b: 8 },
    xaxis: { visible: false }, yaxis: { visible: false, scaleanchor: "x" },
    showlegend: false, hovermode: "closest",
  };
  Plotly.react(node.id, edgeTraces.concat(nodeTrace), layout, { displayModeBar: false, responsive: true });
}

/* ====================================================== Dynamics ======== */

async function renderDynamics(root) {
  const [models, metrics, topologies] = await Promise.all([
    api("/api/models").catch(() => ({ models: [] })),
    api("/api/metrics").catch(() => ({ metrics: [] })),
    api("/api/topologies").catch(() => ({ topologies: [] })),
  ]);

  const mkCard = (title, sub) => {
    const card = el("div", "card");
    card.appendChild(el("p", "card-title", title));
    if (sub) card.appendChild(el("p", "card-sub", sub));
    const list = el("div", "kvlist");
    card.appendChild(list);
    root.appendChild(card);
    return list;
  };

  const mList = mkCard("Dynamics models", "Registered in DYNAMICS — only these can be selected in Experiments.");
  (models.models || []).forEach((m) => {
    const row = el("div", "kv");
    row.appendChild(el("span", "kv-name", m.name));
    const tag = el("span", "badge " + (m.external_kernel ? "badge-ext" : "badge-syn"),
      m.external_kernel ? "external kernel" : "synthetic stand-in");
    row.appendChild(tag);
    const note = el("p", "kv-note",
      m.external_kernel
        ? "Requires the external odin_g3 kernel to run real physics."
        : "Pure-Python reference model for CI, offline demos and pipeline testing. Values are synthetic — not scientific conclusions.");
    mList.appendChild(row);
    mList.appendChild(note);
  });

  const tList = mkCard("Topology generators", "Registered in TOPOLOGY.");
  (topologies.topologies || []).forEach((t) => {
    const row = el("div", "kv");
    row.appendChild(el("span", "kv-name", t));
    row.appendChild(el("span", "kv-note", "Native generator; supports ba / er / ring / ws / sbm."));
    tList.appendChild(row);
  });

  const gList = mkCard("Metrics", "Registered in METRICS. Paired metrics need a control configuration.");
  (metrics.metrics || []).forEach((m) => {
    const row = el("div", "kv");
    row.appendChild(el("span", "kv-name", m.name));
    if (m.requires_control) row.appendChild(el("span", "badge badge-ctl", "needs control"));
    gList.appendChild(row);
  });
}

/* ====================================================== Reproducibility == */

async function renderReproducibility(root) {
  const info = state.info || (state.info = await api("/api/info").catch(() => null));
  const sweeps = state.sweeps;

  const card = el("div", "card");
  card.appendChild(el("p", "card-title", "Environment & code fingerprint"));
  card.appendChild(el("p", "card-sub",
    "Every result in this workspace is tied to the environment that produced it. The values below are sampled live from the running server — never hard-coded."));
  const list = el("div", "kvlist");
  card.appendChild(list);
  root.appendChild(card);

  const add = (k, v, warn) => {
    const row = el("div", "kv");
    row.appendChild(el("span", "kv-name", k));
    const val = el("span", "kv-val" + (warn ? " warn" : ""), v);
    row.appendChild(val);
    list.appendChild(row);
  };
  if (info) {
    add("odin-sim version", info.extra?.odin_sim_version ?? "—");
    add("python", info.python_version ?? "—");
    add("platform", info.platform ?? "—");
    const commit = info.git_commit ? String(info.git_commit).slice(0, 12) : null;
    add("git commit", commit ?? "—", info.git_dirty);
    if (commit) add("working tree", info.git_dirty ? "dirty" : "clean", info.git_dirty);
    const pkgs = info.packages || {};
    Object.entries(pkgs).forEach(([k, v]) => add("package " + k, String(v)));
  } else {
    add("status", "environment snapshot unavailable", true);
  }

  const note = el("p", "card-sub");
  note.style.marginTop = "12px";
  note.textContent = "Determinism: runs are content-addressed (run_id = sha1 of the spec), seeds are explicit, and the sweep fingerprint covers axes, seeds and control口径. Re-running an identical spec reproduces the same run_ids — provenance is built into the artefact, not tracked separately.";
  root.appendChild(note);

  const sw = panel("Sweep fingerprints", `${sweeps.length} sweeps under the results root`, null);
  sw.appendChild(dataTable(
    [
      { label: "SWEEP", key: "id" },
      { label: "MODEL", key: "model" },
      { label: "FINGERPRINT", key: "fingerprint", cls: "mono" },
      { label: "RUNS", key: "counts", right: true, render: (r) => r.counts.executed ?? 0 },
      { label: "UPDATED", key: "updated", render: (r) => String(r.updated).slice(0, 10) },
    ],
    (sweeps || []).slice().sort((a, b) => String(b.updated).localeCompare(String(a.updated)))
  ));
  root.appendChild(sw);
}

/* ---------------------------------------------------------------- boot */

async function boot() {
  /* deep-link: ?theme=dark|light|system forces the palette (shareable link) */
  const themeParam = new URLSearchParams(window.location.search).get("theme");
  if (themeParam === "dark" || themeParam === "light" || themeParam === "system") {
    localStorage.setItem("odin-theme", themeParam);
  }

  initTheme();
  applyTheme();

  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.addEventListener("click", () => setView(btn.dataset.view));
  });

  const [info, models] = await Promise.all([
    api("/api/info").catch(() => null),
    api("/api/models").catch(() => null),
  ]);
  state.info = info;
  state.models = models;

  $("#version-text").textContent = info?.extra?.odin_sim_version ?? "—";
  $("#env-text").textContent = info ? `${info.python_version} · uv` : "unreachable";
  const engine = $("#engine-badge");
  const count = models?.models?.length ?? 0;
  engine.lastChild.textContent = count ? `ENGINE · ${count} MODEL${count > 1 ? "S" : ""}` : "ENGINE · NO MODEL";
  if (!count) engine.classList.add("is-off");

  try {
    const list = await api("/api/sweeps");
    state.sweeps = list.sweeps || [];
  } catch (err) {
    toast(`could not list sweeps: ${err.message}`);
  }

  /* deep-link: ?view=results opens a specific workspace view (shareable link) */
  const wanted = new URLSearchParams(window.location.search).get("view");
  setView(VIEWS[wanted] ? wanted : "overview");
}

boot().catch((err) => toast(`startup failed: ${err.message}`));
