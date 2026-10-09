/* ==========================================================================
   Veyra — modules/sort.js
   Sort By: orders the ACTIVE dataset (Normal listing or group members).

   Sorting the WHOLE dataset and then reusing the virtual grid keeps a sort
   change as cheap as a scroll jump: only the active window is mounted.
   ========================================================================== */

var SORT_MODES = {
  date_desc: 'Date',          /* newest -> oldest */
  date_asc: '(Date)',         /* oldest -> newest */
  name_asc: 'File Name',      /* A -> Z */
  name_desc: '(File Name)',   /* Z -> A */
  size_desc: 'File Size',     /* largest -> smallest */
  size_asc: '(File Size)'     /* smallest -> largest */
};

function sortNum(v) { return (v == null || isNaN(v)) ? 0 : Number(v); }

function compareNames(a, b) {
  var x = String((a && a.name) || '').toLowerCase();
  var y = String((b && b.name) || '').toLowerCase();
  if (x < y) return -1;
  if (x > y) return 1;
  return 0;
}

/* Pure helper: returns a NEW sorted array (never mutates the raw listing). */
function sortImages(list, mode) {
  var arr = (list || []).slice();
  var cmp;
  if (mode === 'date_desc') cmp = function (a, b) { return sortNum(b.modified_at) - sortNum(a.modified_at); };
  else if (mode === 'date_asc') cmp = function (a, b) { return sortNum(a.modified_at) - sortNum(b.modified_at); };
  else if (mode === 'size_desc') cmp = function (a, b) { return sortNum(b.size) - sortNum(a.size); };
  else if (mode === 'size_asc') cmp = function (a, b) { return sortNum(a.size) - sortNum(b.size); };
  else if (mode === 'name_desc') cmp = function (a, b) { return compareNames(b, a); };
  else cmp = compareNames;
  arr.sort(cmp);
  return arr;
}

/* The dataset the Image Grid (and therefore the Image View navigation) consumes
   is the ACTIVE one: the Group selector picks the source - "Normal" -> the live
   File-Explorer listing, a group -> that group's members - and Sort By orders
   it.  ``state.images`` is always derived from this one pipeline, so the grid
   and the Image View can never disagree about the order. */
function activeSource() {
  return (state.group != null) ? (state.groupImages || []) : (state.rawImages || []);
}

/* Re-sort the active dataset and refresh the grid.  Selection is preserved by
   *file path* (indices move when the order changes) and the grid returns to the
   top so the new order is visible from the first item. */
function rebuildImages() {
  var picked = {};
  getSelectedImages().forEach(function (it) { if (it && it.path) picked[it.path] = true; });
  state.images = sortImages(activeSource(), state.sort || 'name_asc');
  var sel = [];
  state.images.forEach(function (it, idx) {
    if (it && it.path && picked[it.path]) { sel.push(idx); }
  });
  state.selection = sel;
  state.anchor = sel.length ? sel[0] : null;
  state.selected = sel.length ? sel[sel.length - 1] : null;
  if (els.grid) els.grid.scrollTop = 0;
  renderGrid();
  updateStatus();
  updateStatusFile();
}

function applySortBy(mode) {
  if (!Object.prototype.hasOwnProperty.call(SORT_MODES, mode)) mode = state.sort || 'name_asc';
  state.sort = mode;
  rebuildImages();
  if (els.sortBy) els.sortBy.value = mode;
}

if (els.sortBy) {
  els.sortBy.onchange = function () { applySortBy(this.value); };
}
