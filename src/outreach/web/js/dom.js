// Element helper. All text goes in through text nodes / textContent: never innerHTML with data.

// Props set as DOM properties (not attributes) so they reflect live state.
const PROPERTIES = new Set(['value', 'checked', 'disabled', 'selected', 'srcdoc', 'hidden', 'textContent', 'multiple']);

function setProp(el, key, value) {
  if (value === null || value === undefined || value === false) return;
  if (key === 'class') el.className = value;
  else if (key === 'style' && typeof value === 'object') Object.assign(el.style, value);
  else if (key === 'dataset') Object.assign(el.dataset, value);
  else if (key.startsWith('on') && typeof value === 'function') el.addEventListener(key.slice(2).toLowerCase(), value);
  else if (key === 'ref' && typeof value === 'function') value(el);
  else if (PROPERTIES.has(key)) el[key] = value;
  else el.setAttribute(key, value === true ? '' : String(value));
}

export function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false || child === '') continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

/** h('button', {class: 'btn', onClick}, 'Save') -> <button class="btn">Save</button> */
export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) setProp(el, key, value);
  return append(el, children);
}

/** Replace all children of `el`. */
export function mount(el, ...children) {
  el.replaceChildren();
  return append(el, children);
}

/** fn delayed until `ms` after the last call. */
export function debounce(fn, ms) {
  let timer = null;
  const wrapped = (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
  wrapped.cancel = () => clearTimeout(timer);
  return wrapped;
}
