/* OZX Level Studio — browser client.
 *
 * Structure and interactions follow level_editor_template.html; the data is
 * real GameData served by serve.py. Two differences from the prototype that
 * matter, and that the UI has to be honest about:
 *
 *  - A room's enemies are NOT authored on the room. They come from the
 *    EncounterData its `encounterId` points at, so the enemy column is a
 *    derived view and its quantity controls edit the encounter's steps.
 *  - Doors are bidirectional. Toggling one writes the twin on the other side
 *    in the same request, because a door without its twin is a one-way door
 *    that nothing reports at runtime.
 */

const $ = (id) => document.getElementById(id);

const state = {
  boot: null,
  level: null,       // the loaded level view
  levelId: null,
  floorIndex: 0,
  selectedRoomIndex: 0,
  // 'level' (authored rooms) or 'plan' (a generation recipe with no rooms).
  mode: 'level',
  plan: null,
  planId: null,
  session: null,
  sessionName: null,
  sessions: [],
  // What a library click will act on. `kind` names the column; `pointer` is
  // set only when a specific slot was picked (e.g. one cargo box's loot
  // table) rather than the column as a whole.
  selection: { roomIndex: 0, kind: 'enemies', pointer: null, index: null, label: null },
  busy: false,
};

const DIRS = ['N', 'E', 'S', 'W'];

// ── plumbing ─────────────────────────────────────────────────────────────

async function api(path, options) {
  const response = await fetch(path, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.ok === false) {
    const err = new Error(payload.error || payload.message || `${response.status} ${path}`);
    err.externallyModified = !!payload.externallyModified;
    throw err;
  }
  return payload;
}

const post = (path, body) =>
  api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

function showToast(message) {
  const toast = $('toast');
  toast.textContent = message;
  toast.classList.add('show');
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove('show'), 1900);
}

function setBusy(on) {
  state.busy = on;
  document.querySelector('.app').classList.toggle('saving', on);
}

/** Every edit writes through to disk immediately, so "dirty" is really
 *  "last write", not an unsaved buffer. Say so rather than implying a
 *  working copy that does not exist yet. */
function markSaved(text) {
  $('dirtyLabel').innerHTML = `<span style="color:var(--acid)">●</span> ${text}`;
}

const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// ── project folder ───────────────────────────────────────────────────────
// The app has no command line, so the ozx_base checkout is chosen here. When
// running inside the native wrapper a real NSOpenPanel is available through a
// WKWebView message handler; in a plain browser tab it is a text field, and
// both go through the same PUT /api/config.

const nativeBridge = () => window.webkit?.messageHandlers?.pickFolder;

function showSetup({ dismissible, reason, current, suggestion, configPath }) {
  $('setupVeil').hidden = false;
  $('setupError').textContent = '';
  $('setupCancel').hidden = !dismissible;
  $('setupBrowse').hidden = !nativeBridge();

  // Pre-fill with the saved folder if there is one, otherwise the guess — so
  // the usual case is one click rather than typing a path.
  const prefill = current || suggestion || '';
  $('setupPath').value = prefill;

  // When the saved path is the problem, the guess is the way out, so offer it
  // explicitly instead of silently overwriting what the user had.
  const offerSuggestion = suggestion && suggestion !== prefill;
  $('setupHint').hidden = !offerSuggestion;
  if (offerSuggestion) $('setupSuggestion').textContent = suggestion;

  // An empty field is the one state that looks broken rather than merely
  // unset, so never leave it unexplained.
  $('setupReason').textContent = reason
    || (prefill ? DEFAULT_SETUP_REASON : NO_GUESS_REASON);

  // Whichever config file is in play — the answer to "why is it not
  // remembering my folder", which is otherwise invisible.
  $('setupConfig').textContent = configPath || '';
  $('setupConfig').hidden = !configPath;

  $('setupPath').focus();
  $('setupPath').select();
}

const DEFAULT_SETUP_REASON =
  'Level Studio edits the JSON under Assets/StreamingAssets/GameData. '
  + 'Choose the folder that contains it.';

const NO_GUESS_REASON =
  'No ozx_base checkout found automatically. Type the path to the folder that '
  + 'contains Assets/StreamingAssets/GameData, or browse to it.';

const hideSetup = () => { $('setupVeil').hidden = true; };

async function applyProjectRoot(path) {
  if (!path || !path.trim()) {
    $('setupError').textContent = 'Enter a folder path.';
    return;
  }
  setBusy(true);
  try {
    const result = await fetch('/api/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ projectRoot: path.trim() }),
    }).then((r) => r.json());

    if (!result.ok) {
      $('setupError').textContent = result.error || 'Could not use that folder.';
      return;
    }
    hideSetup();
    showToast(`Project set — ${result.documents} documents indexed`);
    await boot();                       // re-bootstrap against the new project
  } catch (err) {
    $('setupError').textContent = err.message;
  } finally {
    setBusy(false);
  }
}

/** Called by the native wrapper after the user chooses a folder. */
window.onFolderPicked = (path) => { $('setupPath').value = path; applyProjectRoot(path); };

$('setupUse').addEventListener('click', () => applyProjectRoot($('setupPath').value));
$('setupPath').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') applyProjectRoot($('setupPath').value);
});
$('setupBrowse').addEventListener('click', () => nativeBridge()?.postMessage({}));
$('setupSuggestion').addEventListener('click', () => {
  $('setupPath').value = $('setupSuggestion').textContent;
  applyProjectRoot($('setupPath').value);
});
$('setupCancel').addEventListener('click', hideSetup);
$('projectBtn').addEventListener('click', async () => {
  const cfg = await api('/api/config');
  showSetup({ dismissible: cfg.mounted, current: cfg.project_root,
              suggestion: cfg.suggestion, configPath: cfg.config_path });
});

// ── loading ──────────────────────────────────────────────────────────────

async function boot() {
  try {
    state.boot = await api('/api/bootstrap');
  } catch (err) {
    document.querySelector('.app').innerHTML =
      `<div class="boot-shell"><h2>Cannot reach the server</h2>
       <p>${esc(err.message)}</p><p>Start it with <code>python3 serve.py</code>.</p></div>`;
    return;
  }

  const { levels, counts, projectRoot } = state.boot;
  $('projectLabel').textContent = projectRoot
    ? `${projectRoot} · ${counts.documents} docs`
    : 'no project folder set';

  // Nothing mounted yet: block on the chooser rather than showing an editor
  // with nothing in it.
  if (!projectRoot || !counts.documents) {
    const cfg = await api('/api/config').catch(() => ({}));
    showSetup({
      dismissible: false,
      current: cfg.project_root || cfg.suggestion,
      reason: cfg.reason && cfg.project_root
        ? `That folder cannot be used: ${cfg.reason}`
        : undefined,
    });
    return;
  }
  hideSetup();

  if (!levels.length) {
    $('roomsGrid').innerHTML = '<div class="empty-copy">No LevelData found under GameData/levels/.</div>';
    return;
  }

  // Plans sit in the same picker as levels, but they are a different kind of
  // document: a LevelData has rooms, a LevelBasePlanData has a recipe for
  // rooms that do not exist until something generates them. Grouping them
  // apart is the first place the UI has to say so.
  const plans = state.boot.plans || [];
  const levelOpts = levels
    .map((l) => `<option value="level:${esc(l.id)}">${esc(l.id)} · ${l.rooms} rooms</option>`)
    .join('');
  const planOpts = plans
    .map((p) => `<option value="plan:${esc(p.id)}">${esc(p.id)} · ${p.floors} floor${p.floors === 1 ? '' : 's'}</option>`)
    .join('');
  // A third kind: a level that was actually generated and played. Not a
  // recipe and not an authored level — a record of one run.
  const sessions = (await api('/api/sessions').catch(() => ({}))).documents || [];
  state.sessions = sessions;
  // Label by run, and say which file when a run captured more than one —
  // /api/control/reload regenerates within the same session.
  const sessionOpts = sessions
    .map((sdoc) => {
      const file = sdoc.name.split('/').pop().replace(/\.json$/, '');
      const extra = file === 'level' ? '' : ` · ${file}`;
      return `<option value="session:${esc(sdoc.name)}">${esc(sdoc.session)}${extra}${
        sdoc.legacy ? ' (legacy)' : ''}</option>`;
    })
    .join('');
  $('levelPicker').innerHTML =
    `<optgroup label="Levels (authored rooms)">${levelOpts}</optgroup>`
    + (planOpts ? `<optgroup label="Plans (generated at runtime)">${planOpts}</optgroup>` : '')
    + (sessionOpts ? `<optgroup label="Sessions (a run that happened)">${sessionOpts}</optgroup>` : '');
  $('levelPicker').addEventListener('change', (e) => openDocument(e.target.value));

  await loadLevel(levels[0].id);
  await refreshGlobalValidation();
}

/** Forget which slot a library click would act on.
 *
 *  The selection is (roomIndex, kind, index) resolved against the CURRENT
 *  floor, so carrying it across a floor or level switch silently re-aims it
 *  at whatever room now sits at that index. The library target line then
 *  reads like a room the user never picked, and one click edits it — for the
 *  Skill library that means repointing a skill platform in a room the user
 *  is not even looking at. Both switches already reset selectedRoomIndex;
 *  this is the half that was missed.
 */
function resetSelection() {
  state.selection = { roomIndex: 0, kind: 'enemies', pointer: null,
                      index: null, label: null };
}

/** Open whatever the picker names — `level:<id>` or `plan:<id>`. */
async function openDocument(value) {
  const [kind, ...rest] = String(value).split(':');
  const id = rest.join(':');
  if (kind === 'plan') return loadPlan(id);
  if (kind === 'session') return loadSession(id);
  return loadLevel(id);
}

async function loadLevel(levelId) {
  state.mode = 'level';
  state.levelId = levelId;
  state.level = await api(`/api/level/${encodeURIComponent(levelId)}`);
  state.floorIndex = 0;
  state.selectedRoomIndex = 0;
  resetSelection();
  $('levelPicker').value = `level:${levelId}`;
  renderAll();
}

async function loadPlan(planId) {
  state.mode = 'plan';
  state.planId = planId;
  state.plan = await api(`/api/plan/${encodeURIComponent(planId)}`);
  resetSelection();
  $('levelPicker').value = `plan:${planId}`;
  renderPlanAll();
}

/** A played run. Rendered by the ordinary level path, not a view of its own.
 *
 *  A generated level is the same LevelData shape, so it earns the same floor
 *  map, room cards, enemy rows and loot columns. Only two things differ: the
 *  reachability overlay, and that nothing may be edited — there is no source
 *  file to patch.
 */
async function loadSession(name) {
  state.mode = 'session';
  state.sessionName = name;
  state.level = await api(`/api/session/${encodeURIComponent(name)}`);
  state.levelId = state.level.id;
  state.floorIndex = 0;
  state.selectedRoomIndex = 0;
  resetSelection();
  $('levelPicker').value = `session:${name}`;
  renderAll();
}

async function reloadPlan() {
  state.plan = await api(`/api/plan/${encodeURIComponent(state.planId)}`);
  await syncCatalogsIfStale();
  renderPlanAll();
}

async function reloadLevel() {
  state.level = await api(`/api/level/${encodeURIComponent(state.levelId)}`);
  await syncCatalogsIfStale();
  renderAll();
}

/** Re-fetch the cached catalogs when the dataset behind them has moved.
 *
 *  The libraries, the encounter dropdown and the rarity palette all come from
 *  /api/bootstrap and would otherwise be whatever they were at page load — so
 *  a loot table added outside the editor would never appear in the library.
 *  The revision makes that detectable without re-fetching to find out.
 */
async function syncCatalogsIfStale() {
  const seen = state.level?.revision;
  if (seen == null || seen === state.boot.revision) return;
  state.boot = await api('/api/bootstrap');
}

// ── derived helpers ──────────────────────────────────────────────────────

const currentFloor = () => state.level.floors[state.floorIndex];
const currentRoom = () => currentFloor().rooms[state.selectedRoomIndex];

function issuesFor(pointerPrefix) {
  return (state.level.issues || []).filter(
    (i) => i.pointer && i.pointer.startsWith(pointerPrefix));
}

function catalogItem(list, id) {
  return list.find((x) => x.id === id) || { id, meta: 'not in catalog', code: '??' };
}

// ── rendering ────────────────────────────────────────────────────────────

/** Options for a loot-table picker: every table, plus an explicit "none".
 *  Clearing a picker removes the key rather than writing an empty string —
 *  an omitted lootPlanId and a blank one are not the same thing. */
/** A glyph for a catalog row: the real sprite when we can resolve one.
 *
 *  The sheet is served whole and cropped with background-position — that is
 *  what a sprite sheet is for, and it means the server never decodes a pixel.
 *  The sprite is scaled to fit the box while keeping its aspect ratio, so a
 *  91×96 body and a 512×512 tree both sit sensibly in the same 25px square.
 *
 *  Falls back to the two-letter code: four enemies are drawn procedurally and
 *  have no static sprite at all, and most placement kinds have no prefab art.
 */
/** The larger variant used by the library cards. */
function portrait(sprite) {
  const box = 34;
  const scale = Math.min(box / sprite.width, box / sprite.height);
  const style = [
    `background-image:url('/api/texture?path=${encodeURIComponent(sprite.texture)}')`,
    `background-position:${-sprite.x * scale}px ${-sprite.y * scale}px`,
    `background-size:${sprite.sheetWidth * scale}px ${sprite.sheetHeight * scale}px`,
    `width:${sprite.width * scale}px`,
    `height:${sprite.height * scale}px`,
  ].join(';');
  return `<span class="portrait art" title="${esc(sprite.name)}"><i style="${style}"></i></span>`;
}

function glyph(sprite, code, extraClass = '') {
  if (!sprite) {
    return `<span class="content-glyph ${extraClass}">${esc(code)}</span>`;
  }
  const box = 25;
  const scale = Math.min(box / sprite.width, box / sprite.height);
  const style = [
    `background-image:url('/api/texture?path=${encodeURIComponent(sprite.texture)}')`,
    `background-position:${-sprite.x * scale}px ${-sprite.y * scale}px`,
    `background-size:${sprite.sheetWidth * scale}px ${sprite.sheetHeight * scale}px`,
    `width:${sprite.width * scale}px`,
    `height:${sprite.height * scale}px`,
  ].join(';');
  return `<span class="content-glyph art ${extraClass}" title="${esc(sprite.name)}"
    ><i style="${style}"></i></span>`;
}

function lootOptions(selected) {
  const catalog = state.boot.lootCatalog || [];
  const opts = [`<option value="">⟨none⟩</option>`];
  for (const t of catalog) {
    opts.push(`<option value="${esc(t.id)}" ${t.id === selected ? 'selected' : ''}>${esc(t.id)}</option>`);
  }
  if (selected && !catalog.some((t) => t.id === selected)) {
    // On a generated level this is the normal case, not a fault: the
    // assigner synthesised the table in memory and it exists nowhere on
    // disk, so the GameData catalog cannot contain it.
    const why = state.level && state.level.readOnly ? 'synthesised' : 'missing';
    opts.push(`<option value="${esc(selected)}" selected>${esc(selected)} (${why})</option>`);
  }
  return opts.join('');
}

/** Options for a skill-platform picker.
 *
 *  Only passive skills are offered — SkillPlatformSpawnAdapter throws on an
 *  active one — but whatever is already authored is kept selectable so the
 *  picker never silently misreports the current value. There is no "none":
 *  a skill platform without a skillId is a crash, not a valid state.
 */
function skillOptions(selected) {
  const catalog = state.boot.skillCatalog || [];
  const opts = [];
  if (!selected) opts.push('<option value="" selected>⟨pick a skill⟩</option>');
  for (const sk of catalog) {
    opts.push(`<option value="${esc(sk.id)}" ${sk.id === selected ? 'selected' : ''}
      >${esc(sk.id)}</option>`);
  }
  if (selected && !catalog.some((sk) => sk.id === selected)) {
    opts.push(`<option value="${esc(selected)}" selected>${esc(selected)} (not placeable)</option>`);
  }
  return opts.join('');
}

/** What one wave drops when it dies.
 *
 *  Shown on the enemy row rather than in the Loot column because that is
 *  where it is authored: an enemy drop comes from the encounter action or the
 *  EnemyData, never from the room, so the Loot column — which reads the
 *  room's own lootPlanId and its cargo — can never reveal it. This is the
 *  channel skills reach the player through, and it was invisible.
 */
function dropLine(drop) {
  if (!drop) return '';
  const names = drop.items.map((i) => i.displayName || i.itemId);
  let body;
  if (drop.missing) body = `${drop.tableId} — table not found`;
  else if (drop.source === 'fallback') body = `${drop.fallbackItemId} — no table on this enemy`;
  else if (drop.empty) body = `${drop.tableId} — empty table`;
  else body = `${drop.tableId} — ${names.join(', ')}`;
  if (drop.eliteBonusItemId) body += ` · +${drop.eliteBonusItemId} (elite)`;

  // The whole point of the row: a skill in there is the one thing a designer
  // cannot find anywhere else in the app.
  const cls = [
    'drop-line',
    drop.hasSkill ? 'has-skill' : '',
    drop.missing || drop.empty ? 'bad' : '',
    // An override is authored for THIS wave; the enemy's own table is just
    // the default it came with, so only the former deserves the eye.
    drop.source === 'override' ? 'authored' : '',
  ].filter(Boolean).join(' ');
  const title = drop.source === 'override'
    ? 'dropTableIdOverride on this wave — overrides the enemy\'s own table'
    : drop.source === 'enemy'
      ? "the enemy's own dropTableId"
      : 'no table anywhere in the chain; the last-resort fallback drops instead';
  return `<span class="${cls}" title="${esc(title)}">
    <span class="drop-tag">drops</span>${esc(body)}</span>`;
}

function renderRooms() {
  const floor = currentFloor();
  const rooms = floor.rooms;
  const enemyCatalog = state.boot.enemyCatalog;
  const staticCatalog = state.boot.staticCatalog;

  $('roomsGrid').innerHTML = rooms.map((room, index) => {
    const sel = state.selection;
    const isSelRoom = sel.roomIndex === index;
    const colCls = (kind) => `content-column${isSelRoom && sel.kind === kind ? ' selected-target' : ''}`;
    const slotCls = (kind, i) =>
      isSelRoom && sel.kind === kind && sel.index === i ? ' selected-slot' : '';

    const roomIssues = issuesFor(room.pointer);
    const hasError = roomIssues.some((i) => i.severity === 'error');

    const units = room.enemies.reduce((sum, e) => sum + (e.count || 0), 0);

    const staticEntries = room.staticPlacements.length
      ? room.staticPlacements.map((p, i) => {
          const source = catalogItem(staticCatalog, p.kind);
          // A skill platform names its grant in `skillId` — a SkillData id,
          // not an item or a loot table — so it has to be read from there or
          // the row says nothing about what the player actually picks up.
          const isSkill = p.kind === 'skill';
          const skill = p.skill;
          const detail = isSkill
            ? (p.skillId
                ? (skill.missing
                    ? `${p.skillId} — no SkillData with this id`
                    : [skill.displayName, skill.skillType].filter(Boolean).join(' · '))
                : 'no skillId — the spawn adapter throws')
            : [p.itemId, p.lootTableId, p.mountedEnemyId]
                .filter(Boolean).join(' · ')
                || (p.cells.length ? `${p.cells.length} cell${p.cells.length === 1 ? '' : 's'}` : 'random');
          const broken = isSkill && (!p.skillId || skill.missing
            || (skill.skillType && skill.skillType !== 'passive') || !skill.iconKey);
          const count = p.count != null ? p.count : p.cells.length;
          const picker = isSkill
            ? `<select class="inline-select skill-picker"
                       data-skill-target="${esc(p.pointer)}/skillId"
                       title="The passive skill this platform grants"
                       ${p.skillIdDeclared ? '' : 'disabled'}
                       >${skillOptions(p.skillId)}</select>`
            : '';
          return `<div class="content-entry${isSkill ? ' skill-entry' : ''}${broken ? ' missing' : ''}${slotCls('statics', i)}" data-slot="statics" data-slot-index="${i}">
            ${glyph(isSkill ? skill.sprite : source.sprite, source.code, broken ? 'missing' : '')}
            <span><strong>${esc(p.kind)}</strong><small>${esc(detail)}</small></span>
            <span class="quantity" data-static-index="${i}">
              <button data-delta="-1" title="Decrease count">−</button>
              <input type="number" min="0" max="999" value="${count}" aria-label="${esc(p.kind)} count" />
              <button data-delta="1" title="Increase count">+</button>
            </span>${picker}</div>`;
        }).join('')
      : (room.staticPlacementsDeclared
          ? '<div class="inline-empty">No static placements</div>'
          : '<div class="inline-empty" style="color:var(--danger)">staticPlacements is null — adapters will throw</div>');

    const enemyEntries = room.enemies.length
      ? room.enemies.map((e, i) => {
          const source = catalogItem(enemyCatalog, e.id);
          const bits = [source.meta];
          if (e.eliteCount) bits.push(`elite ×${e.eliteCount}`);
          if (e.viaSpawner) bits.push(`via ${e.viaSpawner}`);
          // When the wave appears. Shown on every row so its absence is never
          // ambiguous, but muted for the immediate ones — 177 of the 190 waves
          // in this data are immediate, and highlighting all of them would
          // bury the handful that are not.
          const when = `<span class="when ${e.gatedByWait ? 'gated' : ''}"
            title="${esc(e.gatedByWait
              ? 'This wave waits — the condition includes any pure-wait steps before it'
              : 'Fires as soon as the playhead reaches this step')}">${esc(e.appearsWhen)}</span>`;
          return `<div class="content-entry drop-carrier ${e.missing ? 'missing' : ''}${slotCls('enemies', i)}" data-slot="enemies" data-slot-index="${i}">
            ${glyph(source.sprite, source.code, e.missing ? 'missing' : '')}
            <span><strong>${esc(e.id)}</strong><small>${when}<span class="when-sep">·</span>${esc(bits.join(' · '))}</small></span>
            <span class="quantity" data-enemy-index="${i}">
              <button data-delta="-1" title="Decrease count">−</button>
              <input type="number" min="0" max="999" value="${e.count}" aria-label="${esc(e.id)} count" />
              <button data-delta="1" title="Increase count">+</button>
            </span>${dropLine(e.drop)}</div>`;
        }).join('')
      : `<div class="inline-empty">${room.encounterId
            ? 'Encounter has no launch steps'
            : 'No encounter — quiet room'}</div>`;

    const decorationEntries = room.decorations.length
      ? room.decorations.map((d, i) => `<div class="content-entry decoration-entry${slotCls('decorations', i)}" data-slot="decorations" data-slot-index="${i}">
          <span class="content-glyph">D</span>
          <span><strong>${esc(d.decalCategory || d.prefabKey || 'decoration')}</strong><small>room decoration</small></span>
          <button class="remove-decoration" data-decoration-remove="${i}" title="Remove decoration">×</button>
        </div>`).join('')
      : '<div class="inline-empty">No decorations</div>';

    // ── loot ────────────────────────────────────────────────────────────
    // Two separate channels, and conflating them is how "my drop didn't
    // happen" bugs start: lootPlanId rolls when the room is cleared, a
    // cargo placement's lootTableId rolls when that box is opened.
    const lootRow = (table, label, pointer, dataType, docId) => {
      if (!table) return '';
      if (table.missing) {
        return `<div class="content-entry missing">
          <span class="content-glyph missing">!</span>
          <span><strong>${esc(table.id)}</strong><small>${esc(label)} · table not found</small></span>
        </div>`;
      }
      const rows = table.entries.map((e) => {
        const count = e.minCount === e.maxCount ? `×${e.minCount}` : `×${e.minCount}–${e.maxCount}`;
        if (e.missing) {
          return `<div class="loot-item missing" title="no ItemData with this id">
            <span class="li-name">${esc(e.itemId)}<small>item not found</small></span>
            <span class="li-count">${count}</span>
            <span class="li-chance">${e.chance}%</span>
          </div>`;
        }
        // Rarity colour comes from the game's own box_rarity.json, not a
        // guessed palette — this data has uncommon blue and rare green.
        const colour = e.rarity ? (state.boot.rarityPalette || {})[e.rarity] : null;
        const tags = [
          e.itemType ? `<span class="li-type">${esc(e.itemType)}</span>` : '',
          e.rarity ? `<span class="li-rarity"${colour ? ` style="color:${esc(colour)}"` : ''}>${esc(e.rarity)}</span>` : '',
        ].filter(Boolean).join('');
        return `<div class="loot-item" title="${esc(e.itemId)}">
          ${e.sprite ? `<i class="li-icon" style="background-image:url('/api/texture?path=${encodeURIComponent(e.sprite.texture)}');background-position:${-e.sprite.x * Math.min(16 / e.sprite.width, 16 / e.sprite.height)}px ${-e.sprite.y * Math.min(16 / e.sprite.width, 16 / e.sprite.height)}px;background-size:${e.sprite.sheetWidth * Math.min(16 / e.sprite.width, 16 / e.sprite.height)}px ${e.sprite.sheetHeight * Math.min(16 / e.sprite.width, 16 / e.sprite.height)}px"></i>` : ''}
          <span class="li-name">${esc(e.displayName)}<small>${tags || esc(e.itemId)}</small></span>
          <span class="li-count">${count}</span>
          <span class="li-chance">${e.chance}%</span>
        </div>`;
      }).join('');
      return `<div class="loot-table${state.selection.kind === 'loot' && state.selection.pointer === pointer ? ' selected-slot' : ''}" data-loot-pointer="${esc(pointer)}">
        <div class="loot-head">
          <span class="loot-label">${esc(label)}</span>
          <select class="inline-select loot-picker"
                  data-loot-target="${esc(pointer)}"
                  data-loot-type="${esc(dataType)}"
                  data-loot-doc="${esc(docId)}">${lootOptions(table.id)}</select>
        </div>
        <div class="loot-mode">${table.pickOne
            ? 'pick one — weights are shares of the total'
            : 'independent — each weight is its own 0–100% roll'}</div>
        ${table.empty ? '<div class="inline-empty">Table is empty</div>' : `<div class="loot-items">${rows}</div>`}
      </div>`;
    };

    const lootEntries = [
      lootRow(room.roomClearLoot, 'room clear', `${room.pointer}/lootPlanId`, 'LevelData', state.levelId),
      ...room.cargoLoot.map((c) =>
        // Guaranteed cargo is pinned to the room, not to one box, so it has
        // no placement index to name.
        lootRow(c.table, c.placementIndex == null ? c.kind : `${c.kind} #${c.placementIndex}`,
                c.pointer, 'LevelData', state.levelId)),
    ].filter(Boolean).join('')
      || '<div class="inline-empty">No loot on this room</div>';

    const stageOptions = ['', 'start', 'teaching', 'building', 'default', 'pressure', 'peak', 'release', 'boss', 'exit']
      .map((v) => `<option value="${v}" ${room.stageType === (v || null) || room.stageType === v ? 'selected' : ''}>${v || '⟨unset⟩'}</option>`)
      .join('');
    const categoryOptions = ['', 'normal', 'basement', 'cave', 'test']
      .map((v) => `<option value="${v}" ${room.roomCategory === v ? 'selected' : ''}>${v || '⟨unset⟩'}</option>`)
      .join('');
    const encounterOptions = [''].concat(state.boot.encounters)
      .map((v) => `<option value="${esc(v)}" ${room.encounterId === (v || null) || room.encounterId === v ? 'selected' : ''}>${v ? esc(v) : '⟨no encounter⟩'}</option>`)
      .join('');

    const doors = DIRS.map((dir) => {
      const open = room.openDoors.includes(dir);
      const door = room.doors.find((d) => d.dir === dir);
      const title = open
        ? `${dir} → ${door.toRoomId}${door.locked ? ' (locked)' : ''}`
        : `Open ${dir} door`;
      return `<button class="door ${open ? 'open' : ''}" data-door="${dir}" title="${esc(title)}">${dir}</button>`;
    }).join('');

    const issueChips = roomIssues.map((i) =>
      `<span class="room-issue ${i.severity}" title="${esc(i.message)}">${esc(i.code)}</span>`).join('');

    return `<article class="room-list-item ${hasError ? 'has-error' : ''}${isSelRoom ? ' is-selected' : ''}" data-index="${index}">
      <section class="room-core">
        <div class="room-core-head">
          <div>
            <span class="room-sequence">ROOM ${String(index + 1).padStart(2, '0')} · ${esc((room.stageType || 'unset').toUpperCase())}</span>
            <h4>${esc(room.roomId)}</h4>
          </div>
          <div class="room-actions">
            ${(room.dropsKeys || []).map((k) =>
              `<span class="chip key" title="This room yields ${esc(k)}">${esc(k)}</span>`).join('')}
            ${room.reachable === false
              ? '<span class="chip stuck" title="No route here from the start room without a key that is itself unreachable">unreachable</span>'
              : ''}
            ${room.isFinalRoom ? '<span class="issue-badge clean">FINAL</span>' : ''}
          </div>
        </div>
        <div class="room-properties">
          <select class="inline-select" data-field="stageType" aria-label="Stage">${stageOptions}</select>
          <select class="inline-select" data-field="roomCategory" aria-label="Category">${categoryOptions}</select>
          <select class="inline-select" data-field="encounterId" aria-label="Encounter">${encounterOptions}</select>
        </div>
        <div class="room-bottom">
          <div><span class="micro-label">Open doors</span><div class="door-row">${doors}</div></div>
        </div>
        ${issueChips ? `<div class="room-issues">${issueChips}</div>` : ''}
      </section>
      <section class="${colCls('decorations')} decoration-content" data-kind="decorations">
        <div class="content-head">Decorations <span>${room.decorations.length}</span></div>
        <div class="content-list">${decorationEntries}</div>
      </section>
      <section class="${colCls('statics')}" data-kind="statics">
        <div class="content-head">Static items <span>${room.staticPlacements.length}</span></div>
        <div class="content-list">${staticEntries}</div>
      </section>
      <section class="${colCls('loot')} loot-content" data-kind="loot">
        <div class="content-head">Loot <span>${room.cargoLoot.length + (room.roomClearLoot ? 1 : 0)}</span></div>
        <div class="content-list">${lootEntries}</div>
      </section>
      <section class="${colCls('enemies')} enemy-content" data-kind="enemies">
        <div class="content-head">Effective enemies <span>${units} units</span></div>
        <div class="content-list">${enemyEntries}
          ${room.encounterId
            ? `<div class="derived-note">derived from encounter ${esc(room.encounterId)} — in step order; edits write to its steps</div>`
            : ''}
        </div>
      </section>
    </article>`;
  }).join('');

  wireRoomCards();
}

// ── selection ────────────────────────────────────────────────────────────
// Pick a target by clicking it, then click a library entry to add into it.
// The libraries used to act on "whatever room the map has selected", which
// made it impossible to say WHICH cargo box a loot table should go on.

const KIND_LABEL = {
  enemies: 'encounter',
  statics: 'static placements',
  loot: 'loot',
  decorations: 'decorations',
};

// The libraries are named for one thing ('enemy'); the room columns they
// write into are named for many ('enemies'). Map once, here.
// A skill platform is a static placement, so the Skill library writes into
// the same column the Static library does.
const LIBRARY_KIND = { enemy: 'enemies', static: 'statics', skill: 'statics', loot: 'loot' };

function selectSlot(roomIndex, kind, { pointer = null, index = null, label = null } = {}) {
  state.selectedRoomIndex = roomIndex;
  state.selection = { roomIndex, kind, pointer, index, label };
  // Selecting must NOT rebuild the room list. Re-rendering would discard an
  // open dropdown, a half-typed number, and the scroll position — the room
  // you just clicked would jump away from you.
  applySelectionStyles();
  renderMap();
  refreshLibraryTargets();
}

/** Bring the selected room's card into view in the Rooms list and flash it.
 *
 *  The map is a glance view of the same rooms the list holds, so selecting in
 *  one has to move the other — otherwise clicking a room on the map appears to
 *  do nothing when its card is scrolled off screen.
 */
function revealSelectedRoom() {
  const card = document.querySelector(
    `.room-list-item[data-index="${state.selection.roomIndex}"]`);
  if (!card) return;
  // jsdom and older engines have no scrollIntoView; the highlight still works.
  if (typeof card.scrollIntoView === 'function') {
    card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
  card.classList.remove('just-revealed');
  // Force a reflow so re-adding the class restarts the animation.
  void card.offsetWidth;
  card.classList.add('just-revealed');
}

/** Move the selection highlight by toggling classes, touching no markup. */
function applySelectionStyles() {
  const sel = state.selection;
  document.querySelectorAll('.room-list-item').forEach((card) => {
    const isRoom = Number(card.dataset.index) === sel.roomIndex;
    card.classList.toggle('is-selected', isRoom);

    card.querySelectorAll('.content-column').forEach((column) =>
      column.classList.toggle('selected-target',
        isRoom && column.dataset.kind === sel.kind));

    card.querySelectorAll('[data-slot]').forEach((entry) =>
      entry.classList.toggle('selected-slot',
        isRoom && entry.dataset.slot === sel.kind
        && Number(entry.dataset.slotIndex) === sel.index));

    card.querySelectorAll('.loot-table').forEach((table) =>
      table.classList.toggle('selected-slot',
        isRoom && sel.kind === 'loot' && table.dataset.lootPointer === sel.pointer));
  });
}

/** Update the "→ room · column" line under each library header. */
function refreshLibraryTargets() {
  for (const kind of ['enemy', 'static', 'skill', 'loot']) {
    const target = $(`${kind}LibraryTarget`);
    if (!target) continue;
    target.textContent = selectionSummary(kind);
    target.classList.toggle('armed', selectionMatches(kind));
  }
}

function selectionRoom() {
  const rooms = currentFloor().rooms;
  return rooms[state.selection.roomIndex] ?? rooms[state.selectedRoomIndex] ?? rooms[0];
}

/** True when this library's additions land somewhere the user just picked. */
function selectionMatches(libraryKind) {
  return state.selection.kind === (LIBRARY_KIND[libraryKind] ?? libraryKind);
}

/** One line telling the user where a library click will land. */
function selectionSummary(libraryKind) {
  // In plan mode there is no room to land on. Leaving the last level's room
  // id here would be worse than useless: `selectionRoom()` still resolves it
  // against `state.level`, so the line would name a real target in a
  // document the user is no longer looking at.
  if (state.mode === 'plan') return 'plans have no rooms to add to';
  if (state.mode === 'session') return 'a session is read-only';
  const room = selectionRoom();
  if (!room) return '';
  const kind = LIBRARY_KIND[libraryKind] ?? libraryKind;
  if (state.selection.label && selectionMatches(libraryKind)) {
    return `→ ${room.roomId} · ${state.selection.label}`;
  }
  return `→ ${room.roomId} · ${KIND_LABEL[kind]}`;
}

/** True when a click landed on something the user is operating, not on the
 *  background they meant to select.
 *
 *  Selection re-renders the card, which destroys any control inside it. A
 *  click on a <select> would therefore reach the selection handler, rebuild
 *  the DOM, and take the dropdown with it before the user could choose —
 *  making loot pickers and quantity inputs impossible to use.
 */
function isInteractive(target) {
  return !!(target && target.closest &&
            target.closest('select, input, textarea, button, option, label'));
}

function wireRoomCards() {
  document.querySelectorAll('.room-list-item').forEach((card) => {
    const roomIndex = Number(card.dataset.index);

    // Clicking a column selects it as the add target; clicking a specific
    // entry narrows that to the one slot.
    card.querySelectorAll('.content-column').forEach((column) =>
      column.addEventListener('click', (event) => {
        if (state.busy || isInteractive(event.target)) return;
        selectSlot(roomIndex, column.dataset.kind);
      }));

    card.querySelectorAll('[data-slot]').forEach((entry) =>
      entry.addEventListener('click', (event) => {
        if (isInteractive(event.target)) return;   // let the control have it
        event.stopPropagation();
        if (state.busy) return;
        const kind = entry.dataset.slot;
        const index = Number(entry.dataset.slotIndex);
        const room = currentFloor().rooms[roomIndex];
        const label = kind === 'enemies' ? `step ${room.enemies[index]?.stepIndex ?? index}`
          : kind === 'statics' ? `${room.staticPlacements[index]?.kind} #${index}`
          : `#${index}`;
        selectSlot(roomIndex, kind, { index, label });
      }));

    // A loot table is its own slot — that is how a table gets assigned to one
    // particular cargo box rather than to the room's clear-loot plan.
    card.querySelectorAll('.loot-table').forEach((table) =>
      table.addEventListener('click', (event) => {
        if (isInteractive(event.target)) return;   // let the picker have it
        event.stopPropagation();
        if (state.busy) return;
        const pointer = table.dataset.lootPointer;
        const label = table.querySelector('.loot-label')?.textContent || 'loot';
        selectSlot(roomIndex, 'loot', { pointer, label });
      }));

    card.querySelectorAll('[data-door]').forEach((button) =>
      button.addEventListener('click', async (event) => {
        event.stopPropagation();
        if (state.busy) return;
        state.selectedRoomIndex = roomIndex;
        await toggleDoor(roomIndex, button.dataset.door,
                         !button.classList.contains('open'));
      }));

    card.querySelectorAll('[data-field]').forEach((select) => {
      select.addEventListener('click', (event) => event.stopPropagation());
      select.addEventListener('change', async (event) => {
        state.selectedRoomIndex = roomIndex;
        await setRoomField(roomIndex, select.dataset.field, event.target.value);
      });
    });

    card.querySelectorAll('[data-enemy-index]').forEach((control) => {
      const input = control.querySelector('input');
      const commit = (value) => setEnemyCount(
        roomIndex, Number(control.dataset.enemyIndex), value);
      control.querySelectorAll('[data-delta]').forEach((button) =>
        button.addEventListener('click', (e) => {
          e.stopPropagation();
          commit(Number(input.value) + Number(button.dataset.delta));
        }));
      input.addEventListener('click', (e) => e.stopPropagation());
      input.addEventListener('change', () => commit(Number(input.value)));
    });

    card.querySelectorAll('[data-static-index]').forEach((control) => {
      const input = control.querySelector('input');
      const commit = (value) => setStaticCount(
        roomIndex, Number(control.dataset.staticIndex), value);
      control.querySelectorAll('[data-delta]').forEach((button) =>
        button.addEventListener('click', (e) => {
          e.stopPropagation();
          commit(Number(input.value) + Number(button.dataset.delta));
        }));
      input.addEventListener('click', (e) => e.stopPropagation());
      input.addEventListener('change', () => commit(Number(input.value)));
    });

    card.querySelectorAll('.loot-picker').forEach((select) => {
      select.addEventListener('click', (event) => event.stopPropagation());
      select.addEventListener('change', async (event) => {
        state.selectedRoomIndex = roomIndex;
        await setLootTable(event.target.dataset.lootTarget, event.target.value);
      });
    });

    card.querySelectorAll('.skill-picker').forEach((select) => {
      select.addEventListener('click', (event) => event.stopPropagation());
      select.addEventListener('change', async (event) => {
        state.selectedRoomIndex = roomIndex;
        await setSkillId(event.target.dataset.skillTarget, event.target.value);
      });
    });

    card.querySelectorAll('[data-decoration-remove]').forEach((button) =>
      button.addEventListener('click', async (event) => {
        event.stopPropagation();
        const room = currentFloor().rooms[roomIndex];
        const index = Number(button.dataset.decorationRemove);
        await applyEdits('LevelData', state.levelId,
          [{ op: 'remove', path: `${room.pointer}/decorations/${index}` }],
          'decoration removed');
      }));
  });
}

// ── floor map ────────────────────────────────────────────────────────────
// Rooms are drawn at the grid coordinates the server derives with the same
// BFS as MiniMapLayoutBuilder, so this map and the in-game minimap agree.
// Production grid Y increases upward; screen Y increases downward, so the
// vertical axis is flipped exactly once, here.

const CELL_W = 30;
const CELL_H = 20;
const GAP = 10;

const gridToPx = (bounds, x, y) => ({
  left: (x - bounds.minX) * (CELL_W + GAP),
  top: (bounds.maxY - y) * (CELL_H + GAP),
});

/** Scale the plane down so a whole floor fits the sidebar panel.
 *  Levels run tall (chapter_1 spans 10 grid rows), so without this the map
 *  would need scrolling in a 230px-high panel and stop being a glance view. */
function fitMapToPanel(plane) {
  const box = $('mapScroll');
  if (!box) return;
  plane.style.transform = 'scale(1)';
  const available = { w: box.clientWidth - 16, h: box.clientHeight - 16 };
  const natural = { w: plane.offsetWidth, h: plane.offsetHeight };
  if (!natural.w || !natural.h || !available.w || !available.h) return;
  // Below ~0.5 the rooms stop being distinguishable, so clamp and let the
  // panel scroll instead of rendering an illegible thumbnail.
  const raw = Math.min(1, available.w / natural.w, available.h / natural.h);
  const scale = Math.max(0.5, raw);
  plane.style.transform = `scale(${scale})`;
  // Centre the scaled plane in the panel.
  plane.style.marginLeft = `${Math.max(0, (available.w - natural.w * scale) / 2)}px`;
  plane.style.marginTop = `${Math.max(0, (available.h - natural.h * scale) / 2)}px`;
}

function renderMap() {
  const floor = currentFloor();
  const placed = floor.rooms.filter((r) => r.hasLayout);
  const plane = $('mapPlane');

  if (!placed.length) {
    plane.innerHTML = '<div class="map-empty">No room on this floor could be placed — '
      + 'the floor has no reachable start room.</div>';
    $('roomCount').textContent = `${floor.rooms.length} rooms`;
    return;
  }

  const bounds = {
    minX: Math.min(...placed.map((r) => r.gridX)),
    maxX: Math.max(...placed.map((r) => r.gridX + r.cols - 1)),
    minY: Math.min(...placed.map((r) => r.gridY)),
    maxY: Math.max(...placed.map((r) => r.gridY + r.rows - 1)),
  };
  plane.style.width = `${(bounds.maxX - bounds.minX + 1) * (CELL_W + GAP)}px`;
  plane.style.height = `${(bounds.maxY - bounds.minY + 1) * (CELL_H + GAP)}px`;

  const byId = Object.fromEntries(floor.rooms.map((r) => [r.roomId, r]));
  const parts = [];

  // Connectors first so room boxes paint over their ends.
  for (const link of floor.layout.connectors) {
    const a = byId[link.from];
    const b = byId[link.to];
    if (!a || !a.hasLayout) continue;

    if (link.crossFloor || !b || !b.hasLayout) {
      // A door leaving this floor. Draw a stub rather than a line to nowhere.
      const pos = gridToPx(bounds, a.gridX, a.gridY);
      parts.push(`<div class="cross-stub" style="left:${pos.left + 6}px;top:${pos.top - 15}px"
        >↕ ${esc(link.to)}</div>`);
      continue;
    }

    const pa = gridToPx(bounds, a.gridX, a.gridY);
    const pb = gridToPx(bounds, b.gridX, b.gridY);
    const ax = pa.left + (a.cols * CELL_W + (a.cols - 1) * GAP) / 2;
    const ay = pa.top + (a.rows * CELL_H + (a.rows - 1) * GAP) / 2;
    const bx = pb.left + (b.cols * CELL_W + (b.cols - 1) * GAP) / 2;
    const by = pb.top + (b.rows * CELL_H + (b.rows - 1) * GAP) / 2;

    const cls = ['map-link'];
    if (link.locked) cls.push('locked');
    if (b.parentRoomId === a.roomId || a.parentRoomId === b.roomId) cls.push('sub');

    const dx = Math.abs(a.gridX - b.gridX);
    const dy = Math.abs(a.gridY - b.gridY);
    const title = `${link.from} ↔ ${link.to}${link.locked ? ` · locked${link.keyId ? ` (${link.keyId})` : ''}` : ''}`;

    if (dx <= 1 && dy === 0) {
      // Horizontal neighbours: a bar sitting in the gap between them.
      const left = Math.min(ax, bx), width = Math.abs(bx - ax);
      parts.push(`<div class="${cls.join(' ')}" title="${esc(title)}"
        style="left:${left}px;top:${ay - 2}px;width:${width}px;height:4px"></div>`);
    } else if (dy <= 1 && dx === 0) {
      const top = Math.min(ay, by), height = Math.abs(by - ay);
      parts.push(`<div class="${cls.join(' ')}" title="${esc(title)}"
        style="left:${ax - 2}px;top:${top}px;width:4px;height:${height}px"></div>`);
    } else {
      // A cycle-closing edge between non-adjacent cells: draw it rotated.
      const length = Math.hypot(bx - ax, by - ay);
      const angle = Math.atan2(by - ay, bx - ax) * 180 / Math.PI;
      cls.push('diagonal');
      parts.push(`<div class="${cls.join(' ')}" title="${esc(title)}"
        style="left:${ax}px;top:${ay}px;width:${length}px;transform:rotate(${angle}deg)"></div>`);
    }
  }

  // Room boxes.
  for (const room of placed) {
    const pos = gridToPx(bounds, room.gridX, room.gridY + room.rows - 1);
    const w = room.cols * CELL_W + (room.cols - 1) * GAP;
    const h = room.rows * CELL_H + (room.rows - 1) * GAP;
    const bad = issuesFor(room.pointer).some((i) => i.severity === 'error');

    const cls = ['map-room'];
    if (room.index === state.selectedRoomIndex) cls.push('selected');
    if (bad) cls.push('has-error');
    if (room.roomId === floor.layout.startRoomId) cls.push('is-start');
    if (room.isFinalRoom) cls.push('is-final');
    if (room.parentRoomId) cls.push('is-sub');
    // On a generated level, a room the player could never have opened.
    if (room.reachable === false) cls.push('unreachable');

    const units = room.enemies.reduce((sum, e) => sum + (e.count || 0), 0);
    const chips = [];
    if (units) chips.push(`<span class="chip enemies">${units} enemies</span>`);
    if (room.staticPlacements.length) chips.push(`<span class="chip">${room.staticPlacements.length} static</span>`);
    if (room.lootPlanId) chips.push('<span class="chip loot">loot</span>');
    if (room.bossId) chips.push('<span class="chip boss">boss</span>');

    const title = [
      room.roomId,
      `grid (${room.gridX}, ${room.gridY})`,
      room.encounterId ? `encounter ${room.encounterId}` : 'no encounter',
      room.parentRoomId ? `anchored to ${room.parentRoomId}` : '',
    ].filter(Boolean).join('\n');

    parts.push(`<div class="${cls.join(' ')}" data-index="${room.index}"
      title="${esc(title)}"
      style="left:${pos.left}px;top:${pos.top}px;width:${w}px;height:${h}px">
      <i class="stage-bar stage-${esc(room.stageType || 'unset')}"></i>
      ${chips.length ? '<i class="dot"></i>' : ''}
    </div>`);
  }

  plane.innerHTML = parts.join('');
  fitMapToPanel(plane);
  plane.querySelectorAll('.map-room').forEach((box) =>
    box.addEventListener('click', () => {
      if (state.busy) return;
      // The map and the room list are two views of one selection. Keep the
      // column the user was working in, so clicking around the map does not
      // silently retarget the libraries.
      selectSlot(Number(box.dataset.index), state.selection.kind);
      revealSelectedRoom();
    }));

  const unplaced = floor.rooms.length - placed.length;
  $('roomCount').textContent = `${floor.rooms.length} rooms`
    + (unplaced ? ` · ${unplaced} unplaced` : '');
}

function renderFloorTabs() {
  $('floorTabs').innerHTML = state.level.floors.map((f, i) =>
    `<button class="${i === state.floorIndex ? 'active' : ''}" data-floor="${i}">FLOOR ${String(f.index + 1).padStart(2, '0')}</button>`
  ).join('');
}

function renderCatalog(list, targetId, query, kind) {
  const q = (query || '').toLowerCase();
  const filtered = list.filter((item) => `${item.id} ${item.meta}`.toLowerCase().includes(q));
  $(targetId).innerHTML = filtered.map((item) => `<article class="asset-card ${kind}" data-id="${esc(item.id)}">
      ${item.sprite ? portrait(item.sprite) : `<span class="portrait">${esc(item.code)}</span>`}
      <span><strong>${esc(item.id)}</strong><small>${esc(item.meta)}</small></span>
      <button class="plus" title="Add to selected room">＋</button>
    </article>`).join('') || '<div class="empty-copy">No matching assets.</div>';
  $(`${kind}LibraryCount`).textContent = `${filtered.length} FOUND`;
  const target = $(`${kind}LibraryTarget`);
  if (target) {
    target.textContent = selectionSummary(kind);
    target.classList.toggle('armed', selectionMatches(kind));
  }

  $(targetId).querySelectorAll('.asset-card').forEach((card) => {
    const add = async (event) => {
      event.stopPropagation();
      if (state.busy) return;
      if (state.mode === 'plan') {
        showToast('A plan has no rooms — open a level to add things to one');
        return;
      }
      if (state.mode === 'session') {
        showToast('A session is a record of a run, not an editable level');
        return;
      }
      if (kind === 'static') await addStatic(card.dataset.id);
      else if (kind === 'skill') await addSkill(card.dataset.id);
      else if (kind === 'loot') await addLoot(card.dataset.id);
      else await addEnemy(card.dataset.id);
    };
    // The whole card adds, so the flow is "pick a target, click a thing".
    card.addEventListener('click', add);
    card.querySelector('.plus').addEventListener('click', add);
  });
}

function renderLevelIssueBadge() {
  if (state.level && state.level.readOnly) {
    const stuck = (state.level.unreachable || []).length;
    $('levelIssues').innerHTML = `<span class="issue-badge ${stuck ? 'error' : 'clean'}">${
      stuck ? `${stuck} unreachable` : 'all reachable'}</span>`;
    return;
  }
  const issues = state.level.issues || [];
  const errors = issues.filter((i) => i.severity === 'error').length;
  const warnings = issues.filter((i) => i.severity === 'warning').length;
  const cls = errors ? 'error' : warnings ? 'warning' : 'clean';
  const text = errors ? `${errors} error${errors === 1 ? '' : 's'}`
    : warnings ? `${warnings} warning${warnings === 1 ? '' : 's'}`
    : 'clean';
  $('levelIssues').innerHTML = `<span class="issue-badge ${cls}">${text}</span>`;
}

// ── plans ────────────────────────────────────────────────────────────────
// A plan is a recipe, not a level. It has no rooms — they are computed at
// generation time — so none of the room-list, floor-map or library machinery
// applies. Rendering it as a floor map would be the single most misleading
// thing this screen could do, which is why the map panel says so out loud
// instead of going blank.

function renderPlanAll() {
  const plan = state.plan;
  $('floorCrumb').textContent = `${plan.floors.length} FLOORS`;
  $('floorTitle').textContent = 'Floor plans';
  $('levelSub').textContent = 'PLAN';
  $('roomCount').textContent = `${plan.floors.length} floors`;
  $('floorTabs').innerHTML = '';
  $('addRoomBtn').hidden = true;
  // Into the plane, NOT the scroller: #mapPlane lives inside #mapScroll, so
  // replacing the scroller's contents deletes the plane and every later
  // render — including the one after a write — hits a null.
  $('mapPlane').innerHTML =
    `<div class="plan-nomap">A plan has no rooms until it is generated.<br>
     <span>Room layout preview needs the headless generator (OLE-1).</span></div>`;
  renderPlanFloors();
  renderPlanIssueBadge();
  for (const [cat, id, box, kind] of [
    [state.boot.enemyCatalog, 'enemyLibrary', 'enemySearch', 'enemy'],
    [state.boot.staticCatalog, 'staticLibrary', 'staticSearch', 'static'],
    [state.boot.skillCatalog, 'skillLibrary', 'skillSearch', 'skill'],
    [state.boot.lootCatalog, 'lootLibrary', 'lootSearch', 'loot'],
  ]) renderCatalog(cat, id, $(box).value, kind);
}

function renderPlanIssueBadge() {
  const issues = state.plan.issues || [];
  const errors = issues.filter((i) => i.severity === 'error').length;
  const warnings = issues.filter((i) => i.severity === 'warning').length;
  const cls = errors ? 'error' : warnings ? 'warning' : 'clean';
  const text = errors ? `${errors} error${errors === 1 ? '' : 's'}`
    : warnings ? `${warnings} warning${warnings === 1 ? '' : 's'}` : 'clean';
  $('levelIssues').innerHTML = `<span class="issue-badge ${cls}">${text}</span>`;
}

function planIssuesFor(pointer) {
  return (state.plan.issues || []).filter(
    (i) => i.pointer === pointer || (i.pointer || '').startsWith(pointer + '/'));
}

function renderPlanFloors() {
  $('roomsGrid').innerHTML = state.plan.floors.map((f) => {
    const issues = planIssuesFor(f.pointer);
    const bad = issues.some((i) => i.severity === 'error');

    const stages = f.stageTypes.length
      ? f.stageTypes.map((st) => `<div class="content-entry plan-stage">
          <span class="content-glyph">${esc((st.stageType || '?').slice(0, 2).toUpperCase())}</span>
          <span><strong>${esc(st.stageType || '?')}</strong><small>${
            st.portion == null ? 'no portion' : `${Math.round(st.portion * 100)}% of rooms`}</small></span>
        </div>`).join('')
      : '<div class="inline-empty">No stage slices</div>';

    const pool = f.enemyPool.length
      ? f.enemyPool.map((e) => `<div class="content-entry ${e.missing ? 'missing' : ''}">
          <span class="content-glyph ${e.missing ? 'missing' : ''}">${esc(codeOf(e.enemyId))}</span>
          <span><strong>${esc(e.enemyId || '?')}</strong><small>${
            e.missing ? 'no EnemyData with this id'
              : `weight ${e.weight ?? 1}${e.maxPerRoom ? ` · max ${e.maxPerRoom}/room` : ''}`
          }</small></span>
        </div>`).join('')
      : '<div class="inline-empty">No enemy pool</div>';

    // Every part the theme owns, on or off. A chip UI rather than a text
    // field because the legal values are a closed set the theme defines, and
    // typing a name that does not resolve throws at room load.
    const owned = f.themeParts;
    const chosen = f.appearance.tilePartConfigs;
    const chips = owned.found
      ? owned.all.map((name) => `<button class="tilepart ${chosen.includes(name) ? 'on' : ''}${
          owned.named.includes(name) ? ' named-only' : ''}"
          data-floor="${f.index}" data-part="${esc(name)}"
          title="${owned.named.includes(name)
            ? 'Owned by the theme but kept out of the random pool — addressable only by name'
            : 'In the theme\'s random pool'}">${esc(name)}</button>`).join('')
      : `<div class="inline-empty" style="color:var(--warn)">Cannot resolve theme ${esc(f.themeId || '(none)')}</div>`;

    const appearanceNote = !chosen.length
      ? 'none selected — rooms draw from the theme\'s full pool'
      : chosen.length === 1
        ? 'one group — the whole floor is pinned to it'
        : `${chosen.length} groups — each room rolls one of them`;

    return `<div class="room-list-item plan-floor${bad ? ' has-error' : ''}" data-floor="${f.index}">
      <div class="content-column plan-head">
        <div class="content-head">Floor ${String(f.index + 1).padStart(2, '0')}</div>
        <div class="plan-facts">
          <div><span>theme</span><strong>${esc(f.themeId || '—')}</strong></div>
          <div><span>generator</span><strong>${esc(f.generatorType || 'tree (default)')}</strong></div>
          <div><span>rooms</span><strong>${f.roomCountMin ?? '?'}–${f.roomCountMax ?? '?'}</strong></div>
          <div><span>enemies/room</span><strong>${f.enemiesPerRoom ?? '—'}</strong></div>
          <div><span>key</span><strong>${esc(f.transitionKeyId || '—')}</strong></div>
          <div><span>boss</span><strong class="${f.hasBoss ? (f.bossMissing ? 'bad' : '') : 'noboss'}">${
            f.hasBoss ? esc(f.bossId) : 'none — no boss room on this floor'}</strong></div>
        </div>
        ${issues.map((i) => `<div class="room-issue ${i.severity}" title="${esc(i.message)}">${esc(i.code)}</div>`).join('')}
      </div>
      <div class="content-column">
        <div class="content-head">Stage mix <span>${f.stageTypes.length}</span></div>
        <div class="content-list">${stages}</div>
        ${f.stageTypes.length ? `<div class="derived-note">portions total ${f.stagePortionTotal}</div>` : ''}
      </div>
      <div class="content-column">
        <div class="content-head">Enemy pool <span>${f.enemyPool.length}</span></div>
        <div class="content-list">${pool}</div>
      </div>
      <div class="content-column">
        <div class="content-head">Tile groups <span>${chosen.length || 'all'}</span></div>
        <div class="tilepart-list">${chips}</div>
        <div class="derived-note">${appearanceNote}</div>
      </div>
    </div>`;
  }).join('');

  $('roomsGrid').querySelectorAll('.tilepart').forEach((chip) =>
    chip.addEventListener('click', () =>
      toggleTilePart(Number(chip.dataset.floor), chip.dataset.part)));
}

/** Add or remove one tile group from a floor's subset. */
async function toggleTilePart(floorIndex, name) {
  const floor = state.plan.floors[floorIndex];
  if (!floor.appearance.declared) {
    // The span writer replaces nodes; it cannot add a key to an object.
    showToast('This floor has no appearance.tilePartConfigs key yet — add it in the JSON first');
    return;
  }
  const current = floor.appearance.tilePartConfigs;
  const at = current.indexOf(name);
  // One element in or out, never a rewrite of the whole array. Setting the
  // array would re-render it in the writer's house style — chapter_3 writes
  // `[ "a", "b" ]` with inner padding, which is not that style — and would
  // reorder entries the author chose. append/remove touch neither.
  const edit = at >= 0
    ? { op: 'remove', path: `${floor.appearance.pointer}/${at}` }
    : { op: 'append', path: floor.appearance.pointer, value: name };
  const after = at >= 0 ? current.filter((n) => n !== name) : [...current, name];
  await applyPlanEdits([edit],
    after.length ? `floor ${floorIndex + 1} tiles → ${after.join(', ')}`
                 : `floor ${floorIndex + 1} tiles → theme default`);
}

async function applyPlanEdits(edits, message) {
  if (!edits.length) return;
  setBusy(true);
  try {
    await post('/api/edit', { dataType: 'LevelBasePlanData', id: state.planId, edits });
    await reloadPlan();
    markSaved(message || 'written');
    if (message) showToast(message);
  } catch (err) {
    showToast(err.message || 'write failed');
    await reloadPlan();
  } finally {
    setBusy(false);
  }
}

const codeOf = (id) => {
  const parts = String(id || '').replace(/-/g, '_').split('_').filter(Boolean);
  if (!parts.length) return '??';
  return (parts.length === 1 ? parts[0].slice(0, 2)
    : parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
};

function renderAll() {
  const readOnly = !!(state.level && state.level.readOnly);
  $('addRoomBtn').hidden = readOnly;
  document.querySelector('.room-canvas').classList.toggle('read-only', readOnly);
  const floor = currentFloor();
  const label = `FLOOR ${String(floor.index + 1).padStart(2, '0')}`;
  $('floorCrumb').textContent = readOnly
    ? `${label} · seed ${state.level.masterSeed}` : label;
  $('floorTitle').textContent = label;
  if (state.selectedRoomIndex >= floor.rooms.length) state.selectedRoomIndex = 0;
  renderFloorTabs();
  $('levelSub').textContent = label;
  renderMap();
  renderRooms();
  if (readOnly) {
    // Belt and braces: the CSS makes them unclickable, this makes them inert
    // even to a keyboard or a script.
    $('roomsGrid').querySelectorAll('input, select, button')
      .forEach((el) => { el.disabled = true; });
  }
  renderLevelIssueBadge();
  renderCatalog(state.boot.enemyCatalog, 'enemyLibrary', $('enemySearch').value, 'enemy');
  renderCatalog(state.boot.staticCatalog, 'staticLibrary', $('staticSearch').value, 'static');
  renderCatalog(state.boot.skillCatalog, 'skillLibrary', $('skillSearch').value, 'skill');
  renderCatalog(state.boot.lootCatalog, 'lootLibrary', $('lootSearch').value, 'loot');
}

// ── writes ───────────────────────────────────────────────────────────────

async function applyEdits(dataType, id, edits, message) {
  if (!edits.length) return;
  if (state.level && state.level.readOnly) {
    showToast('This is a record of a run — there is no source file to edit');
    return;
  }
  setBusy(true);
  try {
    await post('/api/edit', { dataType, id, edits });
    await reloadLevel();
    markSaved(message || 'written');
    if (message) showToast(message);
  } catch (err) {
    // Someone edited the file outside the editor. The patch was computed
    // against the older text, so applying it would restore that text and
    // take their change with it — the server refuses, and we re-read.
    if (err.externallyModified) {
      await reloadLevel();
      showToast('File changed on disk — reloaded. Redo that edit.');
    } else {
      showToast(`Failed: ${err.message}`);
    }
  } finally {
    setBusy(false);
  }
}

async function setRoomField(roomIndex, field, value) {
  const room = currentFloor().rooms[roomIndex];
  // An empty selection means "omit the key", which is NOT the same as writing
  // null or "". stageType is unset on most rooms and must stay that way.
  if (value === '') {
    if (room[field] == null) return;
    await applyEdits('LevelData', state.levelId,
      [{ op: 'remove', path: `${room.pointer}/${field}` }], `${field} cleared`);
    return;
  }
  if (room[field] === value) return;
  if (room[field] == null) {
    showToast(`${room.roomId} has no ${field} key yet — add it in the JSON first`);
    await reloadLevel();
    return;
  }
  await applyEdits('LevelData', state.levelId,
    [{ op: 'set', path: `${room.pointer}/${field}`, value }], `${field} = ${value}`);
}

async function toggleDoor(roomIndex, direction, open) {
  setBusy(true);
  try {
    const result = await post('/api/door', {
      levelId: state.levelId,
      floor: state.floorIndex,
      room: roomIndex,
      direction,
      open,
    });
    if (result.needsTarget) {
      showToast(result.message);
    } else {
      await reloadLevel();
      markSaved('door updated');
      showToast(open
        ? `Door ${direction} opened${result.linked ? ` → ${result.linked} (twin written)` : ''}`
        : `Door ${direction} closed (twin removed)`);
    }
  } catch (err) {
    if (err.externallyModified) {
      await reloadLevel();
      showToast('File changed on disk — reloaded. Redo that door change.');
    } else {
      showToast(`Failed: ${err.message}`);
    }
  } finally {
    setBusy(false);
  }
}

/** Enemy counts live on the encounter, not the room. */
async function setEnemyCount(roomIndex, enemyIndex, raw) {
  const room = currentFloor().rooms[roomIndex];
  const entry = room.enemies[enemyIndex];
  if (!entry || !entry.encounterId) return;
  const next = Math.max(0, Math.min(999, Number.isFinite(raw) ? Math.round(raw) : 0));
  if (next === entry.count) return;
  await applyEdits('EncounterData', entry.encounterId, [
    { op: 'set', path: `${entry.pointer}/min`, value: next },
    { op: 'set', path: `${entry.pointer}/max`, value: next },
  ], `${entry.id} ×${next} in ${entry.encounterId}`);
}

async function setStaticCount(roomIndex, staticIndex, raw) {
  const room = currentFloor().rooms[roomIndex];
  const placement = room.staticPlacements[staticIndex];
  const next = Math.max(0, Math.min(999, Number.isFinite(raw) ? Math.round(raw) : 0));
  // Two placement modes: pinned `cells` or random `count`. Only `count` is a
  // number we can nudge; editing pinned cells needs the grid, not a spinner.
  if (placement.count == null) {
    showToast(`${placement.kind} uses pinned cells — edit cells, not a count`);
    await reloadLevel();
    return;
  }
  if (next === placement.count) return;
  await applyEdits('LevelData', state.levelId,
    [{ op: 'set', path: `${placement.pointer}/count`, value: next }],
    `${placement.kind} ×${next}`);
}

/** Point a loot pointer (room clear or a cargo placement) at a table.
 *  An empty selection removes the key; it does not write "". */
async function setLootTable(pointer, tableId) {
  const current = pointerValue(pointer);
  if ((current || '') === (tableId || '')) return;
  if (!tableId) {
    if (current == null) return;
    await applyEdits('LevelData', state.levelId,
      [{ op: 'remove', path: pointer }], 'loot table cleared');
    return;
  }
  if (current == null) {
    // The key is absent, so there is no span to replace. Adding a key to an
    // existing object is not something the span writer does yet.
    showToast('This room has no loot key yet — add it in the JSON first');
    await reloadLevel();
    return;
  }
  await applyEdits('LevelData', state.levelId,
    [{ op: 'set', path: pointer, value: tableId }], `loot → ${tableId}`);
}

/** Point a skill platform at the passive skill it grants.
 *
 *  Unlike a loot pointer this is never cleared — an absent skillId throws in
 *  SkillPlatformSpawnAdapter rather than placing nothing, so "none" is not a
 *  state the picker offers.
 */
async function setSkillId(pointer, skillId) {
  if (!skillId) return;
  if (pointerValue(pointer) === skillId) return;
  await applyEdits('LevelData', state.levelId,
    [{ op: 'set', path: pointer, value: skillId }], `skill → ${skillId}`);
}

/** Read the value a JSON pointer currently holds in the loaded level view. */
function pointerValue(pointer) {
  const room = currentFloor().rooms.find((r) => pointer.startsWith(r.pointer + '/'));
  if (!room) return null;
  const rest = pointer.slice(room.pointer.length + 1);
  if (rest === 'lootPlanId') return room.lootPlanId;
  const m = rest.match(/^staticPlacements\/(\d+)\/lootTableId$/);
  if (m) {
    const entry = room.cargoLoot.find((c) => c.placementIndex === Number(m[1]));
    return entry ? entry.table.id : null;
  }
  const sk = rest.match(/^staticPlacements\/(\d+)\/skillId$/);
  if (sk) return room.staticPlacements[Number(sk[1])].skillId;
  return null;
}

async function addStatic(kind) {
  const room = selectionRoom();
  if (!room.staticPlacementsDeclared) {
    showToast(`${room.roomId}: staticPlacements is null — fix that first`);
    return;
  }
  // The span writer replaces existing nodes; it cannot add a key to an
  // object. So a kind that needs a companion field has to be born with it,
  // or the picker for that field would have nothing to write into.
  const value = kind === 'skill'
    ? { kind, skillId: '', count: 1 }
    : { kind, count: 1 };
  await applyEdits('LevelData', state.levelId, [{
    op: 'append',
    path: `${room.pointer}/staticPlacements`,
    value,
  }], `${kind} added to ${room.roomId}`);
}

/** Put a skill platform in the selected room, or repoint the selected one.
 *
 *  Unlike the Static library — which adds a bare `kind` and leaves you to
 *  fill in the rest — this arrives complete, because a skill platform with no
 *  skillId throws at room-spawn time rather than placing nothing.
 */
async function addSkill(skillId) {
  const room = selectionRoom();
  const sel = state.selection;
  // "Pick a target, click a thing": with a skill platform selected, this
  // repoints that one instead of adding a second beside it.
  if (sel.kind === 'statics' && sel.index != null) {
    const placement = room.staticPlacements[sel.index];
    if (placement && placement.kind === 'skill') {
      if (!placement.skillIdDeclared) {
        showToast('This placement has no skillId key yet — add it in the JSON first');
        return;
      }
      await setSkillId(`${placement.pointer}/skillId`, skillId);
      return;
    }
  }
  if (!room.staticPlacementsDeclared) {
    showToast(`${room.roomId}: staticPlacements is null — fix that first`);
    return;
  }
  await applyEdits('LevelData', state.levelId, [{
    op: 'append',
    path: `${room.pointer}/staticPlacements`,
    value: { kind: 'skill', skillId, count: 1 },
  }], `skill ${skillId} added to ${room.roomId}`);
}

async function addLoot(tableId) {
  const room = selectionRoom();
  // A specific loot slot was picked (one cargo box, or the room-clear plan),
  // so assign to exactly that pointer.
  if (state.selection.kind === 'loot' && state.selection.pointer) {
    await setLootTable(state.selection.pointer, tableId);
    return;
  }
  if (room.lootPlanId == null) {
    showToast(`${room.roomId} has no lootPlanId key yet — add it in the JSON first`);
    return;
  }
  await setLootTable(`${room.pointer}/lootPlanId`, tableId);
}

async function addEnemy(enemyId) {
  const room = selectionRoom();
  if (!room.encounterId) {
    showToast(`${room.roomId} has no encounter — assign one before adding enemies`);
    return;
  }
  await applyEdits('EncounterData', room.encounterId, [{
    op: 'append',
    path: '/steps',
    value: {
      id: `${enemyId}_${Date.now().toString(36).slice(-4)}`,
      action: { verb: 'launch', enemyId, min: 1, max: 1 },
    },
  }], `${enemyId} added to ${room.encounterId}`);
}

// ── validation ───────────────────────────────────────────────────────────

async function refreshGlobalValidation() {
  const result = await api('/api/validate');
  $('errorCount').textContent = `${result.errors} errors`;
  $('warningCount').textContent = `${result.warnings} warnings`;
  const health = $('healthLabel');
  health.textContent = result.errors ? '● ISSUES FOUND' : '● DATASET HEALTHY';
  health.className = result.errors ? 'danger' : 'ok';

  $('problemsList').innerHTML = result.issues.length
    ? result.issues.map((i) => `<div class="problem-row ${i.severity}">
        <span class="code">${esc(i.code)}</span>
        <span><span class="problem-entity">${esc(i.entity || '')}</span> ${esc(i.message)}</span>
      </div>`).join('')
    : '<div class="problem-row"><span></span><span>Nothing to report.</span></div>';
  return result;
}

// ── top bar ──────────────────────────────────────────────────────────────

$('validateBtn').addEventListener('click', async () => {
  const result = await refreshGlobalValidation();
  $('problemsDrawer').classList.toggle('show');
  showToast(`${result.errors} errors · ${result.warnings} warnings`);
});

$('reloadBtn').addEventListener('click', async () => {
  setBusy(true);
  try {
    state.boot = await api('/api/reload');
    await reloadLevel();
    await refreshGlobalValidation();
    showToast('Reloaded from disk');
  } finally {
    setBusy(false);
  }
});

// Every edit already writes through, so Save is a validate-and-confirm.
$('saveBtn').addEventListener('click', async () => {
  const result = await refreshGlobalValidation();
  markSaved('on disk');
  showToast(result.errors
    ? `On disk · ${result.errors} errors outstanding`
    : 'On disk · no errors');
});

$('addRoomBtn').addEventListener('click', () => {
  showToast('Adding rooms lands with the graph editor — see the plan');
});

$('enemySearch').addEventListener('input', (e) =>
  renderCatalog(state.boot.enemyCatalog, 'enemyLibrary', e.target.value, 'enemy'));
$('staticSearch').addEventListener('input', (e) =>
  renderCatalog(state.boot.staticCatalog, 'staticLibrary', e.target.value, 'static'));
$('skillSearch').addEventListener('input', (e) =>
  renderCatalog(state.boot.skillCatalog, 'skillLibrary', e.target.value, 'skill'));
$('lootSearch').addEventListener('input', (e) =>
  renderCatalog(state.boot.lootCatalog, 'lootLibrary', e.target.value, 'loot'));

$('floorTabs').addEventListener('click', (event) => {
  const button = event.target.closest('[data-floor]');
  if (!button) return;
  state.floorIndex = Number(button.dataset.floor);
  state.selectedRoomIndex = 0;
  resetSelection();
  renderAll();
});

document.addEventListener('keydown', (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
    event.preventDefault();
    $('saveBtn').click();
  }
});

boot();
