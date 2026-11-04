// JARVIS in your pocket (Day 25): the phone is a microphone, a keyboard and a speaker;
// the laptop does the hearing, the thinking and the acting.
'use strict';

const $ = (id) => document.getElementById(id);
const TOKEN_KEY = 'jarvis-token';
let token = '';
try { token = localStorage.getItem(TOKEN_KEY) || ''; } catch (e) { token = ''; }

function show(who, text) {
  const line = document.createElement('div');
  line.className = `line ${who}`;
  line.textContent = text;
  $('log').appendChild(line);
  $('log').scrollTop = $('log').scrollHeight;
}

function status(text, state) {
  $('status').textContent = text;
  $('dot').className = state || '';
}

function paired(yes) {
  document.body.className = yes ? 'paired' : 'unpaired';
}

async function api(path, options = {}) {
  const headers = Object.assign({}, options.headers || {});
  if (token) headers.Authorization = `Bearer ${token}`;
  let response;
  try {
    response = await fetch(path, Object.assign({}, options, { headers }));
  } catch (e) {
    status('Laptop not reachable', 'off');
    throw new Error('The laptop is not reachable. Is JARVIS running, and the phone on home Wi-Fi?');
  }
  if (response.status === 401) {
    token = '';
    try { localStorage.removeItem(TOKEN_KEY); } catch (e) { /* private mode */ }
    paired(false);
    throw new Error('This phone is no longer paired.');
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `JARVIS answered ${response.status}`);
  return body;
}

// iOS plays audio only after a touch has unlocked it: a silent play inside the gesture.
const voice = $('voice');
let unlocked = false;
function unlock() {
  if (unlocked) return;
  unlocked = true;
  voice.src = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=';
  voice.play().catch(() => { unlocked = false; });
}

async function play(path) {
  if (!path) return;
  try {
    const response = await fetch(path, { headers: { Authorization: `Bearer ${token}` } });
    if (!response.ok) return;
    const url = URL.createObjectURL(await response.blob());
    voice.src = url;
    voice.onended = () => URL.revokeObjectURL(url);
    await voice.play();
  } catch (e) { /* the text is on screen either way */ }
}

async function answered(result) {
  if (result.heard) show('you', result.heard);
  show('jarvis', result.reply || '…');
  status('Connected', 'on');
  await play(result.audio);
}

$('ask').addEventListener('submit', async (event) => {
  event.preventDefault();
  unlock();
  const text = $('text').value.trim();
  if (!text) return;
  $('text').value = '';
  status('Thinking…', 'on');
  try {
    const result = await api('/api/ask', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }) });
    result.heard = text;
    await answered(result);
  } catch (e) {
    show('note', e.message);
    $('text').value = text;            // kept, to send again -- never resent on its own
  }
});

// Tap to talk, tap to send. Safari records AAC in MP4; the laptop decodes it. It was hold
// to talk, and of several tries only one reached the laptop (5 Nov): a finger that moves a
// little ends a hold, and a recording cut that short was dropped without a word.
let recorder = null;
let chunks = [];
let stream = null;
let started = 0;
let ticking = null;
const LONGEST = 30000;

async function startTalking() {
  unlock();
  if (recorder) return;
  // A fresh microphone every time. Measured 5 Nov: the first recording worked and every
  // one after it came back empty -- once the page has played JARVIS's reply, iOS has moved
  // the audio over to playback and the microphone it gave earlier is silent.
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    show('note', 'No microphone: allow it for this page in Settings, or type instead.');
    return;
  }
  chunks = [];
  const type = MediaRecorder.isTypeSupported('audio/mp4') ? 'audio/mp4' : '';
  recorder = new MediaRecorder(stream, type ? { mimeType: type } : {});
  recorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
  recorder.onstop = sendRecording;
  recorder.start(1000);                // a piece every second, not one at the very end
  started = Date.now();
  $('talk').classList.add('recording');
  const tick = () => {
    const seconds = Math.floor((Date.now() - started) / 1000);
    $('talk').textContent = `Listening… ${seconds}s — tap to send`;
    if (Date.now() - started >= LONGEST) stopTalking();
  };
  tick();
  ticking = setInterval(tick, 500);
}

function stopTalking() {
  if (!recorder) return;
  clearInterval(ticking);
  recorder.stop();
  $('talk').classList.remove('recording');
  $('talk').textContent = 'Tap to talk';
}

async function sendRecording() {
  const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/mp4' });
  const length = Date.now() - started;
  recorder = null;
  if (stream) {
    stream.getTracks().forEach((track) => track.stop());   // the microphone is let go
    stream = null;
  }
  if (length < 700) {
    show('note', 'That was too short. Tap, say it, then tap again.');
    return;
  }
  if (blob.size < 2000) {
    show('note', `The microphone gave no sound for those ${Math.round(length / 1000)}s. ` +
                 'Tap and try again.');
    return;
  }
  $('talk').disabled = true;
  status('Hearing…', 'on');
  try {
    await answered(await api('/api/voice', {
      method: 'POST', headers: { 'Content-Type': blob.type }, body: blob }));
  } catch (e) {
    show('note', e.message);
  } finally {
    $('talk').disabled = false;
  }
}

$('talk').addEventListener('click', () => (recorder ? stopTalking() : startTalking()));

$('pairbtn').addEventListener('click', async () => {
  unlock();
  $('pairmsg').textContent = '';
  try {
    const result = await api('/api/pair', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: $('code').value, name: $('name').value }) });
    token = result.token;
    try { localStorage.setItem(TOKEN_KEY, token); } catch (e) { /* kept for this visit */ }
    paired(true);
    show('note', `Paired as ${result.name}. Tap the button, talk, and tap again -- or type.`);
    status('Connected', 'on');
  } catch (e) {
    $('pairmsg').textContent = e.message;
  }
});

async function ping() {
  if (!token) return;
  try {
    await api('/api/ping');
    status('Connected', 'on');
  } catch (e) { /* status already set */ }
}

paired(Boolean(token));
if (token) { ping(); setInterval(ping, 20000); } else { status('Not paired', ''); }
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
