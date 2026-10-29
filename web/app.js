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
  // 'level' (authored rooms), 'plan' (a generation recipe with no rooms),
  // 'session' (a captured run) or 'sample' (one generated draw of a plan).
  mode: 'level',
  plan: null,
  planId: null,
  // OLE-1: the generator's status, and the sample on screen.
  generator: null,
  sample: null,
  sampleSeed: null,
  sampleSummary: null,
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
  if (state.planId !== planId) state.sampleSummary = null;
  state.planId = planId;
  state.plan = await api(`/api/plan/${encodeURIComponent(planId)}`);
  state.generator = await api('/api/generator').catch(() => null);
  resetSelection();
  $('levelPicker').value = `plan:${planId}`;
  renderPlanAll();
}

/** OLE-1: one generated draw of the open plan, rendered like a captured run.
 *
 *  ozx_base's own genlevel builds it, so it is the game's layout and not an
 *  approximation. It is a SAMPLE: each seed lays the plan out differently,
 *  and the bar above the rooms says so on every render. A plan that fails
 *  to generate stays on the plan screen with genlevel's message.
 */
async function loadSample(planId, seed) {
  setBusy(true);
  try {
    const view = await api(`/api/plan-sample/${encodeURIComponent(planId)}?seed=${seed}`);
    if (view.missing) {
      state.sample = view.sample;
      if (state.mode !== 'plan' || state.planId !== planId) await loadPlan(planId);
      else renderPlanAll();
      showToast(view.sample.error || 'generation failed');
      return;
    }
    state.mode = 'sample';
    state.planId = planId;
    state.sampleSeed = seed;
    state.sample = view.sample;
    state.level = view;
    state.levelId = view.id;
    state.floorIndex = 0;
    state.selectedRoomIndex = 0;
    resetSelection();
    $('levelPicker').value = `plan:${planId}`;
    renderAll();
  } finally {
    setBusy(false);
  }
}

/** Several seeds at once, summarised — the spread, not one draw. */
async function compareSamples(planId, first, count) {
  setBusy(true);
  try {
    const seeds = Array.from({ length: count }, (_, i) => first + i);
    state.sampleSummary = await api(
      `/api/plan-samples/${encodeURIComponent(planId)}?seeds=${seeds.join(',')}`);
    renderPlanAll();
  } finally {
    setBusy(false);
  }
}

const randomSeed = () => 1 + Math.floor(Math.random() * 99999);

function generatorNote(gen) {
  if (!gen) return '';
  if (!gen.available) return `<span class="stuck">${esc(gen.reason)}</span>`;
  return gen.stale
    ? `<span class="stuck" title="${esc(gen.buildHint)}">The generator build is older than ${esc(gen.newerSource)} — samples show the rules as built, not as written. Rebuild: ${esc(gen.buildHint)}</span>`
    : '<span class="ok">generator build is current</span>';
}

/** The bar above a sample: what it is, and the way to another one. */
function renderSampleBar() {
  const bar = $('sampleBar');
  if (!bar) return;
  if (state.mode !== 'sample' || !state.sample) {
    bar.hidden = true;
    bar.innerHTML = '';
    return;
  }
  const s = state.sample;
  bar.hidden = false;
  bar.innerHTML = `<div class="sample-what"><strong>SAMPLE</strong> of ${esc(s.planId)} · seed
      <input id="sampleBarSeed" type="number" value="${esc(s.seed)}" aria-label="Seed" />
      <span>— one draw of this plan; every seed lays it out differently</span></div>
    <div class="sample-actions">
      <button class="button ghost" id="sampleAgain">↻ Another seed</button>
      <button class="button ghost" id="sampleBack">← Back to plan</button>
    </div>
    ${s.generator && s.generator.stale ? `<div class="sample-warn">${generatorNote(s.generator)}</div>` : ''}`;
  $('sampleAgain').addEventListener('click', () => loadSample(s.planId, randomSeed()));
  $('sampleBack').addEventListener('click', () => loadPlan(s.planId));
  $('sampleBarSeed').addEventListener('change', (event) => {
    const seed = parseInt(event.target.value, 10);
    if (Number.isFinite(seed)) loadSample(s.planId, seed);
  });
}

/** Re-read whatever is open, by what it is. */
async function reopenCurrent() {
  if (state.mode === 'plan') return reloadPlan();
  if (state.mode === 'session') return loadSession(state.sessionName);
  if (state.mode === 'sample') return loadSample(state.sample.planId, state.sampleSeed);
  return reloadLevel();
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
 *  an omitted lootTableId and a blank one are not the same thing. */
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
 *  Only active skills are offered — SkillPlatformSpawnAdapter throws on
 *  anything else since OZX-1400 — but whatever is already authored is kept
 *  selectable so the
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

/** Options for a cargo entry's crate picker (OZX-1468).
 *
 *  "" is the ordinary choice — the field omitted, a random look per crate
 *  (the default crate when pinned). Every CargoData can be named; naming is
 *  the only way an `authoredOnly` one (cargo_premium) appears. An authored
 *  id with no CargoData stays selectable, marked, so the picker never
 *  misreports the current value.
 */
function cargoOptions(selected) {
  const catalog = state.boot.cargoCatalog || [];
  const opts = [`<option value="" ${selected ? '' : 'selected'}>ordinary look</option>`];
  for (const c of catalog) {
    opts.push(`<option value="${esc(c.id)}" ${c.id === selected ? 'selected' : ''}
      >${esc(c.id)}${c.authoredOnly ? ' (named only)' : ''}</option>`);
  }
  if (selected && !catalog.some((c) => c.id === selected)) {
    opts.push(`<option value="${esc(selected)}" selected>${esc(selected)} (no CargoData)</option>`);
  }
  return opts.join('');
}

/** What one wave drops when it dies.
 *
 *  Shown on the enemy row rather than in the Loot column because that is
 *  where it is authored: an enemy drop comes from the encounter action or the
 *  EnemyData, never from the room, so the Loot column — which reads the
 *  room's own prizes and its cargo — can never reveal it. This is the
 *  channel skills reach the player through, and it was invisible.
 */
/** OZX-1063: a variant group and/or a clump, on a pool entry or on a wave.
 *
 * The count belongs to the GROUP — it is sampled, capped and counted as one
 * type — and which form each body takes is drawn at placement time. Saying
 * that out loud is the point: a reader who sees only the representative will
 * read the row as one creature at one weight, which is the arithmetic the
 * fold changed. A clump then decides how those bodies stand: groups of
 * [min, max] around a shared anchor, overflow starting another clump.
 */
function groupLine(e) {
  const variants = e.variantIds || [];
  const c = e.cluster;
  if (!variants.length && !c) return '';
  const bits = [];
  if (variants.length) bits.push(`1 of ${variants.length + 1} forms per body`);
  if (c && c.declared) {
    const size = c.sizeMin === c.sizeMax ? `${c.sizeMax}` : `${c.sizeMin}–${c.sizeMax}`;
    // OZX-1191: a shared root has no ring — every member stands on the
    // anchor and the arc fans them out.
    bits.push(c.sharedRoot
      ? `tufts of ${size} on one root, fanned ${c.fanArcDegrees}°`
      : `clumps of ${size} @ r${c.radius}`);
  } else if (c) {
    // A radius with no size is authored text that does nothing.
    bits.push(`clusterRadius ${c.radius} with no clump size`);
  }
  const bad = c && !c.declared;
  return `<span class="group-line${bad ? ' bad' : ''}"
    title="${esc(variants.length
      ? `Sampled, capped and counted as ONE type: ${[e.id || e.enemyId, ...variants].join(', ')}`
      : 'Inside a clump the minimum-spacing guard is waived, so the radius is what keeps the bodies apart')}">
    <span class="drop-tag">group</span>${esc(bits.join(' · '))}</span>`;
}

/** Bodies in a room: the ones placed on spawn points, plus the ones hatched.
 *
 * OZX-1063 made the second number real. Eggs open into 1..N occupants each
 * and none of them passes through the sampler, so neither `enemiesPerRoom`
 * nor `maxAlive` counts them — a header that reports only the placed bodies
 * understates every room with a clutch in it, systematically.
 */
function bodyCount(enemies) {
  const placed = enemies.reduce((sum, e) => sum + (e.count || 0), 0);
  const lo = enemies.reduce((sum, e) => sum + (e.hatchedMin || 0), 0);
  const hi = enemies.reduce((sum, e) => sum + (e.hatchedMax || 0), 0);
  const label = !hi ? `${placed}`
    : lo === hi ? `${placed} +${hi}`
    : `${placed} +${lo}\u2013${hi}`;
  return { placed, hatchedMin: lo, hatchedMax: hi, label };
}

/** OZX-1063: what a wave of eggs opens into, and how many bodies that adds.
 *
 * The clutch never passes through the sampler, so it is charged to no budget
 * at all — not the floor's `enemiesPerRoom`, not the encounter's `maxAlive`.
 * A room that draws three eggs holds six to nine more bodies than any number
 * on it says, which is precisely why this needs its own line rather than
 * being folded into the count.
 */
function hatchLine(e) {
  const h = e.hatch;
  if (!h) return '';
  const what = h.pool.length
    ? (h.pool.length === 1 ? h.pool[0] : `one of ${h.pool.length}`)
    : h.enemyId;
  const span = e.hatchedMin === e.hatchedMax
    ? `${e.hatchedMax}` : `${e.hatchedMin}–${e.hatchedMax}`;
  return `<span class="hatch-line${h.ambiguous ? ' bad' : ''}"
    title="${esc(h.ambiguous
      ? 'hatchEnemyId and hatchPool are both set — the engine refuses to pick a winner'
      : 'Hatched bodies are outside enemiesPerRoom and outside the encounter\'s maxAlive')}">
    <span class="drop-tag">hatches</span>${esc(`+${span} ${what}`)}</span>`;
}

/** OZX-1057: a static placement's `chance` and `countMin`, when authored.
 *
 * Both are decided once for the whole entry, which is the thing a reader has
 * to know: `count: 3, chance: 0.2` is a room that is either a cultivation
 * chamber with three pods or an ordinary room with none — never one pod in
 * every fifth room. `countMin` reads two ways (`countMinReading`): on a
 * side-wall prop it is a floor the set-piece declines below; everywhere else
 * that reads it — toxicbarrel, groundlaser, a prop off the wall (OZX-1465) —
 * each room rolls a count in [countMin, count]. Returns '' when neither is
 * authored, so an ordinary placement gains no second line.
 */
function oddsLine(p) {
  const bits = [];
  if (p.chance > 0 && p.chance < 1) bits.push(`${p.chancePercent}% to appear at all`);
  // 0 and 1 both mean "no low end" — the entry takes whatever fits.
  if (p.countMin >= 2 && p.countMinReading === 'range') bits.push(`${p.countMin}–${p.count} per room`);
  else if (p.countMin >= 2 && p.countMinReading === 'floor') bits.push(`all-or-nothing below ${p.countMin}`);
  if (!bits.length) return '';
  return `<span class="odds-line" title="Rolled once for the whole entry, not once per object">
    <span class="drop-tag">odds</span>${esc(bits.join(' · '))}</span>`;
}

/** What a pin says in one line: how many carriers, of what, how often.
 *
 * OZX-1041 split "what this group rolls" from "what is pinned here", so a pin
 * is drawn beside the table rather than in place of it — it is paid on top.
 * `chance: 0` means the field was never authored and the pin always fires,
 * which is why the percentage is only shown when it is really below 1.
 */
function pinRow(pin, label) {
  if (!pin) return '';
  const what = pin.tableId
    ? `${pin.tableId} — one of ${pin.drawsFrom.length}`
    : (pin.displayName || pin.itemId);
  const odds = pin.chance > 0 && pin.chance < 1 ? ` · ${pin.chancePercent}%` : '';
  const cls = ['pin-line', pin.hasSkill ? 'has-skill' : '',
               pin.missing ? 'bad' : ''].filter(Boolean).join(' ');
  const title = pin.missing
    ? (pin.tableId
        ? 'a pin may only draw from a pickOne table that has entries'
        : 'no ItemData with this id')
    : label === 'owed'
      ? 'OZX-1380: paid by the last-killed body of the opening wave, on top of its own drops'
      : 'guaranteedDrop — paid on top of whatever the table rolled';
  return `<span class="${cls}" title="${esc(title)}">
    <span class="drop-tag">${esc(label)}</span>${esc(`×${pin.count} ${what}${odds}`)}</span>`;
}

function dropLine(drop) {
  if (!drop) return '';
  const names = drop.items.map((i) => i.tableId
    ? `${i.tableId} (one of)` : (i.displayName || i.itemId));
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
    ? 'dropTableId on this wave — replaces the enemy\'s own table'
    : drop.source === 'enemy'
      ? "the enemy's own dropTableId"
      : 'no table anywhere in the chain; the last-resort fallback drops instead';
  return `<span class="${cls}" title="${esc(title)}">
    <span class="drop-tag">drops</span>${esc(body)}</span>${pinRow(drop.pin, 'pinned')}`;
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

    const bodies = bodyCount(room.enemies);

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
            : [p.cargoId, p.itemId, p.lootTableId, p.mountedEnemyId]
                .filter(Boolean).join(' · ')
                || (p.cells.length ? `${p.cells.length} cell${p.cells.length === 1 ? '' : 's'}` : 'random');
          const broken = isSkill && (!p.skillId || skill.missing
            || (skill.skillType && skill.skillType !== 'active') || !skill.iconKey
            || !(p.charges > 0));
          const count = p.count != null ? p.count : p.cells.length;
          // OZX-1057. Both are rolled/decided once for the whole entry, so
          // they belong beside `count` rather than on the object: `chance`
          // says whether the entry appears at all, `countMin` says the room
          // takes the whole set-piece or none of it — or, off the wall, how
          // low the per-room roll goes.
          const odds = oddsLine(p);
          const picker = isSkill
            ? `<select class="inline-select skill-picker"
                       data-skill-target="${esc(p.pointer)}/skillId"
                       title="The active skill this platform offers, refilled on touch"
                       ${p.skillIdDeclared ? '' : 'disabled'}
                       >${skillOptions(p.skillId)}</select>`
            : p.kind === 'cargo'
              ? `<select class="inline-select cargo-picker"
                         data-cargo-target="${esc(p.pointer)}/cargoId"
                         data-cargo-current="${esc(p.cargoId || '')}"
                         title="OZX-1468: the crate every box of this entry is — omit for a random ordinary look"
                         >${cargoOptions(p.cargoId)}</select>`
              : '';
          return `<div class="content-entry${isSkill ? ' skill-entry' : ''}${broken ? ' missing' : ''}${odds ? ' odds-carrier' : ''}${slotCls('statics', i)}" data-slot="statics" data-slot-index="${i}">
            ${glyph(isSkill ? skill.sprite : source.sprite, source.code, broken ? 'missing' : '')}
            <span><strong>${esc(p.kind)}</strong><small>${esc(detail)}</small></span>
            <span class="quantity" data-static-index="${i}">
              <button data-delta="-1" title="Decrease count">−</button>
              <input type="number" min="0" max="999" value="${count}" aria-label="${esc(p.kind)} count" />
              <button data-delta="1" title="Increase count">+</button>
            </span>${picker}${odds}</div>`;
        }).join('')
      : (room.staticPlacementsDeclared
          ? '<div class="inline-empty">No static placements</div>'
          : '<div class="inline-empty" style="color:var(--danger)">staticPlacements is null — adapters will throw</div>');

    const enemyEntries = room.enemies.length
      ? room.enemies.map((e, i) => {
          const source = catalogItem(enemyCatalog, e.id);
          // OZX-1309 / OZX-1317: a rail launch counts trains (or tanks),
          // not bodies, and lays them along the rail.
          const bits = e.rail === 'train'
            ? [`train of ${e.cars.length}: ${e.cars.join(' + ')}`]
            : e.rail === 'tank' ? ['rail oil tank — neutral, not counted for clear']
            : [source.meta];
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
            </span>${groupLine(e)}${hatchLine(e)}${e.wallClearance > 0
              ? `<span class="group-line" title="OZX-1263: placed only where every room edge is at least this far away; with no such spot the body is dropped">
                   <span class="drop-tag">walls</span>keeps ${e.wallClearance} off every edge</span>` : ''}${dropLine(e.drop)}</div>`;
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
    // happen" bugs start: the room's guaranteedDrops are paid by the
    // last-killed body of its opening wave (OZX-1380), a cargo placement's
    // lootTableId rolls when that box is opened. The room-clear table that
    // used to sit here is gone (OZX-1381).
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
          return `<div class="loot-item missing" title="${esc(e.tableId
              ? 'a slot may only draw from a pickOne table that has entries'
              : 'no ItemData with this id')}">
            <span class="li-name">${esc(e.tableId || e.itemId)}<small>${
              esc(e.tableId ? 'not a usable pickOne table' : 'item not found')}</small></span>
            <span class="li-count">${count}</span>
            <span class="li-chance">${e.chance}%</span>
          </div>`;
        }
        // OZX-1047: the entry names a table, so the weight is still its own
        // chance but the identity is drawn per roll. Naming the family is the
        // honest row — picking one member of it to show would be a lie.
        if (e.tableId) {
          return `<div class="loot-item from-table" title="${esc(e.drawsFrom.join(', '))}">
            <span class="li-name">${esc(e.tableId)}<small>one of ${e.drawsFrom.length} — drawn per roll</small></span>
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
      ...(room.guaranteedDrops || []).map((pin) => pinRow(pin, 'owed')),
      ...room.cargoLoot.map((c) =>
        // Guaranteed cargo is pinned to the room, not to one box, so it has
        // no placement index to name. Since OZX-1041 it is a pin rather than
        // a wrapper table, and a pin has no table row to draw.
        (c.pin ? pinRow(c.pin, 'guaranteed cargo')
               : lootRow(c.table, c.placementIndex == null ? c.kind
                   : `${c.kind} #${c.placementIndex}${c.fromFloor ? ' · floor table' : ''}`,
                         c.pointer, 'LevelData', state.levelId))),
    ].filter(Boolean).join('')
      || '<div class="inline-empty">No loot on this room</div>';

    const stageOptions = ['', 'start', 'teaching', 'building', 'default', 'pressure', 'peak', 'release', 'boss', 'exit', 'story']
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
        ? `${dir} → ${door.exitsRun ? 'exits the run' : door.toRoomId}${door.locked ? ' (locked)' : ''}`
        : `Open ${dir} door`;
      return `<button class="door ${open ? 'open' : ''}" data-door="${dir}" title="${esc(title)}">${dir}</button>`;
    }).join('');

    // OZX-1168: the banner a refused player sees. Offered only where it can
    // show — a door locked with a key, a room with required pickups — or
    // where one is already written, so a stray tip can still be cleared.
    const tipInput = (label, pointer, value, why) => `<label class="tip-field" title="${esc(why)}">
        <span>${esc(label)}</span>
        <input type="text" maxlength="${TIP_MAX_LENGTH}" spellcheck="false"
               data-tip-path="${esc(pointer)}" data-tip-current="${esc(value ?? '')}"
               value="${esc(value ?? '')}" placeholder="no tip">
      </label>`;
    const tips = [
      ...room.doors.filter((d) => (d.locked && d.keyId) || d.lockedTip != null).map((d) =>
        tipInput(`${d.dir} locked`, `${d.pointer}/lockedTip`, d.lockedTip,
          `Shown when this door refuses a player without ${d.keyId || 'its key'}`)),
      ...((room.requiredForExitItemIds || []).length || room.exitBlockedTip != null
        ? [tipInput('exit blocked', `${room.pointer}/exitBlockedTip`, room.exitBlockedTip,
            `Shown when the exit refuses a player who left ${(room.requiredForExitItemIds || []).join(', ') || 'a required pickup'} on the floor`)]
        : []),
    ].join('');

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
            ${room.railCart ? '<span class="chip" title="OZX-1164: a rail cart is parked in this room">cart</span>' : ''}
            ${room.requireRail ? '<span class="chip" title="OZX-1164: this room must draw a tilemap with track, so a cart can ride through">rail</span>' : ''}
            ${room.requireFullGround ? '<span class="chip" title="OZX-1265: this room only draws tilemaps whose ground has no empty cell; none for its door mask is a throw">full ground</span>' : ''}
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
        ${tips ? `<div class="room-tips"><span class="micro-label">HUD tips</span>${tips}</div>` : ''}
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
        <div class="content-head">Loot <span>${room.cargoLoot.length + (room.guaranteedDrops || []).length}</span></div>
        <div class="content-list">${lootEntries}</div>
      </section>
      <section class="${colCls('enemies')} enemy-content" data-kind="enemies">
        <div class="content-head">Effective enemies <span title="${
          bodies.hatchedMax ? 'placed bodies + the clutch its eggs open into, which no budget counts' : ''
        }">${bodies.label} units</span></div>
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
  if (state.mode === 'sample') return 'a sample is generated, not authored — edit the plan';
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

    card.querySelectorAll('[data-tip-path]').forEach((input) => {
      input.addEventListener('click', (event) => event.stopPropagation());
      input.addEventListener('change', async () => {
        state.selectedRoomIndex = roomIndex;
        const current = input.dataset.tipCurrent;
        await setTip(input.dataset.tipPath, current === '' ? null : current, input.value);
      });
    });

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

    card.querySelectorAll('.cargo-picker').forEach((select) => {
      select.addEventListener('click', (event) => event.stopPropagation());
      select.addEventListener('change', async (event) => {
        state.selectedRoomIndex = roomIndex;
        const { cargoTarget, cargoCurrent } = event.target.dataset;
        await setCargoId(cargoTarget, cargoCurrent, event.target.value);
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
      // A door leaving this area. OZX-1116: areas sit side by side and the
      // seam is an ordinary door, so the stub points the way the door faces
      // and names the area — not a stair arrow, which is what it used to be.
      const pos = gridToPx(bounds, a.gridX, a.gridY);
      const arrow = { N: '↑', S: '↓', W: '←', E: '→' }[link.direction] || '→';
      const where = link.toArea == null ? '' : `area ${link.toArea + 1} · `;
      parts.push(`<div class="cross-stub" style="left:${pos.left + 6}px;top:${pos.top - 15}px"
        title="a seam: an ordinary door into the next area">${arrow} ${esc(where + link.to)}</div>`);
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

    const bodies = bodyCount(room.enemies);
    const chips = [];
    if (bodies.placed) chips.push(`<span class="chip enemies">${bodies.label} enemies</span>`);
    if (room.staticPlacements.length) chips.push(`<span class="chip">${room.staticPlacements.length} static</span>`);
    if ((room.guaranteedDrops || []).length) chips.push('<span class="chip loot" title="owes a prize to the last kill of its opening wave">owes</span>');
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
      if (state.mode === 'sample') {
        showToast('A sample is generated from the plan — edit the plan instead');
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
  renderSampleBar();
  $('floorCrumb').textContent = `${plan.floors.length} FLOORS`;
  $('floorTitle').textContent = 'Floor plans';
  $('levelSub').textContent = 'PLAN';
  $('roomCount').textContent = `${plan.floors.length} floors`;
  $('floorTabs').innerHTML = '';
  $('addRoomBtn').hidden = true;
  // Into the plane, NOT the scroller: #mapPlane lives inside #mapScroll, so
  // replacing the scroller's contents deletes the plane and every later
  // render — including the one after a write — hits a null.
  // OLE-1: the plan has no rooms, but ozx_base's genlevel can draw it some.
  const gen = state.generator;
  const failed = state.sample && state.sample.planId === plan.id && state.sample.error
    ? `<span class="stuck" title="${esc(state.sample.detail || '')}">seed ${esc(state.sample.seed)}: ${esc(state.sample.error)}</span>` : '';
  const story = plan.storyRooms && plan.storyRooms.active;
  // OZX-1568: upgrade rooms are story rooms too, counted apart; one in an
  // area a run can skip is the thing to catch, so it is marked bad.
  const upgrade = plan.upgradeStoryRooms && plan.upgradeStoryRooms.active;
  const upgradeCell = (r) => {
    const stray = (r.upgradeAreas || []).filter((a) => !plan.areas.mustPass[a]);
    return `<td class="${stray.length || (r.upgradeRooms ?? 0) !== plan.upgradeStoryRooms.count ? 'bad' : ''}"
      title="${esc(`in ${(r.upgradeAreas || []).map((a) => `area ${a + 1}`).join(', ') || 'no area'}${
        stray.length ? ' — not every run passes there' : ''}`)}">${r.upgradeRooms ?? 0}</td>`;
  };
  const summary = state.sampleSummary && state.sampleSummary.planId === plan.id
    ? `<table class="sample-table"><tr><th>seed</th><th>rooms</th>${
        plan.floors.map((f) => `<th>f${f.index + 1}</th>`).join('')}<th>teleports</th>${
        story ? '<th>story</th>' : ''}${upgrade ? '<th>upgrade</th>' : ''}</tr>${
        state.sampleSummary.samples.map((r) => r.error
          ? `<tr class="bad"><td><button class="link" data-sample-seed="${r.seed}">${r.seed}</button></td><td colspan="${plan.floors.length + 2 + (story ? 1 : 0) + (upgrade ? 1 : 0)}" title="${esc(r.error)}">fails to generate</td></tr>`
          : `<tr><td><button class="link" data-sample-seed="${r.seed}">${r.seed}</button></td><td>${r.rooms}</td>${
              r.floors.map((f) => `<td title="${esc(Object.entries(f.stages).map(([k, v]) => `${k} ${v}`).join(', '))}">${f.rooms}</td>`).join('')}<td>${r.teleports}</td>${
              story ? `<td>${r.storyRooms ?? 0}</td>` : ''}${upgrade ? upgradeCell(r) : ''}</tr>`).join('')}</table>`
    : '';
  $('mapPlane').innerHTML =
    `<div class="plan-nomap">A plan has no rooms until it is generated.
     ${gen && gen.available ? `<div class="sample-controls">
        <label>seed <input id="sampleSeed" type="number" value="${esc(state.sampleSeed || randomSeed())}" /></label>
        <button class="button" id="sampleGo">Generate a sample</button>
        <button class="button ghost" id="sampleCompare" title="Generate five seeds and compare them">Compare 5 seeds</button>
      </div>
      <span>A sample is one draw — another seed lays the plan out differently.</span>` : ''}
      ${generatorNote(gen)}${failed}${summary}</div>`;
  if (gen && gen.available) {
    const seedOf = () => parseInt($('sampleSeed').value, 10) || randomSeed();
    $('sampleGo').addEventListener('click', () => loadSample(plan.id, seedOf()));
    $('sampleCompare').addEventListener('click', () => compareSamples(plan.id, seedOf(), 5));
    $('mapPlane').querySelectorAll('[data-sample-seed]').forEach((b) =>
      b.addEventListener('click', () => loadSample(plan.id, Number(b.dataset.sampleSeed))));
  }
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

// OZX-1011: a floor writes only what differs from `floorDefaults`, and the
// rest is the chapter's. A designer changing a default needs to see which
// floors it reaches, and one looking at a floor needs to see which of its
// values are not its own — editing those edits every floor that inherits.
const INHERITED_TIP = 'inherited from floorDefaults — not written on this floor';

function inheritedMark(f, key) {
  return f.inherited && f.inherited.includes(key) ? ' inherited' : '';
}

function inheritedTag(f, key) {
  return inheritedMark(f, key)
    ? `<em class="inherit-tag" title="${INHERITED_TIP}">default</em>` : '';
}

/** Tile-group chips for a floor (holder = its index) or the defaults block. */
function tileChips(holder, owned, chosen, themeId) {
  return owned.found
    ? owned.all.map((name) => `<button class="tilepart ${chosen.includes(name) ? 'on' : ''}${
        owned.named.includes(name) ? ' named-only' : ''}"
        data-floor="${holder}" data-part="${esc(name)}"
        title="${owned.named.includes(name)
          ? 'Owned by the theme but kept out of the random pool — addressable only by name'
          : 'In the theme\'s random pool'}">${esc(name)}</button>`).join('')
    : `<div class="inline-empty" style="color:var(--amber)">Cannot resolve theme ${esc(themeId || '(none)')}</div>`;
}

function renderPlanDefaults() {
  const d = state.plan.floorDefaults;
  if (!d || !d.declared) return '';
  const issues = planIssuesFor(d.pointer);
  const n = state.plan.floors.length;
  const reach = (key) => {
    const k = d.fanOut[key];
    return k == null ? '' : `<small class="fan-out" title="floors that do not write ${esc(key)} and take this value">${k}/${n} floors</small>`;
  };
  const rows = d.keys.filter((k) => k !== 'floorLootPlan').map((k) => [k, k])
    .concat(Object.keys(d.fanOut).filter((k) => k.startsWith('floorLootPlan/'))
      .map((k) => [k.slice('floorLootPlan/'.length), k]));
  const chosen = d.appearance.tilePartConfigs;
  return `<div class="room-list-item plan-floor plan-defaults${
      issues.some((i) => i.severity === 'error') ? ' has-error' : ''}" data-floor="defaults">
    <div class="content-column plan-head">
      <div class="content-head">Chapter defaults</div>
      <div class="plan-facts">
        <div><span>theme</span><strong>${esc(d.themeId || '—')}</strong></div>
        <div><span>generator</span><strong>${esc(d.generatorType || '—')}</strong></div>
        <div><span>rooms</span><strong>${d.roomCountMin ?? '?'}–${d.roomCountMax ?? '?'}</strong></div>
        <div><span>enemies/room</span><strong>${d.enemiesPerRoom ?? '—'}</strong></div>
      </div>
      ${issues.map((i) => `<div class="room-issue ${i.severity}" title="${esc(i.message)}">${esc(i.code)}</div>`).join('')}
    </div>
    <div class="content-column">
      <div class="content-head">Inherited by <span>${rows.length}</span></div>
      <div class="content-list">${rows.map(([label, key]) => `<div class="content-entry">
        <span><strong>${esc(label)}</strong>${reach(key) || '<small>never inherited</small>'}</span></div>`).join('')}</div>
      <div class="derived-note">a floor that writes a field overrides it whole; arrays replace, never append</div>
    </div>
    <div class="content-column">
      <div class="content-head">Tile groups <span>${chosen.length || 'all'}</span></div>
      <div class="tilepart-list">${tileChips('defaults', d.themeParts, chosen, d.themeId)}</div>
      <div class="derived-note">${d.fanOut.appearance == null ? 'no chapter default'
        : `${d.fanOut.appearance} of ${n} floors draw these`}</div>
    </div>
  </div>`;
}

// OZX-1185: a plan may say how its areas join — door or stair, keyed or
// not — instead of the implicit chain. Without links the chain is the
// answer, and saying so beats an empty card.
//
// OZX-1518: with links, the run ends in the area `finalArea` names — or, when
// it is omitted, the one area no link leaves. Every other dead end is an
// optional side area. OZX-1568: an area is "must pass" when cutting it off
// strands area 0 from the end; upgrade story rooms only land on those.
function renderAreaLinks() {
  const links = state.plan.areaLinks || [];
  const a = state.plan.areas;
  if (!a || (!links.length && !a.declared)) return '';
  const issues = planIssuesFor('/areaLinks').concat(planIssuesFor(a.pointer));
  const name = (i) => {
    const f = state.plan.floors[i];
    return f ? `area ${i + 1}${f.category === 'basement' ? ' (basement)' : ''}` : `area ${i + 1}?`;
  };
  // Omitting finalArea is only an answer while one area is a dead end.
  const derive = a.deadEnds.length === 1
    ? `<option value=""${a.declared ? '' : ' selected'}>${esc(name(a.deadEnds[0]))} — the only dead end</option>` : '';
  const picks = a.deadEnds.map((f) => `<option value="${f}"${
    a.declared && a.finalArea === f ? ' selected' : ''}>${esc(name(f))}</option>`).join('');
  const unset = !a.declared && a.deadEnds.length !== 1
    ? '<option value="" selected disabled>— pick the end —</option>' : '';
  const bad = a.declared && !a.deadEnds.includes(a.finalArea)
    ? `<option value="${a.finalArea}" selected disabled>area ${a.finalArea + 1} (leads on)</option>` : '';
  const role = (i) => (i === a.final ? 'final'
    : a.side.includes(i) ? 'side area — optional dead end'
      : a.mustPass[i] ? 'must pass' : 'optional');
  return `<div class="room-list-item plan-floor plan-links${
      issues.some((i) => i.severity === 'error') ? ' has-error' : ''}" data-floor="links">
    <div class="content-column plan-head">
      <div class="content-head">Area links</div>
      <div class="plan-facts">
        <div><span>ends in</span><select class="inline-select" id="finalAreaPick" aria-label="Final area"
          title="OZX-1518: the area the run ends in (finalArea). It must be one no link leaves; the other dead ends become optional side areas">${
          unset}${bad}${derive}${picks}</select></div>
      </div>
      <div class="derived-note">each link also builds its way back</div>
      ${issues.map((i) => `<div class="room-issue ${i.severity}" title="${esc(i.message)}">${esc(i.code)}</div>`).join('')}
    </div>
    <div class="content-column">
      <div class="content-head">Joins <span>${links.length}</span></div>
      <div class="content-list">${links.map((l) => `<div class="content-entry">
        <span class="content-glyph">${l.kind === 'stair' ? '⇅' : '⇢'}</span>
        <span><strong>${esc(name(l.fromArea))} → ${esc(name(l.toArea))}</strong><small>${
          l.kind === 'stair'
            ? `stair${l.stairRooms && l.stairRooms[1] > 1 ? ` · ${l.stairRooms[0]}–${l.stairRooms[1]} rooms` : ''}`
            : `door${l.keyId ? ` · locked by ${esc(l.keyId)}` : ''}`}</small></span>
      </div>`).join('')}</div>
    </div>
    <div class="content-column">
      <div class="content-head">Areas <span>${a.mustPass.length}</span></div>
      <div class="content-list">${a.mustPass.map((_, i) => `<div class="content-entry">
        <span><strong>${esc(name(i))}</strong><small>${role(i)}</small></span>
      </div>`).join('')}</div>
      <div class="derived-note">must pass = every run walks it; upgrade story rooms land only there</div>
    </div>
  </div>`;
}

function wireAreaLinks() {
  const pick = $('finalAreaPick');
  const a = state.plan.areas;
  if (!pick || !a) return;
  pick.addEventListener('change', () => {
    if (pick.value === '') {
      if (a.declared) applyPlanEdits([{ op: 'remove', path: a.pointer }], 'finalArea removed');
      return;
    }
    applyPlanEdits([{ op: 'put', path: a.pointer, value: Number(pick.value) }],
      `finalArea = ${pick.value}`);
  });
}

// OZX-1483: per run, not per area — the generator hangs countMin–countMax
// one-door story rooms off branch tips / cycle corners, as far apart as the
// layout allows, each with one skill platform. Their skills come from the
// plan's activeSkillIds (OZX-1488), in the Platform skills card.
// No block means no story rooms, and the card offers to add one.
const STORY_ROOMS_DEFAULT = { countMin: 2, countMax: 3, charges: 3 };
const UPGRADE_STORY_ROOMS_DEFAULT = { count: 1 };
const START_SKILL_PLATFORM_DEFAULT = { charges: 3 };

function quantityField(attr, key, label, value, min) {
  return `<div><span>${label}</span>
    <span class="quantity" ${attr}="${key}">
      <button data-delta="-1" title="Decrease ${label}">−</button>
      <input type="number" min="${min}" max="99" value="${value}" aria-label="${label}" />
      <button data-delta="1" title="Increase ${label}">+</button>
    </span></div>`;
}

function wireQuantityFields(attr, current, pointerOf, label) {
  $('roomsGrid').querySelectorAll(`[${attr}]`).forEach((box) => {
    const key = box.getAttribute(attr);
    const input = box.querySelector('input');
    const write = (value) => {
      const n = Math.max(0, Math.min(99, Math.round(Number(value) || 0)));
      if (n === current(key)) { input.value = current(key); return; }
      applyPlanEdits([{ op: 'put', path: pointerOf(key), value: n }], `${label} ${key} = ${n}`);
    };
    input.addEventListener('change', () => write(input.value));
    box.querySelectorAll('[data-delta]').forEach((b) => b.addEventListener('click',
      () => write(current(key) + Number(b.dataset.delta))));
  });
}

function renderStoryRooms() {
  const s = state.plan.storyRooms;
  if (!s) return '';
  const issues = planIssuesFor(s.pointer);
  if (!s.declared) {
    return `<div class="room-list-item plan-floor plan-links" data-floor="story">
      <div class="content-column plan-head">
        <div class="content-head">Story rooms</div>
        <div class="derived-note">none — no skill-platform rooms are hung off the layout</div>
      </div>
      <div class="content-column">
        <button class="button ghost" id="storyRoomsAdd" title="Write a storyRooms block (OZX-1483)">Add story rooms</button>
      </div>
    </div>`;
  }
  const field = (key, label, min) => quantityField('data-story-field', key, label, s[key], min);
  return `<div class="room-list-item plan-floor plan-links${
      issues.some((i) => i.severity === 'error') ? ' has-error' : ''}" data-floor="story">
    <div class="content-column plan-head">
      <div class="content-head">Story rooms</div>
      <div class="plan-facts">
        ${field('countMin', 'min', 1)}${field('countMax', 'max', 1)}${field('charges', 'charges', 1)}
      </div>
      ${issues.map((i) => `<div class="room-issue ${i.severity}" title="${esc(i.message)}">${esc(i.code)}</div>`).join('')}
    </div>
    <div class="content-column">
      <div class="derived-note">one-door dead ends hung off branch tips and cycle corners, spread as far apart as the layout allows; each platform's skill is drawn from Platform skills</div>
      <button class="button ghost" id="storyRoomsRemove" title="Delete the storyRooms block">Remove story rooms</button>
    </div>
  </div>`;
}

// OZX-1568: a fixed number of story rooms per run holding an equipment
// upgrade platform — the only way into upgrades. Placed with the skill story
// rooms, but only on areas every run passes through (see Area links).
function renderUpgradeRooms() {
  const u = state.plan.upgradeStoryRooms;
  if (!u) return '';
  const issues = planIssuesFor(u.pointer);
  if (!u.declared) {
    return `<div class="room-list-item plan-floor plan-links" data-floor="upgrade">
      <div class="content-column plan-head">
        <div class="content-head">Upgrade rooms</div>
        <div class="derived-note">none — no run is promised an equipment upgrade platform</div>
      </div>
      <div class="content-column">
        <button class="button ghost" id="upgradeRoomsAdd" title="Write an upgradeStoryRooms block (OZX-1568)">Add upgrade rooms</button>
      </div>
    </div>`;
  }
  return `<div class="room-list-item plan-floor plan-links${
      issues.some((i) => i.severity === 'error') ? ' has-error' : ''}" data-floor="upgrade">
    <div class="content-column plan-head">
      <div class="content-head">Upgrade rooms</div>
      <div class="plan-facts">${quantityField('data-upgrade-field', 'count', 'per run', u.count, 1)}</div>
      ${issues.map((i) => `<div class="room-issue ${i.severity}" title="${esc(i.message)}">${esc(i.code)}</div>`).join('')}
    </div>
    <div class="content-column">
      <div class="derived-note">one-door story rooms with an upgrade platform, on top of the skill story rooms; placed with them, but only in areas every run passes through</div>
      <button class="button ghost" id="upgradeRoomsRemove" title="Delete the upgradeStoryRooms block">Remove upgrade rooms</button>
    </div>
  </div>`;
}

function wireUpgradeRooms() {
  const u = state.plan.upgradeStoryRooms;
  if (!u) return;
  const add = $('upgradeRoomsAdd');
  if (add) add.addEventListener('click', () => applyPlanEdits(
    [{ op: 'put', path: u.pointer, value: UPGRADE_STORY_ROOMS_DEFAULT }], 'upgrade rooms added'));
  const remove = $('upgradeRoomsRemove');
  if (remove) remove.addEventListener('click', () => applyPlanEdits(
    [{ op: 'remove', path: u.pointer }], 'upgrade rooms removed'));
  wireQuantityFields('data-upgrade-field', (k) => u[k], (k) => `${u.pointer}/${k}`, 'upgrade rooms');
}

function wireStoryRooms() {
  const s = state.plan.storyRooms;
  if (!s) return;
  const add = $('storyRoomsAdd');
  if (add) add.addEventListener('click', () => applyPlanEdits(
    [{ op: 'put', path: s.pointer, value: STORY_ROOMS_DEFAULT }], 'story rooms added'));
  const remove = $('storyRoomsRemove');
  if (remove) remove.addEventListener('click', () => applyPlanEdits(
    [{ op: 'remove', path: s.pointer }], 'story rooms removed'));
  wireQuantityFields('data-story-field', (k) => s[k], (k) => `${s.pointer}/${k}`, 'story rooms');
}

// OZX-1488: the chapter's active skills, which the start room's platform and
// the story rooms draw from together without replacement, so one run never
// offers a skill twice. The start platform is an optional block of its own.
function renderSkillPlatforms() {
  const p = state.plan.skillPlatforms;
  if (!p) return '';
  const issues = [...planIssuesFor(p.pointer), ...planIssuesFor(p.start.pointer)];
  const taken = new Set(p.skills.map((k) => k.id));
  const choices = (state.boot.skillCatalog || []).filter((k) => !taken.has(k.id));
  const skills = p.skills.length
    ? p.skills.map((k, i) => `<div class="content-entry${k.missing ? ' missing' : ''}">
        ${glyph(k.sprite, codeOf(k.id), k.missing ? 'missing' : '')}
        <span><strong>${esc(k.displayName || k.id)}</strong><small>${esc(
          k.missing ? 'no SkillData' : `${k.id} · ${k.skillType || '?'}`)}</small></span>
        <button class="remove-decoration" data-skill-remove="${i}" title="Remove ${esc(k.id)}">×</button>
      </div>`).join('')
    : `<div class="inline-empty">No skills${p.drawn ? ' — every platform needs one' : ''}</div>`;
  const start = p.start.declared
    ? `<div class="plan-facts">${quantityField('data-start-skill-field', 'charges', 'start platform charges', p.start.charges, 1)}</div>
       <button class="button ghost" id="startSkillRemove" title="Delete the startSkillPlatform block">Remove start platform</button>`
    : `<div class="derived-note">no platform in the start room</div>
       <button class="button ghost" id="startSkillAdd" title="Write a startSkillPlatform block (OZX-1488)">Add start platform</button>`;
  return `<div class="room-list-item plan-floor plan-links${
      issues.some((i) => i.severity === 'error') ? ' has-error' : ''}" data-floor="skills">
    <div class="content-column plan-head">
      <div class="content-head">Start room platform</div>
      ${start}
      ${issues.map((i) => `<div class="room-issue ${i.severity}" title="${esc(i.message)}">${esc(i.code)}</div>`).join('')}
    </div>
    <div class="content-column">
      <div class="content-head">Platform skills <span>${p.skills.length}</span></div>
      <div class="content-list">${skills}</div>
      ${choices.length ? `<select id="platformSkillAdd" aria-label="Add a skill">
        <option value="">+ add an active skill…</option>
        ${choices.map((k) => `<option value="${esc(k.id)}">${esc(k.displayName || k.id)}</option>`).join('')}
      </select>` : ''}
      <div class="derived-note">a run draws up to ${p.drawn} (start platform + story rooms max) and never offers a skill twice, so list at least that many</div>
    </div>
  </div>`;
}

function wireSkillPlatforms() {
  const p = state.plan.skillPlatforms;
  if (!p) return;
  const add = $('startSkillAdd');
  if (add) add.addEventListener('click', () => applyPlanEdits(
    [{ op: 'put', path: p.start.pointer, value: START_SKILL_PLATFORM_DEFAULT }], 'start platform added'));
  const remove = $('startSkillRemove');
  if (remove) remove.addEventListener('click', () => applyPlanEdits(
    [{ op: 'remove', path: p.start.pointer }], 'start platform removed'));
  wireQuantityFields('data-start-skill-field', (k) => p.start[k],
    (k) => `${p.start.pointer}/${k}`, 'start platform');
  $('roomsGrid').querySelectorAll('[data-skill-remove]').forEach((b) => b.addEventListener('click', () => {
    const i = Number(b.dataset.skillRemove);
    applyPlanEdits([{ op: 'remove', path: `${p.pointer}/${i}` }], `platform skills − ${p.skills[i].id}`);
  }));
  const pick = $('platformSkillAdd');
  if (pick) pick.addEventListener('change', () => {
    const id = pick.value;
    if (!id) return;
    // One element in, as with tile groups — but a plan without the array
    // has nothing to append to, so that one case writes it whole.
    applyPlanEdits([p.declared
      ? { op: 'append', path: p.pointer, value: id }
      : { op: 'put', path: p.pointer, value: [id] }], `platform skills + ${id}`);
  });
}

function renderPlanFloors() {
  $('roomsGrid').innerHTML = renderPlanDefaults() + renderAreaLinks() + renderSkillPlatforms() + renderStoryRooms() + renderUpgradeRooms() + state.plan.floors.map((f) => {
    const issues = planIssuesFor(f.pointer);
    const bad = issues.some((i) => i.severity === 'error');

    // OZX-1089: `enemyBudgetCut` is the SHARE of enemiesPerRoom the stage
    // gives back, so a full cut is a room with no fight in it — the thing a
    // stage list most needs to say and could not before.
    const stages = f.stageTypes.length
      ? f.stageTypes.map((st) => {
          const cut = st.enemyBudgetCut;
          const budget = cut <= 0 ? ''
            : `<span class="weight-value${cut >= 1 ? ' zero' : ''}"
                 title="${esc(cut >= 1
                   ? 'a full cut: this stage spawns no enemies at all'
                   : `gives back ${Math.round(cut * 100)}% of the floor's enemiesPerRoom`)}">${
                 st.enemyBudget == null ? `-${Math.round(cut * 100)}%` : `${st.enemyBudget}♦`}</span>`;
          return `<div class="content-entry plan-stage">
            <span class="content-glyph">${esc((st.stageType || '?').slice(0, 2).toUpperCase())}</span>
            <span><strong>${esc(st.stageType || '?')}</strong><small>${
              st.portion == null ? 'no portion' : `${Math.round(st.portion * 100)}% of rooms`}${
              (st.dressing || []).length ? ` · dressed: ${esc(st.dressing.map((d) => `${d.decalCategory} ×${d.count}`).join(', '))}` : ''}${
              st.secondWave ? ` · ${Math.round(st.secondWave.chance * 100)}% of rooms get a second wave of +${Math.round(st.secondWave.share * 100)}% (${esc(st.secondWave.triggers.join(' / '))})` : ''}</small></span>
            ${budget}</div>`;
        }).join('')
      : '<div class="inline-empty">No stage slices</div>';

    const pool = f.enemyPool.length
      ? f.enemyPool.map((e) => {
          // OZX-1032: a spawner produces nothing on its own, so the pair that
          // makes it produce anything has to be visible on the row. These
          // minions are ON TOP of enemies/room — the room's real body count
          // is enemiesPerRoom + minionsPerSpawner × however many drew.
          const channel = e.isSpawner
            ? `<span class="spawner-line" title="${esc(f.spawnerMinionsPerRoom > 0
                ? `OZX-1219: each of these that a room draws is fed from the room's own allowance of ${f.spawnerMinionsPerRoom} minions, shared by every spawner; enemies/room counts only bodies placed directly`
                : `OZX-1064: each of these that a room draws is paired with a viaSpawner launch of ${e.minionId}, charged to the room's enemies/room and capped by the minion's maxPerRoom`)}">
                 <span class="drop-tag">spawns</span>${esc(e.minionIsTrain
                   ? `→ ${e.minionsPerSpawner} train${e.minionsPerSpawner === 1 ? '' : 's'} of ${e.minionId} each`
                   : `×${e.minionsPerSpawner} ${e.minionId} each`)}</span>`
            // Half a pair reads as a spawner to a designer and as an ordinary
            // enemy to the sampler. The validator errors; the row says why.
            : (e.minionId || e.minionsPerSpawner)
              ? `<span class="spawner-line bad" title="Both minionId and minionsPerSpawner are required; with one missing this is sampled as an ordinary enemy">
                   <span class="drop-tag">spawns</span>${esc(
                     e.minionId ? `${e.minionId} — no minionsPerSpawner`
                                : `×${e.minionsPerSpawner} — no minionId`)}</span>`
              : '';
          return `<div class="content-entry pool-entry ${e.missing ? 'missing' : ''}">
            <span class="content-glyph ${e.missing ? 'missing' : ''}">${esc(codeOf(e.railTrainId || e.enemyId))}</span>
            <span><strong>${esc(e.railTrainId || e.enemyId || '?')}</strong><small>${
              e.missing ? (e.railTrainId ? 'no RailTrainData with this id' : 'no EnemyData with this id')
                : `${e.railTrainId ? 'train on the rail · ' : ''}${
                    e.family ? `tier ${e.family.tier} ${e.family.id} · ` : ''}weight ${e.weight ?? 1}${e.maxPerRoom ? ` · max ${e.maxPerRoom}/room` : ''}`
            }</small></span>${groupLine(e)}${channel}
          </div>`;
        }).join('')
      : '<div class="inline-empty">No enemy pool</div>';

    // A procedural floor has no hand-written rooms, so this is where a
    // chapter's props are actually authored — and the only place `chance`
    // and `countMin` appear in shipped data.
    const placements = f.defaultStaticPlacements.length
      ? f.defaultStaticPlacements.map((p) => {
          const odds = oddsLine(p);
          const what = [p.prefabKey, p.cargoId, p.placement].filter(Boolean).join(' · ');
          return `<div class="content-entry${odds ? ' odds-carrier' : ''}">
            <span class="content-glyph">${esc(codeOf(p.kind))}</span>
            <span><strong>${esc(p.kind || '?')}</strong><small>${
              esc(what || (p.pinned ? 'pinned cells' : 'adapter default'))}</small></span>
            <span class="weight-value" title="how many at most">×${p.count || (p.pinned ? '—' : 0)}</span>
            ${p.skipsStages.length ? `<span class="odds-line" title="StaticPlacementPolicy (OZX-1057 / OZX-1225 / OZX-1486): the start room is where the player spawns, at launch and after every death, so it takes no blast hazard or floor trap; a release room is the breather after a peak, so it takes no floor trap">
              <span class="drop-tag">except</span>${esc(p.skipsStages.join(' / '))} rooms</span>` : ''}
            ${odds}</div>`;
        }).join('')
      : '<div class="inline-empty">No default placements — generated rooms spawn nothing static</div>';

    // OZX-1044: a stage weight may scope itself to a taxonomy category, so
    // this is a sparse (stage × category) table, not one row per stage. The
    // category-free row is the stage's fallback — what anything the designer
    // did not scope gets — so it is labelled as such rather than left blank,
    // which would read as a missing value.
    const weights = f.stageWeights.length
      ? f.stageWeights.map((w) => `<div class="content-entry weight-entry${
            w.unknownCategory ? ' missing' : ''}">
          <span class="content-glyph">${esc((w.stageType || '?').slice(0, 2).toUpperCase())}</span>
          <span><strong>${esc(w.stageType || '?')}</strong><small>${
            w.category
              ? `${esc(w.category)}${w.unknownCategory ? ' — not a taxonomy node' : ''}`
              : 'everything else on this stage'}</small></span>
          <span class="weight-value${w.weight === 0 ? ' zero' : ''}"
                title="${w.weight === 0 ? 'this stage drops nothing of that kind' : 'multiplier'}">×${w.weight ?? 1}</span>
        </div>`).join('')
      : '<div class="inline-empty">No stage weights — every stage is ×1</div>';

    const scoped = f.stageWeights.filter((w) => w.category).length;
    const weightNote = scoped
      ? `${scoped} scoped by category · most specific wins, then the stage fallback, then ×1`
      : 'no category scoping — each row scales everything that stage drops';

    // Every part the theme owns, on or off. A chip UI rather than a text
    // field because the legal values are a closed set the theme defines, and
    // typing a name that does not resolve throws at room load.
    const owned = f.themeParts;
    const chosen = f.appearance.tilePartConfigs;
    const chips = tileChips(f.index, owned, chosen, f.themeId);

    const appearanceNote = !chosen.length
      ? 'none selected — rooms draw from the theme\'s full pool'
      : chosen.length === 1
        ? 'one group — the whole floor is pinned to it'
        : `${chosen.length} groups — each room rolls one of them`;

    return `<div class="room-list-item plan-floor${bad ? ' has-error' : ''}" data-floor="${f.index}">
      <div class="content-column plan-head">
        <div class="content-head">Floor ${String(f.index + 1).padStart(2, '0')}${
          f.category === 'basement' ? ' <em class="inherit-tag" title="OZX-1185: a themes/basement area draws only the basement library">basement</em>' : ''}${
          f.elevation ? ` <em class="inherit-tag" title="OZX-1338: stairs point by height">elev ${f.elevation > 0 ? '+' : ''}${f.elevation}</em>` : ''}</div>
        <div class="plan-facts">
          <div><span>theme</span><strong class="${inheritedMark(f, 'themeId')}">${esc(f.themeId || '—')}</strong></div>
          <div><span>generator</span><strong class="${inheritedMark(f, 'generatorType')}">${esc(f.generatorType || 'tree (default)')}</strong></div>
          <div><span>rooms</span><strong class="${inheritedMark(f, 'roomCountMin') || inheritedMark(f, 'roomCountMax')}">${f.roomCountMin ?? '?'}–${f.roomCountMax ?? '?'}</strong></div>
          <div><span>enemies/room</span><strong class="${inheritedMark(f, 'enemiesPerRoom')}">${f.enemiesPerRoom ?? '—'}</strong></div>
          ${f.spawnerMinionsPerRoom > 0 ? `<div><span>minion lines</span><strong class="${inheritedMark(f, 'spawnerMinionsPerRoom')}"
            title="OZX-1219: spawner production lines draw from this allowance instead of enemies/room">${f.spawnerMinionsPerRoom}/room</strong></div>` : ''}
          ${f.pathDepthEnemyScale.length ? `<div><span>depth share</span><strong class="${inheritedMark(f, 'pathDepthEnemyScale')}"
            title="OZX-1558: share of a room's enemy budget, minion allowance and elite count it keeps at pathDepth 0 / 1 / 2 (spine / branch / sub-branch)">${
            esc(f.pathDepthEnemyScale.map((d) => `×${d}`).join(' · '))}</strong></div>` : ''}
          ${f.treeLoops ? `<div><span>loops</span><strong class="${inheritedMark(f, 'treeShape')}"
            title="OZX-1578: trunk loops (balanced ones keep both arms on the spine, depth 0; the others detour at depth 1), branch-end rings (depth 2), and how many rows a loop spans">${
            esc(`trunk ${f.treeLoops.trunk.join('–')} (balanced ${f.treeLoops.balanced.join('–')}) · ring ${f.treeLoops.branch.join('–')} · ${f.treeLoops.length.join('–')} rows`)}</strong></div>` : ''}
          ${f.cycleBranches ? `<div><span>branches</span><strong class="${inheritedMark(f, 'cycleShape')}"
            title="OZX-1579: branches off the ring (depth 1) — side ones straight out of a wall, top ones straight up out of the top row, never off the stairs room — and how many rooms each">${
            esc(`side ${f.cycleBranches.side.join('–')} · top ${f.cycleBranches.top.join('–')} · ${f.cycleBranches.length.join('–')} rooms`)}</strong></div>` : ''}
          ${f.depthBox ? `<div><span>depth box</span><strong class="${f.depthBox.missing ? 'bad' : inheritedMark(f, 'floorLootPlan/depthBoxTableId')}"
            title="${esc(`OZX-1560: one draw from this table, pinned to the deepest off-spine room that can pay it; rarity rises by that room's depth — ${
              f.depthBox.rarityBumps.map((b) => `depth ${b.pathDepth}: ${b.weights.map((w, k) => `+${k} ${w}`).join(', ')}`).join('; ') || 'no rarity bumps'}`)}">${
            esc(f.depthBox.tableId)}${f.depthBox.missing ? ' (missing)' : ''}</strong></div>` : ''}
          ${f.tipSkill ? `<div><span>tip skill</span><strong class="${f.tipSkill.missing ? 'bad' : inheritedMark(f, 'floorLootPlan/tipSkillTableId')}"
            title="OZX-1561: one draw from this skill roster, pinned to a branch tip off the spine — preferring one the depth box is not in">${
            esc(f.tipSkill.tableId)}${f.tipSkill.missing ? ' (missing)' : ''}</strong></div>` : ''}
          <div><span>key</span><strong class="${inheritedMark(f, 'transitionKeyId')}">${esc(f.transitionKeyId || '—')}</strong></div>
          <div><span>rail</span><strong class="${f.railCart ? inheritedMark(f, 'railCart') : 'noboss'}"
            title="OZX-1164: every room draws a tilemap with track, and the start room gets a cart. A floor can switch it on over the chapter, never off.">${
            f.railCart ? 'on — every room, cart at start' : 'off'}</strong></div>
          <div><span>skill cap</span><strong class="${state.plan.skillDropLevelCapDeclared ? '' : 'noboss'}"
            title="OZX-1069: a skill stops DROPPING at this level. Not a level clamp — maxLevel still rules.">${
            state.plan.skillDropLevelCapDeclared
              ? `L${state.plan.skillDropLevelCap}`
              : `L${state.plan.skillDropLevelCapEffective} (default)`}</strong></div>
          <div><span>boss</span><strong class="${f.hasBoss ? (f.bossMissing ? 'bad' : '') : 'noboss'}">${
            f.hasBoss ? esc(f.bossId)
              : f.endsRunOnClear ? 'none — the run ends on its last peak room'
              : 'none — no boss room on this floor'}</strong></div>
        </div>
        ${issues.map((i) => `<div class="room-issue ${i.severity}" title="${esc(i.message)}">${esc(i.code)}</div>`).join('')}
      </div>
      <div class="content-column">
        <div class="content-head">Stage mix ${inheritedTag(f, 'stageTypes')}<span>${f.stageTypes.length}</span></div>
        <div class="content-list">${stages}</div>
        ${f.stageTypes.length ? `<div class="derived-note">portions total ${f.stagePortionTotal}</div>` : ''}
      </div>
      <div class="content-column">
        <div class="content-head">Enemy pool ${inheritedTag(f, 'enemyPool')}<span>${f.enemyPool.length}</span></div>
        <div class="content-list">${pool}</div>
      </div>
      <div class="content-column">
        <div class="content-head">Default placements ${inheritedTag(f, 'defaultStaticPlacements')}<span>${f.defaultStaticPlacements.length || '—'}</span></div>
        <div class="content-list">${placements}</div>
        ${f.defaultStaticPlacements.length
          ? '<div class="derived-note">applied to every generated room whose own staticPlacements came out empty</div>'
          : ''}
      </div>
      <div class="content-column">
        <div class="content-head">Stage weights ${inheritedTag(f, 'floorLootPlan/stageWeights')}<span>${f.stageWeights.length || '—'}</span></div>
        <div class="content-list">${weights}</div>
        ${f.stageWeights.length ? `<div class="derived-note">${weightNote}</div>` : ''}
      </div>
      <div class="content-column">
        <div class="content-head">Tile groups ${inheritedTag(f, 'appearance')}<span>${chosen.length || 'all'}</span></div>
        <div class="tilepart-list${f.appearance.inherited ? ' inherited' : ''}">${chips}</div>
        <div class="derived-note">${appearanceNote}</div>
      </div>
    </div>`;
  }).join('');

  wireSkillPlatforms();
  wireStoryRooms();
  wireUpgradeRooms();
  wireAreaLinks();
  $('roomsGrid').querySelectorAll('.tilepart').forEach((chip) =>
    chip.addEventListener('click', () => toggleTilePart(
      chip.dataset.floor === 'defaults' ? 'defaults' : Number(chip.dataset.floor),
      chip.dataset.part)));
}

/** Add or remove one tile group from a floor's subset, or the chapter's. */
async function toggleTilePart(holder, name) {
  const isDefaults = holder === 'defaults';
  const floor = isDefaults ? state.plan.floorDefaults : state.plan.floors[holder];
  const label = isDefaults ? 'chapter default' : `floor ${holder + 1}`;
  if (floor.appearance.inherited) {
    // Clicking here would edit every floor that inherits, from a card that
    // only names one. The span writer cannot add the key to this floor, so
    // the honest options are both elsewhere.
    showToast(`Floor ${holder + 1} inherits its tile groups — edit them on Chapter defaults, or write appearance on this floor in the JSON`);
    return;
  }
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
    after.length ? `${label} tiles → ${after.join(', ')}`
                 : `${label} tiles → theme default`);
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

// RoomTips.MaxTipLength — one line of the HUD banner.
const TIP_MAX_LENGTH = 25;

const codeOf = (id) => {
  const parts = String(id || '').replace(/-/g, '_').split('_').filter(Boolean);
  if (!parts.length) return '??';
  return (parts.length === 1 ? parts[0].slice(0, 2)
    : parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
};

function renderAll() {
  renderSampleBar();
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
  // `put` sets the key if it is on disk and adds it if not — the projection
  // cannot tell an explicit null from an omitted key, the file can.
  await applyEdits('LevelData', state.levelId,
    [{ op: 'put', path: `${room.pointer}/${field}`, value }], `${field} = ${value}`);
}

/** A HUD tip (OZX-1168): written when given text, removed when cleared.
 *
 *  Blank is never written — the field is optional, so the way to show no
 *  tip is no key, and the shipped-data guard refuses a present-but-empty
 *  one. Over-length is refused here rather than sent: RoomTips throws on it
 *  at runtime instead of truncating.
 */
async function setTip(pointer, current, raw) {
  const value = raw.trim();
  if (value.length > TIP_MAX_LENGTH) {
    showToast(`A tip is at most ${TIP_MAX_LENGTH} characters — this is ${value.length}`);
    await reloadLevel();
    return;
  }
  if (!value) {
    if (current == null) return;
    await applyEdits('LevelData', state.levelId, [{ op: 'remove', path: pointer }], 'tip removed');
    return;
  }
  if (value === current) return;
  await applyEdits('LevelData', state.levelId,
    [{ op: 'put', path: pointer, value }], `tip → ${value}`);
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
  // `put`: set the key if the entry has one, add it if the crate was
  // rolling the floor table instead.
  await applyEdits('LevelData', state.levelId,
    [{ op: 'put', path: pointer, value: tableId }], `loot → ${tableId}`);
}

/** Point a skill platform at the active skill it offers.
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

/** OZX-1468: name a crate, or go back to the ordinary look by omitting the
 *  field — an empty string would be "named", and match no CargoData. */
async function setCargoId(pointer, current, cargoId) {
  if ((current || '') === cargoId) return;
  const edit = cargoId
    ? { op: 'put', path: pointer, value: cargoId }
    : { op: 'remove', path: pointer };
  await applyEdits('LevelData', state.levelId, [edit],
    cargoId ? `crate → ${cargoId}` : 'crate → ordinary look');
}

/** Read the value a JSON pointer currently holds in the loaded level view. */
function pointerValue(pointer) {
  const room = currentFloor().rooms.find((r) => pointer.startsWith(r.pointer + '/'));
  if (!room) return null;
  const rest = pointer.slice(room.pointer.length + 1);
  const m = rest.match(/^staticPlacements\/(\d+)\/lootTableId$/);
  if (m) {
    const entry = room.cargoLoot.find((c) => c.placementIndex === Number(m[1]));
    return entry && !entry.fromFloor ? entry.table.id : null;
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
  // OZX-1400: a platform with no charges throws, so it is born with one.
  const value = kind === 'skill'
    ? { kind, skillId: '', count: 1, charges: 1 }
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
    value: { kind: 'skill', skillId, count: 1, charges: 1 },
  }], `skill ${skillId} added to ${room.roomId}`);
}

async function addLoot(tableId) {
  const room = selectionRoom();
  // A specific loot slot was picked (one cargo box), so assign to exactly
  // that pointer.
  if (state.selection.kind === 'loot' && state.selection.pointer) {
    await setLootTable(state.selection.pointer, tableId);
    return;
  }
  // Nothing picked: the room-clear table this used to set is gone
  // (OZX-1381), and a crate carrying the table is what upstream now uses for
  // a room's own loot — a skill table included (OZX-1348).
  if (!room.staticPlacementsDeclared) {
    showToast(`${room.roomId}: staticPlacements is null — fix that first`);
    return;
  }
  await applyEdits('LevelData', state.levelId, [{
    op: 'append',
    path: `${room.pointer}/staticPlacements`,
    value: { kind: 'cargo', count: 1, lootTableId: tableId },
  }], `a crate of ${tableId} added to ${room.roomId}`);
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
    await reopenCurrent();
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
