(() => {
  'use strict';
  const $ = selector => document.querySelector(selector), root = $('#achievementsSpace');
  let query = '', filter = 'all', includeSelf = true, page = 1, account = '';
  const opened = new Set(), limits = new Map(), modes = new Map();
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num = value => Number(value).toLocaleString('zh-CN', {maximumFractionDigits:1});
  const date = value => value ? new Date(value).toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '尚未同步';
  const stateLabel = p => ({ok:'完整读取',partial:'部分记录缺失 · 不参榜',stale:'旧快照 · 不参榜',pending:'等待成就同步',unavailable:'成就不可读取'}[p.achievementState] || '成就不可读取');
  function people() {
    const hub = window.friendHub;
    return hub.debug().people.map((p,index) => {
      // Demo progress is deliberately synthetic and only used in the labeled demo mode.
      const games = hub.isDemo ? p.games.map((g,i) => {
        const total = [42,30,50,20,12,100][i%6], complete = (i+index)%4 !== 0;
        return {...g,total,unlocked:complete ? total : Math.max(0,total-3-i%5),complete};
      }) : p.achievement_games || [];
      const valid = games.filter(g => g.total > 0), complete = valid.filter(g => g.unlocked === g.total);
      return {...p, achievementState:hub.isDemo ? p.status : p.achievement_status || 'pending',
        achievementGames:valid, completed:complete.sort((a,b) => b.total-a.total || a.appid-b.appid),
        completeCount:complete.length, unlocked:valid.reduce((s,g) => s+g.unlocked,0),
        checked:hub.isDemo ? games.length : p.achievement_checked || 0,
        scope:hub.isDemo ? games.length : p.achievement_scope || 0};
    });
  }
  function ranked() {
    return people().filter(p => (includeSelf || !p.is_self) && `${p.name} ${p.steamid}`.toLowerCase().includes(query) &&
      (filter === 'all' || filter === 'complete' && p.achievementState === 'ok' && p.completeCount > 0 ||
       filter === 'zero' && p.achievementState === 'ok' && p.completeCount === 0 ||
       filter === 'unavailable' && p.achievementState !== 'ok'))
      .sort((a,b) => Number(b.achievementState === 'ok') - Number(a.achievementState === 'ok') || b.completeCount-a.completeCount ||
        a.name.localeCompare(b.name,'zh-CN') || a.steamid.localeCompare(b.steamid));
  }
  function avatar(p) {
    const url = typeof p.avatarfull === 'string' && /^https:\/\/avatars\.(?:steamstatic\.com|akamai\.steamstatic\.com)\//.test(p.avatarfull) ? p.avatarfull : '';
    return `<span class="avatar" style="--member-color:${p.color}">${esc(p.name.slice(0,1))}${url ? `<img src="${esc(url)}" alt="" loading="lazy" onerror="this.remove()">` : ''}</span>`;
  }
  function stats() {
    const list = people().filter(p => includeSelf || !p.is_self), fresh = list.filter(p => p.achievementState === 'ok');
    const count = fresh.reduce((s,p) => s+p.completeCount,0), unique = new Set(fresh.flatMap(p => p.completed.map(g => g.appid)));
    $('#acStats').innerHTML = [
      ['已确认全成就收藏', `${num(count)}<span class="unit">款次</span>`, '仅汇总完整读取成员 · 同款可重复', 'award', 'amber'],
      ['圆满的游戏世界', `${num(unique.size)}<span class="unit">款</span>`, '全成就游戏按 App ID 去重', 'game', 'purple'],
      ['达成全成就的好友', `${fresh.filter(p => p.completeCount > 0).length}<span class="unit">/ ${fresh.length} 人</span>`, `${list.length-fresh.length} 位未完整读取 / 待同步`, 'users', 'blue'],
      ['已解锁成就', `${num(fresh.reduce((s,p) => s+p.unlocked,0))}<span class="unit">枚</span>`, '完整读取成员 · 包括尚未全成就游戏', 'diamond', '']
    ].map(([label,value,note,icon,color]) => `<article class="stat"><div class="stat-top"><span>${label}</span><span class="stat-icon ${color}"><svg class="icon"><use href="#i-${icon}"/></svg></span></div><div class="stat-value num">${value}</div><div class="stat-bottom">${note}</div></article>`).join('');
  }
  function gamesHTML(p) {
    const state = p.achievementState, available = ['ok','partial','stale'].includes(state);
    if (!available) return `<div class="rp-detail-empty">${esc(p.achievement_error || (state === 'pending' ? '登录后会在游戏库同步与估值后自动统计成就，可继续浏览其他榜单。' : 'Steam 未授权读取成就，请检查游戏详情隐私设置或重试。'))}</div>`;
    const mode = modes.get(p.steamid) || 'complete', limit = limits.get(p.steamid) || 8;
    const list = mode === 'complete' ? p.completed : p.achievementGames.filter(g => g.unlocked < g.total).sort((a,b) => b.unlocked/b.total-a.unlocked/a.total || b.unlocked-a.unlocked || a.appid-b.appid);
    return `<div class="rp-detail-heading"><div><strong>${esc(p.name)} · 成就收藏室</strong><p>${state === 'ok' ? '成就进度已完整返回，按当前解锁数量统计。' : esc(p.achievement_error || '本次数据未完整读取；以下为已确认记录，不参与完整排名。')}</p></div><span class="pill ac-read">已读取 ${p.checked} / ${p.scope} 款</span></div><div class="ac-detail-tools"><button class="chip ${mode === 'complete' ? 'active' : ''}" data-ac-mode="complete" data-ac-person="${esc(p.steamid)}">全成就 ${p.completeCount}</button><button class="chip ${mode === 'progress' ? 'active' : ''}" data-ac-mode="progress" data-ac-person="${esc(p.steamid)}">继续挑战 ${p.achievementGames.length-p.completeCount}</button><span>无成就游戏不计入</span></div>` +
      (list.length ? `<div class="rp-games ac-games">${list.slice(0,limit).map(g => `<a class="rp-game ac-game" href="${window.friendHub.isDemo ? `https://store.steampowered.com/app/${g.appid}/` : `https://steamcommunity.com/profiles/${p.steamid}/stats/${g.appid}/?tab=achievements`}" target="_blank" rel="noopener noreferrer"><div class="rp-cover"><span>${esc(g.name)}</span><img src="https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/${g.appid}/header.jpg" alt="${esc(g.name)} 游戏封面" loading="lazy" onerror="this.style.visibility='hidden'"><b>${g.unlocked === g.total ? '✦ 全成就达成' : `还差 ${g.total-g.unlocked} 枚`}</b></div><div class="rp-game-info"><strong>${esc(g.name)}</strong><div class="ac-progress"><b>${g.unlocked} <small>/ ${g.total} 枚成就</small></b><span>${Math.floor(g.unlocked/g.total*100)}%</span></div><div class="bar-track"><span style="width:${g.unlocked/g.total*100}%;background:var(--amber)"></span></div><small>累计游玩 ${num(g.playtime_forever/60)} 小时 <span>${window.friendHub.isDemo ? 'Steam ↗' : '查看成就 ↗'}</span></small></div></a>`).join('')}</div>${list.length > limit ? `<button class="btn ghost small rp-more" data-ac-more="${esc(p.steamid)}">再看 8 款游戏 ↓</button>` : ''}` : `<div class="rp-detail-empty">${mode === 'complete' ? '当前已读取记录中尚无全成就游戏。' : '当前已读取记录中暂无未完成的成就游戏。'}</div>`) +
      `<p class="rp-snapshot">${state === 'stale' ? '上次成功同步' : '同步时间'}：${date(p.achievement_fetched_at || (window.friendHub.isDemo ? p.fetched_at : null))} · ${Math.max(0,p.checked-p.achievementGames.length)} 款未返回可解锁成就</p>`;
  }
  function renderRows() {
    const list = ranked(), fresh = list.filter(p => p.achievementState === 'ok'), max = Math.max(1,Math.ceil(list.length/12));
    page = Math.min(page,max);
    const maximum = fresh[0]?.completeCount || 1;
    $('#acRows').innerHTML = list.slice((page-1)*12,page*12).map(p => {
      const good = p.achievementState === 'ok', rank = good ? fresh.findIndex(x => x.completeCount === p.completeCount)+1 : 0;
      const label = good ? `${p.achievementGames.length} 款有成就游戏 · 已解锁 ${num(p.unlocked)} 枚` : stateLabel(p);
      return `<details class="rp-person ac-person ${rank === 1 ? 'rp-leading' : ''}" data-ac-id="${esc(p.steamid)}" ${opened.has(p.steamid) ? 'open' : ''}><summary><span class="fh-rank-medal award-${rank}">${rank ? String(rank).padStart(2,'0') : '—'}</span><span class="rp-player">${avatar(p)}<span><strong>${esc(p.name)}${p.is_self ? '<span class="fh-badge">我</span>' : ''}</strong><small class="${good ? '' : 'amber'}">${label}</small></span></span><span class="rp-time"><b>${good ? num(p.completeCount) : p.achievementState === 'partial' ? '≥'+num(p.completeCount) : '—'}</b><small>${good ? '款全成就游戏' : '不参与完整排名'}</small><span class="bar-track"><span style="width:${good ? p.completeCount/maximum*100 : 0}%;background:var(--amber)"></span></span></span><span class="rp-expand"><span class="rp-show">查看详情</span><span class="rp-hide">收起详情</span><svg class="icon"><use href="#i-arrow"/></svg></span></summary><div class="rp-details">${opened.has(p.steamid) ? gamesHTML(p) : ''}</div></details>`;
    }).join('') || `<div class="rp-empty"><svg class="icon"><use href="#i-award"/></svg><h3>${people().length ? '没有符合条件的好友' : '让每一个圆满都被看见'}</h3><p>${people().length ? '试试调整搜索关键词或筛选条件。' : '连接一次 Steam，自动加载好友并统计全成就游戏。'}</p>${people().length ? '' : '<button class="btn primary" data-ac-action="connect">连接 Steam</button><button class="btn ghost" data-ac-action="demo">体验示例</button>'}</div>`;
    $('#acSummary').textContent = `${list.length} 位好友 · ${fresh.length} 位完整读取`;
    $('#acPage').textContent = `${page} / ${max}`;
    $('#acPrev').disabled = page <= 1; $('#acNext').disabled = page >= max;
  }
  function render() {
    const hub = window.friendHub, {job = {}, configured, message} = hub.syncState;
    const next = hub.isDemo ? 'demo' : hub.debug().people.find(p => p.is_self)?.steamid || '';
    if (next !== account) {account = next; opened.clear(); limits.clear(); modes.clear(); page = 1;}
    $('#acNotice').firstElementChild.textContent = hub.isDemo ? '示例模式：好友与成就进度均为虚构演示，不代表真实解锁记录。' : message || job.error || (job.running ? job.message : people().length ? '与其他榜单共用本次登录。只有完整返回成就进度的好友参与排名，展开可查看读取情况。' : '登录 Steam 后自动统计全成就游戏，无需为新榜单重复登录。');
    $('#acNotice').classList.toggle('failed', !hub.isDemo && !!job.error);
    $('#acNotice').classList.toggle('demo', hub.isDemo);
    $('#acConnect').textContent = configured ? '刷新成就与好友 ↻' : '连接 Steam';
    $('#acConnect').disabled = hub.running;
    $('#acSource').textContent = hub.isDemo ? '示例数据' : 'Steam 成就进度';
    $('#acDemo').textContent = hub.isDemo ? '返回真实数据 ↗' : '体验示例 ↗';
    stats(); renderRows();
  }
  root.addEventListener('toggle', e => {
    const row = e.target;
    if (!row.matches('details[data-ac-id]') || !row.isConnected) return;
    const id = row.dataset.acId;
    if (row.open) {opened.add(id); const p = people().find(p => p.steamid === id); if (p && !row.querySelector('.rp-details').hasChildNodes()) row.querySelector('.rp-details').innerHTML = gamesHTML(p);}
    else opened.delete(id);
  }, true);
  root.addEventListener('click', e => {
    const more = e.target.closest('[data-ac-more]'), mode = e.target.closest('[data-ac-mode]');
    if (more || mode) {
      const id = more ? more.dataset.acMore : mode.dataset.acPerson;
      if (more) limits.set(id,(limits.get(id) || 8)+8);
      else {modes.set(id,mode.dataset.acMode); limits.delete(id);}
      (more || mode).closest('.rp-details').innerHTML = gamesHTML(people().find(p => p.steamid === id));
    }
    const action = e.target.closest('[data-ac-action]')?.dataset.acAction;
    if (action === 'connect') window.friendHub.refresh();
    if (action === 'demo') window.friendHub.toggleDemo();
  });
  $('#acConnect').onclick = () => window.friendHub.refresh();
  $('#acDemo').onclick = () => window.friendHub.toggleDemo();
  $('#acSearch').oninput = e => {query = e.target.value.trim().toLowerCase(); page = 1; renderRows();};
  $('#acFilter').onchange = e => {filter = e.target.value; page = 1; renderRows();};
  $('#acSelf').onclick = () => {includeSelf = !includeSelf; page = 1; $('#acSelf').textContent = includeSelf ? '包含自己 ✓' : '已排除自己'; $('#acSelf').setAttribute('aria-pressed',String(!includeSelf)); stats(); renderRows();};
  $('#acPrev').onclick = () => {page--; renderRows();}; $('#acNext').onclick = () => {page++; renderRows();};
  function exportRank() {
    const cell = value => '"'+String(value ?? '').replaceAll('"','""')+'"', list = ranked(), fresh = list.filter(p => p.achievementState === 'ok');
    const rows = [['排名','好友','SteamID','记录状态','已确认全成就游戏数','已解锁成就数','已读取游戏数','统计范围游戏数','快照时间','示例数据'],...list.map(p => [p.achievementState === 'ok' ? fresh.findIndex(x => x.completeCount === p.completeCount)+1 : '', /^[=+\-@\t\r]/.test(p.name) ? "'"+p.name : p.name,"'"+p.steamid,p.achievementState,['ok','partial','stale'].includes(p.achievementState) ? p.completeCount : '',p.unlocked,p.checked,p.scope,p.achievement_fetched_at || '',window.friendHub.isDemo])];
    const url = URL.createObjectURL(new Blob(['\ufeff'+rows.map(r => r.map(cell).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'}));
    const a = document.createElement('a'); a.href = url; a.download = (window.friendHub.isDemo ? '示例_' : '')+'好友全成就榜.csv'; a.click(); setTimeout(() => URL.revokeObjectURL(url),5000);
  }
  window.achievementsUI = {render,export:exportRank,debug:() => ({ranked:ranked(),opened:[...opened]})};
  render();
})();
