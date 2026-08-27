/* wing.js — builds one arm station ("wing") from the #wing-template and
 * binds its instruments: link chips, joint gauges, strip charts, posture
 * view, force-feedback console. Both wings are built from the same template
 * so left and right stay identical by construction. */
(function () {
  'use strict';
  const NS = (window.COCKPIT = window.COCKPIT || {});

  const HOT_MA = 350; /* XL330 current worth highlighting */
  const HOT_PCT = 55; /* XL430 load percent worth highlighting */
  const SHOULDER_CUR_DISPLAY_SCALE = 10; /* chart convention: load% ×10 */
  const ARM_LABEL = { left: 'ARM L', right: 'ARM R' };
  const CHIP_TEXT = { none: 'NO LINK', live: 'LINK', drop: 'LINK LOST', stopped: 'STOPPED' };

  function buildGaugeRows(container, JOINTS) {
    const head = document.createElement('div');
    head.className = 'gauge-head';
    head.innerHTML = '<span></span><span>joint</span><span>L</span><span>F</span><span></span><span>mA·%</span>';
    container.appendChild(head);
    return JOINTS.map((name, i) => {
      const isGripper = name === 'gripper';
      const row = document.createElement('div');
      row.className = 'gauge-row';
      row.innerHTML =
        `<span class="gauge-swatch"></span>` +
        `<span class="gauge-name num">${name}</span>` +
        `<b class="gauge-l num">—</b>` +
        `<b class="gauge-f num">—</b>` +
        `<span class="gauge-track"${isGripper ? ' data-span="full"' : ''}>` +
        `<span class="gauge-fill"></span><span class="gauge-bug"></span></span>` +
        `<span class="gauge-cur num">—</span>`;
      row.style.setProperty('--j', `var(--color-j${i + 1})`);
      /* Park the leader bug at its zero position until data arrives. */
      row.querySelector('.gauge-bug').style.transform = `translateX(${isGripper ? 0 : 50}%)`;
      container.appendChild(row);
      return {
        l: row.querySelector('.gauge-l'),
        f: row.querySelector('.gauge-f'),
        fill: row.querySelector('.gauge-fill'),
        bug: row.querySelector('.gauge-bug'),
        cur: row.querySelector('.gauge-cur'),
        isGripper,
      };
    });
  }

  function buildLegend(ul, JOINTS) {
    JOINTS.forEach((name, i) => {
      const li = document.createElement('li');
      li.innerHTML = `<span class="legend-swatch"></span>${i + 1}:${name}`;
      li.style.setProperty('--j', `var(--color-j${i + 1})`);
      ul.appendChild(li);
    });
  }

  function buildParams(container, PARAM_SPECS, armId, onCommand) {
    const rows = {};
    PARAM_SPECS.forEach(({ key, label, min, max, step }) => {
      const id = `param-${armId}-${key}`;
      const row = document.createElement('div');
      row.className = 'param-row';
      row.innerHTML =
        `<label for="${id}">${label}</label>` +
        `<input type="range" id="${id}" min="${min}" max="${max}" step="${step}" value="${min}" />` +
        `<b class="param-value num">—</b>`;
      container.appendChild(row);
      const input = row.querySelector('input');
      const valueEl = row.querySelector('.param-value');
      let held = false;
      input.addEventListener('pointerdown', () => {
        held = true;
      });
      window.addEventListener('pointerup', () => {
        held = false;
      });
      input.addEventListener('input', () => {
        valueEl.textContent = input.value;
      });
      input.addEventListener('change', () => {
        onCommand({ [key]: parseFloat(input.value) });
      });
      rows[key] = { input, valueEl, isSuppressed: () => held || document.activeElement === input };
    });
    return rows;
  }

  function updateGauges(gauges, frame, JOINTS) {
    JOINTS.forEach((name, i) => {
      const g = gauges[i];
      const l = frame.pos[name] ?? 0;
      const f = frame.fpos[name] ?? 0;
      const cur = frame.cur[name] ?? 0;
      g.l.textContent = l.toFixed(0);
      g.f.textContent = f.toFixed(0);
      const fillScale = Math.max(-1, Math.min(1, f / 100));
      g.fill.style.transform = `scaleX(${g.isGripper ? Math.max(0, fillScale) : fillScale})`;
      const bugPct = g.isGripper ? Math.max(0, Math.min(100, l)) : (Math.max(-100, Math.min(100, l)) + 100) / 2;
      g.bug.style.transform = `translateX(${bugPct}%)`;
      const isShoulder = name === 'shoulder_pan' || name === 'shoulder_lift';
      g.cur.textContent = isShoulder ? `${cur}%` : `${cur}`;
      g.cur.dataset.hot = String(isShoulder ? cur > HOT_PCT : cur > HOT_MA);
    });
  }

  NS.wing = {
    /* deps: { spec, onCommand(cmdObject) } */
    build(section, armId, deps) {
      const { JOINTS, PARAM_SPECS, PORTS } = deps.spec;
      const node = document.getElementById('wing-template').content.cloneNode(true);
      section.appendChild(node);
      section.dataset.live = 'false';

      section.querySelector('[data-role="wing-title"]').textContent = ARM_LABEL[armId];
      const portEl = section.querySelector('[data-role="port"]');
      const ports = PORTS[armId];
      portEl.textContent = `${ports.leader} · ${ports.follower}`;
      portEl.title = `leader ${ports.leader} · follower ${ports.follower}`;

      const linkChip = section.querySelector('[data-role="link-chip"]');
      const chips = {
        mode: section.querySelector('[data-role="chip-mode"]'),
        temp: section.querySelector('[data-role="chip-temp"]'),
        ff: section.querySelector('[data-role="chip-ff"]'),
        frames: section.querySelector('[data-role="chip-frames"]'),
        recon: section.querySelector('[data-role="chip-recon"]'),
      };

      const gauges = buildGaugeRows(section.querySelector('[data-role="gauges"]'), JOINTS);
      buildLegend(section.querySelector('[data-role="legend"]'), JOINTS);
      const params = buildParams(section.querySelector('[data-role="params"]'), PARAM_SPECS, armId, deps.onCommand);

      const charts = {
        pos: NS.charts.create(section.querySelector('[data-role="chart-pos"]'), {}),
        fpos: NS.charts.create(section.querySelector('[data-role="chart-fpos"]'), {}),
        delta: NS.charts.create(section.querySelector('[data-role="chart-delta"]'), { zeroLine: true }),
        cur: NS.charts.create(section.querySelector('[data-role="chart-cur"]'), {}),
      };
      const posture = NS.posture.create(section.querySelector('[data-role="posture"]'));

      const modeButtons = Array.from(section.querySelectorAll('[data-role="mode-segment"] button'));
      modeButtons.forEach((btn) => {
        btn.addEventListener('click', () => deps.onCommand({ mode: btn.dataset.mode }));
      });
      const resyncBtn = section.querySelector('[data-role="resync"]');
      const stopBtn = section.querySelector('[data-role="stop"]');
      resyncBtn.addEventListener('click', () => deps.onCommand({ resync: 1 }));
      stopBtn.addEventListener('click', () => deps.onCommand({ stop: 1 }));

      function setControlsEnabled(live) {
        modeButtons.forEach((btn) => {
          btn.disabled = !live;
        });
        Object.values(params).forEach(({ input }) => {
          input.disabled = !live;
        });
        resyncBtn.disabled = !live;
        stopBtn.disabled = !live;
      }
      setControlsEnabled(false);

      function pushCharts(frame) {
        const pos = JOINTS.map((name) => frame.pos[name] ?? 0);
        const fpos = JOINTS.map((name) => frame.fpos[name] ?? 0);
        const delta = pos.map((v, i) => v - fpos[i]);
        const cur = JOINTS.map((name) => {
          const raw = frame.cur[name] ?? 0;
          return name === 'shoulder_pan' || name === 'shoulder_lift'
            ? raw * SHOULDER_CUR_DISPLAY_SCALE
            : raw;
        });
        charts.pos.push(frame.t, pos);
        charts.fpos.push(frame.t, fpos);
        charts.delta.push(frame.t, delta);
        charts.cur.push(frame.t, cur, frame.ff);
      }

      return {
        reset() {
          Object.values(charts).forEach((chart) => chart.clear());
        },

        update(arm, frame) {
          const live = arm.link === 'live';
          const merging = live && arm.mergeUntil >= 0;
          section.dataset.live = String(live);
          linkChip.dataset.state = arm.link;
          linkChip.textContent = merging ? 'MERGING' : CHIP_TEXT[arm.link];
          setControlsEnabled(live);
          chips.temp.textContent = `${arm.temp.toFixed(1)}°C`;
          chips.temp.dataset.heat =
            arm.temp >= deps.spec.TEMP_STOP_GUARD
              ? 'alert'
              : arm.temp >= deps.spec.TEMP_GAIN_GUARD
                ? 'caution'
                : 'off';
          if (!frame) {
            chips.mode.textContent = '—';
            chips.ff.textContent = '—';
            if (arm.link === 'none') chips.temp.textContent = '—';
            modeButtons.forEach((btn) => btn.setAttribute('aria-pressed', 'false'));
            return;
          }
          chips.mode.textContent = frame.mode;
          chips.ff.textContent = `${frame.ff} mA`;
          chips.frames.textContent = String(frame.n);
          chips.recon.textContent = String(frame.rec);
          modeButtons.forEach((btn) => {
            btn.setAttribute('aria-pressed', String(btn.dataset.mode === frame.mode));
          });
          Object.entries(params).forEach(([key, row]) => {
            const value = frame.params[key];
            if (value === undefined || row.isSuppressed()) return;
            row.input.value = String(value);
            row.valueEl.textContent = String(value);
          });
          updateGauges(gauges, frame, JOINTS);
          pushCharts(frame);
          posture.update(frame);
        },
      };
    },
  };
})();
