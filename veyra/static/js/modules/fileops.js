/* ==========================================================================
   Veyra — modules/fileops.js
   File operations exposed to the UI: copy / move / rename / new folder /
   delete / encrypt / decrypt plus the Veyra-internal clipboard.

   These are thin wrappers over the EXISTING service/API layer; no business
   rule lives here.
   ========================================================================== */

function apiCopy(paths, target) { return postJSON('/api/files/copy', { paths: paths, target: target }); }
function apiMove(paths, target) { return postJSON('/api/files/move', { paths: paths, target: target }); }
function apiRename(path, name) { return postJSON('/api/files/rename', { path: path, name: name }); }
function apiNewFolder(parent, name) { return postJSON('/api/files/newfolder', { parent: parent, name: name }); }
function apiEncryptFiles(paths) { return postJSON('/api/aimg/encrypt-files', { paths: paths }); }
function apiDecryptFiles(paths) { return postJSON('/api/aimg/decrypt-files', { paths: paths }); }
function apiRemoveFiles(paths) { return postJSON('/api/files/delete', { paths: paths }); }

/* -- internal clipboard (the Veyra file clipboard is pure JS state) ------- */
function setClipboard(mode) {
  var imgs = getSelectedImages();
  if (!imgs.length) { toast('Pilih file dulu.'); return; }
  state.clipboard = { mode: mode, paths: imgs.map(function (x) { return x.path; }) };
  toast((mode === 'cut' ? 'Cut ' : 'Copy ') + imgs.length + ' file.');
}

function pasteClipboard() {
  if (!state.clipboard || !state.clipboard.paths.length) { toast('Clipboard kosong.'); return; }
  if (!state.path) { toast('Tidak ada folder aktif.'); return; }
  var move = state.clipboard.mode === 'cut';
  var url = move ? '/api/files/move' : '/api/files/copy';
  var paths = state.clipboard.paths;
  postJSON(url, { paths: paths, target: state.path }).then(function (res) {
    var data = res.data || {};
    if (data.ok) {
      toast((move ? 'Dipindah ' : 'Disalin ') + ((data.moved || 0) + (data.copied || 0)) + ' file.');
      if (move) state.clipboard = null;
      if (state.path) openFolder(state.path, { history: false });
    } else { toast(data.error || 'Paste gagal.'); }
  }).catch(function (err) { toast('Error: ' + err.message); });
}

/* -- delete ---------------------------------------------------------------- */
function removeSelectedFiles() {
  var imgs = getSelectedImages();
  if (!imgs.length) { toast('Pilih file dulu.'); return; }
  var ask = imgs.length > 1
    ? 'Hapus ' + imgs.length + ' file terpilih?\n\n' + imgs.map(function (x) { return x.name; }).join('\n')
    : 'Hapus file "' + imgs[0].name + '"?';
  if (!confirm(ask)) return;
  var paths = imgs.map(function (x) { return x.path; });
  apiRemoveFiles(paths).then(function (res) {
    var data = res.data || {};
    if (!data.ok) { toast(data.error || 'Hapus gagal.'); return; }
    var done = data.deleted || 0, fail = data.failed || 0;
    toast('Dihapus: ' + done + (fail ? (' (' + fail + ' gagal)') : ''));
    state.selection = []; state.selected = null; state.anchor = null;
    applySelectionClasses();
    updateStatus();
    updateStatusFile();
    if (state.path) {
      openFolder(state.path, { history: false }).then(function () {
        refreshTreeNode(state.path, { expand: true });
      });
    }
    loadGroups();
  }).catch(function (err) { toast('Error: ' + err.message); });
}

/* -- encrypt / decrypt (context menu) ------------------------------------- */
/* The heavy lifting lives in openCryptoFilesDialog(): the context menu opens a
   modal (password + the selected file list) instead of running blindly. */
function encryptSelection() { openCryptoFilesDialog('encrypt'); }
function decryptSelection() { openCryptoFilesDialog('decrypt'); }
