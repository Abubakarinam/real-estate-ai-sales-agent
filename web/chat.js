(() => {
  const log = document.getElementById("log");
  const input = document.getElementById("input");
  const form = document.getElementById("composer");
  const sendBtn = document.getElementById("send");
  const chips = document.getElementById("chips");
  const KEY = "hk_conversation";
  let convId = null, lastId = 0, busy = false, status = "ai";

  const store = {
    get() { try { return localStorage.getItem(KEY); } catch { return null; } },
    set(v) { try { v ? localStorage.setItem(KEY, v) : localStorage.removeItem(KEY); } catch {} },
  };

  const esc = (s) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  // tiny, safe markdown: escape first, then **bold**, bullet lists and paragraphs
  function md(text) {
    const blocks = esc(text).split(/\n{2,}/);
    return blocks.map((b) => {
      const lines = b.split("\n");
      if (lines.every((l) => /^\s*([-*•]|\d+\.)\s+/.test(l))) {
        return "<ul>" + lines.map((l) => "<li>" + l.replace(/^\s*([-*•]|\d+\.)\s+/, "") + "</li>").join("") + "</ul>";
      }
      return "<p dir=\"auto\">" + lines.join("<br>") + "</p>";
    }).join("").replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  }

  function render(m) {
    if (m.id && m.id <= lastId) return;
    if (m.id) lastId = m.id;
    const div = document.createElement("div");
    div.className = "msg " + m.role;
    div.dir = "auto";
    if (m.role === "staff") div.innerHTML = '<span class="label">Consultant</span>' + md(m.text);
    else div.innerHTML = md(m.text);
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
  }

  function systemNote(text) {
    const div = document.createElement("div");
    div.className = "msg system";
    div.textContent = text;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
  }

  function setChips(list) {
    chips.innerHTML = "";
    list.forEach((t) => {
      const b = document.createElement("button");
      b.className = "chip"; b.type = "button"; b.textContent = t; b.dir = "auto";
      b.onclick = () => send(t);
      chips.appendChild(b);
    });
  }

  function typing(on) {
    let t = document.getElementById("typing");
    if (on && !t) {
      t = document.createElement("div"); t.id = "typing"; t.className = "typing";
      t.innerHTML = "<i></i><i></i><i></i>"; log.appendChild(t); log.scrollTop = log.scrollHeight;
    } else if (!on && t) t.remove();
  }

  async function api(path, body) {
    const res = await fetch(path, { method: body ? "POST" : "GET", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    return res.json();
  }

  async function start(fresh) {
    log.innerHTML = ""; lastId = 0;
    convId = fresh ? null : store.get();
    if (convId) {
      try {
        const data = await api(`/api/chat/${convId}/messages`);
        data.messages.forEach(render); status = data.status;
        if (data.messages.length <= 1) setChips(["I want to buy", "I want to rent", "Show me investment options", "أريد شراء شقة في دبي"]);
        return;
      } catch { convId = null; }
    }
    const data = await api("/api/chat/start", {});
    convId = data.conversation_id; store.set(convId);
    data.messages.forEach(render);
    setChips(["I want to buy", "I want to rent", "Show me investment options", "أريد شراء شقة في دبي"]);
  }

  async function send(text) {
    text = (text || "").trim();
    if (!text || busy) return;
    busy = true; sendBtn.disabled = true; setChips([]);
    input.value = ""; autosize();
    render({ role: "customer", text });
    if (status === "ai") typing(true);
    try {
      const r = await api(`/api/chat/${convId}`, { message: text });
      typing(false);
      await poll();
      if (r.status === "human" && status !== "human") systemNote("A consultant has joined and will reply here.");
      status = r.status || status;
    } catch (e) {
      typing(false);
      systemNote("Sorry, something went wrong: " + e.message);
    } finally {
      busy = false; sendBtn.disabled = false; input.focus();
    }
  }

  async function poll() {
    if (!convId) return;
    try {
      const data = await api(`/api/chat/${convId}/messages?after=${lastId}`);
      data.messages.filter((m) => m.role !== "customer").forEach(render);
      data.messages.filter((m) => m.role === "customer").forEach((m) => { lastId = Math.max(lastId, m.id); });
      status = data.status;
    } catch {}
  }

  function autosize() { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 120) + "px"; }
  input.addEventListener("input", autosize);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input.value); } });
  form.addEventListener("submit", (e) => { e.preventDefault(); send(input.value); });
  document.getElementById("new-chat").onclick = () => { store.set(null); start(true); };
  setInterval(() => { if (!busy) poll(); }, 4000);

  // ?q=... lets a demo link open the chat with a first question
  start(false).then(() => {
    const q = new URLSearchParams(location.search).get("q");
    if (q) send(q);
  });
})();
