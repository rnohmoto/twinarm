/* sim.js — fake telemetry engine for the TwinArm cockpit mock.
 *
 * Emits TelemetryFrame-shaped data ({t, pos, fpos, cur, ff, mode, temp, n,
 * rec, params}) per arm at 15 Hz, driven by a fixed-step sim clock so PAUSE
 * freezes everything, and by named scenarios that exercise every visual
 * state of the cockpit. Nothing here talks to hardware.
 */
(function () {
  'use strict';
  const NS = (window.COCKPIT = window.COCKPIT || {});

  const TAU = Math.PI * 2;
  const TICK_MS = 66; /* ~15 Hz, matching the real panel's SSE cadence */
  const TICK_S = TICK_MS / 1000;

  const JOINTS = [
    'shoulder_pan',
    'shoulder_lift',
    'elbow_flex',
    'wrist_flex',
    'wrist_roll',
    'gripper',
  ];
  const SHOULDERS = ['shoulder_pan', 'shoulder_lift'];

  /* Verbatim from twinarm-web-ui/src/shared/api/commands.ts */
  const PARAM_SPECS = [
    { key: 'ff_gain', label: 'Grip gain', min: 0, max: 2.5, step: 0.1 },
    { key: 'ff_cap', label: 'Grip cap (mA)', min: 60, max: 900, step: 10 },
    { key: 'ff_floor', label: 'Return spring (mA)', min: 0, max: 120, step: 5 },
    { key: 'arm_gain', label: 'Arm gain', min: 0, max: 1.5, step: 0.05 },
    { key: 'arm_cap', label: 'Arm cap (mA)', min: 0, max: 400, step: 10 },
    { key: 'max_rel', label: 'Tracking limiter', min: 5, max: 100, step: 5 },
  ];
  const DEFAULT_PARAMS = {
    ff_gain: 1,
    ff_cap: 300,
    ff_floor: 20,
    arm_gain: 0.5,
    arm_cap: 200,
    max_rel: 40,
  };

  /* Right pair = the real ports from the descovery docstrings;
   * left pair = plausible siblings for the second, future pair. */
  const PORTS = {
    left: { leader: '/dev/tty.usbmodem5B141157061', follower: '/dev/tty.usbmodem5B141157401' },
    right: { leader: '/dev/tty.usbmodem5B141156061', follower: '/dev/tty.usbmodem5B141156401' },
  };

  const TEMP_NOMINAL = 34;
  const TEMP_GAIN_GUARD = 60; /* halve grip gain */
  const TEMP_STOP_GUARD = 65; /* force force-feedback off */
  const FF_DEADBAND_MA = 25;
  const FPOS_LAG_S = 0.15;
  const TRACK_WARN_NORM = 15; /* max |L−F| that lights the TRACK lamp */
  const RESYNC_S = 1.5;
  const OFFSET_DECAY = 0.995; /* tracking-limiter pull per tick */
  const REC_FILE = 'teleop_20260827_141500.csv';

  /* Per-joint waveforms: normalized positions in −100..+100 (gripper 0..100). */
  const WAVE = [
    { amp: 38, freq: 0.13, phase: 0.0, bias: 8 },
    { amp: 30, freq: 0.1, phase: 1.1, bias: -12 },
    { amp: 52, freq: 0.17, phase: 2.2, bias: 5 },
    { amp: 44, freq: 0.23, phase: 3.3, bias: -6 },
    { amp: 58, freq: 0.2, phase: 4.4, bias: 0 },
    { amp: 45, freq: 0.28, phase: 5.5, bias: 50 },
  ];

  const CHECK_STEPS = ['PORT', 'CALIB', 'LINK', 'RESYNC', 'ENGAGE'];

  const CHECK_DONE_RIGHT = [
    { state: 'done', detail: '14:14:52 · leader …56061 · follower …56401' },
    { state: 'done', detail: '14:14:55 · max offset 1.2% (≤3%)' },
    { state: 'done', detail: '14:14:57 · 6 motors @ 1 Mbps' },
    { state: 'done', detail: '14:14:59 · soft merge 1.5 s · delta 0.4' },
    { state: 'done', detail: '14:15:00 · teleop loop 30 fps' },
  ];
  const CHECK_ABSENT = [
    { state: 'pending', detail: 'no tty match — pair absent' },
    { state: 'pending', detail: '' },
    { state: 'pending', detail: '' },
    { state: 'pending', detail: '' },
    { state: 'pending', detail: '' },
  ];

  /* Left-arm bring-up timeline for the "dual" scenario (sim seconds). */
  const DUAL_SCRIPT = [
    { at: 0.4, step: 0, state: 'done', detail: 'leader …57061 · follower …57401' },
    { at: 0.9, step: 1, state: 'running', detail: 'comparing saved ranges…' },
    { at: 2.2, step: 1, state: 'done', detail: 'max offset 0.8% (≤3%)' },
    { at: 2.4, step: 2, state: 'running', detail: 'bus sync_read probe' },
    { at: 3.2, step: 2, state: 'done', detail: '6 motors @ 1 Mbps' },
    { at: 3.4, step: 3, state: 'running', detail: 'soft merge 1.5 s' },
    { at: 4.9, step: 3, state: 'done', detail: 'delta 0.3 norm' },
    { at: 5.1, step: 4, state: 'done', detail: 'teleop loop 30 fps' },
  ];

  /* Deterministic pseudo-random so a scenario replays identically. */
  let seed = 42;
  function rand() {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    return seed / 4294967296;
  }

  function makeArm(link, checklist, phaseShift) {
    return {
      link, /* 'none' | 'live' | 'drop' | 'stopped' */
      phaseShift, /* seconds; keeps the two arms from moving as clones */
      mode: link === 'live' ? 'off' : 'off',
      params: { ...DEFAULT_PARAMS },
      temp: TEMP_NOMINAL,
      n: 0,
      rec: 0,
      mergeUntil: -1,
      fposOffset: JOINTS.map(() => 0),
      checklist: checklist.map((s) => ({ ...s })),
      dropEnd: -1,
      nextDropAt: -1,
      guard60: false,
      guard65: false,
    };
  }

  function initState(scenario) {
    const s = {
      scenario,
      t: 0,
      paused: false,
      rec: { on: false, elapsed: 0, file: REC_FILE },
      loadFlashUntil: -1,
      scriptIdx: 0,
      arms: {
        left: makeArm('none', CHECK_ABSENT, 2.7),
        right: makeArm('live', CHECK_DONE_RIGHT, 0),
      },
    };
    if (scenario === 'dual') {
      s.arms.left = makeArm('none', CHECK_ABSENT, 2.7);
      s.arms.left.checklist[0].detail = 'searching tty…';
    }
    if (scenario === 'storm') s.arms.right.nextDropAt = 3;
    if (scenario === 'estop') {
      s.arms.right = makeArm('stopped', CHECK_DONE_RIGHT, 0);
      s.arms.right.checklist[4] = { state: 'fail', detail: 'master stop @ 14:21:08' };
    }
    if (scenario !== 'estop') s.rec.on = true;
    return s;
  }

  function wavePos(j, t) {
    const w = WAVE[j];
    const v = w.bias + w.amp * Math.sin(TAU * w.freq * t + w.phase);
    return JOINTS[j] === 'gripper' ? Math.min(100, Math.max(0, v)) : v;
  }

  function waveVel(j, t) {
    const w = WAVE[j];
    return w.amp * TAU * w.freq * Math.cos(TAU * w.freq * t + w.phase);
  }

  function makeFrame(arm, simT) {
    const t = simT; /* frame timestamp stays on the session clock */
    const wt = simT + arm.phaseShift; /* wave input only */
    const pos = {};
    const fpos = {};
    const cur = {};
    JOINTS.forEach((name, j) => {
      pos[name] = wavePos(j, wt);
      fpos[name] = wavePos(j, wt - FPOS_LAG_S) + arm.fposOffset[j];
      const vel = Math.abs(waveVel(j, wt - FPOS_LAG_S));
      cur[name] = SHOULDERS.includes(name)
        ? Math.round(vel * 0.55 + rand() * 3) /* XL430: load percent */
        : Math.round(vel * 3.2 + rand() * 14); /* XL330: milliamps */
    });
    const grip = Math.abs(cur.gripper);
    const ff =
      arm.mode === 'off'
        ? 0
        : Math.min(
            arm.params.ff_cap,
            Math.round(arm.params.ff_floor + arm.params.ff_gain * Math.max(0, grip - FF_DEADBAND_MA))
          );
    return {
      t,
      pos,
      fpos,
      cur,
      ff,
      mode: arm.mode,
      temp: Math.round(arm.temp * 10) / 10,
      n: arm.n,
      rec: arm.rec,
      params: { ...arm.params },
    };
  }

  function stepMerge(arm, t) {
    if (arm.mergeUntil < 0) return;
    if (t >= arm.mergeUntil) {
      arm.mergeUntil = -1;
      arm.fposOffset = JOINTS.map(() => 0);
      return;
    }
    arm.fposOffset = arm.fposOffset.map((v) => v * 0.82); /* fast convergence */
  }

  function stepHeat(state, events) {
    const arm = state.arms.right;
    arm.temp = arm.guard65
      ? Math.max(61.2, arm.temp - 0.025) /* settles in the caution band */
      : Math.min(66.5, TEMP_NOMINAL + state.t * 2.2);
    if (!arm.guard60 && arm.temp >= TEMP_GAIN_GUARD) {
      arm.guard60 = true;
      arm.params = { ...arm.params, ff_gain: Math.round(arm.params.ff_gain * 5) / 10 };
      events.push({ sev: 'caution', arm: 'R', msg: `gripper ${TEMP_GAIN_GUARD}°C — Grip gain halved` });
    }
    if (!arm.guard65 && arm.temp >= TEMP_STOP_GUARD) {
      arm.guard65 = true;
      arm.mode = 'off';
      events.push({ sev: 'alert', arm: 'R', msg: `gripper ${TEMP_STOP_GUARD}°C — force feedback OFF` });
    }
  }

  function stepStorm(state, events) {
    const arm = state.arms.right;
    if (arm.link === 'live' && state.t >= arm.nextDropAt) {
      arm.link = 'drop';
      arm.dropStart = state.t;
      arm.dropEnd = state.t + 0.8 + rand() * 0.9;
      events.push({ sev: 'alert', arm: 'R', msg: 'link lost — retrying sync_read' });
    }
    if (arm.link === 'drop' && state.t >= arm.dropEnd) {
      arm.link = 'live';
      arm.rec += 1;
      arm.fposOffset = JOINTS.map(() => (rand() - 0.5) * 44);
      state.loadFlashUntil = state.t + 0.8;
      arm.nextDropAt = state.t + 3 + rand() * 2.5;
      events.push({
        sev: 'info',
        arm: 'R',
        msg: `reconnect #${arm.rec} ok (${(state.t - arm.dropStart).toFixed(1)}s) — RESYNC advised`,
      });
    }
  }

  function stepDual(state, events) {
    const left = state.arms.left;
    while (state.scriptIdx < DUAL_SCRIPT.length && state.t >= DUAL_SCRIPT[state.scriptIdx].at) {
      const cue = DUAL_SCRIPT[state.scriptIdx];
      left.checklist[cue.step] = { state: cue.state, detail: cue.detail };
      if (cue.state === 'done') {
        events.push({ sev: 'info', arm: 'L', msg: `${CHECK_STEPS[cue.step]} ✓ ${cue.detail}` });
      }
      if (cue.step === 4 && cue.state === 'done') left.link = 'live';
      state.scriptIdx += 1;
    }
  }

  function createSim() {
    let state = initState('r-only');
    const listeners = [];

    function emit(events) {
      const frames = {};
      ['left', 'right'].forEach((id) => {
        const arm = state.arms[id];
        frames[id] = arm.link === 'live' ? makeFrame(arm, state.t) : null;
      });
      listeners.forEach((fn) => fn(state, frames, events));
    }

    function tick() {
      if (state.paused) return;
      state.t += TICK_S;
      const events = [];
      if (state.scenario === 'heat') stepHeat(state, events);
      if (state.scenario === 'storm') stepStorm(state, events);
      if (state.scenario === 'dual') stepDual(state, events);
      ['left', 'right'].forEach((id) => {
        const arm = state.arms[id];
        if (arm.link !== 'live') return;
        arm.n += 2; /* teleop loop runs 30 fps; telemetry samples every 2nd */
        stepMerge(arm, state.t);
        arm.fposOffset = arm.fposOffset.map((v) => v * OFFSET_DECAY);
        if (state.scenario !== 'heat') {
          arm.temp += (TEMP_NOMINAL - arm.temp) * 0.01 + (rand() - 0.5) * 0.05;
        }
      });
      if (state.rec.on) state.rec.elapsed += TICK_S;
      emit(events);
    }

    function announce(events) {
      emit(events);
    }

    function armTag(armId) {
      return armId === 'left' ? 'L' : 'R';
    }

    return {
      spec: {
        JOINTS,
        SHOULDERS,
        PARAM_SPECS,
        PORTS,
        CHECK_STEPS,
        TRACK_WARN_NORM,
        REC_FILE,
        TEMP_GAIN_GUARD,
        TEMP_STOP_GUARD,
      },
      getState: () => state,

      subscribe(fn) {
        listeners.push(fn);
      },

      start() {
        setInterval(tick, TICK_MS);
        emit([{ sev: 'info', arm: '·', msg: 'sim started — scenario: R only' }]);
      },

      setScenario(name) {
        state = initState(name);
        announce([{ sev: 'info', arm: '·', msg: `SIM scenario → ${name}` }]);
      },

      setPaused(on) {
        state.paused = on;
        announce([{ sev: 'info', arm: '·', msg: on ? 'SIM paused' : 'SIM resumed' }]);
      },

      setRec(on) {
        state.rec = { ...state.rec, on };
        announce([{ sev: 'info', arm: '·', msg: on ? `recording → ${REC_FILE}` : 'recording stopped' }]);
      },

      /* Would-be /ctl command from a cockpit control. Applies it to the sim
       * and logs the exact JSON payload the real panel would send. */
      command(armId, cmd) {
        const arm = state.arms[armId];
        const events = [{ sev: 'info', arm: armTag(armId), msg: `→ /ctl ${JSON.stringify(cmd)}` }];
        if (cmd.mode !== undefined && arm.link === 'live') arm.mode = cmd.mode;
        if (cmd.resync === 1 && arm.link === 'live') {
          arm.mergeUntil = state.t + RESYNC_S;
          events.push({ sev: 'info', arm: armTag(armId), msg: `soft resync — merging ${RESYNC_S} s` });
        }
        if (cmd.stop === 1) {
          arm.link = 'stopped';
          arm.mode = 'off';
          events.push({ sev: 'caution', arm: armTag(armId), msg: 'teleop stopped' });
        }
        PARAM_SPECS.forEach(({ key }) => {
          if (cmd[key] !== undefined && arm.link === 'live') {
            arm.params = { ...arm.params, [key]: cmd[key] };
          }
        });
        announce(events);
      },

      masterStop() {
        ['left', 'right'].forEach((id) => {
          if (state.arms[id].link !== 'none') {
            state.arms[id].link = 'stopped';
            state.arms[id].mode = 'off';
          }
        });
        state.scenario = 'estop';
        state.rec = { ...state.rec, on: false };
        announce([
          { sev: 'alert', arm: '·', msg: 'MASTER STOP — /ctl {"stop":1} both arms' },
          { sev: 'info', arm: '·', msg: 'recording stopped' },
        ]);
      },
    };
  }

  NS.sim = createSim();
})();
