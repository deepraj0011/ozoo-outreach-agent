import { h } from '../dom.js';

export function EmptyState({ title, text, action } = {}) {
  return h('div', { class: 'empty' },
    h('div', { class: 'empty__title' }, title),
    text ? h('div', null, text) : null,
    action || null);
}
