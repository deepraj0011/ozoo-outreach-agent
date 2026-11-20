// Drawers on the Campaign page: one exact email (HTML in a sandboxed iframe + text), and one lead's emails with
// the Pause / Resume, Send next now and Stop emails actions.
import { getEmail, leadAction } from '../../api.js';
import { h, mount } from '../../dom.js';
import { store } from '../../store.js';
import { Button, busy } from '../../components/Button.js';
import { Chip, StateChip } from '../../components/Chip.js';
import { confirmDialog } from '../../components/Dialog.js';
import { openDrawer } from '../../components/Drawer.js';
import { showError, showSnackbar } from '../../components/Snackbar.js';
import { engagementText, StopChip, stopText, whenText } from '../../components/StatusCell.js';
import { Tabs } from '../../components/Tabs.js';

const loading = () => h('div', { class: 'loading' }, 'Loading…');
const findLead = (id) => (store.get().board?.leads || []).find((l) => l.id === id);

/** The email as HTML (sandboxed: no scripts, no forms, no same-origin) or as plain text. */
export function EmailBody({ html, text }) {
  const host = h('div');
  const tabs = html ? [{ id: 'html', label: 'Email' }, { id: 'text', label: 'Text' }] : [{ id: 'text', label: 'Text' }];
  const show = (id) => mount(host,
    Tabs({ tabs, active: id, onChange: show, ariaLabel: 'Email view' }),
    h('div', { style: { marginTop: '12px' } }, id === 'html'
      ? h('iframe', { class: 'email-frame', sandbox: '', referrerpolicy: 'no-referrer', title: 'Email as the lead sees it', srcdoc: html })
      : h('pre', { class: 'email-text' }, text || '(no text)')));
  show(tabs[0].id);
  return host;
}

function emailContent(detail, tz) {
  const engagement = engagementText(detail);
  return h('div', { class: 'stack' },
    h('div', { class: 'email-subject' }, detail.subject || '(no subject)'),
    h('div', { class: 'meta' },
      Chip({ label: detail.status, tone: detail.status === 'sent' ? 'success' : 'primary' }),
      h('span', null, whenText({ ...detail, date: detail.when }, tz)),
      detail.template_name ? h('span', null, 'Template ', h('b', null, detail.template_name)) : null,
      h('span', null, engagement || 'No opens or clicks yet')),
    detail.html_is_current_template
      ? h('div', { class: 'alert alert--info' },
        'Subject and text are exactly what was sent. The designed view is drawn from the current template.')
      : null,
    EmailBody(detail));
}

/** Drawer with one lead's email number `emailNo`; `back` re-opens the lead drawer. */
export async function openEmailDrawer({ lead, emailNo, back }) {
  const drawer = openDrawer({ title: `${lead.name} · Email ${emailNo}`, content: loading() });
  const tz = store.get().board?.tz;
  const backBtn = back ? Button({ label: `← ${lead.name}`, variant: 'text', size: 'small', onClick: back }) : null;
  try {
    const detail = await getEmail(lead.id, emailNo);
    if (drawer.isOpen()) drawer.setContent(backBtn, emailContent(detail, tz));
  } catch (e) {
    if (drawer.isOpen()) drawer.setContent(backBtn, h('div', { class: 'alert alert--error', role: 'alert' }, e.message));
  }
}

function emailList(lead, tz, onOpen) {
  const names = new Map((store.get().board?.schedule || []).map((s) => [s.email_no, s.template_name]));
  if (!lead.emails.length) return h('div', { class: 'muted' }, 'No emails planned for this lead.');
  return h('ul', { class: 'email-list' }, lead.emails.map((cell) => h('li', null,
    h('button', { type: 'button', onClick: () => onOpen(cell.email_no) },
      h('b', null, `Email ${cell.email_no}`),
      h('span', { class: 'spacer' },
        h('div', null, whenText(cell, tz), cell.late && cell.status !== 'sent' ? ' ' : null,
          cell.late && cell.status !== 'sent' ? Chip({ label: 'late', tone: 'warning' }) : null),
        h('div', { class: 'tiny muted' }, [names.get(cell.email_no), engagementText(cell)].filter(Boolean).join(' · '))),
      h('span', { class: 'muted' }, '›')))));
}

const ACTION_DONE = {
  pause: 'Emails paused.', resume: 'Emails resumed.', stop: 'Emails stopped for this lead.',
};

async function runAction(button, lead, action, after) {
  if (action === 'stop') {
    const ok = await confirmDialog({ title: 'Stop emails?', danger: true, confirmLabel: 'Stop emails',
      message: `No more emails will go to ${lead.name}. This cannot be undone from here.` });
    if (!ok) return;
  }
  try {
    const out = await busy(button, () => leadAction(lead.id, action, store.get().by));
    const msg = action === 'send_now'
      ? (out && out.sent ? 'The next email is on its way.' : 'The next email will go in the next send slot.')
      : ACTION_DONE[action];
    showSnackbar(msg, 'success');
    await after();
  } catch (e) {
    showError(e);
  }
}

function actions(lead, after) {
  const stopped = Boolean(lead.stop_reason);
  const make = (label, action, variant) => {
    const btn = Button({ label, variant, disabled: stopped, onClick: () => runAction(btn, lead, action, after) });
    return btn;
  };
  return h('div', { class: 'row' },
    lead.paused ? make('Resume', 'resume', 'filled') : make('Pause', 'pause', 'outlined'),
    make('Send next now', 'send_now', 'outlined'),
    make('Stop emails', 'stop', 'danger'),
    stopped ? h('span', { class: 'muted small' }, stopText(lead, store.get().board?.tz)) : null);
}

/** Drawer with a lead's emails and actions; re-renders from fresh board data after each action. */
export function openLeadDrawer({ leadId, refresh }) {
  const lead = findLead(leadId);
  if (!lead) return;
  const drawer = openDrawer({ title: lead.name });
  const render = () => {
    const fresh = findLead(leadId);
    if (!fresh) { drawer.setContent(h('div', { class: 'alert alert--error' }, 'This lead is no longer on the board.')); return; }
    const tz = store.get().board.tz;
    const reopen = () => openLeadDrawer({ leadId, refresh });
    drawer.setContent(h('div', { class: 'stack' },
      h('div', null,
        h('div', { class: 'muted' }, [fresh.company, fresh.role].filter(Boolean).join(' · ')),
        h('div', { class: 'row', style: { marginTop: '8px' } }, StateChip(fresh.state),
          fresh.paused ? Chip({ label: 'Paused', tone: 'warning' }) : null, StopChip(fresh.stop_reason))),
      actions(fresh, async () => { await refresh(); if (drawer.isOpen()) render(); }),
      h('h3', null, 'Emails'),
      emailList(fresh, tz, (no) => openEmailDrawer({ lead: fresh, emailNo: no, back: reopen }))));
  };
  render();
}
