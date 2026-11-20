import { h, mount } from '../dom.js';
import { holdOverlay } from '../store.js';

let current = null;

/**
 * Opens the right-side drawer (one at a time; opening another replaces it).
 * Returns {setTitle, setContent, close, isOpen}. Esc, the scrim and the X close it.
 */
export function openDrawer({ title = '', content = null, onClose } = {}) {
  if (current) current.close();
  const release = holdOverlay();
  const titleEl = h('div', { class: 'drawer__title', id: 'drawer-title' }, title);
  const body = h('div', { class: 'drawer__body' }, content);
  const closeBtn = h('button', { class: 'drawer__close', type: 'button', 'aria-label': 'Close' }, '×');
  const panel = h('aside', { class: 'drawer', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'drawer-title' },
    h('div', { class: 'drawer__head' }, titleEl, closeBtn), body);
  const scrim = h('div', { class: 'scrim' });
  const previousFocus = document.activeElement;
  let open = true;

  const onKey = (e) => { if (e.key === 'Escape' && !document.querySelector('dialog[open]')) api.close(); };
  const api = {
    setTitle: (t) => mount(titleEl, t),
    setContent: (...nodes) => { mount(body, ...nodes); body.scrollTop = 0; },
    isOpen: () => open,
    close() {
      if (!open) return;
      open = false;
      document.removeEventListener('keydown', onKey);
      panel.remove();
      scrim.remove();
      release();
      if (current === api) current = null;
      if (previousFocus && previousFocus.isConnected) previousFocus.focus();
      if (onClose) onClose();
    },
  };
  closeBtn.addEventListener('click', () => api.close());
  scrim.addEventListener('click', () => api.close());
  document.addEventListener('keydown', onKey);
  document.getElementById('overlays').append(scrim, panel);
  closeBtn.focus();
  current = api;
  return api;
}

export const closeDrawer = () => current && current.close();
