import { h } from '../dom.js';

/** variant: filled | outlined | text | danger; size: 'small' for dense rows. */
export function Button({ label, variant = 'outlined', size, onClick, disabled, type = 'button', title } = {}) {
  const cls = ['btn', `btn--${variant}`, size === 'small' ? 'btn--small' : ''].filter(Boolean).join(' ');
  return h('button', { class: cls, type, onClick, disabled: Boolean(disabled), title }, label);
}

/** Runs an async action with the button disabled; returns the action's result. */
export async function busy(button, action) {
  button.disabled = true;
  try {
    return await action();
  } finally {
    button.disabled = false;
  }
}
