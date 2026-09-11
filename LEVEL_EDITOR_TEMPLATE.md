# OZX Level Studio HTML prototype

Open `level_editor_template.html` directly in a browser. It is a standalone design prototype with no build step or external dependencies.

Implemented interactions:

- The entire left workspace is a stable, vertically scrolling room list. Clicking a room row does not select or refresh it.
- Static placement and enemy quantities are editable inline with minus/input/plus controls; quantity edits do not rerender the room list.
- Every room has a dedicated Decorations column with preset selection, add, and remove controls.
- Switch between three floors from the right-side minimap.
- Choose the target room from the minimap and edit each room's core fields inline.
- Toggle open-door directions.
- Search enemy/static libraries and add entries to the selected room.
- Create a room, validate, preview, and save the mock working copy.
- Use `Cmd/Ctrl + S` to save.

The mock data is embedded in the page and does not write production JSON.
