/* ==========================================================================
   Veyra — core/session.js
   Centralized login session (startup gate + idle auto-lock).

   One password, one unlock.  The login overlay is the single gate before the
   app body (startup) and after an idle auto-lock.  ANY non-empty password is
   accepted - it is only a key source: the password never unlocks every file,
   it only derives the keys of the files that match it.  The password is never
   kept in JS/UI state: it is typed into the field, POSTed to
   /api/aimg/unlock (CryptoService.unlock -> Argon2id password key -> DPAPI
   sealed session), then immediately cleared.  Auto-lock is driven by
   /api/session/status polling; heartbeats come from real user activity.
   ========================================================================== */

var loginOverlay = document.getElementById('loginOverlay');
var loginPassInput = document.getElementById('loginPass');
var loginBtn = document.getElementById('loginBtn');
var loginStatusEl = document.getElementById('loginStatus');
var loginHintEl = document.getElementById('loginHint');
var loginSubEl = document.getElementById('loginSub');

/* session.idleTimeout is the auto-lock interval (seconds) reported by the
   backend (default 300 = 5 minutes).  0 disables auto-lock. */
var session = { unlocked: false, idleTimeout: 300, pollTimer: null, lastHeartbeat: 0 };

function setLoginBusy(busy) {
  if (!loginPassInput || !loginBtn) return;
  loginPassInput.disabled = busy;
  loginBtn.disabled = busy;
  loginBtn.textContent = busy ? 'Membuka...' : 'Login';
}

function showLoginOverlay(reason) {
  if (!loginOverlay) return;
  if (!loginOverlay.classList.contains('hidden')) {
    if (loginPassInput) loginPassInput.focus();
    return;
  }
  if (reason === 'autolock' && loginSubEl) {
    loginSubEl.textContent = 'Session terkunci otomatis. Masukkan password kembali.';
  } else if (loginSubEl) {
    loginSubEl.textContent = 'Masukkan password untuk membuka session';
  }
  if (loginStatusEl) loginStatusEl.textContent = '';
  if (loginHintEl) loginHintEl.textContent = '';
  if (loginPassInput) {
    loginPassInput.value = '';
    try { loginPassInput.focus(); } catch (e) {}
  }
  loginOverlay.classList.remove('hidden');
}

function hideLoginOverlay() {
  if (!loginOverlay) return;
  loginOverlay.classList.add('hidden');
  if (loginPassInput) loginPassInput.value = '';
}

/* The app body only becomes interactive once the centralized session is
   unlocked.  On lock we re-show the login overlay WITHOUT destroying the
   mounted explorer/grid/viewer behind it (auto-lock must not ruin state). */
function updateSessionUi(unlocked, initialized) {
  if (!loginOverlay) return;
  if (unlocked) {
    hideLoginOverlay();
  } else if (!loginOverlay.classList.contains('hidden')) {
    /* already shown */
  } else {
    showLoginOverlay('startup');
  }
}

async function attemptLogin() {
  var pass = loginPassInput ? loginPassInput.value : '';
  if (!pass) {
    if (loginStatusEl) loginStatusEl.textContent = 'Password wajib diisi.';
    return;
  }
  setLoginBusy(true);
  if (loginStatusEl) loginStatusEl.textContent = '';
  try {
    var res = await postJSON('/api/aimg/unlock', { password: pass });
    var data = res.data || {};
    if (data.ok && data.unlocked) {
      /* Never keep the password in the DOM/state: clear it immediately. */
      if (loginPassInput) loginPassInput.value = '';
      setCryptoStatus(data);
      session.unlocked = true;
      hideLoginOverlay();
      /* Refresh the listing so .aimg thumbnails/previews reflect the now-open
         crypto session (an auto-lock re-login behaves exactly like the first
         login: the viewer resumes from the folder the user was on). */
      if (state.path) openFolder(state.path, { history: false });
    } else {
      if (loginStatusEl) loginStatusEl.textContent = data.error || 'Tidak dapat membuka session.';
      session.unlocked = false;
    }
  } catch (err) {
    if (loginStatusEl) loginStatusEl.textContent = 'Error: ' + err.message;
    session.unlocked = false;
  }
  setLoginBusy(false);
}

function stopSessionPolling() {
  if (session.pollTimer) {
    clearTimeout(session.pollTimer);
    session.pollTimer = null;
  }
}

/* Poll /api/session/status.  When the backend reports the idle timeout has
   elapsed (should_lock), the UI calls /api/session/lock (which wipes the
   session key material server-side) and re-shows the login overlay. */
function pollSessionStatus() {
  stopSessionPolling();
  /* Don't poll while the login overlay is already shown: there is nothing to
     auto-lock until the user re-authenticates. */
  if (loginOverlay && !loginOverlay.classList.contains('hidden')) {
    session.pollTimer = setTimeout(pollSessionStatus, Math.min(5000, (session.idleTimeout || 300) * 1000));
    return;
  }
  getJSON('/api/session/status').then(function (res) {
    var data = (res && res.data) || {};
    if (typeof data.idle_timeout === 'number') session.idleTimeout = data.idle_timeout;
    if (data.should_lock) {
      /* Backend says idle elapsed: lock now (wipes session key + caches). */
      return postJSON('/api/session/lock', {}).then(function () {
        session.unlocked = false;
        setCryptoStatus({ unlocked: false, initialized: data.initialized });
        showLoginOverlay('autolock');
      });
    }
  }).catch(function () { /* network blips are non-fatal; retry on next tick */ })
    .then(function () { scheduleNextPoll(); });
}

function scheduleNextPoll() {
  /* Poll at most every 2s; with a 5-min timeout that is plenty responsive and
     keeps the request rate low (tests set a tiny VEYRA_IDLE_TIMEOUT instead). */
  var interval = Math.max(1000, Math.min(2000, (session.idleTimeout || 300) * 250));
  session.pollTimer = setTimeout(pollSessionStatus, interval);
}

/* Throttled activity heartbeat: every keyboard/pointer event resets the idle
   timer on the server, but we never flood the backend (max one heartbeat per
   ~3s of continuous activity).  Activity is only reported while the session is
   actually unlocked (the login overlay does not generate "user activity"). */
var _lastHeartbeat = 0;

function _onActivity() {
  if (loginOverlay && loginOverlay.classList.contains('hidden') === false) return;
  var now = Date.now();
  if (now - _lastHeartbeat < 3000) return;
  _lastHeartbeat = now;
  postJSON('/api/session/heartbeat', {}).catch(function () {});
}

/* -- wiring (guarded: the shell always provides these nodes) -------------- */
if (loginPassInput) {
  loginPassInput.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); attemptLogin(); }
  });
}
if (loginBtn) {
  loginBtn.addEventListener('click', function (e) { e.preventDefault(); attemptLogin(); });
}
