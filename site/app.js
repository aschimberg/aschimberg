(() => {
  "use strict";

  const state = { meta: null, slate: [], ratings: [], record: null, trends: null };
  const $ = (sel) => document.querySelector(sel);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtSpread = (s) => (s == null ? "—" : s === 0 ? "PK" : (s > 0 ? "+" : "") + s);
  const fmtNum = (v, d = 1) => (v == null ? "—" : Number(v).toFixed(d));
  const pct = (v) => (v == null ? "—" : (v * 100).toFixed(1) + "%");
  const recStr = (r, ties = true) => (r ? `${r.W}-${r.L}${ties && r.P ? "-" + r.P : ""}` : "—");
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  // Home-perspective spread -> "Favorite -X" (or "PK").
  const favLine = (g, s) => (s == null ? "—" : s === 0 ? "PK" : s < 0 ? `${g.home.name} ${s}` : `${g.away.name} ${-s}`);

  async function getJSON(name) {
    try {
      const res = await fetch(`data/${name}.json`, { cache: "no-cache" });
      if (!res.ok) return null;
      return await res.json();
    } catch {
      return null;
    }
  }

  function kickoff(g) {
    const d = new Date(g.start);
    const day = d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
    if (g.start_tbd) return `${day} · TBD`;
    return `${day} · ${d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}`;
  }

  // --- Tabs ------------------------------------------------------------------
  function showTab(name) {
    document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === name));
    document.querySelectorAll(".tab-panel").forEach((p) => (p.hidden = p.id !== `tab-${name}`));
    try { history.replaceState(null, "", `#${name}`); } catch { /* ignore */ }
  }
  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

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
        const next = { key, asc: el._sort.key === key ? !el._sort.asc : !!cols.find((c) => c.key === key).ascFirst };
        el._sort = next;
        el._rerender();
      })
    );
  }

  // --- Games -----------------------------------------------------------------
  function trendRow(t) {
    const cls = t.notable ? (t.pct >= 0.5 ? "hot" : "cold") : "";
    const rec = `${t.w}-${t.l}${t.p ? "-" + t.p : ""}`;
    return `<div class="trend ${cls}"><span>${esc(t.label)}</span><span class="r">${rec} · ${t.pct == null ? "—" : Math.round(t.pct * 100) + "%"}</span></div>`;
  }

  function sideName(side, g) {
    return { home: g.home.name, away: g.away.name, over: "Over", under: "Under" }[side];
  }

  function flagText(f, g) {
    const name = sideName(f.side, g);
    const mkt = { spread: "ATS", moneyline: "ML", total: "total" }[f.market];
    const nums = f.money != null ? `${f.bets}% bets / ${f.money}% $` : `${f.bets}% bets`;
    if (f.type === "sharp") return `Sharp $ on ${name} ${mkt} (${nums})`;
    if (f.type === "public") return `Public on ${name} ${mkt} (${nums})`;
    return `Reverse line move toward ${name} (${Math.abs(f.move)} pts vs ${100 - f.bets}% of bets)`;
  }

  function splitBlock(market, label, keys, g) {
    const m = g.splits.markets[market];
    if (!m) return "";
    const rows = keys
      .map((k) => {
        const s = m[k];
        const bar = (cls, v) => (v == null ? "" : `<div class="bar ${cls}"><span style="width:${v}%"></span><em>${v}%</em></div>`);
        return `<div class="split-row"><span class="who">${esc(sideName(k, g))}</span><div class="bars">${bar("bets", s.bets)}${bar("money", s.money)}</div></div>`;
      })
      .join("");
    return `<div class="split"><div class="split-title">${label}</div>${rows}</div>`;
  }

  function splitsSection(g) {
    const provider = state.meta?.splits?.provider;
    if (!g.splits) {
      const msg = !provider || provider === "none" ? "Bet-splits feed not configured (see Method)." : "No splits for this game yet.";
      return `<details class="section"><summary>Bet splits</summary><div class="section-body"><p class="muted">${msg}</p></div></details>`;
    }
    const flags = g.splits.flags || [];
    const sharp = flags.find((f) => f.type === "sharp" || f.type === "rlm");
    const mkt = (f) => ({ spread: "ATS", moneyline: "ML", total: "total" }[f.market]);
    const hint = sharp ? `<span class="hint">⚡ ${sharp.type === "rlm" ? "RLM" : "Sharp"}: ${esc(sideName(sharp.side, g))} ${mkt(sharp)}</span>` : "";
    const legend = `<div class="legend"><span><i style="background:var(--bar-bets)"></i>Bets %</span><span><i style="background:var(--bar-money)"></i>Money %</span></div>`;
    return `<details class="section" ${sharp ? "open" : ""}><summary>Bet splits ${hint}</summary><div class="section-body">${legend}
      ${splitBlock("spread", "Spread", ["away", "home"], g)}
      ${splitBlock("moneyline", "Moneyline", ["away", "home"], g)}
      ${splitBlock("total", "Total", ["over", "under"], g)}
      ${flags.length ? `<div class="flags">${flags.map((f) => `<span class="flag ${f.type}">${esc(flagText(f, g))}</span>`).join("")}</div>` : `<p class="muted">No sharp/public divergence.</p>`}
      ${g.splits.updated ? `<p class="muted">Updated ${esc(new Date(g.splits.updated).toLocaleString())}</p>` : ""}
    </div></details>`;
  }

  function gameCard(g) {
    const p = g.pick || {};
    const r = g.result || {};
    const final = r.completed;
    const ln = g.line;
    const tier = p.tier || (ln ? "pass" : null);
    const badge = final
      ? `<span class="badge final">Final</span>`
      : tier
      ? `<span class="badge ${tier}">${tier === "pass" ? "No edge" : tier}</span>`
      : `<span class="badge pass">No line</span>`;
    const meta = [kickoff(g), (g.tv || []).join("/"), g.neutral ? "Neutral" : null, g.conference_game ? "Conf" : null].filter(Boolean).join(" · ");

    const teamRow = (side) => {
      const t = g[side];
      const score = final ? r[`${side}_pts`] : g.model[`${side}_pts`];
      const won = final && r[`${side}_pts`] > r[`${side === "home" ? "away" : "home"}_pts`];
      return `<div class="team-row ${won ? "winner" : ""}">
        <span class="team-name">${t.rank ? `<span class="rk">${t.rank}</span>` : ""}${esc(t.name)}<span class="rec">${esc(t.record)}</span></span>
        <span class="team-rating mono">${t.rating == null ? "" : fmtNum(t.rating)}</span>
        <span class="team-score ${final ? "" : "proj"}">${score == null ? "—" : final ? score : Math.round(score)}</span>
      </div>`;
    };

    let lineCell = `<div><div class="k">Line</div><div class="v">—</div><div class="s">No line posted</div></div>`;
    if (ln) {
      let mv = "";
      if (ln.move) {
        const toward = ln.move < 0 ? g.home.name : g.away.name;
        mv = ` <span class="${ln.move < 0 ? "move-up" : "move-down"}" title="Moved ${Math.abs(ln.move)} toward ${esc(toward)} since open">${ln.move < 0 ? "▼" : "▲"}${Math.abs(ln.move)}</span>`;
      }
      lineCell = `<div><div class="k">Line · ${esc(ln.book)}</div><div class="v">${esc(favLine(g, ln.spread))}${mv}</div>
        <div class="s">Open ${esc(favLine(g, ln.open))} · O/U ${ln.total ?? "—"}</div></div>`;
    }
    const wp = g.model.home_wp >= 0.5 ? [g.home.name, g.model.home_wp] : [g.away.name, 1 - g.model.home_wp];
    const modelCell = `<div><div class="k">Model</div><div class="v">${esc(favLine(g, g.model.spread))}</div>
      <div class="s">Total ${fmtNum(g.model.total)} · ${esc(wp[0])} ${Math.round(wp[1] * 100)}%</div></div>`;

    let pickHtml = "";
    if (p.ats_team) {
      const atsRes = r.ats ? `<span class="res ${r.ats}">${r.ats === "W" ? "Cover" : r.ats === "L" ? "Miss" : "Push"}</span>` : "";
      const suRes = r.su ? `<span class="res ${r.su}">${r.su}</span>` : "";
      pickHtml = `<div class="pick"><div><div class="label">ATS pick</div><div class="val">${esc(p.ats_team)} ${fmtSpread(p.ats_line)}${atsRes}</div></div>
        <div style="text-align:right"><div class="label">Edge · SU</div><div><span class="edge">${fmtNum(p.edge)}</span> · ${esc(p.su_team)}${suRes}</div></div></div>`;
    } else if (p.su_team) {
      pickHtml = `<div class="pick"><div><div class="label">SU pick</div><div class="val">${esc(p.su_team)} ${r.su ? `<span class="res ${r.su}">${r.su}</span>` : ""}</div></div></div>`;
    }

    const ts = state.meta?.trend_seasons || [];
    const anyNotable = [...(g.trends.home || []), ...(g.trends.away || [])].some((t) => t.notable);
    const trendsHtml = `<details class="section" ${anyNotable ? "open" : ""}><summary>ATS trends ${ts.length ? ts[0] + "–" + ts[1] : ""}</summary>
      <div class="section-body trend-cols">
        <div><h4>${esc(g.away.name)}</h4>${(g.trends.away || []).map(trendRow).join("") || '<p class="muted">No ATS history</p>'}</div>
        <div><h4>${esc(g.home.name)}</h4>${(g.trends.home || []).map(trendRow).join("") || '<p class="muted">No ATS history</p>'}</div>
      </div></details>`;

    return `<article class="card tier-${final ? "final" : tier}">
      <div class="card-head"><span>${esc(meta)}</span>${badge}</div>
      <div class="teams">${teamRow("away")}${teamRow("home")}</div>
      <div class="lines">${lineCell}${modelCell}</div>
      ${pickHtml}
      ${trendsHtml}
      ${splitsSection(g)}
    </article>`;
  }

  function renderGames() {
    const q = $("#game-search").value.trim().toLowerCase();
    const conf = $("#game-conf").value;
    const onlyPicks = $("#game-picks").checked;
    const onlyFlags = $("#game-flags").checked;
    const lean = state.meta?.settings?.lean_edge ?? 2;
    let games = state.slate.filter((g) => {
      if (q && !(g.home.name + " " + g.away.name).toLowerCase().includes(q)) return false;
      if (conf && g.home.conf !== conf && g.away.conf !== conf) return false;
      if (onlyPicks && !(g.pick?.edge >= lean)) return false;
      if (onlyFlags && !(g.splits?.flags?.length)) return false;
      return true;
    });
    const sort = $("#game-sort").value;
    if (sort === "edge") games.sort((a, b) => (b.pick?.edge ?? -1) - (a.pick?.edge ?? -1));
    else if (sort === "move") games.sort((a, b) => Math.abs(b.line?.move ?? 0) - Math.abs(a.line?.move ?? 0));
    $("#games").innerHTML = games.length ? games.map(gameCard).join("") : '<p class="muted">No games match.</p>';
    const withEdge = state.slate.filter((g) => g.pick?.edge >= lean).length;
    $("#games-meta").textContent = `${state.meta?.slate_label || ""} · ${state.slate.length} games · ${withEdge} with edge ≥ ${lean} · times in ${tz}`;
  }

  // --- Ratings ---------------------------------------------------------------
  function renderRatings() {
    const q = $("#rating-search").value.trim().toLowerCase();
    const conf = $("#rating-conf").value;
    const rows = state.ratings.filter((r) => (!q || r.team.toLowerCase().includes(q)) && (!conf || r.conference === conf));
    const n = (k, d = 1) => ({ key: k, num: true, render: (r) => fmtNum(r[k], d) });
    const el = $("#ratings");
    el._rerender = renderRatings;
    renderTable(
      el,
      [
        { key: "rank", label: "#", num: true, ascFirst: true },
        { key: "team", label: "Team", ascFirst: true, render: (r) => `<strong>${esc(r.team)}</strong>` },
        { key: "conference", label: "Conf", ascFirst: true },
        { key: "record", label: "W-L", sortVal: (r) => +r.record.split("-")[0] },
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

  // --- Record ----------------------------------------------------------------
  function renderRecord() {
    const rec = state.record;
    if (!rec) return;
    const sr = rec.season_record;
    const s = state.meta?.settings || {};
    const tile = (k, r, sub, ties = true) => `<div class="tile"><div class="k">${k}</div><div class="v">${recStr(r, ties)}</div><div class="s">${pct(r?.pct)}${sub ? " · " + sub : ""}</div></div>`;
    $("#record-tiles").innerHTML =
      tile("Straight up", sr.su, "", false) +
      tile("ATS · all games", sr.ats) +
      tile(`ATS · edge ≥ ${s.lean_edge ?? 2}`, sr.ats_lean) +
      tile(`ATS · edge ≥ ${s.play_edge ?? 4}`, sr.ats_play) +
      tile(`ATS · edge ≥ ${s.strong_edge ?? 7}`, sr.ats_strong) +
      tile("ATS · all-time", rec.all_time.ats, "all seasons");

    const wk = $("#record-weeks");
    wk._rerender = renderRecord;
    renderTable(
      wk,
      [
        { key: "week", label: "Week", ascFirst: true, render: (r) => (r.season_type === "regular" ? `Wk ${r.week}` : "Post") + ` <span class="muted">${r.season}</span>`, sortVal: (r) => r.season * 100 + (r.season_type === "regular" ? 0 : 50) + r.week },
        { key: "su", label: "SU", num: true, render: (r) => recStr(r.su, false), sortVal: (r) => r.su.pct },
        { key: "ats", label: "ATS", num: true, render: (r) => recStr(r.ats), sortVal: (r) => r.ats.pct },
        { key: "atspct", label: "ATS %", num: true, render: (r) => pct(r.ats.pct), sortVal: (r) => r.ats.pct },
        { key: "plays", label: `ATS edge ≥ ${s.play_edge ?? 4}`, num: true, render: (r) => recStr(r.ats_plays), sortVal: (r) => r.ats_plays.pct },
      ],
      [...rec.season_record.by_week].reverse(),
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
        { key: "start", label: "Date", render: (p) => new Date(p.start).toLocaleDateString(undefined, { month: "short", day: "numeric" }) },
        { key: "game", label: "Game", sortVal: (p) => p.away, render: (p) => `${esc(p.away)} ${p.neutral ? "vs" : "@"} ${esc(p.home)}` },
        { key: "line", label: "Line (home)", num: true, render: (p) => fmtSpread(p.line) },
        { key: "model_spread", label: "Model", num: true, render: (p) => fmtSpread(p.model_spread) },
        { key: "ats_team", label: "ATS pick", render: (p) => (p.ats_team ? `${esc(p.ats_team)} ${fmtSpread(p.ats_line)}` : "—") },
        { key: "edge", label: "Edge", num: true, render: (p) => fmtNum(p.edge) },
        { key: "score", label: "Final", sortVal: (p) => (p.result ? 1 : 0), render: (p) => (p.result ? `${p.result.away_pts}-${p.result.home_pts}` : "—") },
        { key: "ats", label: "ATS", sortVal: (p) => p.result?.ats, render: (p) => res(p.result?.ats) },
        { key: "su", label: "SU", sortVal: (p) => p.result?.su, render: (p) => `${esc(p.su_team)} ${res(p.result?.su)}` },
      ],
      rows,
      el._sort
    );
  }

  // --- Trends ----------------------------------------------------------------
  const TREND_COLS = ["overall", "fav", "dog", "home", "away", "neutral", "home_fav", "home_dog", "away_fav", "away_dog", "conf", "off_bye", "last10"];
  function renderTrends() {
    const t = state.trends;
    if (!t) return;
    const q = $("#trend-search").value.trim().toLowerCase();
    const minN = +$("#trend-min").value || 0;
    const sortKey = $("#trend-sort").value || "overall";
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
        return `<span class="${cls}">${s.w}-${s.l}${s.p ? "-" + s.p : ""}</span> <span class="muted">${Math.round(s.pct * 100)}%</span>`;
      },
    });
    const el = $("#trends");
    el._rerender = renderTrends;
    renderTable(
      el,
      [{ key: "team", label: "Team", ascFirst: true, render: (r) => `<strong>${esc(r.team)}</strong>` }, ...TREND_COLS.map(cell)],
      rows,
      el._sort?.key ? el._sort : { key: sortKey, asc: false }
    );
    const ts = state.meta?.trend_seasons || [];
    $("#trends-meta").textContent = `ATS records vs closing consensus line, ${ts[0]}–${ts[1]}. "Off a bye" = ${state.meta?.settings?.bye_days ?? 13}+ days since previous game. Colored when ≥ 60% / ≤ 40% with at least the minimum games.`;
  }

  // --- Method ----------------------------------------------------------------
  function renderMethod() {
    const m = state.meta;
    if (!m) return;
    const s = m.settings;
    const w = s.weights;
    const splitsLine =
      m.splits.provider === "none"
        ? "No bet-splits feed is configured. The UI and divergence logic are built; set <code>SPLITS_PROVIDER</code> to <code>file</code> or <code>http_json</code> to plug in a licensed source."
        : `Feed: <code>${esc(m.splits.provider)}</code>${m.splits.source ? " (" + esc(m.splits.source) + ")" : ""}.`;
    $("#method").innerHTML = `
      <h3>Power ratings</h3>
      <p>Each team's SP+, ESPN FPI and CFBD Elo are pulled from CollegeFootballData. FPI and Elo are rescaled onto the SP+ points scale (matching the FBS mean and spread), then blended: SP+ ${Math.round(w.sp * 100)}%, FPI ${Math.round(w.fpi * 100)}%, Elo ${Math.round(w.elo * 100)}%. If a team has no current-season rating yet, last season's blend is used, regressed ${Math.round((1 - s.prior_regression) * 100)}% toward average.</p>
      <h3>Projections</h3>
      <p>Projected margin = home rating − away rating + ${s.home_field} pts home field (0 at neutral sites). The total comes from SP+ offense/defense ratings; the projected score splits that total by the margin. Win probability assumes a normal margin distribution (σ = ${s.margin_sd}).</p>
      <h3>Picks</h3>
      <p>Every game with a line gets an ATS side: the model's expected cover margin vs the current line is the <em>edge</em>. Tiers: lean ≥ ${s.lean_edge}, play ≥ ${s.play_edge}, strong ≥ ${s.strong_edge}. Picks update with each refresh until kickoff, then lock at the last pre-kickoff line. They are graded SU and ATS after the game (pushes count as neither).</p>
      <h3>ATS trends</h3>
      <p>Historical lines and results from ${m.trend_seasons[0]}–${m.trend_seasons[1]} (consensus/median closing spread). Splits: overall, favorite/underdog, home/away/neutral (plus combined), conference play, off a bye (${s.bye_days}+ days rest) and last 10. Cards show the splits that apply to the game; trends with ${s.trend_min_games}+ decisions at ≥ 60% or ≤ 40% are highlighted.</p>
      <h3>Bet splits</h3>
      <p>Public bet % (tickets) and money % (handle) per side. <strong>Sharp $</strong> flags a side where money % exceeds bet % by ${s.sharp_gap}+ points; <strong>Public</strong> marks ${s.public_bets}%+ of tickets; <strong>Reverse line move</strong> marks a 1+ point move against the side with most tickets. ${splitsLine}</p>
      <p class="muted">For entertainment and research. Not betting advice.</p>`;
  }

  function fillConfSelect(sel, confs) {
    sel.insertAdjacentHTML("beforeend", [...confs].filter(Boolean).sort().map((c) => `<option>${esc(c)}</option>`).join(""));
  }

  async function init() {
    const [meta, slate, ratings, record, trends] = await Promise.all(["meta", "slate", "ratings", "record", "trends"].map(getJSON));
    if (!meta) {
      $("#empty").hidden = false;
      return;
    }
    Object.assign(state, { meta, slate: slate?.games || [], ratings: ratings?.teams || [], record, trends });

    $("#subtitle").textContent = `${meta.season} · ${meta.slate_label} · power ratings, projections, ATS picks`;
    $("#updated").textContent = `Updated ${new Date(meta.generated_at).toLocaleString()}`;
    if (record?.season_record) {
      const sr = record.season_record;
      const pill = $("#record-pill");
      pill.hidden = false;
      pill.innerHTML = `ATS ${recStr(sr.ats)} <span style="opacity:.7">${pct(sr.ats.pct)}</span><br>SU ${recStr(sr.su, false)}`;
    }

    fillConfSelect($("#game-conf"), new Set(state.slate.flatMap((g) => [g.home.conf, g.away.conf])));
    fillConfSelect($("#rating-conf"), new Set(state.ratings.map((r) => r.conference)));
    if (trends) {
      $("#trend-sort").innerHTML = TREND_COLS.map((k) => `<option value="${k}">Sort: ${esc(trends.labels[k])}</option>`).join("");
    }

    ["#game-search", "#game-conf", "#game-sort", "#game-picks", "#game-flags"].forEach((s) => $(s).addEventListener("input", renderGames));
    ["#rating-search", "#rating-conf"].forEach((s) => $(s).addEventListener("input", renderRatings));
    ["#log-search", "#log-filter"].forEach((s) => $(s).addEventListener("input", renderLog));
    ["#trend-search", "#trend-min"].forEach((s) => $(s).addEventListener("input", renderTrends));
    $("#trend-sort").addEventListener("input", () => {
      $("#trends")._sort = { key: $("#trend-sort").value, asc: false };
      renderTrends();
    });

    renderGames();
    renderRatings();
    renderRecord();
    renderTrends();
    renderMethod();
    const initial = location.hash.slice(1);
    if (initial && document.getElementById(`tab-${initial}`)) showTab(initial);
  }

  init();
})();
