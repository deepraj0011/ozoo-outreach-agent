import { h } from '../dom.js';

/** options: [{value, label, disabled}]. Returns the <select> (wrapped in a labelled field when `label`). */
export function Select({ value, options, onChange, label, ariaLabel } = {}) {
  const select = h('select', { class: 'select', 'aria-label': label ? null : ariaLabel },
    options.map((o) => h('option', { value: String(o.value), disabled: Boolean(o.disabled) }, o.label)));
  select.value = String(value ?? '');
  if (onChange) select.addEventListener('change', () => onChange(select.value));
  if (!label) return select;
  const field = h('label', { class: 'field' }, h('span', { class: 'field__label' }, label), select);
  field.field = select;
  return field;
}
