import { h } from '../dom.js';

/** tabs: [{id, label, count?}]. Calls onChange(id); re-render with the new `active` to move the marker. */
export function Tabs({ tabs, active, onChange, ariaLabel } = {}) {
  return h('div', { class: 'tabs', role: 'tablist', 'aria-label': ariaLabel },
    tabs.map((t) => h('button', {
      class: `tabs__tab${t.id === active ? ' is-active' : ''}`, type: 'button', role: 'tab',
      'aria-selected': t.id === active ? 'true' : 'false', onClick: () => onChange(t.id),
    }, t.label, t.count !== undefined ? h('span', { class: 'tabs__count' }, String(t.count)) : null)));
}
