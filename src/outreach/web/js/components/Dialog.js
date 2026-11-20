import { h } from '../dom.js';
import { holdOverlay } from '../store.js';
import { Button } from './Button.js';

/** A modal confirm. Resolves true on confirm, false on cancel / Esc. */
export function confirmDialog({ title, message, confirmLabel = 'OK', danger = false } = {}) {
  return new Promise((resolve) => {
    const release = holdOverlay();
    const dialog = h('dialog', { class: 'dialog', 'aria-labelledby': 'dialog-title' });
    const finish = (answer) => {
      if (dialog.open) dialog.close();
      dialog.remove();
      release();
      resolve(answer);
    };
    const cancel = Button({ label: 'Cancel', variant: 'text', onClick: () => finish(false) });
    const ok = Button({ label: confirmLabel, variant: danger ? 'danger' : 'filled', onClick: () => finish(true) });
    dialog.append(
      h('div', { class: 'dialog__title', id: 'dialog-title' }, title),
      h('div', { class: 'muted' }, message),
      h('div', { class: 'dialog__actions' }, cancel, ok));
    dialog.addEventListener('cancel', (e) => { e.preventDefault(); finish(false); });
    document.getElementById('overlays').append(dialog);
    dialog.showModal();
    cancel.focus();
  });
}
