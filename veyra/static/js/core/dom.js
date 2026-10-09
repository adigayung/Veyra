/* ==========================================================================
   Veyra — core/dom.js
   Small DOM / UI utilities shared by every feature module:
     * isEditableTarget  — keyboard guard while typing
     * anyDialogVisible  — keyboard guard while a modal is open
     * setCryptoStatus   — the single place the crypto/session UI is painted
   ========================================================================== */

/* Keyboard shortcuts (Delete, Ctrl+C/X/V, arrow navigation) must never fire
   while the user is editing an input/textarea/select or while a modal is open. */
function isEditableTarget(t) {
  if (!t) return false;
  var tag = (t.tagName || '').toUpperCase();
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || t.isContentEditable === true;
}

function anyDialogVisible() {
  try { if (aimgDialog && aimgDialog.style.display === 'block') return true; } catch (e) {}
  try { if (dialogs && dialogs.some(function (d) { return d.style.display === 'block'; })) return true; } catch (e) {}
  return false;
}

/* -- crypto session UI ----------------------------------------------------
   Paints the badge / status text / action buttons for the centralized
   session.  Every element is optional: the native shell owns the title bar,
   so the badge may not exist and a missing node must never abort the call. */
function setCryptoStatus(info) {
  if (info) {
    state.crypto.unlocked = !!info.unlocked;
    state.crypto.initialized = !!info.initialized;
    if (info.boundary) state.crypto.boundary = info.boundary;
  }
  var b = document.getElementById('cryptoBadge');
  if (b) {
    b.textContent = state.crypto.unlocked ? '🔓 Unlocked' : '🔒 Locked';
    b.className = 'crypto-badge ' + (state.crypto.unlocked ? 'open' : 'locked');
  }
  var cs = document.getElementById('cryptoStatus');
  if (cs) {
    cs.textContent = (state.crypto.unlocked ? 'Crypto session TERBUKA' : 'Crypto session TERKUNCI') +
      ' · ' + (state.crypto.initialized ? 'vault tersedia' : 'belum ada vault') +
      ' · boundary: ' + (state.crypto.boundary || '-');
  }
  var enc = document.getElementById('btnEncrypt'),
      dec = document.getElementById('btnDecrypt'),
      lk = document.getElementById('btnLock'),
      ul = document.getElementById('btnUnlock');
  if (enc) enc.disabled = !state.crypto.unlocked;
  if (dec) dec.disabled = !state.crypto.unlocked;
  if (lk) lk.disabled = !state.crypto.unlocked;
  if (ul) ul.disabled = state.crypto.unlocked;
}
