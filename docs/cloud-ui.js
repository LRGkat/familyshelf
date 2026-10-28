(() => {
  const dialog = document.createElement('dialog');
  dialog.id = 'cloudLoginDialog';
  dialog.setAttribute('aria-label', 'Steam 扫码登录');
  dialog.innerHTML = `<div class="modal-inner" style="text-align:center"><span class="eyebrow mint">CONNECT YOUR STEAM</span><h2 style="margin:15px 0">扫码，连接你的收藏。</h2><p>使用 Steam 手机应用扫描官方登录二维码。请核对登录地点，确认这是你刚刚发起的云端登录。</p><div style="padding:18px;background:#fff;display:inline-block;border-radius:14px;margin:16px auto;min-width:220px;min-height:220px"><img id="cloudQR" alt="Steam 官方登录二维码" width="220" height="220" style="display:block;image-rendering:pixelated" hidden><p id="cloudQRWait" style="color:#202831;margin-top:70px">正在获取二维码…</p></div><p id="cloudQRStatus" role="status"></p><div class="modal-note" style="text-align:left">FamilyShelf 是独立工具。此扫码会在云端创建本次 Steam 会话，本站无需你的 Steam 密码。令牌和 Key 只用于本次同步，会话最长 3 小时；刷新网页需重新登录。尚无 API Key 时，需自行在 Steam 官方页面申请后手动输入。</div><div class="modal-actions"><button class="btn ghost" id="cloudQRHide">暂时收起</button><button class="btn" id="cloudQRCancel">取消登录</button></div></div>`;
  document.body.append(dialog);
  let objectURL = '', loading = false, wasRunning = false, offlineSince = 0, lastQR = 0;
  document.querySelector('#cloudQRHide').onclick = () => dialog.close();
  document.querySelector('#cloudQRCancel').onclick = async () => {
    await api('/api/cancel'); await api('/api/friends/login/cancel'); dialog.close();
  };
  function connection(online) {
    document.querySelector('#authNote').textContent = '用 Steam 手机应用扫码登录。令牌、API Key 和游戏库只保留在本次云端会话中，按访客隔离；刷新网页需重新登录。';
    if (!online) {
      document.querySelector('#connectionHint').textContent = '正在连接在线服务；首次启动可能需要约一分钟';
      document.querySelector('#topStatus').textContent = '连接在线服务…';
    }
  }
  function clearQR() {
    if (objectURL) URL.revokeObjectURL(objectURL);
    objectURL = '';
    const image = document.querySelector('#cloudQR'); image.hidden = true; image.removeAttribute('src');
    document.querySelector('#cloudQRWait').hidden = false;
  }
  async function update(state) {
    offlineSince = 0;
    const running = state.session?.phase === 'login' || state.friends?.auth?.running;
    const ready = state.cloud?.qr_ready;
    document.querySelector('#cloudQRStatus').textContent = state.friends?.auth?.running ? state.friends.auth.message : state.session.message;
    if (ready && !wasRunning && !dialog.open) dialog.showModal();
    wasRunning = !!ready;
    if (!running || !ready) {
      clearQR();
      if (!running && dialog.open) dialog.close();
      return;
    }
    if (loading || Date.now() - lastQR < 1800) return;
    loading = true; lastQR = Date.now();
    try {
      const response = await fetch('/api/auth/qr');
      if (response.ok && response.status !== 204) {
        const blob = await response.blob();
        if (blob.type !== 'image/png') return;
        if (objectURL) URL.revokeObjectURL(objectURL);
        objectURL = URL.createObjectURL(blob);
        const image = document.querySelector('#cloudQR'); image.src = objectURL; image.hidden = false;
        document.querySelector('#cloudQRWait').hidden = true;
      }
    } catch { /* Retry with the next state poll. */ }
    finally {loading = false;}
  }
  function offline() {
    connection(false);
    if (!offlineSince) offlineSince = Date.now();
    if (Date.now() - offlineSince > 90000) {
      document.querySelector('#connectionHint').textContent = '服务暂不可用或会话已过期，请刷新网页重试';
      document.querySelector('#topStatus').textContent = '暂未连接';
    }
    clearQR(); if (dialog.open) dialog.close();
  }
  window.cloudUI = {connection, update, offline};
  connection(false);
})();
