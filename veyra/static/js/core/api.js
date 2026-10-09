/* ==========================================================================
   Veyra — core/api.js
   Thin JSON transport layer.  Every backend call goes through these two
   helpers so the request/response shape (and the error handling contract)
   stays identical across modules.
   ========================================================================== */

/* GET JSON -> { status, data }. */
function getJSON(url, opts) {
  return fetch(url, opts).then(function (r) {
    return r.json().then(function (d) { return { status: r.status, data: d }; });
  });
}

/* POST JSON -> { status, data }. */
function postJSON(url, body) {
  return getJSON(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
    body: JSON.stringify(body || {})
  });
}
