(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const root = $('#recentSpace');
  let query = '', filter = 'all', includeSelf = true, page = 1, account = '';
  const opened = new Set(), limits = new Map();
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const number = value => Number(value).toLocaleString('zh-CN', {maximumFractionDigits:1});
  const hours = minutes => number(minutes / 60);
  const duration = minutes => minutes > 0 && minutes < 60 ? `${minutes} 分钟` : `${hours(minutes)} 小时`;
  const date = value => value ? new Date(value).toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '尚未同步';
  function people() {
    const hub = window.friendHub;
    return hub.debug().people.map(p => {
      const games = hub.isDemo ? p.games.filter(g => g.playtime_2weeks > 0) : (p.recent_games || []);
      const state = hub.isDemo ? p.status : (p.recent_status || 'pending');
      const active = games.filter(g => g.playtime_2weeks > 0).sort((a,b) => b.playtime_2weeks - a.playtime_2weeks || a.appid-b.appid);
      return {...p, activity:active, recentState:state, minutes:active.reduce((sum,g) => sum + g.playtime_2weeks, 0)};
    });
  }
  function ranked() {
    return people().filter(p => (includeSelf || !p.is_self) && `${p.name} ${p.steamid}`.toLowerCase().includes(query) &&
      (filter === 'all' || (filter === 'active' && p.recentState === 'ok' && p.minutes > 0) ||
       (filter === 'idle' && p.recentState === 'ok' && p.minutes === 0) || (filter === 'unavailable' && p.recentState !== 'ok')))
      .sort((a,b) => Number(b.recentState === 'ok') - Number(a.recentState === 'ok') ||
        (a.recentState === 'ok' && b.recentState === 'ok' ? b.minutes - a.minutes : 0) || a.name.localeCompare(b.name,'zh-CN') || a.steamid.localeCompare(b.steamid));
  }
  function avatar(p) {
    // Reuse the same validated Steam avatar and initials as the collection board.
    const url = typeof p.avatarfull === 'string' && /^https:\/\/avatars\.(?:steamstatic\.com|akamai\.steamstatic\.com)\//.test(p.avatarfull) ? p.avatarfull : '';
    return `<span class="avatar" style="--member-color:${p.color}">${escape(p.name.slice(0,1))}${url ? `<img src="${escape(url)}" alt="" loading="lazy" onerror="this.remove()">` : ''}</span>`;
  }
  function stats() {
    const list = people().filter(p => includeSelf || !p.is_self), fresh = list.filter(p => p.recentState === 'ok'), active = fresh.filter(p => p.minutes > 0);
    const total = fresh.reduce((s,p) => s+p.minutes,0), games = new Set(active.flatMap(p => p.activity.map(g => g.appid)));
    $('#rpStats').innerHTML = [
      ['近两周游戏时光', `${hours(total)}<span class="unit">小时</span>`, '仅统计本次成功读取的好友', 'clock', ''],
      ['正在冒险的朋友', `${active.length}<span class="unit">/ ${fresh.length} 人</span>`, `${list.length-fresh.length} 位不可读 / 旧快照 / 待同步`, 'users', 'purple'],
      ['最近探索的世界', `${games.size}<span class="unit">款</span>`, '按 App ID 去重 · 包含借用游戏', 'game', 'blue'],
      ['活跃好友人均', `${hours(active.length ? total/active.length : 0)}<span class="unit">小时</span>`, '有游玩记录好友的平均时长', 'heart', 'amber']
    ].map(([label,value,note,icon,color]) => `<article class="stat"><div class="stat-top"><span>${label}</span><span class="stat-icon ${color}"><svg class="icon"><use href="#i-${icon}"/></svg></span></div><div class="stat-value num">${value}</div><div class="stat-bottom">${note}</div></article>`).join('');
  }
  function gamesHTML(p) {
    const fresh = p.recentState === 'ok', stale = p.recentState === 'stale';
    if (!fresh && !stale) return `<div class="rp-detail-empty">${escape(p.recent_error || (p.recentState === 'pending' ? '等待同步最近游玩数据。登录后会自动同步，也可点击右上角同步按钮。' : p.error || '最近游玩详情不可见，请检查 Steam 游戏详情隐私设置或重试同步。'))}</div>`;
    const limit = limits.get(p.steamid) || 8;
    return `<div class="rp-detail-heading"><div><strong>${escape(p.name)} · 近两周在玩</strong><p>${stale ? '旧快照 · 本次读取失败，以下记录不参与当前排名。' : '按近两周游玩时长从高到低排列。'}${escape(p.recent_error || '')}</p></div><span class="pill">${p.activity.length} 款游戏</span></div>` +
      (p.activity.length ? `<div class="rp-games">${p.activity.slice(0,limit).map(g => `<a class="rp-game" href="https://store.steampowered.com/app/${g.appid}/" target="_blank" rel="noopener noreferrer"><div class="rp-cover"><span>${escape(g.name)}</span><img src="https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/${g.appid}/header.jpg" alt="${escape(g.name)} 游戏封面" loading="lazy" onerror="this.style.visibility='hidden'"><b>近两周 ${duration(g.playtime_2weeks)}</b></div><div class="rp-game-info"><strong>${escape(g.name)}</strong><small>累计 ${hours(g.playtime_forever)} 小时 <span>Steam ↗</span></small><div class="bar-track"><span style="width:${p.minutes ? g.playtime_2weeks/p.minutes*100 : 0}%;background:var(--mint)"></span></div><small>占该好友近两周记录 ${number(p.minutes ? g.playtime_2weeks/p.minutes*100 : 0)}%</small></div></a>`).join('')}</div>${p.activity.length > limit ? `<button class="btn ghost small rp-more" data-rp-more="${escape(p.steamid)}">再看 8 款游戏 ↓</button>` : ''}` : '<div class="rp-detail-empty">Steam 返回的近两周游玩时长为 0，暂无游戏记录。</div>') +
      `<p class="rp-snapshot">${stale ? '上次成功同步' : '同步时间'}：${date(p.recent_fetched_at || (window.friendHub.isDemo ? p.fetched_at : null))}</p>`;
  }
  function renderRows() {
    const all = ranked(), fresh = all.filter(p => p.recentState === 'ok'), max = Math.max(1,Math.ceil(all.length/12));
    page = Math.min(page,max);
    const maximum = fresh[0]?.minutes || 1;
    $('#rpRows').innerHTML = all.slice((page-1)*12,page*12).map(p => {
      const index = fresh.findIndex(x => x.steamid === p.steamid), good = p.recentState === 'ok', stale = p.recentState === 'stale';
      const label = good ? (p.minutes ? `${p.activity.length} 款在玩 · ${escape(p.activity[0].name)}` : '近两周无游玩记录') : ({stale:'旧快照 · 不参榜',pending:'等待同步',unavailable:'最近游玩不可读取'}[p.recentState] || '最近游玩不可读取');
      return `<details class="rp-person ${index === 0 ? 'rp-leading' : ''}" data-rp-id="${escape(p.steamid)}" ${opened.has(p.steamid) ? 'open' : ''}><summary><span class="fh-rank-medal award-${index+1}">${index < 0 ? '—' : String(index+1).padStart(2,'0')}</span><span class="rp-player">${avatar(p)}<span><strong>${escape(p.name)}${p.is_self ? '<span class="fh-badge">我</span>' : ''}</strong><small class="${good ? '' : 'amber'}">${label}</small></span></span><span class="rp-time"><b>${good ? hours(p.minutes) : '—'}</b><small>${good ? '小时 / 近两周' : stale ? '保留上次快照' : '不参与排名'}</small><span class="bar-track"><span style="width:${good ? p.minutes/maximum*100 : 0}%;background:var(--mint)"></span></span></span><span class="rp-expand"><span class="rp-show">查看详情</span><span class="rp-hide">收起详情</span><svg class="icon"><use href="#i-arrow"/></svg></span></summary><div class="rp-details">${opened.has(p.steamid) ? gamesHTML(p) : ''}</div></details>`;
    }).join('') || `<div class="rp-empty"><svg class="icon"><use href="#i-clock"/></svg><h3>${people().length ? '没有符合条件的好友' : '把朋友们的游戏足迹带进来'}</h3><p>${people().length ? '试试调整搜索关键词或活跃筛选。' : '连接一次 Steam，自动同步好友游戏库和近两周游玩榜单。'}</p>${people().length ? '' : '<button class="btn primary" data-rp-action="connect">连接 Steam</button><button class="btn ghost" data-rp-action="demo">体验示例</button>'}</div>`;
    $('#rpSummary').textContent = `${all.length} 位好友 · ${fresh.length} 位可排名`;
    $('#rpPage').textContent = `${page} / ${max}`;
    $('#rpPrev').disabled = page <= 1; $('#rpNext').disabled = page >= max;
  }
  function render() {
    const hub = window.friendHub, {job = {}, configured, message} = hub.syncState;
    const nextAccount = hub.isDemo ? 'demo' : hub.debug().people.find(p => p.is_self)?.steamid || '';
    if (nextAccount !== account) { account = nextAccount; opened.clear(); limits.clear(); page = 1; }
    const list = people();
    $('#rpNotice').firstElementChild.textContent = hub.isDemo ? '示例模式：好友、时长和游戏记录均为演示数据。' : message || job.error || (job.running ? job.message : list.length ? '与好友收藏共用本次登录和同步；最近游玩数据独立标记可见性。' : '登录 Steam 后自动加载好友近两周游玩榜单，无需重复登录。');
    $('#rpNotice').classList.toggle('failed', !hub.isDemo && !!job.error);
    $('#rpNotice').classList.toggle('demo', hub.isDemo);
    $('#rpConnect').textContent = configured ? '刷新好友数据 ↻' : '连接 Steam';
    $('#rpConnect').disabled = hub.running;
    $('#rpSource').textContent = hub.isDemo ? '示例数据' : 'Steam 最近游玩记录';
    $('#rpDemo').textContent = hub.isDemo ? '返回真实数据 ↗' : '体验示例 ↗';
    stats(); renderRows();
  }
  root.addEventListener('toggle', e => {
    const row = e.target;
    if (!row.matches('details[data-rp-id]') || !row.isConnected) return;
    const id = row.dataset.rpId;
    if (row.open) {
      opened.add(id);
      const p = people().find(p => p.steamid === id);
      if (p && !row.querySelector('.rp-details').hasChildNodes()) row.querySelector('.rp-details').innerHTML = gamesHTML(p);
    } else opened.delete(id);
  }, true);
  root.addEventListener('click', e => {
    const more = e.target.closest('[data-rp-more]');
    if (more) {
      const id = more.dataset.rpMore;
      limits.set(id, (limits.get(id) || 8) + 8);
      more.closest('.rp-details').innerHTML = gamesHTML(people().find(p => p.steamid === id));
    }
    const action = e.target.closest('[data-rp-action]')?.dataset.rpAction;
    if (action === 'connect') window.friendHub.refresh();
    if (action === 'demo') window.friendHub.toggleDemo();
  });
  $('#rpConnect').onclick = () => window.friendHub.refresh();
  $('#rpDemo').onclick = () => window.friendHub.toggleDemo();
  $('#rpSearch').oninput = e => {query = e.target.value.trim().toLowerCase(); page = 1; renderRows();};
  $('#rpFilter').onchange = e => {filter = e.target.value; page = 1; renderRows();};
  $('#rpSelf').onclick = () => {
    includeSelf = !includeSelf; page = 1;
    $('#rpSelf').textContent = includeSelf ? '包含自己 ✓' : '已排除自己';
    $('#rpSelf').setAttribute('aria-pressed', String(!includeSelf)); stats(); renderRows();
  };
  $('#rpPrev').onclick = () => {page--; renderRows();}; $('#rpNext').onclick = () => {page++; renderRows();};
  function exportRank() {
    const cell = value => '"' + String(value ?? '').replaceAll('"','""') + '"';
    const rows = [['排名','好友','SteamID','记录状态','近两周分钟数','近两周游戏数','快照时间'], ...ranked().map((p,i) => [p.recentState === 'ok' ? i+1 : '', /^[=+\-@\t\r]/.test(p.name) ? "'"+p.name : p.name, "'"+p.steamid, p.recentState, p.recentState === 'ok' ? p.minutes : '', p.recentState === 'ok' ? p.activity.length : '', p.recent_fetched_at || ''])];
    const url = URL.createObjectURL(new Blob(['\ufeff'+rows.map(r => r.map(cell).join(',')).join('\r\n')], {type:'text/csv;charset=utf-8'}));
    const a = document.createElement('a'); a.href = url; a.download = (window.friendHub.isDemo ? '示例_' : '')+'好友近两周游玩榜.csv'; a.click(); setTimeout(() => URL.revokeObjectURL(url),5000);
  }
  window.recentUI = {render, export:exportRank, debug:() => ({ranked:ranked(),opened:[...opened]})};
  render();
})();
