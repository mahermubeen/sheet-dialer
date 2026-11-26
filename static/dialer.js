const CFG = window.DIALER_CONFIG;  // set by templates/dialer.html
const OUTCOMES = ["Interested","Demo booked","Callback","No answer","Voicemail","Not interested","Wrong number","Do not call"];
let queue = [], scripts = {}, idx = 0, chosen = "", timer = null;
let allLeads = [], results = [], resIdx = 0, sortKey = "", sortDir = 1;
const ZOOM_ORIGIN = "https://applications.zoom.us";
const EMBED = CFG.zoom_mode === "embed";
const DEBUG = new URLSearchParams(location.search).has("debug");  // lets you fake Zoom events with window.postMessage
let call = {active:false, connected:false, start:0, secs:0}, liveTimer = null;

const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function toast(msg){ const t=$("#toast"); t.textContent=msg; t.style.display="block"; setTimeout(()=>t.style.display="none",2200); }

function dialUrl(lead){
  let u = "zoomphonecall://" + encodeURIComponent(lead._dial);
  if (CFG.caller_id) u += "?callerid=" + encodeURIComponent(CFG.caller_id);
  return u;
}

// ---- calling hours: the lead's own clock, computed live in the browser ----
function localHour(lead){
  if (!lead._tz) return null;
  return +new Intl.DateTimeFormat("en-GB", {timeZone: lead._tz, hour: "2-digit", hourCycle: "h23"}).format(new Date());
}
function localClock(lead){
  return new Intl.DateTimeFormat("en-GB", {timeZone: lead._tz, hour: "2-digit", minute: "2-digit", hourCycle: "h23"}).format(new Date());
}
function outsideHours(lead){
  const h = localHour(lead), [from, to] = CFG.call_hours || [8, 21];
  return h !== null && (h < from || h >= to);
}
// Manual calls outside the window need an explicit confirm; auto-dial never places them.
function okToCall(lead){
  if (!outsideHours(lead)) return true;
  return confirm(`It's ${localClock(lead)} for this lead (${lead._tz}), outside calling hours. Call anyway?`);
}

function dial(lead){
  if (!lead || !lead._dial || lead._blocked) return;  // server already blanks _dial for these; belt and braces
  if (!okToCall(lead)) return;
  clearTimeout(timer);
  if (!EMBED){ window.location.href = dialUrl(lead); return; }
  const data = {number: lead._dial, autoDial: true};
  if (CFG.caller_id) data.callerId = CFG.caller_id;
  $("#zoom-embeddable-phone-iframe").contentWindow.postMessage({type: "zp-make-call", data}, ZOOM_ORIGIN);
  $("#zpanel").classList.remove("min");
  call = {active:true, connected:false, start:0, secs:0};
  setCallStatus("ringing", "Dialing…");
}

const fmtDur = s => Math.floor(s/60) + ":" + String(s%60).padStart(2,"0");

// ---- friendly dates: "5 minutes ago", "yesterday at 5pm", "14 Oct 2026" ----
const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const FOLLOWUP_STATUSES = ["Callback","Interested","No answer","Voicemail"];
const isoDay = d => d.getFullYear() + "-" + String(d.getMonth()+1).padStart(2,"0") + "-" + String(d.getDate()).padStart(2,"0");

// "2026-10-14" or "2026-10-14 00:20" (the server's local time) -> {d: Date, time: had a time part}
function parseStamp(s){
  const m = String(s||"").match(/^(\d{4})-(\d\d)-(\d\d)(?:[ T](\d\d):(\d\d))?/);
  if (!m) return null;
  return {d: new Date(+m[1], m[2]-1, +m[3], +(m[4]||0), +(m[5]||0)), time: m[4] !== undefined};
}

function friendly(s){
  const p = parseStamp(s);
  if (!p) return String(s||"");
  const d = p.d, now = new Date();
  const midnight = x => new Date(x.getFullYear(), x.getMonth(), x.getDate());
  const days = Math.round((midnight(d) - midnight(now)) / 864e5);
  const rel = days === 0 ? "today" : days === -1 ? "yesterday" : days === 1 ? "tomorrow" : "";
  const dateText = d.getDate() + " " + MONTHS[d.getMonth()] + " " + d.getFullYear();
  if (!p.time) return rel || dateText;
  const mins = Math.floor((now - d) / 6e4);
  if (mins === 0) return "just now";
  if (mins > 0 && mins < 60) return mins + (mins === 1 ? " minute ago" : " minutes ago");
  const h = d.getHours();
  const clock = (h % 12 || 12) + (d.getMinutes() ? ":" + String(d.getMinutes()).padStart(2,"0") : "") + (h < 12 ? "am" : "pm");
  return rel ? rel + " at " + clock : dateText;
}

// Friendly text, with the exact stored value on hover.
const whenHtml = s => s ? `<span title="${esc(s)}">${esc(friendly(s))}</span>` : "";

// Notes cell: "[2026-10-14 00:20] text" per call -> [{stamp, text}]; unstamped lines join the note above.
function parseNotes(text){
  const notes = [];
  for (const line of String(text||"").split("\n")){
    const m = line.match(/^\[(\d{4}-\d\d-\d\d \d\d:\d\d)\] ?(.*)$/);
    if (m) notes.push({stamp: m[1], text: m[2]});
    else if (notes.length) notes[notes.length-1].text += "\n" + line;
    else if (line.trim()) notes.push({stamp: "", text: line});
  }
  return notes;
}

// Newest note first.
function notesHtml(lead){
  return parseNotes(lead.Notes).reverse().map(n =>
    `<div class="note">${esc(n.text.trim())}${n.stamp ? ` <span class="muted">· ${whenHtml(n.stamp)}</span>` : ""}</div>`).join("");
}

// "Callback @ 2026-10-13 05:51" -> "Callback · yesterday at 5:51am"
function outcomeHtml(lead){
  const [what, stamp] = String(lead["Call Outcome"]||"").split(" @ ");
  return esc(what) + (stamp ? ` <span class="muted">· ${whenHtml(stamp)}</span>` : "");
}

function followupHtml(lead){
  const fu = lead["Follow-up Date"];
  if (!fu) return "";
  const due = FOLLOWUP_STATUSES.includes(lead.Status) && fu <= isoDay(new Date());
  return due ? `<span class="due">${whenHtml(fu)}</span>` : whenHtml(fu);
}

function setCallStatus(cls, text){
  const el = $("#callstatus"); if (!el) return;
  el.className = "callstatus " + cls; el.textContent = text;
}

window.addEventListener("message", e => {
  if (e.origin !== ZOOM_ORIGIN && !(DEBUG && e.origin === location.origin)) return;
  const type = e.data && e.data.type;
  if (!call.active) return;  // ignore calls we didn't start (e.g. incoming)
  if (type === "zp-call-ringing-event") setCallStatus("ringing", "Ringing…");
  else if (type === "zp-call-connected-event"){
    call.connected = true; call.start = Date.now();
    clearInterval(liveTimer);
    liveTimer = setInterval(() => setCallStatus("live", "● Live " + fmtDur(Math.round((Date.now()-call.start)/1000))), 1000);
    setCallStatus("live", "● Live 0:00");
  }
  else if (type === "zp-call-ended-event"){
    clearInterval(liveTimer);
    call.active = false;
    call.secs = call.connected ? Math.round((Date.now()-call.start)/1000) : 0;
    setCallStatus("ended", call.connected ? "Call ended · " + fmtDur(call.secs) : "No answer");
    if (!call.connected){
      pick("No answer");
      if ($("#autodial").checked) save();
    } else toast("Call ended - pick an outcome and press Enter");
  }
});

const ICONS = {
  ig: `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><defs><linearGradient id="igg" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="#feda75"/><stop offset=".35" stop-color="#fa7e1e"/><stop offset=".6" stop-color="#d62976"/><stop offset="1" stop-color="#4f5bd5"/></linearGradient></defs><rect width="24" height="24" rx="6" fill="url(#igg)"/><rect x="5" y="5" width="14" height="14" rx="4" fill="none" stroke="#fff" stroke-width="2"/><circle cx="12" cy="12" r="3.2" fill="none" stroke="#fff" stroke-width="2"/><circle cx="16.6" cy="7.4" r="1.1" fill="#fff"/></svg>`,
  fb: `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><circle cx="12" cy="12" r="12" fill="#1877F2"/><path d="M13.4 24v-8.4h2.8l.4-3.3h-3.2v-2.1c0-.9.3-1.6 1.6-1.6h1.7V5.7c-.3 0-1.3-.1-2.5-.1-2.5 0-4.2 1.5-4.2 4.3v2.4H7.2v3.3H10V24z" fill="#fff"/></svg>`,
  wa: `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><circle cx="12" cy="12" r="12" fill="#25D366"/><path d="M6.2 17.9l.9-3.2a6.1 6.1 0 1 1 2.3 2.3z" fill="none" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/><path d="M9.8 8.9c.2-.4.6-.4.8 0l.5 1.2c.1.2 0 .5-.2.7l-.4.4c.4.9 1.1 1.6 2 2l.4-.4c.2-.2.5-.3.7-.2l1.2.5c.4.2.4.6 0 .8-.6.6-1.5.8-2.3.4-1.6-.7-2.8-1.9-3.5-3.5-.3-.7-.1-1.5.3-1.9z" fill="#fff"/></svg>`,
};

// "IG: instagram.com/x; FB: facebook.com/y; WhatsApp link on site" -> logo links (compact = logos only)
function socialLinks(lead, compact){
  const s = String(lead["WhatsApp/IG Active"] || ""), out = [];
  const link = (href, icon, label, title) => `<a class="social" href="${esc(href)}" target="_blank" rel="noopener" title="${esc(title)}">${icon}${compact ? "" : `<span>${esc(label)}</span>`}</a>`;
  const ig = s.match(/instagram\.com\/([A-Za-z0-9_.]+)/i);
  if (ig) out.push(link("https://instagram.com/" + ig[1], ICONS.ig, "@" + ig[1], "Instagram: @" + ig[1]));
  const fb = s.match(/facebook\.com\/([A-Za-z0-9_.\-]+)/i);
  if (fb) out.push(link("https://facebook.com/" + fb[1], ICONS.fb, fb[1], "Facebook: " + fb[1]));
  const digits = (lead._dial || "").replace(/\D/g, "");
  if (/whatsapp/i.test(s) && digits) out.push(link("https://wa.me/" + digits, ICONS.wa, "Chat on WhatsApp", "WhatsApp " + lead._dial));
  return out.length ? `<span class="socials">${out.join("")}</span>` : "";
}

function ownerName(lead){
  const words = String(lead["Owner/Decision Maker"]||"").trim().split(/\s+/).filter(Boolean);
  if (!words.length) return "there";
  if (/^dr\.?$/i.test(words[0])) return "Dr. " + words[words.length-1];  // "Dr. Drew Dean" -> "Dr. Dean"
  return words[0];
}

function fill(text, lead){
  return String(text||"")
    .replaceAll("{business}", lead["Business Name"]||"your business")
    .replaceAll("{owner}", ownerName(lead))
    .replaceAll("{city}", lead["City"]||"")
    .replaceAll("{category}", lead["Category"]||"business")
    .replaceAll("{opener}", lead["Personalized Opener"]||"")
    .replaceAll("{my_name}", CFG.your_name||"")
    .replaceAll("{product}", CFG.product_name||"")
    .replaceAll("{calendly}", CFG.calendly_link||"");
}

async function load(){
  clearTimeout(timer);
  const q = new URLSearchParams({status:$("#status").value, country:$("#country").value, sheet:$("#sheet").value});
  const r = await fetch("/api/leads?"+q); const d = await r.json();
  queue = d.queue; scripts = d.scripts; idx = 0;
  $("#sheet").innerHTML = d.sheets.map(n=>`<option ${n===d.sheet?"selected":""}>${esc(n)}</option>`).join("");
  const sel = $("#country"), cur = sel.value;
  sel.innerHTML = '<option value="">All</option>' + d.countries.map(c=>`<option ${c===cur?"selected":""}>${esc(c)}</option>`).join("");
  showStats(d.stats);
  await refreshAll();
  render();
}

function showStats(s){
  $("#stats").innerHTML = `<span><b>${queue.length}</b> in queue</span><span><b>${s.called}</b>/${s.total} called</span><span><b>${s.connected}</b> connected</span><span><b>${s.interested}</b> interested</span><span><b>${s.booked}</b> booked</span>`;
}

// Every lead (ignores the queue filter) - used by search and the All leads table.
async function refreshAll(){
  const r = await fetch("/api/leads?" + new URLSearchParams({status:"all", country:$("#country").value, sheet:$("#sheet").value}));
  const d = await r.json();
  allLeads = d.queue.sort((a,b) => a._row - b._row);
  showStats(d.stats);
  if (!$("#tableview").hidden) renderTable();
}

function matchLead(lead, q){
  q = q.trim().toLowerCase();
  if (!q) return true;
  const qd = q.replace(/\D/g, "");
  if (qd.length >= 3 && (lead._dial||"").replace(/\D/g,"").includes(qd)) return true;
  const hay = ["ID","Business Name","City","Owner/Decision Maker","Category","Email","Address","Status"].map(k => lead[k]||"").join(" ").toLowerCase();
  return q.split(/\s+/).every(w => hay.includes(w));
}

// Show a lead's card: jump to it if it's in the queue, otherwise slot it in at the current position.
function openLead(lead){
  clearTimeout(timer);
  const i = queue.findIndex(l => l._row === lead._row);
  if (i >= 0) idx = i;
  else { queue.splice(Math.min(idx, queue.length), 0, lead); idx = Math.min(idx, queue.length-1); }
  closeResults(); closeTable();
  render();
  window.scrollTo(0, 0);
}

function next(){ clearTimeout(timer); idx = (idx + 1) % Math.max(queue.length,1); render(); }
function prev(){ clearTimeout(timer); idx = (idx - 1 + Math.max(queue.length,1)) % Math.max(queue.length,1); render(); }

// ---- search dropdown ----
function showResults(){
  const q = $("#search").value;
  results = q.trim() ? allLeads.filter(l => matchLead(l, q)).slice(0, 8) : [];
  resIdx = 0;
  const box = $("#results");
  if (!q.trim()){ box.hidden = true; return; }
  box.innerHTML = results.length
    ? results.map((l,i) => `<div data-i="${i}" class="${i===0?"on":""}"><b>${esc(l["Business Name"])}</b> · ${esc(l.City)} · ${esc(l._dial)}${l.Status ? ` · <span class="badge">${esc(l.Status)}</span>` : ""}</div>`).join("")
    : `<div class="muted">No leads match "${esc(q)}"</div>`;
  box.hidden = false;
  box.querySelectorAll("[data-i]").forEach(el => el.onmousedown = e => { e.preventDefault(); openLead(results[+el.dataset.i]); });
}
function closeResults(){ $("#results").hidden = true; $("#search").value = ""; $("#search").blur(); }
$("#search").addEventListener("input", showResults);
$("#search").addEventListener("blur", () => setTimeout(() => $("#results").hidden = true, 150));
$("#search").addEventListener("keydown", e => {
  const items = $("#results").querySelectorAll("[data-i]");
  if (e.key === "ArrowDown" || e.key === "ArrowUp"){
    e.preventDefault();
    if (!items.length) return;
    resIdx = (resIdx + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
    items.forEach((el,i) => el.classList.toggle("on", i === resIdx));
  } else if (e.key === "Enter"){ e.preventDefault(); if (results[resIdx]) openLead(results[resIdx]); }
  else if (e.key === "Escape") closeResults();
});

// ---- All leads table ----
const TCOLS = [["ID","ID"],["Business Name","Business"],["Category","Category"],["City","City"],["_dial","Phone"],["Status","Status"],
               ["Fit Score","Fit"],["Has Chat Widget","Website chat"],["_social","Social"],["Call Outcome","Last outcome"],["Notes","Notes"],["Follow-up Date","Follow-up"]];
const TIME_SORTED = ["Call Outcome","Notes"];  // sorted by their latest timestamp, newest first
function sortVal(lead, k){
  if (k === "Fit Score") return +(lead[k]||0);
  if (k === "Call Outcome") return String(lead[k]||"").split(" @ ")[1] || "";
  if (k === "Notes") return parseNotes(lead[k]).map(n => n.stamp).sort().pop() || "";
  return String(lead[k]||"").toLowerCase();
}
function cellHtml(lead, k){
  if (k === "_social") return socialLinks(lead, true);
  if (k === "Notes") return notesHtml(lead);
  if (k === "Call Outcome") return outcomeHtml(lead);
  if (k === "Follow-up Date") return followupHtml(lead);
  return esc(lead[k]);
}
function openTable(){
  $("#tsheet").textContent = $("#sheet").value;
  const sel = $("#tstatus"), cur = sel.value;
  const statuses = [...new Set(allLeads.map(l => l.Status).filter(Boolean))];
  sel.innerHTML = `<option value="">All statuses</option><option value="__new">New (not called)</option>` + statuses.map(s => `<option>${esc(s)}</option>`).join("");
  sel.value = cur;
  $("#tableview").hidden = false;
  renderTable();
  $("#tfilter").focus();
}
function closeTable(){ $("#tableview").hidden = true; }
function renderTable(){
  const q = $("#tfilter").value, st = $("#tstatus").value;
  let rows = allLeads.filter(l => matchLead(l, q) && (!st || (st === "__new" ? !l.Status || l.Status === "New" : l.Status === st)));
  if (sortKey){
    rows = rows.slice().sort((a,b) => {
      const x = sortVal(a, sortKey), y = sortVal(b, sortKey);
      return (x > y ? 1 : x < y ? -1 : 0) * sortDir;
    });
  }
  $("#tcount").textContent = `${rows.length} of ${allLeads.length} leads`;
  $("#thead").innerHTML = "<tr>" + TCOLS.map(([k,label]) => `<th data-k="${k}">${label}${sortKey===k ? (sortDir>0?" ▲":" ▼") : ""}</th>`).join("") + "<th></th></tr>";
  $("#thead").querySelectorAll("th[data-k]").forEach(th => th.onclick = () => {
    sortDir = sortKey === th.dataset.k ? -sortDir : (th.dataset.k === "Fit Score" || TIME_SORTED.includes(th.dataset.k) ? -1 : 1);
    sortKey = th.dataset.k; renderTable();
  });
  $("#tbody").innerHTML = rows.map(l => `<tr data-row="${l._row}">${TCOLS.map(([k]) => `<td${k === "Notes" ? ' class="notes"' : ""}>${cellHtml(l, k)}</td>`).join("")}
    <td style="white-space:nowrap">${l._dial ? `<button class="mini" data-a="call">📞 Call</button> ` : ""}<button class="ghost" data-a="open">Open</button></td></tr>`).join("");
  $("#tbody").querySelectorAll("button[data-a]").forEach(b => b.onclick = () => {
    const lead = allLeads.find(l => l._row === +b.closest("tr").dataset.row);
    if (b.dataset.a === "open") openLead(lead);
    else { openLead(lead); dial(lead); }
  });
}
$("#tfilter").addEventListener("input", renderTable);
$("#tstatus").addEventListener("change", renderTable);

function render(){
  const lead = queue[idx];
  if (!lead){ $("#main").innerHTML = `<div class="card empty">🎉 Queue empty. Change the filter or add more leads to leads.xlsx.</div>`; return; }
  chosen = "";
  clearInterval(liveTimer);
  call = {active:false, connected:false, start:0, secs:0};
  const callBtn = !lead._dial ? "" : EMBED
    ? `<button class="call" id="callbtn" onclick="dial(queue[idx])">Call</button>`
    : `<a class="call" id="callbtn" href="${dialUrl(lead)}" onclick="return okToCall(queue[idx])">Call</a>`;
  const country = (lead.Country||"").toUpperCase();
  const own = lead.Script ? {"This lead": lead.Script} : {};
  const tabs = Object.assign(own, scripts);
  const firstTab = lead.Script ? "This lead" : (tabs[country] !== undefined ? country : Object.keys(tabs)[0]);
  const f = (k) => lead[k] ? `<dt>${k}</dt><dd>${k==="Website" ? `<a href="${esc(/^https?:/.test(lead[k])?lead[k]:"https://"+lead[k])}" target="_blank">${esc(lead[k])}</a>` : esc(lead[k])}</dd>` : "";
  $("#main").innerHTML = `
    <section class="card">
      <div class="nav muted"><button onclick="prev()" title="Previous lead (←)">‹</button>Lead ${idx+1} of ${queue.length}<button onclick="next()" title="Next lead (→)">›</button><span>· row ${lead._row}</span></div>
      <p class="biz">${esc(lead["Business Name"])}</p>
      <div class="sub">${esc(lead.Category)} · ${esc(lead.City)}, ${esc(lead.Country)}
        ${lead["Fit Score"] ? `<span class="badge">Fit ${esc(lead["Fit Score"])}/5</span>`:""}
        ${lead.Status ? `<span class="badge">${esc(lead.Status)}</span>`:""}
        ${lead["DNC Checked"] ? `<span class="badge">DNC: ${esc(lead["DNC Checked"])}</span>`:""}
        ${lead._tz ? `<span class="badge ${outsideHours(lead) ? "late" : ""}" title="${esc(lead._tz)}">Local ${esc(localClock(lead))}${outsideHours(lead) ? " · outside calling hours" : ""}</span>` : ""}</div>
      <div class="phone">${lead._blocked ? `<span class="blocked">${esc(lead._blocked)} - not dialable</span>` : lead._bad_phone ? `<span class="blocked">Invalid number: ${esc(lead._bad_phone)}</span>` : esc(lead._dial || "no phone")}</div>
      <div class="row">
        ${callBtn}
        <button class="ghost" onclick="next()">Skip</button>
        <span id="callstatus" class="callstatus"></span>
      </div>
      ${EMBED ? `<div class="muted">Hang up with the red button in the Zoom panel →</div>` : ""}
      ${lead["Personalized Opener"] ? `<div class="opener"><b>Opener:</b> ${esc(lead["Personalized Opener"])}</div>`:""}
      <dl>
        ${f("Owner/Decision Maker")}${f("Alt Phone")}${f("Email")}${f("Website")}${f("Address")}
        ${f("Has Chat Widget")}<dt>Social</dt><dd>${socialLinks(lead) || '<span class="muted">None found on site</span>'}</dd>${f("Google Rating/Reviews")}
        ${f("Business Notes")}${lead.Notes ? `<dt>Notes</dt><dd>${notesHtml(lead)}</dd>` : ""}${lead["Follow-up Date"] ? `<dt>Follow-up</dt><dd>${followupHtml(lead)}</dd>` : ""}${lead["Call Outcome"] ? `<dt>Last outcome</dt><dd>${outcomeHtml(lead)}</dd>` : ""}
      </dl>
    </section>
    <section class="card">
      <div class="tabs" id="tabs">${Object.keys(tabs).map(k=>`<button data-k="${esc(k)}" class="${k===firstTab?"on":""}">${esc(k)}</button>`).join("")}</div>
      <div class="script" id="script"></div>
      <hr style="border:0;border-top:1px solid var(--line);margin:16px 0">
      <b>Outcome</b>
      <div class="outcomes">${OUTCOMES.map((o,i)=>`<button data-o="${o}">${i+1}. ${o}</button>`).join("")}</div>
      <textarea id="notes" placeholder="Notes from this call…"></textarea>
      <div class="row">
        <label>Follow-up <input type="date" id="followup"></label>
        <label><input type="checkbox" id="calsent"> Calendly link sent</label>
        <button class="ghost" onclick="copyCalendly()">Copy Calendly message</button>
      </div>
      <div class="row"><button class="primary" onclick="save()">Save & next (Enter)</button><span class="muted" id="countdown"></span></div>
    </section>`;
  const showTab = k => { $("#script").textContent = fill(tabs[k], lead); document.querySelectorAll("#tabs button").forEach(b=>b.classList.toggle("on", b.dataset.k===k)); };
  document.querySelectorAll("#tabs button").forEach(b => b.onclick = () => showTab(b.dataset.k));
  if (firstTab) showTab(firstTab);
  document.querySelectorAll(".outcomes button").forEach(b => b.onclick = () => pick(b.dataset.o));
}

function pick(o){
  chosen = o;
  document.querySelectorAll(".outcomes button").forEach(b=>b.classList.toggle("on", b.dataset.o===o));
  const fu = $("#followup");
  if (!fu.value && ["Callback","No answer","Voicemail","Interested"].includes(o)){
    const d = new Date(); d.setDate(d.getDate() + (o==="Callback"||o==="Interested" ? 1 : 2));
    fu.value = d.toISOString().slice(0,10);
  }
}

function copyCalendly(){
  const lead = queue[idx];
  const msg = fill(`Hi {owner}, great speaking with you! Here's the link to book your 30-min {product} demo: {calendly}\n— {my_name}, {product}`, lead);
  navigator.clipboard.writeText(msg).then(()=>{ $("#calsent").checked = true; toast("Calendly message copied"); });
}

async function save(){
  if (!chosen){ toast("Pick an outcome first (1-8)"); return; }
  const lead = queue[idx];
  const r = await fetch("/api/lead/"+lead._row, {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({sheet:$("#sheet").value, status:chosen, notes:$("#notes").value, followup:$("#followup").value, calendly_sent:$("#calsent").checked,
                          duration: call.secs ? fmtDur(call.secs) : ""})});
  const d = await r.json();
  if (!d.ok){ toast(d.error || "Save failed"); return; }
  toast("Saved: " + chosen);
  queue.splice(idx, 1);
  if (idx >= queue.length) idx = 0;
  render();
  refreshAll();
  if ($("#autodial").checked && queue[idx] && queue[idx]._dial){
    if (outsideHours(queue[idx])) toast("Auto-dial paused: next lead is outside calling hours");
    else startCountdown();
  }
}

function startCountdown(){
  let n = parseInt($("#delay").value) || 5;
  const tick = () => {
    const el = $("#countdown"); if (!el) return;
    if (n <= 0){ el.textContent = ""; dial(queue[idx]); return; }
    el.textContent = `Dialing next in ${n}s… (Esc to cancel)`; n--; timer = setTimeout(tick, 1000);
  };
  tick();
}

document.addEventListener("keydown", e => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;  // never react to Cmd+C, Cmd+1 etc.
  if (e.target.matches("textarea, input, select")) {
    if (e.key==="Enter" && e.target.id!=="notes" && !e.target.classList.contains("nosave") && e.target.closest("main")) save();
    if (e.key==="Escape" && e.target.id==="tfilter") closeTable();
    return;
  }
  if (!$("#tableview").hidden){ if (e.key==="Escape") closeTable(); return; }
  if (e.key==="/"){ e.preventDefault(); $("#search").focus(); }
  else if (e.key==="l" || e.key==="L") openTable();
  else if (e.key==="ArrowLeft") prev();
  else if (e.key==="ArrowRight") next();
  else if (e.key==="Escape"){
    clearTimeout(timer); const el=$("#countdown"); if(el) el.textContent="Auto-dial cancelled";
    if (EMBED && call.active) $("#zoom-embeddable-phone-iframe").focus();
  }
  else if (e.key==="Enter") save();
  else if (/^[1-8]$/.test(e.key)) pick(OUTCOMES[+e.key-1]);
});
$("#status").onchange = load; $("#country").onchange = load;
$("#sheet").onchange = () => { $("#country").value = ""; load(); };
if (EMBED){
  document.body.classList.add("embed");
  $("#zpanel").hidden = false;
  $("#zoom-embeddable-phone-iframe").src = ZOOM_ORIGIN + "/integration/phone/embeddablephone/home";
}
load();
