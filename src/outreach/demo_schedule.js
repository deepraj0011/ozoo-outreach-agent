// Email schedule screen + the lead panel's "Email schedule" tab (tasks 26-27).
// One schedule, shown as a simple list: each email has a type and "N days after the previous one",
// optionally "sooner if they show interest". The agent writes every email.
// Loaded before the main console script; uses its helpers ($, esc, openModal, toast, modalHead,
// renderPage, renderDrawer, empty, when) at call time. Server: schedule_console.py.
let schData = null, schDraft = null, schTab = 'plan', schUp = null, schDirty = false;
let schLead = null, schLeadId = null, schDateOpen = false;

(() => { const s = document.createElement('style');
  s.textContent = `.sch-in{border:1px solid #e2e8f0;border-radius:.5rem;padding:.4rem .55rem;font-size:.8125rem;background:#fff}
  .sch-num{width:3.75rem;text-align:center}`; document.head.appendChild(s); })();

const SCH_DAY = 86400000;
const schCopy = o => JSON.parse(JSON.stringify(o));
const schKinds = () => schData ? schData.options.kinds : [];
const schKindLabel = k => (schKinds().find(x => x.id === k) || {label: k || ''}).label;
const schDate = (iso, time = true) => iso ? new Date(iso).toLocaleString([], time
  ? {weekday:'short', day:'numeric', month:'short', hour:'2-digit', minute:'2-digit'} : {weekday:'short', day:'numeric', month:'short'}) : '—';
const schKindSel = (val, onchange) => `<select class="sch-in" onchange="${onchange}">${schKinds().map(k =>
  `<option value="${k.id}" ${val === k.id ? 'selected' : ''}>${esc(k.label)}</option>`).join('')}</select>`;
function schDefaultAt(days = 1) { const d = new Date(Date.now() + days * SCH_DAY), z = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}T11:00`; }

async function schLoad() {
  try { const r = await fetch('/demo/api/plans'); schData = await r.json(); }
  catch (_) { return; }
  if (!schDirty) schDraft = schCopy(schData.plan ? schData.plan.data : schData.options.starter);
  if (typeof renderPage === 'function') renderPage();
}
function schEdit(fn) { fn(schDraft); schDirty = true; renderPage(); }

// ------------------------------------------------------------------ the page
function pSchedule() {
  if (!schData || !schDraft) { schLoad(); return '<div class="text-sm text-slate-400">Loading…</div>'; }
  const tab = (id, label) => `<button onclick="schTab='${id}';${id === 'upcoming' ? 'schLoadUp();' : ''}renderPage()" class="px-3.5 py-2 text-sm font-medium rounded-lg ${schTab === id ? 'bg-white shadow-sm text-ink-950' : 'text-slate-500 hover:text-slate-800'}">${label}</button>`;
  return `<div class="space-y-5 fade max-w-5xl">
    <div class="bg-slate-100 rounded-xl p-1 inline-flex gap-1">${tab('plan', 'Schedule')}${tab('upcoming', 'Upcoming emails')}</div>
    ${schTab === 'upcoming' ? schUpcomingView() : schPlanView()}</div>`;
}

function schStatus() {
  if (!schData.supported) return ['bg-amber-50 border-amber-200 text-amber-800', `Not available in this mode (<b>${esc(schData.email_mode || 'none')}</b>). The schedule needs our own sender (Gmail or mailbox mode).`];
  if (!schData.plan) return ['bg-amber-50 border-amber-200 text-amber-800', 'Not saved yet. Until you press <b>Save</b>, new leads get the fixed days (0, 4, 9, 15).'];
  if (schDirty) return ['bg-amber-50 border-amber-200 text-amber-800', 'You have unsaved changes.'];
  return ['bg-emerald-50 border-emerald-200 text-emerald-800', `<b>On.</b> New leads follow this schedule${schData.following ? `; ${schData.following} lead${schData.following === 1 ? ' is' : 's are'} following it now` : ''}.`];
}

function schPlanView() {
  const d = schDraft, [cls, msg] = schStatus(), max = schData.options.max_emails;
  const rows = d.emails.map((e, i) => schEmailRow(e, i)).join('');
  const add = d.emails.length < max ? `<div class="relative pl-12 pb-6"><span class="absolute left-[15px] top-0 bottom-0 w-0.5 bg-slate-200"></span>
      <button class="btn btn-sm" onclick="schEdit(p => p.emails.push({kind:'auto', days:5}))">+ Add email ${d.emails.length + 1}</button></div>` : '';
  return `<div class="flex flex-wrap items-center gap-3 card px-4 py-3 ${cls}"><div class="text-sm flex-1">${msg}</div>
      <button class="btn btn-sm" onclick="if(confirm('Go back to the example schedule?')) schEdit(p => Object.assign(p, schCopy(schData.options.starter)))">Reset</button>
      <button class="btn btn-primary" onclick="schSave()" ${schData.supported ? '' : 'disabled'}>Save</button></div>
    <div class="grid grid-cols-1 lg:grid-cols-[1fr_300px] gap-5 items-start">
      <section class="card p-6">
        <h2 class="font-semibold">What each lead gets</h2>
        <p class="text-xs text-slate-500 mt-0.5 mb-6">You choose the type and the day. The agent writes each email for the lead's role and company.</p>
        ${rows}${add}
        <div class="relative pl-12"><span class="absolute left-0 top-0 h-8 w-8 rounded-full bg-slate-200 text-slate-600 grid place-items-center text-xs">■</span>
          <div class="text-sm font-medium pt-1">Stop</div>
          <div class="text-sm text-slate-600 mt-1"><input type="number" min="0" max="60" value="${d.end_days}" class="sch-in sch-num" onchange="schEdit(p => p.end_days = +this.value)"> days after the last email, the emails stop.</div>
          <div class="text-xs text-slate-400 mt-1">The lead moves to nurture. HOT leads go to sales.</div></div>
      </section>
      <aside class="space-y-4">${schExample()}
        <section class="card p-4 text-xs text-slate-600 space-y-1.5"><div class="font-semibold text-slate-800 text-sm">Always</div>
          <div>⚡ A reply, booking or unsubscribe stops the emails at once. The agent answers the reply (booking link, an answer, a polite close).</div>
          <div>📅 "Not now, try in March": one check-in email on that date.</div>
          <div>🏖️ Out of office: emails pause until they're back, then wait one more day.</div>
          <div>🕘 Emails go in working hours (${esc(schData.tz)}).</div>
          <div>👀 "Interest" = a real click, a page read on our site, or a HOT score. Bots and email opens don't count.</div>
          <div>✋ You can change any single lead from its panel (send now, another date, pause, stop).</div></section></aside>
    </div>`;
}

function schEmailRow(e, i) {
  const n = i + 1, last = i === schDraft.emails.length - 1;
  let when;
  if (i === 0) {
    const st = schDraft.start;
    when = `<select class="sch-in" onchange="schEdit(p => p.start = this.value === 'on_add' ? {mode:'on_add'} : this.value === 'days' ? {mode:'days', days:1} : {mode:'at', at: schDefaultAt()})">
        <option value="on_add" ${st.mode === 'on_add' ? 'selected' : ''}>as soon as the lead is added</option>
        <option value="days" ${st.mode === 'days' ? 'selected' : ''}>some days after the lead is added</option>
        <option value="at" ${st.mode === 'at' ? 'selected' : ''}>on a date</option></select>
      ${st.mode === 'days' ? `<input type="number" min="0" max="60" value="${st.days}" class="sch-in sch-num" onchange="schEdit(p => p.start.days = +this.value)"> days after` : ''}
      ${st.mode === 'at' ? `<input type="datetime-local" value="${esc(st.at)}" class="sch-in" onchange="schEdit(p => p.start.at = this.value)">` : ''}`;
  } else {
    when = `<input type="number" min="0" max="60" value="${e.days}" class="sch-in sch-num" onchange="schEdit(p => p.emails[${i}].days = +this.value)"> days after email ${i}`;
  }
  const fast = i === 0 ? '' : e.fast
    ? `<div class="mt-3 rounded-lg bg-amber-50 border border-amber-200 px-3 py-2.5 text-sm text-slate-700 flex flex-wrap items-center gap-2">
        <label class="flex items-center gap-2 font-medium"><input type="checkbox" checked onchange="schEdit(p => delete p.emails[${i}].fast)"> ⚡ Sooner if they show interest:</label>
        <input type="number" min="0" max="60" value="${e.fast.days}" class="sch-in sch-num" onchange="schEdit(p => p.emails[${i}].fast.days = +this.value)"> days, as
        ${schKindSel(e.fast.kind, `schEdit(p => p.emails[${i}].fast.kind = this.value)`)}
        <label class="flex items-center gap-2 text-slate-600 w-full pl-6"><input type="checkbox" ${e.fast.last ? 'checked' : ''} onchange="schEdit(p => { if (this.checked) p.emails[${i}].fast.last = true; else delete p.emails[${i}].fast.last; })"> and make it the last email (sales takes it from there)</label></div>`
    : `<label class="mt-3 flex items-center gap-2 text-sm text-slate-500 cursor-pointer"><input type="checkbox" onchange="schEdit(p => p.emails[${i}].fast = {days: Math.min(2, p.emails[${i}].days), kind: 'case_study'})"> ⚡ Send it sooner if they show interest</label>`;
  return `<div class="relative pl-12 pb-6">
    <span class="absolute left-[15px] top-8 bottom-0 w-0.5 bg-slate-200"></span>
    <span class="absolute left-0 top-0 h-8 w-8 rounded-full bg-brand-500 text-white grid place-items-center text-sm font-semibold">${n}</span>
    <div class="flex items-center gap-2 pt-1"><div class="text-sm font-medium flex-1">Email ${n}</div>
      <button class="btn btn-sm" onclick="schPreview(${n}, '${e.kind}')">Preview</button>
      ${last && i > 0 ? `<button class="btn btn-sm" title="Remove email ${n}" onclick="schEdit(p => p.emails.pop())">Remove</button>` : ''}</div>
    <div class="mt-2 grid grid-cols-[52px_1fr] gap-y-2 items-center text-sm text-slate-600">
      <span class="text-xs text-slate-400">Type</span><div>${schKindSel(e.kind, `schEdit(p => p.emails[${i}].kind = this.value)`)}</div>
      <span class="text-xs text-slate-400">Sends</span><div class="flex flex-wrap items-center gap-2">${when}</div></div>
    ${fast}</div>`;
}

function schExample() {
  // a lead added today, by calendar days (the real time also moves into working hours)
  const d = schDraft, day = n => new Date(Date.now() + n * SCH_DAY).toLocaleDateString([], {weekday:'short', day:'numeric', month:'short'});
  const st = d.start, first = st.mode === 'days' ? st.days : 0;
  const firstLabel = st.mode === 'at' && st.at ? new Date(st.at).toLocaleDateString([], {weekday:'short', day:'numeric', month:'short'}) : day(first);
  let at = st.mode === 'at' && st.at ? Math.max(0, Math.round((new Date(st.at) - Date.now()) / SCH_DAY)) : first;
  const lines = [`<li class="flex justify-between gap-2"><span>Email 1</span><span class="text-slate-500">${firstLabel}</span></li>`];
  d.emails.slice(1).forEach((e, i) => { const norm = at + e.days, fast = e.fast ? at + e.fast.days : null;
    lines.push(`<li class="flex justify-between gap-2"><span>Email ${i + 2}</span><span class="text-slate-500">${day(norm)}${fast !== null && fast <= norm ? `<span class="text-amber-700"> · ${day(fast)} ⚡${e.fast.last ? ' (last)' : ''}</span>` : ''}</span></li>`);
    at = norm; });
  lines.push(`<li class="flex justify-between gap-2 text-slate-400"><span>Stop</span><span>${day(at + d.end_days)}</span></li>`);
  return `<section class="card p-4"><div class="font-semibold text-sm">Example</div><div class="text-xs text-slate-500 mb-3">A lead added today</div>
    <ul class="text-sm space-y-1.5">${lines.join('')}</ul>
    ${d.emails.some(e => e.fast) ? '<div class="text-[11px] text-amber-700 mt-3">⚡ = the day it goes if they show interest</div>' : ''}</section>`;
}

async function schPost(url, body) {
  const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  let j = {}; try { j = await r.json(); } catch (_) {}
  return r.ok && j.ok ? j : {ok: false, error: j.error || ('HTTP ' + r.status)};
}
async function schSave() {
  const j = await schPost('/demo/api/plans/save', {data: schDraft});
  if (!j.ok) return toast(j.error, false);
  schDirty = false; toast('Saved. New leads follow this schedule.'); await schLoad();
}

async function schPreview(step, kind) {
  openModal(`${modalHead(`Email ${step} · ${esc(schKindLabel(kind))}`, 'As the agent would write it for a sample lead. Each real lead gets it for their own role and company.')}
    <div class="px-6 pt-4 flex items-center gap-2 text-xs text-slate-500">Sample lead's role <input id="schRole" value="Head of Collections" class="sch-in w-56">
      <button class="btn btn-sm" onclick="schPreviewLoad(${step}, '${kind}')">Show</button></div>
    <div id="schPv" class="p-6 overflow-y-auto thin text-sm text-slate-400">Loading…</div>`);
  schPreviewLoad(step, kind);
}
async function schPreviewLoad(step, kind) {
  const j = await schPost('/demo/api/plans/preview', {step, kind, role: $('schRole') ? $('schRole').value : ''});
  if (!$('schPv')) return;
  if (!j.ok) { $('schPv').textContent = j.error; return; }
  $('schPv').innerHTML = `<div class="text-xs text-slate-500">Subject</div><div class="font-medium text-slate-800 mb-3">${esc(j.subject)}</div>
    ${j.html ? '<iframe sandbox="" class="w-full h-[60vh] border border-slate-200 rounded-lg bg-white"></iframe>' : `<pre class="whitespace-pre-wrap text-slate-700">${esc(j.text)}</pre>`}`;
  const f = $('schPv').querySelector('iframe'); if (f) f.srcdoc = j.html;
}

// ------------------------------------------------------------------ upcoming emails
async function schLoadUp() {
  try { const r = await fetch('/demo/api/schedule/upcoming'); schUp = await r.json(); } catch (_) { schUp = {items: []}; }
  renderPage();
}
function schUpcomingView() {
  if (!schUp) { schLoadUp(); return '<div class="text-sm text-slate-400">Loading…</div>'; }
  if (!schUp.items.length) return `<div class="card">${empty('No emails are waiting to go out.')}</div>`;
  const days = {};
  schUp.items.forEach(x => { const k = x.due ? new Date(x.due).toDateString() : '?'; (days[k] = days[k] || []).push(x); });
  const tag = x => x.hold ? ['Paused', 'bg-amber-100 text-amber-800'] : x.decided ? ['Scheduled', 'bg-emerald-50 text-emerald-700']
    : x.fixed ? ['Fixed days', 'bg-slate-100 text-slate-500'] : ['Planned', 'bg-slate-100 text-slate-500'];
  return `<div class="flex items-center justify-between"><div class="text-xs text-slate-500">The next email of every lead. Click a lead to change it.</div>
      <button class="btn btn-sm" onclick="schLoadUp()">↻ Refresh</button></div>
    <div class="space-y-4">${Object.values(days).map(items => `<section class="card">
      <div class="px-4 py-2.5 border-b border-slate-100 text-sm font-semibold">${items[0].due ? schDate(items[0].due, false) : 'Not planned'} <span class="text-slate-400 font-normal">· ${items.length} email${items.length === 1 ? '' : 's'}</span></div>
      <ul class="divide-y divide-slate-100">${items.map(x => { const t = tag(x); return `<li class="px-4 py-2.5 hover:bg-slate-50 cursor-pointer flex items-center gap-3" onclick="openLead(${x.lead_id}).then(() => { drawerTab='schedule'; renderDrawer(); })">
        <div class="w-14 text-xs text-slate-500">${x.due ? new Date(x.due).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'}) : ''}</div>
        <div class="flex-1 min-w-0"><div class="text-sm font-medium truncate">${esc(x.name)} <span class="text-slate-400 font-normal">· ${esc(x.company)}</span></div>
          <div class="text-xs text-slate-500">Email ${x.step} · ${esc(schKindLabel(x.kind))}</div></div>
        <span class="text-[11px] rounded-full px-2 py-0.5 ${t[1]}">${t[0]}</span></li>`; }).join('')}</ul></section>`).join('')}</div>`;
}

// ------------------------------------------------------------------ lead panel: Email schedule tab
async function schLoadLead(id) {
  try { const r = await fetch(`/demo/api/lead/${id}/schedule`); schLead = r.ok ? await r.json() : null; schLeadId = id; }
  catch (_) { schLead = null; }
  if (openId === id && drawerTab === 'schedule') renderDrawer();
}
function schRow(icon, cls, title, sub, extra = '') {
  return `<li class="relative pl-10 pb-5"><span class="absolute left-[13px] top-7 bottom-0 w-0.5 bg-slate-200"></span>
    <span class="absolute left-0 top-0 h-7 w-7 rounded-full grid place-items-center text-xs font-semibold ${cls}">${icon}</span>
    <div class="text-sm font-medium pt-0.5">${title}</div><div class="text-xs text-slate-500 mt-0.5">${sub}</div>${extra}</li>`;
}
function dSchedule(l) {
  if (!schData) schLoad();
  if (schLeadId !== l.id || !schLead) { schLoadLead(l.id); return '<div class="text-sm text-slate-400">Loading…</div>'; }
  const v = schLead;
  const history = v.log.length ? `<details class="mt-4"><summary class="text-xs font-medium text-slate-500 cursor-pointer">History (${v.log.length})</summary>
    <ul class="mt-2 space-y-1.5">${v.log.map(x => `<li class="text-xs text-slate-600 flex justify-between gap-3"><span>${esc(x.what)}</span><span class="text-slate-400 whitespace-nowrap">${when(x.at)}</span></li>`).join('')}</ul></details>` : '';
  if (!v.on_plan) {
    const can = v.in_sequence && v.supported && v.schedule_saved && v.step_sent < 4;
    return `<div class="card p-4"><div class="text-sm font-medium">Fixed days</div>
      <div class="text-xs text-slate-500 mt-1">This lead was added before the email schedule was saved, so it gets emails on days 0, 4, 9 and 15.
      ${v.fixed && v.fixed.due ? ` Next: <b>email ${v.fixed.step}</b> around <b>${schDate(v.fixed.due)}</b>.` : ''}</div>
      ${can ? `<button class="btn btn-sm mt-3" onclick="schAct(${l.id}, 'use_plan')">Switch to the email schedule</button>` : ''}</div>${history}`;
  }
  const rows = v.path.map(p => p.step <= v.step_sent
    ? schRow('✓', 'bg-emerald-500 text-white', `Email ${p.step} · ${esc(schKindLabel(p.kind))}`,
      p.sooner ? '<span class="text-amber-700">⚡ Sent sooner: the lead showed interest</span>' : 'Sent') : '');
  if (v.interest_now && v.in_sequence && !v.ended)
    rows.push(`<li class="pl-10 pb-4 -mt-2 text-xs text-amber-700">👀 Showed interest since email ${v.step_sent} (a click, a page read or HOT)</li>`);
  const actions = hold => {
    const date = schDateOpen ? `<div class="flex flex-wrap items-center gap-2 mt-2"><input id="schAt" type="datetime-local" value="${schDefaultAt(2)}" class="sch-in">
        <button class="btn btn-sm btn-primary" onclick="schAct(${l.id}, 'move', {at: $('schAt').value})">Save date</button>
        <button class="btn btn-sm" onclick="schDateOpen=false;renderDrawer()">Cancel</button></div>` : '';
    return `<div class="flex flex-wrap gap-2 mt-3">
      <button class="btn btn-sm btn-primary" onclick="schAct(${l.id}, 'send_now')">Send now</button>
      <button class="btn btn-sm" onclick="schDateOpen=!schDateOpen;renderDrawer()">Change date</button>
      ${hold ? `<button class="btn btn-sm" onclick="schAct(${l.id}, 'resume')">Resume</button>` : `<button class="btn btn-sm" onclick="schAct(${l.id}, 'hold')">Pause</button>`}
      <button class="btn btn-sm text-rose-600" onclick="confirm('Stop all further emails to this lead?') && schAct(${l.id}, 'end')">Stop emails</button></div>${date}`;
  };
  const card = (title, sub, hold, changed) => `<div class="rounded-xl border-2 border-brand-200 bg-brand-50/40 p-3 mt-2">
      <div class="text-[11px] font-semibold text-brand-700">NEXT</div><div class="text-sm font-medium">${title}</div>
      <div class="text-xs text-slate-600 mt-0.5">${sub}</div>
      ${changed ? `<button class="text-[11px] text-brand-600 hover:underline mt-1" onclick="schAct(${l.id}, 'clear')">Undo my change</button>` : ''}${actions(hold)}</div>`;
  if (v.ended) rows.push(schRow('■', 'bg-slate-200 text-slate-600', 'Emails stopped', when(v.ended)));
  else if (!v.in_sequence) rows.push(schRow('■', 'bg-slate-200 text-slate-600', 'Emails stopped', 'The lead replied, booked, was paused or opted out.'));
  else if (v.pending) {
    const p = v.pending;
    rows.push(schRow(p.step, 'bg-brand-500 text-white', '', '', card(`Email ${p.step} · ${esc(schKindLabel(p.kind))}`,
      p.hold ? '<b class="text-amber-700">Paused.</b> Nothing goes until you resume.' : `${p.fast ? '⚡ Switched: the lead showed interest. ' : ''}Goes ${p.exact ? 'at' : 'in working hours from'} <b>${schDate(p.due)}</b>.${v.last_step === p.step ? ' The last email.' : ''}`, p.hold, false)));
  } else if (v.next && v.next.step) {
    const n = v.next, f = n.options.find(o => o.when === 'interested');
    const sub = n.hold ? '<b class="text-amber-700">Paused.</b> Nothing goes until you resume.'
      : `${n.fast ? '⚡ Sooner: the lead showed interest. ' : ''}Goes ${n.exact ? 'at' : 'in working hours from'} <b>${schDate(n.due)}</b>.`
        + (n.last ? ' The last email.' : '')
        + (f && !n.fast && !n.changed ? ` <span class="text-amber-700">⚡ ${schDate(f.due, false)} as ${esc(schKindLabel(f.kind))}${f.last ? ' (and the last)' : ''} if they show interest.</span>` : '');
    rows.push(schRow(n.step, 'bg-brand-500 text-white', '', '', card(`Email ${n.step} · ${esc(schKindLabel(n.kind))}`, sub, n.hold, n.changed)));
  } else if (v.next) rows.push(schRow('■', 'bg-slate-200 text-slate-600', 'Emails stop', `on ${schDate(v.next.due, false)}`));
  (v.later || []).forEach(x => rows.push(schRow(x.step, 'bg-slate-100 text-slate-500', `Email ${x.step} · ${esc(schKindLabel(x.kind))}`, `${x.days} days after email ${x.step - 1}`)));
  return `<ol>${rows.join('')}</ol>
    <div class="text-[11px] text-slate-400">A change here is only for this lead's next email. <a class="text-brand-600 hover:underline" href="#/schedule">Edit the schedule for everyone</a></div>${history}`;
}
async function schAct(id, action, extra) {
  const j = await schPost(`/demo/api/lead/${id}/schedule`, {action, ...(extra || {})});
  if (!j.ok) return toast(j.error, false);
  schDateOpen = false; toast(j.what); await schLoadLead(id);
  await fetch('/demo/api/run', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({action:'tick'})});
  setTimeout(() => schLoadLead(id), 1500);
}

// ------------------------------------------------------------------ import dialog: email 1 date
function schImportBar() {
  if (!schData || !schData.supported || !schData.plan) return '';
  return `<div class="flex flex-wrap items-center gap-3 text-xs text-slate-600 bg-slate-50 rounded-lg px-4 py-3">
    <span class="font-medium text-slate-700">Email 1 goes</span>
    <select id="impStartMode" class="sch-in" onchange="$('impStart').classList.toggle('hidden', this.value !== 'at')">
      <option value="">as the email schedule says</option><option value="at">on a date</option></select>
    <input id="impStart" type="datetime-local" value="${schDefaultAt()}" class="sch-in hidden"></div>`;
}
function schImportQuery() {
  if (!$('impStartMode') || $('impStartMode').value !== 'at' || !$('impStart').value) return '';
  return '&' + new URLSearchParams({start: $('impStart').value}).toString();
}

// loaded once at start, so the import dialog and the lead panel know whether a schedule is saved
setTimeout(() => schLoad(), 0);
