// Tiny observable store: get() / set(patch) / subscribe(fn). State is replaced, never mutated.

export function createStore(initial) {
  let state = Object.freeze({ ...initial });
  const listeners = new Set();
  return {
    get: () => state,
    set(patch) {
      const next = typeof patch === 'function' ? patch(state) : patch;
      state = Object.freeze({ ...state, ...next });
      for (const fn of [...listeners]) fn(state);
    },
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
  };
}

export const store = createStore({
  by: 'PM',          // who makes changes (recorded by the API)
  board: null,       // last GET /board response
  boardError: '',
  filter: 'all',     // campaign tabs: all | today | replied | stopped
  search: '',
  overlays: 0,       // open drawers + dialogs: auto-refresh waits while > 0
  editing: false,    // unsaved schedule edits: auto-refresh waits
});

/** Count an open drawer/dialog; returns the function that un-counts it (safe to call twice). */
export function holdOverlay() {
  store.set((s) => ({ overlays: s.overlays + 1 }));
  let released = false;
  return () => {
    if (released) return;
    released = true;
    store.set((s) => ({ overlays: Math.max(0, s.overlays - 1) }));
  };
}
