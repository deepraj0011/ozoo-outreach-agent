// Board table: one row per lead, one column per scheduled email. A stopped lead's remaining cells merge into
// one span that says why ("Replied 22 Nov — emails stopped").
import { h } from '../../dom.js';
import { fmtCalendarDay } from '../../format.js';
import { Chip, StateChip } from '../../components/Chip.js';
import { StatusCell, StopChip, stopText } from '../../components/StatusCell.js';
import { Table } from '../../components/Table.js';

export const FILTERS = Object.freeze([
  { id: 'all', label: 'All', test: () => true },
  { id: 'today', label: 'Going today', test: (lead) => lead.emails.some((c) => c.status === 'today') },
  { id: 'replied', label: 'Replied', test: (lead) => lead.stop_reason === 'replied' },
  { id: 'stopped', label: 'Stopped', test: (lead) => Boolean(lead.stop_reason) },
]);

function matchesSearch(lead, search) {
  const q = search.trim().toLowerCase();
  if (!q) return true;
  return `${lead.name || ''} ${lead.company || ''}`.toLowerCase().includes(q);
}

export function filterLeads(leads, filterId, search) {
  const filter = FILTERS.find((f) => f.id === filterId) || FILTERS[0];
  return leads.filter((lead) => filter.test(lead) && matchesSearch(lead, search));
}

export const countByFilter = (leads, search) =>
  Object.fromEntries(FILTERS.map((f) => [f.id, filterLeads(leads, f.id, search).length]));

/** Email numbers shown as columns: the schedule's, plus any a lead still has from an older schedule. */
function columnsOf(board) {
  const nos = new Set(board.schedule.map((s) => s.email_no));
  for (const lead of board.leads) for (const c of lead.emails) nos.add(c.email_no);
  return [...nos].sort((a, b) => a - b);
}

/** "Day 4", "Now" or "24 Nov 10:00" (a schedule row's timing; `at` is local business time). */
function timingText(row) {
  if (row.mode === 'now') return 'Now';
  if (row.mode === 'at' && row.at) {
    const [date, time] = row.at.split('T');
    return `${fmtCalendarDay(date)} ${time}`;
  }
  return `Day ${row.day}`;
}

function headCell(no, schedule) {
  const row = schedule.find((s) => s.email_no === no);
  return h('div', null, `Email ${no}`,
    h('div', { class: 'cell-sub' }, row ? `${timingText(row)} · ${row.template_name}` : 'not on the schedule'));
}

function leadCell(lead, onLead) {
  const meta = [lead.company, lead.role].filter(Boolean).join(' · ');
  return h('div', null,
    h('button', { class: 'link-btn', type: 'button', onClick: () => onLead(lead) }, lead.name || `Lead ${lead.id}`),
    h('div', { class: 'board__lead-meta' }, meta),
    h('div', { class: 'row' }, StateChip(lead.state), lead.paused ? Chip({ label: 'Paused', tone: 'warning' }) : null,
      StopChip(lead.stop_reason)));
}

function emailCells(lead, columns, tz, onCell, onLead) {
  const byNo = new Map(lead.emails.map((c) => [c.email_no, c]));
  const render = (no) => {
    const cell = byNo.get(no);
    return cell ? StatusCell({ cell, tz, onClick: () => onCell(lead, no) }) : h('span', { class: 'muted' }, '—');
  };
  if (!lead.stop_reason) return columns.map(render);
  const stopAt = columns.findIndex((no) => (byNo.get(no) || {}).status !== 'sent');
  if (stopAt < 0) return columns.map(render); // every email went before it stopped
  const span = {
    content: h('button', { class: 'link-btn', type: 'button', onClick: () => onLead(lead) }, stopText(lead, tz)),
    colSpan: columns.length - stopAt, class: 'stop-span',
  };
  return [...columns.slice(0, stopAt).map(render), span];
}

export function BoardTable({ board, leads, onCell, onLead }) {
  const columns = columnsOf(board);
  return Table({
    class: 'board',
    caption: 'Leads and their scheduled emails',
    head: ['Lead', ...columns.map((no) => headCell(no, board.schedule))],
    rows: leads.map((lead) => ({
      key: String(lead.id),
      cells: [leadCell(lead, onLead), ...emailCells(lead, columns, board.tz, onCell, onLead)],
    })),
  });
}
