/* posture.js — SVG side-profile posture view for one arm.
 *
 * ILLUSTRATIVE mapping, not kinematics: normalized joint values scale to
 * drawing angles with fixed segment lengths. Ghost outline = leader command
 * (pos), solid colored linkage = follower actual (fpos). shoulder_pan and
 * wrist_roll render as mini dials; the gripper as an opening jaw + percent.
 */
(function () {
  'use strict';
  const NS = (window.COCKPIT = window.COCKPIT || {});

  const SVG = 'http://www.w3.org/2000/svg';
  const VIEW = { w: 220, h: 180 };
  const SHOULDER = { x: 54, y: 150 };
  const SEG_LEN = [46, 40, 20]; /* upper arm, forearm, wrist */
  const JAW_LEN = 13;
  const DEG = Math.PI / 180;
  /* Illustrative norm→degrees mapping (documented in README.md). */
  const LIFT_BASE = 58;
  const LIFT_SCALE = 0.45;
  const ELBOW_BASE = -62;
  const ELBOW_SCALE = 0.45;
  const WRIST_BASE = -12;
  const WRIST_SCALE = 0.5;
  const DIAL_SCALE = 0.9;
  const JAW_SPREAD = 0.2; /* deg per grip percent, per side */
  const REST = { shoulder_lift: 0, elbow_flex: 0, wrist_flex: 0, shoulder_pan: 0, wrist_roll: 0, gripper: 30 };

  function el(name, attrs, parent) {
    const node = document.createElementNS(SVG, name);
    Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
    if (parent) parent.appendChild(node);
    return node;
  }

  function chainPoints(values) {
    const a1 = (LIFT_BASE + values.shoulder_lift * LIFT_SCALE) * DEG;
    const a2 = a1 + (ELBOW_BASE + values.elbow_flex * ELBOW_SCALE) * DEG;
    const a3 = a2 + (WRIST_BASE + values.wrist_flex * WRIST_SCALE) * DEG;
    const angles = [a1, a2, a3];
    const pts = [{ x: SHOULDER.x, y: SHOULDER.y }];
    angles.forEach((a, i) => {
      const prev = pts[i];
      pts.push({ x: prev.x + SEG_LEN[i] * Math.cos(a), y: prev.y - SEG_LEN[i] * Math.sin(a) });
    });
    return { pts, tipAngle: a3 };
  }

  function pathFrom(pts) {
    return pts.map((p, i) => `${i ? 'L' : 'M'}${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');
  }

  function jawPath(tip, tipAngle, gripPct) {
    const spread = (3 + gripPct * JAW_SPREAD) * DEG;
    const jaw = (offset) => {
      const a = tipAngle + offset;
      return `M${tip.x.toFixed(1)} ${tip.y.toFixed(1)} l${(JAW_LEN * Math.cos(a)).toFixed(1)} ${(-JAW_LEN * Math.sin(a)).toFixed(1)}`;
    };
    return jaw(spread) + ' ' + jaw(-spread);
  }

  function buildDial(svg, cx, cy, label, colorVar) {
    el('circle', { cx, cy, r: 11, class: 'posture-dial' }, svg);
    const needle = el('line', {
      x1: cx,
      y1: cy,
      x2: cx,
      y2: cy - 9,
      stroke: `var(${colorVar})`,
      'stroke-width': 2,
      'stroke-linecap': 'round',
    }, svg);
    const text = el('text', { x: cx, y: cy + 22, class: 'posture-label', 'text-anchor': 'middle' }, svg);
    text.textContent = label;
    return { needle, cx, cy };
  }

  function joints(frameField) {
    return {
      shoulder_lift: frameField.shoulder_lift ?? 0,
      elbow_flex: frameField.elbow_flex ?? 0,
      wrist_flex: frameField.wrist_flex ?? 0,
      shoulder_pan: frameField.shoulder_pan ?? 0,
      wrist_roll: frameField.wrist_roll ?? 0,
      gripper: frameField.gripper ?? 0,
    };
  }

  NS.posture = {
    create(mount) {
      const svg = el('svg', { viewBox: `0 0 ${VIEW.w} ${VIEW.h}`, role: 'img', 'aria-label': 'Arm posture schematic' });
      mount.appendChild(svg);

      el('line', { x1: 18, y1: SHOULDER.y + 8, x2: 150, y2: SHOULDER.y + 8, class: 'posture-ground' }, svg);
      el('rect', { x: SHOULDER.x - 9, y: SHOULDER.y - 2, width: 18, height: 10, rx: 2, class: 'posture-base' }, svg);

      const ghost = el('path', { class: 'posture-ghost', d: '' }, svg);
      const ghostJaw = el('path', { class: 'posture-ghost', d: '' }, svg);
      const seg = [2, 3, 4].map((i) =>
        el('line', { class: 'posture-seg', stroke: `var(--color-j${i})`, x1: 0, y1: 0, x2: 0, y2: 0 }, svg)
      );
      const jaw = el('path', { class: 'posture-jaw', d: '' }, svg);
      const nodes = [2, 3, 4].map((i) =>
        el('circle', { r: 3.4, fill: `var(--color-j${i})`, cx: 0, cy: 0 }, svg)
      );

      const panDial = buildDial(svg, 190, 30, 'PAN', '--color-j1');
      const rollDial = buildDial(svg, 190, 84, 'ROLL', '--color-j5');
      const gripText = el('text', { x: 190, y: 134, class: 'posture-grip num', 'text-anchor': 'middle' }, svg);
      const gripLabel = el('text', { x: 190, y: 148, class: 'posture-label', 'text-anchor': 'middle' }, svg);
      gripLabel.textContent = 'GRIP';

      function apply(cmd, act) {
        const ghostChain = chainPoints(cmd);
        ghost.setAttribute('d', pathFrom(ghostChain.pts));
        ghostJaw.setAttribute('d', jawPath(ghostChain.pts[3], ghostChain.tipAngle, cmd.gripper));
        const chain = chainPoints(act);
        seg.forEach((line, i) => {
          line.setAttribute('x1', chain.pts[i].x.toFixed(1));
          line.setAttribute('y1', chain.pts[i].y.toFixed(1));
          line.setAttribute('x2', chain.pts[i + 1].x.toFixed(1));
          line.setAttribute('y2', chain.pts[i + 1].y.toFixed(1));
        });
        nodes.forEach((node, i) => {
          node.setAttribute('cx', chain.pts[i].x.toFixed(1));
          node.setAttribute('cy', chain.pts[i].y.toFixed(1));
        });
        jaw.setAttribute('d', jawPath(chain.pts[3], chain.tipAngle, act.gripper));
        panDial.needle.setAttribute('transform', `rotate(${(act.shoulder_pan * DIAL_SCALE).toFixed(1)} ${panDial.cx} ${panDial.cy})`);
        rollDial.needle.setAttribute('transform', `rotate(${(act.wrist_roll * DIAL_SCALE).toFixed(1)} ${rollDial.cx} ${rollDial.cy})`);
        gripText.textContent = `${Math.round(act.gripper)}%`;
      }

      apply(REST, REST);

      return {
        update(frame) {
          if (!frame) return; /* dead arm keeps its last (or rest) pose, dimmed by CSS */
          apply(joints(frame.pos), joints(frame.fpos));
        },
      };
    },
  };
})();
