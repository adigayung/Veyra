/* ==========================================================================
   Veyra — modules/aimg.js
   AIMG / crypto session UI: the toolbar panel that unlocks/locks the crypto
   session and encrypts/decrypts whole folders.

   The heavy lifting lives on the server (CryptoService / AimgService); this
   module only drives the panel and reports progress.
   ========================================================================== */

var aimgDialog = document.createElement('div');
aimgDialog.className = 'aimg-dialog';
aimgDialog.innerHTML =
  '<h3>Enkripsi &amp; Dekripsi · Crypto Session</h3>' +
  '<div class="aimg-status" id="cryptoStatus"></div>' +
  '<label>Password</label>' +
  '<input id="aimgPass" type="password" placeholder="Password crypto session">' +
  '<div class="aimg-row"><button id="btnUnlock" class="tool">Unlock</button><button id="btnLock" class="tool">Lock</button></div>' +
  '<label>Path Folder</label>' +
  '<input id="aimgPath" type="text" placeholder="Path folder (kosong = folder saat ini)">' +
  '<div class="aimg-row"><button id="btnEncrypt" class="tool">Enkripsi</button><button id="btnDecrypt" class="tool">Dekripsi</button></div>' +
  '<div class="aimg-status" id="aimgStatus"></div>' +
  '<div class="aimg-progress" id="aimgProgress"></div>' +
  '<button id="btnCloseAimg" class="tool" style="margin-top:12px;width:100%;height:30px;font-size:12px;">Tutup</button>';
document.body.appendChild(aimgDialog);

var aimgBusy = false;

function setAimgBusy(busy) {
  aimgBusy = busy;
  ['btnEncrypt', 'btnDecrypt', 'btnUnlock', 'btnLock', 'aimgPath', 'aimgPass'].forEach(function (id) {
    var el = document.getElementById(id);
    if (el) el.disabled = busy;
  });
  if (!busy) { setCryptoStatus(); }
}

function showAimgDialog() {
  var pathInput = document.getElementById('aimgPath');
  pathInput.value = state.path || '';
  document.getElementById('aimgPass').value = '';
  document.getElementById('aimgStatus').textContent = '';
  document.getElementById('aimgProgress').textContent = '';
  setCryptoStatus();
  aimgDialog.style.display = 'block';
}

function hideAimgDialog() { aimgDialog.style.display = 'none'; }

async function doUnlock() {
  var pass = document.getElementById('aimgPass').value;
  if (!pass) { document.getElementById('aimgStatus').textContent = 'Password wajib diisi.'; return; }
  setAimgBusy(true);
  document.getElementById('aimgStatus').textContent = 'Mengunlock...';
  try {
    var res = await postJSON('/api/aimg/unlock', { password: pass });
    if (res.data && res.data.ok) {
      document.getElementById('aimgStatus').textContent = 'Crypto session terbuka.';
      document.getElementById('aimgPass').value = '';
      setCryptoStatus(res.data);
      if (state.path) { openFolder(state.path, { history: false }); }
    } else {
      document.getElementById('aimgStatus').textContent = 'Unlock gagal: ' + ((res.data && res.data.error) || 'unknown');
    }
  } catch (err) { document.getElementById('aimgStatus').textContent = 'Error: ' + err.message; }
  setAimgBusy(false);
}

async function doLock() {
  setAimgBusy(true);
  try {
    var res = await postJSON('/api/aimg/lock', {});
    setCryptoStatus(res.data);
    document.getElementById('aimgStatus').textContent = 'Crypto session terkunci.';
    if (state.path) { openFolder(state.path, { history: false }); }
  } catch (err) { document.getElementById('aimgStatus').textContent = 'Error: ' + err.message; }
  setAimgBusy(false);
}

async function runAimg(op) {
  if (!state.crypto.unlocked) {
    document.getElementById('aimgStatus').textContent = 'Crypto session terkunci. Unlock dulu.';
    return;
  }
  var path = document.getElementById('aimgPath').value.trim();
  if (!path && state.path) path = state.path;
  if (!path) { document.getElementById('aimgStatus').textContent = 'Pilih folder terlebih dahulu.'; return; }
  setAimgBusy(true);
  document.getElementById('aimgStatus').textContent = op === 'encrypt' ? 'Mengenkripsi...' : 'Mendekripsi...';
  document.getElementById('aimgProgress').textContent = '';
  try {
    var res = await postJSON('/api/aimg/' + op, { path: path });
    var data = res.data || {};
    if (data.ok) {
      document.getElementById('aimgStatus').textContent = op === 'encrypt' ? 'Enkripsi selesai.' : 'Dekripsi selesai.';
      document.getElementById('aimgProgress').textContent =
        'Ditemukan: ' + data.found + ', Berhasil: ' + data.success + ', Gagal: ' + data.failed;
      if (data.errors && data.errors.length) {
        document.getElementById('aimgProgress').textContent += ' (detail di console)';
        console.warn('AIMG errors:', data.errors);
      }
      if (state.path) { openFolder(state.path, { history: false }); }
    } else {
      document.getElementById('aimgStatus').textContent = data.error || 'Operasi gagal.';
    }
  } catch (err) { document.getElementById('aimgStatus').textContent = 'Error: ' + err.message; }
  setAimgBusy(false);
}

/* -- encrypt / decrypt the SELECTION (context menu Tools -> Encrypt/Decrypt) */
/* The modal shows a password field plus the list of files taken from the
   selection at the moment the menu was opened.  Validation happens both here
   (clear feedback; a .aimg is never offered to Encrypt and a plain image is
   never offered to Decrypt) and on the server. */
function cryptoValidSelection(op) {
  var sel = getSelectedImages();
  if (op === 'decrypt') return sel.filter(function (x) { return !!x.encrypted; });
  return sel.filter(function (x) { return !x.encrypted; });
}

function setCryptoFilesStatus(msg) {
  var el = document.getElementById('cryptoFilesStatus');
  if (el) el.textContent = msg || '';
}

function runCryptoFiles(op, paths, password) {
  var seq = Promise.resolve();
  if (password && !state.crypto.unlocked) {
    seq = postJSON('/api/aimg/unlock', { password: password }).then(function (res) {
      var data = res.data || {};
      if (!data.ok) throw new Error(data.error || 'Unlock gagal.');
      setCryptoStatus(data);
    });
  }
  return seq.then(function () {
    return postJSON(op === 'decrypt' ? '/api/aimg/decrypt-files' : '/api/aimg/encrypt-files', { paths: paths });
  });
}

/* -- wiring (toolbar panel) ------------------------------------------------ */
document.getElementById('btnAimg').onclick = showAimgDialog;
document.getElementById('btnCloseAimg').onclick = hideAimgDialog;
document.getElementById('btnUnlock').onclick = doUnlock;
document.getElementById('btnLock').onclick = doLock;
document.getElementById('btnEncrypt').onclick = function () { runAimg('encrypt'); };
document.getElementById('btnDecrypt').onclick = function () { runAimg('decrypt'); };
document.getElementById('aimgPass').addEventListener('keydown', function (e) {
  if (e.key === 'Enter') { e.preventDefault(); doUnlock(); }
});
