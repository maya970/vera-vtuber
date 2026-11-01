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
  if (!$("in-room").dataset.touched) {
    $("in-room").value = settings.room_id || "";
  }
  $("in-key").placeholder = settings.api_key_set ? "已写在 .env，留空则不修改" : "sk-… 不填也能播，只是没有大模型";

  const rows = [
    ["原始数据库", `${db.sqlite} · ${db.sqlite_size}`],
    ["直播目录", `${db.catalog} · ${db.books || 0} 本`],
    ["接口参数", settings.yaml],
    ["密钥", settings.env],
    ["人设", settings.persona],
  ];
  $("paths").innerHTML = rows.map(([k, v]) => `<li><em>${k}</em><code>${v}</code></li>`).join("");

  if (avatar.ready && avatar.preview) {
    $("preview-row").hidden = false;
    $("preview").src = avatar.preview + "?t=" + encodeURIComponent(avatar.created || Date.now());
    $("avatar-meta").innerHTML = `<b>${avatar.name || "形象"}</b> 已可直播<br>${avatar.layers || 0} 个图层<br>${avatar.created || ""}`;
  }
  $("btn-live").disabled = !avatar.ready;
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
$("in-mode").addEventListener("change", () => { $("in-mode").dataset.touched = "1"; });

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
    bilibili: { room_id: Number($("in-room").value || 0) },
    director: { mode: $("in-mode").value || "chat" },
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
      bilibili: { room_id: Number($("in-room").value || 0) },
      director: { mode: $("in-mode").value || "chat" },
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
    setHint($("live-hint"), "舞台已弹出。下次双击「直播间\\开始直播.bat」。", "ok");
  } catch (err) {
    setHint($("live-hint"), err.message, "bad");
  } finally {
    $("btn-live").disabled = false;
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
