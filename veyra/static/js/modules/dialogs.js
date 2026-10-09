/* ==========================================================================
   Veyra — modules/dialogs.js
   Modal dialogs: the shared factory plus Batch Rename, Copy/Move to Folder,
   New Folder, Rename and the Encrypt/Decrypt file-list dialog.
   ========================================================================== */

var dialogs = [];

/* Generic modal: header + body + OK/Cancel row.  ``buildBody`` fills the body
   (and may wire its own inputs); ``onOk`` receives the dialog element. */
function makeDialog(key, title, buildBody, onOk) {
  var d = document.createElement('div');
  d.className = 'modal-dialog';
  d.dataset.key = key;
  d.innerHTML = '<h3>' + esc(title) + '</h3><div class="dlg-body"></div>' +
    '<div class="aimg-row"><button class="tool" data-dlg-ok>OK</button>' +
    '<button class="tool" data-dlg-cancel>Cancel</button></div>';
  buildBody(d.querySelector('.dlg-body'), d);
  document.body.appendChild(d);
  d.querySelector('[data-dlg-ok]').onclick = function () { onOk(d); };
  d.querySelector('[data-dlg-cancel]').onclick = function () { d.style.display = 'none'; };
  dialogs.push(d);
  return d;
}

function showDialog(d) {
  d.style.display = 'block';
  var f = d.querySelector('input');
  if (f) { f.focus(); if (f.select) f.select(); }
}

function hideAllDialogs() { dialogs.forEach(function (d) { d.style.display = 'none'; }); }

/* -- batch rename ---------------------------------------------------------- */
/* Pure planner: prefix + optional zero-padded sequence + suffix, extension
   always preserved. */
function batchPlan(imgs, prefix, suffix, numbering, start, pad) {
  var out = [];
  for (var i = 0; i < imgs.length; i++) {
    var base = imgs[i].name, ext = '';
    var dot = base.lastIndexOf('.');
    if (dot > 0) ext = base.substring(dot);
    var seq = '';
    if (numbering) {
      seq = String(start + i);
      while (seq.length < pad) seq = '0' + seq;
    }
    out.push({ old: base, name: prefix + seq + suffix + ext });
  }
  return out;
}

function batchFormValues() {
  var prefix = document.getElementById('batchPrefix').value;
  var suffix = document.getElementById('batchSuffix').value;
  var numEl = document.getElementById('batchNumbering');
  var numbering = !!(numEl && numEl.checked);
  var start = parseInt(document.getElementById('batchStart').value, 10);
  var pad = parseInt(document.getElementById('batchPad').value, 10);
  if (isNaN(start) || start < 0) start = 1;
  if (isNaN(pad) || pad < 0) pad = 0;
  return { prefix: prefix, suffix: suffix, numbering: numbering, start: start, pad: pad };
}

function setBatchStatus(msg) {
  var el = document.getElementById('batchStatus');
  if (el) el.textContent = msg || '';
}

function updateBatchPreview() {
  var imgs = (batchDialog && batchDialog._items) || [];
  var host = document.getElementById('batchPreview');
  if (!host) return;
  if (!imgs.length) { host.innerHTML = '<div class="dlg-note">Tidak ada file terpilih.</div>'; return; }
  var v = batchFormValues();
  var plan = batchPlan(imgs, v.prefix, v.suffix, v.numbering, v.start, v.pad);
  host.innerHTML = '<b>' + imgs.length + ' file terpilih:</b><br>' +
    plan.map(function (x) { return esc(x.old) + ' \u2192 ' + esc(x.name); }).join('<br>');
}

var batchDialog = makeDialog('batch', 'Batch Rename', function (body) {
  body.innerHTML =
    '<label>Prefix</label>' +
    '<input id="batchPrefix" type="text" placeholder="mis. image_">' +
    '<label>Suffix</label>' +
    '<input id="batchSuffix" type="text" placeholder="mis. _final">' +
    '<label><input id="batchNumbering" type="checkbox" checked> Tambahkan nomor urut</label>' +
    '<label>Nomor awal</label>' +
    '<input id="batchStart" type="number" value="1" min="0">' +
    '<label>Zero padding (jumlah digit)</label>' +
    '<input id="batchPad" type="number" value="3" min="0">' +
    '<div class="dlg-note" id="batchPreview"></div>' +
    '<div class="dlg-note" id="batchStatus"></div>';
  body.querySelectorAll('input').forEach(function (el) {
    el.addEventListener('input', updateBatchPreview);
    el.addEventListener('change', updateBatchPreview);
  });
}, function (d) {
  var imgs = batchDialog._items || [];
  if (!imgs.length) { setBatchStatus('Tidak ada file terpilih.'); return; }
  var v = batchFormValues();
  var paths = imgs.map(function (x) { return x.path; });
  setBatchStatus('Memproses...');
  postJSON('/api/files/batch-rename', {
    paths: paths, prefix: v.prefix, suffix: v.suffix,
    numbering: v.numbering, start: v.start, padding: v.pad
  }).then(function (res) {
    var data = res.data || {};
    if (!data.ok) { setBatchStatus(data.error || 'Batch rename gagal.'); return; }
    var msg = 'Rename: ' + (data.renamed || 0) + ' berhasil';
    if (data.unchanged) msg += ', ' + data.unchanged + ' tanpa perubahan';
    if (data.failed) msg += ', ' + data.failed + ' gagal';
    msg += '.';
    setBatchStatus(msg);
    toast(msg);
    d.style.display = 'none';
    state.selection = []; state.selected = null; state.anchor = null;
    if (state.path) {
      openFolder(state.path, { history: false }).then(function () { refreshTreeNode(state.path, { expand: true }); });
    }
    loadGroups();
  }).catch(function (err) { setBatchStatus('Error: ' + err.message); });
});

function openBatchRenameDialog() {
  var imgs = getSelectedImages();
  if (!imgs.length) { toast('Pilih file dulu.'); return; }
  batchDialog._items = imgs;
  document.getElementById('batchPrefix').value = 'image_';
  document.getElementById('batchSuffix').value = '';
  document.getElementById('batchNumbering').checked = true;
  document.getElementById('batchStart').value = '1';
  document.getElementById('batchPad').value = '3';
  setBatchStatus('');
  updateBatchPreview();
  showDialog(batchDialog);
}

/* -- copy / move to folder ------------------------------------------------- */
var targetDialog = makeDialog('target', 'Copy to Folder...', function (body) {
  body.innerHTML = '<label>Folder tujuan</label>' +
    '<input id="targetFolder" type="text" placeholder="Path folder tujuan">';
}, function (d) {
  var t = document.getElementById('targetFolder').value.trim();
  if (!t) { toast('Folder tujuan wajib diisi.'); return; }
  var paths = getSelectedImages().map(function (x) { return x.path; });
  if (!paths.length) { toast('Pilih file dulu.'); return; }
  var op = d.dataset.op === 'move' ? 'move' : 'copy';
  postJSON(op === 'move' ? '/api/files/move' : '/api/files/copy', { paths: paths, target: t }).then(function (res) {
    var data = res.data || {};
    if (data.ok) {
      toast((op === 'move' ? 'Dipindah ' : 'Disalin ') + ((data.moved || 0) + (data.copied || 0)) + ' file.');
      d.style.display = 'none';
      if (state.path) openFolder(state.path, { history: false });
    } else { toast(data.error || 'Operasi gagal.'); }
  }).catch(function (err) { toast('Error: ' + err.message); });
});

function openTargetFolderDialog(op) {
  if (!getSelectedImages().length) { toast('Pilih file dulu.'); return; }
  targetDialog.dataset.op = op;
  targetDialog.querySelector('h3').textContent = (op === 'move' ? 'Move to Folder...' : 'Copy to Folder...');
  document.getElementById('targetFolder').value = state.path || '';
  showDialog(targetDialog);
}

/* -- new folder ------------------------------------------------------------ */
var newFolderDialog = makeDialog('newfolder', 'New Folder', function (body) {
  body.innerHTML = '<label>Nama folder baru</label>' +
    '<input id="newFolderName" type="text" placeholder="Nama folder">';
}, function (d) {
  var name = document.getElementById('newFolderName').value.trim();
  if (!name) { toast('Nama folder wajib diisi.'); return; }
  if (!state.path) { toast('Tidak ada folder aktif.'); return; }
  apiNewFolder(state.path, name).then(function (res) {
    var data = res.data || {};
    if (data.ok) {
      toast('Folder dibuat: ' + data.folder.name);
      d.style.display = 'none';
      var parent = state.path;
      /* Reload the listing and refresh the explorer so the new folder shows up
         (and can be opened) without a manual page reload. */
      openFolder(parent, { history: false }).then(function () { refreshTreeNode(parent, { expand: true }); });
    } else { toast(data.error || 'Gagal membuat folder.'); }
  }).catch(function (err) { toast('Error: ' + err.message); });
});

function openNewFolderDialog() {
  document.getElementById('newFolderName').value = '';
  showDialog(newFolderDialog);
}

/* -- rename ---------------------------------------------------------------- */
var renameDialog = makeDialog('rename', 'Rename', function (body) {
  body.innerHTML = '<label>Nama baru</label>' +
    '<input id="renameName" type="text" placeholder="Nama file">';
}, function (d) {
  var idx = primarySelectedIndex();
  if (idx == null) { toast('Pilih file dulu.'); return; }
  var name = document.getElementById('renameName').value.trim();
  if (!name) { toast('Nama wajib diisi.'); return; }
  var img = state.images[idx];
  apiRename(img.path, name).then(function (res) {
    var data = res.data || {};
    if (data.ok) {
      toast('File di-rename.');
      d.style.display = 'none';
      if (state.path) openFolder(state.path, { history: false });
    } else { toast(data.error || 'Gagal rename.'); }
  }).catch(function (err) { toast('Error: ' + err.message); });
});

function openRenameDialog() {
  var idx = primarySelectedIndex();
  if (idx == null) { toast('Pilih file dulu.'); return; }
  document.getElementById('renameName').value = state.images[idx].name;
  showDialog(renameDialog);
}

/* -- encrypt / decrypt selected files ------------------------------------- */
var cryptoFilesDialog = makeDialog('cryptofiles', 'Encrypt', function (body) {}, function (d) {
  var op = d.dataset.op || 'encrypt';
  var imgs = cryptoValidSelection(op);
  if (!imgs.length) { setCryptoFilesStatus('Tidak ada file yang sesuai pada pilihan.'); return; }
  var passEl = document.getElementById('cryptoFilesPass');
  var pass = passEl ? passEl.value : '';
  if (!state.crypto.unlocked && !pass) { setCryptoFilesStatus('Password wajib untuk membuka crypto session.'); return; }
  var paths = imgs.map(function (x) { return x.path; });
  setCryptoFilesStatus((op === 'decrypt' ? 'Mendekripsi ' : 'Mengenkripsi ') + paths.length + ' file...');
  runCryptoFiles(op, paths, pass).then(function (res) {
    var data = res.data || {};
    if (data.ok) {
      var skip = data.skipped || 0;
      var summary = (op === 'decrypt' ? 'Dekripsi' : 'Enkripsi') + ': ' + (data.success || 0) + ' berhasil' +
        (data.failed ? (', ' + data.failed + ' gagal') : '') + (skip ? (', ' + skip + ' dilewati') : '') + '.';
      setCryptoFilesStatus(summary);
      toast(summary);
      if (state.path) openFolder(state.path, { history: false });
      if (!data.failed) d.style.display = 'none';
    } else {
      setCryptoFilesStatus(data.error || 'Operasi gagal.');
    }
  }).catch(function (err) { setCryptoFilesStatus('Error: ' + err.message); });
});

function openCryptoFilesDialog(op) {
  op = (op === 'decrypt') ? 'decrypt' : 'encrypt';
  var sel = getSelectedImages();
  if (!sel.length) { toast('Pilih file dulu.'); return; }
  var valid = cryptoValidSelection(op);
  var rejected = sel.length - valid.length;
  if (!valid.length) {
    toast(op === 'decrypt'
      ? 'Hanya file .aimg yang dapat didekripsi.'
      : 'Hanya image biasa yang didukung yang dapat dienkripsi.');
    return;
  }
  cryptoFilesDialog.dataset.op = op;
  cryptoFilesDialog.querySelector('h3').textContent = (op === 'decrypt' ? 'Decrypt Files' : 'Encrypt Files');
  var rows = valid.slice(0, 40).map(function (x) { return '<li>' + esc(x.name) + '</li>'; }).join('');
  var body = cryptoFilesDialog.querySelector('.dlg-body');
  /* Centralized login: when the session is already unlocked, Encrypt/Decrypt
     must NOT ask for a password again.  The field only appears while locked. */
  var passField = '';
  if (!state.crypto.unlocked) {
    passField = '<label>Password crypto session</label>' +
      '<input id="cryptoFilesPass" type="password" placeholder="Password crypto session">';
  }
  body.innerHTML =
    passField +
    '<div class="dlg-note">' + (op === 'decrypt'
      ? 'File .aimg yang akan didekripsi (' + valid.length + '):'
      : 'Image yang akan dienkripsi (' + valid.length + '):') + '</div>' +
    '<ul class="file-list">' + rows + '</ul>' +
    (valid.length > 40 ? ('<div class="dlg-note">... +' + (valid.length - 40) + ' file lain</div>') : '') +
    (rejected ? ('<div class="dlg-warn">' + rejected + ' file dilewati karena jenisnya tidak sesuai.</div>') : '') +
    '<div class="dlg-note" id="cryptoFilesStatus"></div>';
  showDialog(cryptoFilesDialog);
}
