// Date and number formatting. Board times are shown in the board's business time zone (`tz`).

const cache = new Map();
function formatter(key, options) {
  if (!cache.has(key)) cache.set(key, new Intl.DateTimeFormat('en-GB', options));
  return cache.get(key);
}

function safeTz(tz) {
  try {
    new Intl.DateTimeFormat('en-GB', { timeZone: tz });
    return tz;
  } catch {
    return undefined;
  }
}

/** "20 Nov" for an ISO timestamp, in `tz`. */
export function fmtDay(iso, tz) {
  if (!iso) return '';
  const zone = safeTz(tz);
  return formatter(`d|${zone}`, { day: 'numeric', month: 'short', timeZone: zone }).format(new Date(iso));
}

/** "3:00 pm" for an ISO timestamp, in `tz`. */
export function fmtTime(iso, tz) {
  if (!iso) return '';
  const zone = safeTz(tz);
  return formatter(`t|${zone}`, { hour: 'numeric', minute: '2-digit', hour12: true, timeZone: zone })
    .format(new Date(iso)).replace(/\s?(am|pm)$/i, (m) => ' ' + m.trim().toLowerCase());
}

export const fmtDayTime = (iso, tz) => (iso ? `${fmtDay(iso, tz)}, ${fmtTime(iso, tz)}` : '');

/** "Fri 20 Nov" for a calendar day "2026-11-20" (no zone shift). A non-date value is returned as is. */
export function fmtCalendarDay(day) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(day || ''));
  if (!m) return String(day || '');
  const date = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  return formatter('cal', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' }).format(date);
}

export const plural = (n, one, many = one + 's') => `${n} ${n === 1 ? one : many}`;
