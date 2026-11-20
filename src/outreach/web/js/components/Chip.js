import { h } from '../dom.js';

/** tone: neutral | primary | success | warning | danger | info. With onClick it is a button chip. */
export function Chip({ label, tone = 'neutral', onClick, title } = {}) {
  const cls = `chip${tone === 'neutral' ? '' : ` chip--${tone}`}`;
  if (onClick) return h('button', { class: cls, type: 'button', onClick, title }, label);
  return h('span', { class: cls, title }, label);
}

const STATE_TONES = {
  in_sequence: 'primary', engaged: 'success', qualifying: 'success', booked: 'success', handed_off: 'success',
  paused: 'warning', nurture: 'neutral', closed: 'neutral', archived: 'neutral', new: 'info',
};

/** A lead's state as a chip ("in_sequence" -> "In sequence"). */
export function StateChip(state) {
  const label = String(state || 'unknown').replace(/_/g, ' ');
  return Chip({ label: label.charAt(0).toUpperCase() + label.slice(1), tone: STATE_TONES[state] || 'neutral' });
}

const TEMPLATE_STATUS = { live: ['Live', 'success'], draft: ['Draft', 'neutral'], off: ['Off', 'warning'] };

export function TemplateStatusChip(status) {
  const [label, tone] = TEMPLATE_STATUS[status] || [String(status), 'neutral'];
  return Chip({ label, tone });
}
