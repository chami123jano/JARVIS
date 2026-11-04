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

// Hold to talk. Safari records AAC in MP4; the laptop decodes it.
let recorder = null;
let chunks = [];
let stream = null;

async function startTalking(event) {
  event.preventDefault();
  unlock();
  if (recorder) return;
  try {
    stream = stream || await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    show('note', 'No microphone: allow it for this page in Settings, or type instead.');
    return;
  }
  chunks = [];
  const type = MediaRecorder.isTypeSupported('audio/mp4') ? 'audio/mp4' : '';
  recorder = new MediaRecorder(stream, type ? { mimeType: type } : {});
  recorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
  recorder.onstop = sendRecording;
  recorder.start();
  $('talk').classList.add('recording');
  $('talk').textContent = 'Listening… let go to send';
}

function stopTalking(event) {
  if (event) event.preventDefault();
  if (!recorder) return;
  recorder.stop();
  $('talk').classList.remove('recording');
  $('talk').textContent = 'Hold to talk';
}

async function sendRecording() {
  const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/mp4' });
  recorder = null;
  if (blob.size < 2000) return;        // a tap, not a sentence
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

const talk = $('talk');
talk.addEventListener('pointerdown', startTalking);
talk.addEventListener('pointerup', stopTalking);
talk.addEventListener('pointercancel', stopTalking);
talk.addEventListener('pointerleave', stopTalking);

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
    show('note', `Paired as ${result.name}. Hold the button and talk, or type.`);
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
