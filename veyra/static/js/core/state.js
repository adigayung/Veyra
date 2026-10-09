/* ==========================================================================
   Veyra — core/state.js
   Application state + cached DOM references.

   Loaded first: every other module reads `state` / `els` from this shared
   global scope.  Nothing here performs I/O or touches the DOM beyond
   resolving the element references the shell (index.html) provides.
   ========================================================================== */

/* Cached element handles. `menu` is the only element resolved by class; all
   others use the ids declared in the shell so a missing node fails loudly
   during development instead of silently at interaction time. */
var els = {
  menu: document.querySelector('.menu'),
  pathInput: document.getElementById('pathInput'),
  grid: document.getElementById('grid'),
  sortBy: document.getElementById('sortBy'),
  groupFilter: document.getElementById('groupFilter'),
  tree: document.getElementById('tree'),
  viewCount: document.getElementById('viewCount'),
  statusFile: document.getElementById('statusFile'),
  statusFolders: document.getElementById('statusFolders'),
  statusFiles: document.getElementById('statusFiles'),
  selectedStatus: document.getElementById('selectedStatus'),
  toast: document.getElementById('toast'),
  navBack: document.getElementById('navBack'),
  navForward: document.getElementById('navForward'),
  navUp: document.getElementById('navUp'),
  refreshBtn: document.getElementById('refreshBtn')
};

/* Single source of truth for the UI.
   - rawImages   : the untouched /api/browse listing of the current folder
   - groupImages : the members of the active group (may span folders/drives)
   - images      : the ACTIVE ordered dataset the grid + viewer consume
   - group       : null = Normal (explorer listing), otherwise a group id
   - selection   : sorted indices into `images`
   - crypto      : centralized session state (one password for everything)
   - clipboard   : Veyra-internal file clipboard (pure JS state)
   - fs          : Image View geometry (mode / scale / pan / drag) */
var state = {
  path: null,
  images: [],
  rawImages: [],
  group: null,
  groupImages: [],
  sort: 'name_asc',
  dirs: [],
  selected: null,
  selection: [],
  anchor: null,
  treeRoot: null,
  history: [],
  hIndex: -1,
  crypto: { unlocked: false, initialized: false, boundary: '' },
  clipboard: null,
  fs: {
    index: null,
    mode: 'actual',
    scale: 1,
    tx: 0,
    ty: 0,
    natW: 0,
    natH: 0,
    dragging: false,
    moved: false,
    suppressClick: false,
    startX: 0,
    startY: 0,
    origX: 0,
    origY: 0
  }
};

/* Groups panel state (list + currently opened group detail). */
var groupsState = { list: [], selected: null, detail: null };

/* Virtualized grid constants + layout bookkeeping. */
var GRID_MIN = 142;
var GRID_GAP = 8;
/* Only a window of GRID_WINDOW_RADIUS items around the viewport centre is ever
   mounted; the DOM is refreshed GRID_SCROLL_DEBOUNCE ms after scrolling stops. */
var GRID_WINDOW_RADIUS = 30;
var GRID_SCROLL_DEBOUNCE = 250;
var gridLayout = {
  rowH: 170,
  calibrated: false,
  winStart: -1,
  winEnd: -1,
  scrollTimer: null
};

/* Explorer "This PC" pseudo path. */
var PC_PATH = '::pc';

/* -- small helpers --------------------------------------------------------- */
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}

function fmtSize(bytes) {
  if (bytes == null || isNaN(bytes)) return '';
  var u = ['B', 'KB', 'MB', 'GB'], i = 0, n = Number(bytes);
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i === 0 ? n : (n < 10 ? n.toFixed(1) : Math.round(n))) + ' ' + u[i];
}

function imgUrl(p) { return '/api/image?path=' + encodeURIComponent(p); }
function thumbUrl(p) { return '/api/thumb?path=' + encodeURIComponent(p); }

function toast(msg) {
  var t = els.toast;
  if (!t) return;
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toast._t);
  toast._t = setTimeout(function () { t.classList.remove('show'); }, 1600);
}

/* Parent folder of an absolute path, or null at a drive root. */
function parentPath(p) {
  if (!p) return null;
  var s = p.replace(/[\\\/]+$/, '');
  var i = Math.max(s.lastIndexOf('\\'), s.lastIndexOf('/'));
  if (i <= 0) return null;
  var par = s.slice(0, i);
  if (/^[A-Za-z]:$/.test(par)) return par + '\\';
  return par;
}
