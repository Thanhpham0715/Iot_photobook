'use strict';

const $ = id => document.getElementById(id);
const csrf = document.querySelector('meta[name=csrf-token]').content;

let status = null;
let currentView = 'welcome';
let activeFilters = [];
let prevCountdown = -1;
let prevTaken = 0;
let previewUrl = null;
let previewTimer = null;
let resultTimer = null;
let resultSecondsLeft = 45;
let lastHandledSid = null;
let audioCtx = null;

// Clock
setInterval(() => {
  const now = new Date();
  $('kioskClock').textContent = now.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' });
}, 1000);

// Audio
function getAudioContext() {
  if (!audioCtx) {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (AC) audioCtx = new AC();
  }
  if (audioCtx && audioCtx.state === 'suspended') audioCtx.resume();
  return audioCtx;
}

function playBeep(freq = 880, duration = 0.12, type = 'sine') {
  try {
    const ctx = getAudioContext();
    if (!ctx) return;
    const osc = ctx.createOscillator(), gain = ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, ctx.currentTime);
    gain.gain.setValueAtTime(0.2, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + duration);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + duration);
  } catch (e) {}
}

function playShutterSound() {
  try {
    [520, 680, 850].forEach((freq, idx) => {
      setTimeout(() => playBeep(freq, 0.08, 'triangle'), idx * 25);
    });
  } catch (e) {}
}

function triggerFlash() {
  const flash = $('kioskFlash');
  if (!flash) return;
  flash.classList.add('flash');
  setTimeout(() => flash.classList.remove('flash'), 120);
}

// API
async function api(path, method = 'GET', data) {
  const response = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
    body: data === undefined ? undefined : JSON.stringify(data)
  });
  const res = await response.json();
  if (!response.ok) throw new Error(res.error || 'Yêu cầu thất bại');
  return res;
}

function switchView(viewName) {
  currentView = viewName;
  $('viewWelcome').classList.toggle('active', viewName === 'welcome');
  $('viewStudio').classList.toggle('active', viewName === 'studio');
  $('viewResult').classList.toggle('active', viewName === 'result');
}

// Preview loop
async function previewLoop() {
  if (currentView === 'studio' && status?.owned && status.camera_ready) {
    try {
      const r = await fetch('/api/preview.jpg', { cache: 'no-store' });
      if (r.ok && r.status !== 204) {
        const next = URL.createObjectURL(await r.blob());
        $('kioskPreview').src = next;
        if (previewUrl) URL.revokeObjectURL(previewUrl);
        previewUrl = next;
      }
    } catch (e) {}
  }
  setTimeout(previewLoop, 140);
}

// Show Result Screen with QR Code
function showResultScreen(sid) {
  clearInterval(resultTimer);
  lastHandledSid = sid;
  switchView('result');
  $('kioskResultImg').src = `/download/${sid}/${sid}_strip.jpg?view=1`;
  $('kioskQrCode').src = `/api/qr/${sid}.png`;
  
  resultSecondsLeft = 45;
  $('kioskAutoResetTimer').textContent = `Tự động đón người tiếp theo sau ${resultSecondsLeft}s`;
  resultTimer = setInterval(() => {
    resultSecondsLeft--;
    if (resultSecondsLeft <= 0) {
      clearInterval(resultTimer);
      resetToWelcome();
    } else {
      $('kioskAutoResetTimer').textContent = `Tự động đón người tiếp theo sau ${resultSecondsLeft}s`;
    }
  }, 1000);
}

async function resetToWelcome() {
  clearInterval(resultTimer);
  try {
    await api('/api/release', 'POST', {});
  } catch (e) {}
  switchView('welcome');
}

// Main refresh / status poll
async function refresh() {
  try {
    const s = await api('/api/status');
    status = s;
    $('kioskDot').className = 'dot';
    $('kioskStatusText').textContent = s.state === 'stopped' ? 'Booth đang tắt' :
                                      s.state === 'waiting' ? 'Đang chờ người' :
                                      s.state === 'capturing' ? 'Đang chụp ảnh' : 'Sẵn sàng';

    // Auto-claim if customer approaches or opens palm in welcome view
    if (currentView === 'welcome' && s.state === 'ready' && !s.busy && !s.owned) {
      if (s.distance_m !== null && s.distance_m < 1.2) {
        // Someone stepped up close to the booth!
        await claimBooth();
      }
    }

    // State transition handling
    if (s.owned) {
      // If a capture was completed and we haven't displayed it yet
      if (s.last_sid && s.last_sid !== lastHandledSid && s.state === 'ready') {
        showResultScreen(s.last_sid);
        return;
      }

      if (currentView !== 'result') {
        switchView('studio');
      }

      // Countdown and capture progress
      $('kioskCountdown').hidden = s.state !== 'capturing';
      $('kioskCountdown').textContent = s.countdown || '📸';
      $('kioskProgress').textContent = `Đã chụp ${s.taken} / ${s.total}`;
      $('btnKioskSnap').disabled = s.state !== 'ready';

      if (s.state === 'capturing') {
        if (s.countdown !== prevCountdown && s.countdown > 0) playBeep(880, 0.12);
        if (s.taken > prevTaken) {
          playShutterSound();
          triggerFlash();
        }
        prevCountdown = s.countdown;
        prevTaken = s.taken;
      } else {
        prevCountdown = -1;
        prevTaken = 0;
      }

      // Gesture status display
      const g = s.gesture_diagnostic || {};
      const gestureNames = { Open_Palm: 'Bàn tay mở', Thumb_Up: 'Giơ ngón cái', Victory: 'Chữ V', Pointing_Up: 'Trỏ lên', Closed_Fist: 'Nắm tay' };
      const gLabel = gestureNames[g.label] || g.label || '';
      $('kioskGestureBanner').textContent = s.gesture_active ?
        `Cử chỉ đang bật · ${g.message || 'Sẵn sàng'} · ${gLabel} (${Math.round((g.score || 0)*100)}%)` :
        `Giơ bàn tay mở 🖐 để bật điều khiển không chạm`;

      // Sync active filters on pills
      activeFilters = s.selected_filters || [];
      document.querySelectorAll('#kioskFilterPills .filter-pill').forEach(pill => {
        const fid = pill.dataset.filter;
        if (fid === 'none') {
          pill.classList.toggle('active', activeFilters.length === 0);
        } else {
          pill.classList.toggle('active', activeFilters.includes(fid));
        }
      });
    } else {
      if (currentView === 'studio') {
        switchView('welcome');
      }
    }
  } catch (e) {
    $('kioskDot').className = 'dot offline';
    $('kioskStatusText').textContent = 'Mất kết nối';
  }
}

async function claimBooth() {
  getAudioContext();
  try {
    await api('/api/claim', 'POST', {});
    switchView('studio');
  } catch (e) {
    console.error(e);
  }
}

// Event Bindings
$('btnKioskStart').onclick = () => claimBooth();
$('btnKioskLeave').onclick = () => resetToWelcome();
$('btnNextCustomer').onclick = () => resetToWelcome();

$('btnKioskSnap').onclick = async () => {
  getAudioContext();
  try {
    await api('/api/capture', 'POST', {});
  } catch (e) {
    alert(e.message);
  }
};

// Filter pill clicks
document.querySelectorAll('#kioskFilterPills .filter-pill').forEach(pill => {
  pill.onclick = async () => {
    getAudioContext();
    const fid = pill.dataset.filter;
    let next = [...activeFilters];
    if (fid === 'none') {
      next = [];
    } else if (fid === 'mono' || fid === 'sepia') {
      next = next.filter(f => f !== 'mono' && f !== 'sepia');
      if (!activeFilters.includes(fid)) next.push(fid);
    } else {
      if (next.includes(fid)) next = next.filter(f => f !== fid);
      else next.push(fid);
    }
    try {
      await api('/api/select', 'POST', { filters: next });
      activeFilters = next;
    } catch (e) {}
  };
});

// Start loop
setInterval(refresh, 600);
previewLoop();
