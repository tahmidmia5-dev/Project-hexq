"use strict";
(() => {
  const $app = document.getElementById("app");
  const $toast = document.getElementById("toast");
  let CONFIG = null;

  // ---------- utils ----------
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "#");
  const fmtNum = (n) => (n == null ? "—" : Number(n).toLocaleString("en-US"));
  const fmtSubs = (n) => {
    if (n == null) return "—";
    if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1).replace(/\.0$/, "") + "M";
    if (n >= 1e3) return (n / 1e3).toFixed(n >= 1e5 ? 0 : 1).replace(/\.0$/, "") + "K";
    return String(n);
  };
  const fmtRate = (n) => (n == null ? "—" : `${Number(n).toFixed(1).replace(/\.0$/, "")}/mo`);
  const fmtDate = (iso) => {
    if (!iso) return "—";
    const d = new Date(iso.length === 10 ? iso + "T00:00:00Z" : iso);
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
  };
  const fmtWhen = (iso) => new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  const tierClass = (t) => (t || "reject").toLowerCase();
  const stageLabel = { qualified: "Qualified", pending: "Pending verification", rejected: "Rejected" };
  const verLabel = { verified: "Verified", partial: "Partly verified", unverified: "Unverified" };
  const nicheLabel = (k) => (CONFIG && CONFIG.niches[k]) || k || "—";

  function toast(msg, isErr = false) {
    $toast.textContent = msg;
    $toast.className = "show" + (isErr ? " err" : "");
    clearTimeout(toast.t);
    toast.t = setTimeout(() => ($toast.className = ""), 3200);
  }

  async function api(path, opts = {}) {
    const res = await fetch("/api" + path, {
      method: opts.method || "GET",
      headers: opts.body ? { "Content-Type": "application/json" } : {},
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    let data = null;
    try { data = await res.json(); } catch (_) { /* empty */ }
    if (!res.ok) {
      const err = new Error((data && data.error) || `HTTP ${res.status}`);
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  async function busy(btn, fn) {
    const label = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = "Working…";
    try { return await fn(); } finally { btn.disabled = false; btn.innerHTML = label; }
  }

  function tierBadge(lead) {
    if (lead.stage === "pending") return `<span class="badge provisional">Provisional · ${esc(lead.tier)}</span>`;
    return `<span class="badge ${tierClass(lead.tier)}">${esc(lead.tier)}</span>`;
  }
  const verBadge = (s) => `<span class="badge ${esc(s)}"><span class="dot"></span>${esc(verLabel[s] || s)}</span>`;
  const scorePill = (lead) =>
    `<div class="score-pill ${tierClass(lead.tier)} ${lead.stage === "pending" ? "pending" : ""}" title="${lead.stage === "pending" ? "Provisional score" : "Score"}">${lead.score}</div>`;

  // ---------- views ----------
  async function viewHome() {
    const [stats, leads] = await Promise.all([api("/stats"), api("/leads?sort=recent")]);
    const recent = leads.slice(0, 5);
    $app.innerHTML = `
      <div class="stack">
        <div class="spread"><div><h1>Lead Research</h1><p class="muted">Find and qualify YouTube creators for AQ Clipz.</p></div>
          <a class="btn primary" href="#/research">+ Research a channel</a></div>
        <div class="stats">
          <a class="stat hero" href="#/leads/qualified"><div class="label">Qualified</div><div class="value">${stats.qualified}</div></a>
          <a class="stat" href="#/leads/pending"><div class="label">Pending verification</div><div class="value">${stats.pending}</div></a>
          <a class="stat" href="#/leads/rejected"><div class="label">Rejected</div><div class="value">${stats.rejected}</div></a>
          <a class="stat" href="#/history"><div class="label">Research actions</div><div class="value">${stats.searches}</div></a>
        </div>
        ${stats.pending ? `<div class="notice"><strong>${stats.pending} lead${stats.pending > 1 ? "s" : ""} need${stats.pending > 1 ? "" : "s"} verification.</strong> Scores stay provisional until niche, country, format, subscribers and upload rates are all verified.</div>` : ""}
        <div class="card">
          <div class="section-head"><h2>Quick research</h2><span class="faint">${esc(CONFIG.provider === "youtube_api" ? "YouTube API" : "Public feeds · no API key")}</span></div>
          <form id="quick" class="input-row"><input type="url" name="url" placeholder="youtube.com/@channel" required autocomplete="off" inputmode="url"><button class="btn primary">Research</button></form>
        </div>
        <div>
          <div class="section-head"><h2>Recently updated</h2><a class="link" href="#/leads/qualified">All leads →</a></div>
          ${recent.length ? leadListHtml(recent) : `<div class="empty"><strong>No leads yet</strong>Paste a YouTube channel URL above to start.</div>`}
        </div>
        <div class="card"><div class="section-head"><h2>Export</h2></div>
          <div class="actions">
            <a class="btn" href="/api/export.csv?stage=qualified">Qualified CSV</a>
            <a class="btn" href="/api/export.csv?stage=pending">Pending CSV</a>
            <a class="btn" href="/api/export.csv?stage=rejected">Rejected CSV</a>
            <a class="btn ghost" href="/api/export.csv">All leads CSV</a>
          </div></div>
      </div>`;
    bindLeadRows();
    document.getElementById("quick").addEventListener("submit", (e) => {
      e.preventDefault();
      researchOne(e.target.url.value, e.submitter || e.target.querySelector("button"));
    });
  }

  async function researchOne(url, btn, fetchData = true) {
    try {
      const out = await busy(btn, () => api("/research", { method: "POST", body: { url, fetch: fetchData } }));
      toast(out.warnings && out.warnings.length ? "Researched. Some fields need manual checks." : "Lead researched");
      location.hash = `#/lead/${out.lead_id}`;
    } catch (err) {
      if (err.status === 409 && err.data && err.data.lead_id) {
        toast("Already in your leads. Opening it.");
        location.hash = `#/lead/${err.data.lead_id}`;
      } else toast(err.message, true);
    }
  }

  function leadListHtml(leads) {
    const cards = leads.map((l) => `
      <a class="lead-card" href="#/lead/${l.id}">
        ${scorePill(l)}
        <div style="min-width:0">
          <div class="name">${esc(l.creator_name || l.handle || l.channel_url)}</div>
          <div class="meta"><span>${fmtSubs(l.subscriber_count)} subs</span><span>${esc(nicheLabel(l.niche))}</span><span>${esc(l.country || "Country ?")}</span></div>
          <div class="meta"><span>Long ${fmtRate(l.longform_per_month)}</span><span>Shorts ${fmtRate(l.shorts_per_month)}</span></div>
          <div class="badges">${tierBadge(l)}${verBadge(l.verification_status)}</div>
        </div>
      </a>`).join("");
    const rows = leads.map((l) => `
      <tr data-href="#/lead/${l.id}">
        <td>${scorePill(l)}</td>
        <td><a href="#/lead/${l.id}" style="text-decoration:none;font-weight:650">${esc(l.creator_name || l.handle || "Unnamed")}</a><div class="faint mono">${esc(l.handle || "")}</div></td>
        <td class="num">${fmtSubs(l.subscriber_count)}</td>
        <td>${esc(nicheLabel(l.niche))}</td>
        <td>${esc(l.country || "—")}</td>
        <td class="num">${fmtRate(l.longform_per_month)}</td>
        <td class="num">${fmtRate(l.shorts_per_month)}</td>
        <td>${tierBadge(l)}</td>
        <td>${verBadge(l.verification_status)}</td>
        <td class="faint">${fmtDate(l.research_date)}</td>
      </tr>`).join("");
    return `<div class="lead-list cards">${cards}</div>
      <table class="lead-table"><thead><tr><th>Score</th><th>Creator</th><th>Subs</th><th>Niche</th><th>Country</th><th>Long-form</th><th>Shorts</th><th>Tier</th><th>Verification</th><th>Researched</th></tr></thead><tbody>${rows}</tbody></table>`;
  }
  function bindLeadRows() {
    $app.querySelectorAll("tr[data-href]").forEach((tr) => tr.addEventListener("click", () => (location.hash = tr.dataset.href)));
  }

  async function viewLeads(stage, params) {
    stage = ["qualified", "pending", "rejected"].includes(stage) ? stage : "qualified";
    const q = params.get("q") || "";
    const sort = params.get("sort") || "score";
    const tier = params.get("tier") || "";
    const qs = new URLSearchParams({ stage, sort });
    if (q) qs.set("q", q);
    if (tier) qs.set("tier", tier);
    const [leads, stats] = await Promise.all([api("/leads?" + qs), api("/stats")]);
    const tab = (s, label, n) => `<a href="#/leads/${s}" class="${s === stage ? "active" : ""}">${label}<span class="count">${n}</span></a>`;
    const tierChip = (t) => `<button type="button" data-tier="${t}" class="${tier === t ? "active" : ""}">${t || "All tiers"}</button>`;
    $app.innerHTML = `
      <div class="stack">
        <div class="spread"><h1>Leads</h1><a class="btn small" href="/api/export.csv?stage=${stage}">Export CSV</a></div>
        <div class="seg" role="tablist">${tab("qualified", "Qualified", stats.qualified)}${tab("pending", "Pending", stats.pending)}${tab("rejected", "Rejected", stats.rejected)}</div>
        <p class="muted" style="font-size:14px">${{
          qualified: "Verified leads scoring 55+. Excellent is 85+, Qualified 70–84, Possible 55–69.",
          pending: "Scores are provisional. Verify the critical fields (marked •) on each lead to move it into Qualified or Rejected.",
          rejected: "Verified leads scoring under 55, plus leads rejected by hand.",
        }[stage]}</p>
        <form class="toolbar" id="filters">
          <input type="search" name="q" value="${esc(q)}" placeholder="Search name, handle, niche">
          <select name="sort" style="max-width:200px">
            ${[["score", "Highest score"], ["recent", "Recently updated"], ["subscribers", "Most subscribers"], ["name", "Name A–Z"]]
              .map(([v, l]) => `<option value="${v}" ${v === sort ? "selected" : ""}>${l}</option>`).join("")}
          </select>
          ${stage === "qualified" ? `<div class="seg">${["", "Excellent", "Qualified", "Possible"].map(tierChip).join("")}</div>` : ""}
        </form>
        ${leads.length ? leadListHtml(leads) : `<div class="empty"><strong>Nothing here yet</strong>${q || tier ? "No leads match these filters." : stage === "qualified" ? "Leads appear here once they are verified and score 55 or more." : ""}</div>`}
      </div>`;
    bindLeadRows();
    const form = document.getElementById("filters");
    const go = (extra = {}) => {
      const p = new URLSearchParams();
      const fd = new FormData(form);
      if (fd.get("q")) p.set("q", fd.get("q"));
      if (fd.get("sort") !== "score") p.set("sort", fd.get("sort"));
      const t = extra.tier !== undefined ? extra.tier : tier;
      if (t) p.set("tier", t);
      location.hash = `#/leads/${stage}${p.toString() ? "?" + p : ""}`;
    };
    form.addEventListener("submit", (e) => { e.preventDefault(); go(); });
    form.sort.addEventListener("change", () => go());
    form.querySelectorAll("[data-tier]").forEach((b) => b.addEventListener("click", () => go({ tier: b.dataset.tier })));
  }

  // ---------- lead detail ----------
  const TRI = [["", "Unknown"], ["1", "Yes"], ["0", "No"]];
  const FACTS = [
    { k: "subscriber_count", label: "Subscribers", type: "number" },
    { k: "country", label: "Country (e.g. US)", type: "text" },
    { k: "niche", label: "Niche", type: "niche" },
    { k: "content_format", label: "Content format", type: "format" },
    { k: "longform_per_month", label: "Long-form uploads / month", type: "number", step: "0.1" },
    { k: "shorts_per_month", label: "Shorts / month", type: "number", step: "0.1" },
    { k: "avg_longform_minutes", label: "Avg long-form length (min)", type: "number", step: "0.1" },
    { k: "days_since_last_longform", label: "Days since last long-form", type: "number" },
    { k: "max_longform_gap_days", label: "Longest gap between uploads (days)", type: "number" },
    { k: "quotable_moments", label: "Quotable / hook-worthy moments", type: "tri" },
    { k: "guest_variety", label: "Guests or multiple speakers", type: "tri" },
    { k: "has_offer", label: "Sells course, coaching, service or product", type: "tri" },
    { k: "has_sponsors", label: "Sponsors or monetisation visible", type: "tri" },
    { k: "has_business_email", label: "Public business email", type: "tri" },
    { k: "has_website", label: "Website with contact info", type: "tri" },
    { k: "has_booking_link", label: "Booking / inquiry link", type: "tri" },
  ];

  function factInput(f, lead) {
    const v = lead[f.k];
    const val = v == null ? "" : String(v);
    if (f.type === "niche" || f.type === "format") {
      const opts = f.type === "niche" ? CONFIG.niches : CONFIG.content_formats;
      return `<select name="${f.k}"><option value="">Unknown</option>${Object.entries(opts).map(([k, l]) =>
        `<option value="${k}" ${k === val ? "selected" : ""}>${esc(l)}${f.type === "niche" && CONFIG.target_niches.includes(k) ? " ★" : ""}</option>`).join("")}</select>`;
    }
    if (f.type === "tri") {
      return `<select name="${f.k}">${TRI.map(([k, l]) => `<option value="${k}" ${k === val ? "selected" : ""}>${l}</option>`).join("")}</select>`;
    }
    return `<input type="${f.type}" name="${f.k}" value="${esc(val)}" ${f.type === "number" ? `min="0" step="${f.step || 1}" inputmode="decimal"` : ""} autocomplete="off">`;
  }

  async function viewLead(id) {
    const lead = await api(`/leads/${id}`);
    const prov = lead.provenance || {};
    const critical = new Set(CONFIG.critical_fields);
    const longs = lead.videos.filter((v) => v.kind === "long");
    const shorts = lead.videos.filter((v) => v.kind === "short");
    const bars = lead.score_breakdown.map((p) => `
      <div class="bar-row">
        <div class="top"><span>${esc(p.label)}</span><b>${p.points}<span class="faint">/${p.max}</span></b></div>
        <div class="bar"><span style="width:${(p.points / p.max) * 100}%"></span></div>
        ${p.reasons.length ? `<div class="why">${p.reasons.map(esc).join(" · ")}</div>` : ""}
        ${p.missing.length ? `<div class="miss">Missing: ${p.missing.map(esc).join(", ")}</div>` : ""}
      </div>`).join("");
    const videoItem = (v) => `<li><div class="grow"><a class="link video-title" href="${esc(safeUrl(v.url))}" target="_blank" rel="noopener">${esc(v.title || v.video_id)}</a>
      <div class="faint" style="font-size:12px">${fmtDate(v.published_at)}${v.views != null ? ` · ${fmtNum(v.views)} views` : ""}${v.duration_seconds ? ` · ${Math.round(v.duration_seconds / 60)} min` : ""}</div></div>
      ${lead.best_sample_video_url === v.url ? `<span class="badge qualified">Sample</span>` : `<button class="btn small ghost" data-sample="${esc(v.url)}">Use as sample</button>`}</li>`;

    $app.innerHTML = `
      <a class="back" href="#/leads/${lead.stage}">← ${esc(stageLabel[lead.stage])}</a>
      <div class="stack">
        <div class="card detail-head">
          <div class="stack" style="gap:8px;min-width:0">
            <h1>${esc(lead.creator_name || lead.handle || "Unnamed channel")}</h1>
            <a class="link mono" href="${esc(safeUrl(lead.channel_url))}" target="_blank" rel="noopener">${esc(lead.channel_url)}</a>
            <div class="row">${tierBadge(lead)}${verBadge(lead.verification_status)}<span class="badge">${esc(stageLabel[lead.stage])}</span></div>
            <div class="faint" style="font-size:13px">Researched ${fmtDate(lead.research_date)}${lead.research_provider ? ` via ${esc(lead.research_provider)}` : " (manual entry)"}</div>
          </div>
          <div class="score-big"><div class="n" style="color:${lead.stage === "qualified" ? "var(--lime)" : "var(--text)"}">${lead.score}</div><div class="of">/ 100${lead.stage === "pending" ? "<br>provisional" : ""}</div></div>
        </div>
        ${lead.similar && lead.similar.length ? `<div class="notice"><strong>Possible duplicate:</strong> ${lead.similar.map((s) => `<a class="link" href="#/lead/${s.id}">${esc(s.creator_name)}</a>`).join(", ")} has the same name.</div>` : ""}
        ${lead.research_warnings.length ? `<div class="notice">${lead.research_warnings.map(esc).join("<br>")}</div>` : ""}
        <div class="actions">
          <button class="btn" id="refresh">↻ Refresh from YouTube</button>
          ${lead.manual_reject ? `<button class="btn" id="unreject">Restore lead</button>` : `<button class="btn danger" id="reject">Reject</button>`}
          <button class="btn ghost danger" id="delete">Delete</button>
        </div>
        <div class="grid-2 wide-left">
          <div class="card"><div class="section-head"><h2>Score breakdown</h2></div><div class="bars">${bars}</div></div>
          <div class="card"><div class="section-head"><h2>Explanation</h2></div><p class="explain">${esc(lead.qualification_explanation)}</p>
            <div style="margin-top:14px" class="section-head"><h2>Best sample video</h2></div>
            ${lead.best_sample_video_url ? `<a class="link" href="${esc(safeUrl(lead.best_sample_video_url))}" target="_blank" rel="noopener">${esc(lead.best_sample_video_url)}</a>` : `<p class="muted">None yet</p>`}
          </div>
        </div>

        <form class="card" id="facts">
          <div class="section-head"><h2>Research data</h2><span class="faint" style="font-size:12px">• = needed for verification</span></div>
          <p class="muted" style="font-size:13px;margin-bottom:14px">Values read from YouTube are marked <b style="color:var(--lime)">Verified</b>. Anything you type defaults to <b style="color:var(--amber)">Estimate</b>. Switch it to Verified only after you have checked the source, and add the evidence link below.</p>
          <div class="form-grid">
            <label class="field">Creator name<input type="text" name="creator_name" value="${esc(lead.creator_name || "")}"></label>
            <label class="field">Website URL<input type="url" name="website_url" value="${esc(lead.website_url || "")}" placeholder="https://"></label>
            ${FACTS.map((f) => `<div class="prov-field ${critical.has(f.k) ? "critical" : ""}" data-k="${f.k}">
              <div class="lbl"><span>${esc(f.label)}</span>
                <span class="prov-toggle" data-prov="${esc(prov[f.k] || "")}"><button type="button" data-v="verified">Verified</button><button type="button" data-v="estimate">Estimate</button></span></div>
              ${factInput(f, lead)}</div>`).join("")}
            <label class="field">Best sample video URL<input type="url" name="best_sample_video_url" value="${esc(lead.best_sample_video_url || "")}"></label>
            <label class="field" style="grid-column:1/-1">Notes<textarea name="notes" style="font-family:var(--font);font-size:15px;min-height:80px">${esc(lead.notes || "")}</textarea></label>
          </div>
          <div class="actions" style="margin-top:16px"><button class="btn primary">Save and rescore</button></div>
        </form>

        <div class="grid-2">
          <div class="card"><div class="section-head"><h2>Evidence</h2><span class="faint">${lead.evidence.length}</span></div>
            <ul class="list-plain">${lead.evidence.map((e) => `<li><div class="grow"><a class="link" href="${esc(safeUrl(e.url))}" target="_blank" rel="noopener">${esc(e.label || e.url)}</a>
              <div class="faint" style="font-size:12px">${e.source === "fetched" ? "Fetched automatically" : "Added by you"} · ${fmtDate(e.captured_at)}</div></div>
              ${e.source === "manual" ? `<button class="btn small ghost danger" data-del-ev="${e.id}" aria-label="Remove evidence">✕</button>` : ""}</li>`).join("") || `<li class="muted">No evidence saved yet</li>`}</ul>
            <form id="ev" class="stack" style="gap:8px;margin-top:14px">
              <input type="url" name="url" placeholder="Evidence URL (about page, website, email source…)" required>
              <div class="input-row"><input type="text" name="label" placeholder="What it shows (optional)"><button class="btn">Add</button></div>
            </form>
          </div>
          <div class="card"><div class="section-head"><h2>Recent uploads</h2><span class="faint">from public feeds</span></div>
            <h3 style="margin-bottom:8px">Long-form · ${longs.length}</h3>
            <ul class="list-plain">${longs.slice(0, 8).map(videoItem).join("") || `<li class="muted">None found</li>`}</ul>
            <h3 style="margin:16px 0 8px">Shorts · ${shorts.length}</h3>
            <ul class="list-plain">${shorts.slice(0, 5).map(videoItem).join("") || `<li class="muted">No Shorts found</li>`}</ul>
          </div>
        </div>
      </div>`;

    // provenance toggles
    const form = document.getElementById("facts");
    form.querySelectorAll(".prov-field").forEach((pf) => {
      const tog = pf.querySelector(".prov-toggle");
      const input = pf.querySelector("input,select");
      const paint = () => tog.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.v === tog.dataset.prov));
      paint();
      tog.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
        if (!input.value) { toast("Enter a value first"); return; }
        tog.dataset.prov = b.dataset.v; paint();
      }));
      input.addEventListener("input", () => { tog.dataset.prov = input.value ? "estimate" : ""; paint(); });
    });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fields = {}, provenance = {};
      new FormData(form).forEach((v, k) => (fields[k] = typeof v === "string" ? v.trim() : v));
      form.querySelectorAll(".prov-field").forEach((pf) => {
        const p = pf.querySelector(".prov-toggle").dataset.prov;
        if (p && fields[pf.dataset.k] !== "") provenance[pf.dataset.k] = p;
      });
      try {
        const updated = await busy(e.submitter || form.querySelector(".btn.primary"), () =>
          api(`/leads/${id}`, { method: "PATCH", body: { fields, provenance } }));
        toast(`Saved. Score ${updated.score} (${updated.stage === "pending" ? "provisional" : updated.tier})`);
        viewLead(id);
      } catch (err) { toast(err.message, true); }
    });

    document.getElementById("ev").addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        await api(`/leads/${id}/evidence`, { method: "POST", body: { url: e.target.url.value.trim(), label: e.target.label.value } });
        toast("Evidence saved"); viewLead(id);
      } catch (err) { toast(err.message, true); }
    });
    $app.querySelectorAll("[data-del-ev]").forEach((b) => b.addEventListener("click", async () => {
      await api(`/evidence/${b.dataset.delEv}`, { method: "DELETE" }); viewLead(id);
    }));
    $app.querySelectorAll("[data-sample]").forEach((b) => b.addEventListener("click", async () => {
      await api(`/leads/${id}`, { method: "PATCH", body: { fields: { best_sample_video_url: b.dataset.sample } } });
      toast("Sample video set"); viewLead(id);
    }));
    document.getElementById("refresh").addEventListener("click", async (e) => {
      try {
        await busy(e.currentTarget, () => api(`/leads/${id}/refresh`, { method: "POST" }));
        toast("Refreshed from YouTube"); viewLead(id);
      } catch (err) { toast(err.message, true); }
    });
    const setReject = (v) => async () => {
      const upd = await api(`/leads/${id}`, { method: "PATCH", body: { fields: { manual_reject: v } } });
      toast(v ? "Lead rejected" : "Lead restored"); location.hash = `#/lead/${upd.id}`; viewLead(id);
    };
    document.getElementById("reject")?.addEventListener("click", setReject(1));
    document.getElementById("unreject")?.addEventListener("click", setReject(0));
    document.getElementById("delete").addEventListener("click", async () => {
      if (!confirm("Delete this lead and its evidence permanently?")) return;
      await api(`/leads/${id}`, { method: "DELETE" });
      toast("Lead deleted"); location.hash = "#/leads/" + lead.stage;
    });
  }

  // ---------- research ----------
  function viewResearch() {
    const apiOn = CONFIG.youtube_api_enabled;
    $app.innerHTML = `
      <div class="stack">
        <div><h1>Research</h1><p class="muted">Add creators from a channel URL, a video URL or your own research list. Nothing contacts the creator.</p></div>
        <div class="card stack" style="gap:12px">
          <div class="section-head" style="margin:0"><h2>1 · Find creators</h2><span class="badge ${apiOn ? "verified" : ""}">${apiOn ? "YouTube API on" : "Manual search"}</span></div>
          <p class="muted" style="font-size:14px">${apiOn ? "Searches YouTube channels through the API. Each search uses 100 quota units." :
            "Without an API key the app cannot search YouTube itself. It logs your query and opens a YouTube channel search. Copy the channel links you like into step 2."}</p>
          <form id="search" class="input-row"><input type="search" name="query" placeholder="e.g. business podcast interviews" required><button class="btn primary">Search</button></form>
          <div id="search-out"></div>
          <div class="row" style="gap:6px">${["business podcast", "entrepreneur interview", "sales coach", "consulting tips", "founder story podcast"]
            .map((s) => `<button type="button" class="btn small ghost" data-suggest="${esc(s)}">${esc(s)}</button>`).join("")}</div>
        </div>
        <div class="card stack" style="gap:12px">
          <div class="section-head" style="margin:0"><h2>2 · Research one channel</h2></div>
          <form id="one" class="stack" style="gap:10px">
            <input type="url" name="url" placeholder="https://www.youtube.com/@channel or a video link" required inputmode="url" autocomplete="off">
            <div id="dup-hint" class="faint" style="font-size:13px;min-height:18px"></div>
            <div class="actions"><button class="btn primary" data-fetch="1">Fetch and score</button><button class="btn" data-fetch="0" type="button" id="manual-add">Add without fetching</button></div>
          </form>
        </div>
        <div class="card stack" style="gap:12px">
          <div class="section-head" style="margin:0"><h2>3 · Bulk import research results</h2><span class="faint">max 25</span></div>
          <p class="muted" style="font-size:14px">Paste channel or video URLs, one per line. Duplicates are skipped and reported.</p>
          <form id="bulk" class="stack" style="gap:10px">
            <textarea name="urls" placeholder="https://www.youtube.com/@creator1&#10;https://www.youtube.com/@creator2"></textarea>
            <button class="btn primary">Research all</button>
          </form>
          <div id="bulk-out" class="result-list"></div>
        </div>
      </div>`;

    const sform = document.getElementById("search");
    const runSearch = async (btn) => {
      const out = document.getElementById("search-out");
      try {
        const r = await busy(btn, () => api("/search", { method: "POST", body: { query: sform.query.value.trim() } }));
        if (r.mode === "manual") {
          out.innerHTML = `<div class="notice ok">Query saved to history. <a class="link" href="${esc(r.search_url)}" target="_blank" rel="noopener">Open YouTube channel search →</a></div>`;
          window.open(r.search_url, "_blank", "noopener");
        } else {
          out.innerHTML = r.results.length ? `<ul class="list-plain">${r.results.map((c) => `<li><div class="grow"><a class="link" href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.title)}</a><div class="faint" style="font-size:12px">${esc((c.description || "").slice(0, 120))}</div></div>
            ${c.existing_lead_id ? `<a class="btn small" href="#/lead/${c.existing_lead_id}">Open</a>` : `<button class="btn small" data-add="${esc(c.url)}">Research</button>`}</li>`).join("")}</ul>` : `<p class="muted">No channels found.</p>`;
          out.querySelectorAll("[data-add]").forEach((b) => b.addEventListener("click", () => researchOne(b.dataset.add, b)));
        }
      } catch (err) { toast(err.message, true); }
    };
    sform.addEventListener("submit", (e) => { e.preventDefault(); runSearch(e.submitter || sform.querySelector("button")); });
    $app.querySelectorAll("[data-suggest]").forEach((b) => b.addEventListener("click", () => { sform.query.value = b.dataset.suggest; runSearch(b); }));

    const one = document.getElementById("one");
    const hint = document.getElementById("dup-hint");
    let t;
    one.url.addEventListener("input", () => {
      clearTimeout(t);
      t = setTimeout(async () => {
        const v = one.url.value.trim();
        if (!v) { hint.textContent = ""; return; }
        try {
          const r = await api("/duplicates?url=" + encodeURIComponent(v));
          hint.innerHTML = r.duplicate ? `<span style="color:var(--amber)">Already in your leads.</span> <a class="link" href="#/lead/${r.lead_id}">Open it</a>` : "";
        } catch (err) { hint.textContent = err.message; }
      }, 350);
    });
    one.addEventListener("submit", (e) => { e.preventDefault(); researchOne(one.url.value.trim(), e.submitter || one.querySelector("[data-fetch='1']"), true); });
    document.getElementById("manual-add").addEventListener("click", (e) => {
      if (!one.reportValidity()) return;
      researchOne(one.url.value.trim(), e.currentTarget, false);
    });

    document.getElementById("bulk").addEventListener("submit", async (e) => {
      e.preventDefault();
      const out = document.getElementById("bulk-out");
      try {
        const r = await busy(e.submitter || e.target.querySelector("button"), () =>
          api("/research-bulk", { method: "POST", body: { urls: e.target.urls.value } }));
        out.innerHTML = r.results.map((x) => x.ok
          ? `<div class="r">✓ <a class="link" href="#/lead/${x.lead_id}">${esc(x.url)}</a></div>`
          : `<div class="r ${x.duplicate ? "dup" : "err"}">${x.duplicate ? "⧉" : "✕"} ${esc(x.url)}: ${esc(x.error)}${x.lead_id ? ` · <a class="link" href="#/lead/${x.lead_id}">open</a>` : ""}</div>`).join("");
        const ok = r.results.filter((x) => x.ok).length;
        toast(`${ok} of ${r.results.length} researched`);
      } catch (err) { toast(err.message, true); }
    });
  }

  // ---------- history ----------
  async function viewHistory() {
    const items = await api("/history");
    $app.innerHTML = `
      <div class="stack">
        <div><h1>Search history</h1><p class="muted">Every search, research fetch, refresh and export, newest first.</p></div>
        <div class="card" style="padding-top:4px;padding-bottom:4px">
          ${items.length ? items.map((h) => `<div class="hist-item">
            <span class="kind ${esc(h.outcome)}">${esc(h.kind)}</span>
            <div class="q">${/^https?:/.test(h.query) ? `<a class="link" href="${esc(h.query)}" target="_blank" rel="noopener">${esc(h.query)}</a>` : esc(h.query)}
              ${h.lead_id ? ` · <a class="link" href="#/lead/${h.lead_id}">${esc(h.creator_name || "lead")}</a>` : ""}</div>
            ${h.detail ? `<div class="detail">${esc(h.detail)}</div>` : ""}
            <div class="when">${fmtWhen(h.created_at)} · ${esc(h.outcome)}</div></div>`).join("")
            : `<div class="empty" style="margin:12px 0"><strong>No history yet</strong>Searches and research show up here.</div>`}
        </div>
      </div>`;
  }

  // ---------- router ----------
  async function route() {
    const hash = location.hash.replace(/^#/, "") || "/";
    const [path, query] = hash.split("?");
    const params = new URLSearchParams(query || "");
    const parts = path.split("/").filter(Boolean);
    const section = parts[0] === "leads" || parts[0] === "lead" ? "leads" : parts[0] || "home";
    document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === section));
    try {
      if (!CONFIG) CONFIG = await api("/config");
      if (parts[0] === "leads") await viewLeads(parts[1], params);
      else if (parts[0] === "lead" && parts[1]) await viewLead(parts[1]);
      else if (parts[0] === "research") viewResearch();
      else if (parts[0] === "history") await viewHistory();
      else await viewHome();
    } catch (err) {
      $app.innerHTML = `<div class="empty"><strong>Something went wrong</strong>${esc(err.message)}</div>`;
    }
    window.scrollTo(0, 0);
  }
  window.addEventListener("hashchange", route);
  route();
})();
