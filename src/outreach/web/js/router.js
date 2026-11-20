// Hash router. A route's page is `render(params) -> {el, destroy?}`; the previous page is destroyed first.

export function createRouter({ routes, fallback, outlet, onChange }) {
  let current = null;

  function match(hash) {
    const path = hash.replace(/^#/, '') || fallback;
    for (const route of routes) {
      const m = route.pattern.exec(path);
      if (m) return { route, params: m.groups || {} };
    }
    return null;
  }

  function resolve() {
    const found = match(window.location.hash);
    if (!found) {
      window.location.replace(`#${fallback}`);
      return;
    }
    if (current && current.destroy) current.destroy();
    current = found.route.render(found.params);
    outlet.replaceChildren(current.el);
    outlet.focus({ preventScroll: true });
    window.scrollTo(0, 0);
    if (onChange) onChange(found.route.name);
  }

  return {
    start() {
      window.addEventListener('hashchange', resolve);
      resolve();
    },
    go: (path) => { window.location.hash = path; },
  };
}
