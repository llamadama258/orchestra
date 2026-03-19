/* ═══════════════════════════════════════════════════
   Melodra — Loading Screen JS (loading.js)
   Modern Maestro: Skeleton Screen
   ═══════════════════════════════════════════════════ */

(function () {

  const STATUSES = [
    { at: 0,   text: 'Reading file\u2026'     },
    { at: 18,  text: 'Parsing score\u2026'    },
    { at: 35,  text: 'Mapping notes\u2026'    },
    { at: 55,  text: 'Generating audio\u2026' },
    { at: 78,  text: 'Finalising\u2026'       },
    { at: 95,  text: 'Almost ready\u2026'     },
    { at: 100, text: 'Done!'                   },
  ];

  let externalPct = null;
  let animId      = null;

  function getEl(id) { return document.getElementById(id); }

  function applyProgress(totalP) {
    const pct      = Math.round(totalP * 100);
    const fillEl   = getEl('ls-fill');
    const pctEl    = getEl('ls-pct');
    const statusEl = getEl('ls-status');

    if (fillEl) {
      fillEl.style.width = pct + '%';
    }
    if (pctEl) pctEl.textContent = pct + '%';
    if (statusEl) {
      for (let i = STATUSES.length - 1; i >= 0; i--) {
        if (pct >= STATUSES[i].at) { statusEl.textContent = STATUSES[i].text; break; }
      }
    }
  }

  function startAnimation() {
    function tick() {
      const totalP = externalPct !== null ? externalPct / 100 : 0;
      applyProgress(totalP);
      animId = requestAnimationFrame(tick);
    }
    animId = requestAnimationFrame(tick);
  }

  function finishAnimation() {
    const fillEl = getEl('ls-fill');
    const pctEl  = getEl('ls-pct');
    if (fillEl) fillEl.style.width = '100%';
    if (pctEl)  pctEl.classList.add('ls-done');
  }

  /* ── Public API ──────────────────────────────────── */

  window.showLoadingScreen = function () {
    const screen = getEl('screen-loading');
    if (!screen) return;
    screen.classList.remove('hidden');

    // reset play button for this run
    const openBtn = getEl('ls-open-btn');
    if (openBtn) openBtn.classList.add('hidden');

    if (animId) cancelAnimationFrame(animId);
    externalPct = null;

    const fillEl   = getEl('ls-fill');
    const pctEl    = getEl('ls-pct');
    const statusEl = getEl('ls-status');
    if (fillEl)   fillEl.style.width = '0%';
    if (pctEl)    { pctEl.textContent = '0%'; pctEl.classList.remove('ls-done'); }
    if (statusEl) statusEl.textContent = 'Reading file\u2026';

    startAnimation();
  };

  window.updateLoadingProgress = function (pct) {
    externalPct = Math.min(Math.max(pct, 0), 100);
  };

  window.hideLoadingScreen = function () {
    externalPct = null;
    if (animId) cancelAnimationFrame(animId);
    finishAnimation();

    setTimeout(() => {
      const screen = getEl('screen-loading');
      if (screen) screen.classList.add('hidden');
    }, 800);
  };

})();
