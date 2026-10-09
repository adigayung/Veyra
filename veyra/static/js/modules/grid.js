/* ==========================================================================
   Veyra — modules/grid.js
   Image Grid / Thumbnail View.

   The grid is VIRTUALIZED: only a window of GRID_WINDOW_RADIUS items around
   the viewport centre is ever mounted, and the DOM is refreshed only
   GRID_SCROLL_DEBOUNCE ms after scrolling genuinely stops (a direct jump).
   Spacer cells keep the partial first/last rows aligned to their columns and
   the top/bottom pads preserve the full scrollable height.
   ========================================================================== */

/* Column count that fits the current viewport width. */
function computeCols() {
  var w = els.grid.clientWidth - 18;
  if (w <= 0) { w = els.grid.clientWidth || 600; }
  return Math.max(1, Math.floor((w + GRID_GAP) / (GRID_MIN + GRID_GAP)));
}

/* One thumbnail card.  Locked .aimg files never request a thumbnail: they
   render a lock placeholder until the crypto session is unlocked. */
function cardHtml(img, index) {
  var dims = (img.width && img.height) ? (img.width + '×' + img.height) : '—';
  var type = esc(img.type || '');
  var badge = img.encrypted ? '<span class="lock-badge" title="AIMG">🔒</span>' : '';
  var inner;
  if (img.encrypted && !state.crypto.unlocked) {
    inner = '<div class="thumb locked-thumb" title="Crypto session terkunci">🔒</div>';
  } else {
    inner = '<div class="thumb"><img loading="lazy" alt="' + esc(img.name) +
      '" src="' + thumbUrl(img.path) + '"></div>';
  }
  return '<article class="card" data-index="' + index + '">' + inner +
    '<div class="meta"><span class="dims">' + dims + '</span><span>' + type + badge + '</span></div>' +
    '<div class="filename" title="' + esc(img.name) + '">' + esc(img.name) + '</div></article>';
}

/* Empty grid cell used to keep the partial first/last rows of the render
   window in the correct columns (it is never a .card, so the thumbnail DOM
   stays bounded to the window itself). */
function padCellHtml() { return '<div class="cell-pad" aria-hidden="true"></div>'; }

/* Column the grid uses to pick the "centre" item of a row. */
function gridMidCol(cols) { return Math.max(0, Math.floor((cols - 1) / 2)); }

/* The image sitting closest to the vertical+horizontal centre of the viewport. */
function computeCenterIndex() {
  var total = state.images.length;
  if (!total) return -1;
  var cols = computeCols();
  var rowH = gridLayout.rowH || 170;
  var rows = Math.ceil(total / cols);
  var viewH = els.grid.clientHeight || 600;
  var centerY = els.grid.scrollTop + viewH / 2;
  var centerRow = Math.floor(centerY / rowH);
  if (centerRow < 0) centerRow = 0;
  if (centerRow > rows - 1) centerRow = rows - 1;
  var idx = centerRow * cols + gridMidCol(cols);
  if (idx > total - 1) idx = total - 1;
  if (idx < 0) idx = 0;
  return idx;
}

/* Build the pad/grid/pad skeleton and mount the centre window. */
function renderGrid() {
  gridLayout.calibrated = false;
  gridLayout.winStart = -1;
  gridLayout.winEnd = -1;
  if (gridLayout.scrollTimer) { clearTimeout(gridLayout.scrollTimer); gridLayout.scrollTimer = null; }
  els.grid.innerHTML = '';
  els.grid.onscroll = null;
  if (!state.images.length) {
    els.grid.innerHTML = '<div class="empty">Tidak ada file gambar yang didukung di folder ini.</div>';
    return;
  }
  var top = document.createElement('div'); top.className = 'grid-pad'; top.id = 'gridTop';
  var inner = document.createElement('div'); inner.className = 'vgrid'; inner.id = 'gridInner';
  var bottom = document.createElement('div'); bottom.className = 'grid-pad'; bottom.id = 'gridBottom';
  els.grid.appendChild(top);
  els.grid.appendChild(inner);
  els.grid.appendChild(bottom);
  renderWindow(computeCenterIndex());
  els.grid.onscroll = onGridScroll;
}

/* Mount ONLY center - GRID_WINDOW_RADIUS .. center + GRID_WINDOW_RADIUS.  This
   is a *direct jump*: the previously mounted window is discarded wholesale, so
   jumping across thousands of images costs a single re-render. */
function renderWindow(center) {
  var inner = document.getElementById('gridInner');
  if (!inner) return;
  var total = state.images.length;
  if (!total) return;
  if (gridLayout.scrollTimer) { clearTimeout(gridLayout.scrollTimer); gridLayout.scrollTimer = null; }
  var cols = computeCols();
  var rowH = gridLayout.rowH || 170;
  var rows = Math.ceil(total / cols);
  if (center == null || center < 0 || center >= total) center = computeCenterIndex();
  if (center < 0) center = 0;
  var start = Math.max(0, center - GRID_WINDOW_RADIUS);
  var end = Math.min(total - 1, center + GRID_WINDOW_RADIUS);
  gridLayout.winStart = start;
  gridLayout.winEnd = end;
  var startRow = Math.floor(start / cols);
  var endRow = Math.floor(end / cols);
  var alignEnd = Math.min(total - 1, endRow * cols + cols - 1);
  var lead = start - startRow * cols;
  var trail = (cols - 1) - (alignEnd % cols);
  var parts = [];
  var k;
  for (k = 0; k < lead; k++) parts.push(padCellHtml());
  for (k = start; k <= end; k++) parts.push(cardHtml(state.images[k], k));
  for (k = 0; k < trail; k++) parts.push(padCellHtml());
  inner.innerHTML = parts.join('');
  var top = document.getElementById('gridTop'), bottom = document.getElementById('gridBottom');
  if (top) top.style.height = (startRow * rowH) + 'px';
  if (bottom) bottom.style.height = Math.max(0, (rows - endRow - 1)) * rowH + 'px';
  bindCards();
  /* Calibrate the real row height once per render (the card height depends on
     the theme's font metrics), then re-render with the measured value. */
  if (!gridLayout.calibrated) {
    gridLayout.calibrated = true;
    var probe = inner.querySelector('.card');
    if (probe) {
      var h = probe.getBoundingClientRect().height;
      if (h > 0.5 && Math.abs((h + GRID_GAP) - rowH) > 1) {
        gridLayout.rowH = h + GRID_GAP;
        renderWindow(computeCenterIndex());
        return;
      }
    }
  }
}

/* The scrollbar itself must stay instant: no work on the scroll event, only
   (re)arm a timer.  Every further scroll resets the timer, so the window is
   recomputed once the user has genuinely stopped. */
function onGridScroll() {
  if (gridLayout.scrollTimer) { clearTimeout(gridLayout.scrollTimer); }
  gridLayout.scrollTimer = setTimeout(applyScrollWindow, GRID_SCROLL_DEBOUNCE);
}

/* Runs GRID_SCROLL_DEBOUNCE ms after the last scroll event: compute the newest
   centre and jump straight to its window - unless the centre is still inside
   the already mounted window, in which case nothing needs to happen. */
function applyScrollWindow() {
  gridLayout.scrollTimer = null;
  var center = computeCenterIndex();
  if (center < 0) return;
  if (gridLayout.winStart >= 0 && center >= gridLayout.winStart && center <= gridLayout.winEnd) { return; }
  renderWindow(center);
}

/* Wire the freshly mounted window: click / dblclick + thumbnail load/error. */
function bindCards() {
  els.grid.querySelectorAll('.card').forEach(function (card) {
    card.onclick = function (e) { handleCardClick(Number(card.dataset.index), e); };
    card.ondblclick = function () { openFullScreen(Number(card.dataset.index)); };
  });
  els.grid.querySelectorAll('.thumb img').forEach(function (im) {
    im.addEventListener('load', function () {
      var card = im.closest('.card');
      if (!card) return;
      var idx = Number(card.dataset.index);
      var item = state.images[idx];
      if (item && !item.encrypted && im.naturalWidth && im.naturalHeight) {
        var span = card.querySelector('.dims');
        if (span && span.textContent === '—') {
          span.textContent = im.naturalWidth + '×' + im.naturalHeight;
          item.width = im.naturalWidth;
          item.height = im.naturalHeight;
        }
      }
    });
    im.addEventListener('error', function () {
      var thumb = im.closest('.thumb');
      if (thumb) { thumb.classList.add('broken'); thumb.textContent = '⚠'; im.remove(); }
    });
  });
  applySelectionClasses();
}

/* Select a single index, scrolling it into view when it is outside the
   currently mounted window (keyboard navigation / context menu). */
function selectIndex(i) {
  if (i < 0 || i >= state.images.length) return;
  state.selected = i;
  state.selection = [i];
  state.anchor = i;
  var card = els.grid.querySelector('.card[data-index="' + i + '"]');
  if (!card) { ensureVisible(i); return; }
  applySelectionClasses();
  var img = state.images[i];
  els.statusFile.textContent = img.name;
  updateStatus();
}

/* Scroll the target row into view, then mount the window around it. */
function ensureVisible(i) {
  var cols = computeCols();
  var row = Math.floor(i / cols);
  var y = row * gridLayout.rowH;
  var viewH = els.grid.clientHeight || 600;
  if (y < els.grid.scrollTop || y + gridLayout.rowH > els.grid.scrollTop + viewH) {
    els.grid.scrollTop = Math.max(0, y - gridLayout.rowH);
  }
  renderWindow(i);
  applySelectionClasses();
}

/* Status bar + view counter, derived from the ACTIVE dataset. */
function updateStatus() {
  var total = state.images.reduce(function (a, b) { return a + (b.size || 0); }, 0);
  els.statusFolders.textContent = state.dirs.length + ' Folders';
  els.statusFiles.textContent = state.images.length + ' Files (' + fmtSize(total) + ')';
  var n = state.selection.length;
  if (n === 1 && state.images[state.selection[0]]) {
    els.selectedStatus.textContent = '1 Selected (' + fmtSize(state.images[state.selection[0]].size) + ')';
  } else if (n > 1) {
    var bytes = 0;
    state.selection.forEach(function (idx) { var it = state.images[idx]; if (it) bytes += (it.size || 0); });
    els.selectedStatus.textContent = n + ' Selected (' + fmtSize(bytes) + ')';
  } else {
    els.selectedStatus.textContent = '0 Selected';
  }
  els.viewCount.textContent = state.images.length + ' items';
}

/* -- browse ---------------------------------------------------------------- */
function showGridError(msg) {
  els.grid.innerHTML = '<div class="empty error">' + esc(msg) + '</div>';
  els.viewCount.textContent = '0 items';
  state.images = []; state.rawImages = []; state.dirs = [];
  state.selected = null; state.selection = []; state.anchor = null;
  els.statusFolders.textContent = '0 Folders';
  els.statusFiles.textContent = '0 Files (0 B)';
  els.statusFile.textContent = 'No image selected';
  els.selectedStatus.textContent = '0 Selected';
}

/* Open a folder: fetch the listing, derive the active dataset, render. */
function openFolder(path, opts) {
  opts = opts || {};
  var url = '/api/browse' + (path ? ('?path=' + encodeURIComponent(path)) : '');
  return getJSON(url).then(function (res) {
    var data = res.data || {};
    if (data.crypto) { setCryptoStatus(data.crypto); }
    if (!data.ok) {
      showGridError(data.error || 'Tidak dapat membuka folder.');
      toast(data.error || 'Tidak dapat membuka folder.');
      return;
    }
    state.path = data.path;
    /* Keep the untouched filesystem listing, then expose the *sorted* view as
       ``state.images`` - the only array the grid / viewer / selection use. */
    state.rawImages = data.images || [];
    state.images = sortImages(state.rawImages, state.sort || 'name_asc');
    /* A Group filter is a *persistent* dataset: while a group is active the
       grid keeps showing that group's members even when the explorer moves to
       another folder/drive (the Normal/explorer listing never leaks in). */
    if (state.group != null) { state.images = sortImages(activeSource(), state.sort || 'name_asc'); }
    state.dirs = data.directories || [];
    state.selected = null; state.selection = []; state.anchor = null;
    els.statusFile.textContent = 'No image selected';
    if (data.tree_root && !state.treeRoot) { state.treeRoot = data.tree_root; }
    els.pathInput.value = data.path;

    renderGrid();
    updateStatus();
    markSelectedDir(data.path);
    syncTree(data.path);
    if (opts.history !== false) { pushHistory(data.path); }
  }).catch(function (err) {
    showGridError('Gagal membuka folder: ' + err.message);
    toast('Gagal membuka folder: ' + err.message);
  });
}

function pushHistory(p) {
  if (state.hIndex >= 0 && state.history[state.hIndex] === p) return;
  state.history = state.history.slice(0, state.hIndex + 1);
  state.history.push(p);
  state.hIndex = state.history.length - 1;
}
