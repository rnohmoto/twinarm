/* app.js — wires the sim engine to the cockpit DOM: wings, annunciator
 * lamps, cameras, startup checklist, event log, REC block, guarded master
 * stop, and the mock-only SIM panel. Loads last; everything it needs is on
 * window.COCKPIT. */
(function () {
  'use strict';
  const NS = window.COCKPIT;
  const sim = NS.sim;
  const spec = sim.spec;

  const LOG_CAP = 200;
  const LIVE_REGION_MIN_GAP_S = 2;
  const REARM_MS = 3000;

  const $ = (sel) => document.querySelector(sel);

  function fmtClock(seconds) {
    const mm = String(Math.floor(seconds / 60)).padStart(2, '0');
    const ss = String(Math.floor(seconds % 60)).padStart(2, '0');
    return `T+${mm}:${ss}`;
  }

  /* --- static refs --- */
  const clockEl = $('#session-clock');
  const recBlock = $('#rec-block');
  const recElapsed = recBlock.querySelector('[data-role="rec-elapsed"]');
  const logEl = $('#event-log');
  const liveRegion = $('#live-region');
  const cams = {
    left: $('.cam[data-arm="left"]'),
    right: $('.cam[data-arm="right"]'),
  };
  const stopWrap = $('#master-stop');
  const stopCover = $('#stop-cover');
  const stopFire = $('#stop-fire');
  const pauseBtn = $('[data-role="sim-pause"]');
  const recBtn = $('[data-role="sim-rec"]');
  const scenarioRadios = Array.from(document.querySelectorAll('input[name="scenario"]'));

  const lamps = {};
  document.querySelectorAll('.lamp').forEach((el) => {
    lamps[el.dataset.lamp] = { el, stateEl: el.querySelector('[data-role="state"]'), current: 'off' };
  });

  /* --- wings --- */
  const wings = {
    left: NS.wing.build($('#wing-left'), 'left', { spec, onCommand: (cmd) => sim.command('left', cmd) }),
    right: NS.wing.build($('#wing-right'), 'right', { spec, onCommand: (cmd) => sim.command('right', cmd) }),
  };

  /* --- startup checklist --- */
  function buildChecklist(ol) {
    return spec.CHECK_STEPS.map((label) => {
      const li = document.createElement('li');
      li.dataset.state = 'pending';
      li.innerHTML =
        '<span class="check-icon"></span>' +
        `<span class="check-label">${label}</span>` +
        '<span class="check-detail"></span>';
      ol.appendChild(li);
      return { li, detail: li.querySelector('.check-detail') };
    });
  }
  const checkRefs = {
    left: buildChecklist($('.checklist[data-arm="left"]')),
    right: buildChecklist($('.checklist[data-arm="right"]')),
  };

  function updateChecklist(refs, arm) {
    arm.checklist.forEach((step, i) => {
      const ref = refs[i];
      if (ref.li.dataset.state !== step.state) ref.li.dataset.state = step.state;
      if (ref.detail.textContent !== step.detail) ref.detail.textContent = step.detail;
    });
  }

  /* --- annunciator --- */
  function setLamp(name, state) {
    const lamp = lamps[name];
    if (!lamp || lamp.current === state) return;
    lamp.current = state;
    lamp.el.dataset.state = state;
    lamp.stateEl.textContent = `: ${state}`;
  }

  function commState(link) {
    if (link === 'live') return 'nominal';
    if (link === 'drop') return 'alert';
    return 'off';
  }

  function heatState(arm) {
    if (arm.link === 'none') return 'off';
    if (arm.temp >= spec.TEMP_STOP_GUARD) return 'alert';
    if (arm.temp >= spec.TEMP_GAIN_GUARD) return 'caution';
    return 'off';
  }

  function trackState(frame) {
    if (!frame) return 'off';
    const worst = spec.JOINTS.reduce((max, name) => {
      const delta = Math.abs((frame.pos[name] ?? 0) - (frame.fpos[name] ?? 0));
      return Math.max(max, delta);
    }, 0);
    return worst > spec.TRACK_WARN_NORM ? 'caution' : 'off';
  }

  function updateLamps(state, frames) {
    setLamp('l-comm', commState(state.arms.left.link));
    setLamp('r-comm', commState(state.arms.right.link));
    setLamp('l-heat', heatState(state.arms.left));
    setLamp('r-heat', heatState(state.arms.right));
    setLamp('l-track', trackState(frames.left));
    setLamp('r-track', trackState(frames.right));
    setLamp('load', state.loadFlashUntil > state.t ? 'alert' : 'off');
    /* volt / enc / shock: wired to Hardware_Error_Status bits; no mock
     * scenario raises them, so they stay dark fixtures. */
  }

  /* --- event log + live region --- */
  function appendLog(events, t) {
    events.forEach((ev) => {
      const li = document.createElement('li');
      li.dataset.sev = ev.sev;
      const time = document.createElement('span');
      time.className = 'log-time';
      time.textContent = fmtClock(t);
      const msg = document.createElement('span');
      msg.className = 'log-msg';
      msg.textContent = (ev.arm && ev.arm !== '·' ? `${ev.arm} · ` : '') + ev.msg;
      li.append(time, msg);
      logEl.prepend(li);
    });
    while (logEl.children.length > LOG_CAP) logEl.lastElementChild.remove();
  }

  let lastAnnounceT = -Infinity;
  function announceImportant(events, t) {
    const important = events.filter((ev) => ev.sev !== 'info');
    if (!important.length || t - lastAnnounceT < LIVE_REGION_MIN_GAP_S) return;
    lastAnnounceT = t;
    liveRegion.textContent = important[important.length - 1].msg;
  }

  /* --- cameras / rec --- */
  function updateCams(state) {
    ['left', 'right'].forEach((armId) => {
      const live = state.arms[armId].link === 'live';
      cams[armId].dataset.live = String(live);
      cams[armId].querySelector('[data-role="cam-tag"]').textContent = live ? 'SIM FEED · 15 fps' : '—';
    });
  }

  function updateRec(state) {
    recBlock.dataset.on = String(state.rec.on);
    recElapsed.textContent = state.rec.on ? fmtClock(state.rec.elapsed) : 'OFF';
    recBtn.setAttribute('aria-pressed', String(state.rec.on));
  }

  /* --- guarded master stop --- */
  let rearmTimer = 0;

  function rearmStop() {
    if (stopFire.dataset.fired === 'true') return;
    stopWrap.dataset.armed = 'true';
    stopCover.setAttribute('aria-expanded', 'false');
    if (document.activeElement === stopFire) stopCover.focus();
    stopFire.disabled = true;
  }

  function resetStopUi() {
    clearTimeout(rearmTimer);
    stopFire.dataset.fired = 'false';
    rearmStop();
  }

  function showFiredStopUi() {
    clearTimeout(rearmTimer);
    stopWrap.dataset.armed = 'false';
    stopCover.setAttribute('aria-expanded', 'true');
    stopFire.dataset.fired = 'true';
    stopFire.disabled = true;
  }

  stopCover.addEventListener('click', () => {
    stopWrap.dataset.armed = 'false';
    stopCover.setAttribute('aria-expanded', 'true');
    stopFire.disabled = false;
    stopFire.focus();
    clearTimeout(rearmTimer);
    rearmTimer = setTimeout(rearmStop, REARM_MS);
  });

  stopFire.addEventListener('click', () => {
    clearTimeout(rearmTimer);
    stopFire.dataset.fired = 'true';
    stopFire.disabled = true;
    sim.masterStop();
    const estopRadio = scenarioRadios.find((radio) => radio.value === 'estop');
    if (estopRadio) estopRadio.checked = true;
  });

  /* --- SIM panel --- */
  scenarioRadios.forEach((radio) => {
    radio.addEventListener('change', () => {
      if (!radio.checked) return;
      if (radio.value === 'estop') {
        showFiredStopUi(); /* keep frozen traces: no chart clear */
      } else {
        wings.left.reset();
        wings.right.reset();
        resetStopUi();
      }
      sim.setScenario(radio.value);
    });
  });

  pauseBtn.addEventListener('click', () => {
    const on = pauseBtn.getAttribute('aria-pressed') !== 'true';
    pauseBtn.setAttribute('aria-pressed', String(on));
    sim.setPaused(on);
  });

  recBtn.addEventListener('click', () => {
    sim.setRec(recBlock.dataset.on !== 'true');
  });

  /* --- main loop --- */
  sim.subscribe((state, frames, events) => {
    clockEl.textContent = fmtClock(state.t);
    wings.left.update(state.arms.left, frames.left);
    wings.right.update(state.arms.right, frames.right);
    updateLamps(state, frames);
    updateCams(state);
    updateChecklist(checkRefs.left, state.arms.left);
    updateChecklist(checkRefs.right, state.arms.right);
    updateRec(state);
    if (events.length) {
      appendLog(events, state.t);
      announceImportant(events, state.t);
    }
  });

  sim.start();
})();
