/* ═══════════════════════════════════════════════════
   Melodra — Loading Screen JS (loading.js)
   ═══════════════════════════════════════════════════ */

(function () {

  const STATUSES = [
    { at: 0,   text: 'Reading file…'     },
    { at: 18,  text: 'Parsing score…'    },
    { at: 35,  text: 'Mapping notes…'    },
    { at: 55,  text: 'Generating audio…' },
    { at: 78,  text: 'Finalising…'       },
    { at: 95,  text: 'Almost ready…'     },
    { at: 100, text: 'Done!'             },
  ];

  const STAFF_Y  = [18, 32, 46, 60, 74];
  const STAFF_X1 = 28;
  const STAFF_X2 = 452;
  const PITCH_Y  = [4, 11, 18, 25, 32, 39, 46, 53, 60, 67, 74, 81, 88];
  const SCROLL_SPEED = 72;   // px per second
  const NOTE_POOL    = 12;   // notes always on the treadmill
  const CLEF_W       = 26;   // visual width reserved for the clef

  function randInt(a, b) { return Math.floor(a + Math.random() * (b - a + 1)); }
  function ease(t) { return t < 0.5 ? 2*t*t : -1 + (4 - 2*t) * t; }

  let animId      = null;
  let notePool    = [];   // { x, y, type, up, el }  — live positions
  let clefEl      = null;
  let prevTs      = null;
  let externalPct = null;

  function getEl(id) { return document.getElementById(id); }

  function makeLine(x1, y1, x2, y2, color, width, opacity) {
    const el = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    el.setAttribute('x1', x1); el.setAttribute('y1', y1);
    el.setAttribute('x2', x2); el.setAttribute('y2', y2);
    el.setAttribute('stroke', color || '#fff');
    el.setAttribute('stroke-width', width || 1);
    el.setAttribute('opacity', opacity || 1);
    return el;
  }

  // Spawn a fresh note data object at position x
  function spawnNote(x) {
    const pi   = randInt(1, PITCH_Y.length - 2);
    const y    = PITCH_Y[pi];
    const type = Math.random() > 0.4 ? 'quarter' : 'eighth';
    const up   = y > 46;
    return { x, y, type, up };
  }

  // Build one note <g> element at origin (0,0); positioned via transform
  function makeNoteEl(note) {
    const { y, type, up } = note;
    const ox = 0;   // we position via transform translate
    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    g.setAttribute('opacity', '1');

    const head = document.createElementNS('http://www.w3.org/2000/svg', 'ellipse');
    head.setAttribute('cx', ox); head.setAttribute('cy', y);
    head.setAttribute('rx', 6);  head.setAttribute('ry', 4.5);
    head.setAttribute('fill', '#f0ece4');
    head.setAttribute('transform', `rotate(-15,${ox},${y})`);
    g.appendChild(head);

    const sx  = ox + (up ? 5.5 : -5.5);
    const sy1 = up ? y - 3  : y + 3;
    const sy2 = up ? y - 30 : y + 30;
    g.appendChild(makeLine(sx, sy1, sx, sy2, '#f0ece4', 1.2));

    if (type === 'eighth') {
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      const d = up
        ? `M${sx},${sy2} C${sx+16},${sy2+7} ${sx+12},${sy2+18} ${sx},${sy2+20}`
        : `M${sx},${sy2} C${sx+16},${sy2-7} ${sx+12},${sy2-18} ${sx},${sy2-20}`;
      path.setAttribute('d', d);
      path.setAttribute('stroke', '#f0ece4');
      path.setAttribute('stroke-width', '1.3');
      path.setAttribute('fill', 'none');
      g.appendChild(path);
    }

    // ledger lines for extreme pitches
    if (y <= 10) g.appendChild(makeLine(-10, 18, 10, 18, 'rgba(255,255,255,0.4)', 0.8));
    if (y >= 82) g.appendChild(makeLine(-10, 74, 10, 74, 'rgba(255,255,255,0.4)', 0.8));

    return g;
  }

  function buildSVG() {
    const svg = getEl('ls-svg');
    if (!svg) return;
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    // Add a clip path so notes don’t bleed past the right or left edge
    const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
    const clip = document.createElementNS('http://www.w3.org/2000/svg', 'clipPath');
    clip.setAttribute('id', 'ls-clip');
    const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    rect.setAttribute('x', STAFF_X1 + CLEF_W);
    rect.setAttribute('y', '-30');
    rect.setAttribute('width', STAFF_X2 - STAFF_X1 - CLEF_W);
    rect.setAttribute('height', '170');
    clip.appendChild(rect);
    defs.appendChild(clip);
    svg.appendChild(defs);

    // Full-width static staff lines
    STAFF_Y.forEach(y => {
      svg.appendChild(makeLine(STAFF_X1, y, STAFF_X2, y, 'rgba(255,255,255,0.3)', 0.9));
    });

    // Clef at fixed position
    clefEl = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    clefEl.setAttribute('x', 4); clefEl.setAttribute('y', 66);
    clefEl.setAttribute('font-size', '60');
    clefEl.setAttribute('font-family', 'serif');
    clefEl.setAttribute('fill', 'rgba(255,255,255,0.5)');
    clefEl.textContent = '𝄞';
    svg.appendChild(clefEl);

    // Scrolling note group (clipped)
    const scrollGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    scrollGroup.setAttribute('clip-path', 'url(#ls-clip)');
    svg.appendChild(scrollGroup);

    // Spread NOTE_POOL notes evenly across the visible width to start
    const span = STAFF_X2 - STAFF_X1 - CLEF_W;
    const step = span / NOTE_POOL;
    notePool = Array.from({ length: NOTE_POOL }, (_, i) => {
      const note = spawnNote(STAFF_X1 + CLEF_W + i * step + randInt(0, step * 0.6));
      note.el = makeNoteEl(note);
      note.el.setAttribute('transform', `translate(${note.x}, 0)`);
      scrollGroup.appendChild(note.el);
      return note;
    });

    // Keep a reference to the scroll group so we can append recycled notes
    svg._scrollGroup = scrollGroup;
  }

  function applyProgress(totalP) {
    const pct      = Math.round(totalP * 100);
    const fillEl   = getEl('ls-fill');
    const pctEl    = getEl('ls-pct');
    const statusEl = getEl('ls-status');

    if (fillEl) {
      fillEl.style.width = pct + '%';
      if (pct > 2) fillEl.classList.add('ls-active');
    }
    if (pctEl) pctEl.textContent = pct + '%';
    if (statusEl) {
      for (let i = STATUSES.length - 1; i >= 0; i--) {
        if (pct >= STATUSES[i].at) { statusEl.textContent = STATUSES[i].text; break; }
      }
    }
  }

  function startInternalAnimation() {
    prevTs = null;

    function tick(ts) {
      const dt = prevTs === null ? 0 : ts - prevTs;
      prevTs = ts;

      // Scroll all notes left
      const dx = SCROLL_SPEED * dt / 1000;
      const svg = getEl('ls-svg');
      const scrollGroup = svg && svg._scrollGroup;

      if (scrollGroup) {
        notePool.forEach(note => {
          note.x -= dx;

          // Wrap note to the right edge with a new random pitch
          if (note.x < STAFF_X1 + CLEF_W - 20) {
            // Find the rightmost current x to avoid bunching
            const maxX = notePool.reduce((m, n) => Math.max(m, n.x), 0);
            const newNote = spawnNote(maxX + randInt(30, 60));

            // Recycle the DOM element: rebuild its children for new pitch
            while (note.el.firstChild) note.el.removeChild(note.el.firstChild);
            const fresh = makeNoteEl(newNote);
            while (fresh.firstChild) note.el.appendChild(fresh.firstChild);

            note.x    = newNote.x;
            note.y    = newNote.y;
            note.type = newNote.type;
            note.up   = newNote.up;
          }

          note.el.setAttribute('transform', `translate(${note.x}, 0)`);
        });
      }

      // Progress bar
      const totalP = externalPct !== null
        ? externalPct / 100
        : 0;
      applyProgress(totalP);

      animId = requestAnimationFrame(tick);
    }

    animId = requestAnimationFrame(tick);
  }

  function finishAnimation() {
    const fillEl = getEl('ls-fill');
    const pctEl  = getEl('ls-pct');
    if (fillEl) { fillEl.style.width = '100%'; fillEl.classList.remove('ls-active'); }
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
    prevTs = null;

    const fillEl   = getEl('ls-fill');
    const pctEl    = getEl('ls-pct');
    const statusEl = getEl('ls-status');
    if (fillEl)   { fillEl.style.width = '0%'; fillEl.classList.remove('ls-active'); }
    if (pctEl)    { pctEl.textContent = '0%'; pctEl.classList.remove('ls-done'); }
    if (statusEl) statusEl.textContent = 'Reading file…';

    buildSVG();
    startInternalAnimation();
  };

  window.updateLoadingProgress = function (pct) {
    // pct is 0–100 from the server; store as-is
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
