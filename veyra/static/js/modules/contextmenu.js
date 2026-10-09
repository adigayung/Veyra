/* ==========================================================================
   Veyra — modules/contextmenu.js
   Right-click context menu: file actions (open / batch rename / encrypt /
   decrypt / group membership / copy-move / clipboard / delete / new folder /
   rename).  The menu markup uses the shared .menu-row / .menu-sub primitives.
   ========================================================================== */

var ctxMenu = document.createElement('div');
ctxMenu.className = 'ctx-menu';
ctxMenu.innerHTML =
  '<div class="menu-row" data-act="fullscreen">Open Full Screen</div>' +
  '<div class="menu-sep"></div>' +
  '<div class="menu-row has-sub" data-act="tools">Tools<span class="arrow">\u25b6</span>' +
    '<div class="menu-sub"><div class="menu-row" data-act="batch-rename">Batch Rename...</div></div>' +
  '</div>' +
  '<div class="menu-sep"></div>' +
  '<div class="menu-row" data-act="encrypt">Encrypt</div>' +
  '<div class="menu-row" data-act="decrypt">Decrypt</div>' +
  '<div class="menu-sep"></div>' +
  '<div class="menu-row has-sub" data-act="addgroup">Add to Group<span class="arrow">\u25b6</span><div class="menu-sub" id="ctxAddGroup"></div></div>' +
  '<div class="menu-row has-sub" data-act="removegroup">Remove from Group<span class="arrow">\u25b6</span><div class="menu-sub" id="ctxRemoveGroup"></div></div>' +
  '<div class="menu-sep"></div>' +
  '<div class="menu-row" data-act="copytarget">Copy to Folder...</div>' +
  '<div class="menu-row" data-act="movetarget">Move to Folder...</div>' +
  '<div class="menu-sep"></div>' +
  '<div class="menu-row" data-act="copy">Copy</div>' +
  '<div class="menu-row" data-act="cut">Cut</div>' +
  '<div class="menu-row" data-act="paste">Paste</div>' +
  '<div class="menu-row" data-act="remove">Remove</div>' +
  '<div class="menu-row" data-act="newfolder">New Folder</div>' +
  '<div class="menu-sep"></div>' +
  '<div class="menu-row" data-act="rename">Rename</div>';
document.body.appendChild(ctxMenu);

function ctxItem(act) { return ctxMenu.querySelector('.menu-row[data-act="' + act + '"]'); }

/* Show/hide the entries that only apply to the current selection. */
function buildContextMenu() {
  var sel = getSelectedImages();
  var n = sel.length;
  var allPlain = n > 0 && sel.every(function (x) { return !x.encrypted; });
  var allAimg = n > 0 && sel.every(function (x) { return x.encrypted; });
  ctxItem('encrypt').style.display = allPlain ? '' : 'none';
  ctxItem('decrypt').style.display = allAimg ? '' : 'none';
  ctxItem('rename').textContent = (n > 1) ? 'Rename (Batch...)' : 'Rename';
  ctxItem('fullscreen').classList.toggle('disabled', n === 0);
  /* Paste only appears while the internal clipboard actually holds files. */
  var paste = ctxItem('paste');
  if (paste) {
    var clip = (state.clipboard && state.clipboard.paths) ? state.clipboard.paths.length : 0;
    paste.style.display = clip ? '' : 'none';
    paste.textContent = clip ? ('Paste (' + clip + ')') : 'Paste';
  }
}

function populateAddGroup() {
  var host = document.getElementById('ctxAddGroup');
  if (!groupsState.list.length) { host.innerHTML = '<div class="menu-empty">Belum ada group</div>'; return; }
  host.innerHTML = groupsState.list.map(function (g) {
    return '<div class="menu-row" data-act="addgroup-item" data-gid="' + g.id + '">' + esc(g.name) + '</div>';
  }).join('');
}

function populateRemoveGroup() {
  var host = document.getElementById('ctxRemoveGroup');
  var idx = primarySelectedIndex();
  if (idx == null) { host.innerHTML = '<div class="menu-empty">Tidak ada file terpilih</div>'; return; }
  var img = state.images[idx];
  host.innerHTML = '<div class="menu-empty">Memuat...</div>';
  getJSON('/api/groups/for?path=' + encodeURIComponent(img.path)).then(function (res) {
    var groups = (res.data && res.data.groups) || [];
    if (!groups.length) { host.innerHTML = '<div class="menu-empty">Tidak ada group</div>'; return; }
    host.innerHTML = groups.map(function (g) {
      return '<div class="menu-row" data-act="removegroup-item" data-gid="' + g.id + '">' + esc(g.name) + '</div>';
    }).join('');
  }).catch(function () { host.innerHTML = '<div class="menu-empty">Gagal memuat</div>'; });
}

/* Submenu positioning + lazy population on hover. */
ctxMenu.querySelectorAll('.menu-row.has-sub').forEach(function (item) {
  item.addEventListener('mouseenter', function () {
    ctxMenu.querySelectorAll('.menu-row.open').forEach(function (o) { if (o !== item) o.classList.remove('open'); });
    item.classList.add('open');
    var r = item.getBoundingClientRect();
    if (r.right + 190 > window.innerWidth) item.classList.add('left'); else item.classList.remove('left');
    if (item.dataset.act === 'addgroup') populateAddGroup();
    else if (item.dataset.act === 'removegroup') populateRemoveGroup();
  });
});

function handleContextAction(act) {
  if (act === 'fullscreen') { var i = primarySelectedIndex(); if (i != null) openFullScreen(i); }
  else if (act === 'batch-rename') openBatchRenameDialog();
  else if (act === 'encrypt') openCryptoFilesDialog('encrypt');
  else if (act === 'decrypt') openCryptoFilesDialog('decrypt');
  else if (act === 'copytarget') openTargetFolderDialog('copy');
  else if (act === 'movetarget') openTargetFolderDialog('move');
  else if (act === 'copy') setClipboard('copy');
  else if (act === 'cut') setClipboard('cut');
  else if (act === 'paste') pasteClipboard();
  else if (act === 'remove') removeSelectedFiles();
  else if (act === 'newfolder') openNewFolderDialog();
  else if (act === 'rename') { if (state.selection.length > 1) openBatchRenameDialog(); else openRenameDialog(); }
}

ctxMenu.addEventListener('click', function (e) {
  var item = e.target.closest ? e.target.closest('.menu-row') : null;
  if (!item || !ctxMenu.contains(item)) return;
  if (item.classList.contains('has-sub')) return;
  var act = item.dataset.act;
  if (act === 'addgroup-item') { addSelectionToGroup(Number(item.dataset.gid)); hideContextMenu(); return; }
  if (act === 'removegroup-item') { removeSelectionFromGroup(Number(item.dataset.gid)); hideContextMenu(); return; }
  handleContextAction(act);
  hideContextMenu();
});

function showContextMenu(e, index) {
  if (index != null && state.selection.indexOf(index) < 0) {
    state.selection = [index];
    state.anchor = index;
    state.selected = index;
    applySelectionClasses();
    updateStatus();
    updateStatusFile();
  }
  ctxMenu.querySelectorAll('.menu-row.open').forEach(function (o) { o.classList.remove('open'); });
  buildContextMenu();
  ctxMenu.classList.add('show');
  var w = ctxMenu.offsetWidth, h = ctxMenu.offsetHeight;
  var x = e.clientX, y = e.clientY;
  if (x + w > window.innerWidth - 2) x = Math.max(0, window.innerWidth - w - 2);
  if (y + h > window.innerHeight - 2) y = Math.max(0, window.innerHeight - h - 2);
  ctxMenu.style.left = x + 'px';
  ctxMenu.style.top = y + 'px';
}

function hideContextMenu() {
  ctxMenu.classList.remove('show');
  ctxMenu.querySelectorAll('.menu-row.open').forEach(function (o) { o.classList.remove('open'); });
}

/* Right click anywhere in the grid opens the menu; outside it just closes. */
document.addEventListener('contextmenu', function (e) {
  e.preventDefault();
  var card = e.target && e.target.closest ? e.target.closest('.card') : null;
  var idx = card ? Number(card.dataset.index) : null;
  if (idx == null) {
    var inGrid = e.target && e.target.closest && e.target.closest('.grid');
    if (!inGrid) { hideContextMenu(); return; }
  }
  showContextMenu(e, idx);
});

document.addEventListener('click', function (e) {
  if (!ctxMenu.contains(e.target)) hideContextMenu();
}, true);
