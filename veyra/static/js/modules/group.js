/* ==========================================================================
   Veyra — modules/group.js
   Groups: the sidebar panel (create / rename / delete / members) and the
   toolbar Group filter that switches the Image Grid dataset between the live
   File-Explorer listing ("Normal") and a group's members.

   Everything is DB backed through the EXISTING Group subsystem: the list comes
   from GET /api/groups and the members from GET /api/groups/detail - no second
   model and no duplicate database logic.
   ========================================================================== */

function loadGroups() {
  return getJSON('/api/groups').then(function (res) {
    var data = res.data || {};
    groupsState.list = data.groups || [];
    renderGroups();
    renderGroupFilter();
    if (groupsState.selected != null) {
      var still = groupsState.list.some(function (g) { return g.id === groupsState.selected; });
      if (still) { openGroup(groupsState.selected); }
      else { groupsState.selected = null; groupsState.detail = null; renderGroupDetail(); }
    }
    /* Keep the Image-Grid group dataset in sync with the DB: a still-existing
       group is re-read from the DB (members may have changed), a deleted one
       falls back to the Normal (explorer) dataset. */
    if (state.group != null) {
      if (groupsState.list.some(function (g) { return g.id === state.group; })) { selectGroupDataset(state.group); }
      else { resetToNormalDataset(); }
    }
  }).catch(function (err) { toast('Gagal memuat group: ' + err.message); });
}

/* -- sidebar panel --------------------------------------------------------- */
function renderGroups() {
  var host = document.getElementById('groupList');
  /* The sidebar GROUPS panel is gone from the shell, so the list host may not
     exist: painting is a no-op then (the toolbar Group selector still works). */
  if (!host) return;
  if (!groupsState.list.length) { host.innerHTML = '<li class="group-empty">Belum ada group.</li>'; return; }
  host.innerHTML = groupsState.list.map(function (g) {
    return '<li data-id="' + g.id + '" class="' + (g.id === groupsState.selected ? 'selected' : '') + '">' +
      '<span class="gname" title="' + esc(g.name) + '">' + esc(g.name) + '</span>' +
      '<span class="gcount" title="Jumlah member">' + g.member_count + '</span>' +
      '<button class="mini" data-act="rename" title="Rename">\u270e</button>' +
      '<button class="mini" data-act="delete" title="Delete">\ud83d\uddd1</button></li>';
  }).join('');
  host.querySelectorAll('li[data-id]').forEach(function (li) {
    var id = Number(li.dataset.id);
    li.querySelector('.gname').onclick = function () { openGroup(id); };
    li.querySelector('.gcount').onclick = function () { openGroup(id); };
    li.querySelector('[data-act="rename"]').onclick = function (e) { e.stopPropagation(); renameGroup(id); };
    li.querySelector('[data-act="delete"]').onclick = function (e) { e.stopPropagation(); deleteGroup(id); };
  });
}

function renderGroupDetail() {
  var panel = document.getElementById('groupDetail');
  if (!panel) return; /* sidebar detail panel removed -> nothing to paint */
  var d = groupsState.detail;
  if (!d) { panel.hidden = true; return; }
  panel.hidden = false;
  document.getElementById('groupDetailTitle').textContent = 'Members \u00b7 ' + d.name + ' (' + d.members.length + ')';
  var host = document.getElementById('memberList');
  if (!d.members.length) { host.innerHTML = '<li class="group-empty">Belum ada member.</li>'; return; }
  host.innerHTML = d.members.map(function (m) {
    return '<li><span class="mflag">' + (m.is_aimg ? '\ud83d\udd12' : '\ud83d\uddbc') + '</span>' +
      '<span class="mname" title="' + esc(m.path) + '">' + esc(m.name) + '</span>' +
      '<button class="mini" data-fid="' + m.id + '" title="Remove from group">\u2715</button></li>';
  }).join('');
  host.querySelectorAll('button[data-fid]').forEach(function (b) {
    b.onclick = function () { removeMember(Number(b.dataset.fid)); };
  });
}

function openGroup(id) {
  groupsState.selected = id;
  renderGroups();
  getJSON('/api/groups/detail?id=' + id).then(function (res) {
    var data = res.data || {};
    if (data.ok) { groupsState.detail = data.group; }
    else { groupsState.detail = null; toast(data.error || 'Group tidak ditemukan.'); }
    renderGroupDetail();
  }).catch(function (err) { toast('Gagal memuat group: ' + err.message); });
}

function createGroup() {
  var input = document.getElementById('groupName');
  var name = (input.value || '').trim();
  if (!name) { toast('Nama group wajib diisi.'); return; }
  postJSON('/api/groups', { name: name }).then(function (res) {
    var data = res.data || {};
    if (data.ok) { input.value = ''; toast('Group dibuat.'); loadGroups(); }
    else { toast(data.error || 'Gagal membuat group.'); }
  }).catch(function (err) { toast('Gagal membuat group: ' + err.message); });
}

function renameGroup(id) {
  var current = groupsState.list.filter(function (x) { return x.id === id; })[0];
  var name = prompt('Rename group:', current ? current.name : '');
  if (name == null) return;
  name = name.trim();
  if (!name) { toast('Nama group wajib diisi.'); return; }
  postJSON('/api/groups/rename', { id: id, name: name }).then(function (res) {
    var data = res.data || {};
    if (data.ok) { toast('Group di-rename.'); loadGroups(); }
    else { toast(data.error || 'Gagal rename.'); }
  }).catch(function (err) { toast('Gagal rename: ' + err.message); });
}

function deleteGroup(id) {
  if (!confirm('Hapus group ini? File fisik tidak akan dihapus.')) return;
  postJSON('/api/groups/delete', { id: id }).then(function (res) {
    var data = res.data || {};
    if (data.ok) {
      toast('Group dihapus (' + data.memberships_removed + ' membership).');
      if (groupsState.selected === id) { groupsState.selected = null; groupsState.detail = null; renderGroupDetail(); }
      loadGroups();
    } else { toast(data.error || 'Gagal menghapus group.'); }
  }).catch(function (err) { toast('Gagal menghapus group: ' + err.message); });
}

function addSelectedToGroup() {
  if (groupsState.selected == null) { toast('Pilih group dulu.'); return; }
  if (state.selected == null || !state.images[state.selected]) { toast('Pilih file dulu di grid.'); return; }
  var img = state.images[state.selected];
  postJSON('/api/groups/add', { id: groupsState.selected, path: img.path }).then(function (res) {
    var data = res.data || {};
    if (data.ok) {
      toast(data.duplicate ? 'File sudah ada di group.' : 'File ditambahkan ke group.');
      openGroup(groupsState.selected);
      loadGroups();
    } else { toast(data.error || 'Gagal menambah file.'); }
  }).catch(function (err) { toast('Gagal menambah file: ' + err.message); });
}

function removeMember(fileId) {
  if (groupsState.selected == null) return;
  postJSON('/api/groups/remove', { id: groupsState.selected, file_id: fileId }).then(function (res) {
    var data = res.data || {};
    if (data.ok) { toast('Membership dihapus.'); openGroup(groupsState.selected); loadGroups(); }
    else { toast(data.error || 'Gagal menghapus membership.'); }
  }).catch(function (err) { toast('Gagal menghapus membership: ' + err.message); });
}

/* Bulk add/remove of the current selection (context menu). */
function addSelectionToGroup(groupId) {
  var imgs = getSelectedImages();
  if (!imgs.length) { toast('Pilih file dulu.'); return; }
  var done = 0, fail = 0;
  var tasks = imgs.map(function (img) {
    return postJSON('/api/groups/add', { id: groupId, path: img.path }).then(function (res) {
      if (res.data && res.data.ok) done++; else fail++;
    }).catch(function () { fail++; });
  });
  Promise.all(tasks).then(function () {
    toast('Ditambahkan ke group: ' + done + (fail ? (' (' + fail + ' gagal)') : ''));
    loadGroups();
    if (groupsState.selected != null) openGroup(groupsState.selected);
  });
}

function removeSelectionFromGroup(groupId) {
  var imgs = getSelectedImages();
  if (!imgs.length) { toast('Pilih file dulu.'); return; }
  var done = 0, fail = 0;
  var tasks = imgs.map(function (img) {
    return postJSON('/api/groups/remove', { id: groupId, path: img.path }).then(function (res) {
      if (res.data && res.data.ok) done++; else fail++;
    }).catch(function () { fail++; });
  });
  Promise.all(tasks).then(function () {
    toast('Dihapus dari group: ' + done + (fail ? (' (' + fail + ' gagal)') : ''));
    loadGroups();
    if (groupsState.selected != null) openGroup(groupsState.selected);
  });
}

/* -- wiring (sidebar panel) ------------------------------------------------
   The sidebar GROUPS panel was removed from the shell, so every control below
   is optional: wire it only when present.  This keeps the module load-safe
   (no null dereference) while the toolbar Group selector + context menu keep
   the whole group workflow available. */
var btnCreateGroup = document.getElementById('btnCreateGroup');
if (btnCreateGroup) { btnCreateGroup.onclick = createGroup; }
var groupNameInput = document.getElementById('groupName');
if (groupNameInput) {
  groupNameInput.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); createGroup(); }
  });
}
var btnAddSelected = document.getElementById('btnAddSelected');
if (btnAddSelected) { btnAddSelected.onclick = addSelectedToGroup; }

/* -- toolbar Group filter (Image Grid dataset selector) ------------------- */
var GROUP_SEP = '\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500';

function renderGroupFilter() {
  var sel = els.groupFilter;
  if (!sel) return;
  var html = '<option value="">Normal</option>';
  if (groupsState.list.length) {
    html += '<option value="__sep1" disabled>' + GROUP_SEP + '</option>';
    groupsState.list.forEach(function (g) {
      html += '<option value="' + g.id + '">' + esc(g.name) + ' (' + g.member_count + ')</option>';
    });
  }
  html += '<option value="__sep2" disabled>' + GROUP_SEP + '</option>';
  html += '<option value="__add">Add Group</option>';
  html += '<option value="__edit">Edit Group</option>';
  html += '<option value="__remove">Remove Group</option>';
  sel.innerHTML = html;
  sel.value = (state.group != null) ? String(state.group) : '';
}

function syncGroupFilterValue() {
  if (els.groupFilter) els.groupFilter.value = (state.group != null) ? String(state.group) : '';
}

/* Map a DB group member (GroupService._file_dict) onto the same image shape the
   grid/viewer consume from /api/browse (ImageInfo.to_dict), so Sort By and the
   Image View behave identically for a group dataset. */
function groupMemberToImage(m) {
  return {
    name: m.name,
    path: m.path,
    size: m.size || 0,
    modified_at: Date.parse(m.modified) || 0,
    type: String(m.extension || '').toUpperCase(),
    encrypted: !!m.is_aimg,
    width: 0,
    height: 0,
    groupFileId: m.id
  };
}

function resetToNormalDataset() {
  state.group = null;
  state.groupImages = [];
  groupsState.selected = null;
  groupsState.detail = null;
  renderGroupDetail();
  renderGroups();
  rebuildImages();
  syncGroupFilterValue();
}

function selectGroupDataset(id) {
  return getJSON('/api/groups/detail?id=' + id).then(function (res) {
    var data = res.data || {};
    if (!data.ok || !data.group) { toast(data.error || 'Group tidak ditemukan.'); syncGroupFilterValue(); return; }
    groupsState.selected = id;
    groupsState.detail = data.group;
    renderGroups();
    renderGroupDetail();
    state.group = id;
    state.groupImages = (data.group.members || []).map(groupMemberToImage);
    rebuildImages();
    syncGroupFilterValue();
  }).catch(function (err) { toast('Gagal memuat group: ' + err.message); syncGroupFilterValue(); });
}

function applyGroupFilter(value) {
  if (value === '__add') { createGroupPrompt(); return; }
  if (value === '__edit') { renameActiveGroup(); return; }
  if (value === '__remove') { removeActiveGroup(); return; }
  if (value == null || value === '') { resetToNormalDataset(); return; }
  selectGroupDataset(Number(value));
}

function createGroupPrompt() {
  var name = prompt('Nama group baru:');
  if (name == null) { syncGroupFilterValue(); return; }
  name = name.trim();
  if (!name) { toast('Nama group wajib diisi.'); syncGroupFilterValue(); return; }
  postJSON('/api/groups', { name: name }).then(function (res) {
    var data = res.data || {};
    if (data.ok) { toast('Group dibuat.'); loadGroups(); }
    else { toast(data.error || 'Gagal membuat group.'); syncGroupFilterValue(); }
  }).catch(function (err) { toast('Gagal membuat group: ' + err.message); syncGroupFilterValue(); });
}

function renameActiveGroup() {
  if (state.group == null) { toast('Pilih group dulu.'); syncGroupFilterValue(); return; }
  var current = groupsState.list.filter(function (x) { return x.id === state.group; })[0];
  var name = prompt('Rename group:', current ? current.name : '');
  if (name == null || !name.trim()) { syncGroupFilterValue(); return; }
  postJSON('/api/groups/rename', { id: state.group, name: name.trim() }).then(function (res) {
    var data = res.data || {};
    if (data.ok) { toast('Group di-rename.'); loadGroups(); }
    else { toast(data.error || 'Gagal rename.'); syncGroupFilterValue(); }
  }).catch(function (err) { toast('Gagal rename: ' + err.message); syncGroupFilterValue(); });
}

function removeActiveGroup() {
  if (state.group == null) { toast('Pilih group dulu.'); syncGroupFilterValue(); return; }
  if (!confirm('Hapus group ini? File fisik tidak akan dihapus.')) { syncGroupFilterValue(); return; }
  postJSON('/api/groups/delete', { id: state.group }).then(function (res) {
    var data = res.data || {};
    if (data.ok) {
      toast('Group dihapus (' + data.memberships_removed + ' membership).');
      state.group = null;
      state.groupImages = [];
      groupsState.selected = null;
      groupsState.detail = null;
      renderGroupDetail();
      loadGroups();
    } else { toast(data.error || 'Gagal menghapus group.'); syncGroupFilterValue(); }
  }).catch(function (err) { toast('Gagal menghapus group: ' + err.message); syncGroupFilterValue(); });
}

if (els.groupFilter) {
  els.groupFilter.onchange = function () { applyGroupFilter(this.value); };
}
