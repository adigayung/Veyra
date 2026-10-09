/* ==========================================================================
   Veyra — boot.js
   Startup sequence (loaded last).

   Centralized login FIRST: the login overlay gates the app body.  The body
   still boots in the background so state is ready the moment the user
   unlocks, and the same password unlocks every AIMG operation.
   ========================================================================== */

loadGroups();

getJSON('/api/config').then(function (res) {
  var cfg = res.data || {};
  if (cfg.crypto) { setCryptoStatus(cfg.crypto); }
  if (cfg.session) {
    session.unlocked = !!cfg.session.unlocked;
    session.idleTimeout = cfg.session.idle_timeout || 300;
  }
  loadTree();
  openFolder(cfg.default_path || null);
  /* Startup gate: if the centralized session is locked (the normal case at
     first launch), show the login overlay immediately.  Once unlocked the
     overlay is hidden and idle auto-lock polling begins. */
  var unlocked = cfg.session && cfg.session.unlocked;
  updateSessionUi(unlocked, cfg.crypto && cfg.crypto.initialized);
  if (!unlocked) { showLoginOverlay('startup'); }
  scheduleNextPoll();
}).catch(function () {
  loadTree();
  openFolder(null);
  showLoginOverlay('startup');
  scheduleNextPoll();
});

/* -- idle auto-lock: keyboard + pointer activity listeners -----------------
   Both pointer movement and keyboard activity reset the idle timer on the
   server (via /api/session/heartbeat).  The listeners are passive and do not
   interfere with any existing handler (they only call _onActivity, throttled). */
['mousemove', 'pointermove', 'mousedown', 'pointerdown', 'wheel', 'touchstart'].forEach(function (ev) {
  document.addEventListener(ev, _onActivity, { passive: true });
});
document.addEventListener('keydown', _onActivity, { passive: true });
window.addEventListener('beforeunload', stopSessionPolling);
