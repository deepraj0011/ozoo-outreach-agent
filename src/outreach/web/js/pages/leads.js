// Leads: add one lead (through the real /intake checks) and import a CSV / Excel file.
import { addLead, importLeads, SAMPLE_CSV_URL } from '../api.js';
import { h, mount } from '../dom.js';
import { Button, busy } from '../components/Button.js';
import { Card } from '../components/Card.js';
import { Chip } from '../components/Chip.js';
import { showError, showSnackbar } from '../components/Snackbar.js';
import { Table } from '../components/Table.js';
import { TextField } from '../components/TextField.js';

const OUTCOMES = {
  accepted: ['Added', 'success'], skipped_duplicate: ['Duplicate', 'warning'], skipped_suppressed: ['Opted out', 'warning'],
  rejected: ['Rejected', 'danger'], skipped: ['Rejected', 'danger'], error: ['Error', 'danger'],
  held_eu: ['Added, on hold', 'info'], held_verify: ['Added, on hold', 'info'], held_account: ['Added, on hold', 'info'],
};
const outcomeChip = (o) => { const [label, tone] = OUTCOMES[o] || [String(o), 'neutral']; return Chip({ label, tone }); };
const toBoard = () => h('a', { href: '#/campaign' }, 'See it on the Campaign board');

function AddLeadForm() {
  const f = {
    name: TextField({ label: 'Name *', required: true, autocomplete: 'off' }),
    company: TextField({ label: 'Company *', required: true }),
    role: TextField({ label: 'Role *', required: true, placeholder: 'Head of Collections' }),
    email: TextField({ label: 'Email', type: 'email' }),
    phone: TextField({ label: 'Phone', type: 'tel', hint: 'With +country code, or the country below is used.' }),
    country: TextField({ label: 'Country', placeholder: 'IN' }),
  };
  const consent = h('input', { type: 'checkbox' });
  const result = h('div');
  const submit = Button({ label: 'Add lead', variant: 'filled', type: 'submit' });
  const form = h('form', { class: 'stack', novalidate: true },
    h('div', { class: 'form-grid' }, Object.values(f)),
    h('label', { class: 'check' }, consent, 'WhatsApp opt-in: the lead agreed to get WhatsApp messages'),
    h('div', { class: 'row' }, submit), result);
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const lead = Object.fromEntries(Object.entries(f).map(([k, field]) => [k, field.field.value.trim()]).filter(([, v]) => v));
    lead.consent_whatsapp = consent.checked;
    try {
      const out = await busy(submit, () => addLead(lead));
      if (out.error) throw new Error(out.error);
      const added = out.outcome === 'accepted' || String(out.outcome).startsWith('held');
      mount(result, h('div', { class: `alert alert--${added ? 'success' : 'error'}` }, outcomeChip(out.outcome), ' ',
        out.lead_id ? `Lead #${out.lead_id}. ` : '', out.reason ? `Reason: ${out.reason}. ` : '', added ? toBoard() : null));
      if (added) { form.reset(); showSnackbar('Lead added.', 'success'); }
    } catch (err) { mount(result, h('div', { class: 'alert alert--error' }, err.message)); showError(err); }
  });
  return Card({ title: 'Add a lead' }, form);
}

function ImportCard() {
  const input = h('input', { type: 'file', accept: '.csv,.xlsx,.xlsm,.txt' });
  const out = h('div');
  let file = null;
  input.addEventListener('change', async () => {
    file = input.files[0] || null;
    if (!file) { mount(out); return; }
    mount(out, h('div', { class: 'loading' }, 'Reading the file…'));
    try {
      const p = await importLeads(file, { commit: false });
      const go = Button({ label: `Import ${p.ready} lead${p.ready === 1 ? '' : 's'}`, variant: 'filled', disabled: !p.ready,
        onClick: () => commit(go) });
      const problems = p.rows.filter((r) => r.problems.length);
      mount(out, h('div', { class: 'stack' },
        h('div', null, `${p.total} rows, ${p.ready} ready to import.`), go,
        problems.length ? Table({ head: ['Row', 'Name', 'Problem'],
          rows: problems.map((r) => ({ cells: [String(r.row), r.payload.name || '', r.problems.join(', ')] })) }) : null));
    } catch (e) { mount(out, h('div', { class: 'alert alert--error' }, e.message)); }
  });
  async function commit(button) {
    try {
      const r = await busy(button, () => importLeads(file, { commit: true }));
      const s = r.summary || {};
      const added = (s.accepted || 0) + (s.held_eu || 0) + (s.held_verify || 0) + (s.held_account || 0);
      const dup = s.skipped_duplicate || 0;
      const rejected = (s.rejected || 0) + (s.skipped || 0) + (s.error || 0) + (s.skipped_suppressed || 0);
      mount(out, h('div', { class: 'stack' },
        h('div', { class: 'alert alert--success' }, `${added} added, ${dup} duplicates, ${rejected} rejected. `, toBoard()),
        Table({ head: ['Row', 'Name', 'Result', 'Reason'],
          rows: r.results.map((x) => ({ cells: [String(x.row), x.name || '', outcomeChip(x.outcome), x.reason || ''] })) })));
      showSnackbar(`${added} leads added.`, 'success');
      input.value = '';
    } catch (e) { showError(e); }
  }
  return Card({ title: 'Import CSV / Excel' }, h('div', { class: 'stack' },
    h('div', { class: 'muted small' }, 'Columns: Name, Company, Role, Email, Phone, Country, WhatsApp opt-in. ',
      h('a', { href: SAMPLE_CSV_URL, download: 'leads_template.csv' }, 'Download the sample CSV')),
    input, out));
}

export function LeadsPage() {
  return { el: h('div', { class: 'stack' }, h('div', { class: 'page-head' }, h('h1', null, 'Leads')),
    h('div', { class: 'leads-grid' }, AddLeadForm(), ImportCard())) };
}
