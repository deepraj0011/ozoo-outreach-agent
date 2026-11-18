// Email templates screen (task 22): browse + preview, write + edit, approve / switch off, send.
// Loaded before the main console script; uses its helpers ($, esc, openModal, closeModal, toast,
// modalHead, badge, renderPage) at call time. Server: template_console.py.
let tplData = null, tplTab = 'library', tplStep = 'all', tplSel = new Set(), tplSendKey = '', tplLeadQ = '';
let tplPv = null, tplEd = null, tplTimer = null;

const TPL_STATUS = {
  approved: ['Live', 'bg-emerald-50 text-emerald-700', 'bg-emerald-500'],
  draft: ['Draft · needs approval', 'bg-amber-50 text-amber-700', 'bg-amber-500'],
  off: ['Switched off', 'bg-slate-100 text-slate-500', 'bg-slate-400'],
};
const TPL_STEP_HELP = {
  1: 'The first note: short and personal, with the role\'s product screen and the demo button.',
  2: 'A real example or use case, one product screen and a demo button.',
  3: 'A low-effort way to start: free trial or a small pilot.',
  4: 'A short, polite last note that makes replying easy.',
};
const TPL_PH = ['first_name','company','topic','hook','value','proof','usecase','ask','signature','booking_link','angle_link',
  'case_study_link','pricing_link','trial_link','product_link','voice_link','chat_link','homepage_link','unsubscribe_link'];
const TPL_PH_HELP = {topic:'the role\'s topic', hook:'an opening line for the role', value:'what Ozoo does for the role', proof:'the NBFC result',
  usecase:'a scenario for the role', ask:'a one-line question', angle_link:'the product page for the role', signature:'your signature'};
const TPL_LINK_L = {homepage:'Home page', product:'Post-Call Analytics', voice:'Voice AI agents', chat:'Conversation AI agents', pricing:'Pricing',
  case_study:'Case study', trial:'Free trial (AWS)', booking:'Book a demo'};
const TPL_STARTER = {
  sequence: `Hi {first_name},\n\n{hook}\n\n> {usecase}\n\n[[image]]\n\n{ask}\n\n[[button]]\n\n{signature}\n\nSee how it works: {angle_link}\nUnsubscribe: {unsubscribe_link}`,
  manual: `Hi {first_name},\n\nA quick note from our side.\n\n[[button]]\n\n{signature}\n\nUnsubscribe: {unsubscribe_link}`,
};

const tplName = () => { try { return localStorage.getItem('oz_tpl_name') || ''; } catch (_) { return ''; } };
const setTplName = v => { try { localStorage.setItem('oz_tpl_name', v); } catch (_) {} };
const tplPill = st => { const s = TPL_STATUS[st] || TPL_STATUS.draft;
  return `<span class="inline-flex items-center gap-1.5 text-[11px] font-medium px-2 py-0.5 rounded-full whitespace-nowrap ${s[1]}"><span class="h-1.5 w-1.5 rounded-full ${s[2]}"></span>${s[0]}</span>`; };
const tplStepTag = t => t.kind === 'manual' ? 'One-off email' : `Email ${t.step}`;
const tplSnippet = t => esc((t.body || '').replace(/\[\[(image|button)\]\]\n?/g, '').replace(/^> /gm, '').replace(/\n{2,}/g, '\n'))
  .replace(/\{(\w+)\}/g, '<span class="text-violet-700">{$1}</span>');
const tplSampleRoles = () => Object.entries(tplData.options.sample_roles);

async function loadTemplates() {
  try { const r = await fetch('/demo/api/templates'); tplData = await r.json(); }
  catch (_) { toast('Could not reach the agent', false); return; }
  if (page === 'templates') { lastRendered = ''; renderPage(); }
}
async function tplPost(path, body) {
  try {
    const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
    return await r.json();
  } catch (_) { return {ok:false, error:'The agent did not answer.'}; }
}
function tplNeedName() {
  if (tplName().trim()) return false;
  toast('Enter your name first (top of the Templates page).', false);
  closeModal(); location.hash = '#/templates'; setTimeout(() => $('tplWho') && $('tplWho').focus(), 80);
  return true;
}

// ------------------------------------------------------------------ the page
function pTemplates() {
  if (!tplData) { loadTemplates(); return '<div class="text-sm text-slate-400">Loading templates…</div>'; }
  const tab = (id, label) => `<button onclick="tplTab='${id}';renderPage()" class="px-3.5 py-2 text-sm font-medium rounded-lg ${tplTab === id ? 'bg-white shadow-sm text-ink-950' : 'text-slate-500 hover:text-slate-800'}">${label}</button>`;
  const warn = tplData.approver_set ? '' : `<div class="card px-4 py-3 text-xs text-amber-800 bg-amber-50 border-amber-200">Approving, switching off and sending are turned off until <b>TEMPLATE_APPROVER_PASSCODE</b> is set in the env file. Drafts and previews work.</div>`;
  return `<div class="space-y-5 fade">
    <div class="flex flex-wrap items-center gap-3">
      <div class="bg-slate-100 rounded-xl p-1 flex gap-1">${tab('library', 'Template library')}${tab('send', 'Send an email')}</div>
      <label class="ml-auto flex items-center gap-2 text-xs text-slate-500">Your name
        <input id="tplWho" value="${esc(tplName())}" oninput="setTplName(this.value)" placeholder="e.g. Priya (Product)" class="w-48 border border-slate-200 rounded-lg px-3 py-2 text-sm bg-white"></label>
    </div>${warn}
    ${tplTab === 'send' ? tplSendView() : tplLibraryView()}</div>`;
}

function tplLibraryView() {
  const all = tplData.templates;
  const match = t => tplStep === 'all' || (tplStep === 'manual' ? t.kind === 'manual' : tplStep === 'custom' ? t.source === 'custom'
    : tplStep === 'draft' ? t.status === 'draft' : (t.kind === 'sequence' && String(t.step) === tplStep));
  const chip = (id, label) => { const n = all.filter(t => { const keep = tplStep; tplStep = id; const m = match(t); tplStep = keep; return m; }).length;
    return `<button onclick="tplStep='${id}';renderPage()" class="text-xs font-medium rounded-full px-3 py-1.5 border ${tplStep === id ? 'bg-ink-950 text-white border-ink-950' : 'bg-white border-slate-200 text-slate-600 hover:border-slate-400'}">${label} <span class="opacity-60">${n}</span></button>`; };
  const shown = all.filter(match);
  const groups = [1, 2, 3, 4].map(s => [`Email ${s}`, TPL_STEP_HELP[s], shown.filter(t => t.kind === 'sequence' && t.step === s)])
    .concat([['One-off emails', 'Sent by hand from "Send an email", as a reply in the lead\'s thread.', shown.filter(t => t.kind === 'manual')]])
    .filter(g => g[2].length);
  return `<div class="flex flex-wrap gap-2">${chip('all', 'All')}${[1, 2, 3, 4].map(s => chip(String(s), 'Email ' + s)).join('')}${chip('manual', 'One-off')}${chip('custom', 'Written here')}${chip('draft', 'Waiting for approval')}</div>
    ${groups.map(([title, help, items]) => `<section class="space-y-3">
      <div><h2 class="font-semibold text-sm">${title}</h2><p class="text-xs text-slate-500">${help}</p></div>
      <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">${items.map(tplCard).join('')}</div></section>`).join('')
      || empty('No templates match this filter.', `<button class="btn btn-primary mt-4" onclick="tplNew()">+ New template</button>`)}`;
}

function tplCard(t) {
  const i = tplData.templates.indexOf(t);
  const subj = t.subjects || [];
  const tags = [t.rich && t.rich.image ? 'Picture' : '', t.rich ? 'Button' : (t.kind === 'sequence' && t.step === 1 ? 'Plain text' : ''),
    t.angle && t.angle !== 'any' ? 'Only for ' + t.angle : 'All roles', t.source === 'builtin' ? 'Built-in' : 'Written here']
    .filter(Boolean).map(x => `<span class="rounded bg-slate-100 text-slate-600 px-1.5 py-0.5">${esc(x)}</span>`).join('');
  const who = t.approved_by ? `Approved by ${esc(t.approved_by)}` : t.updated_by ? `Last change by ${esc(t.updated_by)}` : '';
  const acts = [`<button class="btn btn-sm" onclick="tplPreview(${i})">Preview</button>`,
    t.source === 'custom' ? `<button class="btn btn-sm" onclick="tplEdit(${i}, false)">Edit</button>` : '',
    `<button class="btn btn-sm" onclick="tplEdit(${i}, true)">Duplicate</button>`,
    t.status === 'draft' ? `<button class="btn btn-sm btn-primary ml-auto" onclick="tplStatus(${i}, 'approved')">Approve</button>`
      : t.status === 'approved' ? `<button class="btn btn-sm ml-auto" onclick="tplStatus(${i}, 'off')">Switch off</button>`
      : `<button class="btn btn-sm ml-auto" onclick="tplStatus(${i}, 'approved')">Switch on</button>`].join('');
  return `<div class="card p-4 flex flex-col gap-2 ${t.status === 'off' ? 'opacity-60' : ''}">
    <div class="flex items-center gap-2"><span class="text-[11px] font-semibold text-brand-700 bg-brand-50 rounded px-1.5 py-0.5">${tplStepTag(t)}</span>
      <span class="text-[11px] text-slate-500 truncate">${t.kind === 'sequence' ? esc(tplData.options.variants[t.variant] || t.variant) : ''}</span><span class="ml-auto">${tplPill(t.status)}</span></div>
    <div class="font-medium text-sm">${esc(t.name)}</div>
    <div class="text-xs text-slate-500 truncate">Subject: <span class="text-slate-700">${esc(subj[0] || '')}</span>${subj.length > 1 ? ` <span class="text-slate-400">+${subj.length - 1} more</span>` : ''}</div>
    <div class="text-xs text-slate-500 whitespace-pre-line line-clamp-4 leading-relaxed">${tplSnippet(t)}</div>
    <div class="flex flex-wrap gap-1.5 text-[10px]">${tags}</div>
    ${who ? `<div class="text-[11px] text-slate-400">${who}</div>` : ''}
    <div class="flex gap-2 mt-auto pt-3 border-t border-slate-100">${acts}</div></div>`;
}

// ------------------------------------------------------------------ preview
function tplPreview(i) {
  const t = tplData.templates[i];
  tplPv = {key: t.key, draft: null, title: t.name, t, role: tplSampleRoles()[0][1], name: 'Asha Rao', company: 'Acme Finance', device: 'desktop', mode: 'html'};
  tplPvOpen();
}
function tplPvOpen() {
  const p = tplPv, t = p.t;
  const seg = (k, v, label) => `<button onclick="tplPv.${k}='${v}';tplPvOpen()" class="flex-1 px-2 py-1.5 text-xs rounded-md ${p[k] === v ? 'bg-white shadow-sm font-medium' : 'text-slate-500'}">${label}</button>`;
  openModal(`${modalHead(esc(p.title), 'Exactly what a lead gets. Links in the preview go straight to the pages (nothing is tracked).')}
    <div class="flex-1 min-h-0 grid grid-cols-1 md:grid-cols-[240px_1fr]">
      <div class="p-5 space-y-4 border-r border-slate-100 overflow-y-auto thin">
        <label class="block text-xs font-medium text-slate-600">Preview as<select id="pvRole" onchange="tplPv.role=this.value;tplPvLoad()" class="mt-1 w-full border border-slate-200 rounded-lg px-2 py-2 text-sm">
          ${tplSampleRoles().map(([a, r]) => `<option value="${esc(r)}" ${p.role === r ? 'selected' : ''}>${esc(r)}</option>`).join('')}</select></label>
        <label class="block text-xs font-medium text-slate-600">Lead name<input id="pvName" value="${esc(p.name)}" onchange="tplPv.name=this.value;tplPvLoad()" class="mt-1 w-full border border-slate-200 rounded-lg px-3 py-2 text-sm"></label>
        <label class="block text-xs font-medium text-slate-600">Company<input id="pvCo" value="${esc(p.company)}" onchange="tplPv.company=this.value;tplPvLoad()" class="mt-1 w-full border border-slate-200 rounded-lg px-3 py-2 text-sm"></label>
        <div class="bg-slate-100 rounded-lg p-1 flex">${seg('device', 'desktop', 'Desktop')}${seg('device', 'phone', 'Phone')}</div>
        <div class="bg-slate-100 rounded-lg p-1 flex">${seg('mode', 'html', 'Designed')}${seg('mode', 'text', 'Plain text')}</div>
        ${t ? `<div class="text-xs text-slate-500 space-y-1"><div>${tplPill(t.status)}</div><div>${tplStepTag(t)}${t.kind === 'sequence' ? ' · ' + esc(tplData.options.variants[t.variant]) : ''}</div></div>` : ''}
      </div>
      <div class="bg-slate-50 p-5 overflow-y-auto thin min-h-[60vh]"><div id="pvBox" class="mx-auto bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden" style="max-width:${p.device === 'phone' ? '390px' : '720px'}">
        <div class="px-5 py-3 border-b border-slate-100"><div class="text-[11px] text-slate-400">Subject</div><div id="pvSubj" class="text-sm font-semibold">…</div></div>
        <div id="pvBody" class="text-sm text-slate-400 p-5">Loading…</div></div></div>
    </div>
    <div class="px-6 py-4 border-t border-slate-100 flex justify-end gap-2">${t ? `<button class="btn" onclick="tplEdit(${tplData.templates.indexOf(t)}, ${t.source !== 'custom'})">${t.source === 'custom' ? 'Edit' : 'Duplicate and edit'}</button>` : ''}
      ${t && t.status === 'draft' ? `<button class="btn btn-primary" onclick="tplStatus(${tplData.templates.indexOf(t)}, 'approved')">Approve</button>` : ''}<button class="btn" onclick="closeModal()">Close</button></div>`, true);
  tplPvLoad();
}
async function tplPvLoad() {
  const p = tplPv;
  const j = await tplPost('/demo/api/templates/preview', {key: p.key, draft: p.draft, sample: {role: p.role, name: p.name, company: p.company}});
  if (!$('pvBody')) return;
  if (!j.ok) { $('pvBody').innerHTML = `<div class="text-rose-700 bg-rose-50 rounded-lg px-3 py-2">${esc(j.error)}</div>`; return; }
  $('pvSubj').textContent = j.subject;
  tplShow($('pvBody'), j, p.mode, p.device);
}
function tplShow(box, j, mode, device) {
  if (mode === 'text' || !j.html) {
    box.innerHTML = `${!j.html ? '<div class="text-[11px] text-slate-400 px-5 pt-4">Plain-text email (no design): best for the Primary inbox.</div>' : ''}<pre class="whitespace-pre-wrap font-sans text-[14px] leading-relaxed text-slate-800 p-5">${esc(j.text)}</pre>`;
    return;
  }
  box.innerHTML = '<iframe title="Email preview" sandbox="allow-popups allow-popups-to-escape-sandbox" class="w-full border-0 block" style="height:720px"></iframe>';
  box.firstChild.srcdoc = j.html;
}

// ------------------------------------------------------------------ editor
function tplNew(kind) {
  tplEd = {key: null, title: 'New template', d: {name: '', kind: kind || 'sequence', step: 2, variant: 'standard', angle: 'any', subject: '{topic}: a quick example',
    body: TPL_STARTER[kind || 'sequence'], rich: {image: 'angle', image_link: 'product', caption: 'Ozoo.ai, illustrative data. Click the image to see the product.', button: 'Book a 30-minute demo'}},
    role: tplSampleRoles()[0][1], device: 'desktop', mode: 'html'};
  tplEdOpen();
}
function tplEdit(i, copy) {
  const t = tplData.templates[i];
  tplEd = {key: copy ? null : t.key, title: copy ? 'New template (copy)' : 'Edit template',
    d: {name: copy ? `Copy of ${t.name}` : t.name, kind: t.kind, step: t.step || 2, variant: t.variant || 'standard', angle: t.angle || 'any',
      subject: (t.subjects || [''])[0], body: t.body, rich: Object.assign({}, t.rich || {})},
    role: tplSampleRoles()[0][1], device: 'desktop', mode: 'html'};
  tplEdOpen();
}
function tplEdOpen() {
  const e = tplEd, d = e.d, o = tplData.options;
  const sel = (id, opts, val) => `<select id="${id}" onchange="tplEdSync()" class="mt-1 w-full border border-slate-200 rounded-lg px-2 py-2 text-sm">${opts.map(([v, l]) => `<option value="${esc(v)}" ${String(val) === String(v) ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select>`;
  const field = (label, html, cls = '') => `<label class="block text-xs font-medium text-slate-600 ${cls}">${label}${html}</label>`;
  const img = (d.rich || {}).image || '';
  const thumb = (v, label, src) => `<button type="button" onclick="tplPickImg('${v}')" class="rounded-lg border-2 ${img === v ? 'border-brand-500' : 'border-transparent'} bg-slate-50 p-1 text-left">
    ${src ? `<img src="${src}" alt="" class="h-14 w-full object-cover rounded">` : `<div class="h-14 grid place-items-center text-[11px] text-slate-500 text-center px-1">${label}</div>`}
    <div class="text-[10px] text-slate-500 truncate mt-0.5">${label}</div></button>`;
  openModal(`${modalHead(e.title, 'Saved templates are drafts until the product manager approves them. Only approved templates are sent.')}
    <div class="flex-1 min-h-0 grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <form id="edForm" onsubmit="return false" class="p-5 space-y-3 overflow-y-auto thin border-r border-slate-100">
        ${field('Template name', `<input id="ed_name" value="${esc(d.name)}" oninput="tplEdSync()" placeholder="e.g. Collections: compliance example" class="mt-1 w-full border border-slate-200 rounded-lg px-3 py-2 text-sm">`)}
        <div class="grid grid-cols-2 gap-3">
          ${field('Type', sel('ed_kind', [['sequence', 'Sequence email (sent by the agent)'], ['manual', 'One-off email (sent by hand)']], d.kind), 'col-span-2')}
          ${d.kind === 'sequence' ? field('Which email', sel('ed_step', Object.entries(o.steps), d.step)) + field('Used when', sel('ed_variant', Object.entries(o.variants), d.variant)) : ''}
          ${field('Role group', sel('ed_angle', o.angles.map(a => [a, a === 'any' ? 'All roles' : a[0].toUpperCase() + a.slice(1) + ' roles only']), d.angle), 'col-span-2')}
        </div>
        ${field('Subject', `<input id="ed_subject" value="${esc(d.subject)}" oninput="tplEdSync()" class="mt-1 w-full border border-slate-200 rounded-lg px-3 py-2 text-sm">`)}
        <div class="text-xs font-medium text-slate-600">Email text
          <div class="flex flex-wrap gap-1 my-1.5">${TPL_PH.map(p => `<button type="button" title="${esc(TPL_PH_HELP[p] || '')}" onclick="tplIns('{${p}}')" class="text-[10px] rounded bg-violet-50 text-violet-700 px-1.5 py-0.5 hover:bg-violet-100">{${p}}</button>`).join('')}
            ${`<button type="button" onclick="tplIns('\\n[[image]]\\n')" class="text-[10px] rounded bg-brand-50 text-brand-700 px-1.5 py-0.5">[[image]]</button><button type="button" onclick="tplIns('\\n[[button]]\\n')" class="text-[10px] rounded bg-brand-50 text-brand-700 px-1.5 py-0.5">[[button]]</button>`}
            <button type="button" onclick="tplIns('\\n> Use case: ')" class="text-[10px] rounded bg-slate-100 text-slate-700 px-1.5 py-0.5">Use-case box</button><button type="button" onclick="tplIns('\\n- ')" class="text-[10px] rounded bg-slate-100 text-slate-700 px-1.5 py-0.5">List item</button></div>
          <textarea id="ed_body" oninput="tplEdSync()" rows="14" class="w-full border border-slate-200 rounded-lg px-3 py-2 text-[13px] leading-relaxed font-mono">${esc(d.body)}</textarea>
          <div class="text-[11px] text-slate-400 mt-1 font-normal">Blank line = new paragraph · "Label: {link}" = a text link · keep to one link besides Unsubscribe for the Primary inbox.</div></div>
        ${`<div class="space-y-3 rounded-xl border border-slate-200 p-3"><div class="text-xs font-semibold text-slate-700">Design</div>
          <div class="text-xs font-medium text-slate-600">Picture (goes where [[image]] is)<div class="grid grid-cols-4 gap-2 mt-1">
            ${thumb('', 'No picture', '')}${thumb('angle', 'The role\'s own screen (recommended)', '')}${o.images.map(n => thumb(n, n.replace(/-/g, ' '), `/img/${n}.jpg`)).join('')}</div></div>
          <div class="grid grid-cols-2 gap-3">
            ${field('A click on the picture opens', sel('ed_image_link', o.image_links.map(l => [l, TPL_LINK_L[l] || l]), (d.rich || {}).image_link || 'product'))}
            ${field('Button text (goes where [[button]] is)', `<input id="ed_button" value="${esc((d.rich || {}).button || '')}" oninput="tplEdSync()" placeholder="Book a 30-minute demo" class="mt-1 w-full border border-slate-200 rounded-lg px-3 py-2 text-sm">`)}
            ${field('Caption under the picture', `<input id="ed_caption" value="${esc((d.rich || {}).caption || '')}" oninput="tplEdSync()" class="mt-1 w-full border border-slate-200 rounded-lg px-3 py-2 text-sm">`, 'col-span-2')}
          </div></div>`}
        <div id="ed_err" class="text-xs"></div>
      </form>
      <div class="bg-slate-50 p-4 overflow-y-auto thin flex flex-col gap-3 min-h-[60vh]">
        <div class="flex flex-wrap items-center gap-2"><select onchange="tplEd.role=this.value;tplEdLoad()" class="border border-slate-200 rounded-lg px-2 py-1.5 text-xs bg-white">${tplSampleRoles().map(([a, r]) => `<option value="${esc(r)}" ${e.role === r ? 'selected' : ''}>As ${esc(r)}</option>`).join('')}</select>
          <div class="bg-slate-200/70 rounded-lg p-0.5 flex text-xs">${['desktop', 'phone'].map(v => `<button type="button" onclick="tplEd.device='${v}';tplEdOpenKeep()" class="px-2 py-1 rounded-md ${e.device === v ? 'bg-white shadow-sm' : 'text-slate-500'}">${v === 'desktop' ? 'Desktop' : 'Phone'}</button>`).join('')}</div>
          <div class="bg-slate-200/70 rounded-lg p-0.5 flex text-xs">${['html', 'text'].map(v => `<button type="button" onclick="tplEd.mode='${v}';tplEdLoad()" class="px-2 py-1 rounded-md ${e.mode === v ? 'bg-white shadow-sm' : 'text-slate-500'}">${v === 'html' ? 'Designed' : 'Plain text'}</button>`).join('')}</div></div>
        <div class="mx-auto w-full bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden" style="max-width:${e.device === 'phone' ? '390px' : '720px'}">
          <div class="px-4 py-2.5 border-b border-slate-100"><div class="text-[11px] text-slate-400">Subject</div><div id="edSubj" class="text-sm font-semibold">…</div></div>
          <div id="edBody" class="text-sm text-slate-400 p-5">Loading…</div></div>
      </div>
    </div>
    <div class="px-6 py-4 border-t border-slate-100 flex items-center gap-2"><span class="text-xs text-slate-400">Saving as ${esc(tplName() || '(enter your name on the page)')}</span>
      <button class="btn ml-auto" onclick="closeModal()">Cancel</button><button class="btn btn-primary" onclick="tplSave()">Save as draft</button></div>`, true);
  tplEdLoad();
}
function tplEdOpenKeep() { tplEdSync(true); tplEdOpen(); }
function tplEdSync(noLoad) {
  const d = tplEd.d, v = id => $(id) ? $(id).value : undefined;
  const kind = v('ed_kind') || d.kind, step = v('ed_step') || d.step;
  const relayout = kind !== d.kind || String(step) !== String(d.step);
  Object.assign(d, {name: v('ed_name') ?? d.name, kind, step: +step, variant: v('ed_variant') || d.variant, angle: v('ed_angle') || d.angle,
    subject: v('ed_subject') ?? d.subject, body: v('ed_body') ?? d.body});
  d.rich = Object.assign({}, d.rich, {image_link: v('ed_image_link') || (d.rich || {}).image_link, button: v('ed_button') ?? (d.rich || {}).button,
    caption: v('ed_caption') ?? (d.rich || {}).caption});
  if (relayout && kind !== 'manual' && +step === 1) { d.body = d.body.replace(/\n?\[\[(image|button)\]\]\n?/g, '\n'); d.rich.image = ''; }
  if (relayout) { tplEdOpen(); return; }
  if (!noLoad) { clearTimeout(tplTimer); tplTimer = setTimeout(tplEdLoad, 450); }
}
function tplPickImg(v) {
  tplEdSync(true); const d = tplEd.d; d.rich = Object.assign({}, d.rich, {image: v});
  if (v && !d.body.includes('[[image]]')) d.body = d.body.replace(/\n\n(\{ask\}|\[\[button\]\])/, '\n\n[[image]]\n\n$1');
  if (!v) d.body = d.body.replace(/\n?\[\[image\]\]\n?/g, '\n');
  tplEdOpen();
}
function tplIns(text) {
  const ta = $('ed_body'); if (!ta) return;
  const s = ta.selectionStart ?? ta.value.length, e = ta.selectionEnd ?? s;
  ta.value = ta.value.slice(0, s) + text + ta.value.slice(e); ta.focus(); ta.selectionStart = ta.selectionEnd = s + text.length; tplEdSync();
}
const tplDraft = () => { const d = tplEd.d, r = d.rich || {};
  return {name: d.name || 'Untitled', kind: d.kind, step: d.step, variant: d.variant, angle: d.angle, subject: d.subject, body: d.body,
    rich: {image: r.image || '', image_link: r.image_link || 'product', button: r.button || '', caption: r.caption || ''}}; };
async function tplEdLoad() {
  if (!tplEd) return;
  const j = await tplPost('/demo/api/templates/preview', {draft: tplDraft(), sample: {role: tplEd.role}});
  if (!$('edBody')) return;
  $('ed_err').innerHTML = j.ok ? '' : `<div class="text-rose-700 bg-rose-50 rounded-lg px-3 py-2">${esc(j.error)}</div>`;
  if (!j.ok) return;
  $('edSubj').textContent = j.subject;
  tplShow($('edBody'), j, tplEd.mode, tplEd.device);
}
async function tplSave() {
  tplEdSync(true);
  if (tplNeedName()) return;
  const j = await tplPost('/demo/api/templates/save', {draft: Object.assign(tplDraft(), {name: tplEd.d.name}), by: tplName(), key: tplEd.key});
  if (!j.ok) { $('ed_err').innerHTML = `<div class="text-rose-700 bg-rose-50 rounded-lg px-3 py-2">${esc(j.error)}</div>`; return; }
  closeModal(); toast('Saved as a draft. It goes live once the product manager approves it.');
  tplStep = 'draft'; await loadTemplates();
}

// ------------------------------------------------------------------ approve / switch off / send (passcode)
function tplAskPass(title, text, onOk) {
  openModal(`${modalHead(title, text)}
    <form id="passForm" class="p-6 space-y-3"><label class="block text-xs font-medium text-slate-600">Approver passcode
      <input id="passIn" type="password" autocomplete="off" class="mt-1 w-full border border-slate-200 rounded-lg px-3 py-2 text-sm"></label>
      <div id="passErr" class="text-xs text-rose-600"></div>
      <div class="flex justify-end gap-2"><button type="button" class="btn" onclick="closeModal()">Cancel</button><button class="btn btn-primary">Confirm</button></div></form>`);
  setTimeout(() => $('passIn') && $('passIn').focus(), 50);
  $('passForm').onsubmit = async ev => { ev.preventDefault(); const err = await onOk($('passIn').value); if (err) $('passErr').textContent = err; };
}
function tplStatus(i, status) {
  if (tplNeedName()) return;
  const t = tplData.templates[i];
  const what = status === 'off' ? 'Switch off' : t.status === 'off' ? 'Switch on' : 'Approve';
  const text = status === 'off' ? 'The agent stops using this version for new leads.' : 'The agent starts using this version for leads enrolled from now on.';
  tplAskPass(`${what}: ${esc(t.name)}`, text, async pass => {
    const j = await tplPost('/demo/api/templates/status', {key: t.key, status, by: tplName(), passcode: pass});
    if (!j.ok) return j.error;
    closeModal(); toast(status === 'off' ? 'Switched off.' : 'Approved: it is live.'); await loadTemplates(); return '';
  });
}

function tplSendView() {
  const live = tplData.templates.filter(t => t.status === 'approved').sort((a, b) => (a.kind === 'manual' ? 0 : 1) - (b.kind === 'manual' ? 0 : 1));
  if (!tplSendKey || !live.some(t => t.key === tplSendKey)) tplSendKey = live.length ? live[0].key : '';
  const t = live.find(x => x.key === tplSendKey);
  const q = tplLeadQ.toLowerCase();
  const leads = tplData.leads.filter(l => !q || [l.name, l.company, l.role].some(x => (x || '').toLowerCase().includes(q)));
  const ok = tplData.leads.filter(l => !l.reason);
  const max = tplData.options.max_send;
  return `<div class="grid grid-cols-1 xl:grid-cols-[420px_1fr] gap-5">
    <section class="card p-5 space-y-4 self-start">
      <div><h2 class="font-semibold text-sm">1. Choose an approved template</h2><p class="text-xs text-slate-500 mt-0.5">One-off emails first, then the sequence versions.</p></div>
      ${live.length ? `<select onchange="tplSendKey=this.value;renderPage()" class="w-full border border-slate-200 rounded-lg px-2 py-2 text-sm">${live.map(x => `<option value="${esc(x.key)}" ${x.key === tplSendKey ? 'selected' : ''}>${tplStepTag(x)} · ${esc(x.name)}</option>`).join('')}</select>` : '<div class="text-sm text-slate-500">No approved templates yet.</div>'}
      ${t ? `<div class="rounded-lg bg-slate-50 p-3 space-y-1"><div class="text-xs text-slate-500">Subject: <span class="text-slate-700">${esc(t.subjects[0] || '')}</span></div>
        <div class="text-xs text-slate-500 whitespace-pre-line line-clamp-6">${tplSnippet(t)}</div>
        <button class="btn btn-sm mt-2" onclick="tplPreview(${tplData.templates.indexOf(t)})">Preview</button></div>` : ''}
      <div class="text-xs text-slate-500 bg-brand-50/60 rounded-lg px-3 py-2.5 leading-relaxed">It goes as a <b>reply in each lead's email thread</b>, from the same mailbox, with the same link tracking and unsubscribe. Leads who unsubscribed or closed, or who haven't had an email yet, are skipped.</div>
    </section>
    <section class="card overflow-hidden">
      <div class="px-5 py-4 border-b border-slate-100 flex flex-wrap items-center gap-2"><h2 class="font-semibold text-sm">2. Choose leads</h2>
        <span class="text-xs text-slate-400">${ok.length} of ${tplData.leads.length} can receive it</span>
        <input id="tplLeadQ" value="${esc(tplLeadQ)}" oninput="tplLeadQ=this.value;renderPage()" placeholder="Search…" class="ml-auto w-48 border border-slate-200 rounded-lg px-3 py-1.5 text-sm">
        <button class="btn btn-sm" onclick="tplSel=new Set(tplData.leads.filter(l=>!l.reason).slice(0,${max}).map(l=>l.id));renderPage()">Select all that can</button>
        <button class="btn btn-sm" onclick="tplSel=new Set();renderPage()">Clear</button></div>
      <div class="max-h-[520px] overflow-y-auto thin divide-y divide-slate-100">${leads.map(l => `<label class="flex items-center gap-3 px-5 py-2.5 ${l.reason ? 'opacity-50' : 'hover:bg-slate-50 cursor-pointer'}">
        <input type="checkbox" ${tplSel.has(l.id) ? 'checked' : ''} ${l.reason ? 'disabled' : ''} onchange="this.checked?tplSel.add(${l.id}):tplSel.delete(${l.id});renderPage()">
        ${avatar(l.name, 'h-8 w-8 text-[11px]')}<div class="min-w-0 flex-1"><div class="text-sm font-medium truncate">${esc(l.name)} <span class="text-slate-400 font-normal">· ${esc(l.company)}</span></div>
        <div class="text-xs text-slate-500 truncate">${esc(l.reason ? 'Can\'t send: ' + l.reason : (l.role || '') + ' · ' + l.step_sent + ' of 4 emails sent')}</div></div>${badge(l.state)}</label>`).join('') || empty('No leads yet.')}</div>
      <div class="px-5 py-3 border-t border-slate-100 flex items-center gap-3 bg-slate-50"><span class="text-xs text-slate-500">${tplSel.size} selected · at most ${max} at a time</span>
        <button class="btn btn-primary ml-auto" ${!t || !tplSel.size || tplSel.size > max || !tplData.can_send ? 'disabled' : ''} onclick="tplSend()">Send to ${tplSel.size} lead${tplSel.size === 1 ? '' : 's'}</button></div>
    </section></div>`;
}
function tplSend() {
  if (tplNeedName()) return;
  const t = tplData.templates.find(x => x.key === tplSendKey), ids = [...tplSel];
  tplAskPass(`Send "${esc(t.name)}" to ${ids.length} lead${ids.length === 1 ? '' : 's'}`, 'Each gets it now, as a reply in their email thread.', async pass => {
    const j = await tplPost('/demo/api/templates/send', {key: t.key, lead_ids: ids, by: tplName(), passcode: pass});
    if (!j.ok) return j.error;
    const names = Object.fromEntries(tplData.leads.map(l => [l.id, l.name]));
    const sent = j.results.filter(r => r.ok).length;
    openModal(`${modalHead(`Sent to ${sent} of ${j.results.length}`, 'Each email is in the lead\'s activity as "Email sent from a template".')}
      <div class="p-6 space-y-2 overflow-y-auto thin">${j.results.map(r => `<div class="flex items-center gap-2 text-sm"><span class="${r.ok ? 'text-emerald-600' : 'text-rose-600'}">${r.ok ? '✓' : '✗'}</span>
        <span class="font-medium">${esc(names[r.lead_id] || 'Lead ' + r.lead_id)}</span>${r.reason ? `<span class="text-xs text-slate-500">${esc(r.reason)}</span>` : ''}</div>`).join('')}</div>
      <div class="px-6 py-4 border-t border-slate-100 flex justify-end"><button class="btn btn-primary" onclick="closeModal()">Done</button></div>`);
    tplSel = new Set(); await loadTemplates(); refresh(); return '';
  });
}
