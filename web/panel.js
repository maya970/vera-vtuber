const $ = (id) => document.getElementById(id);
let firstLoad = true;
let logSince = 0;

function setTag(el, text, cls) {
  el.textContent = text;
  el.className = 'tag' + (cls ? ' ' + cls : '');
}

async function getJSON(url, options) {
  const resp = await fetch(url, options);
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return resp.json();
}

async function loadState() {
  try {
    const s = await getJSON('/api/state');
    const cfg = s.config;
    if (firstLoad) {
      $('in-room').value = cfg.bilibili.room_id || '';
      $('in-sess').placeholder = cfg.bilibili.sessdata_set ? '已设置，留空不修改' : '可选：登录 Cookie';
      $('in-key').placeholder = cfg.deepseek.api_key_set ? '已设置，留空不修改' : 'sk-… 未设置时用本地兜底话术';
      $('in-idle').value = cfg.director.idle_book_after;
      if ($('in-mode')) $('in-mode').value = cfg.director.mode || 'chat';
      $('st-avatar').textContent = s.manifest ? '已加载分层形象' : '未生成';
      $('st-avatar').className = 'tag ' + (s.manifest ? 'ok' : 'bad');
      $('stage-url').textContent = `${location.origin}/stage`;
      firstLoad = false;
    }
    setTag($('st-catalog'), `${s.catalog.count} 本 · ${s.catalog.with_quotes || 0} 本带短评`,
      s.catalog.count > 0 ? 'ok' : 'bad');
    setTag($('st-bili'), s.bili.status || '未连接', s.bili.connected ? 'ok' : (s.bili.room_id ? 'wait' : ''));
    const aud = !!s.director.audience;
    const viewers = s.director.viewers || 0;
    setTag(
      $('st-audience'),
      aud ? (viewers > 0 ? `在线 ${viewers} · 用 API` : '有人互动 · 用 API') : '无人 · 固定话术省 token',
      aud ? 'ok' : '',
    );
    $('st-played').textContent = `${(s.played || []).length} 本`;
    $('st-api').textContent = `${s.director.stats?.api_calls || 0} 次`;
    renderPlayed(s.played || []);
    document.getElementById('svc-dot').classList.toggle('ok', s.brain_ready && s.bili.connected);
  } catch (e) {
    console.warn(e);
  }
}

async function checkHealth() {
  setTag($('st-deepseek'), '检测中', 'wait');
  setTag($('st-tts'), '检测中', 'wait');
  try {
    const h = await getJSON('/api/health');
    setTag($('st-deepseek'), h.deepseek.ok ? '在线' : (h.deepseek.message || '未配置'),
      h.deepseek.ok ? 'ok' : 'bad');
    setTag($('st-tts'), h.tts.ok ? h.tts.message : (h.tts.message || '不可用'),
      h.tts.ok ? 'ok' : 'bad');
    const dot = $('svc-dot');
    dot.className = 'status-dot ' + (h.deepseek.ok && h.tts.ok ? 'ok' : 'bad');
    $('svc-text').textContent = h.deepseek.ok && h.tts.ok ? '链路正常' : '有项目异常';
  } catch (e) {
    setTag($('st-deepseek'), '检测失败', 'bad');
    setTag($('st-tts'), '检测失败', 'bad');
  }
}

async function loadVoices() {
  try {
    const data = await getJSON('/api/voices');
    if (!data.ok) return;
    const sel = $('in-voice');
    const current = sel.value;
    sel.innerHTML = '';
    for (const v of data.voices) {
      const opt = document.createElement('option');
      opt.value = v.name;
      opt.textContent = `${v.name.replace('zh-CN-', '').replace('Neural', '')} · ${v.gender === 'Female' ? '女' : '男'}`;
      sel.appendChild(opt);
    }
    const state = await getJSON('/api/state');
    sel.value = current || state.tts_voice;
  } catch (e) {
    console.warn(e);
  }
}

function renderPlayed(items) {
  const box = $('played-list');
  if (!items.length) {
    box.innerHTML = '<li class="empty">还没有开播记录</li>';
    return;
  }
  box.innerHTML = '';
  for (const item of items) {
    const li = document.createElement('li');
    const name = document.createElement('span');
    name.textContent = `《${item.title}》`;
    const score = document.createElement('span');
    score.className = 'score';
    score.textContent = Number(item.score || 0).toFixed(1);
    li.append(name, score);
    box.appendChild(li);
  }
}

async function saveConfig() {
  const hint = $('save-hint');
  const patch = {
    bilibili: {
      room_id: Number($('in-room').value || 0),
    },
    tts: { voice: $('in-voice').value },
    director: {
      idle_book_after: Number($('in-idle').value || 30),
      mode: $('in-mode') ? $('in-mode').value : 'chat',
    },
  };
  if ($('in-key').value.trim()) patch.deepseek = { api_key: $('in-key').value.trim() };
  if ($('in-sess').value.trim()) patch.bilibili.sessdata = $('in-sess').value.trim();
  try {
    await getJSON('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
    $('in-key').value = '';
    $('in-sess').value = '';
    hint.textContent = '已保存';
    hint.className = 'hint ok';
    setTimeout(() => { hint.textContent = ''; }, 2500);
    checkHealth();
  } catch (e) {
    hint.textContent = '保存失败：' + e.message;
    hint.className = 'hint bad';
  }
}

async function action(url, body, btn) {
  if (btn) btn.disabled = true;
  try {
    await getJSON(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : '{}',
    });
  } catch (e) {
    console.warn(e);
  } finally {
    if (btn) setTimeout(() => { btn.disabled = false; }, 600);
  }
}

async function pollLogs() {
  try {
    const data = await getJSON(`/api/logs?since=${logSince}`);
    if (data.logs.length) {
      const box = $('log-box');
      for (const item of data.logs) {
        const line = document.createElement('div');
        line.className = 'log-line ' + (item.level === 'WARNING' ? 'warn' : item.level === 'ERROR' ? 'error' : 'info');
        const t = new Date(item.ts * 1000);
        const hh = String(t.getHours()).padStart(2, '0');
        const mm = String(t.getMinutes()).padStart(2, '0');
        const ss = String(t.getSeconds()).padStart(2, '0');
        line.innerHTML = `<span class="t">${hh}:${mm}:${ss}</span><span class="m"></span>`;
        line.querySelector('.m').textContent = item.msg;
        box.appendChild(line);
      }
      while (box.children.length > 300) box.removeChild(box.firstChild);
      box.scrollTop = box.scrollHeight;
      logSince = data.now;
    } else {
      logSince = data.now;
    }
  } catch (e) {
    /* ignore */
  }
}

window.addEventListener('DOMContentLoaded', async () => {
  await loadVoices();
  await loadState();
  checkHealth();
  pollLogs();
  setInterval(loadState, 3000);
  setInterval(pollLogs, 1500);

  $('btn-save').onclick = saveConfig;
  $('btn-health').onclick = checkHealth;
  $('btn-voices').onclick = loadVoices;
  $('btn-say').onclick = () => {
    const text = $('in-say').value.trim();
    if (!text) return;
    action('/api/say', { text, user: '主播' }, $('btn-say'));
    $('in-say').value = '';
  };
  $('btn-dm').onclick = () => {
    const text = $('in-dm').value.trim();
    if (!text) return;
    action('/api/danmaku/test', { user: $('in-user').value || '测试观众', text }, $('btn-dm'));
    $('in-dm').value = '';
  };
  $('btn-next').onclick = () => action('/api/next', null, $('btn-next'));
  $('btn-bili').onclick = () => action('/api/bili/restart', null, $('btn-bili'));
  $('btn-reload').onclick = () => action('/api/catalog/reload', null, $('btn-reload'));

  $('in-say').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('btn-say').click(); });
  $('in-dm').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('btn-dm').click(); });
});
