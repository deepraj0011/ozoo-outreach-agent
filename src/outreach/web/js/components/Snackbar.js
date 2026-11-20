import { h } from '../dom.js';

const DURATION_MS = 4500;
let host = null;

/** tone: info | success | error. Error messages stay a little longer. */
export function showSnackbar(message, tone = 'info') {
  if (!host) {
    host = h('div', { class: 'snackbar-host', role: 'status', 'aria-live': 'polite' });
    document.body.append(host);
  }
  const bar = h('div', { class: `snackbar${tone === 'info' ? '' : ` snackbar--${tone}`}` }, message);
  host.append(bar);
  setTimeout(() => bar.remove(), tone === 'error' ? DURATION_MS * 1.6 : DURATION_MS);
}

export const showError = (error) => showSnackbar(error && error.message ? error.message : String(error), 'error');
