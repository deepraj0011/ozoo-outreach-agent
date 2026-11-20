// P0 screens: Templates, Email schedule and the lead panel's Emails tab. Plain on purpose; the frontend
// is rebuilt later. All logic is behind /demo/api/p0/* (p0/console.py). Loaded before the main console
// script; uses its helpers ($, esc, toast, renderPage, renderDrawer, when, empty, run) at call time.
let p0On = false, p0Tpls = null, p0Ph = [], p0Sched = null, p0Example = [], p0Ed = null, p0Pv = null;
let p0Lead = null, p0LeadId = null;

function p0By() {
  let by = '';
  try { by = localStorage.getItem('p0By') || ''; } catch (_) {}
  if (!by) { by = (prompt('Your name (recorded with every change):', 'PM') || 'PM').trim() || 'PM';
    try { localStorage.setItem('p0By', by); } catch (_) {} }
  return by;
}
async function p0Post(url, body) {
  try {
    const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({...body, by: p0By()})});
    const j = await r.json();
    if (!r.ok) { toast(j.error || 'Failed', false); return null; }
    return j;
  } catch (_) { toast('Could not reach the agent', false); return null; }
}
async function p0Get(url) { try { const r = await fetch(url); return r.ok ? await r.json() : null; } catch (_) { return null; } }
async function p0Init() { const j = await p0Get('/demo/api/p0/status'); p0On = !!(j && j.on); if (p0On) renderPage(); }
p0Init();

const p0Day = iso => iso ? new Date(iso).toLocaleDateString([], {weekday:'short', day:'numeric', month:'short'}) : '';

// ------------------------------------------------------------------ Templates
async function p0LoadTpls() { const j = await p0Get('/demo/api/p0/templates'); if (j) { p0Tpls = j.templates; p0Ph = j.placeholders; renderPage(); } }
function pP0Templates() {
  if (!p0Tpls) { p0LoadTpls(); return '<div class="text-sm text-slate-400">Loading…</div>'; }
  const rows = p0Tpls.map(t => `<tr class="border-t"><td class="py-2 pr-3">${esc(t.name)}</td><td class="pr-3 text-xs">${esc(t.status)}</td>
    <td class="pr-3 text-xs text-slate-500">${esc(t.subject)}</td>
    <td class="text-right whitespace-nowrap"><button class="btn btn-sm" onclick="p0Edit(${t.id})">Edit</button>
    ${['draft','live','off'].filter(s => s !== t.status).map(s => `<button class="btn btn-sm" onclick="p0Status(${t.id},'${s}')">${s}</button>`).join(' ')}</td></tr>`).join('');
  return `<div class="space-y-4 max-w-5xl"><div><button class="btn btn-primary" onclick="p0Edit(null)">+ New template</button></div>
    <section class="card p-4"><table class="w-full text-sm"><tr class="text-left text-xs text-slate-400"><th>Name</th><th>Status</th><th>Subject</th><th></th></tr>${rows}</table></section>
    ${p0Ed ? p0Editor() : ''}</div>`;
}
function p0Edit(id) {
  const t = id == null ? {name: '', subject: '', body: ''} : p0Tpls.find(x => x.id === id);
  p0Ed = {id: id, name: t.name, subject: t.subject, body: t.body}; p0Pv = null; renderPage(); p0Preview();
}
function p0Editor() {
  const chips = p0Ph.map(p => `<button class="btn btn-sm" onclick="p0Chip('{${p}}')">{${esc(p)}}</button>`).join(' ');
  const pv = p0Pv ? (p0Pv.error ? `<div class="text-xs text-rose-600">${esc(p0Pv.error)}</div>`
    : `<div class="text-xs text-slate-500">Subject: <b>${esc(p0Pv.subject)}</b></div><iframe sandbox class="w-full h-96 border rounded mt-2" srcdoc="${esc(p0Pv.html)}"></iframe>`) : '';
  return `<section class="card p-4 space-y-2"><h2 class="font-semibold">${p0Ed.id == null ? 'New template' : 'Edit template'}</h2>
    <input id="p0Name" class="sch-in w-full" placeholder="Name" value="${esc(p0Ed.name)}" oninput="p0Ed.name=this.value">
    <input id="p0Subject" class="sch-in w-full" placeholder="Subject" value="${esc(p0Ed.subject)}" oninput="p0Ed.subject=this.value;p0PreviewSoon()">
    <textarea id="p0Body" rows="10" class="sch-in w-full" placeholder="Body" oninput="p0Ed.body=this.value;p0PreviewSoon()">${esc(p0Ed.body)}</textarea>
    <div class="text-xs text-slate-500">Placeholders: ${chips}</div>
    <div class="flex gap-2"><button class="btn btn-primary" onclick="p0SaveTpl()">Save</button><button class="btn" onclick="p0Ed=null;p0Pv=null;renderPage()">Close</button></div>
    ${pv}</section>`;
}
function p0Chip(text) { const b = $('p0Body'); if (!b) return; b.setRangeText(text, b.selectionStart, b.selectionEnd, 'end'); p0Ed.body = b.value; p0PreviewSoon(); }
let p0Timer = null;
function p0PreviewSoon() { clearTimeout(p0Timer); p0Timer = setTimeout(p0Preview, 400); }
async function p0Preview() {
  if (!p0Ed) return;
  try {
    const r = await fetch('/demo/api/p0/preview', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({draft: {name: p0Ed.name || 'x', subject: p0Ed.subject, body: p0Ed.body}})});
    p0Pv = await r.json();
  } catch (_) { return; }
  const box = document.querySelector('#page iframe'); if (box && p0Pv && !p0Pv.error) { box.srcdoc = p0Pv.html; } else renderPage();
}
async function p0SaveTpl() {
  const j = await p0Post('/demo/api/p0/templates', {id: p0Ed.id, name: p0Ed.name, subject: p0Ed.subject, body: p0Ed.body});
  if (j) { toast('Saved'); p0Ed = null; p0Pv = null; await p0LoadTpls(); }
}
async function p0Status(id, status) {
  const j = await p0Post(`/demo/api/p0/templates/${id}/status`, {status});
  if (j) { toast('Status changed'); await p0LoadTpls(); }
}

// ------------------------------------------------------------------ Email schedule
async function p0LoadSched() {
  const [s, t] = await Promise.all([p0Get('/demo/api/p0/schedule'), p0Get('/demo/api/p0/templates')]);
  if (!s || !t) return;
  p0Sched = s.rows.map(r => ({template_id: r.template_id, day: r.day})); p0Example = s.example; p0Tpls = t.templates; renderPage();
}
function pP0Schedule() {
  if (!p0Sched || !p0Tpls) { p0LoadSched(); return '<div class="text-sm text-slate-400">Loading…</div>'; }
  const live = p0Tpls.filter(t => t.status === 'live');
  const rows = p0Sched.map((r, i) => `<tr class="border-t"><td class="py-2 pr-3">${i + 1}</td>
    <td class="pr-3"><select class="sch-in" onchange="p0Sched[${i}].template_id=+this.value">${live.map(t => `<option value="${t.id}" ${t.id === r.template_id ? 'selected' : ''}>${esc(t.name)}</option>`).join('')}</select></td>
    <td class="pr-3">Day <input type="number" min="0" class="sch-in sch-num" value="${r.day}" onchange="p0Sched[${i}].day=+this.value"></td>
    <td class="text-xs text-slate-500">${esc((p0Example.find(e => e.email_no === i + 1) || {}).date || '')}</td>
    <td><button class="btn btn-sm" onclick="p0Sched.splice(${i},1);renderPage()">Remove</button></td></tr>`).join('');
  return `<div class="space-y-4 max-w-4xl"><section class="card p-4"><table class="w-full text-sm"><tr class="text-left text-xs text-slate-400"><th>#</th><th>Template</th><th>Day</th><th>Example date</th><th></th></tr>${rows}</table>
    <div class="flex gap-2 mt-3"><button class="btn" onclick="p0AddRow()">+ Add</button><button class="btn" onclick="p0LoadSched()">Example dates</button>
    <button class="btn btn-primary" onclick="p0SaveSched()">Save</button></div></section></div>`;
}
function p0AddRow() {
  const live = p0Tpls.filter(t => t.status === 'live'); if (!live.length) { toast('No live template', false); return; }
  const last = p0Sched.length ? p0Sched[p0Sched.length - 1].day : -5;
  p0Sched.push({template_id: live[0].id, day: last + 5}); renderPage();
}
async function p0SaveSched() {
  const j = await p0Post('/demo/api/p0/schedule', {rows: p0Sched});
  if (j) { p0Sched = j.rows.map(r => ({template_id: r.template_id, day: r.day})); p0Example = j.example; toast('Schedule saved'); renderPage(); }
}

// ------------------------------------------------------------------ lead panel: Emails tab
async function p0LoadLead(id) { p0LeadId = id; const j = await p0Get('/demo/api/p0/leads/' + id); if (j && p0LeadId === id) { p0Lead = j; renderDrawer(); } }
function dP0Emails(l) {
  if (p0LeadId !== l.id || !p0Lead) { p0LoadLead(l.id); return '<div class="text-sm text-slate-400">Loading…</div>'; }
  const next = p0Lead.rows.find(r => r.status === 'planned' || r.status === 'handed');
  const row = r => {
    let what;
    if (r.status === 'sent') what = `<span class="text-emerald-700">✓ Sent ${esc(p0Day(r.sent_at))}</span> · ${r.opens} opens / ${r.clicks} clicks`;
    else if (r.status === 'cancelled' || r.status === 'skipped') what = `<span class="text-slate-500">${esc(r.status)}${r.reason ? ': ' + esc(r.reason) : ''}</span>`;
    else what = `${r === next ? '<b>Next</b>' : 'To come'}: ${esc(r.due_date || '')}`;
    return `<li class="text-sm flex justify-between gap-3 py-1.5 border-t"><span>${r.email_no}. ${esc(r.template_name)} ${r.late ? '<span class="text-[10px] bg-amber-100 text-amber-800 rounded px-1.5">late</span>' : ''}</span><span>${what}</span></li>`;
  };
  const btn = (a, t) => `<button class="btn btn-sm" onclick="p0Act(${l.id},'${a}')">${t}</button>`;
  return `<div>${p0Lead.paused ? '<div class="text-xs bg-amber-50 text-amber-800 rounded px-3 py-2 mb-3">Paused</div>' : ''}
    <ul>${p0Lead.rows.map(row).join('') || '<li class="text-sm text-slate-400">No emails planned.</li>'}</ul>
    <div class="flex gap-2 mt-4">${p0Lead.paused ? btn('resume', 'Resume') : btn('pause', 'Pause')}${btn('send_now', 'Send now')}${btn('stop', 'Stop')}</div></div>`;
}
async function p0Act(id, action) {
  if (action === 'stop' && !confirm('Stop all remaining emails for this lead?')) return;
  const j = await p0Post(`/demo/api/p0/leads/${id}/${action}`, {});
  if (j) { toast(action === 'send_now' && !j.sent ? 'Nothing was sent' : 'Done'); await p0LoadLead(id); }
}
