// Stav služeb: stáří údajů, automatické obnovení při nových datech, detail dne v historii.
(function () {
  var gen = Number(document.body.getAttribute('data-generated')) || 0;
  var STALE = 30 * 60 * 1000;
  var stale = document.querySelector('.stale');
  var time = document.querySelector('.js-updated time');
  var abs = time ? time.textContent : '';

  function ago(ms) {
    var m = Math.round(ms / 60000);
    if (m < 1) return 'před chvílí';
    if (m < 60) return 'před ' + m + ' min';
    var h = Math.floor(m / 60);
    return 'před ' + h + ' h' + (m % 60 ? ' ' + (m % 60) + ' min' : '');
  }
  function tick() {
    if (!gen) return;
    var age = Math.max(0, Date.now() - gen);
    if (stale) stale.hidden = age <= STALE;
    if (time) time.textContent = abs + ' (' + ago(age) + ')';
  }
  tick();
  setInterval(tick, 30000);

  // Nová data: každé 2 minuty (jen když je stránka vidět) se podíváme do status.json.
  function check() {
    if (document.hidden || !window.fetch) return;
    fetch('status.json?t=' + Date.now(), { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) { if (j && Date.parse(j.generated) > gen + 1000) location.reload(); })
      .catch(function () {});
  }
  if (location.protocol !== 'file:' && document.querySelector('.svcs')) {
    setInterval(check, 120000);
    document.addEventListener('visibilitychange', function () { if (!document.hidden) check(); });
  }

  // Detail dne: najetí myší, klávesnice nebo klepnutí. Na dotyku první klepnutí ukáže detail, druhé otevře událost.
  // Den s incidentem nebo údržbou má v detailu odkaz „Podrobnosti“; na detail jde myší najet a kliknout.
  var tip = document.createElement('div');
  tip.className = 'tip';
  tip.hidden = true;
  tip.setAttribute('role', 'tooltip');
  document.body.appendChild(tip);
  var cur = null, touchShown = null, lastPointer = 'mouse', hideTimer = null;

  function show(el) {
    tip.textContent = '';
    (el.getAttribute('data-tip') || '').split('\n').forEach(function (line, i) {
      if (i) tip.appendChild(document.createElement('br'));
      var s = document.createElement(i ? 'span' : 'b');
      s.textContent = line;
      tip.appendChild(s);
    });
    var href = el.getAttribute('href');
    if (href) {
      var a = document.createElement('a');
      a.className = 'tip-link';
      a.href = href;
      a.textContent = lastPointer === 'mouse' ? 'Kliknutím na den zobrazíte podrobnosti →' : 'Podrobnosti →';
      tip.appendChild(a);
    }
    clearTimeout(hideTimer);
    tip.hidden = false;
    var r = el.getBoundingClientRect(), tw = tip.offsetWidth, th = tip.offsetHeight;
    tip.style.left = Math.min(Math.max(8, r.left + r.width / 2 - tw / 2), window.innerWidth - tw - 8) + 'px';
    var top = r.top - th - 10;
    tip.style.top = (top < 8 ? r.bottom + 10 : top) + 'px';
    if (cur) cur.classList.remove('on');
    cur = el;
    el.classList.add('on');
  }
  function hide() {
    tip.hidden = true;
    if (cur) cur.classList.remove('on');
    cur = null;
    touchShown = null;
  }

  document.querySelectorAll('.bars').forEach(function (b) {
    b.querySelectorAll('[title]').forEach(function (el) { el.removeAttribute('title'); });
    b.addEventListener('pointerdown', function (e) { lastPointer = e.pointerType; });
    b.addEventListener('pointerover', function (e) {
      var d = e.target.closest('.d');
      if (d && e.pointerType === 'mouse') show(d);
    });
    // krátká prodleva, aby šlo myší přejet z dne na detail a kliknout na odkaz
    b.addEventListener('pointerleave', function (e) { if (e.pointerType === 'mouse') { clearTimeout(hideTimer); hideTimer = setTimeout(hide, 350); } });
    b.addEventListener('click', function (e) {
      var d = e.target.closest('.d');
      if (!d) return;
      if (lastPointer !== 'mouse' && touchShown !== d) {
        e.preventDefault();
        show(d);
        touchShown = d;
      }
    });
  });
  tip.addEventListener('pointerenter', function () { clearTimeout(hideTimer); });
  tip.addEventListener('pointerleave', function (e) { if (e.pointerType === 'mouse') { clearTimeout(hideTimer); hideTimer = setTimeout(hide, 200); } });
  document.addEventListener('pointerdown', function (e) { if (!e.target.closest('.bars') && !e.target.closest('.tip')) hide(); });
  window.addEventListener('scroll', hide, { passive: true });
  window.addEventListener('resize', hide);
})();
