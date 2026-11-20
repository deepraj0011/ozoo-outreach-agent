import { h } from '../dom.js';

export function Card({ title, class: extra } = {}, ...children) {
  return h('section', { class: `card${extra ? ` ${extra}` : ''}` },
    title ? h('h2', { class: 'card__title' }, title) : null, ...children);
}
