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
    throw new Error(payload.error || payload.message || `${response.status} ${path}`);
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
  $('projectLabel').textContent = `${projectRoot} · ${counts.documents} docs`;

  if (!levels.length) {
    $('roomsGrid').innerHTML = '<div class="empty-copy">No LevelData found under GameData/levels/.</div>';
    return;
  }

  $('levelPicker').innerHTML = levels
    .map((l) => `<option value="${esc(l.id)}">${esc(l.id)} · ${l.rooms} rooms</option>`)
    .join('');
  $('levelPicker').addEventListener('change', (e) => loadLevel(e.target.value));

  await loadLevel(levels[0].id);
  await refreshGlobalValidation();
}

async function loadLevel(levelId) {
  state.levelId = levelId;
  state.level = await api(`/api/level/${encodeURIComponent(levelId)}`);
  state.floorIndex = 0;
  state.selectedRoomIndex = 0;
  $('levelPicker').value = levelId;
  renderAll();
}

async function reloadLevel() {
  state.level = await api(`/api/level/${encodeURIComponent(state.levelId)}`);
  renderAll();
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
function lootOptions(selected) {
  const catalog = state.boot.lootCatalog || [];
  const opts = [`<option value="">⟨none⟩</option>`];
  for (const t of catalog) {
    opts.push(`<option value="${esc(t.id)}" ${t.id === selected ? 'selected' : ''}>${esc(t.id)}</option>`);
  }
  if (selected && !catalog.some((t) => t.id === selected)) {
    opts.push(`<option value="${esc(selected)}" selected>${esc(selected)} (missing)</option>`);
  }
  return opts.join('');
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
          const detail = [p.itemId, p.lootTableId, p.mountedEnemyId]
            .filter(Boolean).join(' · ')
            || (p.cells.length ? `${p.cells.length} cell${p.cells.length === 1 ? '' : 's'}` : 'random');
          const count = p.count != null ? p.count : p.cells.length;
          return `<div class="content-entry${slotCls('statics', i)}" data-slot="statics" data-slot-index="${i}">
            <span class="content-glyph">${esc(source.code)}</span>
            <span><strong>${esc(p.kind)}</strong><small>${esc(detail)}</small></span>
            <span class="quantity" data-static-index="${i}">
              <button data-delta="-1" title="Decrease count">−</button>
              <input type="number" min="0" max="999" value="${count}" aria-label="${esc(p.kind)} count" />
              <button data-delta="1" title="Increase count">+</button>
            </span></div>`;
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
          return `<div class="content-entry ${e.missing ? 'missing' : ''}${slotCls('enemies', i)}" data-slot="enemies" data-slot-index="${i}">
            <span class="content-glyph ${e.missing ? 'missing' : ''}">${esc(source.code)}</span>
            <span><strong>${esc(e.id)}</strong><small>${esc(bits.join(' · '))}</small></span>
            <span class="quantity" data-enemy-index="${i}">
              <button data-delta="-1" title="Decrease count">−</button>
              <input type="number" min="0" max="999" value="${e.count}" aria-label="${esc(e.id)} count" />
              <button data-delta="1" title="Increase count">+</button>
            </span></div>`;
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
        lootRow(c.table, `${c.kind} #${c.placementIndex}`, c.pointer, 'LevelData', state.levelId)),
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
            ? `<div class="derived-note">derived from encounter ${esc(room.encounterId)} — edits write to its steps</div>`
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
const LIBRARY_KIND = { enemy: 'enemies', static: 'statics', loot: 'loot' };

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
  for (const kind of ['enemy', 'static', 'loot']) {
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
      <span class="portrait">${esc(item.code)}</span>
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
      if (kind === 'static') await addStatic(card.dataset.id);
      else if (kind === 'loot') await addLoot(card.dataset.id);
      else await addEnemy(card.dataset.id);
    };
    // The whole card adds, so the flow is "pick a target, click a thing".
    card.addEventListener('click', add);
    card.querySelector('.plus').addEventListener('click', add);
  });
}

function renderLevelIssueBadge() {
  const issues = state.level.issues || [];
  const errors = issues.filter((i) => i.severity === 'error').length;
  const warnings = issues.filter((i) => i.severity === 'warning').length;
  const cls = errors ? 'error' : warnings ? 'warning' : 'clean';
  const text = errors ? `${errors} error${errors === 1 ? '' : 's'}`
    : warnings ? `${warnings} warning${warnings === 1 ? '' : 's'}`
    : 'clean';
  $('levelIssues').innerHTML = `<span class="issue-badge ${cls}">${text}</span>`;
}

function renderAll() {
  const floor = currentFloor();
  const label = `FLOOR ${String(floor.index + 1).padStart(2, '0')}`;
  $('floorCrumb').textContent = label;
  $('floorTitle').textContent = label;
  if (state.selectedRoomIndex >= floor.rooms.length) state.selectedRoomIndex = 0;
  renderFloorTabs();
  $('levelSub').textContent = label;
  renderMap();
  renderRooms();
  renderLevelIssueBadge();
  renderCatalog(state.boot.enemyCatalog, 'enemyLibrary', $('enemySearch').value, 'enemy');
  renderCatalog(state.boot.staticCatalog, 'staticLibrary', $('staticSearch').value, 'static');
  renderCatalog(state.boot.lootCatalog, 'lootLibrary', $('lootSearch').value, 'loot');
}

// ── writes ───────────────────────────────────────────────────────────────

async function applyEdits(dataType, id, edits, message) {
  if (!edits.length) return;
  setBusy(true);
  try {
    await post('/api/edit', { dataType, id, edits });
    await reloadLevel();
    markSaved(message || 'written');
    if (message) showToast(message);
  } catch (err) {
    showToast(`Failed: ${err.message}`);
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
    showToast(`Failed: ${err.message}`);
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
  return null;
}

async function addStatic(kind) {
  const room = selectionRoom();
  if (!room.staticPlacementsDeclared) {
    showToast(`${room.roomId}: staticPlacements is null — fix that first`);
    return;
  }
  await applyEdits('LevelData', state.levelId, [{
    op: 'append',
    path: `${room.pointer}/staticPlacements`,
    value: { kind, count: 1 },
  }], `${kind} added to ${room.roomId}`);
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
$('lootSearch').addEventListener('input', (e) =>
  renderCatalog(state.boot.lootCatalog, 'lootLibrary', e.target.value, 'loot'));

$('floorTabs').addEventListener('click', (event) => {
  const button = event.target.closest('[data-floor]');
  if (!button) return;
  state.floorIndex = Number(button.dataset.floor);
  state.selectedRoomIndex = 0;
  renderAll();
});

document.addEventListener('keydown', (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
    event.preventDefault();
    $('saveBtn').click();
  }
});

boot();
