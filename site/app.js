(() => {
  "use strict";

  const state = { meta: null, slate: [], ratings: [], record: null, trends: null, filter: "all" };
  const $ = (sel) => document.querySelector(sel);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const minus = (s) => String(s).replace("-", "−");
  const num = (v, d = 1) => (v == null ? "—" : minus(Number(v).toFixed(d)));
  const signed = (s) => (s == null ? "—" : s === 0 ? "PK" : minus((s > 0 ? "+" : "") + Number(s).toFixed(1)));
  const pct = (v) => (v == null ? "—" : (v * 100).toFixed(1) + "%");
  const recStr = (r, ties = true) => (r ? `${r.W}–${r.L}${ties && r.P ? "–" + r.P : ""}` : "—");
  // Home-perspective spread -> "Favorite −X".
  const favLine = (g, s) => (s == null ? "—" : s === 0 ? "Pick'em" : s < 0 ? `${g.home.name} ${num(s)}` : `${g.away.name} ${num(-s)}`);
  // Card version: team and number in separate spans so a long name can truncate
  // (or wrap) while the number itself always stays visible.
  const favLineHtml = (g, s) => (s == null ? "—" : s === 0 ? "Pick'em"
    : `<span class="fl-team">${esc(s < 0 ? g.home.name : g.away.name)}</span><span class="fl-num">${esc(num(s < 0 ? s : -s))}</span>`);
  const teamLine = (team, s) => `${team} ${s === 0 ? "PK" : signed(s)}`;

  async function getJSON(name) {
    try {
      const res = await fetch(`${window.DATA_PATH || "data/"}${name}.json`, { cache: "no-cache" });
      return res.ok ? await res.json() : null;
    } catch {
      return null;
    }
  }

  const T = (k, d) => state.meta?.terms?.[k] ?? d;
  const fmtTime = (d) => d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit", timeZoneName: "short" });
  const fmtDay = (d) => d.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });

  // --- Tabs ------------------------------------------------------------------
  function showTab(name) {
    document.querySelectorAll(".nav a").forEach((a) => a.setAttribute("aria-selected", a.dataset.tab === name));
    document.querySelectorAll(".tab-panel").forEach((p) => (p.hidden = p.id !== `tab-${name}`));
  }
  document.querySelectorAll(".nav a, .wordmark").forEach((a) =>
    a.addEventListener("click", (e) => {
      e.preventDefault();
      const t = a.getAttribute("href").slice(1);
      showTab(t);
      try { history.replaceState(null, "", `#${t}`); } catch { /* ignore */ }
      window.scrollTo({ top: 0 });
    })
  );
  window.addEventListener("hashchange", () => {
    const t = location.hash.slice(1);
    if (document.getElementById(`tab-${t}`)) showTab(t);
  });

  // --- Sortable tables -------------------------------------------------------
  function renderTable(el, cols, rows, sortState) {
    const ss = sortState || el._sort || {};
    if (ss.key) {
      const col = cols.find((c) => c.key === ss.key);
      const val = col.sortVal || ((r) => r[col.key]);
      rows = [...rows].sort((a, b) => {
        const va = val(a), vb = val(b);
        if (va == null && vb == null) return 0;
        if (va == null) return 1;
        if (vb == null) return -1;
        const cmp = typeof va === "string" ? va.localeCompare(vb) : va - vb;
        return ss.asc ? cmp : -cmp;
      });
    }
    el._sort = ss;
    el.innerHTML =
      `<thead><tr>${cols
        .map((c) => `<th data-key="${c.key}" class="${c.num ? "n" : ""} ${ss.key === c.key ? "sorted" + (ss.asc ? " asc" : "") : ""}">${esc(c.label)}</th>`)
        .join("")}</tr></thead><tbody>${rows
        .map((r) => `<tr>${cols.map((c) => `<td class="${c.num ? "n" : ""}">${c.render ? c.render(r) : esc(r[c.key] ?? "—")}</td>`).join("")}</tr>`)
        .join("")}</tbody>`;
    el.querySelectorAll("th").forEach((th) =>
      th.addEventListener("click", () => {
        const key = th.dataset.key;
        el._sort = { key, asc: el._sort.key === key ? !el._sort.asc : !!cols.find((c) => c.key === key).ascFirst };
        el._rerender();
      })
    );
  }

  const logo = (src, name, cls = "") =>
    src
      ? `<img src="${esc(src)}" alt="" loading="lazy" class="${cls}" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'initials',textContent:'${esc(initials(name))}'}))">`
      : `<span class="initials">${esc(initials(name))}</span>`;
  function initials(name) {
    const w = String(name || "").replace(/[^A-Za-z ]/g, "").split(/\s+/).filter(Boolean);
    return (w.length > 1 ? w[0][0] + w[1][0] : (w[0] || "?").slice(0, 3)).toUpperCase();
  }

  // --- Game cards ------------------------------------------------------------
  function sideName(side, g) {
    return { home: g.home.name, away: g.away.name, over: "Over", under: "Under" }[side];
  }
  const mktName = (f) => ({ spread: "ATS", moneyline: "ML", total: "total" }[f.market]);

  function flagText(f, g) {
    const name = sideName(f.side, g);
    const nums = f.money != null ? `${f.bets}% bets / ${f.money}% $` : `${f.bets}% bets`;
    if (f.type === "sharp") return `Sharp $ on ${name} ${mktName(f)} (${nums})`;
    if (f.type === "public") return `Public on ${name} ${mktName(f)} (${nums})`;
    return `Reverse line move toward ${name} (${Math.abs(f.move)} pts vs ${100 - f.bets}% of bets)`;
  }

  function trendRow(t) {
    const cls = t.notable ? (t.pct >= 0.5 ? "hot" : "cold") : "";
    return `<div class="trend ${cls}"><span>${esc(t.label)}</span><span class="r">${t.w}–${t.l}${t.p ? "–" + t.p : ""} · ${t.pct == null ? "—" : Math.round(t.pct * 100) + "%"}</span></div>`;
  }

  function splitBlock(market, label, keys, g) {
    const m = g.splits.markets[market];
    if (!m) return "";
    const bar = (cls, v) => (v == null ? "" : `<div class="bar ${cls}"><span style="width:${Math.max(0, Math.min(100, v))}%"></span><em>${v}%</em></div>`);
    const rows = keys
      .map((k) => `<div class="split-row"><span class="who">${esc(sideName(k, g))}</span><div class="bars">${bar("bets", m[k].bets)}${bar("money", m[k].money)}</div></div>`)
      .join("");
    return `<div class="split"><div class="split-title">${label}</div>${rows}</div>`;
  }

  // Anytime scorer best bets (NFL anytime TD, NHL anytime goal)
  function propsSection(g) {
    const rows = g.pick?.props;
    if (!rows || !rows.length) return "";
    const title = state.meta?.sport === "nhl" ? "Anytime goal" : "Anytime TD";
    const anyBet = rows.some((b) => b.bet);
    const odds = (v) => (v == null ? "—" : (v > 0 ? "+" : "") + Math.round(v));
    const resTag = (b) => (b.result ? `<span class="res ${b.result}">${b.result === "W" ? "Scored" : "No"}</span>` : "");
    const body = rows.map((b) => `<div class="prop-row">
        <div class="prop-who"><strong>${esc(b.name)}</strong> <span class="muted">${esc([b.pos, b.team].filter(Boolean).join(" · "))}</span></div>
        <div class="prop-nums">
          <span title="Model probability to score">${Math.round(b.p * 100)}%</span>
          <span class="muted" title="Model fair odds">fair ${odds(b.fair)}</span>
          ${b.odds != null ? `<span title="Best price${b.n_books ? " of " + b.n_books + " books" : ""}">${odds(b.odds)}${b.book ? ` <span class="muted">${esc(b.book)}</span>` : ""}</span>` : ""}
          ${b.ev != null ? `<span class="${b.ev > 0 ? "pos" : "neg"}">EV ${b.ev > 0 ? "+" : ""}${(b.ev * 100).toFixed(1)}%</span>` : ""}
          ${b.bet ? '<span class="tier play">bet</span>' : ""}${resTag(b)}
        </div>
      </div>`).join("");
    const note = anyBet ? "Best bets ranked by Kelly (EV vs the best price, weighted by scoring chance)."
      : rows[0]?.note === "no value at market price" ? "No player priced with enough value; top scoring chances shown."
      : "Top scoring chances (no market prices yet).";
    return `<details class="section props" ${anyBet ? "open" : ""}><summary>${title}${anyBet ? ` <span class="tier play">${rows.filter((b) => b.bet).length} bet${rows.filter((b) => b.bet).length > 1 ? "s" : ""}</span>` : ""}</summary>
      <div class="section-body">${body}<p class="small-note">${esc(note)}</p></div></details>`;
  }

  function splitsSection(g) {
    const provider = state.meta?.splits?.provider;
    if (!g.splits) {
      const msg = !provider || provider === "none" ? "Bet-splits feed not configured — see Method." : "No splits posted for this game yet.";
      return `<details class="section"><summary>Bet splits</summary><div class="section-body"><p class="small-note">${msg}</p></div></details>`;
    }
    const flags = g.splits.flags || [];
    const lead = flags.find((f) => f.type === "sharp" || f.type === "rlm");
    const hint = lead ? `<span class="hint">⚡ ${lead.type === "rlm" ? "RLM" : "Sharp"} · ${esc(sideName(lead.side, g))} ${mktName(lead)}</span>` : "";
    return `<details class="section" ${lead ? "open" : ""}><summary>Bet splits ${hint}</summary><div class="section-body">
      <div class="legend"><span><i style="background:var(--bar-bets)"></i>Bets</span><span><i style="background:var(--accent)"></i>Money</span></div>
      ${splitBlock("spread", "Spread", ["away", "home"], g)}
      ${splitBlock("moneyline", "Moneyline", ["away", "home"], g)}
      ${splitBlock("total", "Total", ["over", "under"], g)}
      ${flags.length ? `<div class="flags">${flags.map((f) => `<span class="flag ${f.type}">${esc(flagText(f, g))}</span>`).join("")}</div>` : `<p class="small-note">No sharp/public divergence.</p>`}
      ${g.splits.updated ? `<p class="small-note" style="margin-top:6px">Updated ${esc(new Date(g.splits.updated).toLocaleString())}</p>` : ""}
    </div></details>`;
  }

  function gameCard(g) {
    const p = g.pick || {};
    const r = g.result || {};
    const final = r.completed;
    const ln = g.line;
    const d = new Date(g.start);
    const tags = [];
    if (final) tags.push(`<span class="tag final">Final</span>`);
    else if (status(g) === "live") tags.push(`<span class="tag">In progress</span>`);
    if (g.neutral) tags.push(`<span class="tag">Neutral</span>`);

    const teamRow = (side) => {
      const t = g[side];
      const other = side === "home" ? "away" : "home";
      const score = final ? r[`${side}_pts`] : g.model[`${side}_pts`];
      const lost = final && r[`${side}_pts`] < r[`${other}_pts`];
      const sub = [t.record, t.rank ? `Power #${t.rank}` : null, t.conf].filter(Boolean).join(" · ");
      return `<div class="team-row ${lost ? "loser" : ""}">
        <span class="logo-frame">${logo(t.logo, t.name)}</span>
        <div class="team-identity">
          <div class="team-name-line">${t.poll_rank ? `<span class="rank">${t.poll_rank}</span>` : ""}<strong>${esc(t.name)}</strong></div>
          <span class="team-record">${esc(sub)}</span>
        </div>
        <div class="score-block"><small>${final ? "Final" : "Projected"}</small><strong>${score == null ? "—" : final ? score : Number(score).toFixed(g.score_decimals ?? 1)}</strong></div>
      </div>`;
    };

    const mv = ln?.move ? `<span class="mv" title="Moved ${Math.abs(ln.move)} toward ${esc(ln.move < 0 ? g.home.name : g.away.name)} since open">${ln.move < 0 ? "▼" : "▲"}${Math.abs(ln.move)}</span>` : "";
    const market = g.market_rows ? `<dl class="market custom">${g.market_rows.map((m) => `<div><dt>${esc(m.label)}</dt><dd>${esc(m.value)}</dd></div>`).join("")}</dl>` : `<dl class="market">
      <div><dt>Open</dt><dd>${ln ? favLineHtml(g, ln.open) : "—"}</dd></div>
      <div><dt>Current${ln?.book ? " · " + esc(ln.book) : ""}</dt><dd>${ln ? favLineHtml(g, ln.spread) : "No line"}${mv}</dd></div>
      <div><dt>Proj. Line</dt><dd>${favLineHtml(g, g.model.spread)}</dd></div>
      <div><dt>O/U · Proj</dt><dd>${ln?.total ?? "—"} · ${num(g.model.total)}</dd></div>
    </dl>`;

    const res = (v, labels) => (v ? `<span class="res ${v}">${labels[v]}</span>` : "");
    const wp = g.model.home_wp >= 0.5 ? g.model.home_wp : 1 - g.model.home_wp;
    let sel = "";
    const confBadge = p.confidence != null ? `<span><span class="conf ${esc((p.conf_label || "").toLowerCase())}" title="Confidence ${p.confidence}/100">${esc(p.conf_label)} ${p.confidence}</span></span>` : "";
    if (g.pick_rows) {
      const lines = [[]];
      for (const row of g.pick_rows) {
        if (row.result_key === "total") lines.push([`<span class="ou-label">Total</span>`]);
        let html;
        if (row.tier) html = `<span><span class="tier ${row.tier}">${row.tier === "pass" ? "No value" : row.tier}</span> ${esc(row.text)}</span>`;
        else if (row.strong) html = `<strong>${esc(row.text)}${row.result_key ? res(r[row.result_key], { W: "Win", L: "Loss", P: "Push" }) : ""}</strong>`;
        else html = `<span class="${row.dim ? "dim" : ""}">${esc(row.text)}</span>`;
        lines[lines.length - 1].push(html);
      }
      if (confBadge) lines[0].push(confBadge);
      sel = lines.filter((l) => l.length).map((l) => `<div class="pick-line">${l.join("")}</div>`).join("");
      if (!g.pick_rows.length && p.su_team) sel = `<span class="dim">No line yet</span><strong>${esc(p.su_team)} ${Math.round(wp * 100)}%</strong>`;
    } else if (p.ats_team) {
      sel = `<div class="pick-line"><strong>${esc(teamLine(p.ats_team, p.ats_line))}${res(r.ats, { W: "Cover", L: "Miss", P: "Push" })}</strong>
        <span><span class="tier ${p.tier}">${p.tier === "pass" ? "No edge" : p.tier}</span> ${num(p.edge)}</span>
        ${confBadge}
        <span class="dim">SU ${esc(p.su_team)} ${Math.round(wp * 100)}%${res(r.su, { W: "W", L: "L" })}</span></div>`;
      const t = p.total_pick;
      if (t) sel += `<div class="pick-line"><span class="ou-label">Total</span><strong>${esc(ouText(t))}${res(r.total, { W: "Win", L: "Loss", P: "Push" })}</strong>
        <span><span class="tier ${t.tier}">${t.tier === "pass" ? "No edge" : t.tier}</span> ${num(t.edge)}</span>
        <span class="dim">proj ${num(t.model)}</span></div>`;
    } else if (p.su_team) {
      sel = `<span class="dim">No line</span><strong>SU ${esc(p.su_team)} ${Math.round(wp * 100)}%${res(r.su, { W: "W", L: "L" })}</strong>`;
    }

    const ts = state.meta?.trend_seasons || [];
    const notable = g.trends && [...(g.trends.home || []), ...(g.trends.away || [])].some((t) => t.notable);
    const trends = !g.trends ? "" : `<details class="section" ${notable ? "open" : ""}><summary>ATS trends ${ts.length ? ts[0] + "–" + ts[1] : ""}</summary>
      <div class="section-body trend-cols">
        <div><h4>${esc(g.away.name)}</h4>${(g.trends.away || []).map(trendRow).join("") || '<p class="small-note">No ATS history</p>'}</div>
        <div><h4>${esc(g.home.name)}</h4>${(g.trends.home || []).map(trendRow).join("") || '<p class="small-note">No ATS history</p>'}</div>
      </div></details>`;

    const confHtml = confidenceSection(g);

    return `<article class="game-card">
      <div class="game-meta">
        <div><span class="kickoff">${g.start_tbd ? "TBD" : esc(fmtTime(d))}</span>${(g.tv || []).length ? `<span class="network">${esc(g.tv.join(" / "))}</span>` : ""}${tags.join("")}</div>
        <p class="venue">${esc(g.venue || "")}</p>
      </div>
      <div class="teams">${teamRow("away")}${teamRow("home")}</div>
      ${(g.extras || []).length ? `<div class="extras">${g.extras.map((e) => `<span>${esc(e)}</span>`).join("")}</div>` : ""}
      ${market}
      <div class="pick-panel"><span class="pick-label">Model picks</span><div class="pick-sel">${sel}</div></div>
      ${propsSection(g)}
      ${confHtml}
      ${trends}
      ${state.meta?.sport && state.meta.sport !== "cfb" && !g.splits ? "" : splitsSection(g)}
    </article>`;
  }

  const SRC_NAMES = { sp: "SP+", fpi: "FPI", elo: "Elo" };
  const CONF_ROWS = [
    ["agreement", "Source agreement", "Share of SP+/FPI/Elo (by weight) that cover the line on their own"],
    ["consistency", "Source consistency", "How tightly the three sources' margins cluster"],
    ["edge", "Edge size", "Blended edge relative to the strong-edge threshold"],
    ["market", "Market", "Line moving toward the pick and sharp-money flags"],
    ["trends", "ATS trends", "Notable situational trends for the pick vs against it"],
    ["data", "Data quality", "Current-season SP+, FPI and Elo for both teams"],
  ];

  function confidenceSection(g) {
    const c = g.confidence;
    if (!c) return "";
    const w = state.meta?.settings?.conf_weights || {};
    const names = c.source_names || SRC_NAMES;
    const srcs = Object.entries(c.sources || {})
      .map(([k, m]) => c.units === "goals"
        ? `<span>${esc(names[k] || k)} ${esc(g.home.abbr || g.home.name)} ${m >= 0 ? "+" : "−"}${Math.abs(m).toFixed(2)}</span>`
        : `<span>${esc(names[k] || k)} ${esc(favLine(g, -m))}</span>`)
      .join("");
    const rows = CONF_ROWS.map(([k, label, tip]) => {
      const v = c.components[k] ?? 0;
      return `<div class="conf-row" title="${esc(tip)}"><span>${label}<em>${Math.round((w[k] ?? 0) * 100)}%</em></span>
        <div class="bar money"><span style="width:${Math.round(v * 100)}%"></span><em>${Math.round(v * 100)}</em></div></div>`;
    }).join("");
    return `<details class="section"><summary>Confidence <span class="conf ${esc(c.label.toLowerCase())}">${esc(c.label)} ${c.score}</span></summary>
      <div class="section-body">
        <p class="small-note">${c.agree}/${c.n_sources} ${c.units === "goals" ? "sources see value on this side · projected home margin (goals)" : "sources cover on their own · source lines"}: </p>
        <div class="src-lines">${srcs}</div>
        ${rows}
      </div></details>`;
  }

  const FILTERS = [
    { key: "all", label: "All games", test: () => true },
    { key: "top25", label: "Top 25", test: (g) => g.home.poll_rank || g.away.poll_rank },
    { key: "edges", label: "Edges", test: (g) => g.pick?.edge >= (state.meta?.settings?.lean_edge ?? 2) },
    { key: "highconf", label: "High confidence", test: (g) => g.pick?.conf_label === "High" },
    { key: "flags", label: "Split flags", test: (g) => g.splits?.flags?.length },
  ];

  function renderFilters() {
    const hasPoll = state.slate.some((g) => g.home.poll_rank || g.away.poll_rank);
    const hasSplits = state.slate.some((g) => g.splits);
    const shown = FILTERS.filter((f) => (f.key !== "top25" || hasPoll) && (f.key !== "flags" || hasSplits || state.meta?.sport === "cfb"));
    $("#game-filters").innerHTML = shown.map(
      (f) => `<button role="tab" data-f="${f.key}" class="${state.filter === f.key ? "active" : ""}">${f.label}<span>${state.slate.filter(f.test).length}</span></button>`
    ).join("");
    $("#game-filters").querySelectorAll("button").forEach((b) =>
      b.addEventListener("click", () => {
        state.filter = b.dataset.f;
        renderFilters();
        renderGames();
      })
    );
  }

  // Status at view time: data refreshes daily, so "started" comes from the
  // viewer's clock, while "final" needs a refresh with the score.
  const STATUS_ORDER = { live: 0, upcoming: 1, final: 2 };
  function status(g) {
    if (g.result?.completed) return "final";
    if (!g.start_tbd && new Date(g.start) <= Date.now()) return "live";
    return "upcoming";
  }

  const daySection = (title, gs) =>
    `<section><div class="day-heading"><h3>${esc(title)}</h3><span>${gs.length} game${gs.length === 1 ? "" : "s"}</span></div>
      <div class="game-grid">${gs.map(gameCard).join("")}</div></section>`;

  function renderGames() {
    const q = $("#game-search").value.trim().toLowerCase();
    const conf = $("#game-conf").value;
    const f = FILTERS.find((x) => x.key === state.filter);
    let games = state.slate.filter((g) => {
      if (q && !(g.home.name + " " + g.away.name).toLowerCase().includes(q)) return false;
      if (conf && g.home.conf !== conf && g.away.conf !== conf) return false;
      return f.test(g);
    });
    const sort = $("#game-sort").value;
    $("#games-showing").textContent = `Showing ${games.length} game${games.length === 1 ? "" : "s"}`;
    if (!games.length) {
      $("#games-list").innerHTML = `<div class="empty-state"><p class="muted">No games match these filters.</p></div>`;
      return;
    }
    const byStatus = (a, b) => STATUS_ORDER[status(a)] - STATUS_ORDER[status(b)];
    if (sort !== "time") {
      const key = sort === "edge" ? (g) => g.pick?.edge ?? -1 : sort === "conf" ? (g) => g.pick?.confidence ?? -1 : (g) => Math.abs(g.line?.move ?? 0);
      games.sort((a, b) => byStatus(a, b) || key(b) - key(a));
      $("#games-list").innerHTML = `<div class="game-grid">${games.map(gameCard).join("")}</div>`;
      return;
    }
    games.sort((a, b) => byStatus(a, b) || new Date(a.start) - new Date(b.start));
    const days = new Map();
    for (const g of games.filter((g) => status(g) === "upcoming")) {
      const k = fmtDay(new Date(g.start));
      if (!days.has(k)) days.set(k, []);
      days.get(k).push(g);
    }
    const live = games.filter((g) => status(g) === "live");
    const final = games.filter((g) => status(g) === "final");
    $("#games-list").innerHTML = [
      live.length ? daySection("In progress", live) : "",
      ...[...days].map(([day, gs]) => daySection(day, gs)),
      final.length ? daySection("Final", final) : "",
    ].join("");
  }

  // --- Ratings ---------------------------------------------------------------
  function renderRatings() {
    const q = $("#rating-search").value.trim().toLowerCase();
    const conf = $("#rating-conf").value;
    const rows = state.ratings.filter((r) => (!q || r.team.toLowerCase().includes(q)) && (!conf || r.conference === conf));
    const n = (k, d = 1) => ({ key: k, num: true, render: (r) => num(r[k], d) });
    const el = $("#ratings-table");
    el._rerender = renderRatings;
    const teamCell = (r) => `<span class="team-cell">${r.logo ? `<img src="${esc(r.logo)}" alt="" loading="lazy">` : ""}<strong>${esc(r.team)}</strong>${r.poll_rank ? ` <span class="rank">${r.poll_rank}</span>` : ""}</span>`;
    const cfg = state.meta?.rating_columns;
    if (cfg) {
      const cols = cfg.map((c) => c.key === "team"
        ? { key: "team", label: c.label, ascFirst: true, render: teamCell }
        : c.text ? { key: c.key, label: c.label, ascFirst: true, render: (r) => esc(r[c.key] ?? "—") }
        : { key: c.key, label: c.label, num: true, ascFirst: !!c.asc, render: (r) => num(r[c.key], c.digits ?? 1) });
      renderTable(el, cols, rows, el._sort?.key ? el._sort : { key: "rank", asc: true });
      return;
    }
    renderTable(
      el,
      [
        { key: "rank", label: "#", num: true, ascFirst: true },
        { key: "team", label: "Team", ascFirst: true, render: (r) => `<span class="team-cell">${r.logo ? `<img src="${esc(r.logo)}" alt="" loading="lazy">` : ""}<strong>${esc(r.team)}</strong>${r.poll_rank ? ` <span class="rank">${r.poll_rank}</span>` : ""}</span>` },
        { key: "conference", label: "Conf", ascFirst: true },
        { key: "record", label: "W–L", sortVal: (r) => +r.record.split("-")[0], render: (r) => esc(r.record.replace("-", "–")) },
        { ...n("rating", 1), label: "Power" },
        { ...n("sp", 1), label: "SP+" },
        { ...n("fpi", 1), label: "FPI" },
        { ...n("elo", 0), label: "Elo" },
        { ...n("off", 1), label: "SP+ Off" },
        { ...n("def", 1), label: "SP+ Def", ascFirst: true },
      ],
      rows,
      el._sort?.key ? el._sort : { key: "rank", asc: true }
    );
  }

  // --- Over/under -------------------------------------------------------------
  const ouRec = (picks, playOnly = false) => {
    const xs = picks.filter((p) => p.total_pick && p.result?.total && (!playOnly || p.total_pick.play)).map((p) => p.result.total);
    const W = xs.filter((x) => x === "W").length, L = xs.filter((x) => x === "L").length, P = xs.filter((x) => x === "P").length;
    return { W, L, P, pct: W + L ? W / (W + L) : null };
  };
  const ouText = (t) => (t ? `${t.side === "over" ? "Over" : "Under"} ${t.line}` : "—");
  const ouEdge = (t) => (t?.ev != null ? `EV ${t.ev >= 0 ? "+" : ""}${(t.ev * 100).toFixed(1)}%` : t ? `${num(t.edge)} pts` : "");

  // --- Closing line value ----------------------------------------------------
  const isML = () => state.meta?.sport === "nhl";
  const fmtOdds = (v) => (v == null ? "—" : (v > 0 ? "+" : "") + Math.round(v));
  const clvFmt = (v) => (v == null ? "—" : isML() ? `${v > 0 ? "+" : ""}${(v * 100).toFixed(1)}%` : `${v > 0 ? "+" : ""}${v.toFixed(1)}`);
  function clvStats(picks) {
    const xs = picks.filter((p) => p.clv != null && !p.backtest);
    if (!xs.length) return null;
    const beat = xs.filter((p) => p.clv > 0).length, same = xs.filter((p) => p.clv === 0).length;
    const probs = xs.filter((p) => p.clv_prob != null);
    return { n: xs.length, avg: xs.reduce((a, p) => a + p.clv, 0) / xs.length, beat, same, worse: xs.length - beat - same,
      prob: probs.length ? probs.reduce((a, p) => a + p.clv_prob, 0) / probs.length : null };
  }
  function clvTile(label, st) {
    if (!st) return `<div class="tile"><div class="k">${label}</div><div class="v">—</div><div class="s">no closed picks yet</div></div>`;
    const cls = st.avg > 0 ? "pos" : st.avg < 0 ? "neg" : "";
    const sub = `${st.n} picks: ${st.beat} beat · ${st.same} same · ${st.worse} worse than close` + (st.prob != null ? ` · cover prob ${st.prob >= 0 ? "+" : ""}${(st.prob * 100).toFixed(1)}%` : "");
    return `<div class="tile clv"><div class="k">${label}</div><div class="v ${cls}">${clvFmt(st.avg)}${isML() ? "" : " pts"}</div><div class="s">${sub}</div></div>`;
  }

  // --- Record ----------------------------------------------------------------
  function renderRecord() {
    const rec = state.record;
    if (!rec) return;
    const sr = rec.season_record;
    const s = state.meta?.settings || {};
    const tile = (k, r, ties = true, sub = "") => `<div class="tile"><div class="k">${k}</div><div class="v">${recStr(r, ties)}</div><div class="s">${pct(r?.pct)}${sub}</div></div>`;
    const A = T("ats", "ATS");
    if (rec.tiles) {
      $("#record-tiles").innerHTML = rec.tiles.map((t) => tile(esc(t.k), t.rec, true, t.sub ? " · " + esc(t.sub) : "")).join("");
    } else $("#record-tiles").innerHTML =
      tile("Straight up", sr.su, false) +
      tile("ATS · all", sr.ats) +
      tile(`ATS · edge ${s.lean_edge ?? 2}+`, sr.ats_lean) +
      tile(`ATS · edge ${s.play_edge ?? 4}+`, sr.ats_play) +
      tile(`ATS · edge ${s.strong_edge ?? 7}+`, sr.ats_strong) +
      tile("ATS · all-time", rec.all_time.ats, true, " · all seasons");
    const bc = sr.ats_by_conf;
    if (bc && !rec.tiles) {
      $("#record-tiles").innerHTML +=
        tile("ATS · High confidence", bc.High) + tile("ATS · Medium confidence", bc.Medium) + tile("ATS · Low confidence", bc.Low);
    }

    const seasonPicks = rec.picks || [];
    const playEdge = s.play_edge ?? 4;
    if (!rec.tiles) $("#record-tiles").innerHTML += tile("O/U · all", ouRec(seasonPicks)) + tile("O/U · plays", ouRec(seasonPicks, true));
    if (["nfl", "nhl"].includes(state.meta?.sport)) {
      const bets = seasonPicks.flatMap((p) => (p.props || []).filter((b) => b.bet && b.result));
      const W = bets.filter((b) => b.result === "W").length, L = bets.length - W;
      const units = bets.reduce((a, b) => a + (b.units || 0), 0);
      const label = state.meta.sport === "nhl" ? "Anytime goal · best bets" : "Anytime TD · best bets";
      $("#record-tiles").innerHTML += tile(label, { W, L, P: 0, pct: W + L ? W / (W + L) : null }, false,
        bets.length ? ` · ${units >= 0 ? "+" : ""}${units.toFixed(1)}u` : " · none graded yet");
    }
    $("#record-tiles").innerHTML +=
      clvTile(`CLV · all picks`, clvStats(seasonPicks)) +
      clvTile(`CLV · ${T("edge", "edge")} ${playEdge}+`, clvStats(seasonPicks.filter((p) => (p.edge ?? 0) >= playEdge)));
    const clvByWeek = new Map();
    for (const p of seasonPicks) {
      const k = `${p.season}|${p.season_type}|${p.week}`;
      if (!clvByWeek.has(k)) clvByWeek.set(k, []);
      clvByWeek.get(k).push(p);
    }

    const wk = $("#record-weeks");
    wk._rerender = renderRecord;
    renderTable(
      wk,
      [
        { key: "week", label: "Week", ascFirst: true, render: (r) => r.label ? esc(r.label) : (r.season_type === "regular" ? `Week ${r.week}` : "Postseason") + ` <span class="muted">${r.season}</span>`, sortVal: (r) => r.season * 100 + (r.season_type === "regular" ? 0 : 50) + r.week },
        { key: "su", label: "SU", num: true, render: (r) => recStr(r.su, false), sortVal: (r) => r.su.pct },
        { key: "ats", label: A, num: true, render: (r) => recStr(r.ats), sortVal: (r) => r.ats.pct },
        { key: "atspct", label: `${A} %`, num: true, render: (r) => pct(r.ats.pct), sortVal: (r) => r.ats.pct },
        { key: "plays", label: `${A} ${T("edge", "edge")} ${s.lean_edge ?? 4}+`, num: true, render: (r) => recStr(r.ats_plays), sortVal: (r) => r.ats_plays.pct },
        { key: "ou", label: "O/U", num: true, sortVal: (r) => ouRec(clvByWeek.get(`${r.season}|${r.season_type}|${r.week}`) || []).pct ?? -1,
          render: (r) => { const o = ouRec(clvByWeek.get(`${r.season}|${r.season_type}|${r.week}`) || []); return o.W + o.L + o.P ? recStr(o) : '<span class="muted">—</span>'; } },
        { key: "clv", label: "CLV", num: true, sortVal: (r) => clvStats(clvByWeek.get(`${r.season}|${r.season_type}|${r.week}`) || [])?.avg ?? -99,
          render: (r) => { const st = clvStats(clvByWeek.get(`${r.season}|${r.season_type}|${r.week}`) || []); return st ? `<span class="${st.avg > 0 ? "pos" : st.avg < 0 ? "neg" : ""}">${clvFmt(st.avg)}</span> <span class="muted">${st.beat}/${st.n}</span>` : '<span class="muted">—</span>'; } },
      ],
      [...sr.by_week].reverse(),
      wk._sort
    );
    renderLog();
  }

  function renderLog() {
    const rec = state.record;
    const q = $("#log-search").value.trim().toLowerCase();
    const f = $("#log-filter").value;
    const play = state.meta?.settings?.play_edge ?? 4;
    const rows = rec.picks.filter((p) => {
      if (q && !(p.home + " " + p.away).toLowerCase().includes(q)) return false;
      if (f === "plays" && !(p.edge >= play)) return false;
      if (f === "high" && p.conf_label !== "High") return false;
      if (f === "totals" && !p.total_pick?.play) return false;
      if (f === "graded" && !p.result) return false;
      if (f === "pending" && p.result) return false;
      return true;
    });
    const el = $("#record-log");
    el._rerender = renderLog;
    const res = (v) => (v ? `<span class="res ${v}">${v}</span>` : '<span class="muted">—</span>');
    renderTable(
      el,
      [
        { key: "start", label: "Date", render: (p) => new Date(p.start).toLocaleDateString(undefined, { month: "short", day: "numeric" }) + (p.backtest ? ' <span class="bt-tag" title="Backtest pick (simulated before live tracking began)">BT</span>' : "") },
        { key: "game", label: "Game", sortVal: (p) => p.away, render: (p) => `${esc(p.away)} ${p.neutral ? "vs" : "at"} ${esc(p.home)}` },
        { key: "line", label: "Line", num: true, render: (p) => esc(p.line_label ?? favLine({ home: { name: p.home }, away: { name: p.away } }, p.line)) },
        { key: "model_spread", label: "Model", num: true, render: (p) => esc(p.model_label ?? favLine({ home: { name: p.home }, away: { name: p.away } }, p.model_spread)) },
        { key: "ats_team", label: `${T("ats", "ATS")} pick`, render: (p) => (p.ats_team ? esc(p.pick_label ?? teamLine(p.ats_team, p.ats_line)) : "—") },
        { key: "edge", label: T("edge", "Edge"), num: true, render: (p) => num(p.edge) },
        { key: "confidence", label: "Conf", num: true, render: (p) => (p.confidence == null ? "—" : `<span class="conf ${esc((p.conf_label || "").toLowerCase())}">${p.confidence}</span>`) },
        { key: "score", label: "Final", num: true, sortVal: (p) => (p.result ? 1 : 0), render: (p) => (p.result ? `${p.result.away_pts}–${p.result.home_pts}` : "—") },
        { key: "ats", label: T("ats", "ATS"), sortVal: (p) => p.result?.ats, render: (p) => res(p.result?.ats) },
        { key: "su", label: "SU", sortVal: (p) => p.result?.su, render: (p) => `${esc(p.su_team)} ${res(p.result?.su)}` },
        { key: "ou", label: "O/U pick", sortVal: (p) => p.total_pick?.edge ?? -1,
          render: (p) => (p.total_pick ? `${esc(ouText(p.total_pick))} <span class="muted">${esc(ouEdge(p.total_pick))}</span> ${res(p.result?.total)}` : '<span class="muted">—</span>') },
        { key: "clv", label: "CLV", num: true, sortVal: (p) => p.clv ?? -99, render: (p) => {
          if (p.clv == null) return '<span class="muted">—</span>';
          const fmtN = (v) => (isML() ? fmtOdds(v) : (v > 0 ? "+" : "") + v);
          const side = p.ats_side === "home" ? 1 : -1;
          const n = (v) => (isML() ? fmtN(v) : fmtN(side * v));
          return `<span class="${p.clv > 0 ? "pos" : p.clv < 0 ? "neg" : ""}" title="Bet number ${esc(n(p.bet_line))}${p.bet_src ? " (" + esc(p.bet_src) + ")" : ""} → close ${esc(n(p.close_line))}">${clvFmt(p.clv)}</span>`;
        } },
      ],
      rows,
      el._sort
    );
  }

  // --- Backtest --------------------------------------------------------------
  const wkLabel = (r) => (r.season_type === "regular" ? `Week ${r.week}` : (state.meta?.sport === "nfl" ? { 19: "Wild Card", 20: "Divisional", 21: "Conference", 22: "Super Bowl" }[r.week] || `Post ${r.week}` : "Bowls"));
  const wkKey = (r) => `${r.season}|${r.season_type}|${r.week}`;
  const wFmt = (w) => Object.entries(w || {}).map(([k, v]) => `${k.toUpperCase()} ${Math.round(v * 100)}%`).join(" · ");

  function renderBacktest() {
    const bt = state.backtest;
    if (!bt) return;
    $("#backtest").hidden = false;
    $("#bt-method").textContent = bt.method || "";
    const s = state.meta?.settings || {};
    const A = T("ats", "ATS");
    const seasons = [...bt.seasons.map(String), "all"];
    const sumRows = seasons.filter((k) => bt.summary[k]).map((k) => ({ season: k, ...bt.summary[k] }));
    const el = $("#bt-seasons");
    renderTable(el, [
      { key: "season", label: "Season", render: (r) => (r.season === "all" ? "<strong>All</strong>" : esc(r.season)), sortVal: (r) => (r.season === "all" ? 9999 : +r.season) },
      { key: "ats", label: A, num: true, render: (r) => recStr(r.ats), sortVal: (r) => r.ats.pct },
      { key: "atspct", label: `${A} %`, num: true, render: (r) => pct(r.ats.pct), sortVal: (r) => r.ats.pct },
      { key: "lean", label: `Edge ${s.lean_edge ?? 2}+`, num: true, render: (r) => recStr(r.ats_lean), sortVal: (r) => r.ats_lean?.pct },
      { key: "play", label: `Edge ${s.play_edge ?? 4}+`, num: true, render: (r) => recStr(r.ats_play), sortVal: (r) => r.ats_play?.pct },
      { key: "strong", label: `Edge ${s.strong_edge ?? 7}+`, num: true, render: (r) => recStr(r.ats_strong), sortVal: (r) => r.ats_strong?.pct },
      { key: "high", label: "High conf", num: true, render: (r) => recStr(r.ats_by_conf?.High), sortVal: (r) => r.ats_by_conf?.High?.pct },
      { key: "su", label: "SU", num: true, render: (r) => recStr(r.su, false), sortVal: (r) => r.su.pct },
      ...(sumRows.some((r) => r.ou) ? [
        { key: "ou", label: "O/U", num: true, render: (r) => recStr(r.ou), sortVal: (r) => r.ou?.pct },
        { key: "ou_play", label: "O/U plays", num: true, render: (r) => recStr(r.ou_play), sortVal: (r) => r.ou_play?.pct }] : []),
    ], sumRows, el._sort);

    const sel = $("#bt-season");
    if (!sel.options.length) {
      sel.innerHTML = bt.seasons.map((x) => `<option value="${x}">${x}</option>`).join("");
      sel.value = String(bt.seasons[bt.seasons.length - 1]);
    }
    const season = +sel.value;
    const weeks = bt.weeks.filter((w) => w.season === season);
    const last = weeks[weeks.length - 1];
    const i0 = bt.weeks.indexOf(weeks[0]);
    const startW = i0 > 0 ? bt.weeks[i0 - 1].weights : bt.start.weights;
    $("#bt-tuning").textContent = last ? `Weights: start ${wFmt(startW)} → end ${wFmt(last.weights)} · home ${num(last.home_field, 2)}` : "";
    const wk = $("#bt-weeks");
    wk._rerender = renderBacktest;
    renderTable(wk, [
      { key: "week", label: "Week", render: wkLabel, sortVal: (r) => (r.season_type === "regular" ? 0 : 50) + r.week },
      { key: "n", label: "Games", num: true, render: (r) => r.n },
      { key: "ats", label: A, num: true, render: (r) => recStr(r.ats), sortVal: (r) => r.ats.pct },
      { key: "play", label: `Edge ${s.play_edge ?? 4}+`, num: true, render: (r) => recStr(r.ats_play), sortVal: (r) => r.ats_play?.pct },
      { key: "strong", label: `Edge ${s.strong_edge ?? 7}+`, num: true, render: (r) => recStr(r.ats_strong), sortVal: (r) => r.ats_strong?.pct },
      { key: "su", label: "SU", num: true, render: (r) => recStr(r.su, false), sortVal: (r) => r.su.pct },
      ...(weeks.some((w) => w.ou) ? [{ key: "ou", label: "O/U", num: true, render: (r) => recStr(r.ou), sortVal: (r) => r.ou?.pct }] : []),
      { key: "w", label: "Weights after tuning", render: (r) => `<span class="muted">${esc(wFmt(r.weights))} · HFA ${num(r.home_field, 2)}${r.tune === "insufficient" ? " (too few games yet)" : ""}</span>` },
    ], weeks, wk._sort);

    const wsel = $("#bt-week");
    if (wsel.dataset.season !== String(season)) {
      wsel.dataset.season = String(season);
      wsel.innerHTML = `<option value="">All weeks</option>` + weeks.map((w) => `<option value="${esc(wkKey(w))}">${esc(wkLabel(w))}</option>`).join("");
      if (weeks.length) wsel.value = wkKey(weeks[weeks.length - 1]);
    }
    renderBacktestPicks();
  }

  function renderBacktestPicks() {
    const bt = state.backtest;
    const s = state.meta?.settings || {};
    const season = +$("#bt-season").value;
    const wk = $("#bt-week").value;
    const f = $("#bt-filter").value;
    const q = $("#bt-search").value.trim().toLowerCase();
    const rows = bt.picks.filter((p) => p.season === season && (!wk || wkKey(p) === wk)
      && (f !== "plays" || p.edge >= (s.play_edge ?? 4)) && (f !== "strong" || p.edge >= (s.strong_edge ?? 7))
      && (f !== "high" || p.conf_label === "High") && (!q || (p.home + " " + p.away).toLowerCase().includes(q)));
    const res = (v) => (v ? `<span class="res ${v}">${v}</span>` : '<span class="muted">—</span>');
    const el = $("#bt-picks");
    el._rerender = renderBacktestPicks;
    const gm = (p) => ({ home: { name: p.home }, away: { name: p.away } });
    renderTable(el, [
      { key: "week", label: "Week", render: (p) => esc(wkLabel(p)), sortVal: (p) => (p.season_type === "regular" ? 0 : 50) + p.week },
      { key: "game", label: "Game", sortVal: (p) => p.away, render: (p) => `${esc(p.away)} at ${esc(p.home)}` },
      { key: "line", label: "Close", num: true, render: (p) => esc(favLine(gm(p), p.line)) },
      { key: "model_spread", label: "Model", num: true, render: (p) => esc(favLine(gm(p), p.model_spread)) },
      { key: "ats_team", label: `${T("ats", "ATS")} pick`, render: (p) => (p.ats_team ? esc(teamLine(p.ats_team, p.ats_line)) : "—") },
      { key: "edge", label: T("edge", "Edge"), num: true, render: (p) => num(p.edge) },
      { key: "confidence", label: "Conf", num: true, render: (p) => (p.confidence == null ? "—" : `<span class="conf ${esc((p.conf_label || "").toLowerCase())}">${p.confidence}</span>`) },
      { key: "score", label: "Final", num: true, sortVal: (p) => p.result.home_pts - p.result.away_pts, render: (p) => `${p.result.away_pts}–${p.result.home_pts}` },
      { key: "ats", label: T("ats", "ATS"), sortVal: (p) => p.result.ats, render: (p) => res(p.result.ats) },
      { key: "su", label: "SU", sortVal: (p) => p.result.su, render: (p) => `${esc(p.su_team)} ${res(p.result.su)}` },
      ...(rows.some((p) => p.total_pick) ? [{ key: "ou", label: "O/U pick", sortVal: (p) => p.total_pick?.edge ?? -1,
        render: (p) => (p.total_pick ? `${esc(ouText(p.total_pick))} <span class="muted">${esc(ouEdge(p.total_pick))}</span> ${res(p.result.total)}` : "—") }] : []),
    ], rows, el._sort);
  }

  // --- Trends ----------------------------------------------------------------
  const TREND_COLS = ["overall", "fav", "dog", "home", "away", "neutral", "home_fav", "home_dog", "away_fav", "away_dog", "conf", "off_bye", "last10"];
  function renderTrends() {
    const t = state.trends;
    if (!t) return;
    const q = $("#trend-search").value.trim().toLowerCase();
    const minN = +$("#trend-min").value || 0;
    const rows = Object.entries(t.teams)
      .filter(([team]) => !q || team.toLowerCase().includes(q))
      .map(([team, cats]) => ({ team, ...cats }));
    const cell = (k) => ({
      key: k,
      label: t.labels[k],
      num: true,
      sortVal: (r) => (r[k] && r[k].w + r[k].l >= minN ? r[k].pct : null),
      render: (r) => {
        const s = r[k];
        if (!s || s.w + s.l === 0) return '<span class="muted">—</span>';
        const n = s.w + s.l;
        const cls = n >= minN && s.pct >= 0.6 ? "pos" : n >= minN && s.pct <= 0.4 ? "neg" : "";
        return `<span class="${cls}">${s.w}–${s.l}${s.p ? "–" + s.p : ""}</span> <span class="muted">${Math.round(s.pct * 100)}%</span>`;
      },
    });
    const el = $("#trends-table");
    el._rerender = renderTrends;
    renderTable(
      el,
      [{ key: "team", label: "Team", ascFirst: true, render: (r) => `<strong>${esc(r.team)}</strong>` }, ...TREND_COLS.map(cell)],
      rows,
      el._sort?.key ? el._sort : { key: $("#trend-sort").value || "overall", asc: false }
    );
    const ts = state.meta?.trend_seasons || [];
    $("#trends-meta").textContent = `Records against the consensus closing line, ${ts[0]}–${ts[1]}. Off a bye = ${state.meta?.settings?.bye_days ?? 13}+ days since the previous game. Highlighted at 60%+ or 40%− with at least the minimum games.`;
  }

  // --- Method ----------------------------------------------------------------
  function renderMethod() {
    const m = state.meta;
    if (m.method_html) {
      $("#method-body").innerHTML = m.method_html + `<h3>Weekly tuning</h3>${tuningHtml(m.tuning)}`;
      return;
    }
    const s = m.settings;
    const w = s.weights;
    const pc = (v) => Math.round(v * 100);
    const splitsLine =
      m.splits.provider === "none"
        ? "No bet-splits feed is configured. The display and divergence logic are built; set <code>SPLITS_PROVIDER</code> to <code>file</code> or <code>http_json</code> to plug in a licensed source."
        : `Feed: <code>${esc(m.splits.provider)}</code>${m.splits.source ? " (" + esc(m.splits.source) + ")" : ""}.`;
    $("#method-body").innerHTML = `
      <h3>Power ratings</h3>
      <p>SP+, ESPN FPI and Elo come from <a href="https://collegefootballdata.com">CollegeFootballData</a>. FPI and Elo are rescaled onto the SP+ points scale (matching the FBS mean and spread), then blended ${pc(w.sp)}/${pc(w.fpi)}/${pc(w.elo)}. Teams without a current rating use last season's blend, regressed ${pc(1 - s.prior_regression)}% toward average.</p>
      <h3>Projections</h3>
      <p>Projected margin = home rating − away rating + ${s.home_field} points of home field (none at neutral sites). The total comes from SP+ offense and defense; the projected score splits it by the margin. Win probability assumes a normal margin distribution (σ = ${s.margin_sd}).</p>
      <h3>Picks</h3>
      <p>Every game with a line gets an against-the-spread side. The edge is the model's expected cover margin against the current line. Tiers: lean ${s.lean_edge}+, play ${s.play_edge}+, strong ${s.strong_edge}+. Picks re-price with each refresh until kickoff, lock at the last pre-kickoff line, and are graded straight up and ATS.</p>
      <h3>Confidence</h3>
      <p>A 0–100 score, separate from the edge, for how much to trust the side. It weighs ${confMix(s)}. A small edge can still be <strong>High</strong> (${s.conf_high}+) when SP+, FPI and Elo each cover on their own, the line is moving the pick's way and trends agree; <strong>Medium</strong> is ${s.conf_medium}–${s.conf_high - 1}. Game-day briefings include High-confidence sides even below the edge threshold. The Record tab tracks ATS results by confidence level so the score can be checked.</p>
      <h3>Weekly tuning</h3>
      ${tuningHtml(m.tuning)}
      <h3>ATS trends</h3>
      <p>Lines and results from ${m.trend_seasons[0]}–${m.trend_seasons[1]} (median closing spread across books): overall, favorite/underdog, home/away/neutral and combinations, conference play, off a bye (${s.bye_days}+ days) and last 10. Cards show the splits that apply to that game; splits with ${s.trend_min_games}+ decisions at 60%+ or 40%− are highlighted.</p>
      <h3>Bet splits</h3>
      <p>Bet % is the share of tickets; money % is the share of handle. <strong>Sharp $</strong>: money % exceeds bet % by ${s.sharp_gap}+ points on a side. <strong>Public</strong>: ${s.public_bets}%+ of tickets. <strong>Reverse line move</strong>: the line moved 1+ point away from the side holding most tickets. ${splitsLine}</p>
      <p>For entertainment and research only. Not betting advice.</p>`;
  }

  function confMix(s) {
    const names = { agreement: "source agreement", consistency: "source consistency", edge: "edge size", market: "market movement and sharp flags", trends: "ATS trends", data: "data quality" };
    return Object.entries(s.conf_weights || {}).map(([k, v]) => `${names[k]} ${Math.round(v * 100)}%`).join(", ");
  }

  function tuningHtml(t) {
    const SN = { sp: "SP+", fpi: "FPI", elo: "Elo", epa: "EPA", xg: "xG", goals: "Goals" };
    const pct = (w) => (w ? Object.entries(w).map(([k, v]) => `${SN[k] || k} ${Math.round(v * 100)}`).join(" / ") : "—");
    let html = `<p>Every pick stores what each rating source projected. After weekly grading, the model refits the source weights and home advantage that best predict this season's actual margins, then moves part of the way there (more as games accumulate) so one noisy week can't swing it. Tuning starts after ${t?.min_games ?? 25} graded games.${t && t.enabled === false ? " <strong>Tuning is currently turned off.</strong>" : ""}</p>`;
    if (!t || !t.history || !t.history.length) return html + `<p>Status: not tuned yet — running on default weights.</p>`;
    const last = t.last || {};
    if (last.by_source) {
      html += `<p>Last fit on ${last.games} games: ${Object.entries(last.by_source).map(([k, v]) => `${SN[k] || k} alone ${v.rmse} RMSE${v.ats != null ? ", " + Math.round(v.ats * 100) + "% ATS" : ""}`).join(" · ")}.</p>`;
    }
    const rows = [...t.history].reverse().map((h) => `<tr><td>${esc(new Date(h.at).toLocaleDateString(undefined, { month: "short", day: "numeric" }))}</td><td class="n">${h.games}</td><td>${h.status === "updated" ? "Updated" : "Waiting for games"}</td><td class="n">${pct(h.weights)}</td><td class="n">${h.home_field}</td><td class="n">${h.rmse_before ?? "—"} → ${h.rmse_after ?? "—"}</td></tr>`).join("");
    return html + `<div class="table-wrap weights-table"><table class="data"><thead><tr><th>Date</th><th class="n">Games</th><th>Status</th><th class="n">Weights %</th><th class="n">Home adv.</th><th class="n">RMSE</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function fillConfSelect(sel, confs) {
    sel.insertAdjacentHTML("beforeend", [...confs].filter(Boolean).sort().map((c) => `<option>${esc(c)}</option>`).join(""));
  }

  function renderHero() {
    const m = state.meta;
    const regular = m.season_type === "regular";
    $("#eyebrow").textContent = m.sport_label || `${m.season} college football`;
    $("#hero-title").innerHTML = m.hero_title || (regular ? `Week <span>${esc(m.week)}</span> <br>Projections.` : `Bowl <span>${esc(m.season)}</span> <br>Projections.`);
    if (m.subtitle) $("#hero-intro").textContent = m.subtitle + ".";
    if (m.board_note) $("#board-note").textContent = m.board_note;
    if (m.data_source) $("#data-source").innerHTML = m.data_source;
    document.title = `ASPicks Model · ${(m.sport || "cfb").toUpperCase()}`;
    document.querySelectorAll(".sports a").forEach((a) => a.setAttribute("aria-current", a.dataset.sport === (m.sport || "cfb") ? "page" : "false"));
    $("#games-title").textContent = `${m.slate_label} games`;
    const starts = state.slate.map((g) => new Date(g.start)).sort((a, b) => a - b);
    const md = (d) => d.toLocaleDateString(undefined, { month: "long", day: "numeric" });
    const range = starts.length ? (md(starts[0]) === md(starts.at(-1)) ? md(starts[0]) : `${md(starts[0])} – ${md(starts.at(-1))}`) : "";
    const updated = new Date(m.generated_at).toLocaleString(undefined, { weekday: "long", month: "long", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" });
    $("#hero-meta").innerHTML = `${range ? `<span>${esc(range)}</span>` : ""}<span>Updated ${esc(updated)}</span>${m.poll ? `<span>Ranks: ${esc(m.poll === "Playoff Committee Rankings" ? "CFP Rankings" : m.poll)}</span>` : ""}`;
    $("#updated").textContent = `Updated ${new Date(m.generated_at).toLocaleString()}`;

    const sr = state.record?.season_record;
    if (state.record?.strip && sr && (sr.su.W + sr.su.L > 0)) {
      $("#record-strip").hidden = false;
      $("#record-label").textContent = `${m.sport_label || m.season} model record`;
      $("#record-dl").innerHTML = state.record.strip.map((c) => `<div><dt>${esc(c.k)}</dt><dd>${recStr(c.rec)}<small>${pct(c.rec.pct)} · ${esc(c.sub || "")}</small></dd></div>`).join("");
    } else if (sr && (sr.su.W + sr.su.L > 0)) {
      $("#record-strip").hidden = false;
      $("#record-label").textContent = `${m.season} model record`;
      const cell = (k, r, ties) => `<div><dt>${k}</dt><dd>${recStr(r, ties)}<small>${pct(r.pct)} win rate</small></dd></div>`;
      $("#record-dl").innerHTML =
        cell("Straight up", sr.su, false) + cell("Against the spread", sr.ats, true) + cell(`ATS · edge ${m.settings.play_edge}+`, sr.ats_play, true);
    }
  }

  async function init() {
    const [meta, slate, ratings, record, trends, backtest] = await Promise.all(["meta", "slate", "ratings", "record", "trends", "backtest"].map(getJSON));
    if (!meta) {
      $("#empty").hidden = false;
      document.querySelectorAll(".tab-panel").forEach((p) => (p.hidden = true));
      return;
    }
    Object.assign(state, { meta, slate: slate?.games || [], ratings: ratings?.teams || [], record, trends, backtest });

    renderHero();
    if (meta.terms?.start) $("#sort-time").textContent = `By ${meta.terms.start}`;
    if (meta.terms?.record_note) $("#record-note").textContent = meta.terms.record_note;
    fillConfSelect($("#game-conf"), new Set(state.slate.flatMap((g) => [g.home.conf, g.away.conf])));
    fillConfSelect($("#rating-conf"), new Set(state.ratings.map((r) => r.conference)));
    if (trends) $("#trend-sort").innerHTML = TREND_COLS.map((k) => `<option value="${k}">Sort: ${esc(trends.labels[k])}</option>`).join("");

    ["#game-search", "#game-conf", "#game-sort"].forEach((s) => $(s).addEventListener("input", renderGames));
    ["#rating-search", "#rating-conf"].forEach((s) => $(s).addEventListener("input", renderRatings));
    ["#log-search", "#log-filter"].forEach((s) => $(s).addEventListener("input", renderLog));
    ["#trend-search", "#trend-min"].forEach((s) => $(s).addEventListener("input", renderTrends));
    $("#bt-season").addEventListener("input", renderBacktest);
    ["#bt-week", "#bt-filter", "#bt-search"].forEach((s) => $(s).addEventListener("input", renderBacktestPicks));
    $("#trend-sort").addEventListener("input", () => {
      $("#trends-table")._sort = { key: $("#trend-sort").value, asc: false };
      renderTrends();
    });

    if (!trends) {
      document.querySelectorAll('.nav a[data-tab="trends"]').forEach((a) => (a.hidden = true));
    }
    renderFilters();
    renderGames();
    renderRatings();
    if (record) renderRecord();
    if (backtest) renderBacktest();
    renderTrends();
    renderMethod();
    const initial = location.hash.slice(1);
    if (initial && document.getElementById(`tab-${initial}`)) showTab(initial);
  }

  init();
})();
