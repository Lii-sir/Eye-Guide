(function () {
  const elements = {
    wsUrl: document.getElementById("wsUrl"),
    connectBtn: document.getElementById("connectBtn"),
    disconnectBtn: document.getElementById("disconnectBtn"),
    speakUnlockBtn: document.getElementById("speakUnlockBtn"),
    testSpeechBtn: document.getElementById("testSpeechBtn"),
    connectionBadge: document.getElementById("connectionBadge"),
    latencyBadge: document.getElementById("latencyBadge"),
    sessionStatus: document.getElementById("sessionStatus"),
    gpsStatus: document.getElementById("gpsStatus"),
    gpsFix: document.getElementById("gpsFix"),
    hazardStatus: document.getElementById("hazardStatus"),
    routeStatus: document.getElementById("routeStatus"),
    speechStatus: document.getElementById("speechStatus"),
    cameraSelect: document.getElementById("cameraSelect"),
    gpsSourceSelect: document.getElementById("gpsSourceSelect"),
    qualitySelect: document.getElementById("qualitySelect"),
    fpsSelect: document.getElementById("fpsSelect"),
    profileSelect: document.getElementById("profileSelect"),
    resolutionSelect: document.getElementById("resolutionSelect"),
    startCaptureBtn: document.getElementById("startCaptureBtn"),
    stopCaptureBtn: document.getElementById("stopCaptureBtn"),
    refreshCameraBtn: document.getElementById("refreshCameraBtn"),
    captureHint: document.getElementById("captureHint"),
    providerSelect: document.getElementById("providerSelect"),
    dedupModeSelect: document.getElementById("dedupModeSelect"),
    amapKeyInput: document.getElementById("amapKeyInput"),
    originInput: document.getElementById("originInput"),
    destinationInput: document.getElementById("destinationInput"),
    useGpsOriginBtn: document.getElementById("useGpsOriginBtn"),
    searchRouteBtn: document.getElementById("searchRouteBtn"),
    setRouteBtn: document.getElementById("setRouteBtn"),
    clearRouteBtn: document.getElementById("clearRouteBtn"),
    nextRouteBtn: document.getElementById("nextRouteBtn"),
    repeatRouteBtn: document.getElementById("repeatRouteBtn"),
    candidatePanel: document.getElementById("candidatePanel"),
    candidateSummary: document.getElementById("candidateSummary"),
    candidateList: document.getElementById("candidateList"),
    cameraPreview: document.getElementById("cameraPreview"),
    overlayCanvas: document.getElementById("overlayCanvas"),
    frameCanvas: document.getElementById("frameCanvas"),
    logBox: document.getElementById("logBox"),
  };

  const state = {
    ws: null,
    seq: 0,
    deviceId: `phone_${Math.random().toString(36).slice(2, 10)}`,
    sessionId: `sess_${Date.now()}`,
    cameraStream: null,
    captureTimer: null,
    gpsWatchId: null,
    orientationListener: null,
    audioUnlocked: false,
    lastFix: null,
    lastImu: null,
    selectedCandidate: null,
    latestFrameClientTs: 0,
    latestSpeechKey: "",
    lastSpeechAt: 0,
    speakingNow: false,
    lastSpokenByKey: {},
    persistentSeen: {},
    speechPolicy: {
      persistentDedupeTtlSeconds: 20,
    },
  };

  // 视觉框要求更强的时效性，超过 500ms 就不再可信；
  // 但语音提示在移动端可以适当放宽，否则网络稍有抖动时，
  // 前端会因为 should_drop 提前 return，导致“测试语音正常、实时语音完全不响”。
  const SPEECH_MAX_RESULT_AGE_MS = 1500;

  function log(message) {
    const line = `[${new Date().toLocaleTimeString()}] ${message}`;
    elements.logBox.textContent = `${line}\n${elements.logBox.textContent}`.slice(0, 12000);
  }

  function inferDefaultWsUrl() {
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    return `${protocol}://${window.location.host}/ws`;
  }

  function setBadge(isOnline) {
    elements.connectionBadge.textContent = isOnline ? "已连接" : "未连接";
    elements.connectionBadge.className = isOnline ? "badge online" : "badge offline";
  }

  function setLatencyBadge(text, tone) {
    elements.latencyBadge.textContent = text;
    elements.latencyBadge.className = `badge ${tone || "neutral"}`;
  }

  function setText(node, text) {
    node.textContent = text;
  }

  function send(type, payload) {
    if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
      return false;
    }
    state.ws.send(
      JSON.stringify({
        type,
        session_id: state.sessionId,
        device_id: state.deviceId,
        seq: state.seq++,
        ts: Date.now() / 1000,
        payload: payload || {},
      })
    );
    return true;
  }

  function buildSettingsPayload() {
    return {
      provider: elements.providerSelect.value,
      amap_api_key: elements.amapKeyInput.value.trim(),
      object_speech_dedup_mode: elements.dedupModeSelect.value,
    };
  }

  async function connectSocket() {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      return;
    }

    const wsUrl = elements.wsUrl.value.trim();
    if (!wsUrl) {
      throw new Error("WebSocket 地址不能为空");
    }

    await new Promise((resolve, reject) => {
      const ws = new WebSocket(wsUrl);
      state.ws = ws;

      ws.onopen = function () {
        setBadge(true);
        setText(elements.sessionStatus, "连接成功，等待采集");
        log("WebSocket 已连接。");
        send("hello", { platform: "mobile-web-mvp" });
        send("config.update", buildSettingsPayload());
        resolve();
      };

      ws.onmessage = function (event) {
        handleSocketMessage(event.data);
      };

      ws.onerror = function () {
        log("WebSocket 连接异常。");
      };

      ws.onclose = function () {
        setBadge(false);
        setText(elements.sessionStatus, "连接已断开");
        stopCaptureLoop();
        log("WebSocket 已关闭。");
      };

      ws.addEventListener("error", function () {
        reject(new Error("WebSocket 连接失败"));
      }, { once: true });
    });
  }

  function disconnectSocket() {
    if (!state.ws) {
      return;
    }
    state.ws.close();
    state.ws = null;
    setBadge(false);
  }

  async function unlockSpeech() {
    if (state.audioUnlocked) {
      return true;
    }

    // 先主动发出一段极短的空白语音，借此拿到浏览器的语音播放权限。
    const utterance = new SpeechSynthesisUtterance(" ");
    utterance.volume = 0;

    await new Promise((resolve, reject) => {
      utterance.onend = resolve;
      utterance.onerror = function () {
        reject(new Error("语音权限启用失败"));
      };
      window.speechSynthesis.speak(utterance);
    });

    state.audioUnlocked = true;
    setText(elements.speechStatus, "语音已启用");
    log("浏览器语音播报已启用。");
    return true;
  }

  function prunePersistentSeen(now) {
    const ttlMs = Math.max(0, Number(state.speechPolicy.persistentDedupeTtlSeconds || 20) * 1000);
    Object.keys(state.persistentSeen).forEach(function (key) {
      if (now - Number(state.persistentSeen[key] || 0) >= ttlMs) {
        delete state.persistentSeen[key];
      }
    });
  }

  function speakText(text, meta) {
    const normalized = String(text || "").trim();
    if (!normalized || !state.audioUnlocked) {
      return;
    }

    const options = meta || {};
    const eventKey = String(options.eventKey || normalized).trim();
    const cooldownMs = Math.max(0, Number(options.cooldownSeconds || 0) * 1000);
    const persistentDedupe = !!options.persistentDedupe;
    const channel = String(options.channel || "general");
    const replacePending = !!options.replacePending;
    const interrupt = !!options.interrupt;
    const now = Date.now();

    prunePersistentSeen(now);

    const recentAt = Number(state.lastSpokenByKey[eventKey] || 0);
    if (recentAt > 0 && now - recentAt < cooldownMs) {
      return;
    }

    if (persistentDedupe && state.persistentSeen[eventKey]) {
      return;
    }

    // ?????????
    // - interrupt=True ????????????
    // - ?????????? replace_pending=True ????????
    const shouldInterruptCurrent = state.speakingNow && (
      interrupt || (replacePending && eventKey !== state.latestSpeechKey)
    );
    if (shouldInterruptCurrent) {
      window.speechSynthesis.cancel();
    }

    state.latestSpeechKey = eventKey;
    state.lastSpeechAt = now;
    state.lastSpokenByKey[eventKey] = now;
    if (persistentDedupe) {
      state.persistentSeen[eventKey] = now;
    }

    const utterance = new SpeechSynthesisUtterance(normalized);
    utterance.lang = "zh-CN";
    utterance.rate = 1.0;
    utterance.pitch = 1.0;
    utterance.volume = 1.0;
    utterance.onstart = function () {
      state.speakingNow = true;
      setText(elements.speechStatus, "???");
    };
    utterance.onend = function () {
      state.speakingNow = false;
      setText(elements.speechStatus, "?????");
    };
    utterance.onerror = function () {
      state.speakingNow = false;
      setText(elements.speechStatus, "?????");
    };
    window.speechSynthesis.speak(utterance);
  }

  function getProfilePreset(profile) {
    if (profile === "realtime") {
      return { resolution: "480", quality: "0.45", fps: "8" };
    }
    if (profile === "clarity") {
      return { resolution: "720", quality: "0.8", fps: "4" };
    }
    return { resolution: "640", quality: "0.6", fps: "6" };
  }

  function applyProfile(profile) {
    const preset = getProfilePreset(profile);
    elements.resolutionSelect.value = preset.resolution;
    elements.qualitySelect.value = preset.quality;
    elements.fpsSelect.value = preset.fps;
    log(`已切换到 ${profile} 配置档。`);
  }

  async function refreshCameraOptions() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) {
      return;
    }

    const devices = await navigator.mediaDevices.enumerateDevices();
    const videoInputs = devices.filter(function (item) {
      return item.kind === "videoinput";
    });

    const manualOptions = [
      { value: "environment", label: "后置相机（推荐）" },
      { value: "user", label: "前置相机" },
    ];

    const existingValue = elements.cameraSelect.value;
    elements.cameraSelect.innerHTML = "";

    manualOptions.forEach(function (item) {
      const option = document.createElement("option");
      option.value = item.value;
      option.textContent = item.label;
      elements.cameraSelect.appendChild(option);
    });

    videoInputs.forEach(function (device, index) {
      const option = document.createElement("option");
      option.value = `device:${device.deviceId}`;
      option.textContent = device.label || `摄像头 ${index + 1}`;
      elements.cameraSelect.appendChild(option);
    });

    if ([...elements.cameraSelect.options].some(function (option) { return option.value === existingValue; })) {
      elements.cameraSelect.value = existingValue;
    }
  }

  function buildVideoConstraints() {
    const resolution = Number(elements.resolutionSelect.value || "640");
    const cameraValue = elements.cameraSelect.value;
    const constraints = {
      width: { ideal: resolution },
      height: { ideal: Math.round(resolution * 0.75) },
    };

    if (cameraValue.startsWith("device:")) {
      constraints.deviceId = { exact: cameraValue.slice("device:".length) };
    } else {
      constraints.facingMode = { ideal: cameraValue };
    }
    return constraints;
  }

  async function startCamera() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw new Error("当前浏览器不支持摄像头");
    }

    stopCamera();

    state.cameraStream = await navigator.mediaDevices.getUserMedia({
      video: buildVideoConstraints(),
      audio: false,
    });

    elements.cameraPreview.srcObject = state.cameraStream;
    await elements.cameraPreview.play();
    resizeOverlayCanvas();
    setText(elements.captureHint, "相机已启动，等待上传。");
    log("相机采集已启动。");
  }

  function stopCamera() {
    if (!state.cameraStream) {
      return;
    }
    state.cameraStream.getTracks().forEach(function (track) {
      track.stop();
    });
    state.cameraStream = null;
    elements.cameraPreview.srcObject = null;
    clearOverlay();
  }

  function startGpsWatch() {
    if (!navigator.geolocation || state.gpsWatchId !== null) {
      return;
    }

    state.gpsWatchId = navigator.geolocation.watchPosition(
      function (position) {
        state.lastFix = {
          lat: position.coords.latitude,
          lon: position.coords.longitude,
          accuracy: typeof position.coords.accuracy === "number" ? position.coords.accuracy : null,
          speed_mps: typeof position.coords.speed === "number" && position.coords.speed >= 0
            ? position.coords.speed
            : 0,
          heading_deg: typeof position.coords.heading === "number" ? position.coords.heading : null,
          altitude: typeof position.coords.altitude === "number" ? position.coords.altitude : null,
        };

        setText(elements.gpsStatus, "定位正常");
        setText(elements.gpsFix, formatFixText(state.lastFix));

        if (elements.gpsSourceSelect.value === "separate-only" || elements.gpsSourceSelect.value === "hybrid") {
          send("sensor.gps", state.lastFix);
        }
      },
      function (error) {
        setText(elements.gpsStatus, "定位失败");
        log(`定位错误：${error.message}`);
      },
      {
        enableHighAccuracy: true,
        maximumAge: 1000,
        timeout: 10000,
      }
    );
  }

  function stopGpsWatch() {
    if (state.gpsWatchId !== null) {
      navigator.geolocation.clearWatch(state.gpsWatchId);
      state.gpsWatchId = null;
    }
  }

  async function enableOrientation() {
    if (typeof DeviceOrientationEvent === "undefined") {
      return;
    }
    if (typeof DeviceOrientationEvent.requestPermission === "function") {
      try {
        const result = await DeviceOrientationEvent.requestPermission();
        if (result !== "granted") {
          log("方向权限未授予。");
          return;
        }
      } catch (error) {
        log(`方向权限申请失败：${error.message}`);
        return;
      }
    }

    if (state.orientationListener) {
      return;
    }

    state.orientationListener = function (event) {
      state.lastImu = {
        heading_deg: typeof event.alpha === "number" ? event.alpha : null,
        pitch_deg: typeof event.beta === "number" ? event.beta : null,
        roll_deg: typeof event.gamma === "number" ? event.gamma : null,
        yaw_deg: typeof event.alpha === "number" ? event.alpha : null,
      };
    };
    window.addEventListener("deviceorientation", state.orientationListener);
  }

  function stopOrientation() {
    if (state.orientationListener) {
      window.removeEventListener("deviceorientation", state.orientationListener);
      state.orientationListener = null;
    }
  }

  function getCaptureIntervalMs() {
    const fps = Number(elements.fpsSelect.value || "6");
    return Math.max(80, Math.round(1000 / Math.max(1, fps)));
  }

  function buildFramePayload() {
    if (!elements.cameraPreview.videoWidth || !elements.cameraPreview.videoHeight) {
      return null;
    }

    const targetWidth = Number(elements.resolutionSelect.value || "640");
    const scale = targetWidth / elements.cameraPreview.videoWidth;
    const targetHeight = Math.max(1, Math.round(elements.cameraPreview.videoHeight * scale));
    const quality = Number(elements.qualitySelect.value || "0.6");

    elements.frameCanvas.width = targetWidth;
    elements.frameCanvas.height = targetHeight;

    const ctx = elements.frameCanvas.getContext("2d");
    if (!ctx) {
      return null;
    }
    ctx.drawImage(elements.cameraPreview, 0, 0, targetWidth, targetHeight);

    const frameClientTs = Date.now();
    state.latestFrameClientTs = frameClientTs;

    const payload = {
      frame_id: `frame_${frameClientTs}_${Math.random().toString(36).slice(2, 6)}`,
      client_ts: frameClientTs,
      image_b64: elements.frameCanvas.toDataURL("image/jpeg", quality),
    };

    if (elements.gpsSourceSelect.value === "frame-only" || elements.gpsSourceSelect.value === "hybrid") {
      if (state.lastFix) {
        payload.gps = state.lastFix;
      }
      if (state.lastImu) {
        payload.imu = state.lastImu;
      }
    }
    return payload;
  }

  function startCaptureLoop() {
    stopCaptureLoop();

    state.captureTimer = window.setInterval(function () {
      const payload = buildFramePayload();
      if (!payload) {
        return;
      }
      send("sensor.frame", payload);
    }, getCaptureIntervalMs());
  }

  function stopCaptureLoop() {
    if (state.captureTimer !== null) {
      window.clearInterval(state.captureTimer);
      state.captureTimer = null;
    }
  }

  async function startCapture() {
    await connectSocket();
    await unlockSpeech();
    await startCamera();
    startGpsWatch();
    await enableOrientation();
    send("config.update", buildSettingsPayload());
    startCaptureLoop();
    setText(elements.sessionStatus, "采集中");
    log(
      `开始采集：camera=${elements.cameraSelect.value} gps=${elements.gpsSourceSelect.value} quality=${elements.qualitySelect.value} fps=${elements.fpsSelect.value} profile=${elements.profileSelect.value}`
    );
  }

  function stopCapture() {
    stopCaptureLoop();
    stopGpsWatch();
    stopOrientation();
    stopCamera();
    setText(elements.sessionStatus, "采集已停止");
    setText(elements.gpsStatus, "未开始");
    setText(elements.captureHint, "采集已停止。");
    log("手机端采集已停止。");
  }

  function formatFixText(fix) {
    if (!fix) {
      return "暂无定位坐标";
    }
    const parts = [
      `${Number(fix.lat).toFixed(6)}, ${Number(fix.lon).toFixed(6)}`,
    ];
    if (typeof fix.accuracy === "number" && Number.isFinite(fix.accuracy)) {
      parts.push(`精度约 ${Math.round(fix.accuracy)}m`);
    }
    return parts.join(" | ");
  }

  function resizeOverlayCanvas() {
    const rect = elements.cameraPreview.getBoundingClientRect();
    const dpr = Math.max(1, window.devicePixelRatio || 1);
    const width = Math.max(1, Math.round(rect.width * dpr));
    const height = Math.max(1, Math.round(rect.height * dpr));
    if (elements.overlayCanvas.width !== width || elements.overlayCanvas.height !== height) {
      elements.overlayCanvas.width = width;
      elements.overlayCanvas.height = height;
    }
  }

  function clearOverlay() {
    resizeOverlayCanvas();
    const ctx = elements.overlayCanvas.getContext("2d");
    if (!ctx) {
      return;
    }
    ctx.clearRect(0, 0, elements.overlayCanvas.width, elements.overlayCanvas.height);
  }

  function drawBoxes(resultPayload) {
    clearOverlay();

    const frameShape = resultPayload.frame_shape;
    const boxes = Array.isArray(resultPayload.boxes) ? resultPayload.boxes : [];
    if (!frameShape || !boxes.length) {
      return;
    }

    const ctx = elements.overlayCanvas.getContext("2d");
    if (!ctx) {
      return;
    }

    const scaleX = elements.overlayCanvas.width / Math.max(1, frameShape.width);
    const scaleY = elements.overlayCanvas.height / Math.max(1, frameShape.height);

    ctx.lineWidth = Math.max(2, 2 * (window.devicePixelRatio || 1));
    ctx.font = `${14 * (window.devicePixelRatio || 1)}px sans-serif`;
    ctx.textBaseline = "top";

    boxes.forEach(function (item) {
      const color = item.label === "person" ? "#f59e0b" : "#10b981";
      const x1 = item.box[0] * scaleX;
      const y1 = item.box[1] * scaleY;
      const x2 = item.box[2] * scaleX;
      const y2 = item.box[3] * scaleY;
      const labelParts = [item.label];
      if (item.relative_direction) {
        labelParts.push(item.relative_direction);
      }
      if (typeof item.distance_meters === "number") {
        labelParts.push(`${item.distance_meters.toFixed(1)}m`);
      }

      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.strokeRect(x1, y1, Math.max(1, x2 - x1), Math.max(1, y2 - y1));
      ctx.fillRect(x1, Math.max(0, y1 - 24), Math.max(72, labelParts.join(" ").length * 12), 22);
      ctx.fillStyle = "#ffffff";
      ctx.fillText(labelParts.join(" "), x1 + 6, Math.max(0, y1 - 21));
    });
  }

  function handleFrameAccepted(payload) {
    if (!payload) {
      return;
    }
    if (payload.replaced_frame_id) {
      log(`新帧 ${payload.frame_id} 已接管，旧帧 ${payload.replaced_frame_id} 被覆盖。`);
    }
  }

  function handleFrameDropped(payload) {
    if (!payload) {
      return;
    }
    log(`旧帧 ${payload.frame_id} 已被丢弃，原因：${payload.reason}`);
  }

  function handleFrameResult(payload) {
    if (!payload) {
      return;
    }

    const timing = payload.timing || {};
    const age = Number(timing.result_age_ms || 0);
    const shouldDrop = !!timing.should_drop;

    if (shouldDrop) {
      setLatencyBadge(`结果过期 ${Math.round(age)}ms`, "warning");
      log(`检测结果已过期：frame=${payload.frame_id} age=${Math.round(age)}ms`);

      // 对于明显过期的结果，继续丢弃框绘制，避免画面和结果错位。
      // 但如果只是“略微过期”，仍然允许更新状态文本并播报，
      // 这样手机端至少还能听到风险提示，而不是完全静默。
      setText(elements.hazardStatus, payload.hazard_summary || "环境相对安全");

      const staleSpeechText = String(payload.top_event || "").trim();
      const staleTopEvent = payload.events && payload.events[0] ? payload.events[0] : null;
      const staleEventKey = String(
        (staleTopEvent && (staleTopEvent.dedupe_key || staleTopEvent.category)) || staleSpeechText
      ).trim();
      if (age <= SPEECH_MAX_RESULT_AGE_MS && staleSpeechText) {
        speakText(staleSpeechText, {
          eventKey: staleEventKey,
          cooldownSeconds: staleTopEvent ? staleTopEvent.cooldown_seconds : 0,
          persistentDedupe: staleTopEvent ? staleTopEvent.persistent_dedupe : false,
          channel: staleTopEvent ? staleTopEvent.channel : "vision",
          replacePending: staleTopEvent ? staleTopEvent.replace_pending : true,
          interrupt: staleTopEvent ? staleTopEvent.interrupt : false,
        });
      }
      return;
    }

    setLatencyBadge(`端到端 ${Math.round(age)}ms`, age > 320 ? "warning" : "neutral");
    setText(elements.hazardStatus, payload.hazard_summary || "环境相对安全");

    if (payload.location) {
      setText(elements.gpsFix, formatFixText({
        lat: payload.location.lat,
        lon: payload.location.lon,
        accuracy: payload.location.accuracy_m,
      }));
    }

    if (payload.route && payload.route.active) {
      setText(elements.routeStatus, payload.route.instruction || "暂无导航指令");
    }

    drawBoxes(payload);

    // 与桌面端保持一致：只有真正的事件才触发播报。
    // “环境相对安全”只作为状态文本展示，不参与 TTS。
    const speechText = String(payload.top_event || "").trim();
    const topEvent = payload.events && payload.events[0] ? payload.events[0] : null;
    const topEventKey = String(
      (topEvent && (topEvent.dedupe_key || topEvent.category)) || speechText
    ).trim();
    if (speechText) {
      speakText(speechText, {
        eventKey: topEventKey,
        cooldownSeconds: topEvent ? topEvent.cooldown_seconds : 0,
        persistentDedupe: topEvent ? topEvent.persistent_dedupe : false,
        channel: topEvent ? topEvent.channel : "vision",
        replacePending: topEvent ? topEvent.replace_pending : true,
        interrupt: topEvent ? topEvent.interrupt : false,
      });
    }
  }

  function handleRouteSearchResult(payload) {
    const candidates = Array.isArray(payload && payload.candidates) ? payload.candidates : [];
    elements.candidateList.innerHTML = "";

    if (!candidates.length) {
      elements.candidatePanel.classList.add("hidden");
      elements.candidateSummary.textContent = "暂无候选";
      log("没有搜索到终点候选。");
      return;
    }

    elements.candidatePanel.classList.remove("hidden");
    elements.candidateSummary.textContent = `共 ${candidates.length} 个候选`;

    candidates.forEach(function (item) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "candidate-item";
      if (state.selectedCandidate && state.selectedCandidate.route_value === item.route_value) {
        button.classList.add("active");
      }
      button.innerHTML =
        `<span class="candidate-title">${item.display_name}</span>` +
        `<span class="candidate-meta">来源：${item.provider || "-"}${typeof item.score === "number" ? ` | 评分：${item.score}` : ""}</span>`;
      button.addEventListener("click", function () {
        state.selectedCandidate = item;
        elements.destinationInput.value = item.display_name;
        handleRouteSearchResult({ candidates: candidates });
      });
      elements.candidateList.appendChild(button);
    });

    log(`收到 ${candidates.length} 个候选终点。`);
  }

  function handleSocketMessage(rawData) {
    let message = null;
    try {
      message = JSON.parse(rawData);
    } catch (error) {
      log(`收到非 JSON 消息：${rawData}`);
      return;
    }

    const payload = message.payload || {};
    if (message.type === "hello.ack") {
      setText(elements.sessionStatus, "会话已准备");
      log(`后端就绪：${payload.backend || "unknown"}`);
      return;
    }
    if (message.type === "frame.accepted") {
      handleFrameAccepted(payload);
      return;
    }
    if (message.type === "frame.dropped") {
      handleFrameDropped(payload);
      return;
    }
    if (message.type === "frame.result") {
      handleFrameResult(payload);
      return;
    }
    if (message.type === "gps.updated") {
      if (payload.location) {
        setText(elements.gpsStatus, "定位正常");
        setText(elements.gpsFix, formatFixText({
          lat: payload.location.lat,
          lon: payload.location.lon,
          accuracy: payload.location.accuracy_m,
        }));
      }
      return;
    }
    if (message.type === "route.search.result") {
      handleRouteSearchResult(payload);
      return;
    }
    if (message.type === "route.set.result") {
      if (payload.route) {
        setText(elements.routeStatus, payload.route.instruction || "暂无导航指令");
      }
      log("路线生成成功。");
      return;
    }
    if (message.type === "route.pending") {
      log(payload.message || "等待 GPS 后再生成路线。");
      return;
    }
    if (message.type === "route.clear.result") {
      setText(elements.routeStatus, "暂无导航指令");
      return;
    }
    if (message.type === "speech.test.result") {
      speakText(payload.text || "这是一条语音测试。");
      return;
    }
    if (message.type === "error") {
      log(`服务端错误：${payload.code || "UNKNOWN"} ${payload.message || ""}`);
      return;
    }
  }

  function buildRoutePayload() {
    const destination = elements.destinationInput.value.trim();
    if (!destination) {
      return null;
    }
    return {
      origin: elements.originInput.value.trim() || "@gps",
      destination: state.selectedCandidate ? state.selectedCandidate.route_value : destination,
      provider: elements.providerSelect.value,
      amap_api_key: elements.amapKeyInput.value.trim(),
      prefer_online: true,
      use_demo_fallback: true,
    };
  }

  async function searchRoute() {
    await connectSocket();
    const destination = elements.destinationInput.value.trim();
    if (!destination) {
      log("请先输入终点。");
      return;
    }
    send("route.search", {
      origin: elements.originInput.value.trim() || "@gps",
      destination: destination,
      provider: elements.providerSelect.value,
      amap_api_key: elements.amapKeyInput.value.trim(),
      limit: 5,
    });
  }

  async function setRoute() {
    await connectSocket();
    const payload = buildRoutePayload();
    if (!payload) {
      log("请先输入终点。");
      return;
    }
    send("route.set", payload);
  }

  function useGpsAsOrigin() {
    if (!state.lastFix) {
      log("当前还没有可用定位。");
      return;
    }
    elements.originInput.value = `${state.lastFix.lat.toFixed(6)}, ${state.lastFix.lon.toFixed(6)}`;
  }

  elements.wsUrl.value = inferDefaultWsUrl();
  setBadge(false);
  setLatencyBadge("等待首帧", "neutral");

  elements.connectBtn.addEventListener("click", function () {
    connectSocket().catch(function (error) {
      log(`连接失败：${error.message}`);
    });
  });

  elements.disconnectBtn.addEventListener("click", function () {
    disconnectSocket();
  });

  elements.speakUnlockBtn.addEventListener("click", function () {
    unlockSpeech().catch(function (error) {
      log(`启用语音失败：${error.message}`);
    });
  });

  elements.testSpeechBtn.addEventListener("click", function () {
    unlockSpeech()
      .then(connectSocket)
      .then(function () {
        send("speech.test", {});
      })
      .catch(function (error) {
        log(`测试语音失败：${error.message}`);
      });
  });

  elements.profileSelect.addEventListener("change", function () {
    applyProfile(elements.profileSelect.value);
  });

  elements.refreshCameraBtn.addEventListener("click", function () {
    refreshCameraOptions().catch(function (error) {
      log(`刷新相机列表失败：${error.message}`);
    });
  });

  elements.startCaptureBtn.addEventListener("click", function () {
    startCapture().catch(function (error) {
      log(`开始采集失败：${error.message}`);
    });
  });

  elements.stopCaptureBtn.addEventListener("click", function () {
    stopCapture();
  });

  elements.providerSelect.addEventListener("change", function () {
    send("config.update", buildSettingsPayload());
  });

  elements.dedupModeSelect.addEventListener("change", function () {
    send("config.update", buildSettingsPayload());
  });

  elements.amapKeyInput.addEventListener("change", function () {
    send("config.update", buildSettingsPayload());
  });

  elements.useGpsOriginBtn.addEventListener("click", useGpsAsOrigin);
  elements.searchRouteBtn.addEventListener("click", function () {
    searchRoute().catch(function (error) {
      log(`搜索候选失败：${error.message}`);
    });
  });
  elements.setRouteBtn.addEventListener("click", function () {
    setRoute().catch(function (error) {
      log(`生成路线失败：${error.message}`);
    });
  });
  elements.clearRouteBtn.addEventListener("click", function () {
    send("route.clear", {});
    elements.candidatePanel.classList.add("hidden");
    elements.routeStatus.textContent = "暂无导航指令";
  });
  elements.nextRouteBtn.addEventListener("click", function () {
    send("route.next", {});
  });
  elements.repeatRouteBtn.addEventListener("click", function () {
    send("route.repeat", {});
  });

  window.addEventListener("resize", resizeOverlayCanvas);
  elements.cameraPreview.addEventListener("loadedmetadata", resizeOverlayCanvas);

  applyProfile(elements.profileSelect.value);
  refreshCameraOptions().catch(function () {
    log("暂时无法读取相机列表，等授权后可重试。");
  });
})();
