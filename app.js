/* ============================================================
   FRIDAY — App (Frontend lógica principal)
   ============================================================ */

const $ = (id) => document.getElementById(id);

window.fridayState = 'idle';
window.listening = false;
window.speaking = false;
window.ytPlayer = null;

let recognition = null, lastReply = "";
let currentUtterance = null, speechRunId = 0;
let lockoutInterval = null, lockoutEndsAt = 0, silenceTimer = null;
let isProcessing = false, sysChart = null, sysPollTimer = null;
let wakeWordRecognition = null, wakeWordActive = false;
let wakeWordHits = 0, wakeWordLastHit = 0;
let currentAttachment = null, currentAttachmentType = null, currentAttachmentName = null;
let statusPollingInterval = null, deferredPrompt = null, availableVoices = [];
let bootTime = Date.now();
let bootRunning = false;
let assistantActivated = false;
let nativeVoiceToastShown = false;

let currentAbortController = null;
let thinkingTimer = null;
let thinkingStart = 0;
let currentToolCard = null;
let ambienceNodes = null;
let lastAIResponseTime = 0;
let audioCtx = null;

let bgScene, bgCamera, bgRenderer, particlesMesh, linesMesh;

const MONTHS_PT = ['JAN','FEV','MAR','ABR','MAI','JUN','JUL','AGO','SET','OUT','NOV','DEZ'];
const WEEKDAYS_PT = ['DOM','SEG','TER','QUA','QUI','SEX','SÁB'];

function safeMarkedParse(text) {
  try {
    const html = marked.parse(text);
    if (typeof DOMPurify !== 'undefined') return DOMPurify.sanitize(html);
    return html;
  } catch (e) { return String(text); }
}

const recentToasts = new Map();
const TOAST_SESSION_KEY = 'friday_toast_dedup';
function _loadToastDedup() {
  try { return JSON.parse(sessionStorage.getItem(TOAST_SESSION_KEY) || '{}'); } catch(e){ return {}; }
}
function _saveToastDedup(obj) {
  try { sessionStorage.setItem(TOAST_SESSION_KEY, JSON.stringify(obj)); } catch(e){}
}

window.showToast = function(text, duration = 5000) {
  const container = $('toastContainer');
  if (!container) return;
  const now = Date.now();
  const persistent = _loadToastDedup();
  if (persistent[text] && now - persistent[text] < 30000) return;
  const lastSeen = recentToasts.get(text) || 0;
  if (now - lastSeen < 3000) return;
  recentToasts.set(text, now);
  persistent[text] = now;
  for (const k in persistent) { if (now - persistent[k] > 300000) delete persistent[k]; }
  _saveToastDedup(persistent);
  if (container.children.length >= 3) { const oldest = container.firstChild; if (oldest) oldest.remove(); }
  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.textContent = text;
  const firstChar = String(text).trim().charAt(0);
  if (/[\u{1F300}-\u{1F9FF}\u{1F600}-\u{1F64F}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}]/u.test(firstChar)) toast.classList.add('has-icon');
  let dismissed = false; let timer;
  const dismiss = () => {
    if (dismissed) return; dismissed = true;
    clearTimeout(timer); toast.classList.remove('show'); toast.classList.add('hide');
    setTimeout(() => toast.remove(), 500);
  };
  toast.addEventListener('click', dismiss);
  container.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add('show'));
  try { window.playTone(880, 'sine', 0.08, 0.025); } catch(e) {}
  timer = setTimeout(dismiss, duration);
};

function buildDayStrip() {
  const now = new Date();
  const days = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
  let html = '';
  for (let i = 1; i <= days; i++) {
    const active = i === now.getDate() ? ' active' : '';
    html += `<span class="day-num${active}">${String(i).padStart(2,'0')}</span>`;
  }
  $('topDays').innerHTML = html;
  requestAnimationFrame(() => {
    const el = document.querySelector('.day-num.active');
    if (el) { try { el.scrollIntoView({inline:'center',block:'nearest',behavior:'smooth'}); } catch(e) { el.scrollIntoView(); } }
  });
}

function updateClocks() {
  const now = new Date();
  const hh = String(now.getHours()).padStart(2,'0');
  const mm = String(now.getMinutes()).padStart(2,'0');
  const ss = String(now.getSeconds()).padStart(2,'0');
  $('topTime').textContent = `${hh}:${mm}:${ss}`;
  $('clockMonth').textContent = MONTHS_PT[now.getMonth()];
  $('clockDay').textContent = now.getDate();
  $('clockWeekday').textContent = WEEKDAYS_PT[now.getDay()];
  $('rightDate').textContent = `${WEEKDAYS_PT[now.getDay()]}, ${String(now.getDate()).padStart(2,'0')} ${MONTHS_PT[now.getMonth()]}`;
  const elapsed = Math.floor((Date.now() - bootTime) / 1000);
  $('uptime').textContent = [Math.floor(elapsed/3600), Math.floor((elapsed%3600)/60), elapsed%60].map(n => String(n).padStart(2,'0')).join(':');
}
buildDayStrip(); updateClocks(); setInterval(updateClocks, 1000);

const sparkHistory = { cpu: [], ram: [] };
const SPARK_MAX = 30;

function pushSpark(key, v) { const arr = sparkHistory[key]; arr.push(v); if (arr.length > SPARK_MAX) arr.shift(); }

function drawSparkline(canvasId, values, color) {
  const c = $(canvasId); if (!c) return;
  const ctx = c.getContext('2d');
  const w = c.width, h = c.height;
  ctx.clearRect(0, 0, w, h);
  if (values.length < 2) return;
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, color + '66'); grad.addColorStop(1, color + '00');
  ctx.beginPath(); ctx.moveTo(0, h);
  for (let i = 0; i < values.length; i++) {
    const x = (i / (SPARK_MAX - 1)) * w;
    const y = h - (values[i] / 100) * h * 0.85 - 2;
    ctx.lineTo(x, y);
  }
  ctx.lineTo(w, h); ctx.closePath(); ctx.fillStyle = grad; ctx.fill();
  ctx.beginPath();
  for (let i = 0; i < values.length; i++) {
    const x = (i / (SPARK_MAX - 1)) * w;
    const y = h - (values[i] / 100) * h * 0.85 - 2;
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }
  ctx.strokeStyle = color; ctx.lineWidth = 1.2;
  ctx.shadowBlur = 4; ctx.shadowColor = color;
  ctx.stroke();
}

window.redrawSparklines = function() {
  const style = getComputedStyle(document.documentElement);
  const color = style.getPropertyValue('--cyan-2').trim() || '#5ee7f2';
  drawSparkline('sparkCpu', sparkHistory.cpu, color);
  drawSparkline('sparkRam', sparkHistory.ram, color);
};

function updateGauge(id, value) {
  const el = document.getElementById('gauge' + id);
  const valEl = document.getElementById('gauge' + id + 'Val');
  if (el) { const circ = 2 * Math.PI * 42; el.style.strokeDashoffset = circ * (1 - Math.min(100, Math.max(0, value)) / 100); }
  if (valEl) valEl.textContent = Math.round(value);
}

function initChart() {
  sysChart = {
    data: { datasets: [{ data: [0,0,0,0,0] }] },
    update: function() {
      const d = this.data.datasets[0].data;
      updateGauge('Cpu', d[0] || 0);
      updateGauge('Ram', d[1] || 0);
      pushSpark('cpu', d[0] || 0);
      pushSpark('ram', d[1] || 0);
      window.redrawSparklines();
    }
  };
}

async function pollMetrics() {
  if (!sysChart) return;
  try {
    const res = await fetch('/system_metrics', { credentials: 'same-origin' });
    if (!res.ok) return;
    const data = await res.json();
    sysChart.data.datasets[0].data = [data.cpu||0, data.ram||0, Math.random()*20+10, Math.random()*30+20, window.fridayState === 'thinking' ? 90 : 20];
    sysChart.update();
  } catch(e) {}
}

function startSysMetrics() {
  if (!sysChart) initChart();
  if (sysPollTimer) clearInterval(sysPollTimer);
  pollMetrics();
  sysPollTimer = setInterval(pollMetrics, 2000);
}

setInterval(() => {
  document.querySelectorAll('.bar-fill[data-min]').forEach(el => {
    const min = +el.dataset.min, max = +el.dataset.max;
    const val = min + Math.random() * (max - min);
    el.style.width = val + '%';
    const row = el.closest('.bar-row');
    if (row) { const v = row.querySelector('.bar-value'); if (v) v.textContent = Math.round(val) + '%'; }
  });
}, 3500);

setInterval(() => {
  const fill = document.querySelector('.e-fill');
  const txt = document.querySelector('.e-text');
  if (fill && txt) {
    const pct = 92 + Math.random() * 8;
    fill.style.strokeDashoffset = 157 * (1 - pct/100);
    txt.innerHTML = `${Math.round(pct)}<small>%</small>`;
  }
}, 5000);

function loadVisualPreferences() {
  if (localStorage.getItem('friday_hide_sidebar') === 'true') { document.body.classList.add('hide-sidebar'); $('toggleSidebar').checked = false; }
  if (localStorage.getItem('friday_hide_chart') === 'true') { document.body.classList.add('hide-chart'); $('toggleChart').checked = false; }
  if (localStorage.getItem('friday_hide_particles') === 'true') { document.body.classList.add('hide-particles'); $('toggleParticles').checked = false; }
  if (localStorage.getItem('friday_auto_speak') === 'false') { $('autoSpeak').checked = false; $('toggleAutoSpeak').checked = false; }
}

$('toggleSidebar').addEventListener('change', e => {
  if (e.target.checked) document.body.classList.remove('hide-sidebar'); else document.body.classList.add('hide-sidebar');
  localStorage.setItem('friday_hide_sidebar', !e.target.checked);
});
$('toggleChart').addEventListener('change', e => {
  if (e.target.checked) document.body.classList.remove('hide-chart'); else document.body.classList.add('hide-chart');
  localStorage.setItem('friday_hide_chart', !e.target.checked);
});
$('toggleParticles').addEventListener('change', e => {
  const show = e.target.checked;
  if (show) document.body.classList.remove('hide-particles'); else document.body.classList.add('hide-particles');
  localStorage.setItem('friday_hide_particles', !show);
  if (show && !bgRenderer) initBgParticles();
});
$('toggleAutoSpeak').addEventListener('change', e => {
  $('autoSpeak').checked = e.target.checked;
  localStorage.setItem('friday_auto_speak', e.target.checked ? 'true' : 'false');
});
$('openSettingsBtn').addEventListener('click', () => $('settingsModal').classList.remove('hidden'));
$('closeSettingsBtn').addEventListener('click', () => $('settingsModal').classList.add('hidden'));

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    if (!$('palette').classList.contains('hidden')) { closePalette(); return; }
    if (!$('settingsModal').classList.contains('hidden')) { $('settingsModal').classList.add('hidden'); return; }
    if (window.speaking || window.listening) { window.stopVoice(); window.setStatus("Interrompido."); }
  }
  if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === 'k') {
    e.preventDefault();
    const c = $('command'); if (c && !$('assistant').classList.contains('hidden')) c.focus();
  }
  if ((e.ctrlKey || e.metaKey) && e.key === '/') { e.preventDefault(); $('settingsModal').classList.toggle('hidden'); }
  if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === 'p') { e.preventDefault(); window.openPalette(); }
});

async function runBootSequence() {
  if (bootRunning) return;
  bootRunning = true;
  const overlay = $("bootOverlay");
  const log = $("bootLog");
  try {
    overlay.classList.remove("hidden", "fade-out");
    const steps = ["INITIALIZING KERNEL","LOADING NEURAL NETWORK","MOUNTING FILESYSTEM","ESTABLISHING SECURE LINK","CALIBRATING SENSORS","SYNCING CALENDAR","FRIDAY ONLINE"];
    for (let i = 0; i < steps.length; i++) {
      log.innerHTML = `<span class="ok">[OK]</span> ${steps[i]}<span class="cursor"></span>`;
      try { window.playTone(400 + i * 120, 'sine', 0.06); } catch(e) {}
      await new Promise(r => setTimeout(r, 220));
    }
    await new Promise(r => setTimeout(r, 400));
    overlay.classList.add("fade-out");
    await new Promise(r => setTimeout(r, 500));
  } finally {
    overlay.classList.add("hidden");
    overlay.classList.remove("fade-out");
    bootRunning = false;
  }
}

window.addEventListener('beforeinstallprompt', e => {
  e.preventDefault();
  deferredPrompt = e;
  $('installPwaBtn').style.display = 'block';
  $('installStatusText').textContent = "Pronto para instalar!";
});
$('installPwaBtn').addEventListener('click', async () => {
  if (!deferredPrompt) return;
  deferredPrompt.prompt();
  const { outcome } = await deferredPrompt.userChoice;
  if (outcome === 'accepted') { $('installPwaBtn').style.display = 'none'; $('installStatusText').textContent = "Instalado!"; }
  deferredPrompt = null;
});
if (/iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream) {
  $('iosInstallInstructions').classList.remove('hidden');
  $('installStatusText').textContent = "Use o Safari para instalar.";
}
$('requestMicBtn').addEventListener('click', async () => {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    $('micPermissionStatus').textContent = "AUTORIZADO";
    $('micPermissionStatus').className = "status-badge status-granted";
    stream.getTracks().forEach(t => t.stop());
  } catch (err) {
    $('micPermissionStatus').textContent = "BLOQUEADO";
    $('micPermissionStatus').className = "status-badge status-denied";
    alert("Libere o microfone nas configurações do navegador.");
  }
});

function loadVoices() {
  availableVoices = window.speechSynthesis.getVoices();
  const select = $('voiceSelect');
  select.innerHTML = '';
  let filtered = availableVoices.filter(v => v.lang.includes('pt'));
  if (filtered.length === 0) filtered = availableVoices.filter(v => v.lang.includes('en'));
  if (filtered.length === 0) filtered = availableVoices;
  filtered.forEach(voice => {
    const opt = document.createElement('option');
    opt.value = voice.name; opt.textContent = `${voice.name} (${voice.lang})`;
    select.appendChild(opt);
  });
  const femaleHints = ['female', 'feminina', 'luciana', 'maria', 'google português do brasil', 'google portuguese'];
  const female = filtered.find(v => femaleHints.some(h => v.name.toLowerCase().includes(h)));
  if (female) select.value = female.name;
}
window.speechSynthesis.onvoiceschanged = loadVoices;
setTimeout(loadVoices, 500);
$('pitchSlider').addEventListener('input', e => { $('pitchValue').textContent = e.target.value; });
$('testUltronVoiceBtn').addEventListener('click', () => {
  browserSpeak("Olá, chefe. Esta é minha voz nativa, pronta para entrar em ação quando necessário.", 0, true);
});

function browserSpeak(text, runId, forceTest = false) {
  if (!('speechSynthesis' in window)) return;
  const synth = window.speechSynthesis;
  synth.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = 'pt-BR'; utterance.rate = 0.85;
  const p = parseFloat($('pitchSlider').value);
  utterance.pitch = Number.isFinite(p) ? p : 0.1;
  const selectedName = $('voiceSelect').value;
  if (selectedName) { const voice = availableVoices.find(v => v.name === selectedName); if (voice) utterance.voice = voice; }
  utterance.onstart = () => { if (runId === speechRunId || forceTest) { window.speaking = true; soundListen(); setWaveformState('speaking'); } };
  utterance.onend = () => { if (runId === speechRunId || forceTest) { window.speaking = false; window.setStatus('Sistema online.'); setWaveformState('idle'); } };
  synth.speak(utterance);
}

window.playTone = function(freq, type, duration, vol=0.04) {
  try {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === 'suspended') audioCtx.resume();
    const osc = audioCtx.createOscillator(); const gain = audioCtx.createGain();
    osc.type = type; osc.frequency.setValueAtTime(freq, audioCtx.currentTime);
    gain.gain.setValueAtTime(vol, audioCtx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.00001, audioCtx.currentTime + duration);
    osc.connect(gain); gain.connect(audioCtx.destination);
    osc.start(); osc.stop(audioCtx.currentTime + duration);
  } catch(e) {}
};
function soundBoot() { window.playTone(400, 'sine', 0.08); setTimeout(() => window.playTone(800, 'sine', 0.15), 90); }
function soundListen() { window.playTone(600, 'sine', 0.08); }
function soundSend() { window.playTone(300, 'sine', 0.08); }
function soundReceive() { window.playTone(800, 'sine', 0.08); }

function startAmbience() {
  if (ambienceNodes) return;
  try {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === 'suspended') audioCtx.resume();
    const ctx = audioCtx;
    const osc1 = ctx.createOscillator(); osc1.type = 'sine'; osc1.frequency.value = 50;
    const osc2 = ctx.createOscillator(); osc2.type = 'sine'; osc2.frequency.value = 75;
    const gain = ctx.createGain(); gain.gain.value = 0.008;
    const filter = ctx.createBiquadFilter(); filter.type = 'lowpass'; filter.frequency.value = 200;
    osc1.connect(gain); osc2.connect(gain); gain.connect(filter); filter.connect(ctx.destination);
    osc1.start(); osc2.start();
    ambienceNodes = { osc1, osc2, gain };
  } catch(e) {}
}
function stopAmbience() {
  if (ambienceNodes) { try { ambienceNodes.osc1.stop(); ambienceNodes.osc2.stop(); } catch(e) {} ambienceNodes = null; }
}

function initBgParticles() {
  if (document.body.classList.contains('hide-particles') || bgRenderer) return;
  const canvas = $('bg-canvas');
  if (!canvas) return;
  bgRenderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
  bgRenderer.setSize(window.innerWidth, window.innerHeight);
  bgScene = new THREE.Scene();
  bgCamera = new THREE.PerspectiveCamera(75, window.innerWidth / window.innerHeight, 0.1, 1000);
  bgCamera.position.z = 10;
  const particleCount = 80;
  const geometry = new THREE.BufferGeometry();
  const positions = new Float32Array(particleCount * 3);
  const velocities = [];
  for (let i = 0; i < particleCount; i++) {
    positions[i*3] = (Math.random() - 0.5) * 20;
    positions[i*3+1] = (Math.random() - 0.5) * 20;
    positions[i*3+2] = (Math.random() - 0.5) * 20;
    velocities.push({ x: (Math.random()-0.5)*0.02, y: (Math.random()-0.5)*0.02, z: (Math.random()-0.5)*0.02 });
  }
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  particlesMesh = new THREE.Points(geometry, new THREE.PointsMaterial({ color: 0x00e5ff, size: 0.12, transparent: true, opacity: 0.6, blending: THREE.AdditiveBlending }));
  bgScene.add(particlesMesh);
  const lineGeo = new THREE.BufferGeometry();
  const linePositions = new Float32Array(particleCount * particleCount * 3);
  lineGeo.setAttribute('position', new THREE.BufferAttribute(linePositions, 3));
  linesMesh = new THREE.LineSegments(lineGeo, new THREE.LineBasicMaterial({ color: 0x00e5ff, transparent: true, opacity: 0.08, blending: THREE.AdditiveBlending }));
  bgScene.add(linesMesh);
  function animateBg() {
    requestAnimationFrame(animateBg);
    const posAttr = particlesMesh.geometry.attributes.position;
    const posArray = posAttr.array;
    for (let i = 0; i < particleCount; i++) {
      posArray[i*3] += velocities[i].x; posArray[i*3+1] += velocities[i].y; posArray[i*3+2] += velocities[i].z;
      if (Math.abs(posArray[i*3]) > 10) velocities[i].x *= -1;
      if (Math.abs(posArray[i*3+1]) > 10) velocities[i].y *= -1;
      if (Math.abs(posArray[i*3+2]) > 10) velocities[i].z *= -1;
    }
    posAttr.needsUpdate = true;
    const linePosAttr = linesMesh.geometry.attributes.position;
    const linePosArray = linePosAttr.array;
    let vi = 0;
    for (let i = 0; i < particleCount; i++) {
      for (let j = i + 1; j < particleCount; j++) {
        const dx = posArray[i*3] - posArray[j*3];
        const dy = posArray[i*3+1] - posArray[j*3+1];
        const dz = posArray[i*3+2] - posArray[j*3+2];
        if (Math.sqrt(dx*dx + dy*dy + dz*dz) < 3.5) {
          linePosArray[vi++] = posArray[i*3]; linePosArray[vi++] = posArray[i*3+1]; linePosArray[vi++] = posArray[i*3+2];
          linePosArray[vi++] = posArray[j*3]; linePosArray[vi++] = posArray[j*3+1]; linePosArray[vi++] = posArray[j*3+2];
        }
      }
    }
    linesMesh.geometry.setDrawRange(0, vi/3); linePosAttr.needsUpdate = true;
    bgScene.rotation.y += 0.001; bgScene.rotation.x += 0.0005;
    bgRenderer.render(bgScene, bgCamera);
  }
  animateBg();
}

window.addEventListener('resize', () => {
  if (bgRenderer) {
    bgRenderer.setSize(window.innerWidth, window.innerHeight);
    bgCamera.aspect = window.innerWidth / window.innerHeight;
    bgCamera.updateProjectionMatrix();
  }
});

let micContext, micAnalyser, micSource, micDataArray;
async function setupMicVisualizer() {
  if (micContext) return;
  try {
    micContext = new (window.AudioContext || window.webkitAudioContext)();
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    micSource = micContext.createMediaStreamSource(stream);
    micAnalyser = micContext.createAnalyser();
    micAnalyser.fftSize = 64;
    micSource.connect(micAnalyser);
    micDataArray = new Uint8Array(micAnalyser.frequencyBinCount);
  } catch(e) {}
}

function animateMicVisualizer() {
  if (!window.listening) return;
  requestAnimationFrame(animateMicVisualizer);
  if (!micAnalyser) return;
  micAnalyser.getByteFrequencyData(micDataArray);
  const bars = document.querySelectorAll('#waveform span');
  for (let i = 0; i < bars.length; i++) {
    const val = micDataArray[i] || 0;
    bars[i].style.height = `${Math.max(4, (val / 255) * 28)}px`;
  }
}

function animateTTSVisualizer() {
  if (!window.speaking) return;
  requestAnimationFrame(animateTTSVisualizer);
  if (!window.audioAnalyser) return;
  const data = new Uint8Array(window.audioAnalyser.frequencyBinCount);
  window.audioAnalyser.getByteFrequencyData(data);
  const bars = document.querySelectorAll('#waveform span');
  const step = Math.floor(data.length / bars.length);
  for (let i = 0; i < bars.length; i++) {
    const val = data[i * step] || 0;
    bars[i].style.height = `${Math.max(4, (val / 255) * 30)}px`;
  }
}

if ('serviceWorker' in navigator) { window.addEventListener('load', () => { navigator.serviceWorker.register('/sw.js').catch(() => {}); }); }
window.onYouTubeIframeAPIReady = function() { window.ytPlayer = new YT.Player('musicPlayer', { events: { 'onReady': () => {} } }); };
$('closeMusicPlayer').addEventListener('click', () => {
  $('musicPlayerContainer').classList.add('hidden');
  $('musicPlayer').src = "";
  if (window.ytPlayer && window.ytPlayer.stopVideo) window.ytPlayer.stopVideo();
});

function startStatusPolling() {
  stopStatusPolling();
  statusPollingInterval = setInterval(async () => {
    try {
      const res = await fetch("/status", { credentials: "same-origin", cache: "no-store" });
      if (!res.ok) return;
      const data = await res.json();
      if (data.status && data.status.length > 0) window.setStatus(data.status[data.status.length - 1]);
    } catch (e) {}
  }, 3000);
}
function stopStatusPolling() { if (statusPollingInterval) { clearInterval(statusPollingInterval); statusPollingInterval = null; } }

function setWaveformState(state) {
  const wf = $('waveform');
  if (!wf) return;
  wf.className = 'waveform-container';
  if (state === 'listening') {
    wf.classList.add('active');
    $('ctx-mic').textContent = "LISTENING";
    $('ctx-mic').style.color = "var(--green)";
    document.querySelectorAll('#waveform span').forEach(s => s.style.animation = 'none');
    animateMicVisualizer();
  } else if (state === 'speaking') {
    wf.classList.add('active', 'speaking');
    $('ctx-mic').textContent = "STANDBY";
    $('ctx-mic').style.color = "var(--cyan-2)";
    document.querySelectorAll('#waveform span').forEach(s => s.style.height = '4px');
    animateTTSVisualizer();
  } else {
    $('ctx-mic').textContent = "STANDBY";
    $('ctx-mic').style.color = "var(--text-mid)";
  }
}

window.setStatus = function(text) { $("status").textContent = text; };

function formatEventDate(dateStr) {
  if (!dateStr) return "Sem data";
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return dateStr;
  return new Intl.DateTimeFormat("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }).format(d);
}

window.loadCalendarEvents = async function(range = "upcoming") {
  const panel = $("calendarPanel"); const container = $("calendarEventsList");
  if (!panel || !container) return;
  panel.classList.remove("hidden");
  container.innerHTML = '<p class="awareness-note">Sincronizando agenda...</p>';
  try {
    const response = await fetch(`/calendar/events?range=${range}`, { credentials: "same-origin", cache: "no-store" });
    const data = await response.json();
    if (!response.ok) { container.innerHTML = `<p class="awareness-note" style="color: var(--red);">${data.error || data.detail || "Erro."}</p>`; return; }
    if (!data.events || data.events.length === 0) { container.innerHTML = `<p class="awareness-note">Nenhum compromisso ${range === "today" ? "hoje" : "nos próximos dias"}.</p>`; return; }
    container.replaceChildren();
    data.events.forEach(evt => {
      const item = document.createElement("div"); item.className = "awareness-item";
      const title = document.createElement("strong"); title.textContent = evt.summary;
      const timeSpan = document.createElement("span"); timeSpan.textContent = formatEventDate(evt.start);
      item.append(title, timeSpan); container.appendChild(item);
    });
  } catch (e) { container.innerHTML = '<p class="awareness-note" style="color: var(--red);">Erro de conexão.</p>'; }
};

async function loadHistoryFromBackend() {
  try {
    const res = await fetch('/history', { credentials: 'same-origin' });
    if (!res.ok) return;
    const data = await res.json();
    if (data.history && data.history.length) {
      $('messages').replaceChildren();
      data.history.forEach(msg => {
        if (msg.role === 'user') window.addMessage('Você', msg.content);
        else if (msg.role === 'assistant') window.addMessage('FRIDAY', msg.content);
      });
      console.log('[HISTORY] Restauradas', data.history.length, 'mensagens');
    }
  } catch(e) { console.warn('[HISTORY] Falha:', e); }
}

window.stopVoice = function() {
  speechRunId++;
  clearTimeout(silenceTimer);
  if (wakeWordRecognition && wakeWordActive) { wakeWordActive = false; try { wakeWordRecognition.stop(); } catch(e) {} }
  if (currentUtterance instanceof Audio) { currentUtterance.pause(); currentUtterance.currentTime = 0; }
  currentUtterance = null;
  if (recognition && window.listening) { try { recognition.stop(); } catch (e) {} }
  if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  window.speaking = false;
  setWaveformState('idle');
};

function cleanSpeechText(text) {
  return String(text)
    .replace(/```[\s\S]*?```/g, "trecho de código omitido")
    .replace(/`([^`]*)`/g, "$1")
    .replace(/\*\*(.*?)\*\*/g, "$1")
    .replace(/\*(.*?)\*/g, "$1")
    .replace(/https?:\/\/\S+/g, "link omitido")
    .replace(/^\s*[-*]\s+/gm, "")
    .trim();
}

function scrambleText(element, finalText) {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!<>-_/[]{}';
  let it = 0; const max = 15;
  const interval = setInterval(() => {
    const scrambled = finalText.split('').map((char, i) => {
      if (i < (it / max) * finalText.length) return char;
      if (char === ' ' || char === '\n') return char;
      return chars[Math.floor(Math.random() * chars.length)];
    }).join('');
    element.innerHTML = safeMarkedParse(scrambled);
    it++;
    if (it >= max) { clearInterval(interval); element.innerHTML = safeMarkedParse(finalText); }
  }, 25);
}

window.speakText = async function(text, force = false) {
  const autoEl = $("autoSpeak");
  if (!force && autoEl && !autoEl.checked) return;
  const cleanText = cleanSpeechText(text);
  if (!cleanText) return;
  speechRunId++;
  const runId = speechRunId;
  if ('speechSynthesis' in window) window.speechSynthesis.cancel();
  window.setStatus('FRIDAY está falando...');
  try {
    const response = await fetch(`/tts?text=${encodeURIComponent(cleanText)}`);
    if (response.status === 204) throw new Error('Fallback');
    if (!response.ok) throw new Error('TTS falhou');
    const blob = await response.blob();
    if (blob.size === 0) throw new Error('Áudio vazio');
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    if (window.setupAudioAnalyser) window.setupAudioAnalyser(audio);
    audio.onplay = () => { if (runId === speechRunId) { window.speaking = true; soundListen(); setWaveformState('speaking'); } };
    audio.onended = () => {
      if (runId === speechRunId) {
        window.speaking = false; window.setStatus('Sistema online.'); setWaveformState('idle');
        URL.revokeObjectURL(url);
        setTimeout(() => { if (!$("assistant").classList.contains("hidden") && !window.listening && !isProcessing) window.startVoice(true); }, 800);
      }
    };
    audio.onerror = () => {
      if (!nativeVoiceToastShown) {
        nativeVoiceToastShown = true;
        window.showToast('🎙 Voz nativa ativada (ElevenLabs indisponível).');
      }
      browserSpeak(cleanText, runId);
    };
    currentUtterance = audio;
    await audio.play();
    return;
  } catch (e) {
    if (!nativeVoiceToastShown) {
      nativeVoiceToastShown = true;
      window.showToast('🎙 Voz nativa ativada (ElevenLabs indisponível).');
    }
    browserSpeak(cleanText, runId);
  }
};

window.speakResponse = function(text) { lastReply = String(text || ""); window.speakText(lastReply, false); };

window.addMessage = function(who, text) {
  const item = document.createElement("div");
  item.className = "msg " + (who === "Você" ? "user" : (who === "SYSTEM" ? "system" : "bot"));
  const label = document.createElement("strong");
  const name = who === "Você" ? "CHEFE" : (who === "SYSTEM" ? "SYSTEM" : "FRIDAY");
  const time = new Date().toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
  label.textContent = name;
  const timeEl = document.createElement("span");
  timeEl.style.cssText = "font-size:8px;color:var(--text-dim);margin-left:6px;letter-spacing:0.1em;font-weight:400;";
  timeEl.textContent = time;
  label.appendChild(timeEl);

  const content = document.createElement("span");
  if (text && text.trim()) {
    content.innerHTML = safeMarkedParse(text);
  }

  item.append(label, content);
  $("messages").appendChild(item);
  $("messages").scrollTop = $("messages").scrollHeight;
  return content;
};

function addToolCard(toolName) {
  const item = document.createElement("div"); item.className = "msg bot";
  const label = document.createElement("strong"); label.textContent = "SYSTEM";
  const card = document.createElement("div"); card.className = "tool-card loading";
  let icon = "⚙"; let displayName = toolName.replace(/_/g, ' ');
  if (toolName.includes("calendar")) { icon = "📅"; displayName = "Acessando Agenda..."; }
  else if (toolName.includes("weather")) { icon = "🌤"; displayName = "Consultando Clima..."; }
  else if (toolName.includes("music")) { icon = "🎵"; displayName = "Buscando áudio..."; }
  card.innerHTML = `<span>${icon}</span><span>${displayName}</span>`;
  item.append(label, card); $("messages").appendChild(item);
  $("messages").scrollTop = $("messages").scrollHeight;
  return card;
}

const msgsEl = $("messages");
const pill = $("scrollPill");
msgsEl.addEventListener('scroll', () => {
  const nearBottom = msgsEl.scrollHeight - msgsEl.scrollTop - msgsEl.clientHeight < 80;
  pill.classList.toggle('visible', !nearBottom);
});
pill.addEventListener('click', () => { msgsEl.scrollTo({ top: msgsEl.scrollHeight, behavior: 'smooth' }); });

async function request(url, data) {
  const response = await fetch(url, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
  let result;
  try { result = await response.json(); } catch (e) { throw new Error("Resposta inválida."); }
  if (!response.ok) throw new Error((result && result.detail) || (result && result.error) || "Falha.");
  return result;
}

$("loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (lockoutEndsAt > Date.now()) return;

  const button = $("loginForm").querySelector('button[type="submit"]');
  const passwordInput = $("password");
  const notice = $("lockoutNotice");

  button.disabled = true;
  notice.classList.add("hidden");
  notice.textContent = "";

  try {
    await request("/login", { password: passwordInput.value });
    const sessionResponse = await fetch("/session", { credentials: "same-origin", cache: "no-store" });
    if (!sessionResponse.ok) throw new Error("Não foi possível confirmar a sessão.");
    const sessionData = await sessionResponse.json();
    if (!sessionData.authenticated) throw new Error("A sessão não foi validada. Tente entrar novamente.");
    
    passwordInput.value = "";
    await activateAssistant();
  } catch (error) {
    notice.textContent = error.message || "Falha ao entrar. Verifique a senha.";
    notice.classList.remove("hidden");
    passwordInput.focus();
    passwordInput.select();
  } finally {
    button.disabled = false;
  }
});

function startLockout(remaining) {
  const seconds = Math.max(0, Math.ceil(Number(remaining) || 0));
  const notice = $("lockoutNotice");
  clearInterval(lockoutInterval);
  if (seconds === 0) { lockoutEndsAt = 0; notice.classList.add("hidden"); return; }
  lockoutEndsAt = Date.now() + seconds * 1000;
  notice.classList.remove("hidden");
  const upd = () => {
    const left = Math.max(0, Math.ceil((lockoutEndsAt - Date.now()) / 1000));
    if (left === 0) { clearInterval(lockoutInterval); lockoutEndsAt = 0; notice.textContent = "Bloqueio encerrado."; return; }
    notice.textContent = `ACESSO BLOQUEADO · ${left}s`;
  };
  upd(); lockoutInterval = setInterval(upd, 1000);
}

const MAX_IMAGE_BYTES = 4 * 1024 * 1024;
const MAX_PDF_BYTES = 8 * 1024 * 1024;

function handleFileSelect(e, type) {
  const file = e.target.files[0]; if (!file) return;
  const limit = type === 'image' ? MAX_IMAGE_BYTES : MAX_PDF_BYTES;
  if (file.size > limit) {
    window.showToast(`⚠ Arquivo muito grande (máx ${Math.round(limit / 1024 / 1024)}MB).`);
    e.target.value = '';
    return;
  }
  currentAttachmentName = file.name;
  const reader = new FileReader();
  reader.onload = (ev) => {
    currentAttachment = ev.target.result; currentAttachmentType = type;
    $('attachmentName').textContent = file.name;
    $('attachmentPreview').classList.remove('hidden');
  };
  reader.readAsDataURL(file); e.target.value = '';
}
$('attachImgBtn').addEventListener('click', () => $('fileInputImage').click());
$('attachPdfBtn').addEventListener('click', () => $('fileInputPdf').click());
$('fileInputImage').addEventListener('change', e => handleFileSelect(e, 'image'));
$('fileInputPdf').addEventListener('change', e => handleFileSelect(e, 'pdf'));
$('removeAttachment').addEventListener('click', () => {
  currentAttachment = null; currentAttachmentType = null; currentAttachmentName = null;
  $('attachmentPreview').classList.add('hidden'); $('attachmentName').textContent = '';
});

$('cancelBtn').addEventListener('click', () => {
  if (currentAbortController) {
    currentAbortController.abort();
    window.setStatus('Geração cancelada.');
    window.showToast('⏹ Geração cancelada pelo chefe.');
  }
});

async function sendCommandToFriday(command, isInitial = false) {
  if (isProcessing) return;
  isProcessing = true; window.fridayState = 'thinking';
  
  if (!isInitial) { window.addMessage("Você", command); soundSend(); }
  else { window.addMessage("SYSTEM", "_Iniciando briefing..._"); }
  
  const botSpan = window.addMessage("FRIDAY", " ");
  const botMsgEl = botSpan.parentElement;
  botMsgEl.classList.add('typing');
  $("sendBtn").disabled = true; $("voiceBtn").disabled = true;
  window.setStatus("FRIDAY está processando..."); startStatusPolling();

  currentAbortController = new AbortController();
  thinkingStart = Date.now();
  $('thinkingIndicator').classList.remove('hidden');
  $('cancelBtn').classList.remove('hidden');
  thinkingTimer = setInterval(() => {
    const elapsed = ((Date.now() - thinkingStart) / 1000).toFixed(1);
    $('thinkingTime').textContent = `${elapsed}s`;
  }, 100);

  let botError = null;
  let streamWatchdog = null;
  let lastChunkAt = Date.now();

  try {
    const payload = { command };
    if (currentAttachmentType === 'pdf' && !isInitial) {
      payload.pdf = currentAttachment; payload.pdf_name = currentAttachmentName;
      currentAttachment = null; currentAttachmentType = null; currentAttachmentName = null;
      $('attachmentPreview').classList.add('hidden'); $('attachmentName').textContent = '';
    }
    if (currentAttachmentType === 'image' && !isInitial) {
      payload.image = currentAttachment; payload.image_name = currentAttachmentName;
      currentAttachment = null; currentAttachmentType = null; currentAttachmentName = null;
      $('attachmentPreview').classList.add('hidden'); $('attachmentName').textContent = '';
    }
    
    const response = await fetch("/chat", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: currentAbortController.signal,
    });

    if (response.status === 401) { showLogin(); throw new Error("Sessão expirada. Faça login novamente."); }
    if (response.status === 429) { const err = await response.json().catch(() => ({})); throw new Error(err.detail || "Muitas mensagens em pouco tempo."); }
    if (!response.ok) throw new Error("Erro na conexão.");

    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let fullReplyText = "", buffer = "", streamDone = false, gotFirst = false;
    lastChunkAt = Date.now();

    streamWatchdog = setInterval(() => {
      if (Date.now() - lastChunkAt > 45000) {
        clearInterval(streamWatchdog);
        streamWatchdog = null;
        try { reader.cancel(); } catch(e) {}
        window.setStatus("Conexão lenta — cancelando.");
        botMsgEl.classList.remove('typing');
        if (!fullReplyText.trim()) botSpan.innerHTML = "[ERRO] Conexão perdida com o servidor.";
      }
    }, 5000);

    while (!streamDone) {
      const { done, value } = await reader.read();
      if (done) break;
      lastChunkAt = Date.now();
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n'); buffer = lines.pop();
      for (const line of lines) {
        if (line.startsWith("data: ")) {
          const dataStr = line.substring(6).trim(); if (!dataStr) continue;
          try {
            const dataObj = JSON.parse(dataStr);
            if (dataObj.done) { streamDone = true; break; }
            if (!gotFirst) { soundReceive(); gotFirst = true; }

            if (dataObj.tool) {
              window.fridayState = 'tooling';
              currentToolCard = addToolCard(dataObj.tool);
            }

            if (dataObj.action === 'open_eye_of_god') {
              window.openEyeOfGod();
            }

            if (dataObj.music) {
              $('musicPlayer').src = dataObj.music;
              $('musicPlayerContainer').classList.remove('hidden');
              if (currentToolCard) { currentToolCard.classList.remove('loading'); currentToolCard = null; }
            }
            if (dataObj.music_action) {
              if (dataObj.music_action === 'pause' && window.ytPlayer && window.ytPlayer.pauseVideo) window.ytPlayer.pauseVideo();
              if (dataObj.music_action === 'play' && window.ytPlayer && window.ytPlayer.playVideo) window.ytPlayer.playVideo();
              if (currentToolCard) { currentToolCard.classList.remove('loading'); currentToolCard = null; }
            }

            if (dataObj.text) {
              if (botMsgEl.classList.contains('typing')) botMsgEl.classList.remove('typing');
              if (currentToolCard) { currentToolCard.classList.remove('loading'); currentToolCard = null; }
              fullReplyText += dataObj.text;
              botSpan.innerHTML = safeMarkedParse(fullReplyText);
              $("messages").scrollTop = $("messages").scrollHeight;
            }
            if (dataObj.error) {
              if (currentToolCard) { currentToolCard.classList.remove('loading'); currentToolCard = null; }
              botMsgEl.classList.remove('typing');
              botError = dataObj.error;
              window.setStatus("Erro na IA.");
            }
          } catch (e) {}
        }
      }
    }
    botMsgEl.classList.remove('typing');
    if (currentToolCard) { currentToolCard.classList.remove('loading'); currentToolCard = null; }
    if (window.hljs) botSpan.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
    stopStatusPolling();

    if (botError) {
      botSpan.innerHTML = "[ERRO] " + botError;
    } else if (!fullReplyText.trim()) {
      botSpan.innerHTML = "Desculpe, chefe. Não obtive resposta do núcleo neural.";
      window.setStatus("Sistema online.");
    } else {
      window.setStatus("Sistema online.");
      scrambleText(botSpan, fullReplyText);
      lastReply = fullReplyText; window.speakResponse(fullReplyText);
      lastAIResponseTime = Date.now();
    }
  } catch (error) {
    stopStatusPolling();
    botMsgEl.classList.remove('typing');
    if (currentToolCard) { currentToolCard.classList.remove('loading'); currentToolCard = null; }
    if (error.name === 'AbortError') { botSpan.innerHTML = "_[Cancelado pelo chefe]_"; }
    else {
      botSpan.innerHTML = "[ERRO] " + error.message;
      window.setStatus("Erro de conexão.");
      window.fridayState = 'error';
      setTimeout(() => { window.fridayState = 'idle'; }, 2000);
    }
  } finally {
    if (streamWatchdog) { clearInterval(streamWatchdog); streamWatchdog = null; }
    clearInterval(thinkingTimer);
    $('thinkingIndicator').classList.add('hidden');
    $('cancelBtn').classList.add('hidden');
    if (currentToolCard) { currentToolCard.classList.remove('loading'); currentToolCard = null; }
    currentAbortController = null;
    isProcessing = false;
    if (window.fridayState !== 'error') window.fridayState = 'idle';
    $("sendBtn").disabled = false; $("voiceBtn").disabled = false;
    if (!$("assistant").classList.contains("hidden")) $("command").focus();
  }
}

$("chatForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const command = $("command").value.trim();
  if (!command && !currentAttachment) return;
  $("command").value = "";
  sendCommandToFriday(command, false);
});

window.startVoice = function(isContinuous = false) {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { window.setStatus("Reconhecimento indisponível."); return; }
  if (window.listening && recognition) { recognition.stop(); return; }
  window.stopVoice();
  
  recognition = new SR();
  recognition.lang = "pt-BR"; recognition.continuous = isContinuous;
  recognition.interimResults = false; recognition.maxAlternatives = 1;
  
  recognition.onstart = () => {
    window.listening = true; setupMicVisualizer();
    $("voiceBtn").classList.add("listening"); soundListen(); setWaveformState('listening');
    $("voiceBtn").textContent = "⏹";
    window.setStatus(isContinuous ? "Aguardando continuação..." : "Estou ouvindo, chefe...");
    clearTimeout(silenceTimer);
    if (isContinuous) silenceTimer = setTimeout(() => { if (window.listening && recognition) { recognition.stop(); window.setStatus("Escuta encerrada."); } }, 5000);
  };
  
  recognition.onresult = (event) => {
    clearTimeout(silenceTimer);
    $("command").value = event.results[0][0].transcript;
    window.setStatus("Voz reconhecida.");
    $("chatForm").requestSubmit();
  };
  
  recognition.onerror = (event) => { clearTimeout(silenceTimer); window.setStatus("Falha no mic: " + event.error); setWaveformState('idle'); };
  
  recognition.onend = () => {
    clearTimeout(silenceTimer); window.listening = false;
    $("voiceBtn").classList.remove("listening"); $("voiceBtn").textContent = "🎙";
    setWaveformState('idle');
    setTimeout(() => { if (!$("assistant").classList.contains("hidden") && !window.speaking && !isProcessing) startWakeWordListener(); }, isContinuous ? 3000 : 1000);
  };
  
  try { recognition.start(); } catch (e) { window.setStatus("Não foi possível iniciar o mic."); setWaveformState('idle'); }
};

$("voiceBtn").addEventListener("click", () => window.startVoice(false));

async function checkCalendarConnection() {
  try {
    const res = await fetch("/calendar/status", { credentials: "same-origin", cache: "no-store" });
    const data = await res.json();
    if (res.ok && data.connected) {
      $("calendarConnect").textContent = "📅 CONNECTED";
      window.loadCalendarEvents("upcoming");
      return true;
    }
  } catch (e) {}
  return false;
}
$("calendarConnect").addEventListener("click", () => { window.location.href = "/calendar/connect"; });
$("refreshCalendarTodayBtn").addEventListener("click", () => window.loadCalendarEvents("today"));
$("refreshCalendarBtn").addEventListener("click", () => window.loadCalendarEvents("upcoming"));
$("stopSpeakTop").addEventListener("click", () => { window.stopVoice(); window.setStatus("Fala interrompida."); });

function startWakeWordListener() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR || wakeWordActive) return;
  wakeWordRecognition = new SR();
  wakeWordRecognition.lang = 'pt-BR'; wakeWordRecognition.continuous = true; wakeWordRecognition.interimResults = true;
  wakeWordRecognition.onresult = (event) => {
    const transcript = Array.from(event.results).map(r => r[0].transcript).join('').toLowerCase();
    const now = Date.now();
    if (now - wakeWordLastHit < 5000) return;
    if (transcript.includes('hey friday') || transcript.includes('oi friday') || transcript.includes('ei friday')) {
      wakeWordHits++;
      wakeWordLastHit = now;
      if (wakeWordHits >= 2) {
        wakeWordHits = 0;
        try { wakeWordRecognition.stop(); } catch(e) {}
        wakeWordActive = false;
        soundListen(); window.startVoice(false);
      } else {
        setTimeout(() => { wakeWordHits = 0; }, 3000);
      }
    }
  };
  wakeWordRecognition.onerror = (event) => { if (event.error === 'no-speech' || event.error === 'aborted') return; };
  wakeWordRecognition.onend = () => {
    if (!wakeWordActive && !$("assistant").classList.contains("hidden") && !window.listening && !window.speaking && !isProcessing) {
      try { wakeWordRecognition.start(); } catch (e) {}
    }
  };
  try { wakeWordRecognition.start(); wakeWordActive = true; } catch (e) {}
}

function showLogin() {
  window.stopVoice(); stopStatusPolling(); stopAmbience();
  $("assistant").classList.add("hidden"); $("login").classList.remove("hidden");
  $("messages").replaceChildren(); $("settingsModal").classList.add("hidden");
  $("calendarPanel").classList.add("hidden"); $('musicPlayerContainer').classList.add('hidden');
  $('musicPlayer').src = ""; window.setStatus("Aguardando autenticação");
  document.body.dataset.state = 'idle';
  assistantActivated = false;
}
$("logout").addEventListener("click", async () => {
  window.stopVoice();
  try { await request("/logout", {}); } catch (e) {}
  assistantActivated = false;
  showLogin();
});

async function activateAssistant() {
  if (assistantActivated) return;
  assistantActivated = true;

  try {
    $("login").classList.add("hidden");
    await runBootSequence();
    $("assistant").classList.remove("hidden");
    window.setStatus("Sessão ativa. FRIDAY online.");
    startAmbience();

    $("messages").replaceChildren();
    await loadHistoryFromBackend();

    startSysMetrics();
    
    if (window.initReactor) window.initReactor();

    sendCommandToFriday(
      "Gere o meu briefing matinal para a cidade de Criciúma usando a ferramenta get_morning_briefing",
      true
    );

    checkCalendarConnection();
    startWakeWordListener();
  } catch (error) {
    assistantActivated = false;
    $("assistant").classList.add("hidden");
    $("login").classList.remove("hidden");
    const notice = $("lockoutNotice");
    notice.textContent = "Não foi possível iniciar a interface: " + (error.message || "erro desconhecido");
    notice.classList.remove("hidden");
    window.setStatus("Falha ao iniciar a FRIDAY.");
  }
}

async function checkSession() {
  try {
    const res = await fetch("/session", { credentials: "same-origin", cache: "no-store" });
    if (!res.ok) throw new Error("Falha ao verificar a sessão.");
    const data = await res.json();
    const calendarState = new URLSearchParams(location.search).get("calendar");
    const msgMap = {
      connected: "Google Calendar conectado.", nosession: "Entre antes de conectar.", denied: "Autorização cancelada.",
      old: "Tentativa expirou.", state: "Sessão não confere.", config: "OAuth incompleto.",
      redirect: "Redirect URI inválida.", client: "Cliente OAuth recusado.", token: "Google não concluiu.", incomplete: "Resposta incompleta."
    };
    if (data.authenticated) await activateAssistant();
    if (msgMap[calendarState]) {
      window.setStatus(msgMap[calendarState]);
      history.replaceState({}, "", location.pathname);
    }
  } catch (error) {
    console.error("FRIDAY session check:", error);
    window.setStatus("Não foi possível verificar a sessão.");
  }
}

setInterval(async () => {
  if ($("assistant").classList.contains("hidden")) return;
  try {
    const res = await fetch("/calendar/events?range=today", { credentials: "same-origin", cache: "no-store" });
    if (!res.ok) return;
    const data = await res.json();
    if (!data.events || !data.events.length) return;
    const now = new Date();
    data.events.forEach(evt => {
      if (!evt.start) return;
      const t = new Date(evt.start);
      const diff = (t - now) / 1000 / 60;
      if (diff > 0 && diff <= 15) {
        const key = `alerted_${evt.summary}_${evt.start}`;
        if (!localStorage.getItem(key)) {
          localStorage.setItem(key, "true");
          const msg = `Chefe, seu compromisso "${evt.summary}" começa em ${Math.round(diff)} minutos.`;
          window.showToast(msg, 8000); window.speakText(msg, true);
        }
      }
    });
  } catch (e) {}
}, 5 * 60 * 1000);

setInterval(async () => {
  if ($("assistant").classList.contains("hidden")) return;
  try {
    const res = await fetch("/notifications", { credentials: "same-origin" });
    if (!res.ok) return;
    const data = await res.json();
    if (!data.notifications || data.notifications.length === 0) return;
    const now = Date.now();
    const timeSinceAI = now - lastAIResponseTime;
    data.notifications.forEach(msg => {
      if (timeSinceAI < 30000 && !msg.includes('[CRITICAL]')) return;
      window.showToast(msg, 5000);
    });
  } catch (e) {}
}, 15000);

const PALETTE_COMMANDS = [
  { name: 'Focar input', shortcut: 'Ctrl+K', action: () => $('command').focus() },
  { name: 'Configurações', shortcut: 'Ctrl+/', action: () => $('settingsModal').classList.remove('hidden') },
  { name: 'Parar voz', shortcut: 'Esc', action: () => window.stopVoice() },
  { name: 'Recarregar agenda', shortcut: '', action: () => { $('calendarPanel').classList.remove('hidden'); window.loadCalendarEvents('today'); } },
  { name: 'Conectar Google Calendar', shortcut: '', action: () => { window.location.href = '/calendar/connect'; } },
  { name: 'Nova conversa (limpar)', shortcut: '', action: () => $('messages').replaceChildren() },
  { name: 'Mostrar/ocultar sidebar', shortcut: '', action: () => document.body.classList.toggle('hide-sidebar') },
  { name: 'Mostrar/ocultar telemetria', shortcut: '', action: () => document.body.classList.toggle('hide-chart') },
  { name: 'Sair', shortcut: '', action: () => $('logout').click() },
];

window.openPalette = function() {
  $('palette').classList.remove('hidden');
  $('paletteInput').value = '';
  renderPaletteItems('');
  setTimeout(() => $('paletteInput').focus(), 50);
};
function closePalette() { $('palette').classList.add('hidden'); }
function renderPaletteItems(query) {
  const q = query.toLowerCase();
  const filtered = PALETTE_COMMANDS.filter(c => c.name.toLowerCase().includes(q));
  const container = $('paletteResults');
  container.innerHTML = '';
  if (!filtered.length) { container.innerHTML = '<div class="palette-item" style="color:var(--text-dim);">Nenhum comando</div>'; return; }
  filtered.forEach((cmd, i) => {
    const el = document.createElement('div');
    el.className = 'palette-item' + (i === 0 ? ' selected' : '');
    el.innerHTML = `<span>${cmd.name}</span>${cmd.shortcut ? `<span class="shortcut">${cmd.shortcut}</span>` : ''}`;
    el.onclick = () => { cmd.action(); closePalette(); };
    container.appendChild(el);
  });
}
$('paletteInput')?.addEventListener('input', e => renderPaletteItems(e.target.value));
$('palette')?.addEventListener('click', e => { if (e.target.id === 'palette') closePalette(); });

loadVisualPreferences();
checkSession();

window.addEventListener('load', () => {
  if (!document.body.classList.contains('hide-particles')) initBgParticles();
  const stage = $('reactorStage');
  if (stage) {
    const ro = new ResizeObserver(() => {
      if (stage.clientWidth === 0 || stage.clientHeight === 0) return;
      if (window.initReactor) window.initReactor(); 
    });
    ro.observe(stage);
  }
});

/* ---------- OLHO DE DEUS ---------- */
window.openEyeOfGod = function() {
  document.body.classList.add('hide-sidebar');
  
  const stage = document.getElementById('reactorStage');
  if (stage) {
    stage.style.transition = 'transform 2s cubic-bezier(0.4, 0, 0.2, 1), opacity 2s';
    stage.style.transform = 'scale(3)';
    stage.style.opacity = '0';
  }

  setTimeout(() => {
    const overlay = document.getElementById('eyeOfGod');
    if (overlay) {
      overlay.classList.remove('hidden');
      void overlay.offsetWidth; 
      overlay.classList.add('active');
      if (window.initEyeOfGodGlobe) window.initEyeOfGodGlobe();

      document.querySelectorAll('.eye-menu-btn').forEach(btn => {
        btn.onclick = () => {
          document.querySelectorAll('.eye-menu-btn').forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
          
          const panelTitle = document.querySelector('.eye-panel-header h3');
          const panelSubtitle = document.querySelector('.eye-panel-header span');
          const text = btn.innerText.trim();
          
          if (panelTitle) panelTitle.textContent = text;
          if (panelSubtitle) panelSubtitle.textContent = "Carregando dados...";
          
          if (text.includes('Clima')) {
            if (panelSubtitle) panelSubtitle.textContent = "Ventos, raios e nuvens";
          } else if (text.includes('Câmeras')) {
            if (panelSubtitle) panelSubtitle.textContent = "Trânsito ao vivo";
          } else if (text.includes('Eventos')) {
            if (panelSubtitle) panelSubtitle.textContent = "3 de 4 no mapa";
          }
        };
      });
    }
  }, 1000); 
};

document.getElementById('closeEyeBtn')?.addEventListener('click', () => {
  const overlay = document.getElementById('eyeOfGod');
  if (overlay) overlay.classList.remove('active');
  
  const stage = document.getElementById('reactorStage');
  if (stage) {
    stage.style.transform = 'scale(1)';
    stage.style.opacity = '1';
    setTimeout(() => stage.style.transition = '', 2000);
  }
  
  document.body.classList.remove('hide-sidebar');

  setTimeout(() => {
    if (overlay) overlay.classList.add('hidden');
  }, 1500);
});
