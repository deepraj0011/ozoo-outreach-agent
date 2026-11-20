import { h } from '../dom.js';

/**
 * A labelled input (or textarea with `multiline`). Returns the wrapper; the input is `wrapper.field`.
 * onInput(value) fires on every keystroke.
 */
export function TextField({ label, value = '', type = 'text', multiline, rows = 8, onInput, placeholder, required,
  name, min, max, maxLength, hint, autocomplete, class: extra } = {}) {
  const props = { class: 'field__input', name, placeholder, required: Boolean(required), maxlength: maxLength,
    autocomplete };
  const control = multiline
    ? h('textarea', { ...props, rows })
    : h('input', { ...props, type, min, max });
  control.value = value ?? '';
  if (onInput) control.addEventListener('input', () => onInput(control.value));
  const field = h('label', { class: `field${extra ? ` ${extra}` : ''}` },
    label ? h('span', { class: 'field__label' }, label) : null, control,
    hint ? h('span', { class: 'field__hint' }, hint) : null);
  field.field = control;
  return field;
}
