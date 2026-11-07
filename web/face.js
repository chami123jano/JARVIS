// JARVIS's face: a head drawn as glowing scan lines, in 3D, from geometry alone.
// It listens, thinks and speaks with the voice loop: the mouth follows the loudness of the
// audio actually playing (speech.Speaker.voice_now), not a guess.
'use strict';
const $ = s => document.querySelector(s);
const canvas = $('#face'), ctx = canvas.getContext('2d');

// ------------------------------------------------------------------ the voice loop
let live = {running: false, state: 'off', levels: [], lines: [], voice: null};
let token = '';
let voice = {id: null, envelope: [], rate: 50, elapsed: 0, at: 0};
let polling = false;

async function poll() {
  if (polling || document.hidden) return; polling = true;
  try {
    const r = await fetch('/api/live', {cache: 'no-store'});
    live = await r.json();
    if (live.voice) {
      if (live.voice.id !== voice.id) voice = {id: live.voice.id, envelope: live.voice.envelope || [], rate: live.voice.rate || 50};
      voice.elapsed = live.voice.elapsed; voice.at = performance.now();
    } else voice.id = null;
    showState(); showLines();
  } catch (e) { live = {running: false, state: 'off', levels: [], lines: []}; showState(); }
  finally { polling = false; }
}
setInterval(poll, 100); poll();

const WORDS = {idle: 'STANDING BY', listening: 'LISTENING', thinking: 'THINKING', speaking: 'SPEAKING', off: 'VOICE LOOP OFF'};
function current() { return live.running ? (live.state || 'idle') : 'off'; }
function showState() {
  const s = current(); $('#state').dataset.state = s; $('#stateText').textContent = WORDS[s] || s.toUpperCase();
}
let shownKey = '', typedAnswer = null;
function showLines() {
  const lines = live.lines || [];
  const you = [...lines].reverse().find(l => l.who === 'you');
  const said = [...lines].reverse().find(l => l.who === 'jarvis');
  const key = JSON.stringify([you, said]);
  if (key === shownKey) return; shownKey = key;
  $('#you').textContent = you ? you.text : '';
  if (said) typeOut(said.text); else if (!typedAnswer) $('#jarvis').textContent = '';
}
let typing = null;
function typeOut(text) {
  clearInterval(typing); const el = $('#jarvis'); let n = 0; el.textContent = '';
  typing = setInterval(() => { n += 2; el.textContent = text.slice(0, n); if (n >= text.length) clearInterval(typing); }, 22);
}

// Typed requests go through the same brain as speech, and are said aloud.
$('#ask').addEventListener('submit', async event => {
  event.preventDefault();
  const prompt = $('#prompt').value.trim(); if (!prompt) return;
  $('#send').disabled = true;
  try {
    if (!token) token = (await (await fetch('/api/state')).json()).token || '';
    const r = await fetch('/api/chat', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Jarvis-Token': token}, body: JSON.stringify({prompt, speak: true})});
    const job = await r.json(); if (!r.ok) throw new Error(job.error || 'Request failed');
    $('#prompt').value = '';
    if (!live.running) { $('#you').textContent = prompt; waitFor(job.id); }
  } catch (error) { $('#jarvis').textContent = String(error.message || error); }
  finally { $('#send').disabled = false; }
});
async function waitFor(id) {
  // Only when the voice loop is off: otherwise its own lines show the answer.
  for (let i = 0; i < 600; i++) {
    await new Promise(r => setTimeout(r, 400));
    const job = await (await fetch('/api/jobs/' + id)).json();
    if (!['running', 'stopping'].includes(job.state)) { typedAnswer = job.answer; typeOut(job.answer || ''); return; }
  }
}

// ------------------------------------------------------------------ the head
const R = 66, C = 100;              // scan lines, points around each
const TOP = 1.08, CHIN = -1.04, MOUTH_Y = -0.555;
const g = (x, y, cx, cy, sx, sy) => Math.exp(-(((x - cx) / sx) ** 2) - (((y - cy) / sy) ** 2));
const smooth = (a, b, v) => { const t = Math.min(1, Math.max(0, (v - a) / (b - a))); return t * t * (3 - 2 * t); };

// Half widths down the face, from the temples to the chin: a human head, not a bulb.
const PROFILE = [[0.3, 0.79], [0.1, 0.775], [-0.1, 0.745], [-0.35, 0.69], [-0.6, 0.59], [-0.78, 0.46], [-0.9, 0.32]];
function table(y, points) {
  for (let k = 0; k < points.length - 1; k++) {
    const [y0, w0] = points[k], [y1, w1] = points[k + 1];
    if (y <= y0 && y >= y1) { const t = (y0 - y) / (y0 - y1), s = t * t * (3 - 2 * t); return w0 + (w1 - w0) * s; }
  }
  return points[points.length - 1][1];
}
const chinCap = y => y < -0.9 ? Math.sqrt(Math.max(0, 1 - ((-0.9 - y) / 0.14) ** 2)) : 1;
function width(y) {                 // half width of the head at height y
  if (y >= 0.3) return 0.79 * Math.sqrt(Math.max(0, 1 - ((y - 0.3) / 0.78) ** 2));
  return table(y, PROFILE) * chinCap(y);
}
function depth(y) {                 // half depth, front to back
  if (y >= 0.1) return width(y) * 1.12;
  return (0.868 - 0.25 * smooth(0.1, -0.9, y)) * chinCap(y);
}

function features(x, y) {           // the face, as height above the plain head
  const ax = Math.abs(x);
  let f = 0;
  f += 0.30 * g(x, y, 0, -0.13, 0.085, 0.24) * (0.55 + 0.45 * smooth(0.1, -0.3, y));   // nose
  f += 0.07 * g(x, y, 0, -0.32, 0.09, 0.07);                                           // nose tip
  f += 0.05 * g(ax, y, 0.11, -0.34, 0.055, 0.05);                                     // nostrils
  f -= 0.14 * g(ax, y, 0.29, 0.13, 0.15, 0.09);                                       // eye sockets
  f += 0.07 * g(ax, y, 0.28, 0.27, 0.2, 0.06) + 0.05 * g(x, y, 0, 0.24, 0.1, 0.07);   // brow
  f += 0.07 * g(ax, y, 0.43, -0.07, 0.13, 0.11);                                      // cheekbones
  f -= 0.045 * g(ax, y, 0.40, -0.40, 0.12, 0.12);                                     // under the cheek
  f += 0.075 * g(x, y, 0, -0.51, 0.17, 0.04);                                         // upper lip
  f += 0.07 * g(x, y, 0, -0.61, 0.15, 0.045);                                         // lower lip
  f -= 0.03 * g(ax, y, 0.2, -0.56, 0.04, 0.04);                                       // mouth corners
  f += 0.065 * g(x, y, 0, -0.87, 0.15, 0.08);                                         // chin
  return f;
}
// A point on the head: theta 0 faces you.
function headPoint(theta, y) {
  const w = width(y), d = depth(y);
  const x = w * Math.sin(theta);
  const front = Math.max(0, Math.cos(theta)) ** 1.4;
  return [x, y, d * Math.cos(theta) + features(x, y) * front];
}
function frontZ(x, y) {             // the face's surface where it faces you
  const w = width(y); const s = Math.max(-0.999, Math.min(0.999, x / Math.max(w, 1e-3)));
  return headPoint(Math.asin(s), y)[2];
}

// The rest pose, worked out once.
const rest = [];
for (let i = 0; i < R; i++) {
  const y = TOP + (CHIN - TOP) * i / (R - 1); const row = [];
  for (let j = 0; j < C; j++) row.push(headPoint(-Math.PI + 2 * Math.PI * j / C, y));
  rest.push(row);
}
const NECK = [];
for (let i = 0; i < 10; i++) {
  const y = -0.82 - i * 0.075, r = 0.36 + i * i * 0.005, row = [];
  for (let j = 0; j < C; j++) { const t = -Math.PI + 2 * Math.PI * j / C; row.push([r * Math.sin(t), y, r * Math.cos(t) * 0.92 - 0.2]); }
  NECK.push(row);
}

// The jaw drops with the mouth; the lips draw in a little.
function deform(p, open) {
  let [x, y, z] = p;
  if (open > 0.001 && z > -0.2) {
    const below = smooth(MOUTH_Y + 0.004, MOUTH_Y - 0.012, y);    // the lower lip goes with the jaw
    const side = 1 - 0.55 * smooth(0.22, 0.7, Math.abs(x));
    y -= open * 0.1 * below * side;
    z -= open * 0.03 * below * side;
    x *= 1 - open * 0.07 * g(x, y, 0, MOUTH_Y, 0.25, 0.12);
  }
  return [x, y, z];
}

// ------------------------------------------------------------------ the look
let W = 0, H = 0, ratio = 1;
function resize() {
  ratio = Math.min(2, window.devicePixelRatio || 1);
  W = innerWidth; H = innerHeight;
  canvas.width = Math.round(W * ratio); canvas.height = Math.round(H * ratio);
}
addEventListener('resize', resize); resize();

const colours = {idle: [114, 228, 226], listening: [140, 240, 236], thinking: [223, 182, 125], speaking: [170, 246, 242], off: [90, 140, 145]};
let colour = [114, 228, 226], open = 0, blink = 1, nextBlink = 2, gaze = [0, 0], gazeTo = [0, 0], nextGaze = 1;
let glitchUntil = 0, nextGlitch = 6, mouseX = 0, mouseY = 0;
addEventListener('pointermove', e => { mouseX = e.clientX / W - 0.5; mouseY = e.clientY / H - 0.5; });

const particles = Array.from({length: 70}, () => ({x: Math.random() * 2 - 1, y: Math.random(), s: Math.random() * 0.6 + 0.2}));

function mouthTarget(t) {
  const state = current();
  if (voice.id !== null && voice.envelope.length) {
    const at = voice.elapsed + Math.min(0.6, (performance.now() - voice.at) / 1000);
    const k = Math.floor(at * voice.rate);
    const v = k < voice.envelope.length ? voice.envelope[k] : 0;
    return 0.8 * Math.max(0, Math.min(1, (v - 0.15) / 0.85));
  }
  if (state === 'speaking')        // the offline Windows voice: no loudness to follow
    return 0.25 + 0.35 * Math.abs(Math.sin(t * 9.1) * Math.sin(t * 5.3));
  return 0;
}

function project(p, yaw, pitch, scale, cx, cy) {
  let [x, y, z] = p;
  const cyaw = Math.cos(yaw), syaw = Math.sin(yaw);
  [x, z] = [x * cyaw + z * syaw, -x * syaw + z * cyaw];
  const cp = Math.cos(pitch), sp = Math.sin(pitch);
  [y, z] = [y * cp - z * sp, y * sp + z * cp];
  const k = 4.2 / (4.2 - z);
  return [cx + x * scale * k, cy - y * scale * k, z, x, y];   // on screen, and turned in 3D
}
const LIGHT = (() => { const v = [-0.45, 0.55, 0.75], n = Math.hypot(...v); return v.map(c => c / n); })();

let last = performance.now(), clock = 0;
function frame(now) {
  const dt = Math.min(0.05, (now - last) / 1000); last = now; clock += dt;
  const t = clock, state = current();
  const target = colours[state] || colours.idle;
  colour = colour.map((c, i) => c + (target[i] - c) * Math.min(1, dt * 3));
  const want = mouthTarget(t);
  open += (want - open) * Math.min(1, dt * (want > open ? 28 : 14));

  // Blinks, and the eyes looking about.
  nextBlink -= dt; if (nextBlink < 0) { nextBlink = 2.5 + Math.random() * 4; blink = -1; }
  if (blink < 1) blink = Math.min(1, blink + dt * 9);
  const lid = Math.abs(blink);
  nextGaze -= dt; if (nextGaze < 0) { nextGaze = 1 + Math.random() * 2.5; gazeTo = state === 'thinking' ? [0.5 * (Math.random() > .5 ? 1 : -1), 0.6] : [(Math.random() - 0.5) * 0.8, (Math.random() - 0.5) * 0.5]; }
  gaze = gaze.map((v, i) => v + (gazeTo[i] - v) * Math.min(1, dt * 12));
  nextGlitch -= dt; if (nextGlitch < 0) { nextGlitch = 5 + Math.random() * 9; glitchUntil = t + 0.12; }
  const glitch = t < glitchUntil;

  const yaw = 0.22 * Math.sin(t * 0.31) + 0.06 * Math.sin(t * 0.83) + mouseX * 0.35 + (state === 'listening' ? 0.05 * Math.sin(t * 1.7) : 0);
  const pitch = 0.13 + 0.06 * Math.sin(t * 0.23) - mouseY * 0.2 + open * 0.04 + (state === 'thinking' ? 0.1 : 0);
  const scale = Math.min(W * 0.26, H * 0.30), cx = W / 2, cy = H * 0.43 + Math.sin(t * 0.9) * 4;

  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, W, H);
  ctx.globalCompositeOperation = 'lighter';
  const [r, gg, b] = colour.map(Math.round);
  const rgba = a => `rgba(${r},${gg},${b},${a})`;

  // The projector below, and its light.
  const baseY = cy + scale * 1.95;
  const levels = live.levels || [], level = levels.length ? levels[levels.length - 1] : 0;
  const cone = ctx.createLinearGradient(0, baseY, 0, cy - scale);
  cone.addColorStop(0, rgba(0.16)); cone.addColorStop(1, rgba(0));
  ctx.fillStyle = cone; ctx.beginPath();
  ctx.moveTo(cx - scale * 0.5, baseY); ctx.lineTo(cx - scale * 1.25, cy - scale * 1.1);
  ctx.lineTo(cx + scale * 1.25, cy - scale * 1.1); ctx.lineTo(cx + scale * 0.5, baseY); ctx.fill();
  for (let k = 0; k < 4; k++) {
    const grow = state === 'listening' ? level * 0.5 : 0;
    const wobble = state === 'listening' ? (Math.sin(t * 4 - k) + 1) * 0.04 : 0;
    ctx.strokeStyle = rgba(0.5 - k * 0.1); ctx.lineWidth = k ? 1 : 2;
    ctx.beginPath(); ctx.ellipse(cx, baseY, scale * (0.5 + k * 0.2 + grow + wobble), scale * (0.07 + k * 0.03), 0, 0, Math.PI * 2); ctx.stroke();
  }
  for (const p of particles) {
    p.y += dt * 0.08 * p.s; if (p.y > 1) { p.y = 0; p.x = Math.random() * 2 - 1; }
    const py = baseY - p.y * scale * 2.9, px = cx + p.x * scale * (0.5 + p.y * 0.8);
    ctx.fillStyle = rgba(0.5 * (1 - p.y) * p.s); ctx.fillRect(px, py, 1.6, 1.6);
  }

  // The head: every point moved, turned and projected, then shaded by which way it faces.
  const rows = rest.map(row => row.map(p => project(deform(p, open), yaw, pitch, scale, cx, cy)));
  const neck = NECK.map(row => row.map(p => project(p, yaw, pitch, scale, cx, cy)));
  const scanY = state === 'thinking' ? (Math.sin(t * 1.6) * 0.5 + 0.5) : -1;
  const BUCKETS = 10, paths = Array.from({length: BUCKETS}, () => new Path2D());
  // Lit like a solid: the surface's own direction, from the turned 3D points.
  const shade = (a, b, c) => {
    const rx = b[3] - a[3], ry = b[4] - a[4], rz = b[2] - a[2];      // along the line
    const dx = c[3] - a[3], dy = c[4] - a[4], dz = c[2] - a[2];      // down the head
    let nx = dy * rz - dz * ry, ny = dz * rx - dx * rz, nz = dx * ry - dy * rx;
    const len = Math.hypot(nx, ny, nz) + 1e-9; nx /= len; ny /= len; nz /= len;
    if (nz < 0) return 0.10 * (1 + nz);                               // the far side, faint
    const lit = Math.max(0, nx * LIGHT[0] + ny * LIGHT[1] + nz * LIGHT[2]);
    return 0.10 + 0.62 * lit ** 1.3 + 0.28 * nz ** 3;
  };
  const addRow = (row, below, weight, i) => {
    const shift = glitch && i % 7 === 3 ? (Math.random() - 0.5) * 18 : 0;
    for (let j = 0; j < C; j++) {
      const a = row[j], bnext = row[(j + 1) % C], d = below ? below[j] : null;
      const f = d ? shade(a, bnext, d) : 0.3;
      const depthFade = 0.45 + 0.55 * smooth(-0.6, 0.6, a[2]);
      let light = f * depthFade * weight;
      if (scanY >= 0 && f > 0.15 && Math.abs(i / R - scanY) < 0.04) light = Math.min(1, light + 0.45);
      const k = Math.max(0, Math.min(BUCKETS - 1, Math.floor(light * BUCKETS)));
      if (light < 0.03) continue;
      paths[k].moveTo(a[0] + shift, a[1]); paths[k].lineTo(bnext[0] + shift, bnext[1]);
    }
  };
  for (let i = 0; i < R; i++) addRow(rows[i], rows[i + 1] || null, 1, i);
  for (let i = 0; i < NECK.length; i++) addRow(neck[i], neck[i + 1] || null, 0.4 * (1 - i / NECK.length), -1);
  // A few lines from crown to chin, faint, so it reads as a solid.
  const vertical = new Path2D();
  for (let j = 0; j < C; j += 5) {
    const p0 = rows[0][j]; if (p0[2] < -0.2) continue;
    vertical.moveTo(p0[0], p0[1]);
    for (let i = 1; i < R; i++) { const p = rows[i][j]; if (p[2] < -0.2) { vertical.moveTo(p[0], p[1]); continue; } vertical.lineTo(p[0], p[1]); }
  }
  const flicker = glitch ? 0.6 + Math.random() * 0.4 : 0.94 + Math.random() * 0.06;
  ctx.lineCap = 'round';
  ctx.lineWidth = 0.8; ctx.strokeStyle = rgba(0.10 * flicker); ctx.stroke(vertical);
  for (let k = 0; k < BUCKETS; k++) {
    const a = (k + 1) / BUCKETS;
    ctx.lineWidth = 3.2; ctx.strokeStyle = rgba(0.07 * a * flicker); ctx.stroke(paths[k]);   // glow
    ctx.lineWidth = 1.05; ctx.strokeStyle = rgba(0.85 * a * flicker); ctx.stroke(paths[k]);  // line
  }

  // The lips and the mouth between them.
  const P = (x, y, lift = 0.012) => project(deform([x, y, frontZ(x, y) + lift], open), yaw, pitch, scale, cx, cy);
  const half = 0.19 - open * 0.015;
  const upper = [], lower = [];
  // Lips: the corners stay together and the middle parts, as a real mouth does.
  const lens = u => Math.pow(Math.max(0, 1 - u * u), 0.75);
  const Q = (x, y) => project([x, y, frontZ(x, y) + 0.012], yaw, pitch, scale, cx, cy);
  for (let s = 0; s <= 24; s++) {
    const x0 = -half + 2 * half * s / 24, u = x0 / half, x = x0;
    const level = -0.034 * u * u;   // seen from a little above, a level mouth looks like a smile
    upper.push(Q(x, MOUTH_Y + 0.004 + 0.006 * Math.cos(u * Math.PI / 2) - 0.004 * g(u, 0, 0, 0, 0.25, 1) + open * 0.016 * lens(u) + level));
    lower.push(Q(x, MOUTH_Y - 0.003 - 0.004 * Math.cos(u * Math.PI / 2) - open * 0.1 * lens(u) + level));
  }
  ctx.globalCompositeOperation = 'source-over';
  ctx.fillStyle = 'rgba(2,8,9,.85)'; ctx.beginPath();
  upper.forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]));
  [...lower].reverse().forEach(p => ctx.lineTo(p[0], p[1])); ctx.fill();
  ctx.globalCompositeOperation = 'lighter';
  for (const [line, a] of [[upper, 0.95], [lower, 0.8]]) {
    ctx.beginPath(); line.forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]));
    ctx.lineWidth = 4; ctx.strokeStyle = rgba(0.12 * flicker); ctx.stroke();
    ctx.lineWidth = 1.4; ctx.strokeStyle = rgba(a * flicker); ctx.stroke();
  }

  // The eyes: lids that blink, and lit irises that look about.
  for (const side of [-1, 1]) {
    const ex = side * 0.29, ey = 0.125, wide = 0.105, tall = 0.05 * lid * (state === 'listening' ? 1.12 : 1);
    const top = [], bottom = [];
    for (let s = 0; s <= 20; s++) {
      const u = -1 + 2 * s / 20, x = ex + u * wide, bend = 1 - u * u;
      const tilt = side * u * 0.008;
      top.push(P(x, ey + tall * bend + tilt, 0.03)); bottom.push(P(x, ey - tall * 0.65 * bend + tilt, 0.03));
    }
    ctx.globalCompositeOperation = 'source-over';
    ctx.fillStyle = 'rgba(2,10,11,.8)'; ctx.beginPath();
    top.forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]));
    [...bottom].reverse().forEach(p => ctx.lineTo(p[0], p[1])); ctx.fill();
    ctx.globalCompositeOperation = 'lighter';
    if (lid > 0.35) {
      ctx.save(); ctx.beginPath();
      top.forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]));
      [...bottom].reverse().forEach(p => ctx.lineTo(p[0], p[1])); ctx.clip();
      const iris = P(ex + gaze[0] * 0.035, ey + gaze[1] * 0.018 + 0.004, 0.035);
      const rad = scale * 0.042;
      const glow = ctx.createRadialGradient(iris[0], iris[1], 0, iris[0], iris[1], rad * 1.6);
      glow.addColorStop(0, rgba(0.95)); glow.addColorStop(0.35, rgba(0.55)); glow.addColorStop(1, rgba(0));
      ctx.fillStyle = glow; ctx.beginPath(); ctx.arc(iris[0], iris[1], rad * 1.6, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = rgba(0.9); ctx.lineWidth = 1.2; ctx.beginPath(); ctx.arc(iris[0], iris[1], rad, 0, Math.PI * 2); ctx.stroke();
      ctx.fillStyle = 'rgba(255,255,255,.9)'; ctx.beginPath(); ctx.arc(iris[0] - rad * 0.3, iris[1] - rad * 0.3, rad * 0.18, 0, Math.PI * 2); ctx.fill();
      ctx.restore();
    }
    for (const [line, a] of [[top, 0.95], [bottom, 0.55]]) {
      ctx.beginPath(); line.forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]));
      ctx.lineWidth = 1.3; ctx.strokeStyle = rgba(a * flicker); ctx.stroke();
    }
  }

  // Thinking: a ring of light turning about the head.
  if (state === 'thinking') {
    // Drawn in short pieces: bright in front of the head, faint where it passes behind.
    ctx.lineWidth = 1.5;
    const ox = cx, oy = cy - scale * 0.05, rx = scale * 1.15, ry = scale * 0.2;
    for (let k = 0; k < 3; k++) {
      const a0 = t * 1.8 + k * 2.1;
      for (let s = 0; s < 22; s++) {
        const a = a0 + 1.1 * s / 22, b2 = a0 + 1.1 * (s + 1) / 22;
        const front = Math.sin(a) > 0, inside = Math.abs(Math.cos(a)) < 0.68;
        ctx.strokeStyle = rgba(front ? 0.6 : inside ? 0.04 : 0.3);
        ctx.beginPath(); ctx.moveTo(ox + Math.cos(a) * rx, oy + Math.sin(a) * ry);
        ctx.lineTo(ox + Math.cos(b2) * rx, oy + Math.sin(b2) * ry); ctx.stroke();
      }
    }
  }
  ctx.globalCompositeOperation = 'source-over';
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

// ------------------------------------------------------------------ the rest
function tick() { $('#clock').textContent = new Date().toLocaleTimeString('en-GB'); }
setInterval(tick, 1000); tick();
function full() { document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen().catch(() => {}); }
$('#full').addEventListener('click', full);
addEventListener('keydown', e => { if ((e.key === 'f' || e.key === 'F') && document.activeElement !== $('#prompt')) full(); });
