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
  '/api/config': JSON.parse(fs.readFileSync(`${FIXTURES}/cfg.json`)),
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

// ── map and room list are two views of one selection ─────────────────────
const mapRooms = qa('.map-room');
const targetIndex = 7;
const mapRoom = mapRooms.find((m) => Number(m.dataset.index) === targetIndex);
click(mapRoom);
await tick();
const card = q(`.room-list-item[data-index="${targetIndex}"]`);
check('clicking a map room highlights its card', card.classList.contains('is-selected'),
      card.querySelector('h4')?.textContent);
check('and flashes it so the eye finds it', card.classList.contains('just-revealed'));
check('only one card is highlighted', qa('.room-list-item.is-selected').length === 1,
      `${qa('.room-list-item.is-selected').length} highlighted`);
// renderMap() replaces the plane's markup, so re-query rather than holding
// the node we clicked.
const mapNode = (i) => qa('.map-room').find((m) => Number(m.dataset.index) === i);
check('the map node is highlighted too', mapNode(targetIndex).classList.contains('selected'));
check('only one map node is highlighted', qa('.map-room.selected').length === 1,
      `${qa('.map-room.selected').length} highlighted`);

// The column the user was working in must survive a map click, or the
// libraries would silently retarget.
const kindBefore = q('.content-column.selected-target')?.dataset.kind;
click(qa('.map-room').find((m) => Number(m.dataset.index) === 2));
await tick();
const kindAfter = q('.content-column.selected-target')?.dataset.kind;
check('a map click keeps the selected column kind', kindBefore === kindAfter,
      `${kindBefore} -> ${kindAfter}`);
check('the target line follows to the new room',
      q('#lootLibraryTarget').textContent.includes(
        level.floors[0].rooms[2].roomId), q('#lootLibraryTarget').textContent);

// ── and selecting in the list moves the map ──────────────────────────────
click(qa('.content-column[data-kind="enemies"]')[5]);
await tick();
check('selecting a card highlights its map node', mapNode(5)?.classList.contains('selected'));

// ── the library panels lay out as declared ───────────────────────────────
// jsdom has no layout engine, so geometry cannot be asserted. What can be is
// the invariant that actually broke: `.library` pins each child to a fixed
// row, so adding a child without adding a row silently pushes everything
// down one and stretches whatever lands in the 1fr track.
const css = fs.readFileSync(path.join(ROOT, 'web/styles.css'), 'utf8');
const rule = css.match(/\.library\s*\{[^}]*grid-template-rows:\s*([^;]+);/);
check('.library declares its rows', !!rule);
if (rule) {
  // split on top-level whitespace — minmax(0,1fr) is one track, not two
  const tracks = [];
  let depth = 0, current = '';
  for (const ch of rule[1].trim()) {
    if (ch === '(') depth++;
    if (ch === ')') depth--;
    if (/\s/.test(ch) && depth === 0) { if (current) tracks.push(current); current = ''; }
    else current += ch;
  }
  if (current) tracks.push(current);

  for (const panel of qa('.library')) {
    const label = panel.querySelector('h3')?.textContent ?? '?';
    check(`${label}: a row per child`, panel.children.length === tracks.length,
          `${panel.children.length} children, ${tracks.length} rows`);
  }
  check('the search box sits in its own fixed row',
        tracks.length >= 3 && tracks[tracks.length - 2] === '42px', tracks.join(' | '));
}

// ── catalogs show real artwork ───────────────────────────────────────────
const art = qa('.content-glyph.art, .portrait.art');
check('catalogs render real sprites', art.length > 0, `${art.length} sprite glyphs`);
check('each crops from its sheet rather than stretching it',
      art.every((el) => {
        const style = el.querySelector('i')?.getAttribute('style') || '';
        return style.includes('background-position') && style.includes('background-size');
      }));
check('sprites are served from the texture endpoint',
      art.every((el) => (el.querySelector('i')?.getAttribute('style') || '')
        .includes('/api/texture?path=')));
check('rows with no resolvable art keep their letter glyph',
      qa('.content-glyph:not(.art)').every((el) => el.textContent.trim().length > 0));

// ── the enemy column says when each wave appears ─────────────────────────
const whens = qa('.enemy-content .when');
check('every enemy row states when it appears',
      whens.length > 0 && whens.every((w) => w.textContent.trim().length > 0),
      `${whens.length} rows`);
check('immediate waves are not highlighted',
      whens.filter((w) => w.textContent.trim() === 'immediately')
           .every((w) => !w.classList.contains('gated')));

// ── the project picker offers a path rather than an empty box ────────────
const cfg = routes['/api/config'];
if (cfg) {
  q('#projectBtn').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  await settle();
  check('the picker opens', q('#setupVeil').hidden === false);
  check('and is pre-filled, not empty',
        q('#setupPath').value.length > 0, q('#setupPath').value);
  check('a mounted project can dismiss it', q('#setupCancel').hidden === false);
}

console.log(failures.length ? `\n${failures.length} failing: ${failures.join(', ')}` : '\nall green');
process.exit(failures.length ? 1 : 0);
