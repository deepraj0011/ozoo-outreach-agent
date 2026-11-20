import { h } from '../dom.js';
import { fmtDay, fmtDayTime, fmtTime, plural } from '../format.js';
import { Chip } from './Chip.js';

/** "2 opens · 1 click" (empty when nothing happened). */
export function engagementText(cell) {
  const parts = [];
  if (cell.opens) parts.push(plural(cell.opens, 'open'));
  if (cell.clicks) parts.push(plural(cell.clicks, 'click'));
  if (cell.visits) parts.push(plural(cell.visits, 'visit'));
  return parts.join(' · ');
}

/** What one board cell says: {main, sub, tone}. */
export function describeCell(cell, tz) {
  const when = cell.sent_at || cell.date;
  switch (cell.status) {
    case 'sent':
      return { main: `✓ ${fmtDay(when, tz)}`, sub: engagementText(cell), tone: 'sent' };
    case 'today':
      return { main: `Today ${fmtTime(cell.date, tz)}`, sub: cell.reason === 'send_now' ? 'sent by hand' : '', tone: 'today' };
    case 'planned':
      return { main: fmtDay(cell.date, tz), sub: '', tone: 'planned' };
    case 'handed':
      return { main: `Queued ${fmtDay(cell.date, tz)}`, sub: 'in the sender', tone: 'today' };
    case 'cancelled':
      return { main: 'Cancelled', sub: '', tone: 'muted' };
    case 'skipped':
      return { main: 'Skipped', sub: '', tone: 'muted' };
    default:
      return { main: String(cell.status || ''), sub: '', tone: 'muted' };
  }
}

/** Long label for drawers: "Sent 20 Nov, 3:00 pm" / "Planned for 29 Nov, 10:12 am". */
export function whenText(cell, tz) {
  const when = cell.sent_at || cell.date || cell.when;
  const at = fmtDayTime(when, tz);
  switch (cell.status) {
    case 'sent': return `Sent ${at}`;
    case 'today': return `Going today, ${fmtTime(when, tz)}`;
    case 'planned': return `Planned for ${at}`;
    case 'handed': return `In the sender since ${at}`;
    case 'cancelled': return 'Cancelled: will not be sent';
    case 'skipped': return 'Skipped';
    default: return at;
  }
}

/** A clickable board cell. */
export function StatusCell({ cell, tz, onClick }) {
  const { main, sub, tone } = describeCell(cell, tz);
  const late = cell.late && cell.status !== 'sent';
  return h('button', {
    class: `board__cell cell--${tone}${late ? ' cell--late' : ''}`, type: 'button', onClick,
    title: whenText(cell, tz),
  },
  h('div', { class: 'cell-main' }, main, late ? ' ' : null, late ? Chip({ label: 'late', tone: 'warning' }) : null),
  sub ? h('div', { class: 'cell-sub' }, sub) : null);
}

const STOP_LABELS = {
  replied: (d) => `Replied${d} — emails stopped`,
  booked: (d) => `Booked a demo${d} — emails stopped`,
  unsubscribed: () => 'Unsubscribed — stopped',
  bounced: () => 'Email bounced — stopped',
  stopped: (d) => `Stopped by hand${d}`,
};

/** "Replied 22 Nov — emails stopped" for a stopped lead. */
export function stopText(lead, tz) {
  const day = lead.stopped_at ? ` ${fmtDay(lead.stopped_at, tz)}` : '';
  const label = STOP_LABELS[lead.stop_reason];
  return label ? label(day) : `Stopped (${lead.stop_reason})`;
}

const STOP_TONES = { replied: 'success', booked: 'success', unsubscribed: 'danger', bounced: 'danger', stopped: 'warning' };

export function StopChip(reason) {
  if (!reason) return null;
  const label = { replied: 'Replied', booked: 'Booked', unsubscribed: 'Unsubscribed', bounced: 'Bounced', stopped: 'Stopped' }[reason] || reason;
  return Chip({ label, tone: STOP_TONES[reason] || 'neutral' });
}
