const $ = selector => document.querySelector(selector);
const icons = () => window.lucide?.createIcons();
let state = null, activeJob = null, filter = 'all', deleteId = null, mode = 'standby', busyPoll = false;
// The voice loop's live picture (Day 24): its state, microphone levels and subtitles.
let live = {running: false, state: 'off', levels: [], lines: []}, livePolling = false;
const LIVE_MODES = {listening: 'listening', thinking: 'thinking', speaking: 'speaking', idle: 'standby'};
let voiceEnabled = localStorage.getItem('jarvis-voice') === 'true';
let recognition = null, listening = false, lastEvents = [], toastTimer;
const notified = new Set();
const esc = text => String(text ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));

function toast(message) { $('#toast').textContent = message; $('#toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').hidden = true, 5000); }
async function api(path, body) {
  const response = await fetch('/api/' + path, body ? {method:'POST', headers:{'Content-Type':'application/json','X-Jarvis-Token':state?.token || ''}, body:JSON.stringify(body)} : {});
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || 'Request failed');
  return value;
}
function setMode(next) {
  mode = next;
  const names = {standby:['STANDBY','Ready for your next request','AWAITING INPUT'],thinking:['PROCESSING','Working on your request','REASONING'],speaking:['SPEAKING','Response in progress','VOICE ACTIVE'],listening:['LISTENING','Microphone is active','LISTENING'],error:['ATTENTION','Review the response below','NEEDS ATTENTION']};
  const words = names[next] || names.standby;
  $('#stateLabel').textContent = words[0]; $('#stateDetail').textContent = words[1]; $('#coreState').textContent = words[2];
}
function view(name) {
  document.querySelectorAll('.view').forEach(el => el.classList.toggle('active', el.id === name));
  document.querySelectorAll('.nav').forEach(el => el.classList.toggle('active', el.dataset.view === name));
  history.replaceState(null, '', '#' + name);
  if (name === 'settings' && state) populateSettings();
  if (name === 'memory') renderRecords();
  if (name === 'activity') renderActivity();
}
document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => view(button.dataset.view)));
document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => document.getElementById(button.dataset.close).close()));

function message(role, text, error = false) {
  $('#messages .welcome')?.remove();
  const article = document.createElement('article'); article.className = `message ${role} ${error ? 'error' : ''}`;
  article.innerHTML = `<header><i data-lucide="${role === 'user' ? 'user-round' : 'sparkles'}"></i>${role === 'user' ? 'YOU' : 'JARVIS'}</header><p></p>`;
  article.querySelector('p').textContent = text; $('#messages').append(article); icons(); $('#messages').scrollTop = $('#messages').scrollHeight;
}
// Speech runs on the server, not in the browser: Windows has no Sinhala voice, so the
// browser physically cannot say a Sinhala reply. The server uses si-LK-SameeraNeural.
// The browser voice stays only as a fallback for when the server has no audio path.
function speak(text) {
  if (!voiceEnabled || !text) return;
  if (state?.voice?.available) {
    setMode('speaking');
    api('speak', {text}).catch(error => { toast('Speech: ' + error.message); browserSpeak(text); })
      .finally(() => { if (!activeJob && !listening && mode === 'speaking') setMode('standby'); });
    return;
  }
  browserSpeak(text);
}
function browserSpeak(text) {
  if (!window.speechSynthesis) return;
  speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text.slice(0, 3500));
  utterance.rate = Number(localStorage.getItem('jarvis-rate') || 1);
  utterance.voice = speechSynthesis.getVoices().find(v => v.voiceURI === localStorage.getItem('jarvis-voice-id')) || null;
  utterance.onstart = () => setMode('speaking');
  utterance.onend = utterance.onerror = () => { if (!activeJob && !listening) setMode('standby'); };
  speechSynthesis.speak(utterance);
}
function stopSpeaking() {
  window.speechSynthesis?.cancel();
  if (state?.voice?.available) api('speak/stop', {}).catch(() => {});
}
function updateVoice() {
  localStorage.setItem('jarvis-voice', String(voiceEnabled));
  $('#voiceToggle').setAttribute('aria-pressed', String(voiceEnabled));
  $('#voiceToggle').title = voiceEnabled ? 'Mute spoken responses' : 'Enable spoken responses';
  $('#voiceToggle').setAttribute('aria-label', $('#voiceToggle').title);
  $('#voiceToggle').innerHTML = `<i data-lucide="${voiceEnabled ? 'volume-2' : 'volume-x'}"></i>`;
  $('#speakSetting').checked = voiceEnabled;
  if (!voiceEnabled) { stopSpeaking(); if (mode === 'speaking') setMode('standby'); }
  // Persist on the server too, so JARVIS keeps speaking when no browser is open.
  if (state?.token) api('settings', {voice_enabled: voiceEnabled}).catch(() => {});
  icons();
}
$('#voiceToggle').onclick = () => { voiceEnabled = !voiceEnabled; updateVoice(); };
$('#speakSetting').onchange = event => { voiceEnabled = event.target.checked; updateVoice(); };
$('#voiceRate').value = localStorage.getItem('jarvis-rate') || 1;
$('#rateLabel').textContent = Number($('#voiceRate').value).toFixed(1);
$('#voiceRate').oninput = event => { localStorage.setItem('jarvis-rate', event.target.value); $('#rateLabel').textContent = Number(event.target.value).toFixed(1); };
function voiceOptions() {
  const voices = window.speechSynthesis?.getVoices() || [];
  $('#voiceSelect').innerHTML = '<option value="">System default</option>' + voices.map(v => `<option value="${esc(v.voiceURI)}">${esc(v.name)} (${esc(v.lang)})</option>`).join('');
  $('#voiceSelect').value = localStorage.getItem('jarvis-voice-id') || '';
}
window.speechSynthesis?.addEventListener('voiceschanged', voiceOptions);
$('#voiceSelect').onchange = event => localStorage.setItem('jarvis-voice-id', event.target.value);
$('#testVoice').onclick = () => { voiceEnabled = true; updateVoice(); speak('At your service. What shall we work on today?'); };
$('#micButton').onclick = () => {
  if (listening) { recognition?.stop(); return; }
  if (!('SpeechRecognition' in window || 'webkitSpeechRecognition' in window)) return toast('Voice input is not available in this browser. Try Chrome or Edge.');
  if (sessionStorage.getItem('jarvis-mic-consent') !== 'yes') $('#voiceDialog').showModal();
  else startListening();
};
$('#allowVoice').onclick = () => { sessionStorage.setItem('jarvis-mic-consent', 'yes'); $('#voiceDialog').close(); startListening(); };
function startListening() {
  if (activeJob) return toast('Stop the active request before recording another.');
  stopSpeaking();
  const Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
  recognition = new Speech(); recognition.lang = 'en-US'; recognition.interimResults = true;
  recognition.onstart = () => { listening = true; setMode('listening'); $('#inputStatus').textContent = 'LISTENING'; $('#micButton').style.color = 'var(--red)'; };
  recognition.onresult = event => { $('#prompt').value = Array.from(event.results).map(result => result[0].transcript).join(' '); };
  recognition.onerror = event => toast('Microphone: ' + event.error);
  recognition.onend = () => { listening = false; setMode(activeJob ? 'thinking' : 'standby'); $('#inputStatus').textContent = 'REVIEW & SEND'; $('#micButton').style.color = ''; };
  try { recognition.start(); } catch (error) { toast(error.message); }
}
async function submit(prompt) {
  if (activeJob || !prompt.trim()) return;
  recognition?.stop(); stopSpeaking();
  $('#sendButton').disabled = true;
  try {
    const result = await api('chat', {prompt});
    activeJob = result.id; message('user', prompt); $('#prompt').value = ''; setMode('thinking');
    $('#working').hidden = false; $('#stopButton').hidden = false; $('#inputStatus').textContent = 'REQUEST IN PROGRESS';
    streamText('');
    pollJob();
  } catch (error) { toast(error.message); $('#sendButton').disabled = false; }
}
$('#chatForm').onsubmit = event => { event.preventDefault(); submit($('#prompt').value); };
$('#prompt').onkeydown = event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); submit($('#prompt').value); } };
$('#stopButton').onclick = async () => { stopSpeaking(); recognition?.stop(); if (activeJob) { try { await api('stop', {id:activeJob}); } catch (error) { toast(error.message); } } };
// Shows the model's words as they stream in, so a long answer is never a blank wait.
function streamText(text) {
  const node = $('#streaming');
  if (!text) { node.hidden = true; node.textContent = ''; return; }
  const atBottom = node.scrollHeight - node.scrollTop - node.clientHeight < 40;
  node.hidden = false; node.textContent = text;
  if (atBottom) node.scrollTop = node.scrollHeight;
}
async function pollLive() {
  if (livePolling || document.hidden) return; livePolling = true;
  try {
    live = await api('live');
    $('#voiceState').textContent = live.running ? live.state.toUpperCase() : 'VOICE LOOP OFF';
    $('#voiceState').dataset.state = live.running ? live.state : 'off';
    if (live.running && !activeJob) setMode(LIVE_MODES[live.state] || 'standby');
    renderSubtitles();
  } catch (error) { live = {running: false, state: 'off', levels: [], lines: []}; }
  finally { livePolling = false; }
}
// What it heard and what it said, both on screen, so a mishearing is always visible.
let lastLines = '';
function renderSubtitles() {
  const lines = (live.lines || []).slice(-4), key = JSON.stringify(lines);
  if (key === lastLines) return; lastLines = key;
  if (!lines.length) return;
  $('#subtitles').innerHTML = lines.map(l => `<p class="line ${l.who}"><span>${l.who === 'you' ? 'HEARD' : l.unprompted ? 'JARVIS, UNPROMPTED' : 'JARVIS'} <time>${esc(l.at)}</time></span>${esc(l.text)}</p>`).join('');
}
// The newest request's steps, as they happen.
function renderJournal(job) {
  job = job || (state?.jobs || []).at(-1);
  if (!job) return;
  $('#journalTag').textContent = job.state.toUpperCase();
  $('#liveJournal').innerHTML = job.events.slice(-6).map(e => `<li class="${e.state}"><time>${esc(e.time)}</time><b>${esc(e.label)}</b><span>${esc(String(e.detail).slice(0, 140))}</span></li>`).join('');
}
async function pollJob() {
  if (!activeJob) return;
  try {
    const job = await api('jobs/' + activeJob); lastEvents = job.events;
    $('#workingLabel').textContent = job.events.at(-1)?.label || 'Thinking';
    $('#stepCount').textContent = String(job.events.filter(e => e.label === 'Tool completed').length).padStart(2, '0') + ' OPERATIONS';
    if (state) { state.jobs = state.jobs.filter(j => j.id !== job.id); state.jobs.push(job); }
    renderActivity(); renderJournal(job);
    streamText(job.partial);
    if (['done','error','cancelled'].includes(job.state)) {
      activeJob = null; $('#working').hidden = true; $('#stopButton').hidden = true; $('#sendButton').disabled = false;
      streamText('');
      $('#inputStatus').textContent = 'READY WHEN YOU ARE'; message('assistant', job.answer, job.state === 'error');
      setMode(job.state === 'error' ? 'error' : 'standby'); if (job.state === 'done') speak(job.answer);
      refresh(); return;
    }
  } catch (error) { $('#workingLabel').textContent = 'Connection interrupted. Retrying...'; }
  setTimeout(pollJob, 250);
}
document.querySelectorAll('[data-prompt]').forEach(button => button.onclick = () => {
  view('console'); const prompt = button.dataset.prompt;
  if (prompt.endsWith(' ')) { $('#prompt').value = prompt; $('#prompt').focus(); } else submit(prompt);
});
function recordForm(kind = 'note') { $('#recordForm').reset(); $('#recordKind').value = kind; recordFields(); $('#recordDialog').showModal(); $('#recordTitle').focus(); }
function recordFields() { const reminder = $('#recordKind').value === 'reminder'; $('#dueField').hidden = !reminder; $('#recordDue').required = reminder; $('#contentField').hidden = reminder; }
$('#recordKind').onchange = recordFields; $('#quickNote').onclick = () => recordForm(); $('#quickReminder').onclick = () => recordForm('reminder'); $('#addRecord').onclick = () => recordForm();
$('#recordForm').onsubmit = async event => {
  event.preventDefault();
  try {
    const due = $('#recordKind').value === 'reminder' ? new Date($('#recordDue').value).toISOString() : null;
    if (due && new Date(due) <= new Date()) throw new Error('Choose a future reminder time.');
    await api('records', {kind:$('#recordKind').value, title:$('#recordTitle').value.trim(), content:$('#recordContent').value, due});
    $('#recordDialog').close(); toast('Record saved.'); await refresh();
  } catch (error) { toast(error.message); }
};
function renderRecords() {
  if (!state) return;
  const query = $('#recordSearch').value.toLowerCase();
  const learned = r => r.kind === 'memory' && (r.content || '').startsWith('Heard on');
  const records = state.records.filter(r => (filter === 'all' || r.kind === filter || (filter === 'learned' && learned(r))) && (r.title + r.content).toLowerCase().includes(query));
  $('#recordList').innerHTML = records.length ? records.map(r => `<article class="record"><header><span>${learned(r) ? 'LEARNED FROM YOU' : r.kind.toUpperCase()}</span><i data-lucide="${r.kind === 'note' ? 'notebook' : r.kind === 'reminder' ? 'bell' : 'brain-circuit'}"></i></header><h3>${esc(r.title)}</h3><p>${esc(r.content)}</p><footer><span>${r.done ? 'Completed' : r.due ? esc(new Date(r.due).toLocaleString()) : 'Saved on this device'}</span>${r.kind === 'reminder' && !r.done ? `<button class="icon-button" data-complete="${r.id}" title="Complete reminder" aria-label="Complete reminder"><i data-lucide="check"></i></button>` : ''}<button class="icon-button" data-delete="${r.id}" title="Delete record" aria-label="Delete ${esc(r.title)}"><i data-lucide="trash-2"></i></button></footer></article>`).join('') : '<p class="empty">No records here yet.</p>';
  icons();
}
document.querySelectorAll('[data-filter]').forEach(button => button.onclick = () => { filter = button.dataset.filter; document.querySelectorAll('[data-filter]').forEach(b => b.classList.toggle('selected', b === button)); renderRecords(); });
$('#recordSearch').oninput = renderRecords;
document.addEventListener('click', async event => {
  const button = event.target.closest('[data-delete],[data-complete]'); if (!button) return;
  if (button.dataset.delete) { deleteId = button.dataset.delete; $('#deleteTitle').textContent = state.records.find(r => r.id === deleteId)?.title; $('#deleteDialog').showModal(); }
  else { try { await api('records/update', {id:button.dataset.complete, action:'complete'}); await refresh(); } catch(error) { toast(error.message); } }
});
$('#confirmDelete').onclick = async () => { try { await api('records/update', {id:deleteId, action:'delete'}); $('#deleteDialog').close(); refresh(); } catch(error) { toast(error.message); } };
// Skills JARVIS wrote. Approval is here, after reading, and never by voice.
async function refreshSkills() {
  let data;
  try { data = await api('skills'); } catch (error) { return; }
  $('#pendingSkills').innerHTML = data.pending.length ? data.pending.map(s => `<section class="job"><h3>${esc(s.name)} <span class="tiny-tag">${s.problems.length ? 'refused by the check' : 'not reviewed'}</span></h3>${s.problems.length ? `<p class="help">${s.problems.map(esc).join('<br>')}</p>` : ''}<pre class="skill-code">${esc(s.code)}</pre><div class="dialog-actions"><button class="secondary" data-skill-reject="${esc(s.name)}">Reject</button>${s.problems.length ? '' : `<button class="primary" data-skill-approve="${esc(s.name)}">I have read it. Test and approve</button>`}</div></section>`).join('') : '<p class="empty">Nothing waiting. Say "make a new tool that..." and it will appear here.</p>';
  $('#activeSkills').innerHTML = data.active.length ? data.active.map(s => `<section class="job"><h3>${esc(s.title_en)} <span class="tiny-tag">${esc(s.name)}</span></h3><p class="help">${esc(s.description)}<br>Say: ${(s.phrases || []).slice(0, 3).map(esc).join(' &middot; ')}</p></section>`).join('') : '<p class="empty">No approved skills yet.</p>';
}
document.addEventListener('click', async event => {
  const approve = event.target.closest('[data-skill-approve]')?.dataset.skillApprove;
  const reject = event.target.closest('[data-skill-reject]')?.dataset.skillReject;
  try {
    if (approve) {
      const result = await api('skills/approve', {name: approve});
      toast(result.approved ? 'Approved: its tests passed, and it is in use now.' : 'Not approved: ' + (result.problems || (result.tests || []).filter(t => !t[0]).map(t => t[1])).join('; '));
    }
    if (reject) { await api('skills/reject', {name: reject, reason: 'rejected in the console'}); toast('Rejected and deleted.'); }
  } catch (error) { toast(error.message); }
  if (approve || reject) refreshSkills();
});
// Multi-step tasks run in the voice loop; the console reads their journals and can stop them.
function renderTasks(tasks) {
  const bad = ['failed', 'skipped', 'not run'];
  $('#taskList').innerHTML = tasks.length ? tasks.map(t => `<section class="job"><h3>${esc(t.request)} <span class="tiny-tag">${esc(t.state)}</span> ${['running','needs_you'].includes(t.state) ? `<button class="stop-button" data-stop-task="${esc(t.id)}">Stop</button>` : ''}</h3>${t.question ? `<p class="help">${esc(t.question)}</p>` : ''}${t.steps.map(s => `<details class="event ${bad.includes(s.state) ? 'error' : ''}"><summary><time>${esc(String(s.number))}</time>${esc(s.step)} &middot; ${esc(s.state)}</summary><pre>${esc(s.error || JSON.stringify(s.result ?? '', null, 1))}</pre></details>`).join('')}</section>`).join('') : '<p class="empty">Ask for several things in one sentence and the plan appears here.</p>';
}
async function refreshTasks() {
  try { renderTasks((await api('tasks')).tasks || []); } catch (error) { /* the console still works without them */ }
}
document.addEventListener('click', event => {
  const id = event.target.closest('[data-stop-task]')?.dataset.stopTask;
  if (id) api('tasks/stop', {id}).then(() => toast('Stopping after the current step.')).catch(() => toast('Could not stop the task.'));
});
function renderActivity() {
  if (!state) return;
  const jobs = [...state.jobs].reverse();
  $('#activityList').innerHTML = jobs.length ? jobs.map(job => `<section class="job"><h3>${esc(job.events[0]?.detail || 'Request')} <span class="tiny-tag">${esc(job.state)}</span></h3>${job.events.map(e => `<details class="event ${e.state}"><summary><time>${esc(e.time)}</time>${esc(e.label)}</summary><pre>${esc(e.detail)}</pre></details>`).join('')}${job.reasoning ? `<details class="event"><summary>Full reasoning, ${job.reasoning.length.toLocaleString()} characters. Shown here, never spoken.</summary><pre>${esc(job.reasoning)}</pre></details>` : ''}</section>`).join('') : '<p class="empty">Your next request will appear here.</p>';
}
function populateSettings() {
  $('#modelSelect').innerHTML = '<option value="">Built-in tools only</option>' + state.ollama.models.map(model => `<option>${esc(model)}</option>`).join('');
  if (state.model && !state.ollama.models.includes(state.model)) $('#modelSelect').insertAdjacentHTML('beforeend', `<option>${esc(state.model)}</option>`);
  $('#modelSelect').value = state.model; $('#workspacePath').value = state.workspace;
  $('#modelHelp').textContent = state.ollama.online ? `${state.ollama.models.length} installed models. Multi-step requests need a model that supports tools.` : 'Ollama is offline. Start Ollama on this computer, then reopen Settings.';
}
$('#settingsForm').onsubmit = async event => { event.preventDefault(); try { await api('settings', {model:$('#modelSelect').value, workspace:$('#workspacePath').value}); toast('Settings saved.'); await refresh(); } catch(error) { toast(error.message); } };
function reminders() {
  if (!state) return;
  const pending = state.records.filter(r => r.kind === 'reminder' && r.due && !r.done).sort((a,b) => a.due.localeCompare(b.due));
  $('#nextReminder').textContent = pending[0]?.title || 'Nothing scheduled';
  for (const record of pending) {
    if (new Date(record.due) > new Date() || notified.has(record.id)) continue;
    notified.add(record.id);
    const notice = document.createElement('div'); notice.className = 'notification';
    notice.innerHTML = `<strong>REMINDER</strong><p>${esc(record.title)}</p><button>Mark complete</button>`;
    notice.querySelector('button').onclick = async () => { try { await api('records/update', {id:record.id, action:'complete'}); notice.remove(); refresh(); } catch(error) { toast(error.message); } };
    $('#notifications').append(notice); speak('Reminder. ' + record.title);
  }
}
async function refresh(first = false) {
  if (busyPoll) return; busyPoll = true;
  try {
    state = await api('state');
    for (const [key, prefix] of [['cpu','cpu'],['memory','ram'],['disk','disk']]) { const value = Math.round(state.system[key]); $('#' + prefix + 'Value').innerHTML = `${value}<small>%</small>`; $('#' + prefix + 'Bar').style.width = value + '%'; }
    $('#connectionText').textContent = state.ollama.online ? 'Ollama connected' : 'Ollama offline / local tools ready';
    $('#modelBadge').textContent = state.model || 'LOCAL TOOLS'; $('#activeModel').textContent = state.model || 'Built-in tools';
    $('#memoryCount').textContent = state.records.length + ' records'; $('#footerStatus').textContent = 'LOCAL CONNECTION';
    renderRecords(); reminders();
    if (first) {
      state.history.forEach(m => message(m.role, m.content));
      const running = state.jobs.find(j => ['running','stopping'].includes(j.state));
      if (running) { activeJob = running.id; $('#working').hidden = false; $('#stopButton').hidden = false; $('#sendButton').disabled = true; setMode('thinking'); pollJob(); }
      populateSettings(); renderActivity();
    }
    if (!activeJob) renderJournal();
  } catch(error) { $('#connectionText').textContent = 'Console server unavailable'; $('#footerStatus').textContent = 'DISCONNECTED'; if (first) toast('Cannot reach the local server.'); }
  finally { busyPoll = false; }
}
$('#exportChat').onclick = () => {
  const text = [...document.querySelectorAll('.message')].map(el => el.querySelector('header').textContent + '\n' + el.querySelector('p').textContent).join('\n\n');
  const url = URL.createObjectURL(new Blob([text], {type:'text/plain'})); const link = document.createElement('a'); link.href = url; link.download = 'jarvis-conversation.txt'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
};

// The core is drawn from geometry; its motion reflects the actual assistant state.
const canvas = $('#reactor'), context = canvas.getContext('2d');
let frame = 0;
function draw() {
  const rect = canvas.getBoundingClientRect();
  if (!rect.width) { requestAnimationFrame(draw); return; }
  const ratio = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(rect.width * ratio) || canvas.height !== Math.round(rect.height * ratio)) { canvas.width = Math.round(rect.width * ratio); canvas.height = Math.round(rect.height * ratio); }
  context.setTransform(ratio, 0, 0, ratio, 0, 0); context.clearRect(0,0,rect.width,rect.height);
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!reduced) frame += mode === 'thinking' ? .013 : .003;
  const cx = rect.width / 2, cy = rect.height / 2, r = Math.min(rect.width * .32, rect.height * .40);
  context.save(); context.translate(cx,cy);
  const ring = (radius,start,end,color,width=1) => { context.beginPath();context.arc(0,0,radius,start,end);context.strokeStyle=color;context.lineWidth=width;context.stroke(); };
  for (let i=0;i<120;i++) { const a=i*Math.PI/60;const inner=r*(i%5===0?1.08:1.12);context.beginPath();context.moveTo(Math.cos(a)*inner,Math.sin(a)*inner);context.lineTo(Math.cos(a)*r*1.15,Math.sin(a)*r*1.15);context.strokeStyle=i%5===0?'#618d8c':'#293b3c';context.lineWidth=1;context.stroke(); }
  ring(r*1.03,0,Math.PI*2,'#253c3d'); ring(r*.96,0,Math.PI*2,'#1b3031'); ring(r*.67,0,Math.PI*2,'#345856');
  for(let i=0;i<3;i++) { const a=frame+i*Math.PI*2/3;ring(r*.9,a,a+1.45,'#78d7d0',2);ring(r*.79,-a,-a+.62,'#bfab83',1.5); }
  // The inner ring is the real microphone: one segment per level, newest at the top.
  const levels = live.running && live.levels.length ? live.levels : null;
  for(let i=0;i<64;i++) { const a=i*Math.PI/32-Math.PI/2;const level=levels?levels[(levels.length-64+i+64*levels.length)%levels.length]:0;const wave=levels?level*.16:['speaking','listening'].includes(mode)?Math.sin(frame*18+i*.5)*.045:Math.sin(frame*3+i*.3)*.008;ring(r*(.56+wave),a,a+.045,level>.08?'#9ef0e8':'#517f7c',2); }
  // The state ring: what the voice loop is doing, in colour.
  const stateColour = {listening:'#72e4e2', thinking:'#dfb67d', speaking:'#7ee08a', idle:'#2d4446', off:'#4a2c32'}[live.running ? live.state : 'off'] || '#2d4446';
  const pulse = live.state === 'listening' ? (Math.sin(Date.now()/180)+1)/2 : 1;
  context.globalAlpha = .45 + .55*pulse; ring(r*1.2,0,Math.PI*2,stateColour,live.state === 'idle' ? 1.5 : 3); context.globalAlpha = 1;
  ring(r*.49,0,Math.PI*2,'#193032');
  const a=-frame*1.3;context.fillStyle='#e4c492';context.beginPath();context.arc(Math.cos(a)*r*.79,Math.sin(a)*r*.79,2.2,0,Math.PI*2);context.fill();
  context.restore(); requestAnimationFrame(draw);
}
draw(); updateVoice(); voiceOptions(); icons(); view(['console','memory','activity','settings'].includes(location.hash.slice(1)) ? location.hash.slice(1) : 'console');
refresh(true); setInterval(refresh,5000); pollLive(); setInterval(pollLive,150); setInterval(reminders,1000); refreshTasks(); setInterval(refreshTasks,3000); refreshSkills(); setInterval(refreshSkills,10000);
function updateClock() { $('#clock').textContent = new Date().toLocaleTimeString('en-GB'); }
updateClock(); setInterval(updateClock,1000);
