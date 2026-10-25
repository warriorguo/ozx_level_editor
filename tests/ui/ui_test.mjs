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
const planPath = `${FIXTURES}/plan.json`;
const plan = fs.existsSync(planPath) ? JSON.parse(fs.readFileSync(planPath)) : null;
if (plan) routes[`/api/plan/${plan.id}`] = plan;

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

// ── what a room owes is its own line in the Loot column ──────────────────
// OZX-1380: a room's guaranteedDrops are paid by the last kill of its opening
// wave, on top of that body's drops — not by a table the room rolls, so they
// are drawn as pins, and the room-clear row they replaced is gone.
{
  const owed = level.floors[0].rooms.flatMap((r) => r.guaranteedDrops || []);
  const pins = qa('.loot-content .pin-line');
  check('each owed prize gets its own line', pins.length === owed.length,
        `${pins.length} of ${owed.length}`);
  if (owed.length) {
    check('the line names what it pays and how many',
          pins[0]?.textContent.includes(`×${owed[0].count}`)
          && pins[0]?.textContent.includes(owed[0].displayName || owed[0].itemId),
          pins[0]?.textContent);
  }
  check('there is no room-clear row any more',
        !qa('.loot-label').some((l) => l.textContent.trim() === 'room clear'));
}

// ── a skill platform says which skill it grants ──────────────────────────
// Its skillId is the only thing that distinguishes one platform from another;
// without it the row reads "skill / random" and names nothing.
const skillRows = qa('.content-entry.skill-entry');
check('skill placements render a skill row', skillRows.length > 0,
      `${skillRows.length} rows`);
check('each names the skill rather than just the kind',
      skillRows.every((row) => {
        const sub = row.querySelector('small')?.textContent.trim() || '';
        return sub.length > 0 && sub !== 'random' && !/^\d+ cells?$/.test(sub);
      }),
      skillRows.map((r) => r.querySelector('small')?.textContent.trim()).join(' | '));
check('and offers a picker to change it',
      skillRows.every((row) => row.querySelector('.skill-picker')?.options.length > 0));
check('the picker is pre-selected to the authored skill',
      skillRows.every((row) => {
        const select = row.querySelector('.skill-picker');
        return !select.disabled ? select.value.length > 0 : true;
      }));

const skillSelect = skillRows[0]?.querySelector('.skill-picker');
if (skillSelect) {
  const before = posted.length;
  const other = [...skillSelect.options].find((o) => o.value && o.value !== skillSelect.value);
  skillSelect.value = other.value;
  skillSelect.dispatchEvent(new window.Event('change', { bubbles: true }));
  await settle();
  const write = posted[before];
  check('changing the picker writes an edit', !!write);
  check('the edit targets that placement\'s skillId',
        write?.body.edits[0].path.endsWith('/skillId'), write?.body.edits[0].path);
  check('and carries the chosen skill', write?.body.edits[0].value === other.value);
}

// ── the skill library ────────────────────────────────────────────────────
const skillCards = qa('#skillLibrary .asset-card');
check('the skill library lists the placeable skills',
      skillCards.length === routes['/api/bootstrap'].skillCatalog.length,
      `${skillCards.length} cards`);
check('every skill card shows its artwork',
      skillCards.every((c) => c.querySelector('.portrait.art')),
      `${skillCards.filter((c) => c.querySelector('.portrait.art')).length} with art`);
check('and names the skill, not just the id',
      skillCards.every((c) => (c.querySelector('small')?.textContent || '').includes('passive')));
check('it counts what it shows',
      q('#skillLibraryCount').textContent === `${skillCards.length} FOUND`,
      q('#skillLibraryCount').textContent);
check('and says where a click will land',
      (q('#skillLibraryTarget').textContent || '').startsWith('→'),
      q('#skillLibraryTarget').textContent);

{
  const before = posted.length;
  click(skillCards[0]);
  await settle();
  const write = posted[before];
  check('clicking a skill writes an edit', !!write);
  check('it appends a placement to the room', write?.body.edits[0].op === 'append',
        write?.body.edits[0].path);
  // A skill platform with no skillId throws at spawn, so the appended entry
  // has to arrive complete rather than as a bare kind.
  check('the placement arrives with its skill already set',
        write?.body.edits[0].value?.kind === 'skill'
        && write?.body.edits[0].value?.skillId === skillCards[0].dataset.id,
        JSON.stringify(write?.body.edits[0].value));
}

{
  const search = q('#skillSearch');
  search.value = 'hammer';
  search.dispatchEvent(new window.Event('input', { bubbles: true }));
  await settle();
  const shown = qa('#skillLibrary .asset-card');
  check('searching narrows the list', shown.length > 0 && shown.length < skillCards.length,
        `${shown.length} of ${skillCards.length}`);
  check('and keeps only matches', shown.every((c) => c.dataset.id.includes('hammer')));
  search.value = '';
  search.dispatchEvent(new window.Event('input', { bubbles: true }));
  await settle();
}

// ── HUD tips (OZX-1168) ──────────────────────────────────────────────────
// A tip is offered only where it can show, written when given text and
// removed when cleared — never written blank, never past 25 characters.
{
  const fields = qa('[data-tip-path]');
  const authored = level.floors[0].rooms.flatMap((r) =>
    r.doors.filter((d) => d.lockedTip).map((d) => d.lockedTip)
      .concat(r.exitBlockedTip ? [r.exitBlockedTip] : []));
  check('every authored tip is in a field', authored.every((t) => fields.some((f) => f.value === t)),
        `${authored.length} authored, ${fields.length} fields`);
  const lockable = level.floors[0].rooms.reduce((n, r) =>
    n + r.doors.filter((d) => (d.locked && d.keyId) || d.lockedTip != null).length
      + ((r.requiredForExitItemIds || []).length || r.exitBlockedTip != null ? 1 : 0), 0);
  check('a tip is offered exactly where it can show', fields.length === lockable,
        `${fields.length} of ${lockable}`);
  const field = fields.find((f) => f.value);
  if (field) {
    const change = async (value) => {
      field.value = value;
      field.dispatchEvent(new window.Event('change', { bubbles: true }));
      await settle();
    };
    let before = posted.length;
    await change('GO FIND IT');
    check('editing a tip writes it with put', posted[before]?.body.edits[0].op === 'put'
          && posted[before]?.body.edits[0].path === field.dataset.tipPath,
          JSON.stringify(posted[before]?.body.edits[0]));
    before = posted.length;
    await change('THIS ONE IS FAR TOO LONG TO FIT');
    check('an over-length tip is refused before it is sent', posted.length === before);
    before = posted.length;
    await change('   ');
    check('clearing a tip removes the key rather than writing blank',
          posted[before]?.body.edits[0].op === 'remove', posted[before]?.body.edits[0].op);
  }
}

// ── a switch must not leave a slot armed in another room ─────────────────
// The selection is (roomIndex, kind, index) against the CURRENT floor, so
// carrying it across a switch re-aims it at whatever room now sits at that
// index — and a library click then edits a room nobody pointed at. This was
// real: it rewrote a skill platform two rooms away during testing.
{
  const slot = qa('[data-slot="statics"]')[0];
  if (slot && level.floors.length > 1) {
    click(slot);
    await settle();
    const armed = q('#staticLibraryTarget').textContent;
    check('selecting a slot arms the library', /·/.test(armed), armed);

    const tabs = qa('#floorTabs [data-floor]');
    click(tabs[1]);
    await settle();
    const after = q('#staticLibraryTarget').textContent;
    check('switching floors disarms it',
          !after.includes('#'), after);

    const before = posted.length;
    click(qa('#skillLibrary .asset-card')[0]);
    await settle();
    const write = posted[before];
    // Disarmed means "append to the room I am looking at", never "repoint
    // that slot I happened to have selected on the other floor".
    check('a library click after the switch appends rather than repoints',
          write?.body.edits[0].op === 'append', write?.body.edits[0].op);
    check('and lands on the floor now shown',
          write?.body.edits[0].path.startsWith('/floors/1/'),
          write?.body.edits[0].path);
  }
}

// ── plans are a different document kind ──────────────────────────────────
// A LevelBasePlanData has no rooms until something generates them. Drawing it
// as a floor map, or leaving the libraries aimed at the level that was open
// before, are the two ways this screen can lie.
if (plan) {
  const picker = q('#levelPicker');
  check('the picker groups plans apart from levels',
        qa('#levelPicker optgroup').length === 2,
        qa('#levelPicker optgroup').map((g) => g.label).join(' | '));

  picker.value = `plan:${plan.id}`;
  picker.dispatchEvent(new window.Event('change', { bubbles: true }));
  await settle(20);

  const floorCards = () => qa('.plan-floor:not(.plan-defaults):not(.plan-links)');
  check('opening a plan renders a card per floor',
        floorCards().length === plan.floors.length,
        `${floorCards().length} floors`);
  // OZX-1011: the chapter defaults get a card of their own, which says how
  // far each default reaches — and only when the plan has any.
  check('the chapter defaults have a card iff the plan declares them',
        qa('.plan-defaults').length === (plan.floorDefaults?.declared ? 1 : 0));
  if (plan.floorDefaults?.declared) {
    check('it says how many floors inherit each default',
          qa('.plan-defaults .fan-out').length > 0
          && qa('.plan-defaults .fan-out').every((n) => /^\d+\/\d+ floors$/.test(n.textContent)),
          qa('.plan-defaults .fan-out')[0]?.textContent);
    const inherited = plan.floors.reduce((n, f) => n + (f.inherited || []).length, 0);
    check('a floor marks the values it inherits',
          inherited === 0 || qa('.plan-floor .inherited, .plan-floor .inherit-tag').length > 0);
  }
  check('it draws no room cards', qa('.room-list-item:not(.plan-floor)').length === 0);
  check('and says why there is no map', !!q('.plan-nomap'));
  // #mapPlane lives inside #mapScroll; writing the notice to the scroller
  // deleted the plane and every render after a write threw on the null.
  check('the map plane survives for the next render', !!q('#mapPlane'));

  // ── the spawner channel is on the pool row ─────────────────────────────
  // OZX-1032: a spawner produces nothing on its own, so an enemy-pool row
  // that shows only weight and max/room says nothing about the four jammers
  // each drawn factory adds on top of the floor's enemies/room budget.
  {
    const spawners = plan.floors.flatMap((f) => f.enemyPool.filter((e) => e.isSpawner));
    const lines = qa('.plan-floor .spawner-line');
    check('a spawner pool entry says what it spawns',
          lines.length === spawners.length, `${lines.length} of ${spawners.length}`);
    check('and how many of them each one is fed',
          lines.every((l) => new RegExp(`×\\d+ \\S+ each`).test(l.textContent)),
          lines[0]?.textContent);
    check('an ordinary pool entry gets no such line',
          qa('.plan-floor .pool-entry').length > lines.length);
  }

  // ── stage weights are a table, not a list ──────────────────────────────
  // OZX-1044: an entry may scope itself to a taxonomy category, so a stage
  // has several rows and the multiplier needs its own column — reading ×0 out
  // of the same line as the category is how "this stage drops nothing" gets
  // missed.
  {
    const authored = plan.floors.flatMap((f) => f.stageWeights);
    const rows = qa('.plan-floor .weight-entry');
    check('a floor lists the stage weights it authored',
          rows.length === authored.length, `${rows.length} of ${authored.length}`);
    check('every row shows its multiplier',
          rows.every((r) => /^×/.test(r.querySelector('.weight-value')?.textContent || '')),
          rows[0]?.querySelector('.weight-value')?.textContent);
    check('a zero is muted rather than read as a small number',
          qa('.plan-floor .weight-entry .weight-value.zero').length ===
            authored.filter((w) => w.weight === 0).length,
          `${qa('.plan-floor .weight-entry .weight-value.zero').length} zeros`);
    check('a category-free row says it is the fallback',
          rows.some((r) => r.textContent.includes('everything else on this stage')));
  }

  check('every tile group the theme owns is offered',
        qa('.plan-floor .tilepart').length ===
          plan.floors.reduce((n, f) => n + f.themeParts.all.length,
            plan.floorDefaults?.declared ? plan.floorDefaults.themeParts.all.length : 0));
  check('the authored subset is the one switched on',
        qa('.plan-floor[data-floor="0"] .tilepart.on').length ===
          plan.floors[0].appearance.tilePartConfigs.length);
  check('a part kept out of the random pool is marked',
        qa('.plan-floor[data-floor="0"] .tilepart.named-only').length ===
          plan.floors[0].themeParts.named.length);

  // An absent bossId is a real choice (OZX-994), never an unfilled field.
  const noBoss = plan.floors.filter((f) => !f.hasBoss).length;
  check('a floor with no boss says so rather than showing a blank',
        qa('.plan-facts strong.noboss').filter((n) => n.textContent.startsWith('none')).length
          === noBoss, `${noBoss} bossless`);

  // The target line is "→ <roomId> · <column>" in level mode. What must not
  // survive is the arrow naming a room in the document no longer on screen —
  // `selectionRoom()` still resolves it against state.level, so a click would
  // land there for real.
  check('the libraries stop aiming at the level that was open',
        !q('#enemyLibraryTarget').textContent.trim().startsWith('→'),
        q('#enemyLibraryTarget').textContent);
  check('and adding a room is not offered', q('#addRoomBtn').hidden === true);

  {
    const before = posted.length;
    click(qa('#skillLibrary .asset-card')[0]);
    await settle();
    check('a library click writes nothing in plan mode', posted.length === before);
  }

  // A floor that inherits its tile groups must not edit the chapter from a
  // card that names one floor; the defaults card is where that happens.
  const tileHolder = plan.floors[0].appearance.inherited ? 'defaults' : '0';
  if (tileHolder === 'defaults') {
    const before = posted.length;
    click(qa('.plan-floor[data-floor="0"] .tilepart')[0]);
    await settle();
    check('an inherited tile group is not edited from the floor', posted.length === before);
  }
  {
    const before = posted.length;
    const off = qa(`.plan-floor[data-floor="${tileHolder}"] .tilepart:not(.on)`)[0];
    click(off);
    await settle();
    const write = posted[before];
    check('toggling a tile group on appends one entry',
          write?.body.edits[0].op === 'append', write?.body.edits[0].op);
    // Setting the whole array would reformat it to the writer's house style
    // and reorder the author's entries; chapter_3 writes `[ "a", "b" ]`.
    check('it targets the array, not a rewrite of it',
          write?.body.edits[0].path.endsWith('/appearance/tilePartConfigs'),
          write?.body.edits[0].path);
    check('and it writes to the plan document',
          write?.body.dataType === 'LevelBasePlanData', write?.body.dataType);
  }

  {
    const before = posted.length;
    click(qa(`.plan-floor[data-floor="${tileHolder}"] .tilepart.on`)[0]);
    await settle();
    check('toggling one off removes that index',
          posted[before]?.body.edits[0].op === 'remove',
          posted[before]?.body.edits[0].path);
  }

  // Back to a level: the level-only chrome has to come back with it.
  picker.value = `level:${level.id}`;
  picker.dispatchEvent(new window.Event('change', { bubbles: true }));
  await settle(20);
  check('switching back to a level restores the room list', qa('.room-list-item').length > 0);
  check('and restores Add room', q('#addRoomBtn').hidden === false);
}

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
