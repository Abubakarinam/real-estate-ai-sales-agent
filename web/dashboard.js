(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const page = $("#page");
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const aed = (n) => (n ? "AED " + Number(n).toLocaleString("en-US") : "");
  const TEMP_COLOR = { hot: "var(--hot)", warm: "var(--warm)", cold: "var(--cold)" };
  const STAGES = ["new", "qualified", "viewing booked", "handed off", "won", "lost"];
  let timer = null;

  function md(text) {
    return esc(text).split(/\n{2,}/).map((b) => {
      const lines = b.split("\n");
      if (lines.every((l) => /^\s*([-*•]|\d+\.)\s+/.test(l)))
        return "<ul>" + lines.map((l) => "<li>" + l.replace(/^\s*([-*•]|\d+\.)\s+/, "") + "</li>").join("") + "</ul>";
      return "<p dir=\"auto\">" + lines.join("<br>") + "</p>";
    }).join("").replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  }
  function ago(iso) {
    if (!iso) return "";
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + " min ago";
    if (s < 86400) return Math.floor(s / 3600) + " h ago";
    return Math.floor(s / 86400) + " d ago";
  }
  const timeOf = (iso) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const budget = (l) => l.budget_min && l.budget_max && l.budget_min !== l.budget_max
    ? `${aed(l.budget_min)} – ${Number(l.budget_max).toLocaleString("en-US")}` : aed(l.budget_max || l.budget_min);
  const tempPill = (t) => `<span class="pill ${esc(t)}"><span class="dot"></span>${esc(t)}</span>`;
  const scoreBadge = (l) => `<span class="score ${esc(l.temperature)}"><span class="num">${l.score}</span>${tempPill(l.temperature)}</span>`;
  const stagePill = (s) => `<span class="pill ${s === "viewing booked" || s === "won" ? "ok" : s === "handed off" ? "warm" : s === "lost" ? "grey" : "grey"}">${esc(s)}</span>`;
  const convPill = (s) => s === "human" ? `<span class="pill hot">waiting for a human</span>` : s === "closed" ? `<span class="pill grey">closed</span>` : `<span class="pill navy">AI handling</span>`;
  const leadName = (l) => l.name || (l.language === "Arabic" ? "Arabic-speaking visitor" : "Website visitor");

  async function api(path, opts = {}) {
    const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
    if (res.status === 401) { showLogin(); throw new Error("login"); }
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    return res.headers.get("content-type")?.includes("json") ? res.json() : res.text();
  }

  // ------------------------------------------------------------ auth
  function showLogin() { $("#app").hidden = true; $("#login").hidden = false; $("#password").focus(); }
  $("#login-form").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: $("#password").value }) })
        .then((r) => { if (!r.ok) throw new Error("Wrong password"); });
      $("#login").hidden = true; boot();
    } catch (err) { $("#login-error").textContent = err.message; }
  };
  $("#logout").onclick = async () => { await fetch("/api/logout", { method: "POST" }); showLogin(); };

  async function boot() {
    const me = await api("/api/me");
    $("#app").hidden = false;
    const banner = $("#banner");
    banner.hidden = !me.default_password;
    banner.textContent = "You are using the default demo password. Set ADMIN_PASSWORD in .env before putting this online.";
    route();
  }

  async function refreshWaiting() {
    try {
      const s = await api("/api/conversations?status=human");
      const c = $("#waiting-count"); c.hidden = !s.length; c.textContent = s.length;
    } catch {}
  }

  // ------------------------------------------------------------ router
  window.addEventListener("hashchange", route);
  function route() {
    clearInterval(timer);
    const [, name = "overview", id] = location.hash.split("/");
    document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.page === name));
    refreshWaiting();
    const pages = { overview, leads: id ? leadDetail : leadsPage, conversations, viewings, knowledge, settings };
    (pages[name] || overview)(id).catch((e) => { if (e.message !== "login") page.innerHTML = `<div class="empty">Could not load: ${esc(e.message)}</div>`; });
  }

  const head = (title, sub, actions = "") => `<div class="page-head"><div><h1>${title}</h1><p>${sub}</p></div><div class="actions">${actions}</div></div>`;

  // ------------------------------------------------------------ overview
  async function overview() {
    const s = await api("/api/stats");
    const t = s.by_temperature, total = s.leads || 1;
    const hot = t.hot || 0, warm = t.warm || 0, cold = t.cold || 0;
    const stageMax = Math.max(1, ...Object.values(s.by_stage));
    page.innerHTML = head("Overview", "What the AI sales agent did for you, live.",
      `<a class="btn" href="/api/leads.csv">Export leads (CSV)</a><a class="btn primary" href="/" target="_blank">Try the chat ↗</a>`) + `
      <div class="kpis">
        <div class="panel kpi accent"><div class="label">Conversations</div><div class="value">${s.conversations}</div><div class="note">${s.ai_replies} AI replies sent</div></div>
        <div class="panel kpi"><div class="label">Leads</div><div class="value">${s.leads}</div><div class="note">${Object.entries(s.by_language).map(([k, v]) => `${v} ${esc(k)}`).join(" · ")}</div></div>
        <div class="panel kpi"><div class="label">Hot leads</div><div class="value" style="color:var(--hot)">${hot}</div><div class="note">score 70 and above</div></div>
        <div class="panel kpi"><div class="label">Viewings booked</div><div class="value">${s.viewings_booked}</div><div class="note">booked by the AI</div></div>
        <div class="panel kpi"><div class="label">Waiting for a human</div><div class="value" style="color:${s.waiting_for_human ? "var(--warm)" : "inherit"}">${s.waiting_for_human}</div><div class="note">handed off by the AI</div></div>
      </div>
      <div class="cols">
        <div class="panel">
          <div class="panel-head"><h2>Best leads right now</h2><a class="right btn small" href="#/leads">All leads</a></div>
          <div class="table-wrap"><table><thead><tr><th>Lead</th><th>Score</th><th>Budget</th><th>Looking for</th><th>Stage</th></tr></thead><tbody>
          ${s.top_leads.map((l) => `<tr class="click" data-href="#/leads/${l.id}"><td class="name-cell"><strong dir="auto">${esc(leadName(l))}</strong><span>${esc(l.phone || l.language || "")}</span></td>
            <td>${scoreBadge(l)}</td><td>${esc(budget(l)) || '<span class="muted">not yet</span>'}</td>
            <td dir="auto">${esc([l.intent, l.bedrooms && l.bedrooms + " bed", l.property_type, l.areas].filter(Boolean).join(" · "))}</td><td>${stagePill(l.stage)}</td></tr>`).join("") || `<tr><td colspan="5" class="empty">No leads yet. Open the customer chat and say hello.</td></tr>`}
          </tbody></table></div>
        </div>
        <div class="panel">
          <div class="panel-head"><h2>Lead temperature</h2></div>
          <div class="panel-body">
            <div class="split"><div style="width:${hot / total * 100}%;background:var(--hot)"></div><div style="width:${warm / total * 100}%;background:var(--warm)"></div><div style="width:${cold / total * 100}%;background:var(--cold)"></div></div>
            <div class="legend"><span><i class="dot" style="color:var(--hot)"></i>Hot ${hot}</span><span><i class="dot" style="color:var(--warm)"></i>Warm ${warm}</span><span><i class="dot" style="color:var(--cold)"></i>Cold ${cold}</span></div>
          </div>
          <div class="panel-head" style="border-top:1px solid var(--line)"><h2>Sales pipeline</h2></div>
          <div class="panel-body bars">${STAGES.map((st) => `<div class="bar-row"><span>${st}</span><div class="bar-track"><div class="bar-fill" style="width:${(s.by_stage[st] || 0) / stageMax * 100}%"></div></div><b>${s.by_stage[st] || 0}</b></div>`).join("")}</div>
        </div>
      </div>
      <div class="cols">
        <div class="panel">
          <div class="panel-head"><h2>Upcoming viewings</h2><a class="right btn small" href="#/viewings">Calendar</a></div>
          <div class="table-wrap"><table><tbody>${s.upcoming_viewings.map((v) => `<tr class="click" data-href="#/leads/${v.lead_id}"><td><b>${esc(v.label)}</b></td><td>${esc(v.listing)}<div class="muted">${esc(v.listing_id)} · ${esc(v.agent)}</div></td><td dir="auto">${esc(v.name)}</td><td>${tempPill(v.temperature)}</td></tr>`).join("") || `<tr><td class="empty">No viewings booked yet.</td></tr>`}</tbody></table></div>
        </div>
        <div class="panel">
          <div class="panel-head"><h2>AI usage</h2></div>
          <div class="panel-body kv">
            <span>Model</span><b>${esc(s.model)}</b>
            <span>Input tokens</span><b>${s.tokens.input.toLocaleString("en-US")}</b>
            <span>Cached input tokens</span><b>${s.tokens.cache_read.toLocaleString("en-US")}</b>
            <span>Output tokens</span><b>${s.tokens.output.toLocaleString("en-US")}</b>
          </div>
        </div>
      </div>`;
    bindRows();
    timer = setInterval(() => location.hash.startsWith("#/overview") || !location.hash ? overview().catch(() => {}) : null, 15000);
  }

  function bindRows() { document.querySelectorAll("[data-href]").forEach((r) => r.onclick = () => location.hash = r.dataset.href); }

  // ------------------------------------------------------------ leads
  let leadFilter = "", leadQuery = "";
  async function leadsPage() {
    const rows = await api(`/api/leads?temperature=${leadFilter}&q=${encodeURIComponent(leadQuery)}`);
    page.innerHTML = head("Leads", "Every person the AI talked to, scored by budget, timeline, financing and intent.",
      `<a class="btn" href="/api/leads.csv">Export CSV</a>`) + `
      <div class="panel">
        <div class="filters">
          <div class="seg" id="seg">${["", "hot", "warm", "cold"].map((t) => `<button data-t="${t}" class="${t === leadFilter ? "on" : ""}">${t || "All"}</button>`).join("")}</div>
          <input class="search" id="q" placeholder="Search name, phone, area…" value="${esc(leadQuery)}">
        </div>
        <div class="table-wrap"><table><thead><tr><th>Lead</th><th>Score</th><th>Intent</th><th>Budget</th><th>Area</th><th>Timeline</th><th>Financing</th><th>Stage</th><th>Summary</th><th>Updated</th></tr></thead><tbody>
        ${rows.map((l) => `<tr class="click" data-href="#/leads/${l.id}">
          <td class="name-cell"><strong dir="auto">${esc(leadName(l))}</strong><span>${esc(l.phone || l.email || l.channel)}${l.language ? " · " + esc(l.language) : ""}</span></td>
          <td>${scoreBadge(l)}</td><td>${esc(l.intent || "")}</td><td>${esc(budget(l))}</td><td dir="auto">${esc(l.areas || "")}</td>
          <td>${esc(l.timeline || "")}</td><td>${esc(l.financing || "")}</td><td>${stagePill(l.stage)}${l.conversation_status === "human" ? ' <span class="pill hot">needs reply</span>' : ""}</td>
          <td class="summary-cell">${esc(l.summary || "")}</td><td class="muted">${ago(l.updated_at)}</td></tr>`).join("") || `<tr><td colspan="10" class="empty">No leads match.</td></tr>`}
        </tbody></table></div>
      </div>`;
    document.querySelectorAll("#seg button").forEach((b) => b.onclick = () => { leadFilter = b.dataset.t; leadsPage(); });
    let deb; $("#q").oninput = (e) => { clearTimeout(deb); deb = setTimeout(() => { leadQuery = e.target.value; leadsPage().then(() => { const q = $("#q"); q.focus(); q.setSelectionRange(q.value.length, q.value.length); }); }, 300); };
    bindRows();
  }

  function transcriptHtml(msgs) {
    const who = { customer: "Customer", ai: "AI assistant", staff: "Consultant" };
    return msgs.map((m) => `<div class="bubble ${esc(m.role)}" dir="auto"><span class="who">${who[m.role] || esc(m.role)}</span>${md(m.text)}<span class="time">${timeOf(m.created_at)}</span></div>`).join("");
  }

  async function leadDetail(id) {
    const d = await api(`/api/leads/${id}`);
    const l = d.lead, c = d.conversation;
    const field = (label, value) => `<div><span>${label}</span><b dir="auto">${esc(value) || '<span class="muted">—</span>'}</b></div>`;
    page.innerHTML = head(`<a href="#/leads" style="text-decoration:none;color:var(--muted)">Leads /</a> <span dir="auto">${esc(leadName(l))}</span>`,
      `Started ${ago(c.created_at)} on the ${esc(c.channel)} chat · ${esc(c.language || "")}`,
      `<a class="btn" href="#/conversations/${esc(c.id)}">Open conversation</a>`) + `
      <div class="lead-grid">
        <div class="panel">
          <div class="profile-top">
            <div class="ring" style="--p:${l.score};--c:${TEMP_COLOR[l.temperature]}"><div>${l.score}</div></div>
            <div><h2 dir="auto">${esc(leadName(l))}</h2>${tempPill(l.temperature)} ${stagePill(l.stage)} ${convPill(c.status)}</div>
          </div>
          <div class="fields">
            ${field("Phone", l.phone)}${field("Email", l.email)}${field("Intent", l.intent)}${field("Language", l.language)}
            ${field("Budget", budget(l))}${field("Areas", l.areas)}${field("Property", [l.bedrooms && l.bedrooms + " bed", l.property_type].filter(Boolean).join(" "))}
            ${field("Timeline", l.timeline)}${field("Financing", l.financing)}${field("Listings", l.interested_listings)}
          </div>
          <div class="block"><h3>AI summary</h3><div>${esc(l.summary || "No summary yet.")}</div></div>
          <div class="block"><h3>Next step</h3><div>${esc(l.next_step || "—")}</div></div>
          <div class="block"><h3>Why this score</h3><div class="reasons">${l.score_reasons.map((r) => `<span>${esc(r)}</span>`).join("") || '<span>No signals yet</span>'}</div></div>
          ${c.handoff_reason ? `<div class="block"><h3>Hand-off reason</h3><div>${esc(c.handoff_reason)}</div></div>` : ""}
          <div class="block"><h3>Stage</h3><select id="stage">${STAGES.map((s) => `<option ${s === l.stage ? "selected" : ""}>${s}</option>`).join("")}</select></div>
          <div class="block"><h3>Team notes</h3><textarea class="note" id="notes" placeholder="Add a note for the team">${esc(l.notes || "")}</textarea>
            <button class="btn small" id="save-notes" style="margin-top:8px">Save note</button></div>
        </div>
        <div style="display:flex;flex-direction:column;gap:18px;min-width:0">
          ${d.viewings.length ? `<div class="panel"><div class="panel-head"><h2>Viewings</h2></div><div class="table-wrap"><table><tbody>${d.viewings.map((v) => `<tr><td><b>${esc(v.label)}</b></td><td>${esc(v.listing)} <span class="muted">${esc(v.listing_id)}</span></td><td>${esc(v.agent)}</td><td>${v.status === "booked" ? '<span class="pill ok">booked</span>' : `<span class="pill grey">${esc(v.status)}</span>`}</td></tr>`).join("")}</tbody></table></div></div>` : ""}
          ${d.listings.length ? `<div class="panel"><div class="panel-head"><h2>Interested in</h2></div><div class="table-wrap"><table><tbody>${d.listings.map((x) => `<tr><td><b>${esc(x.id)}</b></td><td>${esc(x.title)}</td><td>${aed(x.price_aed)}${x.purpose === "rent" ? " / year" : ""}</td></tr>`).join("")}</tbody></table></div></div>` : ""}
          <div class="panel"><div class="panel-head"><h2>Conversation</h2><span class="right muted">${(c.input_tokens + c.cache_read_tokens).toLocaleString("en-US")} in · ${c.output_tokens.toLocaleString("en-US")} out tokens</span></div>
            <div class="transcript">${transcriptHtml(d.messages)}</div></div>
        </div>
      </div>`;
    $("#stage").onchange = (e) => api(`/api/leads/${id}`, { method: "PATCH", body: JSON.stringify({ stage: e.target.value }) }).then(() => leadDetail(id));
    $("#save-notes").onclick = () => api(`/api/leads/${id}`, { method: "PATCH", body: JSON.stringify({ notes: $("#notes").value }) }).then(() => { $("#save-notes").textContent = "Saved ✓"; });
    const tr = $(".transcript"); tr.scrollTop = tr.scrollHeight;
  }

  // ------------------------------------------------------------ conversations
  let convFilter = "";
  async function conversations(id) {
    const list = await api(`/api/conversations?status=${convFilter}`);
    if (!id && list.length && window.innerWidth > 860) id = list[0].id;
    page.innerHTML = head("Conversations", "Read any chat, take over from the AI, or give it back.") + `
      <div class="conv-layout">
        <div class="panel conv-list">
          <div class="filters"><div class="seg" id="cseg">${[["", "All"], ["human", "Waiting for a human"], ["ai", "AI handling"]].map(([k, v]) => `<button data-s="${k}" class="${k === convFilter ? "on" : ""}">${v}</button>`).join("")}</div></div>
          ${list.map((c) => `<a class="conv-item ${c.id === id ? "on" : ""}" href="#/conversations/${esc(c.id)}">
            <div class="top"><strong dir="auto">${esc(c.name || (c.language === "Arabic" ? "Arabic-speaking visitor" : "Website visitor"))}</strong>${c.temperature ? tempPill(c.temperature) : ""}</div>
            <div class="top" style="margin-top:4px">${convPill(c.status)}<span class="muted" style="margin-left:auto;font-size:12px">${ago(c.updated_at)}</span></div>
            <div class="last" dir="auto">${esc((c.last_text || "").replace(/\*\*/g, "").replace(/\s+/g, " "))}</div></a>`).join("") || `<div class="empty">No conversations yet.</div>`}
        </div>
        <div id="conv-detail">${id ? "" : `<div class="panel empty">Pick a conversation.</div>`}</div>
      </div>`;
    document.querySelectorAll("#cseg button").forEach((b) => b.onclick = () => { convFilter = b.dataset.s; conversations(); });
    if (id) await convDetail(id);
  }

  async function convDetail(id) {
    const box = $("#conv-detail");
    const [msgs, list] = await Promise.all([api(`/api/chat/${id}/messages`), api(`/api/conversations`)]);
    const c = list.find((x) => x.id === id) || {};
    box.innerHTML = `<div class="panel">
      <div class="panel-head"><h2 dir="auto">${esc(c.name || "Website visitor")}</h2>${convPill(msgs.status)}
        <div class="right" style="display:flex;gap:6px;flex-wrap:wrap">
          ${c.lead_id ? `<a class="btn small" href="#/leads/${c.lead_id}">Lead profile</a>` : ""}
          ${msgs.status === "ai" ? `<button class="btn small" data-status="human">Take over</button>` : `<button class="btn small primary" data-status="ai">Give back to AI</button>`}
          ${msgs.status !== "closed" ? `<button class="btn small" data-status="closed">Close</button>` : ""}
        </div></div>
      ${c.handoff_reason && msgs.status === "human" ? `<div class="block" style="border-top:0;background:var(--warm-bg)"><b>AI handed off:</b> ${esc(c.handoff_reason)}</div>` : ""}
      <div class="transcript" id="tr">${transcriptHtml(msgs.messages)}</div>
      <form class="reply-box" id="reply"><textarea id="reply-text" dir="auto" placeholder="${msgs.status === "ai" ? "Type to take over from the AI and reply as a consultant…" : "Reply to the customer as a consultant…"}"></textarea><button class="btn primary">Send</button></form>
    </div>`;
    const tr = $("#tr"); tr.scrollTop = tr.scrollHeight;
    box.querySelectorAll("[data-status]").forEach((b) => b.onclick = async () => {
      await api(`/api/conversations/${id}/status`, { method: "POST", body: JSON.stringify({ status: b.dataset.status }) });
      conversations(id);
    });
    $("#reply").onsubmit = async (e) => {
      e.preventDefault();
      const text = $("#reply-text").value.trim(); if (!text) return;
      await api(`/api/conversations/${id}/reply`, { method: "POST", body: JSON.stringify({ text }) });
      conversations(id);
    };
    timer = setInterval(async () => {
      if (!location.hash.startsWith("#/conversations") || document.activeElement === $("#reply-text")) return;
      const m = await api(`/api/chat/${id}/messages`).catch(() => null);
      if (m && m.messages.length !== msgs.messages.length) conversations(id);
    }, 5000);
  }

  // ------------------------------------------------------------ viewings
  async function viewings() {
    const rows = await api("/api/viewings");
    const days = {};
    rows.forEach((v) => { const d = v.start_at.slice(0, 10); (days[d] = days[d] || []).push(v); });
    const dayLabel = (d) => new Date(d + "T12:00").toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
    page.innerHTML = head("Viewings", "Booked by the AI into each consultant's free slots. Add any of them to Google Calendar.") + `
      <div class="agenda">${Object.keys(days).sort().map((d) => `<div class="day"><h3>${dayLabel(d)}</h3>
        ${days[d].map((v) => `<div class="panel vcard ${v.status}"><div class="time">${v.start_at.slice(11, 16)}</div>
          <div><div class="t">${esc(v.listing)} <span class="muted">· ${esc(v.listing_id)}</span></div>
          <div class="s"><span dir="auto">${esc(v.name)}</span> · ${esc(v.phone)} · with ${esc(v.agent)} · ${esc(v.area)}</div>
          <div style="margin-top:6px">${tempPill(v.temperature)} <span class="pill ${v.status === "booked" ? "ok" : "grey"}">${esc(v.status)}</span></div></div>
          <div class="acts">
            <a class="btn small" href="#/leads/${v.lead_id}">Lead</a>
            <a class="btn small" href="${esc(v.google_calendar)}" target="_blank" rel="noopener">Google Calendar</a>
            <a class="btn small" href="/api/viewings/${v.id}.ics">.ics</a>
            ${v.status === "booked" ? `<button class="btn small" data-v="${v.id}" data-s="done">Mark done</button><button class="btn small" data-v="${v.id}" data-s="cancelled">Cancel</button>` : ""}
          </div></div>`).join("")}</div>`).join("") || `<div class="panel empty">No viewings yet. They appear here when the AI books one.</div>`}</div>`;
    document.querySelectorAll("[data-v]").forEach((b) => b.onclick = async () => {
      await api(`/api/viewings/${b.dataset.v}`, { method: "PATCH", body: JSON.stringify({ status: b.dataset.s }) });
      viewings();
    });
  }

  // ------------------------------------------------------------ knowledge
  async function knowledge() {
    const [docs, listings] = await Promise.all([api("/api/knowledge"), api("/api/listings")]);
    page.innerHTML = head("Knowledge base", "The AI answers only from these documents and the live listings below.") + `
      <div class="kb-grid">
        <div class="panel">
          <div class="panel-head"><h2>Documents</h2><span class="right muted">${docs.length} files · ${docs.reduce((a, d) => a + d.chunks, 0)} passages</span></div>
          <div class="table-wrap"><table><thead><tr><th>File</th><th>Passages</th><th></th></tr></thead><tbody>
            ${docs.map((d) => `<tr><td><b>${esc(d.source)}</b><div class="muted">${Math.round(d.chars / 100) / 10}k characters</div></td><td>${d.chunks}</td><td><button class="btn small" data-del="${esc(d.source)}">Remove</button></td></tr>`).join("")}
          </tbody></table></div>
          <div class="panel-body"><label class="upload">Add a PDF, Markdown or text file (price list, brochure, FAQ)<br><br><input type="file" id="file" accept=".pdf,.md,.txt"></label><p id="up-msg" class="muted"></p></div>
        </div>
        <div class="panel">
          <div class="panel-head"><h2>Test a search</h2></div>
          <div class="panel-body"><form id="ks" style="display:flex;gap:8px;margin-bottom:12px"><input class="search" id="kq" style="margin:0;flex:1" placeholder="e.g. Golden Visa, DLD fee, payment plan" value="payment plan"><button class="btn primary">Search</button></form><div id="hits"></div></div>
        </div>
      </div>
      <div class="panel" style="margin-top:18px">
        <div class="panel-head"><h2>Live listings</h2><span class="right muted">data/listings.json · ${listings.length} properties</span></div>
        <div class="table-wrap"><table><thead><tr><th>ID</th><th>Property</th><th>Area</th><th>Price</th><th>Status</th><th>Consultant</th></tr></thead><tbody>
          ${listings.map((x) => `<tr><td><b>${esc(x.id)}</b></td><td>${esc(x.title)}</td><td>${esc(x.area)}</td><td>${aed(x.price_aed)}${x.purpose === "rent" ? " / yr" : ""}</td><td>${esc(x.status)}</td><td>${esc(x.agent)}</td></tr>`).join("")}
        </tbody></table></div>
      </div>`;
    const search = async () => {
      const hits = await api(`/api/knowledge/search?q=${encodeURIComponent($("#kq").value)}`);
      $("#hits").innerHTML = hits.map((h) => `<div class="hit" dir="auto"><b>${esc(h.source)} › ${esc(h.heading)} <span class="muted">(${h.score})</span></b>${esc(h.text.slice(0, 420))}${h.text.length > 420 ? "…" : ""}</div>`).join("") || `<p class="muted">No match.</p>`;
    };
    $("#ks").onsubmit = (e) => { e.preventDefault(); search(); };
    search();
    $("#file").onchange = async (e) => {
      const f = e.target.files[0]; if (!f) return;
      const fd = new FormData(); fd.append("file", f);
      $("#up-msg").textContent = "Reading " + f.name + "…";
      const r = await fetch("/api/knowledge/upload", { method: "POST", body: fd });
      const j = await r.json();
      $("#up-msg").textContent = r.ok ? `Added ${j.source}: ${j.chunks} passages.` : j.detail;
      if (r.ok) setTimeout(knowledge, 900);
    };
    document.querySelectorAll("[data-del]").forEach((b) => b.onclick = async () => {
      if (!confirm("Remove " + b.dataset.del + " from the knowledge base?")) return;
      await api("/api/knowledge/" + encodeURIComponent(b.dataset.del), { method: "DELETE" });
      knowledge();
    });
  }

  // ------------------------------------------------------------ settings
  async function settings() {
    const s = await api("/api/settings");
    const sc = s.scoring;
    const rows = (obj) => Object.entries(obj).map(([k, v]) => `<tr><td>${esc(k)}</td><td><b>+${v}</b></td></tr>`).join("");
    page.innerHTML = head("Settings", "Change these in the .env file and restart.") + `
      <div class="kb-grid">
        <div class="panel"><div class="panel-head"><h2>AI agent</h2></div><div class="panel-body kv">
          <span>Agency</span><b>${esc(s.agency)}</b>
          <span>Claude model</span><b>${esc(s.model)} <span class="muted">(CLAUDE_MODEL)</span></b>
          <span>Effort</span><b>${esc(s.effort)} <span class="muted">(CLAUDE_EFFORT)</span></b>
          <span>Claude API key</span><b>${s.api_key_set ? '<span class="pill ok">set</span>' : '<span class="pill hot">missing: add ANTHROPIC_API_KEY</span>'}</b>
          <span>CRM webhook</span><b>${s.webhook ? '<span class="pill ok">on</span> leads, bookings and hand-offs are sent to LEAD_WEBHOOK_URL' : '<span class="pill grey">off</span> set LEAD_WEBHOOK_URL to send leads to n8n, Make, Zapier or a Google Sheet'}</b>
          <span>Time zone</span><b>${esc(s.timezone)}</b>
        </div></div>
        <div class="panel"><div class="panel-head"><h2>Lead scoring rules</h2><span class="right muted">Hot from ${sc.hot_from} · Warm from ${sc.warm_from}</span></div>
          <div class="table-wrap"><table><tbody>
            <tr><th colspan="2">Timeline</th></tr>${rows(sc.timeline)}
            <tr><th colspan="2">Financing</th></tr>${rows(sc.financing)}
            <tr><th colspan="2">Other signals</th></tr>${rows(sc.other)}
          </tbody></table></div></div>
      </div>`;
  }

  // start
  fetch("/api/me").then((r) => (r.ok ? boot() : showLogin())).catch(showLogin);
})();
