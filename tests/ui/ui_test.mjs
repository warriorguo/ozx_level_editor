/* Browser-client tests, run against a real DOM (jsdom).
 *
 * `node --check` only catches syntax. Every client bug so far has been a
 * runtime one — a function deleted along with its neighbour, two vocabularies
 * disagreeing, a click handler eating a dropdown — so the client needs tests
 * that actually click things.
 *
 * Run:  python3 -m pytest tests/test_ui.py      (installs jsdom on demand)
 *   or: node tests/ui/ui_test.mjs               (needs JSDOM_PATH + FIXTURES)
 */

import fs from 'fs';
import path from 'path';

const ROOT = path.resolve(import.meta.dirname, '../..');
const FIXTURES = process.env.FIXTURES;
const { JSDOM } = await import(`file://${process.env.JSDOM_PATH}`);

const routes = {
  '/api/bootstrap': JSON.parse(fs.readFileSync(`${FIXTURES}/boot.json`)),
  '/api/validate': JSON.parse(fs.readFileSync(`${FIXTURES}/val.json`)),
};
const level = JSON.parse(fs.readFileSync(`${FIXTURES}/lvl.json`));
routes[`/api/level/${level.id}`] = level;

const dom = new JSDOM(fs.readFileSync(path.join(ROOT, 'web/index.html'), 'utf8'),
                      { runScripts: 'outside-only', pretendToBeVisual: true });
const { window } = dom;
const posted = [];
window.fetch = async (url, opts) => {
  if (opts?.method === 'POST') {
    posted.push({ url, body: JSON.parse(opts.body) });
    return { ok: true, json: async () => ({ ok: true, applied: 1 }) };
  }
  return { ok: true, json: async () => routes[url] ?? {} };
};
globalThis.window = window;
globalThis.document = window.document;
window.eval(fs.readFileSync(path.join(ROOT, 'web/app.js'), 'utf8'));

const tick = () => new Promise((r) => setTimeout(r, 0));
const settle = async (n = 10) => { for (let i = 0; i < n; i++) await tick(); };
await settle();

const d = window.document;
const q = (s) => d.querySelector(s);
const qa = (s) => [...d.querySelectorAll(s)];
const click = (el) => el.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));

const failures = [];
function check(name, condition, detail = '') {
  const ok = !!condition;
  console.log(`${ok ? 'ok  ' : 'FAIL'}  ${name}${detail ? ` — ${detail}` : ''}`);
  if (!ok) failures.push(name);
}

// ── it renders at all ────────────────────────────────────────────────────
check('renders a card per room', qa('.room-list-item').length === level.floors[0].rooms.length,
      `${qa('.room-list-item').length} cards`);
check('renders the floor map', qa('.map-room').length > 0, `${qa('.map-room').length} map rooms`);
check('populates all three libraries',
      ['enemy', 'static', 'loot'].every((k) => q(`#${k}Library`).children.length > 0));
check('library target lines resolve',
      ['enemy', 'static', 'loot'].every((k) => !q(`#${k}LibraryTarget`).textContent.includes('undefined')),
      q('#enemyLibraryTarget').textContent);

// ── selection ────────────────────────────────────────────────────────────
const lootTable = qa('.loot-table')[3];
const pointer = lootTable.dataset.lootPointer;
click(lootTable);
await tick();
check('clicking a loot table selects that slot', lootTable.classList.contains('selected-slot'), pointer);
check('the library target follows the selection',
      q('#lootLibraryTarget').textContent.includes('·'), q('#lootLibraryTarget').textContent);

// ── the regression this file exists for ──────────────────────────────────
// A click on the dropdown used to bubble into the selection handler, which
// re-rendered the card and destroyed the open <select> before anything could
// be chosen — making loot impossible to edit.
const select = lootTable.querySelector('select.loot-picker');
const firstCard = qa('.room-list-item')[0];
const otherTable = qa('.loot-table')[5];
click(otherTable.querySelector('select.loot-picker'));
await tick();
check('a dropdown click does not move the selection',
      lootTable.classList.contains('selected-slot') && !otherTable.classList.contains('selected-slot'));
check('a dropdown click does not rebuild the room list', qa('.room-list-item')[0] === firstCard);
check('the open dropdown survives', d.contains(select) && select.options.length > 1,
      `${select.options.length} options`);

// ── and it still writes where it should ──────────────────────────────────
select.value = 'loot_boss';
select.dispatchEvent(new window.Event('change', { bubbles: true }));
await settle();
const write = posted.find((p) => p.url === '/api/edit');
check('changing the dropdown writes an edit', !!write);
check('the edit targets the slot it belongs to',
      write && write.body.edits[0].path === pointer, write?.body.edits[0].path);
check('the edit carries the chosen value', write?.body.edits[0].value === 'loot_boss');

// ── other controls are equally protected ─────────────────────────────────
const qty = qa('[data-enemy-index] input')[0];
if (qty) {
  const before = qa('.room-list-item')[0];
  click(qty);
  await tick();
  check('a quantity-input click does not rebuild the list', qa('.room-list-item')[0] === before);
}
const roomSelect = qa('[data-field]')[0];
if (roomSelect) {
  const before = qa('.room-list-item')[0];
  click(roomSelect);
  await tick();
  check('a room-field click does not rebuild the list', qa('.room-list-item')[0] === before);
}

// ── column selection still works ─────────────────────────────────────────
const column = qa('.content-column[data-kind="statics"]')[0];
click(column);
await tick();
check('clicking a column selects it', column.classList.contains('selected-target'));
check('only one target is highlighted', qa('.selected-target').length === 1,
      `${qa('.selected-target').length} highlighted`);

console.log(failures.length ? `\n${failures.length} failing: ${failures.join(', ')}` : '\nall green');
process.exit(failures.length ? 1 : 0);
