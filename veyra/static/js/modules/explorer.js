/* ==========================================================================
   Veyra — modules/explorer.js
   File Explorer sidebar: a "This PC" root that lists every available drive,
   then lazily expanded folders at unlimited depth, plus the address-bar
   navigation (back / forward / up / path input / refresh).
   ========================================================================== */

function loadTree() {
  els.tree.innerHTML = '';
  var pc = buildPcNode();
  els.tree.appendChild(pc);
  return expandNode(pc);
}

function buildPcNode() {
  var li = document.createElement('li');
  li.dataset.kind = 'pc';
  li.dataset.path = PC_PATH;
  li.dataset.loaded = '0';
  var twisty = document.createElement('span');
  twisty.className = 'twisty';
  twisty.textContent = '›';
  twisty.dataset.open = '0';
  var icon = document.createElement('span');
  icon.className = 'folder';
  icon.textContent = '💻';
  var label = document.createElement('span');
  label.className = 'label';
  label.textContent = 'This PC';
  li.appendChild(twisty);
  li.appendChild(icon);
  li.appendChild(label);
  twisty.addEventListener('click', function (e) { e.stopPropagation(); toggleNode(li); });
  li.addEventListener('click', function (e) { e.stopPropagation(); toggleNode(li); });
  return li;
}

function buildTreeList(folders) {
  var ul = document.createElement('ul');
  for (var i = 0; i < folders.length; i++) { ul.appendChild(buildTreeNode(folders[i])); }
  return ul;
}

function buildTreeNode(folder) {
  var li = document.createElement('li');
  li.dataset.kind = folder.kind || 'folder';
  li.dataset.path = folder.path;
  li.dataset.loaded = '0';
  li.dataset.has_children = folder.has_children ? '1' : '0';
  var twisty = document.createElement('span');
  twisty.className = 'twisty';
  twisty.textContent = '›';
  twisty.dataset.open = '0';
  var icon = document.createElement('span');
  icon.className = 'folder';
  icon.textContent = (folder.kind === 'drive') ? '💽' : '📁';
  var label = document.createElement('span');
  label.className = 'label';
  label.textContent = folder.name;
  li.appendChild(twisty);
  li.appendChild(icon);
  li.appendChild(label);
  if (folder.kind === 'drive') {
    var badge = document.createElement('span');
    badge.className = 'drive-badge';
    label.parentNode.insertBefore(badge, label);
    badge.textContent = folder.name.replace(/^[A-Za-z]:\\$/, function (m) { return m.toUpperCase(); });
  }
  twisty.addEventListener('click', function (e) { e.stopPropagation(); toggleNode(li); });
  li.addEventListener('click', function (e) { e.stopPropagation(); selectTreeNode(li); openFolder(folder.path); });
  return li;
}

function nodeTwisty(li) { return li.querySelector(':scope > .twisty'); }
function nodeChildren(li) { return li.querySelector(':scope > ul'); }
/* ``sym`` empty => leaf (no twisty); '⊟' => expanded; otherwise collapsed.
   A single chevron glyph is shown and rotated via data-open, so the marker
   reads consistently across the whole tree. */
function setTwisty(li, sym) {
  var t = nodeTwisty(li);
  if (!t) return;
  if (!sym) { t.textContent = ''; t.dataset.open = '0'; return; }
  t.textContent = '›';
  t.dataset.open = (sym === '⊟') ? '1' : '0';
}

/* Fetch (once) the immediate children of ``li`` and mount them collapsed. */
function loadChildren(li) {
  if (li.dataset.loaded === '1') return Promise.resolve();
  var isPc = li.dataset.kind === 'pc';
  var url = isPc ? '/api/drives' : ('/api/tree?path=' + encodeURIComponent(li.dataset.path));
  return getJSON(url).then(function (res) {
    var data = res.data || {};
    var items = isPc ? (data.drives || []) : (data.folders || []);
    if (isPc) {
      items = items.map(function (d) { return { name: d.name, path: d.path, has_children: true, kind: 'drive' }; });
    }
    li.dataset.loaded = '1';
    var prev = nodeChildren(li);
    if (prev) prev.remove();
    if (!items.length) { setTwisty(li, ''); return; }
    li.appendChild(buildTreeList(items));
    setTwisty(li, '⊞');
  }).catch(function () { li.dataset.loaded = '1'; setTwisty(li, ''); });
}

/* Expand a node, loading its children on first use. */
function expandNode(li) {
  var ul = nodeChildren(li);
  if (ul) { ul.removeAttribute('hidden'); setTwisty(li, '⊟'); return Promise.resolve(); }
  return loadChildren(li).then(function () {
    var nu = nodeChildren(li);
    if (nu) { nu.removeAttribute('hidden'); setTwisty(li, '⊟'); }
  });
}

function toggleNode(li) {
  var ul = nodeChildren(li);
  if (ul) {
    if (ul.hasAttribute('hidden')) { ul.removeAttribute('hidden'); setTwisty(li, '⊟'); }
    else { ul.setAttribute('hidden', ''); setTwisty(li, '⊞'); }
    return Promise.resolve();
  }
  if (li.dataset.kind !== 'pc') {
    var t = nodeTwisty(li);
    if (!t || !t.textContent) { return Promise.resolve(); }
  }
  return loadChildren(li).then(function () {
    var nu = nodeChildren(li);
    if (nu) setTwisty(li, '⊟');
  });
}

function selectTreeNode(li) {
  els.tree.querySelectorAll('li').forEach(function (x) { x.classList.remove('selected'); });
  li.classList.add('selected');
}

function markSelectedDir(path) {
  els.tree.querySelectorAll('li').forEach(function (li) {
    if (li.dataset.path === path) { li.classList.add('selected'); }
    else { li.classList.remove('selected'); }
  });
}

function findTreeNode(path) {
  var found = null;
  els.tree.querySelectorAll('li[data-path]').forEach(function (li) {
    if (!found && li.dataset.path === path) found = li;
  });
  return found;
}

/* Bring the node for ``path`` to the vertical CENTRE of the tree viewport so
   the folder the viewer just opened is always clearly visible, instead of the
   selection changing off-screen.  The scroll is deferred one frame so layout
   has settled after a lazy/async expansion mounted the node.  Guarded on both
   ends: an empty path or a node that is not in the DOM yet (ancestors still
   loading) is a silent no-op - the explorer never throws here.  Only the
   node's own scroll ancestor (``.explorer-body``) moves, so the Image Grid and
   the page viewport are never touched. */
function focusActiveNode(path) {
  if (!path) return;
  var node = findTreeNode(path);
  if (!node) return;
  var schedule = (typeof requestAnimationFrame === 'function')
    ? requestAnimationFrame
    : function (fn) { return setTimeout(fn, 0); };
  schedule(function () {
    if (node && typeof node.scrollIntoView === 'function') {
      node.scrollIntoView({ block: 'center', inline: 'nearest' });
    }
  });
}

/* Cumulative prefixes of an absolute path, drive root first
   (e.g. "J:\a\b" -> ["J:\", "J:\a", "J:\a\b"]). */
function pathChain(path) {
  var norm = String(path).replace(/\//g, '\\');
  var out = [];
  var m = norm.match(/^([a-zA-Z]:)\\?/);
  var start = 0;
  if (m) { out.push(m[1] + '\\'); start = m[0].length; }
  var parts = norm.slice(start).split('\\').filter(function (p) { return p.length; });
  var cur = out.length ? out[0] : '';
  for (var i = 0; i < parts.length; i++) {
    cur = cur.replace(/\\+$/, '') + '\\' + parts[i];
    out.push(cur);
  }
  return out;
}

function revealTowards(prev, childPath) {
  return prev.then(function (parent) {
    if (!parent) return null;
    return expandNode(parent).then(function () { return findTreeNode(childPath) || null; });
  });
}

/* Expand "This PC", the drive and every ancestor so ``path`` becomes visible. */
function revealTreePath(path) {
  var chain = pathChain(path);
  var pc = els.tree.querySelector('li[data-kind="pc"]');
  if (!pc || !chain.length) return Promise.resolve();
  return expandNode(pc).then(function () {
    var node = findTreeNode(chain[0]);
    if (!node) return null;
    var p = Promise.resolve(node);
    for (var i = 1; i < chain.length; i++) { p = revealTowards(p, chain[i]); }
    return p;
  });
}

/* Reload one folder's children in the sidebar so a newly created folder shows
   up immediately.  When ``opts.expand`` is set the node is left expanded. */
function refreshTreeNode(path, opts) {
  opts = opts || {};
  if (!path) return Promise.resolve();
  var li = findTreeNode(path);
  if (!li) return Promise.resolve();
  var oldUl = nodeChildren(li);
  var wasOpen = !!oldUl && !oldUl.hasAttribute('hidden');
  return getJSON('/api/tree?path=' + encodeURIComponent(path)).then(function (res) {
    var data = res.data || {};
    var folders = (data.ok && data.folders) ? data.folders : [];
    var prev = nodeChildren(li);
    if (prev) prev.remove();
    li.dataset.loaded = '1';
    if (!folders.length) { setTwisty(li, ''); return; }
    var ul = buildTreeList(folders);
    var open = wasOpen || !!opts.expand;
    if (!open) ul.setAttribute('hidden', '');
    li.appendChild(ul);
    setTwisty(li, open ? '⊟' : '⊞');
  }).catch(function () {});
}

/* Keep the explorer in sync with the folder the viewer is showing: refresh an
   existing node, otherwise expand its ancestors (drive first) so the folder
   becomes visible and navigable in the tree.  Either way the active node is
   marked selected AND scrolled to the centre of the tree viewport, so the
   visual focus always follows the active path. */
function syncTree(path) {
  if (!path) return;
  if (findTreeNode(path)) {
    return refreshTreeNode(path).then(function () {
      markSelectedDir(path);
      focusActiveNode(path);
    });
  }
  return revealTreePath(path).then(function () {
    markSelectedDir(path);
    focusActiveNode(path);
  });
}

/* -- path bar / navigation ------------------------------------------------- */
els.pathInput.addEventListener('keydown', function (e) {
  if (e.key === 'Enter') { e.preventDefault(); openFolder(els.pathInput.value.trim()); }
});

els.navUp.onclick = function () {
  var p = parentPath(state.path);
  if (p) { openFolder(p); }
};

els.navBack.onclick = function () {
  if (state.hIndex > 0) { state.hIndex--; openFolder(state.history[state.hIndex], { history: false }); }
};

els.navForward.onclick = function () {
  if (state.hIndex >= 0 && state.hIndex < state.history.length - 1) {
    state.hIndex++;
    openFolder(state.history[state.hIndex], { history: false });
  }
};

els.refreshBtn.onclick = function () {
  if (state.path) { openFolder(state.path, { history: false }); toast('Folder refreshed'); }
};
