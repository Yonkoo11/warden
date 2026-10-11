// Rule screen: fill the holdings and limits cards from the JSON API. Loading text appears after 400 ms (brief 5a).
(function () {
  var holdings = document.getElementById('holdings');
  if (!holdings) return;
  var address = holdings.getAttribute('data-address');
  var list = document.getElementById('holdings-list');
  var loading = document.getElementById('holdings-loading');
  var limits = document.getElementById('limits-line');
  var t = setTimeout(function () { loading.hidden = false; }, 400);
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'})[c]; }); }
  function fmtMs(ms) { if (!ms) return ''; var d = new Date(Number(ms)); return d.toISOString().slice(0, 16).replace('T', ' '); }
  function render(data) {
    clearTimeout(t); loading.hidden = true;
    if (!data.holdings || !data.holdings.length) {
      list.innerHTML = '<p class="empty">This wallet holds no stock token that reports in the next 14 days.</p>'; return;
    }
    list.innerHTML = data.holdings.map(function (h) {
      if (h.error) return '<article class="card holding"><p class="error">' + esc(h.symbol) + ': ' + esc(h.error) + '</p></article>';
      var p = h.print, s = h.status || {};
      var local = p.time === 'after-hours' ? 'after US close' : p.time === 'pre-market' ? 'before US open' : 'time unknown';
      var statusLine = h.status_error ? '<p class="error">This token has no status data from Binance. Rules cannot be armed for it.</p>'
        : '<p class="status">' + esc(s.reasonCode) + ' · ' + esc(s.marketStatus || '') + (s.nextOpenTime ? ' · next open ' + fmtMs(s.nextOpenTime) + ' UTC' : '') + '</p>';
      var gap = (s.per_share && s.stock_price) ? '<p class="gap">Token ' + esc(s.per_share) + ' per share · last exchange print ' + esc(s.stock_price) + ' · exchange ' + esc(s.exchange_session || '') + ' · ' + esc(s.gap_pct) + '%</p>'
        : (s.per_share ? '<p class="gap">Token ' + esc(s.per_share) + ' per share · exchange print not provided for this token</p>' : '');
      return '<article class="card holding"><h3>' + esc(h.symbol) + ' · ' + esc(p.company) + '</h3>' +
        '<p>Reports ' + esc(p.date) + ' ' + esc(local) + ' · source: ' + esc(p.time_source) + ' · ' + (p.confirmed ? 'confirmed' : 'time unconfirmed') + '</p>' +
        (p.confirmed ? '' : '<p class="muted">Confirm pre-market or after-hours from the company\'s investor page before arming.</p>') +
        statusLine + gap + '<p class="muted num">Held ' + esc(h.held) + '</p></article>';
    }).join('');
  }
  function fail(msg) { clearTimeout(t); loading.hidden = true; list.innerHTML = '<p class="error">' + esc(msg) + '</p>'; }
  fetch('/api/rule-data?address=' + encodeURIComponent(address)).then(function (r) {
    if (r.status === 429) { fail('Binance did not answer (HTTP 429). Retrying in 15 s.'); setTimeout(load, 15000); return null; }
    return r.json();
  }).then(function (d) { if (d) { if (d.error) fail(d.error); else render(d); } }).catch(function (e) { fail('Could not reach the server: ' + e); });
  function load() { location.reload(); }
  fetch('/api/wallet').then(function (r) { return r.json(); }).then(function (w) {
    if (w.status !== 'CONNECTED') { limits.textContent = 'Wallet session: ' + (w.status || 'unknown') + ' · re-pair the Binance Wallet App'; limits.className = 'error'; return; }
    limits.textContent = 'Warden wallet limits: daily limit ' + w.dailyLimit + ' USD · abnormal transactions: ' + w.abnormalTxnHandling + ' · session expires ' + (w.sessionExpireTime || '?');
    limits.className = 'num';
  }).catch(function () { limits.textContent = 'Wallet limits unavailable right now.'; limits.className = 'error'; });
})();
