(function () {
  function scannerError(message, code) {
    const error = new Error(message);
    error.code = code;
    return error;
  }

  function readableError(error) {
    if (error?.code === 'insecure_context') {
      return '摄像头扫码需要使用 HTTPS 地址；本机调试可使用 localhost。';
    }
    if (error?.code === 'camera_unsupported') {
      return '当前浏览器无法调用摄像头，请升级浏览器或使用扫码枪。';
    }
    if (error?.code === 'decoder_unavailable') {
      return '扫码识别组件加载失败，请刷新页面后重试。';
    }
    if (error?.name === 'NotAllowedError' || error?.name === 'SecurityError') {
      return '摄像头权限未开启，请在浏览器设置中允许当前网站使用摄像头。';
    }
    if (error?.name === 'NotFoundError' || error?.name === 'DevicesNotFoundError') {
      return '未检测到可用摄像头，请连接摄像头或使用扫码枪。';
    }
    if (error?.name === 'NotReadableError' || error?.name === 'TrackStartError') {
      return '摄像头正被其他程序占用，请关闭占用程序后重试。';
    }
    if (error?.code === 'torch_unsupported') {
      return '当前摄像头不支持网页控制手电筒。';
    }
    return '摄像头启动失败，请刷新页面或检查浏览器摄像头权限。';
  }

  let audioContext = null;

  async function feedback() {
    if (navigator.vibrate) navigator.vibrate(80);
    try {
      const AudioContext = window.AudioContext || window.webkitAudioContext;
      if (!AudioContext) return;
      audioContext = audioContext || new AudioContext();
      if (audioContext.state === 'suspended') await audioContext.resume();
      const oscillator = audioContext.createOscillator();
      const gain = audioContext.createGain();
      oscillator.frequency.value = 880;
      gain.gain.setValueAtTime(0.06, audioContext.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, audioContext.currentTime + 0.1);
      oscillator.connect(gain);
      gain.connect(audioContext.destination);
      oscillator.start();
      oscillator.stop(audioContext.currentTime + 0.1);
    } catch (error) {
      // Sound is optional; scanning should continue when the browser blocks audio.
    }
  }

  function createSessionResults(options) {
    const list = options.list;
    const counter = options.counter;
    const empty = options.empty;
    let addedCount = 0;

    function updateCounter() {
      counter.textContent = `已加入 ${addedCount} 项`;
    }

    function reset() {
      addedCount = 0;
      list.replaceChildren(empty);
      empty.hidden = false;
      updateCounter();
    }

    function record(value, outcome) {
      empty.hidden = true;
      if (outcome.state === 'added' || outcome.state === 'invalid') addedCount += 1;
      const row = document.createElement('div');
      row.className = `scan-result-row is-${outcome.state}`;
      const content = document.createElement('span');
      const title = document.createElement('strong');
      const detail = document.createElement('small');
      title.textContent = outcome.item?.name || value;
      detail.textContent = outcome.item?.code
        ? `${outcome.item.code} · ${outcome.message}`
        : outcome.message;
      content.append(title, detail);
      const status = document.createElement('b');
      status.textContent = outcome.state === 'added'
        ? '已加入'
        : outcome.state === 'invalid' ? '待修正'
          : outcome.state === 'existing' ? '已存在' : '未加入';
      row.append(content, status);
      list.prepend(row);
      const rows = Array.from(list.querySelectorAll('.scan-result-row'));
      rows.slice(20).forEach(extra => extra.remove());
      updateCounter();
    }

    reset();
    return {record, reset, getAddedCount: () => addedCount};
  }

  async function start(options) {
    const video = options.video;
    const onResult = options.onResult;
    const onStatus = options.onStatus || function () {};
    const facingMode = options.facingMode || 'environment';
    if (!window.isSecureContext) {
      throw scannerError('', 'insecure_context');
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      throw scannerError('', 'camera_unsupported');
    }

    let active = true;
    let stream = null;
    let frameId = null;
    let zxingControls = null;
    let detecting = false;
    let torchSupported = false;
    let setTorchValue = null;

    function stop() {
      active = false;
      if (frameId !== null) window.cancelAnimationFrame(frameId);
      if (zxingControls?.stop) zxingControls.stop();
      if (stream) stream.getTracks().forEach((track) => track.stop());
      stream = null;
      video.pause();
      video.srcObject = null;
    }

    async function setTorch(enabled) {
      if (!torchSupported || !setTorchValue) throw scannerError('', 'torch_unsupported');
      await setTorchValue(Boolean(enabled));
    }

    function controller(engine) {
      return {stop, setTorch, torchSupported, engine};
    }

    let detector = null;
    if ('BarcodeDetector' in window) {
      try {
        const wantedFormats = ['qr_code', 'code_128', 'code_39', 'ean_13', 'ean_8'];
        const supportedFormats = typeof BarcodeDetector.getSupportedFormats === 'function'
          ? await BarcodeDetector.getSupportedFormats()
          : wantedFormats;
        const formats = wantedFormats.filter((format) => supportedFormats.includes(format));
        if (formats.length) detector = new BarcodeDetector({formats});
      } catch (error) {
        detector = null;
      }
    }

    if (detector) {
      stream = await navigator.mediaDevices.getUserMedia({
        video: {facingMode: {ideal: facingMode}},
        audio: false,
      });
      const videoTrack = stream.getVideoTracks()[0];
      torchSupported = Boolean(videoTrack?.getCapabilities?.().torch);
      if (torchSupported) {
        setTorchValue = (enabled) => videoTrack.applyConstraints({advanced: [{torch: enabled}]});
      }
      video.srcObject = stream;
      await video.play();
      onStatus('扫码中，识别后会自动加入');

      async function detectFrame() {
        if (!active) return;
        if (!detecting && video.readyState >= 2) {
          detecting = true;
          try {
            const codes = await detector.detect(video);
            const value = codes[0]?.rawValue?.trim();
            if (value) await onResult(value);
          } catch (error) {
            onStatus('识别中，请将二维码或条码放入画面中央');
          } finally {
            detecting = false;
          }
        }
        if (active) frameId = window.requestAnimationFrame(detectFrame);
      }
      frameId = window.requestAnimationFrame(detectFrame);
      return controller('native');
    }

    if (!window.ZXingBrowser?.BrowserMultiFormatReader) {
      throw scannerError('', 'decoder_unavailable');
    }
    const reader = new ZXingBrowser.BrowserMultiFormatReader();
    zxingControls = await reader.decodeFromConstraints({
      video: {facingMode: {ideal: facingMode}},
      audio: false,
    }, video, async (result) => {
      const value = result?.getText?.().trim();
      if (active && value) await onResult(value);
    });
    torchSupported = typeof zxingControls?.switchTorch === 'function';
    if (torchSupported) setTorchValue = (enabled) => zxingControls.switchTorch(enabled);
    onStatus('扫码中，识别后会自动加入');
    return controller('zxing');
  }

  // ------------------------------------------------------------------
  // 全局扫码捕获
  //
  // 扫码枪是键盘模拟设备，会被当成"极快的人手打字"。
  // 靠输入节奏区分：扫码枪相邻字符间隔通常 5~30ms，
  // 人手最快也要 60ms 以上，中文输入法更慢。
  //
  // 设计要点：
  // 1. 不抢已有输入框：业务页由输入框自己的逻辑处理扫码结果。
  // 2. 页面空闲时按输入节奏捕获，并在 Enter/Tab 后缀到达时查询。
  // 3. 不限制固定编码前缀，兼容生产厂家自定义 SN 和条码。
  // ------------------------------------------------------------------

  const SCAN_MAX_INTERVAL_MS = 45;    // 相邻字符最大间隔，超过即视为人工
  const SCAN_MIN_LENGTH = 4;          // 兼容厂家较短的 SN / 条码
  const SCAN_RESET_MS = 120;          // 停顿超过此值清空缓冲

  function looksLikeScan(value) {
    const normalized = String(value || '').trim();
    return normalized.length >= SCAN_MIN_LENGTH
      && normalized.length <= 128
      && !/[\r\n\t]/.test(normalized);
  }

  /**
   * 启动全局扫码监听。
   * @param {object} options
   * @param {(value: string) => any} options.onScan 收到扫码结果时的回调
   * @param {(value: string, detail: object) => any} [options.onDetected] 验证到高速扫码输入时的回调
   * @param {() => boolean} [options.shouldCapture] 返回 false 可临时关闭捕获
   * @returns {() => void} 停止函数
   */
  function watchGlobalScan(options) {
    const onScan = options.onScan;
    const onDetected = options.onDetected || function () {};
    const shouldCapture = options.shouldCapture || (() => true);
    const captureEditable = options.captureEditable || (() => false);
    let buffer = '';
    let lastTime = 0;
    let maxGap = 0;

    function isEditable(el) {
      if (!el) return false;
      const tag = el.tagName;
      return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
    }

    function handleKeyDown(event) {
      if (!shouldCapture()) return;
      // 组合键是人工操作，不参与判定
      if (event.ctrlKey || event.altKey || event.metaKey) {
        buffer = '';
        lastTime = 0;
        maxGap = 0;
        return;
      }

      const now = Date.now();
      const gap = lastTime ? now - lastTime : 0;
      if (gap > SCAN_RESET_MS) buffer = '';

      if (event.key === 'Enter' || event.key === 'Tab') {
        const value = buffer.trim();
        buffer = '';
        lastTime = 0;
        const detectedGap = maxGap;
        maxGap = 0;
        if (value.length >= SCAN_MIN_LENGTH && looksLikeScan(value)) {
          onDetected(value, {maxIntervalMs: detectedGap});
          // 普通输入框保留原有行为；物品扫码输入框则强制走统一扫码事件。
          // 否则第一次扫码后焦点落入搜索框，后续条码会退化为普通搜索并刷新页面。
          if (isEditable(event.target) && !captureEditable(event.target)) return;
          // 扫码枪提交：在捕获阶段拦截，避免输入框和表单再次处理同一个 Enter/Tab。
          event.preventDefault();
          event.stopImmediatePropagation();
          onScan(value);
          return;
        }
        return;
      }

      // 只收集可见单字符（忽略 Shift/箭头/F1 等）
      if (event.key.length !== 1) return;

      // 节奏判定：间隔过大说明是人在打字，重新开始
      if (buffer && gap > SCAN_MAX_INTERVAL_MS) {
        buffer = '';
        maxGap = 0;
      }
      if (buffer && gap) maxGap = Math.max(maxGap, gap);
      buffer += event.key;
      lastTime = now;
      if (buffer.length > 128) buffer = buffer.slice(-128);
    }

    // 用 capture 阶段，保证比页面自己的 handler 更早看到按键
    document.addEventListener('keydown', handleKeyDown, true);
    return function stop() {
      document.removeEventListener('keydown', handleKeyDown, true);
    };
  }

  window.WarehouseScanner = {start, readableError, feedback, createSessionResults, watchGlobalScan, looksLikeScan};
})();
