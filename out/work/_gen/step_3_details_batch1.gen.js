(async () => {
const PARAMS = {"runs": [{"key": "Triton_Ascend|Ascend", "rn": 8, "st": "09-20 08:00", "status": "FAILED", "cached": false, "track": "bs", "dims": ["bs"], "projectId": 300130, "pipelineId": "9a4886d40e544cecb7da63c9597490a6", "pipelineRunId": "715bbcb41c6c481f98864ad495d60043"}, {"key": "Triton_Ascend|Ascend", "rn": 45, "st": "10-15 17:00", "status": "FAILED", "cached": false, "track": "bs", "dims": ["bs"], "projectId": 300130, "pipelineId": "9a4886d40e544cecb7da63c9597490a6", "pipelineRunId": "43e567f2cb9b49a989edade14fcb0db3"}, {"key": "Triton_Ascend|Ascend", "rn": 63, "st": "10-22 08:00", "status": "FAILED", "cached": false, "track": "bs", "dims": ["bs"], "projectId": 300130, "pipelineId": "9a4886d40e544cecb7da63c9597490a6", "pipelineRunId": "fdcb33bb6928404f9465de1854ff5829"}, {"key": "Triton_Ascend|Ascend", "rn": 64, "st": "10-22 09:16", "status": "FAILED", "cached": false, "track": "bs", "dims": ["bs"], "projectId": 300130, "pipelineId": "9a4886d40e544cecb7da63c9597490a6", "pipelineRunId": "ef918397deef43d9a00d457ca3ca8fe1"}, {"key": "Triton_Ascend|Ascend", "rn": 65, "st": "10-22 09:48", "status": "FAILED", "cached": false, "track": "bs", "dims": ["bs"], "projectId": 300130, "pipelineId": "9a4886d40e544cecb7da63c9597490a6", "pipelineRunId": "4a2cffa668d24bd58966aa414069ebd5"}, {"key": "Triton_Ascend|Ascend", "rn": 66, "st": "10-22 10:16", "status": "FAILED", "cached": false, "track": "bs", "dims": ["bs"], "projectId": 300130, "pipelineId": "9a4886d40e544cecb7da63c9597490a6", "pipelineRunId": "ed92b97215654b95af9b079f555fefe1"}, {"key": "Triton_Ascend|Ascend", "rn": 70, "st": "10-23 17:00", "status": "FAILED", "cached": false, "track": "bs", "dims": ["bs"], "projectId": 300130, "pipelineId": "9a4886d40e544cecb7da63c9597490a6", "pipelineRunId": "a811076814564a7794b113d85ad2334d"}, {"key": "Triton_Ascend|Ascend", "rn": 76, "st": "10-26 17:00", "status": "FAILED", "cached": false, "track": "bs", "dims": ["bs"], "projectId": 300130, "pipelineId": "9a4886d40e544cecb7da63c9597490a6", "pipelineRunId": "ccd5d0134b3d4b7c8f978bf0680a2e14"}, {"key": "Triton_Ascend|Ascend", "rn": 82, "st": "10-28 19:14", "status": "CANCELED", "cached": false, "track": "bs", "dims": ["bs"], "projectId": 300130, "pipelineId": "9a4886d40e544cecb7da63c9597490a6", "pipelineRunId": "199ba2c5c4534de3a0ac8a1197cab9c5"}, {"key": "Triton_Ascend|Ascend", "rn": 92, "st": "11-02 08:00", "status": "FAILED", "cached": false, "track": "bs", "dims": ["bs"], "projectId": 300130, "pipelineId": "9a4886d40e544cecb7da63c9597490a6", "pipelineRunId": "683e0a99287844b189c34642fd251077"}, {"key": "Triton_Ascend|Ascend", "rn": 93, "st": "11-02 14:56", "status": "FAILED", "cached": false, "track": "bs", "dims": ["bs"], "projectId": 300130, "pipelineId": "9a4886d40e544cecb7da63c9597490a6", "pipelineRunId": "39bac8cda1db43be93bc10f862cf398a"}, {"key": "Triton_Ascend|Ascend", "rn": 96, "st": "11-03 08:00", "status": "FAILED", "cached": false, "track": "bs", "dims": ["bs"], "projectId": 300130, "pipelineId": "9a4886d40e544cecb7da63c9597490a6", "pipelineRunId": "088cb5b9423049699078f30afe0bdf58"}, {"key": "Triton_Ascend|Ascend", "rn": 104, "st": "11-06 17:00", "status": "FAILED", "cached": false, "track": "va", "dims": ["va", "bs"], "projectId": 300130, "pipelineId": "9a4886d40e544cecb7da63c9597490a6", "pipelineRunId": "9ae1e7dda41c4a78957a5ba09d2e5b3c"}], "delay": 250, "conc": 4, "retry": 2, "backoffMs": 2000};
return (async () => {
  const csrf = (document.cookie.split('; ').find(c => c.startsWith('csrf-token-open-li-bing=')) || '').split('=').slice(1).join('=');
  if (!csrf) return JSON.stringify({ ok: false, error: 'NOT_LOGGED_IN' });
  const G = '/gateway/openlibing-cicd';
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const out = [];
  const CONC = Math.max(1, PARAMS.conc || 1);
  const RETRY = Math.max(0, PARAMS.retry || 0);
  // T4 详情响应偶发被网关截断（Unexpected end of JSON input）：
  // 失败原样重试整个请求，最多 RETRY 次，每次间隔 backoffMs
  async function fetchOnce(r) {
    const res = await fetch(G + '/project/pipeline/pipeline-run/detail?projectId=' + r.projectId + '&pipelineId=' + r.pipelineId + '&pipelineRunId=' + r.pipelineRunId, {
      headers: { 'Csrf-Token-Open-Li-Bing': csrf }
    });
    const txt = await res.text();
    try {
      const j = JSON.parse(txt);
      if (j.code !== 200 || !j.data) throw new Error('code=' + j.code + ' len=' + txt.length);
      // 任务图谱为空 = 没拿到失败任务（截断/未生成），对下游同样不可用，按失败处理走重试
      if (!(j.data.stages || []).length) throw new Error('stages 空 len=' + txt.length);
      out.push({ key: r.key, rn: r.rn, st: r.st, rstatus: r.status, rid: r.pipelineRunId, track: r.track, dims: r.dims, code: j.code, data: j.data });
      return true;
    } catch (e) {
      out.push({ key: r.key, rn: r.rn, st: r.st, rstatus: r.status, rid: r.pipelineRunId, err: String(e && e.message || e), body: txt.slice(0, 200) });
      return false;
    }
  }
  async function fetchOne(r) {
    for (let attempt = 0; attempt <= RETRY; attempt++) {
      if (await fetchOnce(r)) return;
      // 只重试真正失败的那条：把刚 push 的错误项弹出来重抓，成功即终止
      const bad = out[out.length - 1];
      if (bad && bad.rid === r.pipelineRunId && bad.err) out.pop();
      if (attempt < RETRY) await sleep(PARAMS.backoffMs || 1500);
    }
    out.push({ key: r.key, rn: r.rn, st: r.st, rstatus: r.status, rid: r.pipelineRunId, err: 'T4 重试 ' + RETRY + ' 次后仍失败' });
  }
  for (let i = 0; i < PARAMS.runs.length; i += CONC) {
    await Promise.all(PARAMS.runs.slice(i, i + CONC).map(fetchOne));
    if (i + CONC < PARAMS.runs.length) await sleep(PARAMS.delay);
  }
  return JSON.stringify({ ok: true, details: out });
})()


})()