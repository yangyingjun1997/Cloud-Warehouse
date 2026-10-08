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
      while (list.querySelectorAll('.scan-result-row').length > 20) {
        list.querySelector('.scan-result-row:last-child').remove();
      }
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

  window.WarehouseScanner = {start, readableError, feedback, createSessionResults};
})();
