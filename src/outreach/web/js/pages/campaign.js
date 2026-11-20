// Campaign (home): schedule strip, totals, filter tabs + search, and the board. Refreshes every 15 s while no
// drawer, dialog or unsaved schedule edit is open.
import { getBoard, pauseCampaign, startCampaign } from '../api.js';
import { h, mount } from '../dom.js';
import { store } from '../store.js';
import { Button, busy } from '../components/Button.js';
import { confirmDialog } from '../components/Dialog.js';
import { Card } from '../components/Card.js';
import { closeDrawer } from '../components/Drawer.js';
import { EmptyState } from '../components/EmptyState.js';
import { showError, showSnackbar } from '../components/Snackbar.js';
import { Tabs } from '../components/Tabs.js';
import { TextField } from '../components/TextField.js';
import { fmtDayTime, plural } from '../format.js';
import { BoardTable, countByFilter, FILTERS, filterLeads } from './campaign/boardTable.js';
import { openEmailDrawer, openLeadDrawer } from './campaign/drawers.js';
import { createScheduleStrip } from './campaign/scheduleStrip.js';

const REFRESH_MS = 15000;

const TOTALS = [
  ['leads', 'Leads'], ['sent', 'Sent'], ['today', 'Going today'], ['replied', 'Replied'],
  ['stopped', 'Stopped'], ['unsubscribed', 'Unsubscribed'],
];

function Totals(totals) {
  return h('div', { class: 'totals' }, TOTALS.map(([key, label]) => Card({ class: 'total' },
    h('div', { class: 'total__value' }, String(totals[key] ?? 0)),
    h('div', { class: 'total__label' }, label))));
}

/** "Not started — nothing is being sent. [Start sending]" / "Sending since 20 Nov, 4:10 pm [Pause all]". */
function CampaignBanner({ campaign, tz }, { onStart, onPause }) {
  if (!campaign) return null;
  if (campaign.running) {
    const pause = Button({ label: 'Pause all', variant: 'outlined', onClick: () => onPause(pause) });
    return h('div', { class: 'alert alert--success campaign-banner', role: 'status' },
      h('span', null, campaign.started_at ? `Sending since ${fmtDayTime(campaign.started_at, tz)}` : 'Sending'),
      pause);
  }
  const paused = Boolean(campaign.started_at);
  const start = Button({ label: paused ? 'Resume sending' : 'Start sending', variant: 'filled',
    onClick: () => onStart(start) });
  return h('div', { class: 'alert alert--info campaign-banner', role: 'status' },
    h('span', null, h('b', null, paused ? 'Paused' : 'Not started'), ' — nothing is being sent.'), start);
}

export function CampaignPage() {
  const strip = createScheduleStrip({ onSaved: () => refresh() });
  const bannerHost = h('div');
  const totalsHost = h('div');
  const tabsHost = h('div', { style: { flex: '1' } });
  const boardHost = h('div', null, h('div', { class: 'loading' }, 'Loading the board…'));
  const search = TextField({ label: 'Search', placeholder: 'Name or company', value: store.get().search,
    onInput: (v) => store.set({ search: v }) });
  const el = h('div', { class: 'stack' },
    h('div', { class: 'page-head' }, h('h1', null, 'Campaign'),
      Button({ label: 'Add leads', variant: 'outlined', onClick: () => { window.location.hash = '/leads'; } })),
    bannerHost, strip.el, totalsHost,
    h('div', { class: 'toolbar' }, tabsHost, search),
    boardHost);

  async function refresh({ quiet = false } = {}) {
    try {
      store.set({ board: await getBoard(), boardError: '' });
    } catch (e) {
      store.set({ boardError: e.message });
      if (!quiet) showError(e);
    }
  }

  async function onStart(button) {
    const n = store.get().board?.campaign?.leads ?? 0;
    const dirty = strip.isDirty();
    const ok = await confirmDialog({ title: `Start sending to ${plural(n, 'lead')}?`,
      message: (dirty ? 'Your schedule changes are saved first. ' : '')
        + 'Leads waiting to start begin now; each email then goes at its time.',
      confirmLabel: dirty ? 'Save & start' : 'Start sending' });
    if (!ok) return;
    try {
      await busy(button, async () => {
        if (dirty) await strip.saveDraft();
        await startCampaign(store.get().by);
      });
      showSnackbar('Sending started.', 'success');
      await refresh();
    } catch (e) {
      showError(e);
    }
  }

  async function onPause(button) {
    const ok = await confirmDialog({ title: 'Pause all sending?',
      message: 'No email goes to any lead until you start again. Emails waiting in the sender are held too.',
      confirmLabel: 'Pause all', danger: true });
    if (!ok) return;
    try {
      await busy(button, () => pauseCampaign(store.get().by));
      showSnackbar('All sending paused.', 'success');
      await refresh();
    } catch (e) {
      showError(e);
    }
  }

  const onCell = (lead, emailNo) => openEmailDrawer({ lead, emailNo });
  const onLead = (lead) => openLeadDrawer({ leadId: lead.id, refresh });

  function renderBoard(state) {
    const { board, filter, search: q, boardError } = state;
    if (!board) {
      if (boardError) mount(boardHost, h('div', { class: 'alert alert--error', role: 'alert' }, boardError));
      return;
    }
    const counts = countByFilter(board.leads, q);
    mount(tabsHost, Tabs({ ariaLabel: 'Filter leads', active: filter, onChange: (id) => store.set({ filter: id }),
      tabs: FILTERS.map((f) => ({ id: f.id, label: f.label, count: counts[f.id] })) }));
    mount(bannerHost, CampaignBanner(board, { onStart, onPause }));
    mount(totalsHost, Totals(board.totals));
    strip.load(board);
    if (!board.leads.length) {
      mount(boardHost, Card({}, EmptyState({ title: 'No leads on the schedule yet',
        text: 'Add a lead or import a CSV / Excel file, and their emails appear here.',
        action: Button({ label: 'Go to Leads', variant: 'filled', onClick: () => { window.location.hash = '/leads'; } }) })));
      return;
    }
    const leads = filterLeads(board.leads, filter, q);
    mount(boardHost,
      boardError ? h('div', { class: 'alert alert--error', role: 'alert' }, `Could not refresh: ${boardError}`) : null,
      leads.length
        ? BoardTable({ board, leads, onCell, onLead })
        : Card({}, EmptyState({ title: 'No leads match', text: 'Try another tab or search.' })));
  }

  // re-render only when what the board shows changed (not on overlay / editing counters)
  let last = {};
  const unsubscribe = store.subscribe((state) => {
    if (state.board === last.board && state.filter === last.filter && state.search === last.search
      && state.boardError === last.boardError) return;
    last = state;
    renderBoard(state);
  });

  const timer = setInterval(() => {
    const { overlays, editing } = store.get();
    if (overlays === 0 && !editing && !document.hidden) refresh({ quiet: true });
  }, REFRESH_MS);

  if (store.get().board) renderBoard(store.get());
  last = store.get();
  refresh();

  return {
    el,
    destroy() {
      clearInterval(timer);
      unsubscribe();
      strip.destroy();
      closeDrawer();
    },
  };
}
