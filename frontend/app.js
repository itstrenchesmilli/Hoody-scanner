const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

const RULE_NAMES = {
  1: "Bias + POI + liquidity + invalidation",
  2: "Breakout and retest",
  3: "Order block / liquidity reaction",
  4: "Volatility and volume",
  5: "Fundamentals, macro, funding, BTC",
  6: "Defined entry, stop, targets, R:R",
  7: "Do not chase",
  8: "Multi-timeframe alignment",
};
const RULE_MAX = { 1: 28, 2: 16, 3: 10, 4: 12, 5: 12, 6: 8, 7: 6, 8: 8 };

const state = {
  setups: [],
  context: [],
  selected: null,
  dir: "all",
  st: "all",
  min: 0,
  q: "",
  watchOnly: false,
  watch: loadWatch(),
  status: {},
  changes: {},
  chart: null,
};

function loadWatch() {
  try { return JSON.parse(localStorage.getItem("hoody.watchlist") || "[]"); }
  catch { return []; }
}
function saveWatch() {
  localStorage.setItem("hoody.watchlist", JSON.stringify(state.watch));
}
function isWatched(sym) { return state.watch.includes(sym); }
function toggleWatch(sym) {
  if (isWatched(sym)) state.watch = state.watch.filter((x) => x !== sym);
  else state.watch = [...state.watch, sym];
  saveWatch();
}

function esc(t) {
  return String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmt(n, d) {
  if (n == null || Number.isNaN(n)) return "—";
  const a = Math.abs(n);
  if (a >= 1000) return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (a >= 1) return n.toFixed(d ?? 4).replace(/\.?0+$/, "");
  if (a >= 0.01) return n.toFixed(6).replace(/\.?0+$/, "");
  return n.toFixed(8).replace(/\.?0+$/, "");
}
function fmtPct(n) {
  if (n == null || Number.isNaN(n)) return "—";
  return (n >= 0 ? "+" : "") + n.toFixed(2) + "%";
}
function fmtUsd(n) {
  if (n == null) return "—";
  if (n >= 1e9) return "$" + (n / 1e9).toFixed(2) + "B";
  if (n >= 1e6) return "$" + (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return "$" + (n / 1e3).toFixed(0) + "K";
  return "$" + n.toFixed(0);
}
function fmtFunding(n) {
  if (n == null || Number.isNaN(n)) return "—";
  const pct = (n * 100).toFixed(4) + "%";
  if (Math.abs(n) >= 0.2) return pct + " (extreme / cap)";
  return pct;
}

async function jget(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

function filtered() {
  return state.setups.filter((s) => {
    if (state.dir !== "all" && s.direction !== state.dir) return false;
    if (state.st !== "all" && s.state !== state.st) return false;
    if ((s.score || 0) < state.min) return false;
    if (state.watchOnly && !isWatched(s.symbol)) return false;
    if (state.q) {
      const n = state.q.toUpperCase().lstrip ? state.q.toUpperCase() : state.q.toUpperCase();
      const needle = n.replace(/^\$/, "");
      if (!s.symbol.includes(needle) && !(s.coin || "").includes(needle)) return false;
    }
    return true;
  });
}

function renderHero() {
  const s = state.status || {};
  const btc = s.btc || {};
  const ch = btc.change_24h_pct;
  const macro = s.macro || {};
  const tops = (state.setups || []).slice(0, 3);
  const chg = ch == null ? "" : `<span class="${ch > 0 ? "up" : "down"}">${fmtPct(ch)}</span>`;
  const stance = (macro.stance || "neutral").replace("_", " ");
  $("#updated").textContent = s.last_scan_qatar
    ? `Updated ${s.last_scan_qatar} · crypto only · ${s.pairs_scanned || 0} scanned`
    : "Bitunix USDT perps · crypto only";
  $("#hero").innerHTML = `
    <div class="hero-card">
      <h2>Market context</h2>
      <div class="btc-line">BTC ${fmt(btc.price, 1)} ${chg}</div>
      <div class="regime">Daily bias <strong>${esc(btc.bias || "—")}</strong>
        · 7d range ${btc.range_7d_pct == null ? "—" : btc.range_7d_pct.toFixed(1) + "%"}
        · Funding ${fmtFunding(btc.funding_rate)}</div>
      <div class="regime">Macro: <strong class="${esc(macro.stance || "")}">${esc(stance)}</strong>
        ${macro.next_high ? " · Next: " + esc(macro.next_high) : ""}</div>
      <div class="regime" style="margin-top:8px">Majors are context only — they do not lead the discovery board.
        ${(s.context_majors || []).slice(0, 6).map((m) =>
          `<span class="pill ${m.direction || ""}">${esc(m.coin)} ${m.score}</span>`
        ).join(" ")}
      </div>
    </div>
    <div class="hero-card">
      <h2>Top discovery setups</h2>
      <div class="tops">
        ${tops.length ? tops.map((t) => `
          <div class="mini" data-sym="${t.symbol}">
            <div class="l">
              <div class="score ${(t.discovery_score || 0) >= 65 ? "hi" : "mid"}">${t.discovery_score ?? "—"}<small>DISC</small></div>
              <div>
                <h3>$${esc(t.coin)}</h3>
                <div class="meta"><span class="pill ${t.direction}">${t.direction}</span> <span class="pill ${t.state}">${t.state}</span></div>
              </div>
            </div>
            <div class="px">${fmt(t.last_price)}<div class="chg ${(t.change_24h_pct || 0) >= 0 ? "up" : "down"}">${fmtPct(t.change_24h_pct)}</div></div>
          </div>`).join("") : "<p class='empty'>Waiting for the first scan…</p>"}
      </div>
    </div>`;
  $$(".mini", $("#hero")).forEach((el) => {
    el.onclick = () => {
      const row = state.setups.find((x) => x.symbol === el.dataset.sym);
      if (row) select(row);
    };
  });
}

function renderChanges() {
  const c = state.changes || {};
  const bits = [];
  if ((c.new || []).length) bits.push(`New: ${(c.new).slice(0, 8).map((s) => s.replace("USDT", "")).join(", ")}`);
  if ((c.state_changes || []).length) bits.push(`${c.state_changes.length} state change(s)`);
  if ((c.dropped || []).length) bits.push(`${c.dropped.length} left the board`);
  const el = $("#changes");
  if (!bits.length) { el.hidden = true; el.innerHTML = ""; return; }
  el.hidden = false;
  el.textContent = "Since last scan · " + bits.join(" · ");
}

function renderList() {
  const rows = filtered();
  $("#count").textContent = `${rows.length} discovery setups · sorted by Hoody discovery (70%) + rule score (30%)`;
  const root = $("#list");
  if (!rows.length) {
    root.innerHTML = `<p class="empty">${state.watchOnly ? "No watched names match these filters." : "No setups match these filters."}</p>`;
    return;
  }
  root.innerHTML = rows.map((s) => {
    const ch = s.change_24h_pct;
    const disc = s.discovery_score ?? 0;
    const cls = disc >= 65 ? "hi" : disc >= 50 ? "mid" : "lo";
    const active = state.selected && state.selected.symbol === s.symbol ? "active" : "";
    const flags = (s.flags || []).filter((f) => f.id === "alert" || f.id === "chase" || f.id === "funding").slice(0, 2);
    return `<div class="row ${active}" data-sym="${s.symbol}">
      <div class="score ${cls}">${disc}<small>DISC</small></div>
      <div>
        <h3>$${esc(s.coin)} ${isWatched(s.symbol) ? "★" : ""}
          <span class="pill ${s.direction}">${s.direction}</span>
          <span class="pill ${s.state}">${s.state}</span>
        </h3>
        <div class="meta">
          <span>Setup ${s.score}</span>
          ${s.reward_risk != null ? `<span>R:R ${s.reward_risk.toFixed(2)}</span>` : ""}
          <span>${esc(s.entry_zone || "—")}</span>
          ${flags.map((f) => `<span class="flag">${esc(f.label)}</span>`).join("")}
        </div>
      </div>
      <div class="px">${fmt(s.last_price)}<div class="chg ${ch > 0 ? "up" : "down"}">${fmtPct(ch)}</div></div>
    </div>`;
  }).join("");
  $$(".row", root).forEach((el) => {
    el.onclick = () => {
      const s = state.setups.find((x) => x.symbol === el.dataset.sym);
      if (s) select(s);
    };
  });
}

function ruleTick(pts, max) {
  if (pts >= max * 0.75) return "pass";
  if (pts >= max * 0.35) return "partial";
  return "fail";
}
function ruleMark(kind) {
  if (kind === "pass") return "✓";
  if (kind === "partial") return "·";
  return "✕";
}

function renderDetail(s) {
  const fa = s.fa || {};
  const ta = s.ta || {};
  const rules = [1, 2, 3, 4, 5, 6, 7, 8].map((i) => {
    const scores = s.rule_scores || {};
    const notes = s.rule_notes || {};
    const pts = scores[i] ?? scores[String(i)] ?? 0;
    const note = notes[i] ?? notes[String(i)] ?? "";
    const kind = ruleTick(pts, RULE_MAX[i]);
    return `<div class="rule"><div class="tick ${kind}">${ruleMark(kind)}</div>
      <div><div class="name">Rule ${i} · ${RULE_NAMES[i]} <span class="pill">${pts}/${RULE_MAX[i]}</span></div>
      <p>${esc(note)}</p></div></div>`;
  }).join("");
  const news = (fa.news || []).map((n) => `<li><a href="${esc(n.link)}" target="_blank" rel="noopener">${esc(n.title)}</a></li>`).join("");
  const tags = (fa.categories || []).map((c) => `<span class="pill">${esc(c)}</span>`).join("");
  const ch = s.change_24h_pct;
  const flags = (s.flags || []).map((f) => `<span class="pill ${f.id === "chase" || f.id === "funding" ? "stretched" : "confirmed"}">${esc(f.label)}</span>`).join(" ");
  const dnotes = (s.discovery_notes || []).map((t) => `<p class="auto">${esc(t)}</p>`).join("");
  const feat = s.discovery_features || {};
  $("#detail").innerHTML = `
    <div class="detail-scroll">
      <button type="button" class="back" id="back-btn">← Board</button>
      <div class="detail-head">
        <div>
          <h2>$${esc(s.coin)}</h2>
          <div class="meta" style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px">
            <span class="pill ${s.direction}">${s.direction}</span>
            <span class="pill ${s.state}">${s.state}</span>
            <span class="pill">${esc(s.symbol)}</span>
            ${fa.name ? `<span class="pill">${esc(fa.name)}</span>` : ""}
          </div>
        </div>
        <div style="display:flex;gap:8px;align-items:flex-start">
          <button type="button" class="star ${isWatched(s.symbol) ? "on" : ""}" id="star-btn" aria-label="Watchlist">${isWatched(s.symbol) ? "★" : "☆"}</button>
          <div class="price-lg">${fmt(s.last_price)}
            <div class="chg ${ch > 0 ? "up" : "down"}" style="font-size:14px">${fmtPct(ch)}</div>
            <div style="font-size:11px;color:var(--muted)">Disc ${s.discovery_score ?? "—"} · Setup ${s.score} · Fund ${fmtFunding(s.funding_rate)}</div>
          </div>
        </div>
      </div>
      <p class="narrative">${esc(s.narrative || "")}</p>
      <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px">${flags}</div>
      <div class="chart-tf" id="chart-tf">
        <button data-tf="1h">1H</button>
        <button data-tf="4h" class="on">4H</button>
        <button data-tf="1d">1D</button>
      </div>
      <div class="chart-wrap"><div id="chart"></div></div>
      <div class="grid2" style="margin-top:12px">
        <div class="card">
          <h3>Plan — confirm on the chart</h3>
          <div class="kv"><span>Entry zone</span><span>${esc(s.entry_zone || "—")}</span></div>
          <div class="kv"><span>Invalidation / SL</span><span>${s.stop == null ? "—" : fmt(s.stop)}</span></div>
          <div class="kv"><span>TP1</span><span>${s.tp1 == null ? "—" : fmt(s.tp1)}</span></div>
          <div class="kv"><span>TP2</span><span>${s.tp2 == null ? "—" : fmt(s.tp2)}</span></div>
          <div class="kv"><span>R:R to TP1</span><span>${s.reward_risk == null ? "—" : s.reward_risk.toFixed(2)}</span></div>
          <p class="auto" style="margin-top:8px">${esc(s.invalidation || "")}</p>
        </div>
        <div class="card">
          <h3>Hoody discovery (primary rank)</h3>
          <div class="kv"><span>Discovery score</span><span>${s.discovery_score ?? "—"} / 100</span></div>
          <div class="kv"><span>Board rank mix</span><span>${s.board_rank == null ? "—" : s.board_rank.toFixed(1)}</span></div>
          <div class="kv"><span>7d range / ATR</span><span>${feat.range_7d_pct == null ? "—" : feat.range_7d_pct.toFixed(1) + "%"} / ${feat.atr14_pct == null ? "—" : feat.atr14_pct.toFixed(1) + "%"}</span></div>
          <div class="kv"><span>Vol spike / vs BTC 7d</span><span>${feat.volume_spike == null ? "—" : feat.volume_spike.toFixed(1) + "×"} / ${feat.rs_vs_btc_7d_pct == null ? "—" : feat.rs_vs_btc_7d_pct.toFixed(1) + " pp"}</span></div>
          <div class="kv"><span>Listing age</span><span>${feat.age_days == null ? "—" : Math.round(feat.age_days) + "d"}</span></div>
          ${dnotes}
        </div>
      </div>
      <div class="card" style="margin-top:10px">
        <h3>Secondary confluence — rules 1–8 (weighted 30% of board rank)</h3>
        ${rules}
      </div>
      <div class="grid2" style="margin-top:10px">
        <div class="card">
          <h3>Technical analysis</h3>
          <p class="auto">Automated from Bitunix klines. Order blocks are an approximation of the last opposing candle, not a hand-drawn chart.</p>
          <div class="kv"><span>Daily / 4H / 1H bias</span><span>${esc((ta.bias_daily || "—") + " / " + (ta.bias_4h || "—") + " / " + (ta.bias_1h || "—"))}</span></div>
          <div class="kv"><span>4H range</span><span>${ta.range_4h ? esc(fmt(ta.range_4h.low) + " – " + fmt(ta.range_4h.high)) : "—"}</span></div>
          <div class="kv"><span>Breakout</span><span>${esc(ta.breakout || ta.breakout_state || "—")}</span></div>
          <div class="kv"><span>4H RSI</span><span>${ta.rsi_4h == null ? "—" : ta.rsi_4h.toFixed(1)}</span></div>
          ${(ta.stretch_notes || []).length ? `<p class="judgement">${esc((ta.stretch_notes || []).join("; "))}</p>` : ""}
        </div>
        <div class="card">
          <h3>Fundamentals / catalysts</h3>
          <p class="judgement">Headlines, unlock keywords, and narrative tags need judgement — they are helpers, not a thesis.</p>
          <div class="taglist">${tags || '<span class="pill">no category tags</span>'}</div>
          <div class="kv"><span>Market cap</span><span>${fmtUsd(fa.market_cap)} ${fa.market_cap_rank ? "· #" + fa.market_cap_rank : ""}</span></div>
          ${fa.unlock ? `<p><strong>Unlock mention:</strong> ${esc(fa.unlock.headline)}</p>` : `<p class="auto">No unlock keyword in the latest headlines. No free official unlock calendar.</p>`}
          <ul class="news">${news || "<li>No headlines fetched.</li>"}</ul>
        </div>
      </div>
      <div class="card" style="margin-top:10px">
        <h3>Automated vs judgement</h3>
        ${(s.automated || []).map((t) => `<p class="auto">${esc(t)}</p>`).join("")}
        ${(s.needs_judgement || []).map((t) => `<p class="judgement">${esc(t)}</p>`).join("")}
      </div>
    </div>`;
  $("#back-btn").onclick = closeDetail;
  $("#star-btn").onclick = (e) => {
    e.stopPropagation();
    toggleWatch(s.symbol);
    renderList();
    renderDetail(s);
  };
  $$("#chart-tf button").forEach((b) => {
    b.onclick = () => {
      $$("#chart-tf button").forEach((x) => x.classList.remove("on"));
      b.classList.add("on");
      loadChart(s.symbol, b.dataset.tf);
    };
  });
}

function select(s) {
  state.selected = s;
  document.body.classList.add("detail-open");
  renderList();
  renderDetail(s);
  history.replaceState(null, "", "#/" + s.symbol.replace("USDT", ""));
  requestAnimationFrame(() => loadChart(s.symbol, "4h"));
}
function closeDetail() {
  document.body.classList.remove("detail-open");
  history.replaceState(null, "", "#/");
}

async function loadChart(symbol, interval) {
  const el = $("#chart");
  if (!el || typeof LightweightCharts === "undefined") return;
  const data = await jget(`/api/klines/${symbol}?interval=${interval}&limit=200`);
  el.innerHTML = "";
  if (state.chart && state.chart.remove) {
    try { state.chart.remove(); } catch (_) {}
  }
  const chart = LightweightCharts.createChart(el, {
    layout: { background: { color: "#0b0d12" }, textColor: "#9aa0ae" },
    grid: { vertLines: { color: "#1c2028" }, horzLines: { color: "#1c2028" } },
    rightPriceScale: { borderColor: "#252a38" },
    timeScale: { borderColor: "#252a38", timeVisible: interval !== "1d" },
    crosshair: { mode: 1 },
    width: el.clientWidth,
    height: el.clientHeight,
  });
  const series = chart.addCandlestickSeries({
    upColor: "#3dbe9a", downColor: "#f07167", borderVisible: false,
    wickUpColor: "#3dbe9a", wickDownColor: "#f07167",
  });
  const candles = (data.rows || []).map((r) => ({
    time: Math.floor(r.t / 1000), open: r.o, high: r.h, low: r.l, close: r.c,
  }));
  series.setData(candles);
  const s = state.selected;
  const lines = [];
  if (s && s.stop) lines.push({ price: s.stop, color: "#f07167", title: "SL" });
  if (s && s.tp1) lines.push({ price: s.tp1, color: "#3dbe9a", title: "TP1" });
  if (s && s.tp2) lines.push({ price: s.tp2, color: "#8ea0c4", title: "TP2" });
  if (s && s.entry_low != null) lines.push({ price: s.entry_low, color: "#f0b429", title: "Entry lo" });
  if (s && s.entry_high != null && s.entry_low != null && Math.abs(s.entry_high - s.entry_low) / (s.last_price || 1) > 0.001) {
    lines.push({ price: s.entry_high, color: "#f0b429", title: "Entry hi" });
  }
  lines.forEach((l) => series.createPriceLine({ price: l.price, color: l.color, lineWidth: 1, lineStyle: 2, title: l.title, axisLabelVisible: true }));
  chart.timeScale().fitContent();
  state.chart = chart;
  new ResizeObserver(() => {
    if (el.clientWidth) chart.applyOptions({ width: el.clientWidth, height: el.clientHeight });
  }).observe(el);
}

async function loadStatus() {
  const s = await jget("/api/status");
  state.status = s;
  state.changes = s.changes || {};
  state.context = s.context_majors || [];
  renderHero();
  renderChanges();
}

async function loadSetups() {
  const params = new URLSearchParams();
  if (state.dir !== "all") params.set("direction", state.dir);
  if (state.st !== "all") params.set("state", state.st);
  params.set("min_score", String(state.min));
  if (state.q) params.set("q", state.q);
  const data = await jget("/api/setups?" + params.toString());
  state.setups = data.setups || [];
  state.changes = data.changes || state.changes;
  renderList();
  renderHero();
  renderChanges();
  if (state.selected) {
    const still = state.setups.find((x) => x.symbol === state.selected.symbol);
    if (still) state.selected = still;
  }
}

function bindFilters() {
  $("#dir-filter").onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    $$("#dir-filter button").forEach((x) => x.classList.remove("on"));
    b.classList.add("on");
    state.dir = b.dataset.v;
    renderList();
  };
  $("#state-filter").onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    $$("#state-filter button").forEach((x) => x.classList.remove("on"));
    b.classList.add("on");
    state.st = b.dataset.v;
    renderList();
  };
  const slider = $("#min-score");
  slider.oninput = () => { $("#min-score-val").textContent = slider.value; };
  slider.onchange = () => { state.min = Number(slider.value); renderList(); };
  let t;
  $("#q").oninput = () => {
    clearTimeout(t);
    t = setTimeout(() => { state.q = $("#q").value; renderList(); }, 160);
  };
  $("#watch-only-btn").onclick = () => {
    state.watchOnly = !state.watchOnly;
    $("#watch-only-btn").setAttribute("aria-pressed", String(state.watchOnly));
    renderList();
  };
  $("#rescan-btn").onclick = async () => {
    $("#rescan-btn").disabled = true;
    try {
      await fetch("/api/rescan", { method: "POST" });
      $("#updated").textContent = "Scan started…";
    } finally {
      setTimeout(() => { $("#rescan-btn").disabled = false; }, 4000);
    }
  };
}

async function tickLive() {
  try {
    const data = await jget("/api/tickers-live");
    const map = data.tickers || {};
    state.setups.forEach((s) => {
      const t = map[s.symbol];
      if (t) {
        s.last_price = t.last_price;
        s.change_24h_pct = t.change_24h_pct;
        if (t.funding_rate != null) s.funding_rate = t.funding_rate;
      }
    });
    if (data.btc) state.status.btc = { ...(state.status.btc || {}), ...data.btc };
    renderList();
    renderHero();
    if (state.selected && map[state.selected.symbol] && document.body.classList.contains("detail-open")) {
      Object.assign(state.selected, map[state.selected.symbol]);
    }
  } catch (_) { /* ignore */ }
}

function openFromHash() {
  const h = (location.hash || "").replace(/^#\/?/, "").toUpperCase();
  if (!h) return;
  const sym = h.endsWith("USDT") ? h : h + "USDT";
  const s = state.setups.find((x) => x.symbol === sym) || (state.context || []).find((x) => x.symbol === sym);
  if (s) select(s);
}

async function boot() {
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
  bindFilters();
  await loadStatus();
  await loadSetups();
  openFromHash();
  window.addEventListener("hashchange", openFromHash);
  setInterval(loadStatus, 15000);
  setInterval(loadSetups, 30000);
  setInterval(tickLive, 10000);
}
boot().catch((e) => {
  document.body.insertAdjacentHTML("beforeend", `<p style="padding:20px;color:#f07167">${esc(e.message)}</p>`);
});
