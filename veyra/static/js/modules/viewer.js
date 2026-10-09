/* ==========================================================================
   Veyra — modules/viewer.js
   Image View / single image: the chrome-free fullscreen surface with zoom,
   pan, previous/next navigation and the native desktop fullscreen bridge
   (window.__aether_fs, injected by desktop.py / pywebview).
   ========================================================================== */

var fsView = document.createElement('div');
fsView.className = 'fs-view';
/* Fullscreen surface: only the image is visible. No UI chrome. */
fsView.innerHTML = '<img id="fsImg" alt="">';
document.body.appendChild(fsView);

var fsImg = document.getElementById('fsImg');

/* View-Image geometry.  ``mode`` is 'actual' (native 100%) or 'fitwidth';
   ``scale`` is the zoom relative to the native pixel size; tx/ty is the pan
   offset in CSS px.  natW/natH are the real pixel dimensions, read on load. */
function fsViewport() { return { w: fsView.clientWidth || 0, h: fsView.clientHeight || 0 }; }

function fsDisplayedSize() {
  var s = state.fs.scale || 1;
  return { w: (state.fs.natW || 0) * s, h: (state.fs.natH || 0) * s };
}

function fsClamp() {
  var vp = fsViewport();
  if (!(vp.w > 0) || !(vp.h > 0)) return;   /* no viewport (detached/headless) */
  var d = fsDisplayedSize();
  /* The flex container keeps the image centred, so on each axis it may only
     travel half of the overflow.  Clamping both ends makes the image behave
     like a sheet *inside* a box: its edges never pull away from the viewport,
     so panning can never expose black space. */
  var maxX = Math.max(0, (d.w - vp.w) / 2);
  var maxY = Math.max(0, (d.h - vp.h) / 2);
  if (state.fs.tx > maxX) state.fs.tx = maxX; else if (state.fs.tx < -maxX) state.fs.tx = -maxX;
  if (state.fs.ty > maxY) state.fs.ty = maxY; else if (state.fs.ty < -maxY) state.fs.ty = -maxY;
}

function fsApply() {
  fsClamp();
  fsImg.style.transform = 'translate(' + state.fs.tx + 'px,' + state.fs.ty + 'px) scale(' + state.fs.scale + ')';
}

/* Size the image box to its native pixels (100%).  The zoom is baked into the
   transform scale, so the element's aspect ratio always equals the image's
   (object-fit:contain then fills the box exactly - no stretch/distortion). */
function fsSizeToNatural() {
  var w = state.fs.natW || 0, h = state.fs.natH || 0;
  if (w > 0 && h > 0) { fsImg.style.width = w + 'px'; fsImg.style.height = h + 'px'; }
}

/* A single left click toggles ACTUAL SIZE <-> FIT WIDTH; each mode resets the
   pan so the new fit always starts centred. */
function fsSetMode(mode) {
  state.fs.mode = mode;
  var vp = fsViewport();
  if (mode === 'fitwidth' && (state.fs.natW || 0) > 0 && vp.w > 0) { state.fs.scale = vp.w / state.fs.natW; }
  else { state.fs.scale = 1; }
  state.fs.tx = 0; state.fs.ty = 0;
  fsApply();
}

function fsToggleMode() { fsSetMode(state.fs.mode === 'fitwidth' ? 'actual' : 'fitwidth'); }

fsImg.addEventListener('load', function () {
  if (!fsActive()) return;
  state.fs.natW = fsImg.naturalWidth || 0;
  state.fs.natH = fsImg.naturalHeight || 0;
  fsSizeToNatural();
  if (state.fs.mode === 'fitwidth') fsSetMode('fitwidth'); else fsApply();
});

function openFullScreen(i) {
  if (i == null || !state.images[i]) return;
  var item = state.images[i];
  if (item.encrypted && !state.crypto.unlocked) {
    toast('Crypto session terkunci. Unlock dulu (\ud83d\udd10).');
    return;
  }
  /* OPEN always starts at ACTUAL SIZE (100%), centred, and clears any zoom/pan
     from the previous image so it can never bleed into the next one. */
  state.fs.index = i;
  state.fs.mode = 'actual';
  state.fs.scale = 1;
  state.fs.tx = 0;
  state.fs.ty = 0;
  state.fs.natW = 0;
  state.fs.natH = 0;
  state.fs.dragging = false;
  state.fs.moved = false;
  state.fs.suppressClick = false;
  state.selected = i;
  state.selection = [i];
  state.anchor = i;
  applySelectionClasses();
  updateStatus();
  updateStatusFile();
  fsImg.style.width = '';
  fsImg.style.height = '';
  fsImg.src = imgUrl(item.path);   /* .aimg streams decrypted bytes from CryptoSession (in memory) */
  fsApply();
  fsView.classList.add('show');
  document.body.classList.add('fs-active');
  /* Enter TRUE native fullscreen via the pywebview bridge: the window frame /
     title bar, app chrome and the Windows taskbar are covered. */
  if (window.__aether_fs && window.__aether_fs.enter) {
    try { window.__aether_fs.enter(); } catch (err) {}
  }
}

function fsActive() { return fsView.classList.contains('show'); }

function closeFullScreen() {
  /* Leave native fullscreen via the pywebview bridge, then restore the chrome. */
  if (window.__aether_fs && window.__aether_fs.leave) {
    try { window.__aether_fs.leave(); } catch (err) {}
  }
  fsView.classList.remove('show');
  document.body.classList.remove('fs-active');
  fsImg.removeAttribute('src');
  fsImg.style.width = '';
  fsImg.style.height = '';
  state.fs.index = null;
  state.fs.mode = 'actual';
  state.fs.scale = 1;
  state.fs.tx = 0;
  state.fs.ty = 0;
  state.fs.dragging = false;
  state.fs.moved = false;
  state.fs.suppressClick = false;
}

function fsStep(delta) {
  if (state.fs.index == null) return;
  var n = state.fs.index + delta;
  if (n < 0 || n >= state.images.length) return;
  openFullScreen(n);
}

function fsZoom(factor) {
  var old = state.fs.scale || 1;
  var next = Math.max(0.1, Math.min(32, old * factor));
  var k = next / old;
  state.fs.scale = next;
  /* Zoom about the viewport centre (the point beneath it stays put), then clamp. */
  state.fs.tx = state.fs.tx * k;
  state.fs.ty = state.fs.ty * k;
  fsApply();
}

/* Single LEFT click on the image: toggle Actual Size <-> Fit Width.
   Double click on the image: leave View Image (back to the grid).  A pan drag
   sets suppressClick so releasing the mouse after dragging never toggles. */
fsImg.addEventListener('click', function (e) {
  if (!fsActive() || e.button !== 0) return;
  if (state.fs.suppressClick) { state.fs.suppressClick = false; return; }
  fsToggleMode();
});

fsImg.addEventListener('dblclick', function (e) {
  if (!fsActive()) return;
  e.preventDefault();
  closeFullScreen();
});

fsImg.addEventListener('mousedown', function (e) {
  if (!fsActive()) return;
  if (e.button != null && e.button !== 0) return;   /* left button only */
  e.preventDefault();
  if (e.stopPropagation) e.stopPropagation();
  state.fs.dragging = true;
  state.fs.moved = false;
  state.fs.startX = e.clientX;
  state.fs.startY = e.clientY;
  state.fs.origX = state.fs.tx;
  state.fs.origY = state.fs.ty;
});

document.addEventListener('mousemove', function (e) {
  if (!state.fs.dragging) return;
  var dx = e.clientX - state.fs.startX, dy = e.clientY - state.fs.startY;
  if (Math.abs(dx) > 3 || Math.abs(dy) > 3) state.fs.moved = true;
  state.fs.tx = state.fs.origX + dx;
  state.fs.ty = state.fs.origY + dy;
  fsApply();
});

document.addEventListener('mouseup', function () {
  if (!state.fs.dragging) return;
  if (state.fs.moved) state.fs.suppressClick = true;
  state.fs.dragging = false;
});

/* Mouse wheel scrolls through the ACTIVE ordered dataset (Normal/Group filter
   -> Sort By -> ordered list): the very same order the Image Grid shows.
   Wheel DOWN moves forward (next item = higher index) and wheel UP moves
   backward (previous item = lower index); the direction never flips with the
   sort kind - it simply walks the current ordered list. */
fsView.addEventListener('wheel', function (e) {
  if (!fsActive()) return;
  e.preventDefault();
  if (e.deltaY > 0) fsStep(1); else fsStep(-1);
}, { passive: false });

/* Keep the clamped geometry correct when the viewport changes (e.g. the native
   window toggling fullscreen).  Guarded so a headless DOM stub has no window. */
if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
  window.addEventListener('resize', function () {
    if (!fsActive()) return;
    if (state.fs.mode === 'fitwidth') fsSetMode('fitwidth'); else fsApply();
  });
}
