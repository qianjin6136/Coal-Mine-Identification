"use strict";

const MODULE_LABELS = {
  tool_and_safety_sign: "工具 / 安全标牌",
  coal_presence: "煤堆检测",
  station_number: "工位编号",
  digital_meter: "数字表",
  analog_meter: "指针表",
};

const STATUS_LABELS = {
  received: "识别中",
  processed: "已完成",
  failed: "失败",
};

const BATCH_STATUS_LABELS = {
  discovered: "待导入",
  awaiting_detection_confirmation: "等待检测确认",
  queued: "等待处理",
  running: "正在处理",
  completed: "处理完成",
  completed_with_errors: "部分完成",
  failed: "处理失败",
};

const RECOGNITION_STATUS_LABELS = {
  processing: "识别中",
  recognized: "识别成功",
  partial: "部分识别",
  unrecognized: "未识别成功",
  failed: "任务失败",
  detected: "检测成功",
};

const REASON_LABELS = {
  no_confirmed_station_number_readings: "没有获得可信的工位编号读数",
  station_number_confidence_below_threshold: "编号候选置信度低于阈值",
  multi_frame_station_numbers_do_not_agree: "多帧工位编号结果不一致",
  multi_frame_majority_confirmed: "多帧多数结果已确认",
  station_number_template_confirmed: "编号模板匹配已确认",
  station_image_classifier_not_trained: "工位编号识别模型尚未训练",
  station_id_is_not_numeric: "任务工位不是数字编号",
  station_number_outside_1_to_10: "工位编号不在允许范围内",
  station_number_outside_allowed_range: "工位编号不在当前模型支持范围内",
  station_marker_not_detected: "YOLO 未检测到编号牌，未执行编号读取",
  invalid_station_marker_bbox: "YOLO 编号牌目标框无效",
  no_confirmed_frame_readings: "没有获得可信的数字表读数",
  coal_detector_not_configured: "煤堆检测模型未配置",
  normal_and_abnormal_reference_images_are_not_available: "缺少正常与异常参考图",
  final_class_list_is_not_frozen: "最终类别清单尚未冻结",
  "normal and abnormal reference images are not available": "缺少正常与异常参考图",
  "final class list is not frozen": "最终类别清单尚未冻结",
  disabled_by_configuration: "已在配置中停用",
};

const state = {
  files: [],
  metadata: null,
  metadataName: null,
  limits: {
    max_images: 5,
    max_image_bytes: 20 * 1024 * 1024,
    accepted_image_types: ["image/jpeg", "image/png", "image/bmp"],
  },
  runtime: null,
  offlineBatches: [],
  inboxPath: "dataset_inbox",
  batchActions: new Set(),
  records: [],
  selectedRecordIds: new Set(),
  deletingRecords: false,
  page: 0,
  pageSize: 50,
  total: 0,
  selectedCaptureId: null,
  selectedCapture: null,
  correctedObjects: [],
  uploading: false,
  switchingMode: false,
  retryJob: null,
  pollTimer: null,
  searchTimer: null,
};

const byId = (id) => document.getElementById(id);

function showToast(message, error = false) {
  const toast = document.createElement("div");
  toast.className = `toast${error ? " error" : ""}`;
  toast.textContent = message;
  byId("toast-region").appendChild(toast);
  window.setTimeout(() => toast.remove(), 4200);
}

async function apiFetch(url, options = {}) {
  const response = await fetch(url, options);
  let data = null;
  try {
    data = await response.json();
  } catch (_error) {
    data = null;
  }
  if (!response.ok) {
    const detail = data && data.detail ? data.detail : `请求失败（HTTP ${response.status}）`;
    throw new Error(detail);
  }
  return data;
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("zh-CN", {
    hour12: false,
  });
}

async function loadOfflineBatches() {
  try {
    const payload = await apiFetch("/api/v1/offline-batches");
    state.offlineBatches = payload.items || [];
    state.inboxPath = payload.inbox_path || "dataset_inbox";
    byId("inbox-path").textContent = state.inboxPath;
    renderOfflineBatches();
    renderBatchFilter();
  } catch (error) {
    const list = byId("offline-batch-list");
    list.replaceChildren();
    const empty = document.createElement("div");
    empty.className = "batch-empty";
    empty.textContent = `收件箱扫描失败：${error.message}`;
    list.appendChild(empty);
  }
}

function renderBatchFilter() {
  const select = byId("batch-filter");
  const selected = select.value;
  select.replaceChildren();
  const all = document.createElement("option");
  all.value = "";
  all.textContent = "全部批次";
  select.appendChild(all);
  state.offlineBatches.forEach((batch) => {
    if (batch.status === "discovered" && !batch.capture_succeeded) return;
    const option = document.createElement("option");
    option.value = batch.batch_id;
    option.textContent = batch.batch_id;
    select.appendChild(option);
  });
  if ([...select.options].some((option) => option.value === selected)) {
    select.value = selected;
  }
  updateExportLinks();
}

function updateExportLinks() {
  const filters = new URLSearchParams();
  const status = byId("status-filter").value;
  const station = byId("station-filter").value.trim();
  const batch = byId("batch-filter").value;
  if (status) filters.set("status", status);
  if (station) filters.set("station_id", station);
  if (batch) filters.set("batch_id", batch);
  const suffix = filters.toString();
  byId("export-csv").href = `/api/v1/export?format=csv${suffix ? `&${suffix}` : ""}`;
  byId("export-json").href = `/api/v1/export?format=json${suffix ? `&${suffix}` : ""}`;
}

function renderOfflineBatches() {
  const list = byId("offline-batch-list");
  list.replaceChildren();
  if (!state.offlineBatches.length) {
    const empty = document.createElement("div");
    empty.className = "batch-empty";
    empty.textContent = "未发现离线数据，请把 U 盘根目录的 gas、thermal、visible 复制到收件箱。";
    list.appendChild(empty);
    return;
  }
  state.offlineBatches.forEach((batch) => list.appendChild(offlineBatchCard(batch)));
}

function offlineBatchCard(batch) {
  const card = document.createElement("article");
  card.className = `offline-batch-card ${batch.status}`;

  const heading = document.createElement("div");
  heading.className = "batch-card-heading";
  const name = document.createElement("strong");
  name.textContent = batch.batch_id;
  name.title = batch.batch_id;
  const stateChip = document.createElement("span");
  stateChip.className = `batch-state ${batch.status}`;
  stateChip.textContent = BATCH_STATUS_LABELS[batch.status] || batch.status;
  heading.append(name, stateChip);
  card.appendChild(heading);

  const metrics = document.createElement("div");
  metrics.className = "batch-metrics";
  const finished = Number(batch.capture_succeeded || 0) + Number(batch.capture_failed || 0);
  metrics.append(
    batchMetric("可见光抓拍", `${finished} / ${batch.capture_total || 0}`,
      `${batch.capture_failed || 0} 个失败`),
    batchMetric("气体记录", String(batch.gas_row_count || 0), "已归档，待模块分析"),
    batchMetric("红外热像", String(batch.thermal_frame_count || 0), "已归档，待模块分析"),
  );
  card.appendChild(metrics);

  const progressCopy = document.createElement("div");
  progressCopy.className = "batch-progress-copy";
  const stage = document.createElement("span");
  stage.textContent = batchProgressText(batch);
  const percent = document.createElement("strong");
  percent.textContent = `${Math.round(Number(batch.progress_percent || 0))}%`;
  progressCopy.append(stage, percent);
  const track = document.createElement("div");
  track.className = "batch-progress-track";
  const value = document.createElement("div");
  value.className = "batch-progress-value";
  value.style.width = `${Math.max(0, Math.min(100, Number(batch.progress_percent || 0)))}%`;
  track.appendChild(value);
  card.append(progressCopy, track);

  const diagnostics = [...(batch.diagnostics || [])];
  if (batch.first_item_error) diagnostics.unshift({message: batch.first_item_error});
  if (batch.error) diagnostics.unshift({message: batch.error});
  if (diagnostics.length) {
    const warnings = document.createElement("ul");
    warnings.className = "batch-diagnostics";
    diagnostics.slice(0, 3).forEach((entry) => {
      const item = document.createElement("li");
      const location = entry.path ? `${entry.path}：` : "";
      item.textContent = `${location}${entry.message || "数据校验警告"}`;
      warnings.appendChild(item);
    });
    if (diagnostics.length > 3) {
      const remainder = document.createElement("li");
      remainder.textContent = `另有 ${diagnostics.length - 3} 条诊断信息`;
      warnings.appendChild(remainder);
    }
    card.appendChild(warnings);
  }
  if (batch.source_available === false) {
    const missing = document.createElement("p");
    missing.className = "batch-source-missing";
    missing.textContent = "收件箱中的原批次目录已不存在，无法继续或重试。";
    card.appendChild(missing);
  }

  const actions = document.createElement("div");
  actions.className = "batch-card-actions";
  if (Number(batch.capture_succeeded || 0) > 0) {
    const results = document.createElement("button");
    results.className = "button button-quiet compact";
    results.type = "button";
    results.textContent = "查看结果";
    results.addEventListener("click", () => showBatchResults(batch.batch_id));
    actions.appendChild(results);
  }
  if (batch.report_available) {
    const report = document.createElement("a");
    report.className = "button button-quiet compact";
    report.href = `/api/v1/offline-batches/${encodeURIComponent(batch.batch_id)}/report.docx`;
    report.download = "";
    report.textContent = "下载Word报告";
    actions.appendChild(report);
  }
  const acting = state.batchActions.has(batch.batch_id);
  if (batch.status === "discovered") {
    actions.appendChild(batchActionButton(batch, "import", "导入并预检", acting));
  } else if (batch.can_start_detection) {
    actions.appendChild(batchActionButton(
      batch, "confirm-detection", "确认并开始检测", acting,
    ));
  }
  if (batch.can_confirm_report) {
    actions.appendChild(batchActionButton(
      batch, "confirm-report", "确认结果并开放报告", acting,
    ));
  }
  if (
    ["completed", "completed_with_errors", "failed"].includes(batch.status)
    && (
      batch.status === "failed"
      || Number(batch.capture_failed || 0) > 0
      || batch.sensor_status === "failed"
    )
  ) {
    actions.appendChild(batchActionButton(batch, "retry", "重试失败项", acting));
  }
  if (actions.childElementCount) card.appendChild(actions);
  return card;
}

function batchMetric(labelText, valueText, noteText) {
  const item = document.createElement("div");
  item.className = "batch-metric";
  const label = document.createElement("span");
  label.textContent = labelText;
  const value = document.createElement("strong");
  value.textContent = valueText;
  const note = document.createElement("small");
  note.textContent = noteText;
  item.append(label, value, note);
  return item;
}

function batchProgressText(batch) {
  if (batch.status === "discovered") return "等待导入并执行预检";
  if (batch.status === "awaiting_detection_confirmation") return "预检完成，等待人工确认检测";
  if (batch.status === "queued") return "已进入后台队列";
  if (batch.status === "running") return "后台逐包识别中";
  if (batch.status === "completed") return "所有有效抓拍处理完成";
  if (batch.status === "completed_with_errors") return "有效数据已处理，存在警告或失败项";
  return "批次处理失败";
}

function batchActionButton(batch, action, text, acting) {
  const button = document.createElement("button");
  button.className = ["import", "confirm-detection", "confirm-report"].includes(action)
    ? "button button-primary compact"
    : "button button-warning compact";
  button.type = "button";
  button.textContent = acting ? "正在提交…" : text;
  button.disabled = acting || batch.source_available === false;
  button.addEventListener("click", () => performBatchAction(batch.batch_id, action));
  return button;
}

async function performBatchAction(batchId, action) {
  if (state.batchActions.has(batchId)) return;
  const confirmationMessages = {
    "confirm-detection": "确认开始检测这个批次吗？确认后后台才会执行视觉识别。",
    "confirm-report": "确认当前结果可以用于生成报告吗？后续修正或重跑会重新锁定报告。",
    retry: "确认重新预检失败项吗？重试后仍需再次确认才会开始检测。",
  };
  if (confirmationMessages[action] && !window.confirm(confirmationMessages[action])) return;
  state.batchActions.add(batchId);
  renderOfflineBatches();
  try {
    await apiFetch(
      `/api/v1/offline-batches/${encodeURIComponent(batchId)}/${action}`,
      {method: "POST"},
    );
    const successMessages = {
      import: "批次预检完成，请确认后开始检测",
      "confirm-detection": "已确认，批次进入检测队列",
      "confirm-report": "结果已确认，现在可以下载报告",
      retry: "失败项已准备完成，请再次确认后开始检测",
    };
    showToast(successMessages[action] || "批次状态已更新");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    state.batchActions.delete(batchId);
    await loadMonitoring();
  }
}

function showBatchResults(batchId) {
  byId("batch-filter").value = batchId;
  state.page = 0;
  updateExportLinks();
  loadRecords();
  byId("batch-filter").scrollIntoView({behavior: "smooth", block: "center"});
}

function randomCaptureId() {
  const timestamp = new Date().toISOString().replace(/\D/g, "").slice(0, 14);
  const suffix = globalThis.crypto && crypto.randomUUID
    ? crypto.randomUUID().replace(/-/g, "").slice(0, 8)
    : Math.random().toString(16).slice(2, 10);
  return `manual_${timestamp}_${suffix}`;
}

function isSupportedImage(file) {
  const extension = file.name.split(".").pop().toLowerCase();
  return state.limits.accepted_image_types.includes(file.type)
    || ["jpg", "jpeg", "png", "bmp"].includes(extension);
}

function addImages(fileList) {
  const additions = Array.from(fileList).filter((file) => {
    if (!isSupportedImage(file)) {
      showToast(`${file.name} 不是支持的图片格式`, true);
      return false;
    }
    if (file.size > state.limits.max_image_bytes) {
      showToast(`${file.name} 超过单张大小限制`, true);
      return false;
    }
    return true;
  });
  const available = state.limits.max_images - state.files.length;
  if (additions.length > available) {
    showToast(`一次最多上传 ${state.limits.max_images} 张图片`, true);
  }
  for (const file of additions.slice(0, available)) {
    state.files.push({file, url: URL.createObjectURL(file)});
  }
  state.retryJob = null;
  renderImages();
  renderMetadataPreview();
}

function renderImages() {
  const list = byId("image-list");
  list.replaceChildren();
  state.files.forEach((entry, index) => {
    const item = document.createElement("article");
    item.className = "image-item";

    const image = document.createElement("img");
    image.className = "image-thumb";
    image.src = entry.url;
    image.alt = `第 ${index + 1} 张：${entry.file.name}`;

    const info = document.createElement("div");
    info.className = "image-info";
    const name = document.createElement("strong");
    name.title = entry.file.name;
    name.textContent = entry.file.name;
    const size = document.createElement("span");
    size.textContent = `${index + 1} / ${state.files.length} · ${formatBytes(entry.file.size)}`;
    info.append(name, size);

    const buttons = document.createElement("div");
    buttons.className = "image-buttons";
    const previous = document.createElement("button");
    previous.type = "button";
    previous.textContent = "←";
    previous.title = "向前移动";
    previous.disabled = index === 0;
    previous.addEventListener("click", () => moveImage(index, -1));
    const next = document.createElement("button");
    next.type = "button";
    next.textContent = "→";
    next.title = "向后移动";
    next.disabled = index === state.files.length - 1;
    next.addEventListener("click", () => moveImage(index, 1));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "移除";
    remove.addEventListener("click", () => removeImage(index));
    buttons.append(previous, next, remove);
    item.append(image, info, buttons);
    list.appendChild(item);
  });
  byId("file-count").textContent = `${state.files.length} / ${state.limits.max_images}`;
  byId("start-upload").disabled = state.files.length === 0 || state.uploading;
}

function moveImage(index, delta) {
  const target = index + delta;
  if (target < 0 || target >= state.files.length) return;
  [state.files[index], state.files[target]] = [state.files[target], state.files[index]];
  state.retryJob = null;
  renderImages();
  renderMetadataPreview();
}

function removeImage(index) {
  URL.revokeObjectURL(state.files[index].url);
  state.files.splice(index, 1);
  state.retryJob = null;
  renderImages();
  renderMetadataPreview();
}

function clearUpload() {
  state.files.forEach((entry) => URL.revokeObjectURL(entry.url));
  state.files = [];
  state.metadata = null;
  state.metadataName = null;
  state.retryJob = null;
  byId("metadata-input").value = "";
  byId("image-input").value = "";
  byId("retry-upload").hidden = true;
  byId("upload-progress-wrap").hidden = true;
  renderImages();
  renderMetadataPreview();
}

async function loadMetadataFile(file) {
  try {
    const parsed = JSON.parse(await file.text());
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("metadata.json 顶层必须是对象");
    }
    state.metadata = parsed;
    state.metadataName = file.name;
    state.retryJob = null;
    renderMetadataPreview();
  } catch (error) {
    byId("metadata-input").value = "";
    showToast(error.message, true);
  }
}

function buildUniqueNames(files) {
  const used = new Set();
  return files.map((entry, index) => {
    const original = entry.file.name || `frame_${String(index + 1).padStart(2, "0")}.jpg`;
    let candidate = original;
    let counter = 2;
    while (used.has(candidate)) {
      candidate = `${String(index + 1).padStart(2, "0")}_${counter}_${original}`;
      counter += 1;
    }
    used.add(candidate);
    return candidate;
  });
}

function preparedJob() {
  if (state.files.length < 1 || state.files.length > state.limits.max_images) {
    throw new Error(`请选择 1–${state.limits.max_images} 张图片`);
  }
  let entries = [...state.files];
  let metadata;
  let uploadNames;
  if (state.metadata) {
    metadata = structuredClone(state.metadata);
    if (!Array.isArray(metadata.images) || metadata.images.length !== entries.length) {
      throw new Error("metadata.json 中的 images 数量必须与所选图片一致");
    }
    const remaining = [...entries];
    const ordered = [];
    for (const declared of metadata.images) {
      const baseName = String(declared).replaceAll("\\", "/").split("/").pop();
      const position = remaining.findIndex((entry) => entry.file.name === baseName);
      if (position < 0) {
        throw new Error(`未找到 metadata.json 声明的图片：${baseName}`);
      }
      ordered.push(remaining.splice(position, 1)[0]);
    }
    entries = ordered;
    uploadNames = metadata.images.map((name) => String(name).replaceAll("\\", "/").split("/").pop());
  } else {
    uploadNames = buildUniqueNames(entries);
    metadata = {
      capture_id: randomCaptureId(),
      capture_time: new Date().toISOString(),
      station_id: "manual",
      robot_pose: {frame: "map", x_m: null, y_m: null, yaw_deg: null},
      camera_id: "browser_upload",
      images: uploadNames,
      batch_id: null,
    };
  }
  return {entries, metadata, uploadNames};
}

function renderMetadataPreview() {
  const status = byId("metadata-status");
  const preview = byId("metadata-preview");
  if (state.metadata) {
    status.textContent = `${state.metadataName} · 只读`;
    preview.textContent = JSON.stringify(state.metadata, null, 2);
    return;
  }
  status.textContent = "未选择，将自动生成基础记录";
  const names = buildUniqueNames(state.files);
  preview.textContent = JSON.stringify({
    capture_id: "上传时自动生成",
    capture_time: "上传时自动生成",
    station_id: "manual",
    robot_pose: {frame: "map", x_m: null, y_m: null, yaw_deg: null},
    camera_id: "browser_upload",
    images: names,
    batch_id: null,
  }, null, 2);
}

function setUploadState(stage, percent) {
  byId("upload-progress-wrap").hidden = false;
  byId("upload-stage").textContent = stage;
  byId("upload-percent").textContent = `${Math.round(percent)}%`;
  byId("upload-progress").style.width = `${Math.max(0, Math.min(100, percent))}%`;
}

function uploadJob(job, isRetry = false) {
  state.uploading = true;
  state.retryJob = null;
  byId("retry-upload").hidden = true;
  renderImages();
  setUploadState(isRetry ? "正在重试上传" : "正在传输图片", 0);

  const formData = new FormData();
  formData.append("metadata", JSON.stringify(job.metadata));
  job.entries.forEach((entry, index) => {
    formData.append("images", entry.file, job.uploadNames[index]);
  });

  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/v1/captures");
  xhr.responseType = "json";
  xhr.upload.addEventListener("progress", (event) => {
    if (event.lengthComputable) {
      setUploadState("正在传输图片", event.loaded / event.total * 90);
    }
  });
  xhr.upload.addEventListener("load", () => {
    setUploadState("图片已接收，正在识别", 92);
  });
  xhr.addEventListener("load", async () => {
    state.uploading = false;
    if (xhr.status >= 200 && xhr.status < 300) {
      setUploadState("识别完成", 100);
      showToast(`任务 ${job.metadata.capture_id} 已完成`);
      clearUpload();
      await loadMonitoring();
      await openDetail(job.metadata.capture_id);
      return;
    }
    setUploadState("上传或识别失败", 100);
    const detail = xhr.response && xhr.response.detail
      ? xhr.response.detail
      : `请求失败（HTTP ${xhr.status}）`;
    showToast(detail, true);
    renderImages();
    await loadMonitoring();
  });
  xhr.addEventListener("error", () => {
    state.uploading = false;
    state.retryJob = job;
    byId("retry-upload").hidden = false;
    setUploadState("网络中断，文件仍保留在当前页面", 0);
    renderImages();
    showToast("未连接到上位机，可以重试上传", true);
  });
  xhr.addEventListener("abort", () => {
    state.uploading = false;
    state.retryJob = job;
    byId("retry-upload").hidden = false;
    renderImages();
  });
  xhr.send(formData);
}

function startUpload() {
  try {
    uploadJob(preparedJob());
  } catch (error) {
    showToast(error.message, true);
  }
}

function renderRuntimeSettings(payload) {
  state.runtime = payload;
  state.limits = payload.limits;
  byId("max-file-size").textContent = formatBytes(payload.limits.max_image_bytes);
  const current = payload.current;
  renderInferenceMode(payload);
  setRangeValue("detector-confidence", current.detector.confidence);
  setRangeValue("fusion-iou", current.pipeline.fusion_iou);
  setRangeValue("meter-confidence", current.digital_meter.minimum_frame_confidence);
  renderModules(current.modules, payload.module_status);
  markSettingsSaved();
  renderImages();
}

function renderInferenceMode(payload) {
  const inference = payload.inference || {};
  const activeMode = inference.active_mode || payload.current.detector.mode || "noop";
  const gpu = inference.gpu || {available: false, reason: "GPU 状态未知"};
  const noopButton = byId("mode-noop");
  const gpuButton = byId("mode-gpu");
  const status = byId("inference-mode-status");

  noopButton.classList.toggle("active", activeMode === "noop");
  gpuButton.classList.toggle("active", activeMode === "gpu");
  noopButton.setAttribute("aria-pressed", String(activeMode === "noop"));
  gpuButton.setAttribute("aria-pressed", String(activeMode === "gpu"));
  noopButton.disabled = state.switchingMode;
  gpuButton.disabled = state.switchingMode || (!gpu.available && activeMode !== "gpu");
  gpuButton.title = gpu.available ? "切换到 NVIDIA GPU 模型识别" : (gpu.reason || "GPU 不可用");

  status.className = "";
  if (state.switchingMode) {
    status.textContent = "正在切换推理模式，请稍候…";
  } else if (activeMode === "gpu") {
    status.textContent = `GPU 模型识别已启用 · ${gpu.device_name || "CUDA 设备 0"}`;
  } else if (activeMode === "json_replay") {
    status.textContent = "当前为 JSON 回放模式，可切换到 NOOP 或 GPU";
  } else if (gpu.available) {
    status.textContent = `当前仅记录 · GPU 已就绪：${gpu.device_name || "CUDA 设备 0"}`;
  } else {
    status.textContent = `当前仅记录 · GPU 不可用：${gpu.reason || "环境未就绪"}`;
    status.className = "error";
  }
}

async function switchInferenceMode(mode) {
  if (!state.runtime || state.switchingMode) return;
  const activeMode = state.runtime.inference?.active_mode
    || state.runtime.current.detector.mode;
  if (activeMode === mode) return;

  state.switchingMode = true;
  renderInferenceMode(state.runtime);
  try {
    const payload = await apiFetch("/api/v1/runtime-settings", {
      method: "PATCH",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({detector: {mode}}),
    });
    state.switchingMode = false;
    renderRuntimeSettings(payload);
    showToast(mode === "gpu" ? "GPU 模型识别已启用" : "已切换到 NOOP 仅记录模式");
    await loadHealth();
  } catch (error) {
    state.switchingMode = false;
    showToast(error.message, true);
    await loadRuntimeSettings();
  }
}

function setRangeValue(id, value) {
  byId(id).value = String(value);
  byId(`${id}-output`).textContent = Number(value).toFixed(2);
}

function renderModules(values, statuses) {
  const list = byId("module-list");
  list.replaceChildren();
  Object.entries(MODULE_LABELS).forEach(([moduleId, label]) => {
    const status = statuses[moduleId] || {status: "unknown"};
    const row = document.createElement("div");
    row.className = "module-row";

    const name = document.createElement("div");
    name.className = "module-name";
    const strong = document.createElement("strong");
    strong.textContent = label;
    const reason = document.createElement("small");
    reason.textContent = reasonText(status.reason) || statusText(status.status);
    reason.title = reason.textContent;
    name.append(strong, reason);

    const stateLabel = document.createElement("span");
    stateLabel.className = `module-state ${status.status}`;
    stateLabel.textContent = statusText(status.status);

    const toggle = document.createElement("label");
    toggle.className = "switch";
    toggle.title = `${label}开关`;
    const input = document.createElement("input");
    input.type = "checkbox";
    input.dataset.moduleId = moduleId;
    input.checked = Boolean(values[moduleId]);
    input.setAttribute("aria-label", `${label}开关`);
    input.addEventListener("change", markSettingsDirty);
    const track = document.createElement("span");
    toggle.append(input, track);
    row.append(name, stateLabel, toggle);
    list.appendChild(row);
  });
}

function statusText(status) {
  return {
    ready: "就绪",
    disabled: "已停用",
    unavailable: "不可用",
    unreadable: "未识别成功",
    confirmed: "识别成功",
    detected: "检测成功",
    metadata_only: "仅元数据",
    unknown: "未知",
  }[status] || status;
}

function reasonText(reason) {
  if (!reason) return "";
  if (REASON_LABELS[reason]) return REASON_LABELS[reason];
  if (String(reason).startsWith("expected_4_digits_but_found_")) {
    return "检测到的数字位数与预期 4 位不一致";
  }
  return String(reason).replaceAll("_", " ");
}

function markSettingsDirty() {
  const label = byId("settings-state");
  label.textContent = "未保存";
  label.className = "save-state dirty";
}

function markSettingsSaved() {
  const label = byId("settings-state");
  label.textContent = "已同步";
  label.className = "save-state saved";
}

function currentSettingsPatch() {
  const modules = {};
  document.querySelectorAll("[data-module-id]").forEach((input) => {
    modules[input.dataset.moduleId] = input.checked;
  });
  return {
    detector: {confidence: Number(byId("detector-confidence").value)},
    pipeline: {fusion_iou: Number(byId("fusion-iou").value)},
    digital_meter: {
      minimum_frame_confidence: Number(byId("meter-confidence").value),
    },
    modules,
  };
}

async function loadRuntimeSettings() {
  try {
    renderRuntimeSettings(await apiFetch("/api/v1/runtime-settings"));
  } catch (error) {
    byId("settings-state").textContent = "读取失败";
    showToast(error.message, true);
  }
}

async function saveRuntimeSettings() {
  const button = byId("save-settings");
  button.disabled = true;
  try {
    const payload = await apiFetch("/api/v1/runtime-settings", {
      method: "PATCH",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(currentSettingsPatch()),
    });
    renderRuntimeSettings(payload);
    showToast("参数已保存，新任务将使用当前配置");
    await loadHealth();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function resetRuntimeSettings() {
  if (!window.confirm("恢复项目默认参数？已处理的历史结果不会自动重跑。")) return;
  const button = byId("reset-settings");
  button.disabled = true;
  try {
    renderRuntimeSettings(await apiFetch("/api/v1/runtime-settings/reset", {method: "POST"}));
    showToast("已恢复默认参数");
    await loadHealth();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function loadHealth() {
  const badge = byId("health-badge");
  try {
    const health = await apiFetch("/health");
    badge.className = "health-badge online";
    const modeLabel = health.inference_mode === "gpu"
      ? "GPU"
      : String(health.inference_mode || health.detector).toUpperCase();
    badge.lastElementChild.textContent = `${modeLabel} · 上位机在线`;
    byId("stat-total").textContent = health.captures_total;
    byId("stat-processed").textContent = health.captures_processed;
    byId("stat-failed").textContent = health.captures_failed;
    byId("stat-received").textContent = Math.max(
      0,
      health.captures_total - health.captures_processed - health.captures_failed,
    );
  } catch (_error) {
    badge.className = "health-badge offline";
    badge.lastElementChild.textContent = "上位机连接失败";
  }
}

function recordQuery() {
  const params = new URLSearchParams({
    limit: String(state.pageSize),
    offset: String(state.page * state.pageSize),
  });
  const status = byId("status-filter").value;
  const station = byId("station-filter").value.trim();
  const captureId = byId("capture-search").value.trim();
  const batchId = byId("batch-filter").value;
  if (status) params.set("status", status);
  if (station) params.set("station_id", station);
  if (captureId) params.set("capture_id", captureId);
  if (batchId) params.set("batch_id", batchId);
  return params;
}

async function loadRecords() {
  try {
    const data = await apiFetch(`/api/v1/captures?${recordQuery()}`);
    state.records = data.items;
    state.total = data.total;
    if (state.page > 0 && state.records.length === 0) {
      state.page -= 1;
      return loadRecords();
    }
    renderRecords();
  } catch (error) {
    const body = byId("record-rows");
    body.replaceChildren();
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.className = "empty-cell";
    cell.colSpan = 9;
    cell.textContent = error.message;
    row.appendChild(cell);
    body.appendChild(row);
  }
}

function renderRecords() {
  const shown = state.records;
  const body = byId("record-rows");
  body.replaceChildren();
  if (shown.length === 0) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.className = "empty-cell";
    cell.colSpan = 9;
    cell.textContent = "暂无匹配的任务记录";
    row.appendChild(cell);
    body.appendChild(row);
  }
  shown.forEach((item) => body.appendChild(recordRow(item)));
  const pageCount = Math.max(1, Math.ceil(state.total / state.pageSize));
  byId("record-summary").textContent = `共 ${state.total} 条，本页显示 ${shown.length} 条`;
  byId("page-label").textContent = `第 ${state.page + 1} / ${pageCount} 页`;
  byId("previous-page").disabled = state.page === 0;
  byId("next-page").disabled = state.page + 1 >= pageCount;
  updateRecordSelection();
}

function recordRow(item) {
  const row = document.createElement("tr");
  row.tabIndex = 0;
  row.addEventListener("click", () => openDetail(item.capture_id));
  row.addEventListener("keydown", (event) => {
    if (event.target === row && (event.key === "Enter" || event.key === " ")) {
      openDetail(item.capture_id);
    }
  });

  const selectionCell = document.createElement("td");
  selectionCell.className = "selection-column";
  const selectionLabel = document.createElement("label");
  selectionLabel.className = "record-select";
  const selection = document.createElement("input");
  selection.type = "checkbox";
  selection.checked = state.selectedRecordIds.has(item.capture_id);
  selection.setAttribute("aria-label", `选择任务 ${item.capture_id}`);
  selection.addEventListener("click", (event) => event.stopPropagation());
  selection.addEventListener("change", () => {
    if (selection.checked) {
      state.selectedRecordIds.add(item.capture_id);
    } else {
      state.selectedRecordIds.delete(item.capture_id);
    }
    updateRecordSelection();
  });
  selectionLabel.addEventListener("click", (event) => event.stopPropagation());
  selectionLabel.appendChild(selection);
  selectionCell.appendChild(selectionLabel);
  row.appendChild(selectionCell);

  appendTextCell(row, formatDate(item.capture_time));

  const idCell = document.createElement("td");
  idCell.className = "capture-cell";
  const id = document.createElement("strong");
  id.textContent = item.capture_id;
  const received = document.createElement("small");
  received.textContent = `接收 ${formatDate(item.received_at)}`;
  idCell.append(id, received);
  row.appendChild(idCell);

  const source = document.createElement("td");
  source.className = "source-cell";
  const station = document.createElement("strong");
  station.textContent = item.station_id || "—";
  const camera = document.createElement("small");
  camera.textContent = item.source_batch_id
    ? `${item.camera_id} · ${item.source_batch_id}`
    : item.camera_id;
  camera.title = item.source_batch_id || "";
  source.append(station, camera);
  row.appendChild(source);
  appendTextCell(row, String(item.image_count));
  appendTextCell(row, String(item.object_count));
  row.appendChild(buildRecognitionTableCell(item.recognition_summary));

  const statusCell = document.createElement("td");
  const status = document.createElement("span");
  status.className = `status-chip ${item.status}`;
  status.textContent = STATUS_LABELS[item.status] || item.status;
  statusCell.appendChild(status);
  if (item.manually_corrected) {
    const corrected = document.createElement("span");
    corrected.className = "status-chip corrected";
    corrected.textContent = "已修正";
    corrected.style.marginLeft = "5px";
    statusCell.appendChild(corrected);
  }
  row.appendChild(statusCell);

  const actionCell = document.createElement("td");
  const actions = document.createElement("div");
  actions.className = "row-actions";
  const viewAction = document.createElement("button");
  viewAction.className = "row-action";
  viewAction.type = "button";
  viewAction.textContent = "查看";
  viewAction.addEventListener("click", (event) => {
    event.stopPropagation();
    openDetail(item.capture_id);
  });
  const deleteAction = document.createElement("button");
  deleteAction.className = "row-action danger";
  deleteAction.type = "button";
  deleteAction.textContent = "删除";
  deleteAction.disabled = state.deletingRecords;
  deleteAction.addEventListener("click", (event) => {
    event.stopPropagation();
    deleteRecords([item.capture_id]);
  });
  actions.append(viewAction, deleteAction);
  actionCell.appendChild(actions);
  row.appendChild(actionCell);
  return row;
}

function buildRecognitionTableCell(summary) {
  const cell = document.createElement("td");
  cell.className = "recognition-cell";
  const headline = document.createElement("strong");
  headline.textContent = recognitionHeadline(summary);
  const status = document.createElement("span");
  const summaryStatus = summary?.status || "unrecognized";
  status.className = `recognition-inline-status ${summaryStatus}`;
  status.textContent = RECOGNITION_STATUS_LABELS[summaryStatus] || summaryStatus;
  const note = document.createElement("small");
  note.textContent = recognitionNote(summary);
  cell.append(headline, status, note);
  return cell;
}

function recognitionHeadline(summary) {
  if (!summary || summary.status === "unrecognized") return "未识别成功";
  if (summary.status === "processing") return "正在识别";
  if (summary.status === "failed") return "识别任务失败";
  const primary = summary.primary;
  if (!primary) return "未识别成功";
  if (primary.source_kind === "module" || primary.source_kind === "manual") {
    return `${primary.source_name}：${primary.display_value || primary.label}`;
  }
  return `${primary.source_name}：检测到${primary.label}`;
}

function recognitionNote(summary) {
  if (!summary) return "没有最终识别摘要";
  if (summary.status === "processing") return "等待模型返回结果";
  if (summary.status === "failed") return summary.error || "处理过程发生错误";
  const notes = [];
  if (summary.primary?.confidence > 0) {
    notes.push(`置信度 ${formatConfidence(summary.primary.confidence)}`);
  }
  const failures = (summary.items || [])
    .filter((item) => item.status === "unrecognized")
    .map((item) => item.source_name);
  if (failures.length) notes.push(`${failures.join("、")}未识别成功`);
  if (!notes.length && !summary.primary) notes.push("没有检测到可确认的目标或读数");
  return notes.join(" · ") || "已获得可信结果";
}

function formatConfidence(value) {
  const confidence = Number(value);
  return Number.isFinite(confidence) ? `${(confidence * 100).toFixed(1)}%` : "—";
}

function updateRecordSelection() {
  const currentIds = state.records.map((item) => item.capture_id);
  const selectedOnPage = currentIds.filter((captureId) =>
    state.selectedRecordIds.has(captureId));
  const selectAll = byId("select-all-records");
  selectAll.disabled = currentIds.length === 0 || state.deletingRecords;
  selectAll.checked = currentIds.length > 0 && selectedOnPage.length === currentIds.length;
  selectAll.indeterminate = selectedOnPage.length > 0
    && selectedOnPage.length < currentIds.length;

  const deleteButton = byId("delete-selected-records");
  const selectedCount = state.selectedRecordIds.size;
  deleteButton.disabled = selectedCount === 0 || state.deletingRecords;
  deleteButton.textContent = selectedCount > 0
    ? `删除所选（${selectedCount}）`
    : "删除所选";
}

async function deleteRecords(captureIds) {
  const uniqueIds = [...new Set(captureIds)];
  if (uniqueIds.length === 0 || state.deletingRecords) return;
  const prompt = uniqueIds.length === 1
    ? `确定删除任务 ${uniqueIds[0]}？其原图、处理图和修正记录也会被永久删除。`
    : `确定删除所选的 ${uniqueIds.length} 条任务？相关图片和修正记录也会被永久删除。`;
  if (!window.confirm(prompt)) return;

  state.deletingRecords = true;
  renderRecords();
  const results = await Promise.allSettled(uniqueIds.map((captureId) =>
    apiFetch(`/api/v1/captures/${encodeURIComponent(captureId)}`, {
      method: "DELETE",
    }).then(() => captureId)));
  const deletedIds = results
    .filter((result) => result.status === "fulfilled")
    .map((result) => result.value);
  const failures = results.filter((result) => result.status === "rejected");
  deletedIds.forEach((captureId) => state.selectedRecordIds.delete(captureId));
  if (state.selectedCaptureId && deletedIds.includes(state.selectedCaptureId)) {
    closeDrawer();
  }
  state.deletingRecords = false;
  await loadMonitoring();
  if (deletedIds.length > 0) {
    showToast(`已删除 ${deletedIds.length} 条巡检任务`);
  }
  if (failures.length > 0) {
    const message = failures[0].reason instanceof Error
      ? failures[0].reason.message
      : "部分任务删除失败";
    showToast(`${failures.length} 条任务删除失败：${message}`, true);
  }
}

function appendTextCell(row, text) {
  const cell = document.createElement("td");
  cell.textContent = text;
  row.appendChild(cell);
}

async function loadMonitoring() {
  await Promise.all([loadHealth(), loadRecords(), loadOfflineBatches()]);
}

function openDrawer() {
  byId("drawer-overlay").hidden = false;
  byId("result-drawer").classList.add("open");
  byId("result-drawer").setAttribute("aria-hidden", "false");
  document.body.style.overflow = "hidden";
}

function closeDrawer() {
  byId("result-drawer").classList.remove("open");
  byId("result-drawer").setAttribute("aria-hidden", "true");
  byId("drawer-overlay").hidden = true;
  document.body.style.overflow = "";
}

async function openDetail(captureId) {
  state.selectedCaptureId = captureId;
  byId("detail-title").textContent = captureId;
  byId("detail-content").innerHTML = '<div class="drawer-empty">正在读取任务结果…</div>';
  openDrawer();
  try {
    const capture = await apiFetch(`/api/v1/results/${encodeURIComponent(captureId)}`);
    if (state.selectedCaptureId !== captureId) return;
    state.selectedCapture = capture;
    state.correctedObjects = structuredClone((capture.result && capture.result.objects) || []);
    renderDetail();
  } catch (error) {
    byId("detail-content").replaceChildren();
    const empty = document.createElement("div");
    empty.className = "drawer-empty";
    empty.textContent = error.message;
    byId("detail-content").appendChild(empty);
  }
}

function renderDetail() {
  const capture = state.selectedCapture;
  byId("detail-title").textContent = capture.capture_id;
  const content = byId("detail-content");
  content.replaceChildren();

  const statusLine = document.createElement("div");
  statusLine.className = "detail-status-line";
  statusLine.appendChild(makeStatusChip(capture.status));
  if (capture.manually_corrected) {
    const corrected = document.createElement("span");
    corrected.className = "status-chip corrected";
    corrected.textContent = "已人工修正";
    statusLine.appendChild(corrected);
  }
  content.appendChild(statusLine);

  const sources = [];
  if (capture.result && capture.result.annotated_image) {
    sources.push({
      label: "标注结果",
      url: `/api/v1/captures/${encodeURIComponent(capture.capture_id)}/annotated?t=${Date.now()}`,
    });
  }
  capture.images.forEach((image, index) => {
    sources.push({
      label: image.original_name || `原图 ${index + 1}`,
      url: `/api/v1/captures/${encodeURIComponent(capture.capture_id)}/images/${image.position}`,
    });
  });
  if (sources.length) {
    const preview = document.createElement("img");
    preview.className = "detail-preview";
    preview.id = "detail-preview";
    preview.src = sources[0].url;
    preview.alt = sources[0].label;
    content.appendChild(preview);

    const thumbnails = document.createElement("div");
    thumbnails.className = "detail-thumbnails";
    sources.forEach((source, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `detail-thumbnail${index === 0 ? " active" : ""}`;
      const image = document.createElement("img");
      image.src = source.url;
      image.alt = "";
      const label = document.createElement("span");
      label.textContent = source.label;
      button.append(image, label);
      button.addEventListener("click", () => {
        preview.src = source.url;
        preview.alt = source.label;
        thumbnails.querySelectorAll(".detail-thumbnail").forEach((item) =>
          item.classList.toggle("active", item === button)
        );
      });
      thumbnails.appendChild(button);
    });
    content.appendChild(thumbnails);
  }

  content.appendChild(buildRecognitionSummarySection(capture));
  content.appendChild(buildMetadataSection(capture));
  if (capture.error) {
    const error = document.createElement("div");
    error.className = "error-box";
    error.textContent = capture.error;
    content.appendChild(error);
  }
  content.appendChild(buildModuleSection(capture));
  content.appendChild(buildCorrectionSection(capture));
  content.appendChild(buildAuditSection(capture));
}

function makeStatusChip(statusValue) {
  const status = document.createElement("span");
  status.className = `status-chip ${statusValue}`;
  status.textContent = STATUS_LABELS[statusValue] || statusValue;
  return status;
}

function buildRecognitionSummarySection(capture) {
  const section = detailSection("最终识别结果");
  const summary = capture.recognition_summary || {
    status: capture.status === "failed" ? "failed" : "unrecognized",
    primary: null,
    items: [],
    error: capture.error,
  };
  const hero = document.createElement("div");
  hero.className = `recognition-summary ${summary.status}`;
  const status = document.createElement("span");
  status.className = `recognition-summary-status ${summary.status}`;
  status.textContent = RECOGNITION_STATUS_LABELS[summary.status] || summary.status;
  const headline = document.createElement("strong");
  headline.textContent = recognitionHeadline(summary);
  const description = document.createElement("p");
  description.textContent = {
    recognized: "本次任务已获得可确认的最终结果。",
    partial: "已检测到目标，但部分启用模块没有获得可信结果。",
    unrecognized: "本次任务没有获得可确认的目标或读数。",
    processing: "模型正在处理本次任务。",
    failed: summary.error || "本次任务处理失败。",
  }[summary.status] || recognitionNote(summary);
  hero.append(status, headline, description);
  section.appendChild(hero);

  const items = summary.items || [];
  if (!items.length) return section;
  const list = document.createElement("div");
  list.className = "recognition-result-list";
  items.forEach((item) => {
    const card = document.createElement("article");
    card.className = `recognition-result-item ${item.status}`;
    const header = document.createElement("div");
    const source = document.createElement("strong");
    source.textContent = item.source_name;
    const itemStatus = document.createElement("span");
    itemStatus.textContent = RECOGNITION_STATUS_LABELS[item.status]
      || statusText(item.raw_status);
    header.append(source, itemStatus);
    const value = document.createElement("p");
    if (item.status === "unrecognized") {
      value.textContent = "未识别成功";
    } else if (item.source_kind === "detector") {
      value.textContent = `检测到：${item.label}`;
    } else {
      value.textContent = `最终结果：${item.display_value || item.label}`;
    }
    const metadata = document.createElement("small");
    const details = [];
    if (item.source_kind === "detector" && item.value) {
      details.push(`类别 ${item.value}`);
    }
    if (item.confidence > 0) details.push(`置信度 ${formatConfidence(item.confidence)}`);
    if (item.reason) details.push(reasonText(item.reason));
    metadata.textContent = details.join(" · ") || "—";
    card.append(header, value, metadata);
    list.appendChild(card);
  });
  section.appendChild(list);
  return section;
}

function buildMetadataSection(capture) {
  const section = detailSection("任务与只读元数据");
  const grid = document.createElement("div");
  grid.className = "meta-grid";
  [
    ["抓拍时间", formatDate(capture.capture_time)],
    ["接收时间", formatDate(capture.received_at)],
    ["离线批次", capture.source_batch_id || "手工上传 / 在线任务"],
    ["工位", capture.station_id || "—"],
    ["相机", capture.camera_id],
    ["坐标系", capture.robot_pose.frame || "—"],
    ["位姿 X / Y / Yaw", poseText(capture.robot_pose)],
  ].forEach(([label, value]) => grid.appendChild(metaItem(label, value)));
  section.appendChild(grid);

  const parameters = document.createElement("details");
  parameters.className = "metadata-details";
  const summary = document.createElement("summary");
  summary.textContent = "查看本次处理参数快照";
  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(
    (capture.result && capture.result.processing_parameters) || {},
    null,
    2,
  );
  parameters.append(summary, pre);
  section.appendChild(parameters);
  return section;
}

function poseText(pose) {
  const value = (item) => item === null || item === undefined ? "—" : item;
  return `${value(pose.x_m)} / ${value(pose.y_m)} / ${value(pose.yaw_deg)}`;
}

function metaItem(label, value) {
  const item = document.createElement("div");
  item.className = "meta-item";
  const title = document.createElement("span");
  title.textContent = label;
  const content = document.createElement("strong");
  content.textContent = String(value ?? "—");
  item.append(title, content);
  return item;
}

function buildModuleSection(capture) {
  const section = detailSection("模块识别结果");
  const list = document.createElement("div");
  list.className = "module-result-list";
  const modules = (capture.result && capture.result.modules) || {};
  if (!Object.keys(modules).length) {
    const empty = document.createElement("p");
    empty.className = "drawer-empty";
    empty.textContent = "没有模块结果";
    section.appendChild(empty);
    return section;
  }
  Object.entries(modules).forEach(([moduleId, result]) => {
    const card = document.createElement("article");
    card.className = "module-result";
    const title = document.createElement("strong");
    title.textContent = MODULE_LABELS[moduleId] || moduleId;
    const status = document.createElement("span");
    status.textContent = statusText(result.status || "unknown");
    const summary = document.createElement("p");
    summary.textContent = moduleResultText(moduleId, result);
    card.append(title, status, summary);
    list.appendChild(card);
  });
  section.appendChild(list);
  return section;
}

function moduleResultText(moduleId, result) {
  if (result.status === "disabled" || result.enabled === false) {
    return `未启用${result.reason ? `：${reasonText(result.reason)}` : ""}`;
  }
  if (["unreadable", "unavailable"].includes(result.status)) {
    return `未识别成功${result.reason ? `：${reasonText(result.reason)}` : ""}`;
  }
  if (moduleId === "station_number" && result.number !== null && result.number !== undefined) {
    return `最终结果：${result.number} 号`;
  }
  if (moduleId === "digital_meter") {
    const value = result.raw_text ?? result.value;
    return value === null || value === undefined ? "未识别成功" : `最终结果：${value}`;
  }
  if (moduleId === "coal_presence" && typeof result.present === "boolean") {
    return result.present ? "检测到煤堆" : "未检测到煤堆";
  }
  const objects = result.objects ?? result.meters;
  if (Array.isArray(objects)) return `识别目标：${objects.length} 个`;
  const preferred = result.raw_text ?? result.number ?? result.value ?? result.present;
  return preferred === null || preferred === undefined ? "识别完成" : `最终结果：${preferred}`;
}

function buildCorrectionSection(capture) {
  const section = detailSection("人工复核");
  const heading = section.querySelector(".detail-section-heading");
  const add = document.createElement("button");
  add.className = "button button-quiet compact";
  add.type = "button";
  add.textContent = "新增对象";
  add.disabled = !capture.result;
  add.addEventListener("click", () => {
    state.correctedObjects.push({
      type: "unknown",
      class: "unknown",
      class_cn: "",
      confidence: null,
      bbox_xyxy: null,
    });
    renderObjectEditors();
  });
  heading.appendChild(add);

  const list = document.createElement("div");
  list.id = "object-editor-list";
  list.className = "object-list";
  section.appendChild(list);

  const advanced = document.createElement("details");
  advanced.className = "advanced-json";
  const summary = document.createElement("summary");
  summary.textContent = "高级 JSON 编辑";
  const textarea = document.createElement("textarea");
  textarea.id = "objects-json";
  textarea.spellcheck = false;
  const apply = document.createElement("button");
  apply.className = "button button-quiet compact";
  apply.type = "button";
  apply.textContent = "应用 JSON";
  apply.addEventListener("click", applyObjectsJson);
  advanced.append(summary, textarea, apply);
  section.appendChild(advanced);

  const form = document.createElement("div");
  form.className = "form-grid";
  form.append(
    formField("操作人", "correction-operator", "text", true),
    formField("修正原因", "correction-reason", "text", false),
  );
  section.appendChild(form);

  const actions = document.createElement("div");
  actions.className = "detail-actions";
  const reprocess = document.createElement("button");
  reprocess.className = "button button-warning";
  reprocess.type = "button";
  reprocess.textContent = "按当前参数重新处理";
  reprocess.addEventListener("click", reprocessSelected);
  const save = document.createElement("button");
  save.className = "button button-primary";
  save.type = "button";
  save.textContent = "保存人工修正";
  save.disabled = !capture.result;
  save.addEventListener("click", saveCorrection);
  actions.append(reprocess, save);
  section.appendChild(actions);

  window.setTimeout(renderObjectEditors, 0);
  return section;
}

function formField(labelText, id, type, required) {
  const label = document.createElement("label");
  const span = document.createElement("span");
  span.textContent = `${labelText}${required ? " *" : ""}`;
  const input = document.createElement("input");
  input.id = id;
  input.type = type;
  input.required = required;
  label.append(span, input);
  return label;
}

function renderObjectEditors() {
  const list = byId("object-editor-list");
  if (!list) return;
  list.replaceChildren();
  if (!state.correctedObjects.length) {
    const empty = document.createElement("div");
    empty.className = "drawer-empty";
    empty.textContent = "当前结果没有识别对象，可点击“新增对象”人工补充。";
    list.appendChild(empty);
  }
  state.correctedObjects.forEach((object, index) => {
    const editor = document.createElement("article");
    editor.className = "object-editor";
    const header = document.createElement("div");
    header.className = "object-editor-header";
    const title = document.createElement("strong");
    title.textContent = `对象 ${index + 1}`;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "删除对象";
    remove.addEventListener("click", () => {
      state.correctedObjects.splice(index, 1);
      renderObjectEditors();
    });
    header.append(title, remove);

    const fields = document.createElement("div");
    fields.className = "object-fields";
    fields.append(
      objectField(index, "类型", "type", object.type ?? ""),
      objectField(index, "类别 ID", "class", object.class ?? ""),
      objectField(index, "中文名称", "class_cn", object.class_cn ?? ""),
      objectField(index, "置信度", "confidence", object.confidence ?? "", "number"),
      bboxField(index, object.bbox_xyxy),
    );
    editor.append(header, fields);
    list.appendChild(editor);
  });
  syncObjectsJson();
}

function objectField(index, labelText, key, value, type = "text") {
  const label = document.createElement("label");
  const span = document.createElement("span");
  span.textContent = labelText;
  const input = document.createElement("input");
  input.type = type;
  input.value = value;
  if (type === "number") {
    input.min = "0";
    input.max = "1";
    input.step = "0.01";
  }
  input.addEventListener("input", () => {
    const next = type === "number"
      ? (input.value === "" ? null : Number(input.value))
      : input.value;
    state.correctedObjects[index][key] = next;
    syncObjectsJson();
  });
  label.append(span, input);
  return label;
}

function bboxField(index, bbox) {
  const label = document.createElement("label");
  label.className = "bbox-field";
  const span = document.createElement("span");
  span.textContent = "边框 xyxy（留空表示无框）";
  const inputs = document.createElement("div");
  inputs.className = "bbox-inputs";
  const values = Array.isArray(bbox) ? bbox : ["", "", "", ""];
  ["x1", "y1", "x2", "y2"].forEach((name, position) => {
    const input = document.createElement("input");
    input.type = "number";
    input.step = "0.1";
    input.placeholder = name;
    input.value = values[position] ?? "";
    input.addEventListener("input", () => {
      const all = Array.from(inputs.querySelectorAll("input")).map((item) =>
        item.value === "" ? null : Number(item.value)
      );
      state.correctedObjects[index].bbox_xyxy = all.every((item) => item === null)
        ? null
        : all;
      syncObjectsJson();
    });
    inputs.appendChild(input);
  });
  label.append(span, inputs);
  return label;
}

function syncObjectsJson() {
  const textarea = byId("objects-json");
  if (textarea) textarea.value = JSON.stringify(state.correctedObjects, null, 2);
}

function applyObjectsJson() {
  const previous = state.correctedObjects;
  try {
    const parsed = JSON.parse(byId("objects-json").value);
    if (!Array.isArray(parsed)) throw new Error("对象 JSON 必须是数组");
    state.correctedObjects = parsed;
    validateCorrectedObjects();
    renderObjectEditors();
    showToast("已应用 JSON 内容");
  } catch (error) {
    state.correctedObjects = previous;
    showToast(`JSON 格式错误：${error.message}`, true);
  }
}

async function saveCorrection() {
  const operator = byId("correction-operator").value.trim();
  if (!operator) {
    showToast("请填写操作人", true);
    byId("correction-operator").focus();
    return;
  }
  try {
    validateCorrectedObjects();
  } catch (error) {
    showToast(error.message, true);
    return;
  }
  try {
    await apiFetch(
      `/api/v1/results/${encodeURIComponent(state.selectedCaptureId)}/correction`,
      {
        method: "PATCH",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          objects: state.correctedObjects,
          operator,
          reason: byId("correction-reason").value.trim() || null,
        }),
      },
    );
    showToast("人工修正已保存，原模型结果仍保留");
    await Promise.all([loadRecords(), openDetail(state.selectedCaptureId)]);
  } catch (error) {
    showToast(error.message, true);
  }
}

function validateCorrectedObjects() {
  state.correctedObjects.forEach((object, index) => {
    if (!object || typeof object !== "object" || Array.isArray(object)) {
      throw new Error(`对象 ${index + 1} 必须是有效对象`);
    }
    if (!String(object.type || "").trim() || !String(object.class || "").trim()) {
      throw new Error(`对象 ${index + 1} 必须填写类型和类别 ID`);
    }
    if (object.confidence !== null && object.confidence !== undefined) {
      const confidence = Number(object.confidence);
      if (!Number.isFinite(confidence) || confidence < 0 || confidence > 1) {
        throw new Error(`对象 ${index + 1} 的置信度必须在 0–1 之间`);
      }
    }
    if (object.bbox_xyxy !== null && object.bbox_xyxy !== undefined) {
      if (!Array.isArray(object.bbox_xyxy)
          || object.bbox_xyxy.length !== 4
          || object.bbox_xyxy.some((value) => !Number.isFinite(value))) {
        throw new Error(`对象 ${index + 1} 的边框必须完整填写四个数字`);
      }
      const [x1, y1, x2, y2] = object.bbox_xyxy;
      if (x2 <= x1 || y2 <= y1) {
        throw new Error(`对象 ${index + 1} 的边框右下角必须大于左上角`);
      }
    }
  });
}

async function reprocessSelected() {
  if (!state.selectedCaptureId) return;
  if (!window.confirm("使用当前全局参数重新处理此任务？旧人工修正会停用但不会删除。")) return;
  try {
    await apiFetch(
      `/api/v1/results/${encodeURIComponent(state.selectedCaptureId)}/reprocess`,
      {method: "POST"},
    );
    showToast("重新处理完成");
    await Promise.all([loadMonitoring(), openDetail(state.selectedCaptureId)]);
  } catch (error) {
    showToast(error.message, true);
  }
}

function buildAuditSection(capture) {
  const section = detailSection("修正审计记录");
  const list = document.createElement("div");
  list.className = "audit-list";
  if (!capture.corrections.length) {
    const empty = document.createElement("div");
    empty.className = "drawer-empty";
    empty.textContent = "暂无人工修正记录";
    list.appendChild(empty);
  }
  capture.corrections.forEach((entry) => {
    const item = document.createElement("div");
    item.className = "audit-item";
    const main = document.createElement("span");
    const operator = document.createElement("strong");
    operator.textContent = entry.operator;
    main.append(operator, document.createTextNode(` · ${entry.reason || "未填写原因"}`));
    const time = document.createElement("span");
    time.textContent = `${formatDate(entry.created_at)} · ${entry.active ? "当前有效" : "历史"}`;
    item.append(main, time);
    list.appendChild(item);
  });
  section.appendChild(list);
  return section;
}

function detailSection(titleText) {
  const section = document.createElement("section");
  section.className = "detail-section";
  const heading = document.createElement("div");
  heading.className = "detail-section-heading";
  const title = document.createElement("h3");
  title.textContent = titleText;
  heading.appendChild(title);
  section.appendChild(heading);
  return section;
}

function bindEvents() {
  byId("refresh-batches").addEventListener("click", loadOfflineBatches);
  const dropZone = byId("drop-zone");
  dropZone.addEventListener("click", () => byId("image-input").click());
  dropZone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") byId("image-input").click();
  });
  ["dragenter", "dragover"].forEach((name) => dropZone.addEventListener(name, (event) => {
    event.preventDefault();
    dropZone.classList.add("dragging");
  }));
  ["dragleave", "drop"].forEach((name) => dropZone.addEventListener(name, (event) => {
    event.preventDefault();
    dropZone.classList.remove("dragging");
  }));
  dropZone.addEventListener("drop", (event) => {
    const files = Array.from(event.dataTransfer.files);
    const metadata = files.find((file) => file.name.toLowerCase().endsWith(".json"));
    if (metadata) loadMetadataFile(metadata);
    addImages(files.filter((file) => file !== metadata));
  });
  byId("image-input").addEventListener("change", (event) => {
    addImages(event.target.files);
    event.target.value = "";
  });
  byId("metadata-input").addEventListener("change", (event) => {
    if (event.target.files[0]) loadMetadataFile(event.target.files[0]);
  });
  byId("clear-upload").addEventListener("click", clearUpload);
  byId("start-upload").addEventListener("click", startUpload);
  byId("retry-upload").addEventListener("click", () => {
    if (state.retryJob) uploadJob(state.retryJob, true);
  });

  ["detector-confidence", "fusion-iou", "meter-confidence"].forEach((id) => {
    byId(id).addEventListener("input", () => {
      byId(`${id}-output`).textContent = Number(byId(id).value).toFixed(2);
      markSettingsDirty();
    });
  });
  byId("save-settings").addEventListener("click", saveRuntimeSettings);
  byId("reset-settings").addEventListener("click", resetRuntimeSettings);
  byId("mode-noop").addEventListener("click", () => switchInferenceMode("noop"));
  byId("mode-gpu").addEventListener("click", () => switchInferenceMode("gpu"));

  byId("refresh-records").addEventListener("click", loadMonitoring);
  byId("select-all-records").addEventListener("change", (event) => {
    state.records.forEach((item) => {
      if (event.target.checked) {
        state.selectedRecordIds.add(item.capture_id);
      } else {
        state.selectedRecordIds.delete(item.capture_id);
      }
    });
    renderRecords();
  });
  byId("delete-selected-records").addEventListener("click", () => {
    deleteRecords([...state.selectedRecordIds]);
  });
  byId("capture-search").addEventListener("input", () => {
    window.clearTimeout(state.searchTimer);
    state.searchTimer = window.setTimeout(() => {
      state.page = 0;
      loadRecords();
    }, 250);
  });
  byId("station-filter").addEventListener("change", () => {
    state.page = 0;
    updateExportLinks();
    loadRecords();
  });
  byId("status-filter").addEventListener("change", () => {
    state.page = 0;
    updateExportLinks();
    loadRecords();
  });
  byId("batch-filter").addEventListener("change", () => {
    state.page = 0;
    updateExportLinks();
    loadRecords();
  });
  byId("previous-page").addEventListener("click", () => {
    if (state.page > 0) {
      state.page -= 1;
      loadRecords();
    }
  });
  byId("next-page").addEventListener("click", () => {
    state.page += 1;
    loadRecords();
  });

  byId("close-drawer").addEventListener("click", closeDrawer);
  byId("drawer-overlay").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeDrawer();
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && byId("auto-refresh").checked) loadMonitoring();
  });
}

async function initialize() {
  bindEvents();
  renderImages();
  renderMetadataPreview();
  await Promise.all([loadRuntimeSettings(), loadMonitoring()]);
  state.pollTimer = window.setInterval(() => {
    if (!document.hidden && byId("auto-refresh").checked && !state.uploading) {
      loadMonitoring();
    }
  }, 3000);
}

initialize();
