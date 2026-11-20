// Templates: the list (#/templates) and the editor (#/templates/:id or #/templates/new) with placeholder chips
// and a live preview.
import { getTemplates, previewDraft, saveTemplate, setTemplateStatus } from '../api.js';
import { debounce, h, mount } from '../dom.js';
import { fmtDayTime } from '../format.js';
import { store } from '../store.js';
import { Button, busy } from '../components/Button.js';
import { Card } from '../components/Card.js';
import { Chip, TemplateStatusChip } from '../components/Chip.js';
import { EmptyState } from '../components/EmptyState.js';
import { showError, showSnackbar } from '../components/Snackbar.js';
import { Table } from '../components/Table.js';
import { TextField } from '../components/TextField.js';
import { EmailBody } from './campaign/drawers.js';

const PREVIEW_DELAY_MS = 400;
const go = (path) => { window.location.hash = path; };
const tz = () => store.get().board?.tz;
const errorBox = (message) => h('div', { class: 'alert alert--error', role: 'alert' }, message);

function usedBy(t) {
  return t.used_by.length ? t.used_by.map((n) => `Email ${n}`).join(', ') : '—';
}

export function TemplatesPage() {
  const body = h('div', { class: 'loading' }, 'Loading templates…');
  const el = h('div', { class: 'stack' },
    h('div', { class: 'page-head' }, h('h1', null, 'Templates'),
      Button({ label: 'New template', variant: 'filled', onClick: () => go('/templates/new') })),
    body);
  getTemplates().then(({ templates }) => {
    if (!templates.length) {
      mount(body, Card({}, EmptyState({ title: 'No templates yet', text: 'Write the first email.' })));
      return;
    }
    mount(body, Table({
      caption: 'Email templates',
      head: ['Name', 'Status', 'Used by', 'Updated'],
      rows: templates.map((t) => ({
        key: String(t.id),
        onClick: () => go(`/templates/${t.id}`),
        cells: [
          h('div', null, h('a', { href: `#/templates/${t.id}` }, t.name), h('div', { class: 'tiny muted' }, t.subject)),
          TemplateStatusChip(t.status), usedBy(t),
          h('span', { class: 'small' }, `${fmtDayTime(t.updated_at, tz())} by ${t.updated_by}`),
        ],
      })),
    }));
  }).catch((e) => mount(body, errorBox(e.message)));
  return { el };
}

/** Insert `{name}` at the cursor of `field` (input or textarea) and fire its input event. */
function insertAtCursor(field, name) {
  const token = `{${name}}`;
  const start = field.selectionStart ?? field.value.length;
  const end = field.selectionEnd ?? start;
  field.setRangeText(token, start, end, 'end');
  field.focus();
  field.dispatchEvent(new Event('input', { bubbles: true }));
}

function previewPanel() {
  const host = h('div', null, h('div', { class: 'muted small' }, 'The preview shows here as you type.'));
  return {
    el: Card({ title: 'Preview', class: 'editor__preview' }, h('div', { class: 'tiny muted', style: { marginBottom: '8px' } },
      'As a Head of Collections would get it. Links are not tracked here.'), host),
    show(out) {
      mount(host, h('div', { class: 'email-subject', style: { marginBottom: '8px' } }, out.subject), EmailBody(out));
    },
    error(message) { mount(host, errorBox(message)); },
  };
}

function editorForm(tpl, placeholders, onChange) {
  const name = TextField({ label: 'Name', value: tpl.name, maxLength: 120, onInput: onChange,
    hint: 'Only you see this, e.g. "Intro for collections heads".' });
  const subject = TextField({ label: 'Subject', value: tpl.subject, onInput: onChange });
  const text = TextField({ label: 'Email text', value: tpl.body, multiline: true, rows: 16, onInput: onChange,
    hint: 'An unsubscribe line is added if you leave {unsubscribe_link} out.' });
  let target = text.field;
  subject.field.addEventListener('focus', () => { target = subject.field; });
  text.field.addEventListener('focus', () => { target = text.field; });
  const chips = h('div', { class: 'placeholders' },
    placeholders.map((p) => Chip({ label: `{${p}}`, title: `Insert {${p}} at the cursor`,
      onClick: () => insertAtCursor(target, p) })));
  const values = () => ({ name: name.field.value, subject: subject.field.value, body: text.field.value });
  return { name, subject, text, chips, values };
}

function editorActions({ isNew, status, onSave, onLive, onOff }) {
  const saveBtn = Button({ label: isNew ? 'Save draft' : 'Save', variant: 'outlined', onClick: () => onSave(saveBtn) });
  const liveBtn = Button({ label: isNew ? 'Save and make live' : 'Make live', variant: 'filled', onClick: () => onLive(liveBtn) });
  const offBtn = Button({ label: 'Switch off', variant: 'text', onClick: () => onOff(offBtn) });
  return h('div', { class: 'row' }, saveBtn, isNew || status !== 'live' ? liveBtn : null,
    !isNew && status !== 'off' ? offBtn : null);
}

export function TemplateEditorPage({ id }) {
  const isNew = id === 'new';
  const el = h('div', { class: 'stack' }, h('div', { class: 'loading' }, 'Loading…'));
  const preview = previewPanel();
  const runPreview = debounce(async (draft) => {
    try {
      preview.show(await previewDraft(draft));
    } catch (e) {
      preview.error(e.message);
    }
  }, PREVIEW_DELAY_MS);

  getTemplates().then(({ templates, placeholders }) => {
    const tpl = isNew ? { name: '', subject: '', body: '', status: 'draft', used_by: [] }
      : templates.find((t) => t.id === Number(id));
    if (!tpl) { mount(el, errorBox('That template does not exist.'), Button({ label: 'Back to templates', onClick: () => go('/templates') })); return; }
    renderEditor(tpl, placeholders);
  }).catch((e) => mount(el, errorBox(e.message)));

  function renderEditor(tpl, placeholders) {
    const errorHost = h('div');
    const form = editorForm(tpl, placeholders, () => runPreview(form.values()));
    const fail = (e) => { mount(errorHost, errorBox(e.message)); showError(e); };

    async function save(button, status) {
      mount(errorHost);
      const by = store.get().by;
      const payload = isNew ? { ...form.values(), by, ...(status ? { status } : {}) } : { ...form.values(), id: tpl.id, by };
      try {
        const out = await busy(button, () => saveTemplate(payload));
        if (!isNew && status) await setTemplateStatus(tpl.id, status, by);
        showSnackbar(status === 'live' ? 'Saved and live.' : 'Saved.', 'success');
        if (isNew) go(`/templates/${out.id}`); else reload();
      } catch (e) { fail(e); }
    }
    async function switchOff(button) {
      mount(errorHost);
      try {
        await busy(button, () => setTemplateStatus(tpl.id, 'off', store.get().by));
        showSnackbar('Template switched off.', 'success');
        reload();
      } catch (e) { fail(e); }
    }

    mount(el,
      h('div', { class: 'page-head' },
        h('h1', null, isNew ? 'New template' : tpl.name), isNew ? null : TemplateStatusChip(tpl.status),
        tpl.used_by.length ? h('span', { class: 'muted small' }, `On the schedule as ${usedBy(tpl)}`) : null,
        Button({ label: 'All templates', variant: 'text', onClick: () => go('/templates') })),
      h('div', { class: 'editor' },
        Card({ class: 'stack' }, form.name, form.subject, form.text,
          h('div', null, h('div', { class: 'field__label', style: { marginBottom: '6px' } }, 'Placeholders (click to insert)'), form.chips),
          errorHost,
          editorActions({ isNew, status: tpl.status, onSave: (b) => save(b), onLive: (b) => save(b, 'live'), onOff: switchOff })),
        preview.el));
    if (form.values().subject && form.values().body) runPreview(form.values());
  }

  function reload() {
    getTemplates().then(({ templates, placeholders }) => {
      const tpl = templates.find((t) => t.id === Number(id));
      if (tpl) renderEditor(tpl, placeholders);
    }).catch(showError);
  }

  return { el, destroy: () => runPreview.cancel() };
}
