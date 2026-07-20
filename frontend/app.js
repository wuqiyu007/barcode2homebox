// 扫码录入 Homebox 前端逻辑（含登录鉴权）
const $ = (id) => document.getElementById(id);
let currentScan = null;
let lastProduct = null;
let lastBarcode = null;
let locations = [];

// ---------- 鉴权 ----------
function showLogin() {
  $("loginOverlay").classList.remove("hidden");
}
function hideLogin() {
  $("loginOverlay").classList.add("hidden");
}

// 统一封装 fetch：携带凭证，遇 401 弹回登录
async function apiFetch(url, opts = {}) {
  opts.credentials = "same-origin";
  const r = await fetch(url, opts);
  if (r.status === 401) {
    showLogin();
    throw new Error("未登录");
  }
  return r;
}

async function checkAuth() {
  try {
    const r = await fetch("/api/me", { credentials: "same-origin" });
    const d = await r.json();
    if (d.authenticated) {
      hideLogin();
      loadLocations();
      loadCacheStat();
    } else {
      showLogin();
    }
  } catch (_) {
    showLogin();
  }
}

// ---------- 登录 ----------
$("loginBtn").onclick = async () => {
  const email = $("loginEmail").value.trim();
  const pwd = $("loginPassword").value;
  if (!email || !pwd) { setStatus("loginStatus", "请输入邮箱和密码", "err"); return; }
  $("loginBtn").disabled = true;
  setStatus("loginStatus", "登录中…", "");
  try {
    const r = await fetch("/api/login", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password: pwd }),
    });
    const d = await r.json();
    if (r.ok && d.ok) {
      $("loginPassword").value = "";
      hideLogin();
      loadLocations();
      setStatus("loginStatus", "", "");
    } else {
      setStatus("loginStatus", d.detail || "登录失败，请检查邮箱和密码", "err");
    }
  } catch (e) {
    setStatus("loginStatus", "请求失败: " + e.message, "err");
  }
  $("loginBtn").disabled = false;
};

$("togglePwd").onclick = () => {
  const input = $("loginPassword");
  const isText = input.type === "text";
  input.type = isText ? "password" : "text";
  $("eyeIcon").innerHTML = isText
    ? '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>'
    : '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/>';
  $("togglePwd").title = isText ? "显示密码" : "隐藏密码";
};

["loginEmail", "loginPassword"].forEach((id) => {
  $(id).addEventListener("keydown", (e) => { if (e.key === "Enter") $("loginBtn").click(); });
});

// ---------- 登出 ----------
$("logoutBtn").onclick = async () => {
  try { await fetch("/api/logout", { method: "POST", credentials: "same-origin" }); } catch (_) {}
  $("loginEmail").value = "";
  showLogin();
};

// ---------- 页面切换（扫码页 / 设置页）----------
$("navScan").onclick = () => showView("scan");
$("navSettings").onclick = () => showView("settings");
$("settingsBack").onclick = () => showView("scan");

function showView(name) {
  clearFlow();   // 切换页面时清理图片与识别结果，避免残留
  const scan = name === "scan";
  $("scanPage").style.display = scan ? "block" : "none";
  $("settingsPage").classList.toggle("active", !scan);
  $("navScan").classList.toggle("active", scan);
  $("navSettings").classList.toggle("active", !scan);
  if (!scan) {
    stopCam();
    loadSettingsIntoPage();
  }
}

function loadSettingsIntoPage() {
  $("settingsStatus").textContent = "";
  $("gs1TestResult").textContent = "";
  $("gs1TestResult").className = "test-result";
  $("visionTestResult").textContent = "";
  $("visionTestResult").className = "test-result";
  apiFetch("/api/settings")
    .then((r) => r.json())
    .then((d) => {
      $("setGs1Url").value = d.gs1_api_url || "";
      $("setGs1Id").value = d.gs1_secret_id || "";
      $("setGs1Key").value = d.gs1_secret_key || "";
      $("setVisionUrl").value = d.vision_api_url || "";
      $("setVisionKey").value = d.vision_api_key || "";
      $("setVisionModel").value = d.vision_model || "gpt-4o-mini";
    })
    .catch((e) => { if (e.message !== "未登录") setStatus("settingsStatus", "加载失败: " + e.message, "err"); });
}

$("testGs1").onclick = async () => {
  const payload = {
    gs1_api_url: $("setGs1Url").value.trim(),
    gs1_secret_id: $("setGs1Id").value.trim(),
    gs1_secret_key: $("setGs1Key").value,
  };
  $("testGs1").disabled = true;
  $("gs1TestResult").textContent = "测试中…";
  $("gs1TestResult").className = "test-result";
  try {
    const r = await apiFetch("/api/settings/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const d = await r.json();
    if (d.ok) {
      $("gs1TestResult").textContent = "● 连接正常";
      $("gs1TestResult").className = "test-result ok";
    } else {
      $("gs1TestResult").textContent = "✕ " + (d.error || "连接失败");
      $("gs1TestResult").className = "test-result err";
    }
  } catch (e) {
    if (e.message !== "未登录") {
      $("gs1TestResult").textContent = "✕ " + e.message;
      $("gs1TestResult").className = "test-result err";
    }
  }
  $("testGs1").disabled = false;
};

$("testVision").onclick = async () => {
  const payload = {
    target: "vision",
    vision_api_url: $("setVisionUrl").value.trim(),
    vision_api_key: $("setVisionKey").value,
    vision_model: $("setVisionModel").value.trim() || "gpt-4o-mini",
  };
  $("testVision").disabled = true;
  $("visionTestResult").textContent = "测试中…";
  $("visionTestResult").className = "test-result";
  try {
    const r = await apiFetch("/api/settings/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const d = await r.json();
    if (d.ok) {
      $("visionTestResult").textContent = "● 连接正常";
      $("visionTestResult").className = "test-result ok";
    } else {
      $("visionTestResult").textContent = "✕ " + (d.error || "连接失败");
      $("visionTestResult").className = "test-result err";
    }
  } catch (e) {
    if (e.message !== "未登录") {
      $("visionTestResult").textContent = "✕ " + e.message;
      $("visionTestResult").className = "test-result err";
    }
  }
  $("testVision").disabled = false;
};

$("saveSettings").onclick = async () => {
  const payload = {
    gs1_api_url: $("setGs1Url").value.trim(),
    gs1_secret_id: $("setGs1Id").value.trim(),
    gs1_secret_key: $("setGs1Key").value,
    vision_api_url: $("setVisionUrl").value.trim(),
    vision_api_key: $("setVisionKey").value,
    vision_model: $("setVisionModel").value.trim() || "gpt-4o-mini",
  };
  $("saveSettings").disabled = true;
  setStatus("settingsStatus", "保存中…", "");
  try {
    const r = await apiFetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const d = await r.json();
    if (!d.ok) {
      setStatus("settingsStatus", "保存失败: " + JSON.stringify(d), "err");
      $("saveSettings").disabled = false;
      return;
    }
    setStatus("settingsStatus", "✅ 已保存", "ok");
    setTimeout(() => showView("scan"), 900);
  } catch (e) {
    if (e.message !== "未登录") setStatus("settingsStatus", "请求失败: " + e.message, "err");
  }
  $("saveSettings").disabled = false;
};

// ---- 位置列表 ----
async function loadLocations() {
  try {
    const r = await apiFetch("/api/locations");
    if (r.ok) {
      locations = await r.json();
      const sel = $("locationSelect");
      sel.innerHTML = locations.length
        ? locations.map((l) => `<option value="${l.id}">${l.name}</option>`).join("")
        : '<option value="">无位置</option>';
    }
  } catch (_) {}
}

// ---- Tab 切换 ----
document.querySelectorAll(".tab").forEach((t) => {
  t.onclick = () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((x) => x.classList.remove("active"));
    t.classList.add("active");
    $("panel-" + t.dataset.tab).classList.add("active");
    if (t.dataset.tab !== "cam") stopCam();
    // 切换 tab 时清理上一环节残留：
    // 进入「AI 图片识别」清掉扫码信息；切回「条码识别」清掉 AI 图片与识别结果
    clearFlow();
  };
});

// ---- 摄像头 ----
async function startCam() {
  if (typeof ZXing === "undefined") {
    setStatus("camStatus", "ZXing 库未加载", "err");
    return;
  }
  try {
    const reader = new ZXing.BrowserMultiFormatReader();
    let scanned = false;
    $("video").style.display = "block";
    $("camPlaceholder").style.display = "none";
    currentScan = await reader.decodeFromVideoDevice(
      undefined, $("video"),
      (result, err, controls) => {
        if (result && !scanned) {
          scanned = true;
          const code = result.getText();
          setStatus("camStatus", "✅ 识别到条码: " + code, "ok");
          $("startCam").disabled = false;
          $("stopCam").style.display = "none";
          $("stopCam").disabled = true;
          currentScan = null;
          try { controls.stop(); } catch (_) {}
          onBarcode(code);
        }
      }
    );
    $("startCam").disabled = true;
    $("stopCam").style.display = "inline-flex";
    $("stopCam").disabled = false;
    setStatus("camStatus", "📸 将条码对准摄像头…", "");
  } catch (e) {
    $("video").style.display = "none";
    $("camPlaceholder").style.display = "flex";
    setStatus("camStatus", "摄像头启动失败: " + e.message, "err");
  }
}

function stopCam() {
  if (currentScan) {
    try { currentScan.stop(); } catch (_) {}
    currentScan = null;
  }
  $("video").style.display = "none";
  $("camPlaceholder").style.display = "flex";
  $("startCam").disabled = false;
  $("stopCam").style.display = "none";
  $("stopCam").disabled = true;
}

$("startCam").onclick = startCam;
$("stopCam").onclick = stopCam;

// ---- 手动输入条码查询 ----
$("barcodeQuery").onclick = () => {
  const code = $("barcodeInput").value.trim();
  if (!code) { setStatus("camStatus", "请输入条码", "err"); return; }
  onBarcode(code);
};
$("barcodeInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); $("barcodeQuery").click(); }
});

// ---- 文件上传 ----
const uploadZone = $("uploadZone");
const fileInput = $("fileInput");

uploadZone.onclick = () => fileInput.click();
fileInput.onchange = () => {
  if (fileInput.files[0]) {
    $("uploadBtn").disabled = false;
    const preview = $("uploadPreview");
    preview.src = URL.createObjectURL(fileInput.files[0]);
    preview.style.display = "block";
  }
};

uploadZone.ondragover = (e) => { e.preventDefault(); uploadZone.classList.add("dragover"); };
uploadZone.ondragleave = () => uploadZone.classList.remove("dragover");
uploadZone.ondrop = (e) => {
  e.preventDefault();
  uploadZone.classList.remove("dragover");
  if (e.dataTransfer.files.length) {
    fileInput.files = e.dataTransfer.files;
    fileInput.onchange();
  }
};

$("uploadBtn").onclick = async () => {
  const f = fileInput.files[0];
  if (!f) return;
  $("uploadBtn").disabled = true;
  setStatus("fileStatus", "🔍 识别中…", "");
  const fd = new FormData();
  fd.append("image", f);
  try {
    const r = await apiFetch("/api/scan", { method: "POST", body: fd });
    const d = await r.json();
    if (!r.ok) { setStatus("fileStatus", d.detail || "识别失败", "err"); $("uploadBtn").disabled = false; return; }
    showResult(d.product, d.barcode, d.decoded_by);
    setStatus("fileStatus", "", "");
    $("uploadBtn").disabled = false;
  } catch (e) {
    setStatus("fileStatus", "请求失败: " + e.message, "err");
    $("uploadBtn").disabled = false;
  }
};

// ---- 条码查询 ----
async function onBarcode(code) {
  lastBarcode = code;
  setStatus("camStatus", "🔍 查询商品: " + code, "");
  try {
    const r = await apiFetch("/api/lookup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ barcode: code }),
    });
    const d = await r.json();
    showResult(d, code, "barcode");
    setStatus("camStatus", "", "");
  } catch (e) {
    if (e.message !== "未登录") setStatus("camStatus", "查询失败: " + e.message, "err");
  }
}

// ---- 结果展示 ----
function showResult(product, barcode, decodedBy) {
  lastProduct = product;
  if (decodedBy && decodedBy.includes("vision")) {
    // AI 识图结果：条码以识别结果本身为准；纯 AI 识别无条码时应清空旧条码，
    // 防止先扫码未命中再 AI 识别时，仍把上一次扫码的条码带进后端导致 404。
    lastBarcode = product.barcode || "";
  } else if (barcode) {
    lastBarcode = barcode;
  }
  $("result").classList.add("show");

  const imgSrc = product.image ? "/api/proxy-image?url=" + encodeURIComponent(product.image) : "";
  $("rImg").src = imgSrc;
  $("rImg").style.display = imgSrc ? "block" : "none";

  const found = product && product.found;
  $("rName").textContent = found ? product.name : "未找到商品";
  $("qtyInput").value = 1; // 每次新结果显示，数量重置为 1

  let meta = "";
  if (found) {
    if (product.brand) meta += `<span class="label">品牌</span> ${product.brand}<br/>`;
    if (product.specification) meta += `<span class="label">规格</span> ${product.specification}<br/>`;
    if (product.manufacturer) meta += `<span class="label">厂家</span> ${product.manufacturer}<br/>`;
    if (product.category) meta += `<span class="label">品类</span> ${product.category}<br/>`;
    meta += `<span class="label">来源</span> ${product.source || "-"} `;
    if (product.from_cache) meta += `<span class="tag cache">⚡缓存命中</span> `;
    if (decodedBy) meta += `<span class="tag">${decodedBy}</span>`;
  } else {
    meta = `<span class="label">条码</span> ${barcode || ""}<br/>该条码未匹配到商品，请切换到「AI 图片识别」上传商品图片进行识别`;
  }
  $("rMeta").innerHTML = meta;
  // 未匹配到商品时：没有可录入的商品，隐藏「录入」，仅提示用 AI 识别；
  // 命中时才显示「录入」按钮。
  $("addBtn").style.display = found ? "inline-flex" : "none";
  $("aiFallbackBtn").style.display = found ? "none" : "inline-flex";
  loadCacheStat();   // 每次出结果后刷新缓存命中统计
}

// ---- AI 托底：未匹配时切到图片识别 tab ----
$("aiFallbackBtn").onclick = () => {
  const t = document.querySelector('.tab[data-tab="file"]');
  if (t) t.click();
  setStatus("fileStatus", "上传商品图片，AI 将识别名称/品牌/规格/厂家", "");
};

// ---- 录入 ----
$("addBtn").onclick = async () => {
  const btn = $("addBtn");
  btn.disabled = true;
  setStatus("addStatus", "⏳ 正在录入…", "");
  // 入库数量：解析为正整数，非法/缺省归 1
  let qty = parseInt($("qtyInput").value, 10);
  if (!Number.isFinite(qty) || qty < 1) qty = 1;
  const payload = { barcode: lastBarcode || "", quantity: qty };
  const locId = $("locationSelect").value;
  if (locId) payload.locationId = locId;
  if (lastProduct && lastProduct.found) {
    const bc = lastProduct.barcode || lastBarcode || "";
    payload.barcode = bc;
    // AI 识图结果：把名称/品牌/规格/厂家/品类一并传给后端，避免条码兜底后丢失 AI 识别信息
    if (lastProduct.source === "vision-ai") {
      payload.name = lastProduct.name;
      payload.brand = lastProduct.brand;
      payload.specification = lastProduct.specification;
      payload.manufacturer = lastProduct.manufacturer;
      payload.category = lastProduct.category;
    }
  } else if (lastProduct && !lastProduct.found) {
    const name = prompt("未匹配到商品，请手动输入商品名称：");
    if (!name) { setStatus("addStatus", "已取消", "err"); btn.disabled = false; return; }
    payload.name = name;
    payload.brand = lastProduct && lastProduct.brand;
  }
  try {
    const r = await apiFetch("/api/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const d = await r.json();
    if (d.homebox && d.homebox.ok) {
      setStatus("addStatus", "✅ 录入成功！", "ok");
      btn.disabled = true;
      setTimeout(() => { clearFlow(); }, 1200);
    } else {
      setStatus("addStatus", "录入失败: " + (d.homebox?.error || JSON.stringify(d)), "err");
    }
  } catch (e) {
    if (e.message !== "未登录") setStatus("addStatus", "请求失败: " + e.message, "err");
  }
  btn.disabled = false;
};

// ---- 清理流程：隐藏结果卡片、重置状态与上传区，保证扫码/AI 识别之间干净切换 ----
function clearFlow() {
  $("result").classList.remove("show");
  $("addStatus").textContent = "";
  $("qtyInput").value = 1;
  lastProduct = null;
  lastBarcode = null;
  // 重置上传区：清除已选图片与文件，避免 AI 识别的图片/结果残留
  const up = $("uploadPreview");
  if (up) { up.src = ""; up.style.display = "none"; }
  const fi = $("fileInput");
  if (fi) fi.value = "";
  $("uploadBtn").disabled = true;
}

function setStatus(id, msg, cls) {
  const el = $(id);
  el.textContent = msg;
  el.className = "status" + (cls ? " " + cls : "");
}

// ---- 本地缓存统计 + 清空 ----
async function loadCacheStat() {
  try {
    const r = await apiFetch("/api/cache/stats");
    const d = await r.json();
    const el = document.getElementById("cacheStat");
    if (el) el.textContent = `本地缓存 ${d.count} 条 · 累计命中 ${d.hits} 次`;
    const ci = document.getElementById("cacheInfo");
    if (ci) ci.textContent = `已缓存 ${d.count} 条 · 累计命中 ${d.hits} 次`;
  } catch (_) { /* 忽略 */ }
}

$("clearCache").onclick = async () => {
  if (!confirm("确定清空本地条码缓存？已缓存的商品将重新走 GS1 / 视觉查询。")) return;
  try {
    const r = await apiFetch("/api/cache/clear", { method: "POST" });
    const d = await r.json();
    if (d.ok) {
      const ci = $("cacheInfo");
      if (ci) ci.textContent = `已清空（共 ${d.cleared} 条）`;
      loadCacheStat();
    }
  } catch (_) {}
};

// ---- 启动：检查登录态 ----
checkAuth();
