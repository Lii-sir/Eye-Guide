(function () {
  const wsUrlEl = document.getElementById("wsUrl");
  const providerEl = document.getElementById("provider");
  const objectSpeechDedupModeEl = document.getElementById("objectSpeechDedupMode");
  const amapApiKeyEl = document.getElementById("amapApiKey");
  const originEl = document.getElementById("origin");
  const destinationEl = document.getElementById("destination");
  const frameIntervalMsEl = document.getElementById("frameIntervalMs");
  const frameQualityEl = document.getElementById("frameQuality");
  const startExploreBtn = document.getElementById("startExploreBtn");
  const startNavigationBtn = document.getElementById("startNavigationBtn");
  const testSpeechBtn = document.getElementById("testSpeechBtn");
  const stopBtn = document.getElementById("stopBtn");
  const useCurrentGpsBtn = document.getElementById("useCurrentGpsBtn");
  const searchRouteBtn = document.getElementById("searchRouteBtn");
  const setRouteBtn = document.getElementById("setRouteBtn");
  const clearRouteBtn = document.getElementById("clearRouteBtn");
  const nextRouteBtn = document.getElementById("nextRouteBtn");
  const repeatRouteBtn = document.getElementById("repeatRouteBtn");
  const startCameraBtn = document.getElementById("startCameraBtn");
  const stopCameraBtn = document.getElementById("stopCameraBtn");
  const imagePickerEl = document.getElementById("imagePicker");
  const sendPickedImageBtn = document.getElementById("sendPickedImageBtn");
  const logEl = document.getElementById("log");
  const sessionStatusEl = document.getElementById("sessionStatus");
  const gpsStatusEl = document.getElementById("gpsStatus");
  const gpsFixEl = document.getElementById("gpsFix");
  const hazardStatusEl = document.getElementById("hazardStatus");
  const routeStatusEl = document.getElementById("routeStatus");
  const speechStatusEl = document.getElementById("speechStatus");
  const connectionBadgeEl = document.getElementById("connectionBadge");
  const cameraDiagEl = document.getElementById("cameraDiag");
  const cameraPreviewEl = document.getElementById("cameraPreview");
  const overlayCanvasEl = document.getElementById("overlayCanvas");
  const frameCanvasEl = document.getElementById("frameCanvas");
  const candidatePanelEl = document.getElementById("candidatePanel");
  const candidateSummaryEl = document.getElementById("candidateSummary");
  const candidateListEl = document.getElementById("candidateList");

  const deviceId = "phone_" + Math.random().toString(36).slice(2, 10);
  const sessionId = "sess_" + Date.now();
  const SILENT_WAV_DATA_URL =
    "data:audio/wav;base64,UklGRlIAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YS4AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";

  let ws = null;
  let seq = 0;
  let socketOpenPromise = null;
  let watchId = null;
  let orientationHandler = null;
  let cameraStream = null;
  let frameLoopHandle = null;
  let lastFrameSentAt = 0;
  let lastOrientationSentAt = 0;
  let lastVisionSummary = "";
  let lastVisionSummaryAt = 0;
  let currentFix = null;
  let selectedCandidate = null;
  let audioReady = false;
  let audioPrimed = false;
  let audioWarningShown = false;
  let audioQueue = [];
  let audioPlaying = false;
  let audioCurrentUrl = "";
  let currentAudioMessageId = "";
  const audioPlayer = new Audio();
  audioPlayer.preload = "auto";
  audioPlayer.playsInline = true;

  function log(message) {
    const line = `[${new Date().toLocaleTimeString()}] ${message}`;
    logEl.textContent = `${line}\n${logEl.textContent}`.slice(0, 12000);
  }

  function setText(el, text) {
    if (el) {
      el.textContent = text;
    }
  }

  function setSessionStatus(text) {
    setText(sessionStatusEl, text);
  }

  function setGpsStatus(text) {
    setText(gpsStatusEl, text);
  }

  function setGpsFixText(text) {
    setText(gpsFixEl, text);
  }

  function setHazardStatus(text) {
    setText(hazardStatusEl, text);
  }

  function setRouteStatus(text) {
    setText(routeStatusEl, text);
  }

  function setSpeechStatus(text) {
    setText(speechStatusEl, text);
  }

  function setConnectionBadge(online) {
    connectionBadgeEl.textContent = online ? "已连接" : "未连接";
    connectionBadgeEl.className = online ? "badge badge-online" : "badge badge-offline";
  }

  function setCameraRunning(isRunning) {
    startCameraBtn.disabled = isRunning;
    stopCameraBtn.disabled = !isRunning;
  }

  function setCameraDiag(message) {
    cameraDiagEl.textContent = message;
  }

  function inferDefaultWsUrl() {
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    return `${protocol}://${window.location.host}/ws/track`;
  }

  function formatCoords(lat, lon) {
    return `${Number(lat).toFixed(6)}, ${Number(lon).toFixed(6)}`;
  }

  function formatFix(coords) {
    if (!coords) return "暂无定位坐标";
    const parts = [formatCoords(coords.lat, coords.lon)];
    if (typeof coords.accuracy === "number") {
      parts.push(`精度约 ${Math.round(coords.accuracy)}m`);
    }
    if (typeof coords.speed_mps === "number") {
      parts.push(`速度 ${(coords.speed_mps * 3.6).toFixed(1)}km/h`);
    }
    return parts.join(" | ");
  }

  function dedupModeLabel(value) {
    return value === "simple" ? "简单播报" : "详细播报";
  }

  function applySettings() {
    if (!send("config.update", buildSettingsPayload())) {
      log("设置尚未发送，等待连接建立。");
    }
  }

  function buildSettingsPayload() {
    return {
      provider: providerEl.value.trim() || "osm",
      amap_api_key: amapApiKeyEl.value.trim(),
      object_speech_dedup_mode: objectSpeechDedupModeEl.value,
    };
  }

  function send(type, payload) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    ws.send(
      JSON.stringify({
        type,
        session_id: sessionId,
        device_id: deviceId,
        seq: seq++,
        ts: Date.now() / 1000,
        payload: payload || {},
      })
    );
    return true;
  }

  function sendSpeechAck(messageId, status) {
    const normalizedId = String(messageId || "").trim();
    if (!normalizedId) return;
    send("speech.ack", {
      message_id: normalizedId,
      status: String(status || "completed"),
    });
  }

  function stopAudioPlayback() {
    audioPlayer.pause();
    if (audioCurrentUrl && audioCurrentUrl.startsWith("blob:")) {
      URL.revokeObjectURL(audioCurrentUrl);
    }
    audioCurrentUrl = "";
    currentAudioMessageId = "";
    audioPlayer.removeAttribute("src");
    audioPlayer.load();
    audioPlaying = false;
  }

  async function unlockAudioPlayback() {
    if (audioReady) return true;
    if (audioPrimed) return audioReady;
    audioPrimed = true;
    setSpeechStatus("正在启用音频播放");

    try {
      audioPlayer.src = SILENT_WAV_DATA_URL;
      audioPlayer.currentTime = 0;
      await audioPlayer.play();
      audioPlayer.pause();
      audioPlayer.currentTime = 0;
      audioPlayer.removeAttribute("src");
      audioPlayer.load();
      audioReady = true;
      setSpeechStatus("音频播放已就绪");
      return true;
    } catch (error) {
      audioReady = false;
      audioPrimed = false;
      setSpeechStatus("音频播放未授权");
      log(`音频播放启用失败：${error.message}`);
      return false;
    }
  }

  function normalizeAudioItems(items) {
    return (Array.isArray(items) ? items : [])
      .map((item) => ({
        kind: String(item?.kind || "speak").trim() || "speak",
        command: String(item?.command || "").trim(),
        messageId: String(item?.message_id || "").trim(),
        text: String(item?.text || "").trim(),
        interrupt: !!item?.interrupt,
        audioUrl: String(item?.audio_url || item?.audio_data_url || "").trim(),
      }))
      .filter((item) => item.kind === "control" || item.text);
  }

  function enqueueSpeechItems(items) {
    const normalizedItems = normalizeAudioItems(items);
    if (!normalizedItems.length) return;

    if (normalizedItems.some((item) => item.interrupt)) {
      const interruptedId = currentAudioMessageId;
      audioQueue = [];
      stopAudioPlayback();
      if (interruptedId) {
        sendSpeechAck(interruptedId, "stopped");
      }
      setSpeechStatus("已打断，准备播放");
    }

    audioQueue.push(...normalizedItems);
    void playNextAudio();
  }

  async function playNextAudio() {
    if (audioPlaying || !audioQueue.length) return;
    if (!audioReady) {
      setSpeechStatus("等待用户点击启用音频");
      if (!audioWarningShown) {
        log("音频播放尚未启用，请先点击页面按钮。");
        audioWarningShown = true;
      }
      return;
    }

    const nextItem = audioQueue.shift();
    if (!nextItem) return;

    if (nextItem.kind === "control") {
      if (nextItem.command === "stop") {
        if (!nextItem.messageId || nextItem.messageId === currentAudioMessageId) {
          const interruptedId = currentAudioMessageId;
          stopAudioPlayback();
          if (interruptedId) {
            sendSpeechAck(interruptedId, "stopped");
          }
          setSpeechStatus(audioReady ? "音频播放已就绪" : "等待用户点击启用音频");
        }
      }
      void playNextAudio();
      return;
    }

    if (!nextItem.audioUrl) {
      setSpeechStatus("缺少音频数据");
      log(`播报已下发文本但没有音频：${nextItem.text}`);
      sendSpeechAck(nextItem.messageId, "missing-audio");
      void playNextAudio();
      return;
    }

    audioPlaying = true;
    audioCurrentUrl = new URL(nextItem.audioUrl, window.location.origin).toString();
    currentAudioMessageId = nextItem.messageId;
    audioPlayer.src = audioCurrentUrl;
    audioPlayer.currentTime = 0;
    setSpeechStatus("音频播放中");

    try {
      await audioPlayer.play();
    } catch (error) {
      sendSpeechAck(nextItem.messageId, "error");
      audioPlaying = false;
      currentAudioMessageId = "";
      setSpeechStatus("音频播放失败");
      log(`音频播放失败：${error.message}`);
    }
  }

  audioPlayer.addEventListener("ended", () => {
    const finishedMessageId = currentAudioMessageId;
    stopAudioPlayback();
    sendSpeechAck(finishedMessageId, "ended");
    setSpeechStatus(audioReady ? "音频播放已就绪" : "等待用户点击启用音频");
    void playNextAudio();
  });

  audioPlayer.addEventListener("error", () => {
    const failedMessageId = currentAudioMessageId;
    stopAudioPlayback();
    sendSpeechAck(failedMessageId, "error");
    setSpeechStatus("音频播放失败");
    log("音频解码或播放失败。");
    void playNextAudio();
  });

  function resizeOverlayCanvas() {
    const rect = cameraPreviewEl.getBoundingClientRect();
    const width = Math.max(1, Math.round(rect.width));
    const height = Math.max(1, Math.round(rect.height));
    const dpr = Math.max(1, window.devicePixelRatio || 1);
    const targetWidth = Math.round(width * dpr);
    const targetHeight = Math.round(height * dpr);
    if (overlayCanvasEl.width !== targetWidth || overlayCanvasEl.height !== targetHeight) {
      overlayCanvasEl.width = targetWidth;
      overlayCanvasEl.height = targetHeight;
    }
  }

  function clearOverlay() {
    resizeOverlayCanvas();
    const ctx = overlayCanvasEl.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, overlayCanvasEl.width, overlayCanvasEl.height);
  }

  function drawVisionOverlay(vision) {
    void vision;
    clearOverlay();
  }

  function frameIntervalMs() {
    const value = Number(frameIntervalMsEl.value);
    if (!Number.isFinite(value)) return 0;
    return Math.max(0, Math.min(5000, Math.floor(value)));
  }

  function frameQuality() {
    const value = Number(frameQualityEl.value);
    if (!Number.isFinite(value)) return 0.6;
    return Math.max(0.2, Math.min(0.92, value));
  }

  function stopFrameLoop() {
    if (frameLoopHandle !== null) {
      cancelAnimationFrame(frameLoopHandle);
      frameLoopHandle = null;
    }
  }

  function stopCameraStreamTracks() {
    if (!cameraStream) return;
    cameraStream.getTracks().forEach((track) => track.stop());
    cameraStream = null;
    cameraPreviewEl.srcObject = null;
  }

  function sendFrame() {
    if (!cameraStream) return;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    if (!cameraPreviewEl.videoWidth || !cameraPreviewEl.videoHeight) return;

    const targetWidth = 640;
    const ratio = targetWidth / cameraPreviewEl.videoWidth;
    const targetHeight = Math.max(1, Math.round(cameraPreviewEl.videoHeight * ratio));
    frameCanvasEl.width = targetWidth;
    frameCanvasEl.height = targetHeight;

    const ctx = frameCanvasEl.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(cameraPreviewEl, 0, 0, targetWidth, targetHeight);
    send("sensor.frame", {
      image_b64: frameCanvasEl.toDataURL("image/jpeg", frameQuality()),
      mime: "image/jpeg",
      width: targetWidth,
      height: targetHeight,
    });
  }

  function startFrameLoop() {
    stopFrameLoop();
    lastFrameSentAt = 0;

    const tick = (now) => {
      frameLoopHandle = requestAnimationFrame(tick);
      const minInterval = frameIntervalMs();
      if (minInterval > 0 && now - lastFrameSentAt < minInterval) return;
      lastFrameSentAt = now;
      sendFrame();
    };

    frameLoopHandle = requestAnimationFrame(tick);
  }

  async function startCameraStream() {
    const hasMediaDevices = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
    setCameraDiag(`secure=${window.isSecureContext} protocol=${window.location.protocol} mediaDevices=${hasMediaDevices}`);
    if (!hasMediaDevices) {
      log("当前浏览器/环境不支持相机 API，请尝试 HTTPS 或主浏览器。");
      return;
    }
    if (cameraStream) return;

    try {
      cameraStream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: { ideal: "environment" },
          width: { ideal: 1280 },
          height: { ideal: 720 },
        },
        audio: false,
      });
      cameraPreviewEl.srcObject = cameraStream;
      await cameraPreviewEl.play();
      resizeOverlayCanvas();
      setCameraRunning(true);
      startFrameLoop();
      log(`相机已启动。最小上传间隔=${frameIntervalMs()}ms，JPEG 质量=${frameQuality()}`);
    } catch (error) {
      log(`相机启动失败：${error.message}`);
      stopCamera();
    }
  }

  function stopCamera() {
    stopFrameLoop();
    stopCameraStreamTracks();
    clearOverlay();
    setCameraRunning(false);
    log("相机上传已停止。");
  }

  function sendImageFromElement(imageEl) {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      log("当前尚未连接到电脑端服务。");
      return;
    }
    const width = imageEl.naturalWidth || imageEl.videoWidth || imageEl.width;
    const height = imageEl.naturalHeight || imageEl.videoHeight || imageEl.height;
    if (!width || !height) {
      log("图片尺寸无效。");
      return;
    }

    const targetWidth = 640;
    const ratio = targetWidth / width;
    const targetHeight = Math.max(1, Math.round(height * ratio));
    frameCanvasEl.width = targetWidth;
    frameCanvasEl.height = targetHeight;

    const ctx = frameCanvasEl.getContext("2d");
    if (!ctx) {
      log("画布上下文不可用。");
      return;
    }
    ctx.drawImage(imageEl, 0, 0, targetWidth, targetHeight);
    send("sensor.frame", {
      image_b64: frameCanvasEl.toDataURL("image/jpeg", frameQuality()),
      mime: "image/jpeg",
      width: targetWidth,
      height: targetHeight,
    });
  }

  function sendPickedImage() {
    const file = imagePickerEl.files && imagePickerEl.files[0];
    if (!file) {
      log("请先选择一张图片。");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const img = new Image();
      img.onload = () => {
        sendImageFromElement(img);
        log("已发送图片模拟推理。");
      };
      img.onerror = () => log("图片解码失败。");
      img.src = String(reader.result || "");
    };
    reader.onerror = () => log("图片读取失败。");
    reader.readAsDataURL(file);
  }

  async function requestOrientationPermissionIfNeeded() {
    if (typeof DeviceOrientationEvent === "undefined") return;
    if (typeof DeviceOrientationEvent.requestPermission !== "function") return;

    try {
      const permission = await DeviceOrientationEvent.requestPermission();
      log(`方向权限：${permission}`);
    } catch (error) {
      log(`方向权限申请失败：${error.message}`);
    }
  }

  function beginSensors() {
    if (!navigator.geolocation) {
      setGpsStatus("当前浏览器不支持地理定位");
      log("当前浏览器不支持地理定位。");
      return;
    }
    if (watchId !== null) return;

    setGpsStatus("定位采集中");
    watchId = navigator.geolocation.watchPosition(
      (pos) => {
        currentFix = {
          lat: pos.coords.latitude,
          lon: pos.coords.longitude,
          accuracy: pos.coords.accuracy,
          speed_mps: typeof pos.coords.speed === "number" ? pos.coords.speed : 0,
          heading_deg: typeof pos.coords.heading === "number" ? pos.coords.heading : null,
          altitude: typeof pos.coords.altitude === "number" ? pos.coords.altitude : null,
        };
        setGpsStatus("定位正常");
        setGpsFixText(formatFix(currentFix));
        send("sensor.gps", currentFix);
      },
      (err) => {
        setGpsStatus("定位失败");
        log(`定位错误：${err.message}`);
      },
      {
        enableHighAccuracy: true,
        timeout: 10000,
        maximumAge: 1000,
      }
    );

    orientationHandler = (ev) => {
      const now = Date.now();
      if (now - lastOrientationSentAt < 400) return;
      lastOrientationSentAt = now;
      send("sensor.orientation", {
        alpha: ev.alpha,
        beta: ev.beta,
        gamma: ev.gamma,
        absolute: !!ev.absolute,
      });
    };
    window.addEventListener("deviceorientation", orientationHandler);
  }

  function stopSensors() {
    if (watchId !== null) {
      navigator.geolocation.clearWatch(watchId);
      watchId = null;
    }
    if (orientationHandler) {
      window.removeEventListener("deviceorientation", orientationHandler);
      orientationHandler = null;
    }
    setGpsStatus("未开始定位");
  }

  function closeSocket() {
    if (!ws) return;
    ws.onopen = null;
    ws.onclose = null;
    ws.onmessage = null;
    ws.onerror = null;
    ws.close();
    ws = null;
    socketOpenPromise = null;
    setConnectionBadge(false);
  }

  function connectSocket() {
    const wsUrl = wsUrlEl.value.trim();
    if (!wsUrl) {
      return Promise.reject(new Error("WebSocket 地址不能为空。"));
    }
    if (ws && ws.readyState === WebSocket.OPEN) {
      return Promise.resolve();
    }
    if (ws && ws.readyState === WebSocket.CONNECTING && socketOpenPromise) {
      return socketOpenPromise;
    }

    closeSocket();
    socketOpenPromise = new Promise((resolve, reject) => {
      let settled = false;
      ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        settled = true;
        setConnectionBadge(true);
        setRunning(true);
        setSessionStatus("会话已连接，等待模式启动");
        log("已连接到电脑端服务。");
        send("hello", { app_ver: "0.4.0", platform: "mobile-web" });
        send("config.update", buildSettingsPayload());
        resolve();
      };

      ws.onmessage = handleSocketMessage;
      ws.onerror = () => {
        log("Socket 连接异常。");
      };
      ws.onclose = () => {
        if (!settled) {
          reject(new Error("Socket 连接失败。"));
        }
        log("Socket 已关闭。");
        setConnectionBadge(false);
        setRunning(false);
        stopSensors();
        stopFrameLoop();
        ws = null;
        socketOpenPromise = null;
      };
    });

    return socketOpenPromise;
  }

  function buildRoutePayload() {
    const destinationText = destinationEl.value.trim();
    if (!destinationText) return null;
    return {
      origin: originEl.value.trim() || "@gps",
      destination: selectedCandidate ? selectedCandidate.route_value : destinationText,
      display_name: selectedCandidate ? selectedCandidate.display_name : destinationText,
      provider: providerEl.value.trim() || "osm",
      amap_api_key: amapApiKeyEl.value.trim(),
      prefer_online: true,
      use_demo_fallback: true,
    };
  }

  function requestRouteCandidates() {
    const destinationText = destinationEl.value.trim();
    if (!destinationText) {
      log("请先输入终点，再搜索候选。");
      return;
    }
    if (
      !send("route.search", {
        origin: originEl.value.trim() || "@gps",
        destination: destinationText,
        provider: providerEl.value.trim() || "osm",
        amap_api_key: amapApiKeyEl.value.trim(),
        limit: 5,
      })
    ) {
      log("请先建立连接后再搜索候选。");
      return;
    }
    log("已发送终点候选搜索请求。");
  }

  function setRoute() {
    const payload = buildRoutePayload();
    if (!payload) {
      log("请先输入终点。");
      return;
    }
    if (!send("route.set", payload)) {
      log("请先建立连接后再生成路线。");
      return;
    }
    log("已发送路线生成请求。");
  }

  function clearRoute() {
    renderCandidates([]);
    selectedCandidate = null;
    if (!send("route.clear", {})) {
      log("请先建立连接后再清除路线。");
      return;
    }
    setRouteStatus("暂无导航指令");
    log("已发送清除路线请求。");
  }

  function useCurrentGpsAsOrigin() {
    if (!currentFix) {
      log("当前还没有可用定位，暂时无法填入起点。");
      return;
    }
    originEl.value = formatCoords(currentFix.lat, currentFix.lon);
    log("已将当前位置填入起点。");
  }

  function renderCandidates(candidates) {
    const list = Array.isArray(candidates) ? candidates : [];
    candidateListEl.innerHTML = "";
    if (!list.length) {
      candidatePanelEl.classList.add("hidden");
      candidateSummaryEl.textContent = "暂无候选";
      return;
    }

    candidatePanelEl.classList.remove("hidden");
    candidateSummaryEl.textContent = `共 ${list.length} 个候选，点击即可选中`;

    for (const item of list) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "candidate-item";
      if (selectedCandidate && selectedCandidate.route_value === item.route_value) {
        button.classList.add("active");
      }
      button.innerHTML =
        `<span class="candidate-title">${item.display_name}</span>` +
        `<span class="candidate-meta">来源：${item.provider || "-"}${typeof item.score === "number" ? ` | 评分：${item.score}` : ""}</span>`;
      button.addEventListener("click", () => {
        selectedCandidate = item;
        destinationEl.value = item.display_name;
        renderCandidates(list);
        log(`已选择终点候选：${item.display_name}`);
      });
      candidateListEl.appendChild(button);
    }
  }

  async function ensureSessionReady(options) {
    const opts = Object.assign({ openCamera: true }, options || {});
    await unlockAudioPlayback();
    await requestOrientationPermissionIfNeeded();
    await connectSocket();
    beginSensors();
    applySettings();
    if (opts.openCamera) {
      await startCameraStream();
    }
  }

  async function startExploreMode() {
    await ensureSessionReady({ openCamera: true });
    send("session.start", { mode: "explore" });
    setSessionStatus("自由探索进行中");
    log(`普通物体播报去重模式：${dedupModeLabel(objectSpeechDedupModeEl.value)}`);
  }

  async function startNavigationMode() {
    await ensureSessionReady({ openCamera: true });
    send("session.start", { mode: "navigation" });
    setSessionStatus("路线导航进行中");
    setRoute();
  }

  function stop() {
    stopSensors();
    stopCamera();
    audioQueue = [];
    stopAudioPlayback();
    closeSocket();
    setRunning(false);
    setSessionStatus("会话已停止");
    log("手机端采集已停止。");
  }

  function handleInferResult(payload) {
    if (!payload || typeof payload !== "object") return;

    if (Array.isArray(payload.tts_items) && payload.tts_items.length) {
      const texts = payload.tts_items.map((item) => String(item?.text || "").trim()).filter(Boolean);
      if (texts.length) {
        log(`播报：${texts.join(" | ")}`);
      }
      enqueueSpeechItems(payload.tts_items);
    } else if (payload.tts) {
      log(`播报文本：${payload.tts}`);
    }

    if (payload.location) {
      setGpsFixText(
        formatFix({
          lat: payload.location.lat,
          lon: payload.location.lon,
          accuracy: payload.location.accuracy_m,
          speed_mps: typeof payload.location.speed_kmh === "number" ? payload.location.speed_kmh / 3.6 : 0,
        })
      );
    }

    if (payload.vision) {
      const visionSummary = String(payload.vision.hazard_summary || "").trim() || "环境相对安全";
      setHazardStatus(visionSummary);
      const now = Date.now();
      if (visionSummary !== lastVisionSummary || now - lastVisionSummaryAt >= 4000) {
        log(`视觉：${visionSummary}`);
        lastVisionSummary = visionSummary;
        lastVisionSummaryAt = now;
      }
      drawVisionOverlay(payload.vision);
    }

    if (payload.route) {
      if (payload.route.active) {
        const step = payload.route.step_index;
        const total = payload.route.step_total;
        const instruction = payload.route.instruction || "暂无导航指令";
        const distance = payload.route.distance_to_step_m;
        setRouteStatus(instruction);
        log(`导航：第 ${step}/${total} 条${distance != null ? `，距当前节点约 ${distance}m` : ""}，${instruction}`);
      } else {
        setRouteStatus("暂无导航指令");
      }
    }
  }

  function handleRouteStatus(payload) {
    if (!payload || typeof payload !== "object") return;
    if (Array.isArray(payload.candidates)) {
      renderCandidates(payload.candidates);
      log(`收到 ${payload.candidates.length} 个终点候选。`);
    }
    if (Array.isArray(payload.debug_lines)) {
      for (const line of payload.debug_lines) {
        log(`候选搜索：${line}`);
      }
    }
    if (payload.instruction) {
      setRouteStatus(payload.instruction);
    }
    if (payload.message) {
      log(`路线状态：${payload.message}`);
    } else {
      log(`路线状态：${JSON.stringify(payload)}`);
    }
  }

  function handleConfigStatus(payload) {
    if (!payload || typeof payload !== "object") return;
    if (payload.object_speech_dedup_mode) {
      objectSpeechDedupModeEl.value = payload.object_speech_dedup_mode;
    }
    if (payload.message) {
      log(payload.message);
    }
  }

  function handleSocketMessage(evt) {
    try {
      const msg = JSON.parse(evt.data);
      if (msg.type === "infer.result") {
        handleInferResult(msg.payload);
        return;
      }
      if (msg.type === "route.status") {
        handleRouteStatus(msg.payload);
        return;
      }
      if (msg.type === "config.status") {
        handleConfigStatus(msg.payload);
        return;
      }
      if (msg.type === "ack" || msg.type === "pong") {
        return;
      }
      if (msg.type === "error") {
        log(`服务端错误：${msg.payload?.code || "UNKNOWN"} ${msg.payload?.message || ""}`);
        return;
      }
      log(`消息：${evt.data}`);
    } catch (error) {
      log(`消息：${evt.data}`);
    }
  }

  startExploreBtn.addEventListener("click", () => {
    startExploreMode().catch((err) => {
      log(`启动自由探索失败：${err.message}`);
      setRunning(false);
    });
  });
  startNavigationBtn.addEventListener("click", () => {
    startNavigationMode().catch((err) => {
      log(`启动路线导航失败：${err.message}`);
      setRunning(false);
    });
  });
  testSpeechBtn.addEventListener("click", async () => {
    try {
      await ensureSessionReady({ openCamera: false });
      send("speech.test", {});
    } catch (err) {
      log(`测试语音失败：${err.message}`);
    }
  });
  stopBtn.addEventListener("click", stop);
  useCurrentGpsBtn.addEventListener("click", useCurrentGpsAsOrigin);
  searchRouteBtn.addEventListener("click", async () => {
    try {
      await ensureSessionReady({ openCamera: false });
      requestRouteCandidates();
    } catch (err) {
      log(`搜索候选失败：${err.message}`);
    }
  });
  setRouteBtn.addEventListener("click", async () => {
    try {
      await ensureSessionReady({ openCamera: false });
      setRoute();
    } catch (err) {
      log(`生成路线失败：${err.message}`);
    }
  });
  clearRouteBtn.addEventListener("click", clearRoute);
  nextRouteBtn.addEventListener("click", () => {
    if (!send("route.next", {})) {
      log("请先建立连接后再切换下一条指令。");
    }
  });
  repeatRouteBtn.addEventListener("click", () => {
    if (!send("route.repeat", {})) {
      log("请先建立连接后再重复当前指令。");
    }
  });
  startCameraBtn.addEventListener("click", () => {
    startCameraStream().catch((err) => {
      log(`启动相机失败：${err.message}`);
      setCameraRunning(false);
    });
  });
  stopCameraBtn.addEventListener("click", stopCamera);
  sendPickedImageBtn.addEventListener("click", sendPickedImage);
  providerEl.addEventListener("change", applySettings);
  objectSpeechDedupModeEl.addEventListener("change", applySettings);
  amapApiKeyEl.addEventListener("change", applySettings);
  destinationEl.addEventListener("input", () => {
    selectedCandidate = null;
  });
  window.addEventListener("resize", resizeOverlayCanvas);
  cameraPreviewEl.addEventListener("loadedmetadata", resizeOverlayCanvas);
  cameraPreviewEl.addEventListener("playing", resizeOverlayCanvas);

  setConnectionBadge(false);
  setSessionStatus("等待启动");
  setGpsStatus("未开始定位");
  setGpsFixText("暂无定位坐标");
  setHazardStatus("暂无实时信息");
  setRouteStatus("暂无导航指令");
  setSpeechStatus("等待用户点击启用音频");
  setCameraDiag(
    `secure=${window.isSecureContext} protocol=${window.location.protocol} mediaDevices=${!!(
      navigator.mediaDevices && navigator.mediaDevices.getUserMedia
    )}`
  );

  if (!wsUrlEl.value || wsUrlEl.value.includes("192.168.1.100")) {
    wsUrlEl.value = inferDefaultWsUrl();
  }
  clearOverlay();
})();
