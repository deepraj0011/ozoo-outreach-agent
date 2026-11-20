// Schedule strip: one card per email (template + day), "+ Add email", Save / Cancel while edited, and under each
// card the date a lead added today would get it (POST schedule/example with the unsaved rows).
import { exampleDates, saveSchedule } from '../../api.js';
import { debounce, h, mount } from '../../dom.js';
import { fmtCalendarDay } from '../../format.js';
import { store } from '../../store.js';
import { Button, busy } from '../../components/Button.js';
import { Card } from '../../components/Card.js';
import { Select } from '../../components/Select.js';
import { TextField } from '../../components/TextField.js';
import { showError, showSnackbar } from '../../components/Snackbar.js';

const MAX_EMAILS = 10;
const DEFAULT_GAP_DAYS = 3;
const EXAMPLE_DELAY_MS = 300;

/** A day as the API wants it: a number when it is one, otherwise the raw text (the API names the problem). */
const dayValue = (raw) => (/^\d+$/.test(String(raw).trim()) ? Number(raw) : String(raw));
const sameRows = (a, b) => JSON.stringify(a) === JSON.stringify(b);

function templateOptions(templates, currentId) {
  return templates
    .filter((t) => t.status === 'live' || t.id === Number(currentId))
    .map((t) => ({ value: t.id, label: t.status === 'live' ? t.name : `${t.name} (${t.status})`, disabled: t.status !== 'live' }));
}

export function createScheduleStrip({ onSaved }) {
  let templates = [];
  let saved = [];
  let draft = [];
  let dates = [];
  let savedDates = [];
  let dateEls = [];
  let error = '';
  const cardsHost = h('div', { class: 'schedule' });
  const actionsHost = h('div');
  const el = h('section', { 'aria-label': 'Email schedule' },
    h('div', { class: 'page-head' }, h('h2', null, 'Email schedule'),
      h('span', { class: 'muted small' }, 'Day 0 is the day a lead is added. Emails go inside working hours.')),
    cardsHost, actionsHost);

  const isDirty = () => !sameRows(draft, saved);

  function renderDate(i) {
    const date = dates[i];
    mount(dateEls[i], date ? 'A lead added today gets it ' : 'Enter a day (0 or more)',
      date ? h('b', null, fmtCalendarDay(date)) : null);
  }

  const refreshExample = debounce(async () => {
    const rows = draft;
    try {
      const out = await exampleDates(rows.map((r) => ({ template_id: Number(r.template_id), day: dayValue(r.day) })));
      if (rows !== draft) return; // edited again meanwhile: the next call answers
      dates = out.example.map((e) => e.date);
      dates.forEach((_, i) => dateEls[i] && renderDate(i));
    } catch (e) {
      showError(e);
    }
  }, EXAMPLE_DELAY_MS);

  function setDraft(next, { rerender = false } = {}) {
    draft = next;
    error = '';
    store.set({ editing: isDirty() });
    if (rerender) renderCards();
    renderActions();
    refreshExample();
  }

  const update = (i, patch) => draft.map((r, j) => (j === i ? { ...r, ...patch } : r));

  function card(row, i) {
    const dateEl = h('div', { class: 'sched-card__date' });
    dateEls[i] = dateEl;
    const remove = Button({ label: 'Remove', variant: 'text', size: 'small', disabled: draft.length <= 1,
      onClick: () => setDraft(draft.filter((_, j) => j !== i), { rerender: true }) });
    const select = Select({ value: row.template_id, ariaLabel: `Email ${i + 1} template`,
      options: templateOptions(templates, row.template_id),
      onChange: (v) => setDraft(update(i, { template_id: Number(v) })) });
    const day = TextField({ label: 'Day', type: 'number', min: 0, value: String(row.day),
      onInput: (v) => setDraft(update(i, { day: dayValue(v) })) });
    const node = Card({ class: 'sched-card' },
      h('div', { class: 'sched-card__head' }, h('span', { class: 'sched-card__name' }, `Email ${i + 1}`), remove),
      select, day, dateEl);
    renderDate(i);
    return node;
  }

  function addCard() {
    const last = draft[draft.length - 1];
    const firstLive = templates.find((t) => t.status === 'live');
    const lastDay = last && /^\d+$/.test(String(last.day)) ? Number(last.day) : -DEFAULT_GAP_DAYS;
    const row = { template_id: last ? last.template_id : (firstLive ? firstLive.id : ''), day: lastDay + DEFAULT_GAP_DAYS };
    return Card({ class: 'sched-card sched-card--add' },
      Button({ label: '+ Add email', variant: 'text', onClick: () => setDraft([...draft, row], { rerender: true }) }));
  }

  function renderCards() {
    dateEls = [];
    mount(cardsHost, draft.map(card), draft.length < MAX_EMAILS ? addCard() : null);
  }

  async function save(button) {
    try {
      const rows = draft.map((r) => ({ template_id: Number(r.template_id), day: dayValue(r.day) }));
      await busy(button, () => saveSchedule(rows, store.get().by));
      store.set({ editing: false });
      showSnackbar('Schedule saved. Leads still waiting follow the new days.', 'success');
      await onSaved();
    } catch (e) {
      error = e.message;
      renderActions();
      showError(e);
    }
  }

  function cancel() {
    refreshExample.cancel();
    draft = saved;
    error = '';
    store.set({ editing: false });
    dates = savedDates;
    renderCards();
    renderActions();
  }

  function renderActions() {
    if (!isDirty() && !error) { mount(actionsHost); return; }
    const saveBtn = Button({ label: 'Save schedule', variant: 'filled', onClick: () => save(saveBtn) });
    mount(actionsHost, h('div', { class: 'sched-actions' },
      isDirty() ? saveBtn : null,
      isDirty() ? Button({ label: 'Cancel', variant: 'text', onClick: cancel }) : null,
      isDirty() ? h('span', { class: 'muted small' }, 'Unsaved changes') : null,
      error ? h('div', { class: 'alert alert--error', role: 'alert' }, error) : null));
  }

  return {
    el,
    /** New board data; ignored while the PM has unsaved edits. */
    load(board) {
      if (store.get().editing) return;
      const nextSaved = board.schedule.map((r) => ({ template_id: r.template_id, day: r.day }));
      const nextDates = board.example.map((e) => e.date);
      const unchanged = sameRows(nextSaved, saved) && sameRows(nextDates, savedDates)
        && sameRows(board.templates, templates) && cardsHost.childElementCount > 0;
      if (unchanged) return; // keep focus in the strip across auto-refreshes
      templates = board.templates;
      saved = nextSaved;
      draft = saved;
      savedDates = nextDates;
      dates = savedDates;
      renderCards();
      renderActions();
    },
    destroy() {
      refreshExample.cancel();
      store.set({ editing: false });
    },
  };
}
