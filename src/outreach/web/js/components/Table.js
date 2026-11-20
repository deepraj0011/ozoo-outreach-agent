import { h } from '../dom.js';

/**
 * head: header cells (strings or nodes). rows: [{cells, onClick?, key?}]; a cell is a string/node or
 * {content, colSpan, class}. Wrapped in a scroll box with a sticky header.
 */
export function Table({ head, rows, class: extra, caption } = {}) {
  const th = head.map((cell) => h('th', { scope: 'col' }, cell));
  const body = rows.map((row) => {
    const tds = row.cells.map((cell) => {
      if (cell && typeof cell === 'object' && !(cell instanceof Node) && 'content' in cell) {
        return h('td', { colSpan: cell.colSpan > 1 ? cell.colSpan : null, class: cell.class }, cell.content);
      }
      return h('td', null, cell);
    });
    const tr = h('tr', { class: row.onClick ? 'is-clickable' : null, dataset: row.key ? { key: row.key } : null }, tds);
    if (row.onClick) tr.addEventListener('click', row.onClick);
    return tr;
  });
  return h('div', { class: 'table-wrap' },
    h('table', { class: `table${extra ? ` ${extra}` : ''}` },
      caption ? h('caption', { class: 'visually-hidden' }, caption) : null,
      h('thead', null, h('tr', null, th)), h('tbody', null, body)));
}
