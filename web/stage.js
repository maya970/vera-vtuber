// 薇拉 · 直播舞台
// 立体分层渲染：透视相机 + 分层深度 + 头部转动 + 呼吸/眨眼/口型/发丝摆动
import * as THREE from 'three';

const qs = new URLSearchParams(location.search);
const stageEl = document.getElementById('stage');
const canvas = document.getElementById('gl');
const subtitleWrap = document.getElementById('subtitle-wrap');
const subtitleEl = document.getElementById('subtitle');
const statusPill = document.getElementById('status-pill');
const statusText = document.getElementById('status-text');
const feedEl = document.getElementById('danmaku-feed');
const cardEl = document.getElementById('book-card');
const debugEl = document.getElementById('debug');
const unlockEl = document.getElementById('unlock');
const bootError = document.getElementById('boot-error');

if (qs.get('bg') === 'transparent') {
  stageEl.classList.remove('bg-library');
  stageEl.classList.add('bg-transparent');
  document.documentElement.style.background = 'transparent';
  document.body.style.background = 'transparent';
}
const DEBUG = qs.get('debug') === '1';
if (DEBUG) debugEl.classList.remove('hidden');

const WORLD_H = 9.2;
const HEAD_PARTS = new Set(['face', 'eye', 'brow', 'mouth', 'nose', 'ear', 'hair', 'hair_front', 'hair_back', 'headwear']);

const S = {
  manifest: null,
  scene: null,
  camera: null,
  renderer: null,
  root: null,
  head: null,
  body: null,
  layers: [],
  mouth: null,
  innerMouth: null,
  eyes: [],
  irises: [],
  brows: [],
  hair: [],
  pointer: { x: 0, y: 0, tx: 0, ty: 0 },
  energy: 0,
  mouthOpen: 0,
  blink: 1,
  nextBlink: 3 + Math.random() * 3,
  blinkT: -1,
  mood: 0,
  moodT: 0,
  speaking: false,
  time: 0,
  ws: null,
  wsTries: 0,
  bootedAt: performance.now(),
  fps: 0,
  lastFrame: performance.now(),
  parallax: 1,
};

// ---------------- 渲染器 ----------------
function setupThree() {
  S.scene = new THREE.Scene();
  S.camera = new THREE.PerspectiveCamera(26, window.innerWidth / window.innerHeight, 0.1, 200);
  S.renderer = new THREE.WebGLRenderer({
    canvas,
    alpha: true,
    antialias: true,
    powerPreference: 'high-performance',
    premultipliedAlpha: false,
  });
  S.renderer.setClearColor(0x000000, 0);
  S.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  S.renderer.outputColorSpace = THREE.SRGBColorSpace;
  S.renderer.setSize(window.innerWidth, window.innerHeight, false);

  S.root = new THREE.Group();
  S.scene.add(S.root);
  S.body = new THREE.Group();
  S.head = new THREE.Group();
  S.root.add(S.body, S.head);

  window.addEventListener('resize', onResize);
  window.addEventListener('pointermove', (e) => {
    S.pointer.tx = (e.clientX / window.innerWidth) * 2 - 1;
    S.pointer.ty = (e.clientY / window.innerHeight) * 2 - 1;
  });
}

function onResize() {
  S.camera.aspect = window.innerWidth / window.innerHeight;
  S.camera.updateProjectionMatrix();
  S.renderer.setSize(window.innerWidth, window.innerHeight, false);
  layoutAvatar();
}

// ---------------- 形象构建 ----------------
function clearGroup(group) {
  while (group.children.length) {
    const child = group.children.pop();
    child.traverse?.((o) => {
      o.geometry?.dispose?.();
      if (o.material) {
        if (Array.isArray(o.material)) o.material.forEach((m) => { m.map?.dispose?.(); m.dispose?.(); });
        else { o.material.map?.dispose?.(); o.material.dispose?.(); }
      }
    });
  }
}

function buildAvatar(manifest) {
  if (!manifest || !manifest.layers?.length) {
    bootError.classList.remove('hidden');
    return;
  }
  bootError.classList.add('hidden');
  S.manifest = manifest;
  clearGroup(S.body);
  clearGroup(S.head);
  S.layers = [];
  S.eyes = [];
  S.irises = [];
  S.brows = [];
  S.hair = [];
  S.mouth = null;
  S.innerMouth = null;

  const W = manifest.canvas.width;
  const H = manifest.canvas.height;
  const unit = WORLD_H / H;
  S.unit = unit;
  S.canvasW = W;
  S.canvasH = H;

  const anchors = manifest.anchors || {};
  const neck = anchors.neck || { x: W / 2, y: H * 0.42 };
  const neckX = (neck.x - W / 2) * unit;
  const neckY = -(neck.y - H / 2) * unit;

  const fov = Number(manifest.camera_fov || 26);
  S.camera.fov = fov;
  const dist = (WORLD_H / 2) / Math.tan(THREE.MathUtils.degToRad(fov / 2));
  S.dist = dist;
  S.camera.updateProjectionMatrix();

  S.head.position.set(neckX, neckY, 0);

  const loader = new THREE.TextureLoader();
  const sorted = [...manifest.layers].sort((a, b) => a.order - b.order);
  const mouthAnchor = anchors.mouth || null;

  for (const layer of sorted) {
    const k = (dist - layer.z) / dist;
    const geo = new THREE.PlaneGeometry(layer.w * unit, layer.h * unit, 6, 6);
    const tex = loader.load(`/assets/avatar/${layer.file}`);
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.anisotropy = 4;
    tex.minFilter = THREE.LinearFilter;
    tex.magFilter = THREE.LinearFilter;

    const isMouth = layer.part === 'mouth';
    let mat;
    if (isMouth) {
      mat = new THREE.ShaderMaterial({
        uniforms: {
          map: { value: tex },
          uOpen: { value: 0 },
          uHeight: { value: layer.h * unit },
        },
        vertexShader: `
          varying vec2 vUv;
          uniform float uOpen;
          uniform float uHeight;
          void main() {
            vUv = uv;
            vec3 p = position;
            float lower = smoothstep(0.6, 0.0, uv.y);
            p.y -= lower * uOpen * uHeight * 0.55;
            gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
          }`,
        fragmentShader: `
          varying vec2 vUv;
          uniform sampler2D map;
          void main() {
            vec4 c = texture2D(map, vUv);
            if (c.a < 0.02) discard;
            gl_FragColor = c;
          }`,
        transparent: true,
        depthWrite: false,
        depthTest: false,
      });
    } else {
      mat = new THREE.MeshBasicMaterial({
        map: tex,
        transparent: true,
        depthWrite: false,
        depthTest: false,
        alphaTest: 0.0,
      });
    }

    const mesh = new THREE.Mesh(geo, mat);
    const cx = (layer.x + layer.w / 2 - W / 2) * unit;
    const cy = -(layer.y + layer.h / 2 - H / 2) * unit;
    mesh.position.set(cx, cy, layer.z);
    mesh.scale.set(k, k, 1);
    mesh.renderOrder = layer.order;
    mesh.userData = {
      layer,
      baseX: cx,
      baseY: cy,
      w: layer.w * unit,
      h: layer.h * unit,
      k,
      phase: Math.random() * Math.PI * 2,
    };

    const inHead = HEAD_PARTS.has(layer.part);
    if (inHead) {
      mesh.position.x = cx - neckX;
      mesh.position.y = cy - neckY;
      mesh.userData.baseX = cx - neckX;
      mesh.userData.baseY = cy - neckY;
      S.head.add(mesh);
    } else {
      S.body.add(mesh);
    }

    if (layer.part === 'eye') {
      S.eyes.push(mesh);
      if (/iris|irides|pupil|瞳/i.test(layer.tag)) S.irises.push(mesh);
    }
    if (layer.part === 'brow') S.brows.push(mesh);
    if (layer.part.startsWith('hair')) S.hair.push(mesh);
    if (isMouth) {
      S.mouth = mesh;
      S.mouthAnchor = mouthAnchor || { x: layer.x + layer.w / 2, y: layer.y + layer.h / 2 };
    }

    S.layers.push(mesh);
  }

  // 口腔（嘴张开时露出的暗色）
  if (S.mouth) {
    const mw = S.mouth.userData.w;
    const mh = S.mouth.userData.h;
    const geo = new THREE.CircleGeometry(1, 28);
    const mat = new THREE.MeshBasicMaterial({ color: 0x3a161c, transparent: true, depthWrite: false, depthTest: false });
    const mesh = new THREE.Mesh(geo, mat);
    const anchor = S.mouthAnchor;
    const ax = (anchor.x - W / 2) * unit - neckX;
    const ay = -(anchor.y - H / 2) * unit - neckY;
    mesh.position.set(ax, ay - mh * 0.05, S.mouth.position.z - 0.02);
    mesh.scale.set(mw * 0.42, mh * 0.5, 1);
    mesh.renderOrder = S.mouth.renderOrder - 0.5;
    S.head.add(mesh);
    S.innerMouth = mesh;
    S.innerMouthBase = { x: ax, y: ay - mh * 0.05, sx: mw * 0.42, sy: mh * 0.5 };
  }

  layoutAvatar();
  document.getElementById('live-badge').classList.add('on');
  statusPill.classList.add('on');

  const backdrop = manifest.backdrop ? `/assets/avatar/${manifest.backdrop}` : '';
  if (backdrop) stageEl.style.setProperty('--backdrop', `url("${backdrop}")`);
}

function layoutAvatar() {
  if (!S.manifest || !S.camera) return;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const W = S.canvasW;
  const H = S.canvasH;

  // 与 stage.css 里书卡的 clamp 保持一致，始终为卡片留出位置
  const cardW = Math.min(392, Math.max(190, vw * 0.205));
  const cardLeft = Math.min(60, Math.max(20, vw * 0.03));
  const gutter = Math.max(16, vw * 0.02);
  const leftLimit = cardLeft + cardW + gutter;
  const rightLimit = vw - gutter;
  const available = Math.max(120, rightLimit - leftLimit);

  // 角色高度先按视口 90%，宽度放不下就等比缩小
  let fit = 0.9;
  let avatarW = (W * fit * vh) / H;
  if (avatarW > available) {
    fit = Math.max(0.32, (available * H) / (W * vh));
    avatarW = (W * fit * vh) / H;
  }

  S.root.scale.setScalar(fit);
  const half = (WORLD_H * fit) / 2;
  const y = -(WORLD_H / 2) + half + 0.1;
  // 角色保持在世界原点（与相机光轴重合），避免不同深度图层的透视错位；
  // 构图靠整块画布的 2D 位移完成，不影响层间对齐。
  S.root.position.set(0, y, 0);

  const center = (leftLimit + rightLimit) / 2;
  const shiftPct = ((center - vw / 2) / vw) * 100;
  canvas.style.transform = `translateX(${shiftPct.toFixed(2)}%)`;
}

// ---------------- 动画 ----------------
function animate(now) {
  requestAnimationFrame(animate);
  const dt = Math.min(0.05, (now - S.lastFrame) / 1000);
  S.lastFrame = now;
  S.time += dt;
  const t = S.time;

  S.pointer.x += (S.pointer.tx - S.pointer.x) * Math.min(1, dt * 3);
  S.pointer.y += (S.pointer.ty - S.pointer.y) * Math.min(1, dt * 3);

  // 眨眼
  S.nextBlink -= dt;
  if (S.nextBlink <= 0 && S.blinkT < 0) {
    S.blinkT = 0;
    S.nextBlink = 3.2 + Math.random() * 4.6;
  }
  if (S.blinkT >= 0) {
    S.blinkT += dt;
    const p = S.blinkT;
    let v;
    if (p < 0.06) v = 1 - p / 0.06;
    else if (p < 0.10) v = 0;
    else if (p < 0.22) v = (p - 0.10) / 0.12;
    else { v = 1; S.blinkT = -1; }
    S.blink = v;
  }
  const blinkScale = 0.06 + 0.94 * S.blink;
  for (const eye of S.eyes) {
    const ud = eye.userData;
    eye.scale.y = ud.k * blinkScale;
    eye.position.y = ud.baseY + ud.h * ud.k * (blinkScale - 1) * 0.5;
  }

  // 呼吸
  if (S.body) {
    const breath = 1 + Math.sin(t * 1.55) * 0.0035;
    S.body.scale.set(1, breath, 1);
    S.body.position.y = Math.sin(t * 1.55) * 0.012;
  }

  // 头部：视线 + 轻微晃动 + 说话点头
  const energy = S.energy;
  const yaw = S.pointer.x * 0.11 + Math.sin(t * 0.36) * 0.018;
  const pitch = -S.pointer.y * 0.05 + Math.sin(t * 0.27 + 1.2) * 0.012 - energy * 0.02;
  const roll = Math.sin(t * 0.31) * 0.012;
  if (S.head) {
    S.head.rotation.y += (yaw - S.head.rotation.y) * Math.min(1, dt * 4);
    S.head.rotation.x += (pitch - S.head.rotation.x) * Math.min(1, dt * 4);
    S.head.rotation.z += (roll - S.head.rotation.z) * Math.min(1, dt * 3);
  }

  // 瞳孔跟随
  for (const iris of S.irises) {
    iris.position.x = iris.userData.baseX + S.pointer.x * 0.012;
    iris.position.y = iris.userData.baseY - S.pointer.y * 0.008;
  }

  // 发丝摆动（相位错开）
  for (const hair of S.hair) {
    const ud = hair.userData;
    const amp = ud.layer.part === 'hair_back' ? 0.6 : 1;
    hair.position.x = ud.baseX + Math.sin(t * 0.9 + ud.phase) * 0.012 * amp;
    hair.rotation.z = Math.sin(t * 0.75 + ud.phase) * 0.006 * amp;
  }

  // 眉毛（说话/情绪时轻微抬起）
  const browLift = S.mood * 0.01 + energy * 0.004;
  for (const brow of S.brows) {
    brow.position.y = brow.userData.baseY + browLift;
  }

  // 口型
  const target = S.speaking ? Math.min(0.85, Math.pow(S.energy * 1.8, 0.75)) : 0;
  S.mouthOpen += (target - S.mouthOpen) * Math.min(1, dt * 18);
  if (S.mouth) {
    S.mouth.material.uniforms.uOpen.value = S.mouthOpen * 0.8;
  }
  if (S.innerMouth) {
    const base = S.innerMouthBase;
    const on = S.mouthOpen > 0.04;
    S.innerMouth.visible = on;
    S.innerMouth.scale.y = base.sy * (0.4 + S.mouthOpen * 1.15);
    S.innerMouth.scale.x = base.sx * (0.9 + S.mouthOpen * 0.16);
  }

  // 情绪衰减
  if (S.mood > 0) S.mood = Math.max(0, S.mood - dt * 0.35);

  // 相机：缓慢环绕 + 鼠标视差
  const parallax = S.parallax || 1;
  S.camera.position.x = Math.sin(t * 0.19) * 0.22 * parallax + S.pointer.x * 0.28;
  S.camera.position.y = Math.sin(t * 0.26 + 0.8) * 0.09 * parallax - S.pointer.y * 0.14;
  S.camera.position.z = S.dist + Math.sin(t * 0.13) * 0.06;
  S.camera.lookAt(0, 0.15, 0);

  // 音频能量
  if (analyser) {
    analyser.getByteTimeDomainData(waveData);
    let sum = 0;
    for (let i = 0; i < waveData.length; i++) {
      const v = (waveData[i] - 128) / 128;
      sum += v * v;
    }
    const rms = Math.sqrt(sum / waveData.length);
    const targetEnergy = Math.min(1, rms * 3.2);
    S.energy += (targetEnergy - S.energy) * Math.min(1, dt * 12);
  } else if (S.speaking) {
    S.energy = 0.18 + 0.22 * Math.abs(Math.sin(t * 11.3) * Math.sin(t * 2.7));
  } else {
    S.energy += (0 - S.energy) * Math.min(1, dt * 8);
  }

  S.renderer.render(S.scene, S.camera);

  if (DEBUG) {
    S.fps += (1 / Math.max(dt, 0.001) - S.fps) * 0.05;
    debugEl.textContent =
      `fps ${S.fps.toFixed(0)} | energy ${S.energy.toFixed(2)} | mouth ${S.mouthOpen.toFixed(2)}\n` +
      `layers ${S.layers.length} | blink ${S.blink.toFixed(2)} | ws ${S.ws?.readyState ?? -1}`;
  }
}

// ---------------- 音频 ----------------
let audioCtx = null;
let analyser = null;
let waveData = null;

function ensureAudio() {
  if (!audioCtx) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    audioCtx = new Ctx();
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 512;
    analyser.smoothingTimeConstant = 0.55;
    waveData = new Uint8Array(analyser.fftSize);
    analyser.connect(audioCtx.destination);
  }
  if (audioCtx.state === 'suspended') {
    audioCtx.resume().catch(() => {});
  }
  return audioCtx;
}

document.addEventListener('pointerdown', () => {
  ensureAudio();
  unlockEl.classList.remove('on');
}, { once: false });

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function playClip(clip) {
  ensureAudio();
  const duration = Math.max(0.3, Number(clip.duration) || 0.6);
  if (!clip.file) {
    S.speaking = true;
    await playSilently(clip, duration * 1000);
    return;
  }
  const audio = new Audio(`/audio/${clip.file}`);
  audio.crossOrigin = 'anonymous';
  let source = null;
  if (audioCtx && analyser) {
    try {
      source = audioCtx.createMediaElementSource(audio);
      source.connect(analyser);
    } catch (e) {
      source = null;
    }
  }
  S.speaking = true;
  try {
    await audio.play();
    unlockEl.classList.remove('on');
  } catch (err) {
    unlockEl.classList.add('on');
    await playSilently(clip, duration * 1000);
    return;
  }
  const start = performance.now();
  await new Promise((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      resolve();
    };
    audio.onended = finish;
    audio.onerror = finish;
    const tick = () => {
      if (done) return;
      const elapsed = (performance.now() - start) / 1000;
      showSubtitle(clip.text, Math.min(1, elapsed / duration));
      if (elapsed > duration + 3) finish();
      else requestAnimationFrame(tick);
    };
    tick();
  });
  S.speaking = false;
  if (source) {
    try { source.disconnect(); } catch (e) { /* noop */ }
  }
  showSubtitle(clip.text, 1);
}

async function playSilently(clip, ms) {
  const total = Math.max(300, ms);
  const start = performance.now();
  await new Promise((resolve) => {
    const tick = () => {
      const p = (performance.now() - start) / total;
      if (p >= 1) return resolve();
      showSubtitle(clip.text, p);
      requestAnimationFrame(tick);
    };
    tick();
  });
  S.speaking = false;
  showSubtitle(clip.text, 1);
}

// ---------------- 字幕 ----------------
let subtitleTimer = null;
function showSubtitle(text, progress = 0) {
  if (!text) return;
  subtitleEl.textContent = text;
  subtitleEl.style.setProperty('--p', `${Math.round(progress * 100)}%`);
  subtitleWrap.classList.add('on');
  clearTimeout(subtitleTimer);
  if (progress >= 1) {
    subtitleTimer = setTimeout(() => subtitleWrap.classList.remove('on'), 1200);
  }
}
function hideSubtitle() {
  subtitleWrap.classList.remove('on');
}

// ---------------- 书卡 ----------------
let cardTimer = null;
function showBook(book) {
  if (!book) return;
  document.getElementById('book-title').textContent = `《${book.title}》`;
  document.getElementById('book-author').textContent = book.author || '佚名';
  document.getElementById('book-words').textContent = book.words ? `${(book.words / 10000).toFixed(0)} 万字` : '字数未知';
  document.getElementById('book-status').textContent = book.status || '状态未知';
  document.getElementById('book-index').textContent = `NO. ${String(book.id || 0).padStart(5, '0')}`;
  document.getElementById('book-score').textContent = (book.score || 0).toFixed(1);
  document.getElementById('book-raters').textContent = `${book.raters || 0} 人评分`;
  const stars = Math.round(((book.score || 0) / 10) * 5);
  document.getElementById('book-stars').textContent = '★'.repeat(stars) + '☆'.repeat(5 - stars);
  const tags = document.getElementById('book-tags');
  tags.innerHTML = '';
  for (const tag of (book.tags || []).slice(0, 6)) {
    const span = document.createElement('span');
    span.textContent = tag;
    tags.appendChild(span);
  }
  cardEl.classList.remove('hidden');
  requestAnimationFrame(() => cardEl.classList.add('show'));
  clearTimeout(cardTimer);
  cardTimer = setTimeout(hideBook, 40000);
  S.mood = 1;
}
function hideBook() {
  cardEl.classList.remove('show');
  clearTimeout(cardTimer);
  cardTimer = setTimeout(() => cardEl.classList.add('hidden'), 600);
}

// ---------------- 弹幕 / 状态 ----------------
function addDanmaku(item) {
  const div = document.createElement('div');
  div.className = 'dm';
  if (item.kind === 'super_chat') div.classList.add('super');
  if (item.kind === 'gift') div.classList.add('gift');
  const u = document.createElement('span');
  u.className = 'u';
  u.textContent = item.kind === 'super_chat' ? `SC ¥${item.price || 0}` : item.user || '观众';
  const t = document.createElement('span');
  t.textContent = item.text || '';
  div.append(u, t);
  feedEl.appendChild(div);
  while (feedEl.children.length > 8) feedEl.removeChild(feedEl.firstChild);
  setTimeout(() => {
    div.classList.add('out');
    setTimeout(() => div.remove(), 600);
  }, 26000);
}

function setStatus(text, thinking = false) {
  statusText.textContent = text;
  statusPill.classList.toggle('thinking', thinking);
}

// ---------------- 语音队列 ----------------
const speechQueue = [];
let pumping = false;

async function pumpSpeech() {
  if (pumping) return;
  pumping = true;
  while (speechQueue.length) {
    const msg = speechQueue.shift();
    try {
      for (const clip of msg.clips || []) {
        await playClip(clip);
      }
    } catch (e) {
      console.error(e);
    } finally {
      hideSubtitle();
      S.speaking = false;
      if (msg.id && S.ws && S.ws.readyState === 1) {
        S.ws.send(JSON.stringify({ type: 'ack', id: msg.id }));
      }
    }
  }
  pumping = false;
}

function speak(msg) {
  speechQueue.push(msg);
  pumpSpeech();
}

// ---------------- WebSocket ----------------
function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  S.ws = ws;

  ws.onopen = () => {
    S.wsTries = 0;
    setStatus('已连接', false);
    ws.send(JSON.stringify({ type: 'ready' }));
    ws.send(JSON.stringify({ type: 'viewport', w: window.innerWidth, h: window.innerHeight, dpr: window.devicePixelRatio }));
  };
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (e) { return; }
    handleMessage(msg);
  };
  ws.onclose = () => {
    setStatus('连接断开，重连中', false);
    S.wsTries += 1;
    setTimeout(connect, Math.min(1000 * 1.6 ** S.wsTries, 15000));
  };
  ws.onerror = () => {};
}

function applyScene(cfg, manifest) {
  const mode = cfg?.director?.mode || 'chat';
  const labels = { chat: '闲聊', game: '游戏陪播', book: '讲书' };
  const sub = document.getElementById('live-sub');
  if (sub) sub.textContent = labels[mode] || '直播';
  const speaker = document.getElementById('speaker');
  if (speaker && manifest?.name) speaker.textContent = manifest.name;
  if (mode !== 'book') hideBook();
}

function handleMessage(msg) {
  switch (msg.type) {
    case 'init': {
      if (msg.config?.avatar) S.parallax = Number(msg.config.avatar.parallax ?? 1);
      if (msg.config) applyScene(msg.config, msg.manifest);
      if (msg.manifest) {
        const stamp = `${msg.manifest.created}|${msg.manifest.layers?.length}`;
        if (S.manifestStamp !== stamp) {
          S.manifestStamp = stamp;
          buildAvatar(msg.manifest);
        }
      } else {
        bootError.classList.remove('hidden');
      }
      if (msg.state) {
        setStatus(msg.state.busy ? '说话中' : '待机', !!msg.state.thinking);
        const mode = msg.config?.director?.mode || 'chat';
        if (mode === 'book' && msg.state.busy && msg.state.current_book) showBook(msg.state.current_book);
      }
      break;
    }
    case 'speech':
      if (msg.meta?.book) showBook(msg.meta.book);
      speak(msg);
      break;
    case 'book':
      if (msg.action === 'hide') hideBook();
      else showBook(msg.book);
      break;
    case 'danmaku':
      addDanmaku({ ...msg, kind: msg.kind || 'danmaku' });
      break;
    case 'enter':
      break;
    case 'gift':
      addDanmaku({ kind: 'gift', user: msg.user, text: `送出 ${msg.text}` });
      break;
    case 'status':
      if (msg.thinking) setStatus('思考中…', true);
      else if (msg.busy) setStatus('说话中', false);
      else setStatus('待机', false);
      break;
    case 'thinking':
      if (msg.on) setStatus('思考中…', true);
      break;
    case 'config':
      if (msg.config?.avatar) S.parallax = Number(msg.config.avatar.parallax ?? 1);
      if (msg.config) applyScene(msg.config, S.manifest);
      break;
    default:
      break;
  }
}

// ---------------- 启动 ----------------
setupThree();
requestAnimationFrame(animate);
connect();

// 断线时自动重连心跳
setInterval(() => {
  if (S.ws && S.ws.readyState === 1) {
    S.ws.send(JSON.stringify({ type: 'ping' }));
  }
}, 20000);

// 页面可见性变化时恢复音频
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) ensureAudio();
});

if (qs.get('autoplay') === '1') {
  ensureAudio();
}
