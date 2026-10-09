/* ==========================================================================
   Veyra — modules/shortcuts.js
   Keyboard / mouse interaction layer.

   Two document-level keydown handlers, mirroring the original split:
     1. navigation + Image View keys (Escape / arrows / zoom / grid walk)
     2. clipboard + delete shortcuts (Escape also closes menu/dialogs)
   ========================================================================== */

/* Fullscreen navigation/zoom + prev/next selection + Escape to exit. */
document.addEventListener('keydown', function (e) {
  /* Escape is the ONLY primary way out of native fullscreen. It must not close
     the app and must not reload the page; it simply restores the normal viewer
     (chrome comes back, image/selection/navigation state is preserved). */
  if (e.key === 'Escape') {
    if (fsActive()) {
      e.preventDefault();
      e.stopPropagation();
      closeFullScreen();
      return;
    }
    return;   /* let other Escape handlers (context menu, dialogs) run */
  }
  /* While fullscreen, arrow/page keys navigate images and +/-/0 zoom; the
     grid-selection shortcuts below must not run on the hidden grid. */
  if (fsActive()) {
    if (isEditableTarget(e.target)) return;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown' || e.key === 'PageDown' || e.key === ' ' || e.key === 'Spacebar') {
      e.preventDefault(); fsStep(1); return;
    }
    if (e.key === 'ArrowLeft' || e.key === 'ArrowUp' || e.key === 'PageUp') {
      e.preventDefault(); fsStep(-1); return;
    }
    if (e.key === '+' || e.key === '=') { e.preventDefault(); fsZoom(1.25); return; }
    if (e.key === '-' || e.key === '_') { e.preventDefault(); fsZoom(0.8); return; }
    if (e.key === '0') { e.preventDefault(); fsSetMode('actual'); return; }
    return;
  }
  if (isEditableTarget(e.target) || anyDialogVisible()) return;
  if (!state.images.length) return;
  var cols = computeCols();
  var cur = (state.selected == null) ? -1 : state.selected;
  var next = null;
  if (e.key === 'ArrowRight') { next = (cur < 0) ? 0 : cur + 1; }
  else if (e.key === 'ArrowLeft') { next = (cur < 0) ? 0 : cur - 1; }
  else if (e.key === 'ArrowDown') { next = (cur < 0) ? 0 : cur + cols; }
  else if (e.key === 'ArrowUp') { next = (cur < 0) ? 0 : cur - cols; }
  if (next == null) return;
  e.preventDefault();
  next = Math.max(0, Math.min(state.images.length - 1, next));
  if (e.shiftKey && state.anchor != null) {
    var a = Math.min(state.anchor, next), b = Math.max(state.anchor, next), r = [];
    for (var k = a; k <= b; k++) r.push(k);
    state.selection = r;
    state.selected = next;
    applySelectionClasses();
    updateStatus();
    updateStatusFile();
  } else {
    selectIndex(next);
  }
});

/* Extra shortcuts: Escape closes menu/dialogs, Delete removes, Ctrl+A/C/X/V
   drive the internal selection + clipboard. */
document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape') { hideContextMenu(); hideAllDialogs(); closeFullScreen(); return; }
  if (isEditableTarget(e.target)) return;
  if (anyDialogVisible()) return;
  if (e.key === 'Delete') {
    if (state.selection.length) { e.preventDefault(); removeSelectedFiles(); }
    return;
  }
  if ((e.ctrlKey || e.metaKey) && (e.key === 'a' || e.key === 'A')) {
    if (state.images.length) {
      e.preventDefault();
      state.selection = state.images.map(function (_, idx) { return idx; });
      state.anchor = 0;
      state.selected = state.selection[state.selection.length - 1];
      applySelectionClasses();
      updateStatus();
      updateStatusFile();
    }
  }
  else if ((e.ctrlKey || e.metaKey) && (e.key === 'c' || e.key === 'C')) {
    if (state.selection.length) { e.preventDefault(); setClipboard('copy'); }
  }
  else if ((e.ctrlKey || e.metaKey) && (e.key === 'x' || e.key === 'X')) {
    if (state.selection.length) { e.preventDefault(); setClipboard('cut'); }
  }
  else if ((e.ctrlKey || e.metaKey) && (e.key === 'v' || e.key === 'V')) {
    e.preventDefault(); pasteClipboard();
  }
});

/* Toolbar buttons that are visual only (each reports its action). */
document.querySelectorAll('[data-action]').forEach(function (b) {
  b.onclick = function () { toast(b.dataset.action); };
});
