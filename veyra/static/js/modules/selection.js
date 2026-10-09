/* ==========================================================================
   Veyra — modules/selection.js
   Multi-selection model (Ctrl / Shift click, keyboard navigation) shared by
   the grid, the context menu and every file operation.
   ========================================================================== */

function getSelectedIndices() {
  return state.selection.slice().sort(function (a, b) { return a - b; });
}

function getSelectedImages() {
  return getSelectedIndices().map(function (i) { return state.images[i]; })
    .filter(function (x) { return !!x; });
}

function primarySelectedIndex() {
  if (state.selected != null && state.images[state.selected]) return state.selected;
  if (state.selection.length) return state.selection[state.selection.length - 1];
  return null;
}

function applySelectionClasses() {
  var has = {};
  state.selection.forEach(function (i) { has[i] = true; });
  els.grid.querySelectorAll('.card').forEach(function (c) {
    if (has[Number(c.dataset.index)]) c.classList.add('selected');
    else c.classList.remove('selected');
  });
}

function updateStatusFile() {
  var idx = primarySelectedIndex();
  els.statusFile.textContent = (idx != null && state.images[idx]) ? state.images[idx].name : 'No image selected';
}

/* Ctrl/Cmd click toggles, Shift click extends from the anchor, plain click
   replaces the selection. */
function handleCardClick(i, e) {
  e = e || {};
  if (e.ctrlKey || e.metaKey) {
    var pos = state.selection.indexOf(i);
    if (pos >= 0) state.selection.splice(pos, 1);
    else state.selection.push(i);
    state.selection.sort(function (a, b) { return a - b; });
    state.anchor = i;
  } else if (e.shiftKey && state.anchor != null) {
    var a = Math.min(state.anchor, i), b = Math.max(state.anchor, i), r = [];
    for (var k = a; k <= b; k++) r.push(k);
    state.selection = r;
  } else {
    state.selection = [i];
    state.anchor = i;
  }
  state.selected = i;
  applySelectionClasses();
  updateStatus();
  updateStatusFile();
}
