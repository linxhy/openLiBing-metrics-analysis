// openLiBing eCDashboard 看板原值采集驱动器（hook 拦截 + 页面自带鉴权，规避裸 fetch 缺鉴权头被 WAF 挂起）。
// 单进程端到端保持 CDP 会话存活：addScriptToEvaluateOnNewDocument 预注入 XHR hook -> Page.reload ->
// 短轮询读取捕获。hook 内把 ops-overview/main 请求体改写为指定单日（startDate=endDate=date）。
// 用法: node cdp_ol_collect.js --out <file> --port 9222 --tab openlibing --date yyyy-mm-dd
//          --project-id 300130 --page-size 100 --deadline 12000
const fs = require('fs');
const http = require('http');

function argOf(flag, dflt) {
  const i = process.argv.indexOf(flag);
  return i >= 0 ? process.argv[i + 1] : dflt;
}
const outFile = argOf('--out', null);
const PORT = parseInt(argOf('--port', '9222'), 10);
const TAB = argOf('--tab', 'openlibing');
const DATE = String(argOf('--date', ''));
const PAGE_SIZE = parseInt(argOf('--page-size', '100'), 10);
const DEADLINE_MS = parseInt(argOf('--deadline', '12000'), 10);
const sleep = ms => new Promise(r => setTimeout(r, ms));

function buildHook(date, pageSize) {
  return `(function(){
  try {
    window.__HOOK_BOOTED = (window.__HOOK_BOOTED || 0) + 1;
    window.__CAPTURES = [];
    window.__HOOK_ERR = null;
    var TARGET_DATE = ${JSON.stringify(date)};
    var TARGET_PAGESIZE = ${JSON.stringify(pageSize)};
    function kindOf(u){
      if(!u) return '';
      if(u.indexOf('ops-overview/main') >= 0) return 'main';
      if(u.indexOf('metric/dict') >= 0) return 'dict';
      return '';
    }
    var XO = XMLHttpRequest.prototype.open, XS = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function(m,u){ this.__u=u; return XO.apply(this, arguments); };
    XMLHttpRequest.prototype.send = function(b){
      var self = this;
      var outBody = b;
      try {
        if(self.__u && self.__u.indexOf('ops-overview/main') >= 0 && typeof b === 'string'){
          var parsed = JSON.parse(b);
          parsed.startDate = TARGET_DATE;
          parsed.endDate = TARGET_DATE;
          parsed.page = 1;
          parsed.pageSize = TARGET_PAGESIZE;
          outBody = JSON.stringify(parsed);
          self.__rewritten = true;
        }
      } catch(e){ window.__HOOK_ERR = (window.__HOOK_ERR || '') + '|rewrite:' + e.message; }
      this.addEventListener('load', function(){
        try {
          var kind = kindOf(self.__u);
          if(kind){
            window.__CAPTURES.push({
              kind: kind,
              url: self.__u,
              rewritten: !!self.__rewritten,
              postData: (typeof outBody === 'string' ? outBody : null),
              status: self.status,
              body: String(self.responseText || '')
            });
          }
        } catch(e){ window.__HOOK_ERR = (window.__HOOK_ERR || '') + '|capture:' + e.message; }
      });
      return XS.call(this, outBody);
    };
    window.__HOOK_READY = true;
  } catch(e) { window.__HOOK_ERR = (window.__HOOK_ERR || '') + '|boot:' + e.message; }
})();`;
}

const STATE_EXPR = `JSON.stringify({
  booted: window.__HOOK_BOOTED || 0,
  ready: !!window.__HOOK_READY,
  hookErr: window.__HOOK_ERR || null,
  captures: window.__CAPTURES || []
})`;

function httpJSON(method, path) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port: PORT, path, method }, r => {
      let d = ''; r.on('data', c => d += c);
      r.on('end', () => { try { resolve(JSON.parse(d)); } catch (e) { reject(e); } });
    });
    req.on('error', reject); req.end();
  });
}

(async () => {
  if (!DATE) { fs.writeFileSync(outFile, JSON.stringify({ ok: false, error: 'MISSING_DATE' })); process.exit(1); }
  const t0 = Date.now();
  const targets = await httpJSON('GET', '/json/list');
  const page = targets.find(t => t.type === 'page' && t.url.includes(TAB));
  if (!page) { fs.writeFileSync(outFile, JSON.stringify({ ok: false, error: 'NO_TAB' })); process.exit(1); }

  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let id = 0;
  const pending = new Map();
  ws.addEventListener('message', ev => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      const p = pending.get(msg.id); pending.delete(msg.id);
      msg.error ? p.rej(new Error(JSON.stringify(msg.error))) : p.res(msg.result);
    }
  });
  await new Promise((res, rej) => {
    ws.addEventListener('open', res, { once: true });
    ws.addEventListener('error', rej, { once: true });
  });
  const send = (method, params = {}) => new Promise((res, rej) => {
    const mid = ++id; pending.set(mid, { res, rej });
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  const evalSync = async (expr) => {
    const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: false, returnByValue: true });
    return r.result && r.result.value;
  };

  await send('Page.enable');
  await send('Runtime.enable');
  const inj = await send('Page.addScriptToEvaluateOnNewDocument', { source: buildHook(DATE, PAGE_SIZE) });
  await send('Page.reload', { ignoreCache: true });

  let state = null;
  while (Date.now() - t0 < DEADLINE_MS) {
    await sleep(700);
    try {
      const raw = await evalSync(STATE_EXPR);
      if (raw) {
        state = JSON.parse(raw);
        const kinds = (state.captures || []).map(c => c.kind);
        if (kinds.includes('main')) break;
      }
    } catch (e) { /* 上下文重建中，继续轮询 */ }
  }
  ws.close();

  const captures = (state && state.captures) || [];
  const mainCap = captures.find(c => c.kind === 'main') || null;
  const dictCap = captures.find(c => c.kind === 'dict') || null;
  const result = {
    ok: !!mainCap,
    date: DATE,
    projectId: Number(argOf('--project-id', '300130')),
    identifier: inj.identifier,
    elapsedMs: Date.now() - t0,
    booted: state ? state.booted : 0,
    hookErr: state ? state.hookErr : 'no readable state before deadline',
    main: mainCap,
    dict: dictCap
  };
  fs.writeFileSync(outFile, JSON.stringify(result, null, 1), 'utf8');
  process.exit(result.ok ? 0 : 1);
})().catch(e => {
  try { fs.writeFileSync(outFile, JSON.stringify({ ok: false, error: String(e && e.message || e) })); } catch (_) {}
  process.exit(1);
});
