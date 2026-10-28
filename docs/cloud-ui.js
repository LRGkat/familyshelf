(() => {
  const dialog = document.createElement('dialog');
  dialog.id = 'cloudLoginDialog';
  dialog.setAttribute('aria-label', 'Steam 扫码登录');
  dialog.innerHTML = `<div class="modal-inner" style="text-align:center"><span class="eyebrow mint">CONNECT YOUR STEAM</span><h2 style="margin:15px 0">扫码，连接你的收藏。</h2><p>使用 Steam 手机应用扫描官方登录二维码。请核对登录地点，确认这是你刚刚发起的云端登录。</p><div style="padding:18px;background:#fff;display:inline-block;border-radius:14px;margin:16px auto;min-width:220px;min-height:220px"><img id="cloudQR" alt="Steam 官方登录二维码" width="220" height="220" style="display:block;image-rendering:pixelated" hidden><p id="cloudQRWait" style="color:#202831;margin-top:70px">正在获取二维码…</p></div><p id="cloudQRStatus" role="status"></p><div class="modal-note" style="text-align:left">FamilyShelf 是独立工具。此扫码会在云端创建本次 Steam 会话，本站无需你的 Steam 密码。令牌和 Key 只用于本次同步，会话最长 3 小时；刷新网页需重新登录。家庭和好友共用本次登录；如好友接口权限不足，可补充 API Key，无需重复登录。SteamDB 成员自动同步，价格需录入或导入。</div><div class="modal-actions"><button class="btn ghost" id="cloudQRHide">暂时收起</button><button class="btn" id="cloudQRCancel">取消登录</button></div></div>`;
  document.body.append(dialog);
  const auth = document.querySelector('#authDialog');
  const input = document.querySelector('#steamTokenInput');
  const tokenMessage = document.querySelector('#tokenLoginMessage');
  const submit = document.querySelector('#tokenLoginBtn');
  const reveal = document.querySelector('#tokenReveal');
  let method = 'qr', submitting = false;
  function clearToken() {
    input.value = ''; input.type = 'password';
    reveal.textContent = '显示'; reveal.setAttribute('aria-label', '显示令牌'); reveal.setAttribute('aria-pressed', 'false');
  }
  function setMethod(next) {
    method = next;
    for (const name of ['QR', 'Token']) {
      const active = next === name.toLowerCase();
      const tab = document.querySelector(`#auth${name}Tab`);
      tab.setAttribute('aria-selected', String(active)); tab.tabIndex = active ? 0 : -1;
      document.querySelector(`#auth${name}Panel`).hidden = !active;
    }
    tokenMessage.hidden = true;
    if (next === 'qr') clearToken();
  }
  for (const [id, next] of [['authQRTab', 'qr'], ['authTokenTab', 'token']]) {
    const tab = document.getElementById(id);
    tab.onclick = () => setMethod(next);
    tab.onkeydown = event => {
      if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
        event.preventDefault();
        const target = event.key === 'Home' ? 'qr' : event.key === 'End' ? 'token' : method === 'qr' ? 'token' : 'qr';
        setMethod(target); document.querySelector(target === 'qr' ? '#authQRTab' : '#authTokenTab').focus();
      }
    };
  }
  reveal.onclick = () => {
    const visible = input.type === 'password'; input.type = visible ? 'text' : 'password';
    reveal.textContent = visible ? '隐藏' : '显示'; reveal.setAttribute('aria-label', visible ? '隐藏令牌' : '显示令牌'); reveal.setAttribute('aria-pressed', String(visible));
  };
  auth.addEventListener('close', clearToken);
  document.querySelector('#tokenLoginForm').onsubmit = async event => {
    event.preventDefault();
    if (submitting || !backend) return;
    submitting = true; submit.disabled = true; tokenMessage.hidden = true;
    try {
      const pending = fetch('/api/login/token', {method:'POST', headers:{'Content-Type':'application/json', 'X-FamilyShelf-CSRF':csrf}, body:JSON.stringify({token:input.value})});
      clearToken();
      const response = await pending, result = await response.json();
      if (!response.ok) {
        tokenMessage.textContent = result.message || result.error || '登录未完成，请重试。'; tokenMessage.hidden = false;
        input.focus();
      } else { toast(result.message); await poll(); }
    } catch {
      tokenMessage.textContent = '在线服务暂不可用，请稍后重试。'; tokenMessage.hidden = false;
    } finally { submitting = false; connection(backend); }
  };
  const switchToken = document.createElement('button');
  switchToken.id = 'cloudQRToken'; switchToken.className = 'text-link'; switchToken.textContent = '无法扫码？改用令牌登录 →';
  dialog.querySelector('.modal-inner').append(switchToken);
  switchToken.onclick = () => { dialog.close(); setMethod('token'); openAuth(); input.focus(); };
  let objectURL = '', loading = false, wasRunning = false, offlineSince = 0, lastQR = 0;
  document.querySelector('#cloudQRHide').onclick = () => dialog.close();
  document.querySelector('#cloudQRCancel').onclick = async () => {
    await api('/api/cancel'); await api('/api/friends/login/cancel'); dialog.close();
  };
  function connection(online) {
    document.querySelector('#authNote').textContent = '支持扫码或令牌登录。令牌、API Key 和游戏库仅在本次云端会话内存中使用；刷新网页需重新连接。';
    submit.disabled = !online || submitting || session.phase === 'refresh';
    submit.querySelector('span').textContent = submitting || session.phase === 'refresh' ? '正在验证与同步…' : !online ? '等待在线服务连接…' : '使用令牌同步';
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
    if (ready && !wasRunning && !dialog.open && !(auth.open && method === 'token')) dialog.showModal();
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
