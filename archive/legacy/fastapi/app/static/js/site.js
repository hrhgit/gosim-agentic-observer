(function () {
  // reveal on scroll
  var els = document.querySelectorAll('.reveal');
  if ('IntersectionObserver' in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add('visible'); io.unobserve(e.target); } });
    }, { threshold: 0.1, rootMargin: '0px 0px -50px 0px' });
    els.forEach(function (el) { io.observe(el); });
  } else { els.forEach(function (el) { el.classList.add('visible'); }); }
  // safety net: never leave content hidden (printing, screenshots, observers that never fire)
  setTimeout(function () { els.forEach(function (el) { el.classList.add('visible'); }); }, 2500);

  // mobile nav
  var burger = document.querySelector('[data-burger]');
  var mobile = document.querySelector('[data-mobile-nav]');
  if (burger && mobile) burger.addEventListener('click', function () { mobile.classList.toggle('open'); });

  // copy buttons
  document.querySelectorAll('[data-copy]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var text = btn.getAttribute('data-copy');
      if (navigator.clipboard) navigator.clipboard.writeText(text).then(function () {
        var old = btn.textContent; btn.textContent = btn.getAttribute('data-copied') || 'Copied'; setTimeout(function () { btn.textContent = old; }, 1500);
      });
    });
  });

  // confirm dialogs
  document.querySelectorAll('form[data-confirm]').forEach(function (f) {
    f.addEventListener('submit', function (ev) { if (!window.confirm(f.getAttribute('data-confirm'))) ev.preventDefault(); });
  });

  // auto refresh while a submission is pending
  var pending = document.querySelector('[data-poll-status]');
  if (pending) {
    var url = pending.getAttribute('data-poll-status');
    var tick = function () {
      fetch(url, { headers: { Accept: 'application/json' }, credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (d) {
        if (d.status && d.status !== 'queued' && d.status !== 'running') window.location.reload(); else setTimeout(tick, 3000);
      }).catch(function () { setTimeout(tick, 5000); });
    };
    setTimeout(tick, 3000);
  }

  // live leaderboard refresh
  var board = document.querySelector('[data-leaderboard]');
  if (board) {
    var api = board.getAttribute('data-leaderboard');
    var stamp = document.querySelector('[data-board-updated]');
    setInterval(function () {
      fetch(api, { headers: { Accept: 'application/json' }, credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (d) {
        if (!d.visible) return;
        var tbody = board.querySelector('tbody'); if (!tbody) return;
        var me = board.getAttribute('data-my-team');
        var rows = d.entries.map(function (e) {
          return '<tr' + (me && String(e.team_id) === me ? ' class="me"' : '') + '><td class="m accent">' + e.rank + '</td><td>' + esc(e.team_name) + '</td><td class="r m">' + fmt(e.total_score) + '</td><td class="r m">' + fmt(e.science_score) + '</td><td class="r m">' + pct(e.completion_rate) + '</td><td class="r m">' + fmt(e.uniformity_score, 3) + '</td><td class="r m">' + e.submission_count + '</td></tr>';
        }).join('');
        if (rows) tbody.innerHTML = rows;
        if (stamp && d.updated_at) stamp.textContent = new Date(d.updated_at).toLocaleTimeString();
      }).catch(function () {});
    }, 60000);
  }
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function fmt(v, d) { return v == null ? '—' : Number(v).toFixed(d == null ? 2 : d); }
  function pct(v) { return v == null ? '—' : (Number(v) * 100).toFixed(1) + '%'; }

  // submit form: toggle scenario select by kind
  var kind = document.querySelector('[data-kind]');
  if (kind) {
    var update = function () {
      var k = document.querySelector('input[name=kind]:checked'); var v = k ? k.value : 'results';
      document.querySelectorAll('[data-show-kind]').forEach(function (el) { el.style.display = el.getAttribute('data-show-kind') === v ? '' : 'none'; });
      var file = document.querySelector('input[name=file]'); if (file) file.setAttribute('accept', v === 'results' ? '.csv' : '.py,.zip');
    };
    document.querySelectorAll('input[name=kind]').forEach(function (r) { r.addEventListener('change', update); });
    var phaseSel = document.querySelector('select[name=phase]');
    if (phaseSel) {
      var updPhase = function () {
        var slug = phaseSel.value;
        document.querySelectorAll('[data-phase-scenarios]').forEach(function (el) { el.style.display = el.getAttribute('data-phase-scenarios') === slug ? '' : 'none'; });
        document.querySelectorAll('input[name=kind]').forEach(function (r) {
          var opt = phaseSel.options[phaseSel.selectedIndex]; var allowed = (opt.getAttribute('data-allow-' + r.value) === '1');
          r.disabled = !allowed; r.parentElement.style.opacity = allowed ? '1' : '.4';
          if (!allowed && r.checked) { var other = document.querySelector('input[name=kind]:not([disabled])'); if (other) other.checked = true; }
        });
        update();
      };
      phaseSel.addEventListener('change', updPhase); updPhase();
    } else update();
  }
})();
