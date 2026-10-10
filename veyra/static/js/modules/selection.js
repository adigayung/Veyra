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
/* Rubber-band selection.  Coordinates are viewport coordinates; card geometry
   is extrapolated from a mounted card so virtualization does not limit the
   selectable range.

   LAZY by design: ``marqueeStart`` only ARMS a potential drag on a plain
   left press; it never activates the overlay, never captures the pointer and
   never calls preventDefault.  That keeps a plain click / double-click on a
   card a *normal* browser event (card.onclick -> handleCardClick,
   card.ondblclick -> openFullScreen).  The marquee becomes ``active`` only
   once the pointer genuinely moves past MARQUEE_THRESHOLD in marqueeMove. */
var MARQUEE_THRESHOLD = 5;

function marqueeStart(e) {
  if (!e || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey) return false;
  var card = e.target && e.target.closest ? e.target.closest('.card') : null;
  if (!card || !els.grid.contains(card)) return false;
  state.marquee.armed = true;
  state.marquee.active = false;
  state.marquee.dragged = false;
  state.marquee.startX = e.clientX;
  state.marquee.startY = e.clientY;
  state.marquee.baseSelection = state.selection.slice();
  state.marquee.baseAnchor = state.anchor;
  state.marquee.overlay = null;
  return true;
}

function marqueeMove(e) {
  if (!state.marquee.armed) return;
  var dx = e.clientX - state.marquee.startX, dy = e.clientY - state.marquee.startY;
  if (!state.marquee.active) {
    /* Below the click/drag tolerance this is still a (potential) click. */
    if (Math.abs(dx) <= MARQUEE_THRESHOLD && Math.abs(dy) <= MARQUEE_THRESHOLD) return;
    /* Threshold crossed: a real rubber-band drag begins now. */
    state.marquee.active = true;
    state.marquee.dragged = true;
    if (els.grid.classList) els.grid.classList.add('marquee-active');
    var box = document.createElement('div');
    box.className = 'marquee-selection';
    box.id = 'marqueeSelection';
    document.body.appendChild(box);
    state.marquee.overlay = box;
  }
  var x = Math.min(e.clientX, state.marquee.startX), y = Math.min(e.clientY, state.marquee.startY);
  var w = Math.abs(dx), h = Math.abs(dy), o = state.marquee.overlay;
  if (o) { o.style.left = x + 'px'; o.style.top = y + 'px'; o.style.width = w + 'px'; o.style.height = h + 'px'; }
  marqueePreview(e.clientX, e.clientY);
}

function marqueePreview(ex, ey) {
  var cards = els.grid.querySelectorAll('.card'), sample = cards[0];
  if (!sample) return;
  var cols = computeCols(), rowH = gridLayout.rowH || 170;
  var sr = sample.getBoundingClientRect(), sw = sr.width, sh = sr.height;
  var si = Number(sample.dataset.index), sc = si % cols, srow = Math.floor(si / cols);
  var left = Math.min(state.marquee.startX, ex), right = Math.max(state.marquee.startX, ex);
  var top = Math.min(state.marquee.startY, ey), bottom = Math.max(state.marquee.startY, ey);
  var picked = [];
  for (var i = 0; i < state.images.length; i++) {
    var col = i % cols, row = Math.floor(i / cols);
    var x = sr.left + (col - sc) * (sw + GRID_GAP);
    var y = sr.top + (row - srow) * rowH;
    if (x < right && x + sw > left && y < bottom && y + sh > top) picked.push(i);
  }
  var next = state.marquee.baseSelection.slice();
  picked.forEach(function (i) { if (next.indexOf(i) < 0) next.push(i); });
  state.selection = next.sort(function (a, b) { return a - b; });
  if (picked.length) { state.selected = picked[picked.length - 1]; state.anchor = state.selected; }
  applySelectionClasses(); updateStatus(); updateStatusFile();
}

function marqueeEnd() {
  var dragged = state.marquee.active && state.marquee.dragged;
  if (state.marquee.overlay && state.marquee.overlay.parentNode) state.marquee.overlay.parentNode.removeChild(state.marquee.overlay);
  if (els.grid.classList) els.grid.classList.remove('marquee-active');
  state.marquee.armed = false;
  state.marquee.active = false;
  state.marquee.overlay = null;
  return !!dragged;
}

function handleCardClick(i, e) {
  e = e || {};
  if (state.marquee.suppressClick) { state.marquee.suppressClick = false; return; }
  if (state.marquee.active) return;
  /* A pointer drag is completed before click dispatch; the grid marks this
     flag to preserve plain-click replacement semantics without opening a card. */
  if (state.marquee.justDragged) { state.marquee.justDragged = false; return; }
  
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
