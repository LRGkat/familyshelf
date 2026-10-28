(() => {
  const nativeFetch = window.fetch.bind(window);
  const apiBase = String(window.FAMILYSHELF_CONFIG?.apiBase || '').replace(/\/$/, '');
  let sessionPromise;
  async function session() {
    if (!apiBase) throw new Error('在线服务尚未配置');
    if (!sessionPromise) sessionPromise = (async () => {
      const response = await nativeFetch(apiBase + '/api/session', {
        method: 'POST', headers: {'Content-Type':'application/json'}, body:'{}',
        credentials:'omit', cache:'no-store', signal:AbortSignal.timeout(90000)
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || '无法创建会话');
      return result.session;
    })().catch(error => {sessionPromise = null; throw error});
    return sessionPromise;
  }
  window.fetch = async (input, options = {}) => {
    if (typeof input !== 'string' || !input.startsWith('/api/')) return nativeFetch(input, options);
    const headers = new Headers(options.headers);
    headers.set('X-FamilyShelf-Session', await session());
    return nativeFetch(apiBase + input, {...options, headers, credentials:'omit', cache:'no-store', signal: options.signal || AbortSignal.timeout(90000)});
  };
})();
