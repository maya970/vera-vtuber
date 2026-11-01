const $ = (id) => document.getElementById(id);
let picked = null;
let pollTimer = 0;

function setHint(el, text, cls) {
  el.textContent = text || "";
  el.className = "hint" + (cls ? " " + cls : "");
}

async function readJSON(resp) {
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const detail = data.detail;
    const msg = typeof detail === "string" ? detail : (detail ? JSON.stringify(detail) : resp.statusText);
    throw new Error(msg || "请求失败");
  }
  return data;
}

function renderInfo(info) {
  const avatar = info.avatar || {};
  const db = info.database || {};
  const settings = info.settings || {};
  const standrig = info.standrig || {};
  $("prompt").textContent = standrig.prompt || "";
  $("link-standrig").href = standrig.repo || $("link-standrig").href;
  $("link-connect").href = standrig.connect || $("link-connect").href;
  $("link-tweet").href = standrig.tweet || $("link-tweet").href;
  $("standrig-hint").textContent = standrig.psd
    ? `当前 PSD：${standrig.psd}。工作区：${standrig.workdir}`
    : "还没有 PSD。上传图片并生成后，会自动准备 StandRig 工作区。";

  if (!$("in-name").dataset.touched) {
    if (avatar.name) $("in-name").value = avatar.name;
  }
  if (!$("in-mode").dataset.touched) {
    $("in-mode").value = settings.mode || "chat";
  }
  if (!$("in-platform").dataset.touched) {
    $("in-platform").value = settings.platform || "bilibili";
  }
  if (!$("in-room").dataset.touched) {
    $("in-room").value = settings.target || "";
  }
  roomHint();
  $("in-key").placeholder = settings.api_key_set ? "已写在 .env，留空则不修改" : "sk-… 不填也能播，只是没有大模型";

  const rows = [
    ["原始数据库", `${db.sqlite} · ${db.sqlite_size}`],
    ["直播目录", `${db.catalog} · ${db.books || 0} 本`],
    ["接口参数", settings.yaml],
    ["密钥", settings.env],
    ["人设", settings.persona],
  ];
  $("paths").innerHTML = rows.map(([k, v]) => `<li><em>${k}</em><code>${v}</code></li>`).join("");

  const start = info.start || {};
  const ready = [
    ["图片", !!start.image, start.image ? "已有形象" : "还没有"],
    ["数据库", !!start.database, start.database ? `${start.books || 0} 本` : "还没有"],
    ["游戏", !!start.game, start.game ? (start.game_name || "已选择") : "还没有"],
  ];
  $("ready").innerHTML = ready.map(([k, ok, v]) => `<li class="${ok ? "yes" : "no"}"><em>${k}</em><code>${v}</code></li>`).join("");
  if (!$("in-game").dataset.touched) {
    $("in-game").value = (info.game && info.game.exe) || "";
  }

  if (avatar.ready && avatar.preview) {
    $("preview-row").hidden = false;
    $("preview").src = avatar.preview + "?t=" + encodeURIComponent(avatar.created || Date.now());
    $("avatar-meta").innerHTML = `<b>${avatar.name || "形象"}</b> 已可直播<br>${avatar.layers || 0} 个图层<br>${avatar.created || ""}`;
  }
  $("btn-live").disabled = !start.ok;
  if (info.show && info.show.ready) {
    $("btn-folder").hidden = false;
    setHint($("live-hint"), "直播间文件夹已生成。下次双击「直播间\\开始直播.bat」。", "ok");
  }
  if (info.job && info.job.status === "running") watchJob(info.job.id);
}

async function loadInfo() {
  const resp = await fetch("/api/studio");
  renderInfo(await readJSON(resp));
}

function chooseFile(file) {
  picked = file || null;
  $("drop-title").textContent = picked ? picked.name : "点击或拖入立绘";
  $("btn-gen").disabled = !picked;
}

$("file").addEventListener("change", () => chooseFile($("file").files[0]));
const drop = $("drop");
["dragenter", "dragover"].forEach((name) => drop.addEventListener(name, (ev) => {
  ev.preventDefault();
  drop.classList.add("over");
}));
["dragleave", "drop"].forEach((name) => drop.addEventListener(name, (ev) => {
  ev.preventDefault();
  drop.classList.remove("over");
}));
drop.addEventListener("drop", (ev) => {
  const file = ev.dataTransfer.files && ev.dataTransfer.files[0];
  if (file) chooseFile(file);
});

$("in-name").addEventListener("input", () => { $("in-name").dataset.touched = "1"; });
$("in-room").addEventListener("input", () => { $("in-room").dataset.touched = "1"; });
$("in-platform").addEventListener("change", () => {
  $("in-platform").dataset.touched = "1";
  roomHint();
});

const ROOM_HINT = {
  off: "不接弹幕。推流仍然是把网页推进 OBS 或各平台的直播工具。",
  bilibili: "填 B 站房间号，或直播间网址。一直没有弹幕时，再到控制台补 Cookie。",
  youtube: "填直播网址、视频号，或 @频道。公开直播不用另办钥匙。",
  twitch: "填频道名，或 twitch.tv/频道。公开聊天不用登录。",
  kick: "填频道名，或 kick.com/频道。",
  douyu: "填房间号，或斗鱼直播间网址。",
};

function roomHint() {
  const platform = $("in-platform").value || "off";
  $("room-hint").textContent = ROOM_HINT[platform] || "";
}
$("in-mode").addEventListener("change", () => { $("in-mode").dataset.touched = "1"; });
$("in-game").addEventListener("input", () => { $("in-game").dataset.touched = "1"; });

function showLog(job) {
  const box = $("gen-log");
  box.hidden = false;
  box.textContent = (job.log || []).join("\n");
  box.scrollTop = box.scrollHeight;
}

async function watchJob(id) {
  if (pollTimer) clearInterval(pollTimer);
  $("btn-gen").disabled = true;
  setHint($("gen-hint"), "正在生成，请不要关这个页面。", "wait");
  const tick = async () => {
    try {
      const resp = await fetch("/api/avatar/job/" + id);
      const job = await readJSON(resp);
      showLog(job);
      if (job.status === "running") return;
      clearInterval(pollTimer);
      pollTimer = 0;
      $("btn-gen").disabled = !picked;
      if (job.status === "done") {
        setHint($("gen-hint"), job.fallback ? "已用整图兜底，可以开播。远程分层没成功。" : "形象已生成，可以开始直播。", job.fallback ? "" : "ok");
        $("in-name").dataset.touched = "";
        await loadInfo();
      } else {
        setHint($("gen-hint"), job.error || "生成失败", "bad");
      }
    } catch (err) {
      clearInterval(pollTimer);
      pollTimer = 0;
      setHint($("gen-hint"), err.message, "bad");
    }
  };
  await tick();
  pollTimer = setInterval(tick, 1500);
}

$("btn-gen").addEventListener("click", async () => {
  if (!picked) return;
  const body = new FormData();
  body.append("file", picked);
  body.append("name", $("in-name").value.trim() || "薇拉");
  $("btn-gen").disabled = true;
  setHint($("gen-hint"), "正在上传…");
  try {
    const resp = await fetch("/api/avatar/generate", { method: "POST", body });
    const job = await readJSON(resp);
    showLog(job);
    watchJob(job.id);
  } catch (err) {
    $("btn-gen").disabled = false;
    setHint($("gen-hint"), err.message, "bad");
  }
});

$("btn-save").addEventListener("click", async () => {
  const patch = {
    director: { mode: $("in-mode").value || "chat" },
    game: { exe: $("in-game").value.trim() },
    chat: { platform: $("in-platform").value || "off", target: $("in-room").value.trim() },
  };
  const key = $("in-key").value.trim();
  if (key) patch.deepseek = { api_key: key };
  setHint($("save-hint"), "保存中…");
  try {
    const resp = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    await readJSON(resp);
    $("in-key").value = "";
    setHint($("save-hint"), "已写入 config 和 .env", "ok");
    await loadInfo();
  } catch (err) {
    setHint($("save-hint"), err.message, "bad");
  }
});

$("btn-live").addEventListener("click", async () => {
  $("btn-live").disabled = true;
  setHint($("live-hint"), "正在弹出直播窗口…");
  try {
    const key = $("in-key").value.trim();
    const patch = {
      director: { mode: $("in-mode").value || "chat" },
      game: { exe: $("in-game").value.trim() },
      chat: { platform: $("in-platform").value || "off", target: $("in-room").value.trim() },
    };
    if (key) patch.deepseek = { api_key: key };
    await readJSON(await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }));
    const resp = await fetch("/api/show/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: $("in-name").value.trim() || "薇拉" }),
    });
    const data = await readJSON(resp);
    $("in-key").value = "";
    $("btn-folder").hidden = false;
    const basis = (data.show && data.show.basis) || {};
    const used = ["图片", "数据库", "游戏"].filter((label, i) => basis[["image", "database", "game"][i]]);
    setHint($("live-hint"), `已开始（${used.join("、") || "已就绪"}）。舞台已弹出。下次双击「直播间\\开始直播.bat」。`, "ok");
  } catch (err) {
    setHint($("live-hint"), err.message, "bad");
  } finally {
    $("btn-live").disabled = false;
  }
});

$("btn-game").addEventListener("click", async () => {
  setHint($("save-hint"), "正在打开选择窗口…", "wait");
  try {
    const data = await readJSON(await fetch("/api/game/pick", { method: "POST" }));
    if (!data.exe) {
      setHint($("save-hint"), "没有选择游戏");
      return;
    }
    $("in-game").value = data.exe;
    $("in-game").dataset.touched = "1";
    setHint($("save-hint"), "已选中游戏", "ok");
    await loadInfo();
  } catch (err) {
    setHint($("save-hint"), err.message, "bad");
  }
});

$("btn-folder").addEventListener("click", async () => {
  try {
    await readJSON(await fetch("/api/show/open", { method: "POST" }));
  } catch (err) {
    setHint($("live-hint"), err.message, "bad");
  }
});

loadInfo().catch((err) => setHint($("gen-hint"), err.message, "bad"));
