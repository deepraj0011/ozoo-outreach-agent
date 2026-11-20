// Boot: name-for-`by`, router, nav highlighting.
import { createRouter } from './router.js';
import { store } from './store.js';
import { CampaignPage } from './pages/campaign.js';
import { TemplatesPage, TemplateEditorPage } from './pages/templates.js';
import { LeadsPage } from './pages/leads.js';

const NAME_KEY = 'ozoo.outreach.by';
const DEFAULT_NAME = 'PM';

function loadName() {
  try {
    return window.localStorage.getItem(NAME_KEY) || DEFAULT_NAME;
  } catch {
    return DEFAULT_NAME;
  }
}

function saveName(name) {
  try {
    window.localStorage.setItem(NAME_KEY, name);
  } catch {
    // storage blocked (private window): the name still works for this visit
  }
}

function setupName() {
  const input = document.getElementById('who');
  const name = loadName();
  input.value = name;
  store.set({ by: name });
  input.addEventListener('change', () => {
    const value = input.value.trim() || DEFAULT_NAME;
    input.value = value;
    store.set({ by: value });
    saveName(value);
  });
}

function setupNav() {
  const nav = document.getElementById('sidenav');
  const toggle = document.getElementById('nav-toggle');
  toggle.addEventListener('click', () => {
    const open = nav.classList.toggle('is-open');
    toggle.setAttribute('aria-expanded', String(open));
  });
  nav.addEventListener('click', () => {
    nav.classList.remove('is-open');
    toggle.setAttribute('aria-expanded', 'false');
  });
}

function highlight(name) {
  const section = name.startsWith('template') ? 'templates' : name;
  for (const link of document.querySelectorAll('.sidenav__link')) {
    const active = link.dataset.route === section;
    link.classList.toggle('is-active', active);
    if (active) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
  const titles = { campaign: 'Campaign', templates: 'Templates', template: 'Template', leads: 'Leads' };
  document.title = `${titles[name] || 'Outreach'} · Ozoo.ai Outreach`;
}

setupName();
setupNav();
createRouter({
  outlet: document.getElementById('view'),
  fallback: '/campaign',
  onChange: highlight,
  routes: [
    { name: 'campaign', pattern: /^\/campaign$/, render: CampaignPage },
    { name: 'templates', pattern: /^\/templates$/, render: TemplatesPage },
    { name: 'template', pattern: /^\/templates\/(?<id>new|\d+)$/, render: TemplateEditorPage },
    { name: 'leads', pattern: /^\/leads$/, render: LeadsPage },
  ],
}).start();
