# -*- coding: utf-8 -*-
"""第 9 步 生成展示页（首页 + 二级页）—— 只写静态壳，不写任何业务数据。

为什么不内嵌数据：
  数据和 HTML 必须完全隔离。页面里只有样式、公共日期筛选组件和取数脚本，
  业务数字一律在浏览器运行时从 out/archive/ 下的 JSON 读，
  所以归档改了、补抓了、agent 补了根因，页面刷新即生效，不需要重新生成 HTML。

取数链（每一层都带口径与来源，便于核对）：
  首页    archive/_index.json                      → 归档日清单（days[]）
          archive/_products.json                   → 全部开源组件（含无待分析流水线的，行不能缺）
          archive/<年>/<年-月>/<日>/_day.json       → 当日产品点灯 + 流水线索引
  二级页  _day.json.pipelines[].path                → 该流水线当天的归档目录
          <目录>/pipeline.json                      → 一次读完：stepIndex / runHistory /
                                                       evidence.missing / records[]（记录全文）
  说明：一条流水线归档已合并成两份文件，页面首屏只 fetch 其中一份：
        pipeline.json（清单 + 每步正文 + 逐条记录 + 运行历史）与
        pipeline.evidence.json（证据原文逐行内嵌，最大单目录几十 MB，只在需要看原文时才取，
        页面不读它 —— 「位置」列的行号与它内部行号一致，按需用 tools/read_evidence.py 取）。
        碰到还没合并的老归档（没有 pipeline.json），jpipe() 自动退回
        「step_2_runs.json + _manifest.json + records/run_*.json」分开读，页面逻辑不用分支。
        step_6_records.json 单份可达 80KB+，页面**始终不读它**。

口径：首页和二级页都用 lib/ui.py 里的同一个筛选组件，日期一律是【执行结束时间】。
      二级页的区间与首页同源（URL from/to → localStorage），不会出现"首页算进去、
      二级页算不进去"的错位。

⚠ 直接用 file:// 双击打开时，浏览器会拦截 fetch 读取本地 JSON（CORS）。
   请用入口起本地服务：python run_pipeline.py --serve
"""
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import ui  # noqa: E402
from lib.cdp_exec import load_config  # noqa: E402
from lib.paths import DATE_RULE, HTML_DIR, doc, iter_archive_days, rel  # noqa: E402
from lib.workdays import build_workday_set  # noqa: E402

INDEX_NAME = "index.html"
DETAIL_NAME = "detail.html"
META_NAME = "_pages.json"

INDEX_TITLE = "流水线失败原因智能定位定界"
DETAIL_TITLE = "流水线失败原因智能定位定界 · 明细"

# ---------------------------------------------------------------- 首页
# 顶部顺序固定为：品牌行 → 全局日期筛选条 → 页面自身标题/表格。
# 二级页用同一份 品牌行 + 筛选条 且排在最前，所以切页时筛选条位置不动。
INDEX_BODY = (
    '<div class="wrap">\n'
    '  <span id="rule" hidden>' + DATE_RULE + '</span>\n'
    '  ' + ui.header() + '\n'
    '  ' + ui.toolbar() + '\n'
    '  <h2>开源组件级概览</h2>\n'
    '  <div class="tbl-wrap">\n'
    '    <table class="ov" id="tbl">\n'
    '      <thead><tr>\n'
    '        <th>开源组件</th>\n'
    '        <th>版本可用度</th>\n'
    '        <th>构建成功率</th>\n'
    '        <th>流水线数</th>\n'
    '        <th>待分析流水线数（版本可用度）</th>\n'
    '        <th>待分析流水线数（构建成功率）</th>\n'
    '      </tr></thead>\n'
    '      <tbody id="tb"><tr><td colspan="6" class="loading">正在读取归档…</td></tr></tbody>\n'
    '    </table>\n'
    '  </div>\n'
    '  <div class="tnote" id="tnote"></div>\n'
    '  <footer id="foot"></footer>\n'
    '</div>\n'
    # 「流水线数」列的弹窗：列出该组件全部流水线 + 各自的版本可用度/构建成功率。
    # 版式与二级页同一个弹窗骨架：外框定高不滚，滚动条只出现在表格里
    '<div class="modal" id="mrec">\n'
    '  <div class="modal-box">\n'
    '    <div class="modal-hd"><div class="hd-l">'
    '<button type="button" id="mback" hidden>← 返回</button>'
    '<span id="mttl"></span></div>'
    '<button type="button" id="mclose">关闭</button></div>\n'
    '    <div id="mbody"></div>\n'
    '  </div>\n'
    '</div>'
)

# 弹窗骨架 + table.rca 版式：首页（流水线清单弹窗 / 执行记录二级弹窗）与二级页（失败用例 / 日志证据）共用。
# 版式固定成「标题 → 表格区（唯一滚动区）→ 脚注」三段：
# 外框只做定高、overflow:hidden，所以弹窗自己永不出滚动条；
# 纵向滚动条贴着表格右边缘、横向滚动条贴着表格底边，滚动的始终是表格本身。
SHARED_MODAL_CSS = """
.modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:50;
  align-items:center;justify-content:center;padding:24px 16px;overflow:hidden}
.modal.on{display:flex}
.modal-box{background:#fff;border-radius:10px;width:min(1280px,96vw);
  max-height:calc(100vh - 48px);overflow:hidden;
  padding:16px 20px;box-shadow:0 8px 30px rgba(16,32,64,.2);
  display:flex;flex-direction:column}
.modal-hd{display:flex;justify-content:space-between;align-items:center;gap:10px;flex:0 0 auto;
  font-weight:600;margin-bottom:10px;padding-bottom:8px;border-bottom:1px solid var(--line2)}
.modal-hd button{background:#fff;border:1px solid #ccc;border-radius:6px;padding:3px 12px;cursor:pointer;font:inherit}
/* 弹窗正文 = 表格区（可滚，吃掉全部剩余高度）+ 脚注（固定不动） */
#mbody{flex:1 1 auto;min-height:0;display:flex;flex-direction:column}
/* 表格独占的滚动容器：纵向滚动条在表格右侧、横向滚动条在表格下方，弹窗本身不滚 */
.mtbl{flex:1 1 auto;min-height:0;overflow:auto}
.mtbl table.rca{margin:0}
/* 表头吸顶：表格内滚动时列名始终可见 */
.mtbl table.rca thead th{position:sticky;top:0;z-index:2;background:#f7f9fc;text-align:center;
  box-shadow:inset 0 -1px 0 #e3e6ea,inset 0 1px 0 #e3e6ea}
.mrec-note{margin-top:6px;color:#999;font-size:12px;flex:0 0 auto}
/* ---------- 弹窗内的通用表格（同旧报告 table.rca） ---------- */
table.rca{table-layout:fixed;width:100%;border-collapse:collapse;font-size:13px;margin:8px 0 4px}
table.rca th,table.rca td{border:1px solid #e3e6ea;padding:7px 9px;text-align:left;
  vertical-align:top;line-height:1.55;word-break:break-word;overflow-wrap:anywhere}
table.rca th{background:#f7f9fc;font-weight:600;white-space:nowrap;color:#3b4a60;text-align:center}
table.rca tbody tr:nth-child(even) td{background:#fafbfc}
/* 弹窗表格里的长内容列：一律在列宽内换行（长路径/用例名没有空格也能断），
   绝不横向溢出到相邻列 —— 列宽由 colgroup 固定，行高随内容自然增长 */
table.rca td.cw{white-space:normal;word-break:break-all;overflow-wrap:anywhere}
/* 时间列：优先在「日期」和「时刻」之间的空格处折行，不要从毫秒中间断开 */
table.rca td.cw1{white-space:normal;word-break:normal;overflow-wrap:anywhere}
/* 居中列：流水线弹窗的后三列（平台 / 版本可用度 / 构建成功率 / 执行记录）
   —— 数字与短词居中排，长说明仍然留在第一列换行 */
table.rca td.ctr{text-align:center;white-space:nowrap}
table.rca td.ctr .rate-line{justify-content:center}
/* 数字一律不按灯色上色（用户口径）：达标与否只由灯点表达，数字保持正文色 */
table.rca .pct{color:#1b2739}
/* 二级弹窗的「← 返回」：只在弹窗里又开了弹窗时出现，标题左对齐并允许省略 */
.modal-hd .hd-l{display:flex;align-items:center;gap:10px;min-width:0;flex:1 1 auto}
.modal-hd .hd-l #mttl{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.modal-hd #mback{border-color:#bfd3fe;color:#1d4ed8;white-space:nowrap}
"""

INDEX_EXTRA_CSS = SHARED_MODAL_CSS + """
/* 首页整页固定成一屏：html/body 的滚动全部关掉，页面本身永远不出滚动条。
   之前只给 .wrap 定了高，卡片底部那 12px 外边距会溢出 body，页面就多出 12px 的滚动条
   （右侧那条细滚动条就是这么来的）；现在把滚动彻底关在首页之外。 */
html,body{height:100%;overflow:hidden}
/* 卡片高度 = 一屏减去上下外边距；只有主尺寸确定，表格区的 flex:1 才会真的分到
   「剩余高度」，fitRows() 也才能算出「13 条数据刚好铺满」的行高。
   数据变多时只在表格区内部滚动，滚动条留在表格里，不会跑到页面上。 */
.wrap{height:calc(100vh - 24px)}
.tbl-wrap{min-height:0}
/* 表下统计口径 + 页脚（对齐旧报告版式）：不参与收缩，高度不够时优先压表格区而不是裁文字；
   口径是一条一个 li 的列表（见 CALIBER_JS），容器用 div 而不是 p */
.tnote{flex:0 0 auto;font-size:11.5px;color:#7c8698;line-height:1.65;margin:9px 0 0}
footer#foot{flex:0 0 auto;font-size:11.5px;color:#9aa3af;line-height:1.7;margin-top:7px;padding-top:8px;
  border-top:1px solid var(--line2)}
/* 统计口径按条展示（一条一个 li）：首页是一屏定高的看板，所以两列铺开省高度，
   不再像以前那样把几百字拼成一整条压在表格下面 */
.tnote .tn-hd{font-size:11.5px;font-weight:600;color:#4a5668;margin:0 0 2px}
.tnote ul.tn-list{list-style:none;margin:0;padding:0;columns:2;column-gap:26px}
.tnote ul.tn-list li{margin:0 0 2px;padding-left:10px;text-indent:-10px;
  break-inside:avoid;page-break-inside:avoid}
.tnote .tn-list li b{color:#3b4a60;font-weight:600}
/* 首页 h2 右侧的 PR 页入口（与二级页 chip 同色系，不抢表格视觉） */
.prlnk{float:right;font-size:12.5px;font-weight:400;color:var(--acc);text-decoration:none}
.prlnk:hover{text-decoration:underline}
"""

# ---------------------------------------------------------------- 统计口径（只在首页出现）
# 首页表格下方的「统计口径」区块。以前是一整条拼出来的长字符串，几百字连成一段，
# 要核对其中的某一条得自己在里面找；现在一条一个 <li>（小标题加粗 + 一句说明）。
# 两个容易「结果被挑战」的点各用一句话带过（不展开）：
#   ① 版本可用度 = 当天有成功执行【并且测试用例通过率 100%】才算可用日（不是「有成功运行就算」）；
#   ② 特例只用括号括两句：GitHub 平台流水线不看用例 100%；vllm-ascend 只采定时（schedule）运行。
# 二级页不再重复这份口径（只有首页有），所以口径永远只有这一处脚本、一份文案。
CALIBER_JS = r"""
/* ================= 统计口径（只在首页，按条展示） =================
   纯口径、与取数无关，所以脚本一执行就填好，不用等归档数据回来。
   每条 = <li><b>小标题</b>：说明</li>；唯一来源是本文件的 CALIBER_JS，二级页不再挂一份。 */
function caliberItems(){
  return [
    ['统计区间',
     '按流水线<b>执行结束时间</b>归属日期（GitHub 平台取 <span class="mono">run.updated_at</span>），'
     + '结束时间落在所选区间内才计入；换区间即按新区间整体重算。'],
    ['版本可用度（流水线）',
     '<b>可用日 ÷ 该流水线在区间内有执行记录的天数</b>（当天有运行<b>开始或结束</b>才算'
     + '「有执行记录」；当天没启动、没跑过的日子不进分母）。'
     + '可用日 = 当天有<b>执行成功</b>的运行，<b>并且该运行的测试用例通过率 100%</b>。'],
    ['版本可用度（组件）',
     '该组件各流水线版本可用度之和 ÷ 流水线条数（算术平均），'
     + '不是 Σ可用日 ÷ Σ有运行日。'],
    ['特例',
     '（GitHub 平台流水线不看用例 100%；'
     + 'vllm-ascend 的流水线只采集定时任务（schedule）触发的运行。）'],
    ['区间内无执行记录',
     '只有看板配置、一次都没跑过的流水线显示 <span class="mono">--</span>，'
     + '不参与平均、不计入流水线条数、也不算「待分析流水线数」。'],
    ['构建成功率',
     'Σ编译通过 ÷ Σ编译次数；GitHub 平台不采集编译次数，显示 <span class="mono">--</span>。'],
    ['区间重算与灯色',
     '区间一变就按分子/分母重算（不是逐日比率取平均）；灯色不落库，'
     + '按重算后的比率就地判档：版本可用度 &ge;90% 绿 / &ge;80% 黄 / 否则红，'
     + '构建成功率 100% 绿 / &ge;95% 黄 / 否则红，区间内只出这一档。'],
    ['待分析流水线数',
     '该组件在区间内进入待分析集合、且对应维度重算后未达标的流水线条数，'
     + '与看板「待分析」判定一致，不是失败次数。'],
  ];
}
function caliberHTML(){
  const items = caliberItems();
  return '<div class="tn-hd">统计口径（' + items.length + ' 条）</div>'
    + '<ul class="tn-list">'
    + items.map(function(kv){ return '<li><b>' + kv[0] + '</b>：' + kv[1] + '</li>'; }).join('')
    + '</ul>';
}
/* 页面脚本都在 body 末尾执行，容器已在 DOM 里 → 直接填一次（首页才有这份口径） */
(function(){
  const el = document.getElementById('tnote');
  if (el) el.innerHTML = caliberHTML();
})();
"""

INDEX_JS = r"""
/* ================= 首页：读归档 JSON，按执行结束时间区间聚合 ================= */
/* 数据根：一律相对「当前文档」算，全程不出现域名也不写死站点根。
   正常情况页面在 out/html/ 下，第一枪 '../' 就命中 out/archive/；
   万一整站被放到别的层级（比如直接以 out/ 为站点根），后面几枪能自己找到，
   不会因为层级差一层就整页空白。 */
let ARCH_ROOT = '../';
const ROOT_CANDIDATES = ['../', './', '../../', '../../../'];
function probeRoot(){
  let i = 0;
  function step(){
    if (i >= ROOT_CANDIDATES.length){
      return Promise.reject(new Error('archive/_index.json → HTTP 404（已依次试过：'
        + ROOT_CANDIDATES.join('  ') + '）'));
    }
    const cand = ROOT_CANDIDATES[i++];
    return jget(cand + 'archive/_index.json').then(function(x){
      ARCH_ROOT = cand;          /* 命中后就固定下来，后面所有数据都拼这个前缀 */
      return x;
    }).catch(function(e){
      if (isNotFound(e)) return step();
      throw e;
    });
  }
  return step();
}

const DETAIL_PAGE = '__DETAIL__';   /* 二级页文件名（构建时注入，避免 JS 里出现未定义变量） */
const LIGHT_RANK = {'红': 3, '黄': 2, '绿': 1};
let IDX = null;
let CATALOG = [];        /* archive/_products.json：全部开源组件（含无待分析流水线的） */
let ROWS = [];           /* 最近一次 render 的组件行：点「流水线数」时从这里取该组件的流水线清单 */

function jget(p){
  return fetch(p, {cache: 'no-store'}).then(function(r){
    if (!r.ok) throw new Error(p + ' → HTTP ' + r.status);
    return r.json();
  });
}
/* 文件不存在（归档区还没跑过采集）与「读取失败」要分开：前者提示去采集，后者才是环境问题 */
function isNotFound(e){
  const m = String((e && e.message) || e);
  return /HTTP 40[34]/.test(m) || /\b(404|ENOENT)\b/.test(m) || /not\s*found/i.test(m);
}
/* ===== 一条流水线归档的统一读法（与二级页同一套） =====
   归档已把「9 份 JSON + records/run_*.json」合并成一份 pipeline.json（schema=pipeline/1），
   运行历史在 steps.step_2_runs.runHistory —— 一次 fetch 就拿到，弹窗里点「查看」时才按需读。
   碰到还没合并的老归档（pipeline.json 不存在）自动退回 step_2_runs.json，页面不用分支。 */
function jpipe(dirPath){
  const base = ARCH_ROOT + dirPath + '/';
  return jget(base + 'pipeline.json').then(function(doc){
    doc = doc || {};
    return {base: base, merged: true,
            runs: (((doc.steps || {}).step_2_runs || {}).runHistory) || []};
  }).catch(function(e){
    if (!isNotFound(e)) throw e;
    return jget(base + 'step_2_runs.json').catch(function(){ return {}; }).then(function(s2){
      return {base: base, merged: false, runs: (s2 || {}).runHistory || []};
    });
  });
}
function nz(v){ return (v === null || v === undefined || v === '') ? '--' : String(v); }
function fmtPct(v){
  return (v === null || v === undefined || v === '' || isNaN(v)) ? '--' : (Number(v) * 100).toFixed(1) + '%';
}
function avgOf(a){ if (!a.length) return null; let s = 0; a.forEach(function(v){ s += Number(v); }); return s / a.length; }
/* 灯色一律就地按比率判档（归档只存数值，不存黄/绿/红）：
     版本可用度 >=90% 绿 / >=80% 黄 / 否则红；构建成功率 100% 绿 / >=95% 黄 / 否则红
   老归档里还留着 vaLight/bsLight 字段，仅作字段缺失时的兜底。 */
function lightByRate(v, kind){
  if (v === null || v === undefined || v === '' || isNaN(v)) return null;
  const r = Number(v);
  if (kind === 'bs') return r >= 1 ? '绿' : (r >= 0.95 ? '黄' : '红');
  return r >= 0.9 ? '绿' : (r >= 0.8 ? '黄' : '红');
}
function worstLight(a, b){
  if (!a || a === '无数据') return b || a;
  if (!b || b === '无数据') return a;
  return (LIGHT_RANK[b] || 0) > (LIGHT_RANK[a] || 0) ? b : a;
}
function dotOf(l){ const m = {'绿': 'green', '黄': 'yellow', '红': 'red'}; return m[l] ? '<span class="dot ' + m[l] + '"></span>' : ''; }

/* 组件级版本可用度的 title：把「哪几条流水线、各是多少、加起来除以几条」摊开写，
   口径变了要能一眼核对 —— 组件级 = Σ各流水线版本可用度 ÷ 流水线条数。 */
function vaTitleOf(mineVA, vals, rate, wdN, from, to){
  /* 分母统一为「该流水线有执行记录的天数」：当天有运行开始或结束才算，没启动 / 没跑过的
     日子不进分母（老归档缺 vaDetail 时才退回区间法定工作日数 wdN 兜底）。 */
  const lines = ['版本可用度（组件级 = 各流水线版本可用度之和 ÷ 流水线条数）',
                 '统计区间 ' + from + ' ~ ' + to + ' ｜ 口径：可用天数 ÷ 有执行记录的天数'];
  if (!vals.length){
    lines.push(mineVA.length
      ? '该组件的流水线在本区间内都没有执行记录，可用度显示 --（不纳入统计）'
      : '该区间内没有带版本可用度维度的流水线，比值取自归档当日值');
    return lines.join('\n');
  }
  let sum = 0;
  vals.forEach(function(v){ sum += Number(v); });
  mineVA.forEach(function(p){
    const skipped = (p.vaRate === null || p.vaRate === undefined || isNaN(p.vaRate));
    const den = (Number(p.vaN) > 0) ? Number(p.vaN) : wdN;
    lines.push('  ' + plDisp(p) + (skipped
      ? '  区间内无执行记录 → --（不纳入统计）'
      : '  可用日 ' + p.vaA + ' / 有执行记录 ' + den + ' 天 → ' + fmtPct(p.vaRate)));
  });
  lines.push('合计 ' + fmtPct(sum) + ' ÷ ' + vals.length + ' 条流水线 = ' + fmtPct(rate)
    + (mineVA.length > vals.length
      ? '（另有 ' + (mineVA.length - vals.length) + ' 条区间内无执行记录，已排除）' : ''));
  lines.push('流水线级口径：当日存在任一条成功运行即算「当天有可用版本」，'
             + '分母是该流水线有执行记录的天数（当天有运行开始或结束才算；没启动的日子、'
             + '以及当天只有 canceled 运行的都不计入）');
  return lines.join('\n');
}

/* 组件级构建成功率的 title：把「哪几条流水线、各自编译通过/次数、合计」摊开写。
   组件级 = Σ编译通过 ÷ Σ编译次数，且只累加**当前统计区间内逐日**的流水线级 bsDetail
   （与版本可用度同一套「逐日分子/分母」口径），所以单日筛选时不会混入别的日期的编译结果。 */
function bsTitleOf(mineBS, bsP, bsC, bsRate, from, to){
  const lines = ['构建成功率（组件级 = Σ编译通过 ÷ Σ编译次数）',
                 '统计区间 ' + from + ' ~ ' + to];
  if (!mineBS.length){
    lines.push('该组件在本区间内没有可比对的编译次数，比率显示 --');
    return lines.join('\n');
  }
  mineBS.forEach(function(p){
    const v = p.bsC > 0 ? p.bsP / p.bsC : null;
    lines.push('  ' + plDisp(p) + '  编译通过 ' + p.bsP + ' / ' + p.bsC
      + ' = ' + (v === null ? '--' : fmtPct(v)));
  });
  lines.push('合计 ' + bsP + ' ÷ ' + bsC + ' = ' + fmtPct(bsRate));
  return lines.join('\n');
}
function failBox(msg){
  const box = document.getElementById('tb');
  if (box) box.innerHTML = '<tr><td colspan="6"><div class="err">' + msg + '</div></td></tr>';
}

/* 静态托管（GitHub Pages 等）上归档取不到，九成是 Jekyll 把 _ 开头的文件整批过滤了：
   归档索引正好叫 archive/_index.json、archive/<日>/_day.json（流水线目录里的 pipeline.json
   不以 _ 开头，不受影响；老归档里的 _manifest.json 仍会被过滤）。
   本地 http 服务上取不到才是「还没采集」。两种环境给两种提示，
   避免页面在线上让人去跑 run_pipeline（静态页本来就不该跑采集）。 */
function hostedSite(){
  const p = location.protocol, h = String(location.hostname || '');
  if (p !== 'http:' && p !== 'https:') return false;
  if (!h || h === 'localhost' || h === '127.0.0.1') return false;
  if (/^(10|192\.168)\./.test(h) || /^172\.(1[6-9]|2\d|3[01])\./.test(h)) return false;
  return true;
}
function emptyArchiveMsg(){
  if (!hostedSite()) {
    return '归档区暂无数据：请先执行 <span class="mono">python run_pipeline.py</span> 采集并归档。';
  }
  /* 线上还取不到，先别急着怪静态托管：把实际请求的地址打出来，
     一按 F12 就能看出是路径拼错还是托管层没发布这个文件。 */
  return '<b>归档索引没取到（HTTP 404）</b>'
    + '<div style="margin-top:8px">实际请求：<span class="mono">' + rfEsc(ARCH_ROOT + 'archive/_index.json') + '</span></div>'
    + '<div style="margin-top:8px">请按 F12 → Network 看这条请求的最终 URL 与状态码：</div>'
    + '<div style="margin-top:6px">· 若最终 URL 少了 <span class="mono">out/</span> 或多了一层目录 → 页面层级不对，'
    + '应该访问 <span class="mono">&lt;站点根&gt;/out/html/index.html</span>；</div>'
    + '<div style="margin-top:6px">· 若状态码就是 404 但这个文件仓库里确实有 → 是托管层没把它发布出去'
    + '（GitHub Pages 的 Jekyll 会跳过 <span class="mono">_</span> 开头的文件，'
    + '需要在仓库根加一份空的 <span class="mono">.nojekyll</span>）。</div>';
}

function boot(){
  /* 先用相对路径探测数据根，命中后再取组件清单：
       _index.json    → 归档日清单（决定日期控件上下界）
       _products.json → 全部开源组件清单（首页要「列出全部开源组件」，不能只列有待分析的） */
  const sub = document.getElementById('hsub');
  probeRoot().then(function(idx){
    IDX = idx;
    return jget(ARCH_ROOT + 'archive/_products.json').catch(function(){ return {products: []}; });
  }).then(function(prod){
    CATALOG = (prod && prod.products) || [];
    const days = (IDX.days || []).slice().sort(function(a, b){ return a.date < b.date ? -1 : 1; });
    if (!days.length){
      if (sub) sub.textContent = '归档区暂无数据';
      failBox(emptyArchiveMsg());
      return;
    }
    if (sub) sub.textContent = '归档 ' + days.length + ' 天（' + days[0].date + ' ~ '
      + days[days.length - 1].date + '）· 开源组件 ' + (CATALOG.length || '--') + ' 个';
    rfInit({
      min: days[0].date,
      max: days[days.length - 1].date,
      onChange: load
    });
  }).catch(function(e){
    /* 归档区为空时 _index.json 本来就不存在（HTTP 404 / ENOENT），这不叫「读取失败」，
       直接提示去采集；只有 file:// 直开被浏览器拦截（TypeError）才需要提示起本地服务 */
    if (isNotFound(e)){
      if (sub) sub.textContent = '归档区暂无数据';
      failBox(emptyArchiveMsg());
      return;
    }
    if (sub) sub.textContent = '读取归档索引失败';
    if (hostedSite()){
      /* 线上取索引被拦（403/5xx/跨域）——不是本地环境问题，别教人跑 python */
      failBox('<b>读取归档索引失败</b>'
        + '<div class="mono" style="margin-top:6px">' + rfEsc(String(e)) + '</div>'
        + '<div style="margin-top:8px">静态托管环境读不到 '
        + '<span class="mono">archive/_index.json</span>。请确认该文件已随仓库提交、'
        + '仓库根目录有 <span class="mono">.nojekyll</span>、Pages 已重新部署。</div>');
      return;
    }
    failBox('<b>读取归档索引失败</b>'
      + '<div class="mono" style="margin-top:6px">' + rfEsc(String(e)) + '</div>'
      + '<div style="margin-top:8px">直接双击打开（file://）时浏览器会拦截 fetch 读取本地 JSON。'
      + '请在 out 目录起本地服务后访问：<br><span class="mono">python run_pipeline.py --serve</span>'
      + '<br>或 <span class="mono">python -m http.server 8765 --directory out</span>'
      + '，再打开 <span class="mono">http://localhost:8765/html/index.html</span></div>');
  });
}

function load(from, to){
  const tb = document.getElementById('tb');
  /* 区间提示统一由公共筛选条右侧的 #rf-hint 负责（「当前区间 X ~ Y ｜ 工作日 N 天」），
     页面自身标题/副标题保持恒定，切页与改区间都不会让顶部内容跳动。 */
  const days = (IDX.days || []).filter(function(d){ return d.date >= from && d.date <= to; });
  if (!days.length){
    /* 区间内没有归档日（最常见的是「昨天」落在周末/节假日，那天本来就不跑数）：
       表格照常铺满全部开源组件，比率与待分析数一律显示 -- / 0，
       不再用一行提示把整张表替换掉 —— 「不是工作日」就该看到 --，而不是别的日子。 */
    render([], from, to);
    return;
  }
  tb.innerHTML = '<tr><td colspan="6" class="loading">正在读取 ' + days.length + ' 个归档日…</td></tr>';
  Promise.all(days.map(function(d){
    return jget(ARCH_ROOT + d.path + '/_day.json').then(function(x){ x.__path = d.path; return x; })
      .catch(function(e){ return {date: d.date, products: [], pipelines: [], __err: String(e), __path: d.path}; });
  })).then(function(docs){
    docs.sort(function(a, b){ return a.date < b.date ? -1 : 1; });
    render(docs, from, to);
  });
}

function render(docs, from, to){
  const prods = new Map();
  const pipes = new Map();
  const broken = docs.filter(function(d){ return d.__err; });

  /* 先铺全量组件清单：归档日只记录「当天下钻到的产品」，当天没有待分析流水线的产品
     不会出现在 _day.json 里；不全量铺底就会整行消失（用户要求：全部开源组件都列出，
     没有待分析流水线的显示 0）。 */
  CATALOG.forEach(function(p){
    prods.set(p.name, {name: p.name, projectId: p.projectId, days: {}, inCatalog: true});
  });

  docs.forEach(function(dd){
    (dd.products || []).forEach(function(p){
      let r = prods.get(p.product);
      if (!r){ r = {name: p.product, projectId: p.projectId, days: {}}; prods.set(p.product, r); }
      r.days[dd.date] = p;
    });
    (dd.pipelines || []).forEach(function(pl){
      /* 流水线身份一律按 **pipelineId** 归并：短名（= 归档目录名）跨日可能漂移 ——
         同一条 Nightly-CI-Version-pipeline_openUBMC（pipelineId 440c2100…）在
         11-10~11-12 落成目录 openUBMC、在 11-13 落成 openUBMC~f35034，按短名归并
         会把一条流水线算成两条（用户反馈「openUBMC 流水线条数不对」）。
         没有 pipelineId 的老归档才退回短名。 */
      const mk = pl.component + '|' + (pl.pipelineId || pl.pipeline);
      let r = pipes.get(mk);
      if (!r){
        r = {key: mk, component: pl.component, pipeline: pl.pipeline, name: pl.pipelineName || pl.pipeline,
             platform: pl.platform || '', dims: {}, dates: [], path: pl.path,
             vaA: 0, vaN: 0, bsP: 0, bsC: 0, vaVals: [], bsVals: [],
             vaLightOld: null, bsLightOld: null, rateRule: ''};
        pipes.set(mk, r);
      }
      if (pl.rateRule) r.rateRule = pl.rateRule;
      (pl.dims || []).forEach(function(d){ r.dims[d] = 1; });
      /* 区间重算：逐日累加分子/分母，区间一变就按新的 Σ分子/Σ分母 重算，
         不是把逐日比率做算术平均。老归档无 vaDetail/bsDetail 才退回逐日比率平均。 */
      const vd = pl.vaDetail, bd = pl.bsDetail;
      if (vd && Number(vd.daysInPeriod) > 0){
        r.vaA += Number(vd.availDays) || 0; r.vaN += Number(vd.daysInPeriod) || 0;
      } else if (pl.vaRate !== null && pl.vaRate !== undefined && ranInPeriod(pl)){
        r.vaVals.push(Number(pl.vaRate));
      }
      if (bd && Number(bd.buildCount) > 0){
        r.bsP += Number(bd.buildPassCount) || 0; r.bsC += Number(bd.buildCount) || 0;
      } else if (pl.bsRate !== null && pl.bsRate !== undefined && ranInPeriod(pl)){
        r.bsVals.push(Number(pl.bsRate));
      }
      r.vaLightOld = worstLight(r.vaLightOld, pl.vaLight || null);
      r.bsLightOld = worstLight(r.bsLightOld, pl.bsLight || null);
      if (r.dates.indexOf(dd.date) < 0) r.dates.push(dd.date);
      r.path = pl.path;
    });
  });

  const pipeList = Array.from(pipes.values());
  /* 版本可用度的分母 = **该流水线在区间内有执行记录的天数**（不是区间法定工作日数）。
     逐日归档的 vaDetail.daysInPeriod 只有「当天有运行开始或结束（非取消）」才记 1
     （跨午夜运行的开始日由 step_7 补一条逐日条目），所以没启动 / 没跑过的日子天然不进分母：
     一条只在 2 天跑过的流水线是 x/2，而不是 x/5。
     老归档缺 vaDetail（daysInPeriod 全 0）时才退回区间法定工作日数兜底（见 vaDenOf）。 */
  const wdN = wdInRange(from, to);
  pipeList.forEach(function(p){
    /* 流水线级：分子 = 可用日（当日存在任一条 iva=true 的运行即算「当天有可用版本」），
       分母 = 该流水线有执行记录的天数；构建成功率仍是 Σ编译通过 / Σ编译次数。
       **区间内完全没有执行记录**的流水线（RAGSDK、VisionSDK 这类只有看板配置、一次都没跑过的）
       不参与任何计算：既不能算 0%（会被当成「全部不可用」并拉低组件均值），也不能算 100%，
       比率一律给 null → 页面显示 --，组件级平均与「流水线条数」都不含它。
       判据只看分子分母有没有落过数（vaN>0 / vaA>0 / 比率数组非空），且比率数组只收
       「区间内真的跑过」的流水线（ranInPeriod）：归档对零运行的流水线会退回看板值 0.0，
       那个 0 不是「跑了但不可用」，收进来就会把「无执行记录」显示成 0.00%。 */
    const noVA = !(p.vaN > 0 || p.vaVals.length);
    const noBS = !(p.bsC > 0 || p.bsVals.length);
    p.noRun = noVA && noBS;                 /* 两个维度都没有任何运行 = 该流水线无执行记录 */
    p.vaDen = vaDenOf(p.rateRule, p.vaN, wdN);
    p.vaRate = noVA ? null : (p.vaDen > 0 ? Math.min(1, p.vaA / p.vaDen) : avgOf(p.vaVals));
    p.bsRate = noBS ? null : (p.bsC > 0 ? p.bsP / p.bsC : avgOf(p.bsVals));
    p.vaLight = lightByRate(p.vaRate, 'va') || (noVA ? null : p.vaLightOld);
    p.bsLight = lightByRate(p.bsRate, 'bs') || (noBS ? null : p.bsLightOld);
  });

  const rows = Array.from(prods.values()).map(function(r){
    const ds = Object.keys(r.days).sort();
    const dayDocs = ds.map(function(d){ return r.days[d]; });
    const mine = pipeList.filter(function(p){ return p.component === r.name; });
    /* 组件级版本可用度 = 该组件各流水线版本可用度的**算术平均**
       （Σ流水线可用度 ÷ 流水线条数），不是「Σ可用日 / Σ有运行日」。
       只统计带版本可用度维度的流水线：只做编译的流水线不该被算成 0%。 */
    const mineVA = mine.filter(function(p){ return p.dims.va; });
    const vaVals = mineVA.map(function(p){ return p.vaRate; })
                        .filter(function(v){ return v !== null && v !== undefined && !isNaN(v); });
    const vaRate = vaVals.length ? avgOf(vaVals) : null;
    /* 区间内一次都没跑过的流水线：在清单里照常列出（它不是不存在），但不计入上面那个平均 */
    const noRun = mine.filter(function(p){ return p.noRun; });
    /* 组件级构建成功率 = 该组件各流水线的 Σ编译通过 ÷ Σ编译次数（只累加**当前统计区间内的逐日**
       流水线级 bsDetail，与版本可用度同一套「逐日分子/分母」口径）。
       不能读产品级 buildSuccessDetail：那是**采集区间**的聚合 —— 例如 11-11 的归档里
       写的是 11-06~11-12 五天的合计数，单日筛选时会把别的日期的编译结果混进来
       （用户反馈：单日区间仍能看到其它日期的数据）。 */
    const mineBS = mine.filter(function(p){ return p.bsC > 0; });
    const bsP = mineBS.reduce(function(s, p){ return s + p.bsP; }, 0);
    const bsC = mineBS.reduce(function(s, p){ return s + p.bsC; }, 0);
    const bsRate = bsC > 0 ? bsP / bsC : null;
    return {
      name: r.name,
      nd: ds.length,
      days: ds,
      from: from, to: to, wdN: wdN,
      vaRate: vaRate,
      bsRate: bsRate,
      vaLight: lightByRate(vaRate, 'va') || ds.reduce(function(a, d){ return worstLight(a, r.days[d].vaLight); }, null),
      bsLight: lightByRate(bsRate, 'bs') || ds.reduce(function(a, d){ return worstLight(a, r.days[d].bsLight); }, null),
      vaTitle: vaTitleOf(mineVA, vaVals, vaRate, wdN, from, to),
      bsTitle: bsTitleOf(mineBS, bsP, bsC, bsRate, from, to),
      pipes: mine,
      pipeCount: mine.length,
      pipeNoRun: noRun.length,
      noRunNames: noRun.map(function(p){ return plDisp(p); }),
      badVA: mine.filter(function(p){ return p.dims.va && p.vaLight && p.vaLight !== '绿'; }).length,
      badBS: mine.filter(function(p){ return p.dims.bs && p.bsLight && p.bsLight !== '绿'; }).length
    };
  });
  rows.sort(function(a, b){ return ((b.badVA + b.badBS) - (a.badVA + a.badBS)) || (a.name < b.name ? -1 : 1); });
  ROWS = rows;                     /* 供「流水线数」弹窗按行号取该组件的流水线清单 */

  const tb = document.getElementById('tb');
  if (!rows.length && !pipeList.length){
    tb.innerHTML = '<tr><td colspan="6" class="empty">'
      + (docs.length ? '该区间内的归档日没有流水线进入待分析集合'
                     : '该区间内没有归档日（非工作日不跑数），指标显示 --')
      + '</td></tr>';
    paintNotes(docs, [], from, to);
    return;
  }
  const html = rows.map(function(r, i){ return prodRow(r, i, from, to); }).join('');
  tb.innerHTML = html + (broken.length
    ? '<tr><td colspan="6"><div class="err" style="margin:8px 0">'
      + broken.length + ' 个归档日的 _day.json 读取失败：'
      + broken.map(function(b){ return rfEsc(b.date); }).join('、') + '</div></td></tr>' : '');
  paintNotes(docs, rows, from, to);
  fitRows();
}

/* 首页只有这一张表，所以行高要跟着可视区走：整张表恰好铺满表格区（13 条数据刚好吃满），
   既不出现外层滚动条，也不在下面留一大片空白。
   做法：表格容器拿的是卡片内的剩余高度（.tbl-wrap 是 flex:1）→ 减掉表头后按行数均分，
   结果写进 --rowh（table.ov tbody tr 直接用它作行高）。
   行高有 30~70px 的下限/上限：条数多时保持紧凑、条数少时也不会被拉成一根细条。 */
function fitRows(){
  const wrap = document.querySelector('.tbl-wrap');
  const tbl = document.getElementById('tbl');
  if (!wrap || !tbl || !tbl.tBodies.length) return;
  const tb = tbl.tBodies[0];
  const n = tb.rows.length;
  if (!n || (n === 1 && tb.rows[0].cells.length === 1)){
    tbl.style.removeProperty('--rowh');      /* loading / 无数据 提示行不参与铺满 */
    return;
  }
  const th = tbl.tHead ? tbl.tHead.offsetHeight : 0;
  const avail = wrap.clientHeight - th - 2;  /* 减掉表格上下边框，避免差 1~2px 撑出滚动条 */
  if (avail <= 0) return;
  const h = Math.floor(avail / n);
  if (h < 34){                                /* 可视区太矮：回到自然行高，允许表格内滚动 */
    tbl.style.removeProperty('--rowh');
    return;
  }
  tbl.style.setProperty('--rowh', Math.min(h, 120) + 'px');
}
window.addEventListener('resize', function(){ if (IDX) fitRows(); });
window.addEventListener('load', function(){ if (IDX) fitRows(); });

/* 有数据时版本可用度/构建成功率单元格：百分比 + 灯点，title 里带逐日明细 */
function rateCell(light, title, rate){
  const t = title ? ' title="' + rfEsc(title) + '"' : '';
  if (!light || light === '无数据') return '<span class="rate-line"' + t + '><span class="pct">--</span></span>';
  return '<span class="rate-line"' + t + '><span class="pct">' + fmtPct(rate) + '</span>'
       + dotOf(light) + '</span>';
}

/* 待分析流水线数：0 就写 0；大于 0 才是可点数字（同旧版 .lnk），点进二级页看该组件的流水线清单 */
function numCell(n, comp, dim, q){
  if (!n) return '0';
  return '<a class="lnk" href="' + DETAIL_PAGE + '?comp=' + encodeURIComponent(comp)
    + '&dim=' + dim + q + '">' + n + '</a>';
}

/* 「流水线数」：蓝色可点数字（0 不可点），点开弹窗看该组件全部流水线的
   版本可用度与构建成功率（数据来自 render 已经算好的 r.pipes，不额外取数） */
function pipeCountCell(r, i){
  if (!r.pipeCount) return '0';
  return '<span class="lnk" data-pipes="' + i + '"'
    + ' title="点击查看该组件 ' + r.pipeCount + ' 条流水线的版本可用度与构建成功率'
    + (r.pipeNoRun ? '（其中 ' + r.pipeNoRun + ' 条区间内无执行记录，显示 -- 且不纳入统计：'
        + rfEsc(r.noRunNames.join('、')) + '）' : '') + '">'
    + r.pipeCount + '</span>';
}

/* 组件行：严格对齐旧版 6 列，不做任何展开（明细一律在二级页看） */
function prodRow(r, i, from, to){
  const q = '&from=' + encodeURIComponent(from) + '&to=' + encodeURIComponent(to);
  return '<tr>'
    + '<td class="c-name">' + rfEsc(r.name) + '</td>'
    + '<td class="c-rate">' + rateCell(r.vaLight, r.vaTitle, r.vaRate) + '</td>'
    + '<td class="c-rate">' + rateCell(r.bsLight, r.bsTitle, r.bsRate) + '</td>'
    + '<td class="c-num">' + pipeCountCell(r, i) + '</td>'
    + '<td class="c-num">' + numCell(r.badVA, r.name, 'va', q) + '</td>'
    + '<td class="c-num">' + numCell(r.badBS, r.name, 'bs', q) + '</td>'
    + '</tr>';
}

/* 表下统计口径 + 页脚：口径本体在 CALIBER_JS（按条展示，只在首页出现），
   这里只负责「确认它已在位」+ 画页脚，避免两处文案各写一遍后走岔 */
function paintNotes(docs, rows, from, to){
  let badVA = 0, badBS = 0, pipes = 0, comps = 0, red = 0;
  rows.forEach(function(r){
    badVA += r.badVA; badBS += r.badBS; pipes += r.pipeCount;
    if (r.pipeCount) comps += 1;
    if (r.vaLight === '红' || r.bsLight === '红') red += 1;
  });
  const note = document.getElementById('tnote');
  if (note && !note.innerHTML) note.innerHTML = caliberHTML();
  const foot = document.getElementById('foot');
  if (foot) foot.innerHTML =
    '生成时间 <span class="mono">' + rfEsc(nz(IDX && IDX.generatedAt)) + '</span> ｜ '
    + '统计区间 <span class="mono">' + rfEsc(from) + ' ~ ' + rfEsc(to) + '</span>（归档 '
    + docs.length + ' 天）｜ 开源组件 ' + rows.length + ' 个（有待分析流水线 ' + comps
    + ' 个，红灯 ' + red + '）｜ 待分析流水线 ' + pipes + ' 条（版本可用度 ' + badVA
    + ' · 构建成功率 ' + badBS + '）'
    /* 区间内一天归档都没有：说明白是「这天没跑数」，指标才显示 -- */
    + (docs.length ? '' : ' ｜ <b>该区间内无归档日</b>（非工作日不跑数，指标显示 --）');
}

/* ================= 「流水线数」弹窗：该组件全部流水线的版本可用度 / 构建成功率 =================
   数据不用现取：render() 已按当前统计区间把每条流水线重算好放进 rows[i].pipes，
   这里只管排版。骨架与二级页同一套（外框定高不滚，滚动条只出现在表格里）。 */
/* 弹窗是一个单例外框（#mrec），所以「二级弹窗」用栈实现：
   MODAL_UP 里压着被盖住的那一层（标题 + 正文），点「← 返回」原样贴回来。
   这样弹窗里再开弹窗不会出现两层遮罩，也不会把一级弹窗的内容弄丢。
   MODAL_SEQ 是异步回填的守卫：层级一变（返回/关闭）就把还没回来的运行历史结果丢掉，
   避免慢请求回来时把人已经切走的弹窗又盖掉。 */
let MODAL_UP = [];
let MODAL_SEQ = 0;
function paintModal(title, html){
  const m = document.getElementById('mrec');
  const t = document.getElementById('mttl');
  const b = document.getElementById('mbody');
  if (!m || !b) return;
  if (t) t.innerHTML = title || '';
  b.innerHTML = html || '';
  const bk = document.getElementById('mback');
  if (bk) bk.hidden = !MODAL_UP.length;
  m.classList.add('on');
}
function openModal(title, html){
  MODAL_UP = [];
  MODAL_SEQ += 1;
  paintModal(title, html);
}
function openModal2(title, html){
  const t = document.getElementById('mttl'), b = document.getElementById('mbody');
  MODAL_UP.push({title: t ? t.innerHTML : '', html: b ? b.innerHTML : ''});
  MODAL_SEQ += 1;
  paintModal(title, html);
}
function modalBack(){
  if (!MODAL_UP.length) return;
  const prev = MODAL_UP.pop();
  MODAL_SEQ += 1;
  paintModal(prev.title, prev.html);
}
function closeModal(){
  MODAL_UP = [];
  MODAL_SEQ += 1;
  const m = document.getElementById('mrec');
  if (m) m.classList.remove('on');
}
/* 弹窗内的一列比率：百分比（不按灯色上色）+ 灯点（没有该维度/无执行记录则 --）。
   居中由 td.ctr 负责，这里只出内容，和首页组件级的 .rate-line 完全一致。 */
function ctrRate(rate, light, title){
  const t = title ? ' title="' + rfEsc(title) + '"' : '';
  if (rate === null || rate === undefined || isNaN(rate)) return '<span class="dim">--</span>';
  const l = light || lightByRate(rate, 'va');
  return '<span class="rate-line"' + t + '><span class="pct">'
    + fmtPct(rate) + '</span>' + dotOf(l) + '</span>';
}
/* 撞名兜底短名的展示统一走 lib/ui.py 的 plDisp（首页与二级页同一份规则，见 plKeyOf/plDisp） */
/* 一行 = 一条流水线：版本可用度（可用日/区间工作日）与构建成功率（编译通过/编译次数）。
   没有该维度（dims 里没有 va / bs）的显示 --：它们不参与该维度的比较，也不该被当成 0%。
   最后一列「执行记录」是入口：点「查看」在弹窗里再开一层，看这条流水线区间内的全部运行。 */
function pipesModal(i){
  const r = ROWS[i];
  if (!r) return;
  const list = (r.pipes || []).slice().sort(function(a, b){ return a.pipeline < b.pipeline ? -1 : 1; });
  if (!list.length){
    openModal('流水线｜' + rfEsc(r.name),
      '<div class="dim">该组件在当前统计区间内没有进入待分析集合的流水线。</div>');
    return;
  }
  const wdN = r.wdN || 0;
  /* 分母只数「真的能算出可用度」的流水线：区间内一次都没跑过的（无执行记录）显示 --，
     既不进这个平均、也不算条数，否则一条没跑过的流水线会把组件均值直接拉成 0% */
  const vaList = list.filter(function(p){ return p.dims.va && p.vaRate !== null && p.vaRate !== undefined; });
  const trs = list.map(function(p){
    const hasVA = !!p.dims.va, hasBS = !!p.dims.bs;
    const va = (hasVA && p.vaRate !== null && p.vaRate !== undefined) ? fmtPct(p.vaRate) : '--';
    const bs = (hasBS && p.bsRate !== null && p.bsRate !== undefined) ? fmtPct(p.bsRate) : '--';
    const vaT = !hasVA ? '该流水线在区间内没有版本可用度维度'
      : (p.noRun || p.vaRate === null || p.vaRate === undefined)
        ? '该流水线在区间内没有执行记录 → 显示 --，不纳入统计'
        : ('可用日 ' + p.vaA + ' / 有执行记录 '
           + ((p.vaDen === undefined || p.vaDen === null) ? wdN : p.vaDen) + ' 天 = ' + va);
    const bsT = hasBS
      ? ('编译通过 ' + p.bsP + ' / 编译次数 ' + p.bsC + ' = ' + bs)
      : '该流水线在区间内没有构建成功率维度';
    const execT = p.noRun
      ? '该流水线在区间内没有执行记录'
      : '查看该流水线在区间 ' + r.from + ' ~ ' + r.to + ' 内的全部执行记录';
    return '<tr>'
      + '<td class="cw">' + rfEsc(plDisp(p))
        + ((p.name && p.name !== p.pipeline)
            ? '<div class="dim" style="font-size:11px">' + rfEsc(p.name) + '</div>' : '')
        + (p.noRun ? '<div class="dim" style="font-size:11px">区间内无执行记录 · 不纳入统计</div>' : '')
        + '</td>'
      + '<td class="ctr">' + rfEsc(p.platform || '--') + '</td>'
      + '<td class="ctr">'
        + (hasVA ? ctrRate(p.vaRate, p.vaLight || lightByRate(p.vaRate, 'va'), vaT)
                 : '<span class="dim">--</span>') + '</td>'
      + '<td class="ctr">'
        + (hasBS ? ctrRate(p.bsRate, p.bsLight || lightByRate(p.bsRate, 'bs'), bsT)
                 : '<span class="dim">--</span>') + '</td>'
      + '<td class="ctr">'
        + (p.noRun ? '<span class="dim" title="' + rfEsc(execT) + '">--</span>'
                   : '<span class="lnk" data-exec="' + i + '" data-pipekey="'
                     + rfEsc(p.key) + '" title="' + rfEsc(execT) + '">查看</span>')
        + '</td>'
      + '</tr>';
  }).join('');
  openModal('流水线（' + list.length + ' 条）｜' + rfEsc(r.name),
    '<div class="mtbl"><table class="rca"><colgroup><col style="width:38%"><col style="width:14%">'
    + '<col style="width:15%"><col style="width:15%"><col style="width:18%"></colgroup>'
    + '<thead><tr><th>流水线</th><th>平台</th><th>版本可用度</th><th>构建成功率</th>'
    + '<th>执行记录</th></tr></thead>'
    + '<tbody>' + trs + '</tbody></table></div>'
    + '<div class="mrec-note">区间 ' + rfEsc(r.from) + ' ~ ' + rfEsc(r.to)
    + ' · 版本可用度 = 可用日 ÷ 该流水线有执行记录的天数（当天有运行开始或结束才算，没启动、没跑过的日子不计入）'
    + '（本组件 ' + fmtPct(r.vaRate) + ' = 各流水线之和 ÷ ' + vaList.length + ' 条可统计流水线'
    + (list.length > vaList.length ? '，另有 ' + (list.length - vaList.length) + ' 条无执行记录已排除' : '')
    + '）'
    + ' ｜ 构建成功率 = Σ编译通过 ÷ Σ编译次数（本组件 ' + fmtPct(r.bsRate) + '）'
    + ' ｜ 该流水线没有的维度显示 -- ｜ 点「查看」看该流水线区间内的全部执行记录。</div>');
}

/* ===== 二级弹窗：一条流水线在统计区间内的全部执行记录（原二级页区块头的入口搬到这里） =====
   数据源与原「流水线执行记录」完全一致：归档每天的 pipeline.json 里带着一份整区间 runHistory，
   逐日读、按构建号去重即可；区间内每一次运行逐条平铺（不按天汇总），按结束时间倒序。 */
/* 运行状态色标：成功（COMPLETED/SUCCESS/SUCCEEDED）绿、失败红、其它灰 */
function statusTag(s){
  const v = String(s || '').toUpperCase();
  if (v === 'FAILED' || v === 'FAILURE') return '<span class="tag red">' + rfEsc(s) + '</span>';
  if (v === 'SUCCESS' || v === 'SUCCEEDED' || v === 'COMPLETED') return '<span class="tag green">' + rfEsc(s) + '</span>';
  return '<span class="tag">' + rfEsc(s || '--') + '</span>';
}
/* 起止时间统一成 yyyy-MM-dd hh:mm:ss：openLiBing 归档本就是这个格式，
   社区（openGauss / openEuler）的 Jenkins 归档给的是 ISO 8601（2026-11-13T03:05:02），
   中间那个 T 只是「日期」与「时间」的分隔符、没有业务含义，展示时换成空格，与其它流水线对齐。 */
function tm(s){
  const v = String(s == null ? '' : s).replace('T', ' ');
  return v ? v.slice(0, 19) : '--';
}
/* 执行记录表：8 列（编号/状态/失败类型/版本可用/编译/用例/起止时间），构建号可直接跳 openLiBing */
function execTable(runs, r, p){
  const from = r.from, to = r.to;
  if (!runs.length){
    return '<div class="dim">该流水线在区间 ' + rfEsc(from) + ' ~ ' + rfEsc(to)
      + ' 内没有读到运行历史（归档缺少 runHistory）。</div>';
  }
  runs.sort(function(a, b){ return String(b.end || '').localeCompare(String(a.end || '')); });
  const okDay = {}, bsDay = {};
  runs.forEach(function(x){
    if (!x.day) return;
    if (x.iva === true) okDay[x.day] = 1;
    if (x.bc !== null && x.bc !== undefined && x.bc !== ''
        && (Number(x.bpc) || 0) >= Number(x.bc)) bsDay[x.day] = 1;
  });
  const rows = runs.map(function(x){
    const bn = x.url
      ? '<a href="' + rfEsc(x.url) + '" target="_blank" rel="noopener" title="在 openLiBing 查看构建 #'
        + rfEsc(nz(x.rn)) + '">#' + rfEsc(nz(x.rn)) + '</a>'
      : '<span class="mono">#' + rfEsc(nz(x.rn)) + '</span>';
    const va = (x.iva === true) ? '可用'
      : ((x.iva === false) ? '<span class="dim">不可用</span>' : '<span class="dim">--</span>');
    const bs = (x.bc === null || x.bc === undefined || x.bc === '')
      ? '<span class="dim">--</span>' : (Number(x.bpc) || 0) + '/' + Number(x.bc);
    const cpr = (x.cpr === null || x.cpr === undefined || x.cpr === '')
      ? '<span class="dim">--</span>' : Number(x.cpr).toFixed(1) + '%';
    /* 失败类型 = 服务端 failedTaskType（如「用例失败」「编译失败」）；执行成功时为 null */
    const ftt = (x.ftt === null || x.ftt === undefined || x.ftt === '' || x.ftt === '流水线未失败')
      ? '<span class="dim">--</span>' : rfEsc(String(x.ftt));
    return '<tr>'
      + '<td class="nowrap">' + bn + '</td>'
      + '<td class="nowrap">' + statusTag(x.status) + '</td>'
      + '<td class="nowrap dim">' + ftt + '</td>'
      + '<td class="nowrap">' + va + '</td>'
      + '<td class="nowrap">' + bs + '</td>'
      + '<td class="nowrap dim">' + cpr + '</td>'
      + '<td class="nowrap dim">' + rfEsc(tm(x.start)) + '</td>'
      + '<td class="nowrap dim">' + rfEsc(tm(x.end)) + '</td>'
      + '</tr>';
  }).join('');
  const vaDen = Number(p.vaDen) || 0;
  return '<div class="mtbl"><table class="rca"><colgroup><col style="width:8%"><col style="width:9%">'
    + '<col style="width:12%"><col style="width:8%"><col style="width:11%">'
    + '<col style="width:9%"><col style="width:21.5%"><col style="width:21.5%"></colgroup>'
    + '<thead><tr><th>流水线编号</th><th>状态</th><th>失败类型</th><th>版本可用</th>'
    + '<th>编译成功/次数</th><th>用例通过率</th><th>开始时间</th><th>结束时间</th></tr></thead>'
    + '<tbody>' + rows + '</tbody></table></div>'
    + '<div class="mrec-note">区间 ' + rfEsc(from) + ' ~ ' + rfEsc(to)
    + ' · 区间内每一次运行逐条平铺（不按天汇总），按结束时间倒序 · 可用日 '
    + Object.keys(okDay).length + '/' + vaDen + ' 个执行日 · 编译全通过 '
    + Object.keys(bsDay).length + ' 天 · 运行来自归档 pipeline.json 的 runHistory'
    + '（按执行结束时间归属日期）· 执行成功也逐条列出 · 点构建号直接在 openLiBing 打开该次执行。</div>';
}
/* 点「查看」：按流水线 key 逐日找到归档目录，再按需读 pipeline.json 取运行历史
   （首页首屏只算比率、不带运行明细，所以这里现取；多个归档日各带同一份整区间历史，按 rn 去重）

   入参用**身份键**而不是弹窗里的排序下标：pipesModal 渲染的是 r.pipes 按短名排序后的副本，
   下标与 r.pipes 的原始顺序不一致，之前按下标取就会「点 A 打开 B」。 */
function openExecOf(i, key){
  const r = ROWS[i];
  const p = (r && r.pipes)
    ? r.pipes.filter(function(x){ return x.key === key; })[0] : null;
  if (!r || !p) return;
  const label = plDisp(p) || p.key || '流水线';
  const from = r.from, to = r.to;
  const days = ((IDX && IDX.days) || []).filter(function(d){ return d.date >= from && d.date <= to; });
  if (!days.length){
    openModal2('执行记录｜' + rfEsc(label),
      '<div class="dim">该区间内没有归档日（' + rfEsc(from) + ' ~ ' + rfEsc(to) + '）。</div>');
    return;
  }
  openModal2('执行记录｜' + rfEsc(label), '<div class="loading">正在读取运行历史…</div>');
  const seq = MODAL_SEQ;
  const runs = [], seen = {};
  let chain = Promise.resolve();
  days.forEach(function(d){
    chain = chain.then(function(){
      return jget(ARCH_ROOT + d.path + '/_day.json').then(function(day){
        let pl = null;
        /* 与 render() 同一套身份：组件 + pipelineId（老归档退回短名） */
        (day.pipelines || []).forEach(function(x){
          if (x.component + '|' + (x.pipelineId || x.pipeline) === p.key) pl = x;
        });
        if (!pl || !pl.path) return;
        return jpipe(pl.path).then(function(pp){
          (pp.runs || []).forEach(function(h){
            const rn = String(h.rn);
            if (!rn || rn === 'undefined' || seen[rn]) return;
            seen[rn] = 1;
            const end = String(h.end || '');
            const dy = end.slice(0, 10);
            /* 归档里带的是整区间历史，可能越过当前统计区间：先按区间筛一遍 */
            if (dy && (dy < from || dy > to)) return;
            runs.push({rn: h.rn, start: h.start, end: end, day: dy, status: h.status,
                       url: h.url, iva: h.iva, bc: h.bc, bpc: h.bpc, cpr: h.cpr, ftt: h.ftt});
          });
        }).catch(function(){ /* 老归档没有 runHistory：下面统一提示 */ });
      }).catch(function(){ /* 单日读取失败跳过，不影响其它日 */ });
    });
  });
  chain.then(function(){
    if (seq !== MODAL_SEQ) return;   /* 已经返回上一级或关掉弹窗：丢弃过期结果 */
    paintModal('执行记录（' + runs.length + ' 次运行）｜' + rfEsc(label), execTable(runs, r, p));
  });
}

/* 首页表格的点按入口：「流水线数」数字（可点）→ 流水线弹窗 → 「查看」→ 执行记录二级弹窗；
   弹窗关闭（关闭按钮 / 点遮罩 / Esc），二级弹窗按「← 返回」或 Esc 先回上一层 */
document.addEventListener('click', function(ev){
  const t = ev.target;
  if (t && t.closest){
    const ex = t.closest('[data-exec]');
    if (ex){
      ev.preventDefault();
      openExecOf(Number(ex.getAttribute('data-exec')),
                 ex.getAttribute('data-pipekey') || '');
      return;
    }
    const el = t.closest('[data-pipes]');
    if (el){
      ev.preventDefault();
      pipesModal(Number(el.getAttribute('data-pipes')));
      return;
    }
  }
  if (t && t.id === 'mback'){ modalBack(); return; }
  if (t && (t.id === 'mrec' || t.id === 'mclose')) closeModal();   /* 遮罩/关闭按钮都关，点表格内容不关 */
});
document.addEventListener('keydown', function(ev){
  if (ev.key !== 'Escape') return;
  if (MODAL_UP.length) modalBack(); else closeModal();   /* Esc 先退出二级弹窗，再关整层 */
});

/* 说明：首页表格不做任何展开/下钻；「流水线数」点开看该组件全部流水线的两个比率与执行记录，
   「待分析流水线数」是可点数字，点进二级页看该组件的执行记录根因明细（无中间层） */

boot();
""".replace("__DETAIL__", DETAIL_NAME)

# ---------------------------------------------------------------- 二级页
# 顶部顺序与首页**逐像素一致**：品牌行 → 公共日期筛选条 → 才是本页自己的东西
# （返回总览 / 组件标题 / 面包屑 / 概要）。两页共用同一份 header + toolbar，
# 且都贴在最前，所以从首页点进来时「统计区间」整条不换位置、不跳动，
# 语义始终是「整个系统的数据筛选」，而不是某一页的局部筛选。
DETAIL_BODY = (
    '<div class="wrap">\n'
    '  ' + ui.header() + '\n'
    '  ' + ui.toolbar() + '\n'
    '  <a class="chip" href="' + INDEX_NAME + '" id="back">← 返回总览</a>\n'
    '  <a class="chip" href="pr.html">triton-ascend PR 数据 →</a>\n'
    '  <h1 id="ttl">组件根因明细</h1>\n'
    '  <div class="crumb" id="crumb"></div>\n'
    '  <p class="meta" id="meta">正在读取归档…</p>\n'
    '  <div class="dist" id="dist"></div>\n'
    '  <div id="recs"><div class="loading">正在读取归档记录…</div></div>\n'
    '</div>\n'
    '<div class="modal" id="mrec">\n'
    '  <div class="modal-box">\n'
    '    <div class="modal-hd"><span id="mttl"></span>'
    '<button type="button" id="mclose">关闭</button></div>\n'
    '    <div id="mbody"></div>\n'
    '  </div>\n'
    '</div>'
)

# 版式对齐旧报告：h3 区块头（浅蓝底）+ table.rca（全边框/表头浅灰/斑马纹/列宽固定）
DETAIL_EXTRA_CSS = """
#back{margin:2px 0 8px;align-self:flex-start}
/* 统计口径只在首页出现（二级页不再重复一份，避免两处口径各写一遍后走岔） */
.crumb{margin:2px 0 4px;font-size:13px;color:#666}
.crumb b{color:#222}
/* 面包屑只有两层：总览（可点）› 组件（维度），中间那层流水线清单已下线 */
.crumb a{color:var(--acc)}
.crumb .sep{color:#c2cad6;margin:0 5px}
.grow{flex:1 1 auto}

/* ---------- 组件区块头（同旧报告 h3） ---------- */
.subview h3{font-size:15px;font-weight:600;color:#16233a;line-height:1.6;
  margin:22px 0 8px;padding:8px 12px;background:#f0f5ff;border-radius:6px}
.subview h3:first-of-type{margin-top:8px}
.subview h3 .hist{font-size:12px;font-weight:400;margin-left:8px}
.subview h3 .rate-line{margin-left:8px;font-weight:400;font-size:13px}
/* 区块头的「执行记录」入口：紧跟在「可用 N/M 工作日」之后，字号同正文、可点 */
.subview h3 .hd-exec{margin-left:10px;font-size:12.5px;font-weight:400;white-space:nowrap}

/* ---------- 无失败记录时的醒目提醒（不是灰字空态） ----------
   最常见的「有运行、但一条失败记录都没有」其实是「执行成功、测试用例通过率不是 100%」：
   以前这里只写一句灰字「该区间内没有需要根因分析的失败执行记录」，用户必须点开执行记录
   才知道可用度为什么是 0%。现在把结论直接摆在区块头下面，加底色 + 左侧色条。 */
.nofail{margin:10px 0 4px;padding:11px 14px;border-radius:6px;font-size:13px;line-height:1.7;
  background:#fff8e8;border:1px solid #f0ce8e;border-left:4px solid #d98b1f;color:#5c4310}
.nofail .nf-hd{display:block;font-weight:600;font-size:13.5px;color:#9a5b00;margin-bottom:1px}
.nofail b{color:#b8400e}
.nofail .hist{font-size:12px;font-weight:400;margin-left:6px;white-space:nowrap}
/* 没有任何运行 / 归档无运行历史：中性提示，不喊黄 */
.nofail.mute{background:#f7f9fc;border:1px solid #e3e6ea;border-left:4px solid #c8d2e0;color:#5b6779}
.nofail.mute .nf-hd{color:#3b4a60}

/* ---------- 根因明细表（同旧报告 table.rca）：9 列固定列宽，行宽不随内容变化 ---------- */
table.rca{table-layout:fixed;width:100%;border-collapse:collapse;font-size:13px;margin:8px 0 4px}
table.rca th,table.rca td{border:1px solid #e3e6ea;padding:7px 9px;text-align:left;
  vertical-align:top;line-height:1.55;word-break:break-word;overflow-wrap:anywhere}
table.rca th{background:#f7f9fc;font-weight:600;white-space:nowrap;color:#3b4a60;text-align:center}
table.rca tbody tr:nth-child(even) td{background:#fafbfc}
/* 根因明细表每行后面挂了一条「展开行」（rc-det），会把上面的奇偶计数整体打乱，
   所以这张表的斑马纹只按主行自己的奇偶类（rc-alt）上色，展开行另有底色 */
table.rca.rca-rec tbody tr:nth-child(even) td{background:transparent}
table.rca.rca-rec tbody tr.rc-main.rc-alt td{background:#fafbfc}
table.rca.rca-rec tbody tr.rc-main:hover td{background:#f2f7ff}
table.rca td.err-cell{font-size:12.5px}
/* 错误信息列只剩一个「证据」链接：单独一行、永远可点，不再有被截断的 AI 结论正文 */
table.rca td.err-cell .ev-lnk{display:inline-block}
/* 根因 / 建议列：内容按行分点渲染（richPoints 输出 .pt / .pt-lead / .pt-item / .pt-sub / .pt-gap），
   每条问题单独一行，避免一大段文字糊在一起看不出「哪有问题」 */
table.rca td .pt{display:block;line-height:1.6}
table.rca td .pt-hd{display:block;font-weight:600;color:#16233a;line-height:1.6}
table.rca td .pt-gap{display:block;height:5px}
/* 【结论】行：整体加粗，标签用深色块标出来，一眼看到结论在哪 */
table.rca td .pt-lead{display:block;line-height:1.6;font-weight:600;color:#16233a}
table.rca td .pt-lead .pt-tag{color:#1a4b8c;margin-right:2px}
/* 一、二、三 分点：小标题单独一行加粗，编号与正文之间留白 */
table.rca td .pt-item{display:block;line-height:1.6;font-weight:600;color:#16233a;margin-top:3px}
/* 分析过程 / 验证方式：事实行左缩进 + 标签降一级灰度，与上面小标题形成层级 */
table.rca td .pt-sub{display:block;line-height:1.6;padding-left:14px;color:#39485c}
table.rca td .pt-sub .pt-lab{color:#8a94a6}
/* 「第一类（…）：正文」这种散文段：冒号前的短语当小标题加粗，正文直接跟在同一行，
   保证一段话读下来是连贯叙述，而不是被拆成「标题 + 正文」两行 */
table.rca td .pt-sec{display:block;line-height:1.7;color:#39485c}
table.rca td .pt-sec .pt-sec-hd{font-weight:600;color:#16233a;margin-right:2px}
/* 弹窗里的长文本（用例失败详情 / 日志证据原文）：整行完整输出，不截断、不溢出 */
table.rca td.expr-txt{white-space:pre-wrap;word-break:break-word;overflow-wrap:anywhere;
  font-family:Consolas,Menlo,monospace;font-size:12px;line-height:1.5}
/* 第 2 列「流水线编号」里的构建号链接不折行（第 1 列是组件级的「流水线」名） */
table.rca td:nth-child(2) a{white-space:nowrap}
/* 第 1 列「流水线」：组件级才有的列，一条记录归属哪条流水线，名字长可折行 */
table.rca td.pl-cell{white-space:normal;overflow-wrap:anywhere;color:#16233a}
/* 「任务成功但用例未 100%」的补行：用例列只给通过率，橙色提醒「这次没跑满」 */
table.rca .cpr-cell{color:#d46b08;font-weight:600;white-space:nowrap}
/* 第 2 列「流水线编号」：一条记录只有一个构建号，不折行；错误/失败标记另起一行 */
table.rca td.bldg-cell{white-space:normal}
table.rca td.bldg-cell .bldgs a,
table.rca td.bldg-cell .bldgs span.mono{display:inline-block;margin:0 8px 2px 0;white-space:nowrap}
.exec-sub{display:block;color:#999;font-size:11px;margin-top:2px}
.badge{display:inline-block;padding:1px 9px;border-radius:10px;color:#fff;font-size:12px;vertical-align:1px}
.badge.green{background:#52c41a}.badge.yellow{background:#faad14}
.badge.red{background:#f5222d}.badge.gray{background:#999}

/* ---------- 弹窗骨架：两个入口共用（失败用例 / 日志证据） ----------
   版式固定成「标题 → 表格区（唯一滚动区）→ 脚注」三段：
   外框只做定高、overflow:hidden，所以弹窗自己永不出滚动条；
   纵向滚动条贴着表格右边缘、横向滚动条贴着表格底边，滚动的始终是表格本身。 */
.modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:50;
  align-items:center;justify-content:center;padding:24px 16px;overflow:hidden}
.modal.on{display:flex}
.modal-box{background:#fff;border-radius:10px;width:min(1280px,96vw);
  max-height:calc(100vh - 48px);overflow:hidden;
  padding:16px 20px;box-shadow:0 8px 30px rgba(16,32,64,.2);
  display:flex;flex-direction:column}
.modal-hd{display:flex;justify-content:space-between;align-items:center;gap:10px;flex:0 0 auto;
  font-weight:600;margin-bottom:10px;padding-bottom:8px;border-bottom:1px solid var(--line2)}
.modal-hd button{background:#fff;border:1px solid #ccc;border-radius:6px;padding:3px 12px;cursor:pointer;font:inherit}
/* 弹窗正文 = 表格区（可滚，吃掉全部剩余高度）+ 脚注（固定不动） */
#mbody{flex:1 1 auto;min-height:0;display:flex;flex-direction:column}
/* 表格独占的滚动容器：纵向滚动条在表格右侧、横向滚动条在表格下方，弹窗本身不滚 */
.mtbl{flex:1 1 auto;min-height:0;overflow:auto}
.mtbl table.rca{margin:0}
/* 表头吸顶：表格内滚动时列名始终可见 */
.mtbl table.rca thead th{position:sticky;top:0;z-index:2;background:#f7f9fc;text-align:center;
  box-shadow:inset 0 -1px 0 #e3e6ea,inset 0 1px 0 #e3e6ea}
/* 弹窗表格里的长内容列：一律在列宽内换行（长路径/用例名没有空格也能断），
   绝不横向溢出到相邻列 —— 列宽由 colgroup 固定，行高随内容自然增长 */
table.rca td.cw{white-space:normal;word-break:break-all;overflow-wrap:anywhere}
/* 时间列：优先在「日期」和「时刻」之间的空格处折行，不要从毫秒中间断开 */
table.rca td.cw1{white-space:normal;word-break:normal;overflow-wrap:anywhere}
/* 定性分布：二级页字段，按执行记录计数 */
.dist{display:flex;align-items:center;flex-wrap:wrap;gap:6px;margin:8px 0 10px;
  padding:8px 12px;background:#fff;border:1px solid var(--line);border-radius:8px;font-size:12px}
.dist:empty{display:none}
.dist .dt{color:#374151;font-weight:600;margin-right:2px}
.dist .sp{flex:1 1 auto}
/* 根因表：开始/结束时间列字号比正文略大一点（13px 表内正文 → 12.5px 等宽，比原先 11px 好读） */
table.rca td.dt-c{font-size:12.5px;color:#16233a;letter-spacing:.2px}

/* ---------- 根因明细表：行内展开（根因 / 建议全文不再占列） ---------- */
/* 展开三角放最后一列（用户口径）：主行任何单元格都不再带行首小三角；
   整行可点（cursor:pointer 挂在主行上），末列给一个稍大的三角按钮 */
table.rca.rca-rec tbody tr.rc-main[data-exp-row]{cursor:pointer}
table.rca td.exp-cell{text-align:center;white-space:nowrap;vertical-align:middle}
table.rca td.exp-cell .exp-ic{display:inline-block;width:22px;height:22px;line-height:20px;
  border:1px solid #d9e0ea;border-radius:6px;background:#fff;
  color:var(--acc);cursor:pointer;user-select:none;font-weight:600;font-size:14px;
  transition:transform .12s ease,background .12s ease}
table.rca td.exp-cell .exp-ic:hover{background:#eef4ff;border-color:#bfd3fe;color:#0b63c5}
/* 展开行：colspan 撑满 10 列，浅底 + 左侧色条，与主行的斑马纹区分开 */
table.rca.rca-rec tbody tr.rc-det > td.rc-det-cell{background:#fbfcfe;
  border-left:3px solid #b9cdf0;padding:10px 14px}
table.rca.rca-rec tbody tr.rc-det[hidden]{display:none}
.rc-blk{margin:0 0 10px}
.rc-blk:last-child{margin-bottom:0}
.rc-hd{display:inline-block;margin-bottom:5px;padding:1px 8px;border-radius:9px;
  background:#eef4ff;color:#1a4b8c;font-size:12px;font-weight:600;line-height:17px}
/* 问题分类列：只放一个色标（参考仓的 10 类根因），一句话描述交给右边「定性」列 */
table.rca td.cls-cell{text-align:center}
table.rca td.cls-cell .ic-tag{display:inline-block;padding:0 8px;border-radius:9px;
  font-size:11.5px;font-weight:600;line-height:18px;white-space:nowrap}
table.rca td.cls-cell .ic-c0{background:#fff1f0;color:#a8071a}   /* 代码编译错误 */
table.rca td.cls-cell .ic-c1{background:#fff7e6;color:#ad6800}   /* 单元测试失败 */
table.rca td.cls-cell .ic-c2{background:#f9f0ff;color:#531dab}   /* 依赖/环境缺失 */
table.rca td.cls-cell .ic-c3{background:#e6f7ff;color:#0050b3}   /* 网络/下载失败 */
table.rca td.cls-cell .ic-c4{background:#fff0f6;color:#9e1068}   /* 资源不足 */
table.rca td.cls-cell .ic-c5{background:#f0f5ff;color:#1d39c4}   /* 超时 */
table.rca td.cls-cell .ic-c6{background:#e6fffb;color:#006d75}   /* 权限问题 */
table.rca td.cls-cell .ic-c7{background:#f6ffed;color:#237804}   /* 配置/参数错误 */
table.rca td.cls-cell .ic-c8{background:#f5f5f5;color:#434343}   /* 基础设施/平台 */
table.rca td.cls-cell .ic-c9{background:#fafafa;color:#8c8c8c}   /* 无法判定 */
/* 定性列：把「可能是哪几种情况」逐条列出来（大白话、不给百分比）；
   只有一条时不带编号，多条才按 1. 2. 3. 排 */
table.rca td.cat-cell .ct-item{line-height:1.55;color:#25324a}
table.rca td.cat-cell .ct-item + .ct-item{margin-top:3px}
table.rca td.cat-cell .ct-item.ct-num{position:relative;padding-left:14px}
table.rca td.cat-cell .ct-item.ct-num > .ct-k{position:absolute;left:0;top:0;
  color:#8a94a6;font-variant-numeric:tabular-nums}

.mrec-note{margin-top:6px;color:#999;font-size:12px;flex:0 0 auto}

/* ---------- 「执行记录」弹窗：按流水线分组、组内按日期倒序的可用度明细 ----------
   摘要行 + 图例行固定在弹窗顶部（不随内容滚），分组块放 .mtbl 里滚 */
.pexec-sum{flex:0 0 auto;font-size:12.5px;color:#5b6779;line-height:1.6}
.pexec-lg{flex:0 0 auto;display:flex;flex-wrap:wrap;align-items:center;gap:6px;
  font-size:12px;color:#8a94a6;margin:4px 0 8px}
.pexec-lg i{margin-left:12px}
.pexec-lg i:first-child{margin-left:0}
.pexec-lg i,.pexec-row i{display:inline-block;width:8px;height:8px;border-radius:50%;flex:0 0 auto}
.pexec-lg i.g,.pexec-row i.g{background:#52c41a}
.pexec-lg i.r,.pexec-row i.r{background:#f5222d}
.pexec-lg i.n,.pexec-row i.n{background:#d9d9d9}
.pexec-grp{border:1px solid #e3e6ea;border-radius:8px;margin:0 0 10px;overflow:hidden}
.pexec-hd{display:flex;align-items:center;justify-content:space-between;gap:12px;
  padding:7px 12px;background:#f7f9fc;font-size:13px;font-weight:600;color:#16233a}
.pexec-hd .pl{overflow-wrap:anywhere}
.pexec-hd .rt{font-weight:400;color:#3b4a60;white-space:nowrap;flex:0 0 auto}
.pexec-hd .rt .lnk{margin-left:10px;font-size:12px}
.pexec-row{display:flex;align-items:center;gap:10px;padding:5px 12px;font-size:12.5px;
  color:#3b4a60;border-top:1px solid #f0f2f5}
.pexec-row .dt{font-family:Consolas,"Courier New",monospace;color:#2b3547}
.pexec-row .rt{margin-left:auto;color:#6b7280;white-space:nowrap}
.pexec-row .rt b{color:#25324a;font-weight:600}
"""

DETAIL_JS = r"""
/* ================= 二级页：同口径区间 → 该流水线每天的归档目录 → 逐条执行记录 ================= */
/* 数据根：同首页，一律相对「当前文档」逐级探测 archive/_index.json，不写死域名和站点根 */
let BACK_TO = '../';
const ROOT_CANDIDATES = ['../', './', '../../', '../../../'];
function probeRoot(){
  let i = 0;
  function step(){
    if (i >= ROOT_CANDIDATES.length){
      return Promise.reject(new Error('archive/_index.json → HTTP 404（已依次试过：'
        + ROOT_CANDIDATES.join('  ') + '）'));
    }
    const cand = ROOT_CANDIDATES[i++];
    return jget(cand + 'archive/_index.json').then(function(x){
      BACK_TO = cand;            /* 命中后固定，后面所有归档请求都拼这个前缀 */
      return x;
    }).catch(function(e){
      if (isNotFound(e)) return step();
      throw e;
    });
  }
  return step();
}
const CATS = {
  compile_failure: ['编译失败', 'red'],
  test_failure:    ['用例失败', 'red'],
  env_not_ready:   ['用例未拉起', 'yellow'],
  job_failure:     ['任务失败', 'yellow'],
  cancelled:       ['运行被取消', ''],
  evidence_missing:['证据缺失', 'blue'],
  /* 归档里 verdict.category=success 的记录 = 任务执行成功、但用例没跑满 100%：
     不写英文 success（对看页面的人没信息量），直接说清是哪一类问题 */
  success:         ['用例未 100%', 'yellow'],
  unknown:         ['证据不足待确认', '']
};
const QS = new URLSearchParams(location.search);
/* 二级页只有一层：组件级根因明细（?comp=组件&dim=va|bs）。
   从首页「待分析流水线数」点进来就是这个页面 —— 中间那层「组件级流水线清单」已下线，
   组件下所有待分析流水线的失败执行记录合成一张表，靠「流水线」列区分是哪一条。
   老链接带 ?key=组件|流水线 的，取出组件名照旧渲染（不会 404 给一张空表）。 */
const COMP = QS.get('comp') || (QS.get('key') ? String(QS.get('key')).split('|')[0] : '');
const DIM = QS.get('dim') || '';        /* 维度过滤：va / bs；不带就是该组件的全部维度 */
const DIMNAME = {va: '版本可用度', bs: '构建成功率'};
const INDEX_PAGE = '__INDEX__';          /* 首页文件名（构建时注入） */
let DAYS = [];

function jget(p){
  return fetch(p, {cache: 'no-store'}).then(function(r){
    if (!r.ok) throw new Error(p + ' → HTTP ' + r.status);
    return r.json();
  });
}
/* 文件不存在（归档区还没跑过采集）与「读取失败」要分开：前者提示去采集，后者才是环境问题 */
function isNotFound(e){
  const m = String((e && e.message) || e);
  return /HTTP 40[34]/.test(m) || /\b(404|ENOENT)\b/.test(m) || /not\s*found/i.test(m);
}
/* ===== 一条流水线归档的统一读法 =====
   归档已从「9 份 JSON + records/run_*.json」合并成一份 pipeline.json（schema=pipeline/1）：
   运行历史在 steps.step_2_runs.runHistory、证据缺失清单在 evidence.missing、
   执行记录就是顶层 records[]（每条只少一层重复的 _doc 题头）。一次 fetch 全拿到。
   碰到还没合并的老归档（pipeline.json 不存在）自动退回「三份分开读」，页面不用分支。 */
function jpipe(dirPath){
  const base = BACK_TO + dirPath + '/';
  return jget(base + 'pipeline.json').then(function(doc){
    doc = doc || {};
    return {base: base, dir: dirPath, merged: true,
            runs: (((doc.steps || {}).step_2_runs || {}).runHistory) || [],
            missing: (((doc.evidence || {}).missing) || []),
            records: doc.records || []};
  }).catch(function(e){
    if (!isNotFound(e)) throw e;
    return Promise.all([
      jget(base + 'step_2_runs.json').catch(function(){ return {}; }),
      jget(base + '_manifest.json').catch(function(){ return {}; })
    ]).then(function(r){
      const s2 = r[0] || {}, mf = r[1] || {};
      return {base: base, dir: dirPath, merged: false,
              runs: s2.runHistory || [],
              missing: (((mf.evidence || {}).missing) || []),
              records: mf.records || []};
    });
  });
}
function nz(v){ return (v === null || v === undefined || v === '') ? '--' : String(v); }
function dotOf(l){ const m = {'绿': 'green', '黄': 'yellow', '红': 'red'}; return m[l] ? '<span class="dot ' + m[l] + '"></span>' : ''; }
/* 灯色不落库：归档只存数值，这里按比率就地判档（老归档的 vaLight/bsLight 只作兜底）。
   版本可用度 >=90% 绿 / >=80% 黄 / 否则红；构建成功率 100% 绿 / >=95% 黄 / 否则红 */
function lightByRate(v, kind){
  if (v === null || v === undefined || v === '' || isNaN(v)) return null;
  const r = Number(v);
  if (kind === 'bs') return r >= 1 ? '绿' : (r >= 0.95 ? '黄' : '红');
  return r >= 0.9 ? '绿' : (r >= 0.8 ? '黄' : '红');
}

/* ===== 一跳直达 + 原路返回 =====
   首页（组件级概览）→ 本页（组件级根因明细 ?comp=&dim=），中间那层「组件级流水线清单」已下线。
   链接显式带上 comp / dim / from / to：返回总览时维度与统计区间都保持不变（不用重新选日期）。 */
function rangeQS(from, to){
  return (from && to) ? 'from=' + encodeURIComponent(from) + '&to=' + encodeURIComponent(to) : '';
}
function levelQS(extra, from, to){
  const parts = [];
  Object.keys(extra || {}).forEach(function(k){
    if (extra[k] !== null && extra[k] !== undefined && extra[k] !== '') parts.push(k + '=' + encodeURIComponent(extra[k]));
  });
  const r = rangeQS(from, to);
  if (r) parts.push(r);
  return parts.length ? '?' + parts.join('&') : '';
}
function indexHref(from, to){
  return INDEX_PAGE + levelQS({}, from, to);
}
/* 只有一层：返回按钮永远回总览（区间不变），不再有「返回上一层清单」 */
function setBack(from, to){
  const el = document.getElementById('back');
  if (!el) return;
  el.href = indexHref(from, to);
  el.textContent = '← 返回总览';
  el.title = '返回首页组件级概览（统计区间不变）';
}
/* 面包屑：总览 › 组件（维度） */
function crumbHead(from, to){
  return '<a href="' + indexHref(from, to) + '">总览</a><span class="sep">›</span>';
}
function statusTag(s){
  const v = String(s || '').toUpperCase();
  if (v === 'FAILED' || v === 'FAILURE') return '<span class="tag red">' + rfEsc(s) + '</span>';
  /* openLiBing 的流水线状态用 COMPLETED 表示成功（SUCCESS/SUCCEEDED 是别的接口的叫法），
     三种都按绿色显示，和 FAILED（红）、CANCELED（灰）区分开 */
  if (v === 'SUCCESS' || v === 'SUCCEEDED' || v === 'COMPLETED') return '<span class="tag green">' + rfEsc(s) + '</span>';
  return '<span class="tag">' + rfEsc(s || '--') + '</span>';
}
function failBox(msg){
  const box = document.getElementById('recs');
  if (box) box.innerHTML = '<div class="err">' + msg + '</div>';
}

function boot(){
  if (!COMP){
    failBox('缺少参数。请从首页表格点击「待分析流水线数」进入该组件的根因明细。');
    return;
  }
  /* 先把返回按钮按层级摆好（数据回来之前也不要显示错层级） */
  setBack(QS.get('from') || '', QS.get('to') || '');
  probeRoot().then(function(idx){
    DAYS = (idx.days || []).slice().sort(function(a, b){ return a.date < b.date ? -1 : 1; });
    if (!DAYS.length){
      failBox(emptyArchiveMsg());
      return;
    }
    rfInit({min: DAYS[0].date, max: DAYS[DAYS.length - 1].date, onChange: load});
  }).catch(function(e){
    /* 归档区为空时 _index.json 本来就不存在（HTTP 404 / ENOENT）→ 提示去采集；
       file:// 直开被浏览器拦截（TypeError）才提示起本地服务 */
    if (isNotFound(e)){
      failBox(emptyArchiveMsg());
      return;
    }
    if (hostedSite()){
      failBox('<b>读取归档索引失败</b><div class="mono" style="margin-top:6px">' + rfEsc(String(e)) + '</div>'
        + '<div style="margin-top:8px">静态托管环境读不到 <span class="mono">archive/_index.json</span>，'
        + '请确认文件已提交、仓库根目录有 <span class="mono">.nojekyll</span>、Pages 已重新部署。</div>');
      return;
    }
    failBox('<b>读取归档索引失败</b><div class="mono" style="margin-top:6px">' + rfEsc(String(e)) + '</div>'
      + '<div style="margin-top:8px">file:// 直开会被浏览器拦截 fetch，请用 '
      + '<span class="mono">python run_pipeline.py --serve</span> 起本地服务后访问。</div>');
  });
}

function load(from, to){
  setBack(from, to);                     /* 返回按钮跟着当前区间走 */
  const box = document.getElementById('recs');
  box.innerHTML = '<div class="loading">正在读取归档记录…</div>';
  document.getElementById('ttl').textContent = COMP;
  const days = DAYS.filter(function(d){ return d.date >= from && d.date <= to; });
  if (!days.length){
    /* 这天没有归档（非工作日不跑数）：照样出区块头，比率 / 达标工作日一律 --，
       不把「昨天」悄悄换成别的日子，也不留一张空表让人以为数据丢了 */
    RUNS = [];
    CTX = {dirs: [], days: days, from: from, to: to, runs: []};
    renderHead(null, [], CTX);
    renderRecs([], CTX);
    return;
  }
  /* 1) 逐日找该组件下所有流水线的归档目录（同一条流水线在区间内可能每天一个目录，
        一个组件下也可能有多条流水线 —— 全部收进来，靠「流水线」列区分） */
  const dirs = [];
  let meta = null;
  let chain = Promise.resolve();
  days.forEach(function(d){
    chain = chain.then(function(){
      return jget(BACK_TO + d.path + '/_day.json').then(function(day){
        (day.pipelines || []).forEach(function(pl){
          if (pl.component !== COMP) return;
          if (DIM && (pl.dims || []).indexOf(DIM) < 0) return;
          dirs.push({date: d.date, path: pl.path, pl: pl});
          if (!meta) meta = {pl: pl, day: day, comp: COMP};
        });
      }).catch(function(){ /* 该日读取失败则跳过，不影响其它日 */ });
    });
  });
  chain.then(function(){
    if (!dirs.length){
      box.innerHTML = '<div class="empty">该区间内没有该组件'
        + (DIM ? '（' + rfEsc(DIMNAME[DIM]) + ' 维度）' : '') + '的归档记录（'
        + rfEsc(from) + ' ~ ' + rfEsc(to) + '）'
        + '<div class="dim" style="margin-top:6px">流水线只在被点灯的日期才会进入待分析集合。</div></div>';
      RUNS = [];
      CTX = {dirs: [], days: days, from: from, to: to, runs: []};
      renderHead(null, [], CTX);
      return;
    }
    /* 1a) 「流水线」列的展示名：**只有确实重名才补平台**。同组件下两条流水线的流水线名完全相同时
          （openUBMC 的 codearts / gitcode 两条 Nightly-CI-Version-pipeline_openUBMC），光看名字
          分不清是哪条，此时才在名字后补平台；没有重名的组件一律用原名，一个字都不加工。 */
    const dupNames = dupNameSet(dirs.map(function(d){ return (d || {}).pl || {}; }));
    /* 2) 逐目录读一次 pipeline.json（jpipe）：运行历史 + 证据缺失清单 + 执行记录一起到手。
          还没合并的老归档由 jpipe 自动退回「step_2_runs.json + _manifest.json」分开读，
          这里只看 pp.merged 决定记录是「已经在手里」还是「还要按 file 再 fetch 一条」。 */
    let recs = [];
    const runs = [];
    const runSeen = {};
    let chain2 = Promise.resolve();
    dirs.forEach(function(d){
      chain2 = chain2.then(function(){
        return jpipe(d.path).then(function(pp){
          /* 这条归档目录属于哪条流水线：组件级明细里多条流水线的运行 / 记录混在一张表，
             「流水线」列靠这个字段区分（归档目录 → _day.json 的 pipelines[]）。
             **身份键与展示名必须分开**：同组件下可能有两平台完全重名的流水线（openUBMC 的
             codearts / gitcode 两条 Nightly-CI-Version-pipeline_openUBMC），聚合、去重、分组
             一律用 plKeyOf 的身份键 —— 只按名字做键会把两条并成一条：组件级可用度变成其中一条
             的值，另一条的数据看起来「并到它名下」。展示名只在**确实重名**时才补平台。 */
          const plKey = plKeyOf(d.pl);
          const plName = plLabelOf(d.pl, dupNames);
          /* 2a) 运行历史：同一条流水线的每个归档日都带着同一份「整区间」runHistory，
                按「流水线 + 构建号」去重（不同流水线的构建号可能重号，不能只按 rn 去重）。
                每条运行都打上 __pl，后面补「任务成功但用例未 100%」的行时要知道它属于哪条流水线。
                **归档里那份 runHistory 覆盖整个采集区间**（例如 11-06 的归档里带着 11-06~11-12
                的全部运行）：这里必须先按当前统计区间按「执行结束时间」筛一遍，否则单日筛选时
                会把别的日期的运行补成根因表的行 —— 看起来就像「选了 11-10 却冒出别的日期的数据」。 */
          (pp.runs || []).forEach(function(h){
            const rn = String(h.rn);
            const key = plKey + '|' + rn;
            if (!rn || rn === 'undefined' || runSeen[key]) return;
            runSeen[key] = 1;
            const end = String(h.end || '');
            const day = end.slice(0, 10);
            if (day && (day < from || day > to)) return;
            runs.push({rn: h.rn, start: h.start, end: end, day: day,
                       status: h.status, url: h.url, iva: h.iva,
                       bc: h.bc, bpc: h.bpc, cpr: h.cpr, ftt: h.ftt,
                       __pl: plName, __plk: plKey});
          });
          /* 2b) 执行记录：合并后的归档里记录已经是完整的，老归档按清单里的 file 再逐条 fetch */
          const missing = new Set(pp.missing || []);
          let chain3 = Promise.resolve();
          (pp.records || []).forEach(function(r){
            /* 执行成功的运行不进根因表：它只是「流水线执行记录」里的一天历史运行，
               要看就点「流水线执行记录」；归档里仍保留这条轻量记录，这里只是不取、不渲染。
               分类字段老归档在记录顶层（轻量清单的 category），合并后的归档只在 verdict.category 里，
               两处都认，否则判不出来→成功记录会以「错误信息 -- / 定性 --」的空行混进表里。
               社区（Jenkins）归档里成功运行标的是 verdict.category='na' + status SUCCESS/ABORTED，
               与 openlibing 侧的 'success' 不同名，只认 'success' 会把这些成功记录原样漏进根因表，
               所以这里把两种标法一起认（aborted 同样不该进表）。
               唯一例外：这次运行确实跑出了失败用例（用例通过率 ≠ 100%）——「执行成功但用例没全过」
               值得单独一行，但不给展开（它没有根因可讲，问题就在用例本身）。 */
            const vcat = String(((r.verdict || {}).category || r.category || ''));
            const stUp = String(r.status || '').toUpperCase();
            if (vcat === 'success' || vcat === 'na' || stUp === 'SUCCESS' || stUp === 'ABORTED'){
              const ncf = (r.cases || []).filter(function(c){ return c && c.name; }).length;
              if (!ncf) return;
              r.__okRow = 1;
            }
            if (pp.merged){
              r.__base = pp.base;
              r.__dir = d.path;
              r.__missing = missing;
              r.__pl = plName;
              r.__plk = plKey;
              recs.push(r);
              return;
            }
            chain3 = chain3.then(function(){
              return jget(pp.base + r.file).then(function(rec){
                rec.__base = pp.base;
                rec.__dir = d.path;
                rec.__missing = missing;
                rec.__pl = plName;
                rec.__plk = plKey;
                recs.push(rec);
              }).catch(function(e){
                recs.push({__err: String(e), __base: pp.base, __dir: d.path, __missing: missing,
                           __pl: plName, __plk: plKey,
                           recordId: r.recordId, buildNumber: r.buildNumber, endedAt: r.endedAt,
                           category: r.category, tasks: [], cases: [], errorMessages: [], evidence: {files: [], missing: []}});
              });
            });
          });
          return chain3;
        });
      }).catch(function(){ /* 该日这条流水线读不到：跳过，不影响其它日 / 其它流水线 */ });
    });
    chain2.then(function(){
      /* 2b') 记录级维度过滤（用户口径：不影响当日该维度指标的数据不展示）：
             逐条失败记录用**当日切片**判它到底影响哪个维度，只留命中当前维度的行。
               va 命中 = 该流水线有 va 维度，且当日版本不可用（_day.json 的 vaDetail.availDays ≠ 1）
               bs 命中 = 该流水线有 bs 维度，且这条运行自身编译未全过（buildCount>0 且 buildPassCount<buildCount）
             口径与 steps/step_6_records.py 的 light.affects 完全一致（同一份当日切片）：
             归属为空的失败记录（当日两指标都不受影响，如达标日里的用例/环境类失败）
             从此不再以「定性 -- / 问题分类 --」的空行混进根因表。
             执行成功的补行（__okRow）与归档读取失败行（__err）不受此过滤。 */
      const plIdx = {};
      let hasVa = false, hasBs = false;
      dirs.forEach(function(d){
        const pl = d.pl || {};
        plIdx[d.date + '|' + plKeyOf(pl)] = pl;
        const ds = pl.dims || [];
        if (ds.indexOf('va') >= 0) hasVa = true;
        if (ds.indexOf('bs') >= 0) hasBs = true;
      });
      const effDim = DIM || ((hasBs && !hasVa) ? 'bs' : 'va');
      const runBy = {};
      runs.forEach(function(x){ runBy[(x.__plk || '') + '|' + nz(x.rn)] = x; });
      recs = recs.filter(function(r){
        if (r.__err || r.__okRow) return true;
        const stt = String(r.status || '').toUpperCase();
        /* 根因表只收失败 / 取消的运行：非失败的执行（成功、aborted）上面已判过一轮，
           这里再兜一道 —— status 命名与归档不一致时也不能以空行混进表里。 */
        if (stt !== 'FAILED' && stt !== 'CANCELED' && stt !== 'CANCELLED') return false;
        const pl = plIdx[String(r.date || '') + '|' + (r.__plk || '')] || {};
        const ds = pl.dims || [];
        const vd = pl.vaDetail || {};
        const run = runBy[(r.__plk || '') + '|' + nz(r.buildNumber)];
        const aff = [];
        if (ds.indexOf('va') >= 0 && vd.availDays != 1) aff.push('va');
        if (ds.indexOf('bs') >= 0 && run
            && typeof run.bc === 'number' && typeof run.bpc === 'number'
            && run.bc > 0 && run.bpc < run.bc) aff.push('bs');
        return aff.indexOf(effDim) >= 0;
      });
      /* 2c) 「任务执行成功、但用例通过率不是 100%」的运行补一行：
            它们在 runs 里（带 status / cpr），但不在 records 里（records 只收 FAILED / CANCELED），
            所以默认压根不会出现在根因表里。用户口径：这类只需要**标记任务成功 + 给出用例比例**，
            不做 AI 根因、不抓失败用例清单 —— 因此就地合成一条轻量记录。
            去重键是「流水线 + 构建号」：同一组件下不同流水线的构建号可能重号，
            同一次构建如果记录里已经有了（哪怕是失败记录）也不再补。 */
      const taken = {};
      recs.forEach(function(r){ taken[(r.__plk || '') + '|' + nz(r.buildNumber)] = 1; });
      runs.forEach(function(x){
        if (!isOkRun(x)) return;
        const cpr = cprOf(x);
        if (cpr == null || cpr >= 100) return;   /* 100% 通过的正常成功运行不进表 */
        const k = (x.__plk || '') + '|' + nz(x.rn);
        if (taken[k]) return;
        taken[k] = 1;
        recs.push(okRowOf(x));
      });
      /* 行序：**按结束时间降序**（最近结束的那次构建排最上面，和「从最新往回看」的排查习惯一致）。
         时间一律解析成毫秒再比，不做字符串比较——归档里的时间串有带毫秒/时区的、也有缺字段的，
         字符串比大小会错位。结束时间缺失的用开始时间兜底，两者都没有的排到最后；
         时间完全相同时按构建号降序，保证每次刷新顺序稳定。 */
      recs.sort(function(a, b){ return cmpByEndDesc(a, b); });
      runs.sort(function(a, b){ return String(a.end || '') < String(b.end || '') ? 1 : -1; });
      RUNS = runs;
      CTX = {dirs: dirs, days: days, from: from, to: to, runs: runs};
      renderHead(meta, recs, CTX);
      renderRecs(recs, CTX);
    });
  });
}

/* ---------- 组件级页面的维度口径 ----------
   这一页是**从哪个维度点进来的，就只讲哪个维度**：dim=va 只讲版本可用度，dim=bs 只讲构建成功率。
   比率、工作日达标数都按同一个维度取；另一个维度在这一页上完全不出现。 */
function dimOf(){
  if (DIM === 'bs') return 'bs';
  if (DIM === 'va') return 'va';
  /* 没带 dim（直接开链接）时：区间内该组件所有流水线都没有 va 维度才退 bs，否则按 va */
  let hasVa = false, hasBs = false;
  ((CTX || {}).dirs || []).forEach(function(d){
    const ds = (d.pl || {}).dims || [];
    if (ds.indexOf('va') >= 0) hasVa = true;
    if (ds.indexOf('bs') >= 0) hasBs = true;
  });
  return (hasBs && !hasVa) ? 'bs' : 'va';
}

/* 该流水线在区间内的逐日灯态：用于区块头的「可用 N/M 工作日」（按当前维度取灯）
   灯态由当天比率就地判档，归档里不再存灯色 */
function dayLights(ctx){
  const dim = DIM || '';
  const out = [];
  (ctx.dirs || []).forEach(function(d){
    const pl = d.pl || {};
    const ds = pl.dims || [];
    /* 区间内一次都没跑过的流水线（runsInPeriod = 0）：归档里那个 0.0 是「压根没跑」，
       不是「跑了但不可用」，拿它算灯会得到红灯 → 区块头显示 0.00%。
       这里直接不给灯（等同于没有数据），比率就会落到 --。 */
    if (!ranInPeriod(pl)){ out.push({date: d.date, light: null}); return; }
    const useBs = dim ? dim === 'bs' : (ds.indexOf('bs') >= 0 && ds.indexOf('va') < 0);
    const light = useBs
      ? (lightByRate(pl.bsRate, 'bs') || pl.bsLight)
      : (lightByRate(pl.vaRate, 'va') || pl.vaLight);
    out.push({date: d.date, light: light || null});
  });
  return out;
}
/* 区间重算：只统计「执行结束时间落在当前统计区间内」的运行，逐日判定是否达标。
   dim=va：比率 = 可用日 / **该流水线在区间内有执行记录的天数**（用户口径：当日任一条运行
           iva=true 即算当天可用；当日没启动 / 没跑过的日子不进分母，被取消的运行当天也不进）
   dim=bs：比率 = Σ编译通过 / Σ编译次数（该维度口径未改）
   区间一变，分子分母同时按新区间重算，不再沿用归档当日写死的比率。 */
function rangeStats(ctx, dim){
  ctx = ctx || {};
  const from = ctx.from || '', to = ctx.to || '';
  /* 该流水线在区间内到底跑没跑过（runsInPeriod 逐日为准）：没跑过就一律 --、
     不参与任何比率，避免把归档退回的看板 0.0 显示成「0.00%」 */
  const ran = (ctx.dirs || []).some(function(d){ return ranInPeriod(d.pl); });
  const inRange = (ctx.runs || []).filter(function(r){
    return r.day && r.day >= from && r.day <= to;
  });
  if (inRange.length){
    const byDay = {}, dayRuns = {};
    inRange.forEach(function(r){
      const d = byDay[r.day] || (byDay[r.day] = {runs: 0, ok: false, bc: 0, bpc: 0});
      d.runs += 1;
      if (r.iva === true) d.ok = true;
      if (r.bc !== null && r.bc !== undefined && r.bc !== ''){
        d.bc += Number(r.bc) || 0;
        d.bpc += Number(r.bpc) || 0;
      }
      (dayRuns[r.day] || (dayRuns[r.day] = [])).push(r);
    });
    const dayList = Object.keys(byDay).sort();
    let okDays = 0, bc = 0, bpc = 0;
    const dayOk = {};
    dayList.forEach(function(k){
      const d = byDay[k];
      const ok = (dim === 'bs') ? (d.bc > 0 && d.bpc >= d.bc) : !!d.ok;
      dayOk[k] = ok;
      if (ok) okDays += 1;
      bc += d.bc; bpc += d.bpc;
    });
    Object.keys(dayRuns).forEach(function(k){
      dayRuns[k].sort(function(a, b){ return String(a.end || '') < String(b.end || '') ? 1 : -1; });
    });
    /* dim=va 的分母 = 该流水线在区间内**有执行记录的天数**（没启动的日子不进分母）；
       dim=bs 的分母 = 区间内有运行的天数。这里只是组件级的兜底值，
       下面按流水线身份聚合后会覆盖成「各流水线的分子分母」。 */
    let total = dayList.length;
    let availDays = okDays;
    let rate = (dim === 'bs') ? (bc > 0 ? bpc / bc : null)
                              : (total ? Math.min(1, okDays / total) : null);
    /* 组件级版本可用度（用户口径）= 该组件各流水线版本可用度的**算术平均**
       （Σ流水线可用度 ÷ 流水线条数），与首页组件行同一个算法、同一套逐日重算。
       各流水线的分母不再相同（各条「有执行记录的天数」不一样），所以必须逐条算比率再平均，
       不能拿 Σ可用日 ÷ Σ有执行日；这样二级页区块头的数字才能和首页点进来的数字一眼对上。
       只统计带版本可用度维度的流水线（只做编译的流水线不该被算成 0%）；
       被取消的运行不计入（用户口径：canceled 与进行中都不算）。 */
    let pipeCount = 0;
    /* 各流水线的版本可用度分母 = **有执行记录的天数**，取自逐日归档的 vaDetail.daysInPeriod
       （当天只有 canceled 运行 / 没跑过的日子记 0）；归档没这份数据时退回区间内有运行的天数。 */
    const denByPipe = vaDaysDenByPipe(ctx);
    if (dim !== 'bs'){
      /* 维度表按**身份键**建：同组件下两平台重名的流水线（openUBMC 的 codearts / gitcode）
         用名字做键会互相覆盖，后一条的维度盖掉前一条，某条会被错误地当成「不做 va」而漏统计。 */
      const dimsOf = {};
      (ctx.dirs || []).forEach(function(d){
        const pl = d.pl || {};
        const k = plKeyOf(pl);
        if (!k || k === '|') return;
        const ds = dimsOf[k] || (dimsOf[k] = []);
        (pl.dims || []).forEach(function(x){ if (ds.indexOf(x) < 0) ds.push(x); });
      });
      const agg = {};
      inRange.forEach(function(r){
        if (runCanceled(r) || !r.day) return;
        const k = r.__plk || '--';
        if (dimsOf[k] && dimsOf[k].indexOf('va') < 0) return;
        const a = agg[k] || (agg[k] = {ok: {}, ran: {}});
        a.ran[r.day] = 1;
        if (r.iva === true) a.ok[r.day] = 1;
      });
      let num = 0, den = 0, rateSum = 0, rateN = 0;
      Object.keys(agg).forEach(function(k){
        const a = agg[k];
        pipeCount += 1;
        const okn = Object.keys(a.ok).length;
        const dn = (Number(denByPipe[k]) > 0) ? Number(denByPipe[k]) : Object.keys(a.ran).length;
        num += okn; den += dn;
        /* 组件级版本可用度 = 各流水线版本可用度的**算术平均**（用户口径：与首页组件行完全一致）。
           各流水线分母各不相同（各有各的执行天数），所以必须逐条算比率再平均 ——
           否则区块头会和首页点进来的数字对不上。 */
        if (dn > 0){ rateSum += okn / dn; rateN += 1; }
      });
      if (pipeCount){
        total = den; availDays = num;
        rate = rateN > 0 ? Math.min(1, rateSum / rateN) : null;
      }
    }
    return {source: 'runs', ran: ran, runs: inRange, byDay: byDay, dayRuns: dayRuns,
            dayList: dayList, dayOk: dayOk, total: total, availDays: availDays,
            ranDays: dayList.length, bc: bc, bpc: bpc, pipeCount: pipeCount, rate: rate};
  }
  /* 兜底：老归档没有 step_2_runs.json 的运行历史时，退回「归档日逐日灯态」口径（灯态由比率就地算）。
     只统计有灯态的日子：既没有比率也没有灯色字段的日子不计入分母，避免被当成达标日。 */
  const dls = dayLights(ctx).filter(function(d){ return !!d.light; });
  const bad = {};
  dls.forEach(function(d){ if (d.light === '黄' || d.light === '红') bad[d.date] = 1; });
  const total = dls.length;
  const okDays = Math.max(0, total - Object.keys(bad).length);
  return {source: 'lights', ran: ran, runs: [], byDay: {}, dayRuns: {}, dayList: [], dayOk: {},
          total: total, availDays: okDays, bc: 0, bpc: 0,
          rate: total ? okDays / total : null};
}
/* 根因：只取本条记录的 AI 分析结论（不做任何历史结论复用），没有就留空走兜底文案 */
function rootCauseOf(r){
  const an = r.analysis || {};
  return an.rootCause || '';
}
/* ===== 正文清洗：报错码与出处不进页面 =====
   阅读口径：根因 / 建议只留「原因 + 怎么解决」，封装码（DEV-CODECI-35002、DEV.CB.02202002）
   与 文件名:行号 出处都是噪音，不再展示。归档 JSON 一个字都不动（cites 仍留在 analysis 里，
   错误信息列的「证据」弹窗还要用它定位原始行），只在渲染前把这几类片段剥掉：
     1) 贴进正文的原始 JSON：{"errorMessage":"…","errorCode":"…"}
     2) 括注里的出处：（job_logs/x.log:L138、L139）、（case_logs/x.html:L12）、（pipeline.json:L59）
     3) 行内裸出处：job_logs/x.log:L138 / pipeline.json:L59
     4) 封装码 / 错误码：DEV-CODECI-35002、DEV.CB.02202002、error code is 4
     5) 结尾的「已排除的干扰项」整段（旧写法为自证严谨补的，读者不需要）
   剥完收拾残留的空括号、连续标点与行首标点，避免留下「返回 ，如下」这种破句。 */
const RCB_PATH = /(?:[A-Za-z0-9_.\\-]+[\\/])+[A-Za-z0-9_.\\-]+\.(?:log|json|html|txt|xml)/;
const RCB_LC = /:\s*L?\d+(?:\s*[、,，~\-]\s*L?\d+)*/;
const RCB_JSON = /\{[^{}]*"error(?:Message|Code)"[^{}]*\}/g;
/* 行内裸出处：带目录的日志/JSON 路径，以及合并后不带目录的 pipeline.json:L59 */
const RCB_BARE = /(?:(?:[A-Za-z0-9_.\-]+[\\/])+[A-Za-z0-9_.\-]+\.(?:log|json|html|txt|xml)|pipeline\.json)(?::\s*L?\d+(?:\s*[、,，~\-]\s*L?\d+)*)?/g;
/* 全大写 + 数字 + 分隔符的错误码；正常术语（HCCL、OBS、JDK8、OpenEuler22.03）不会被吞 */
const RCB_CODE = /\b[A-Z][A-Z0-9]{1,}(?:[.\-][A-Z0-9]{1,})+\b/g;
const RCB_ERRC = /(?:错误码|错误代码|封装码)?\s*[:：]?\s*"?[A-Z][A-Z0-9]{1,}(?:[.\-][A-Z0-9]{1,})+"?/g;
/* 纯数字码：「error code is 4」「错误码 561002」「返回码：561002」——数字本身没有信息量，
   但只跟着这几个词才剥，避免误伤正常数值（98 个用例、03:25:16、2026-11-09） */
const RCB_ECODE = /(?:error\s*codes?|return\s*code|errno|错误码|错误代码|返回码)\s*(?:is|was|:|=|：|是|为)?\s*-?\d+/gi;

/* 一段文字（不含换行）里的出处括注 → 去掉；括注里没有出处的（如「（2 个用例）」）原样保留 */
function rcbDropParen(line){
  return String(line).replace(/[（(][^（）()\n]{0,200}[）)]/g, function(m){
    const inner = m.slice(1, -1);
    if (RCB_PATH.test(inner) || RCB_LC.test(inner) || /gh_[0-9A-Za-z]+/.test(inner)
        || /\blogs?\s*[:：]/.test(inner)) return '';
    return m;
  });
}
/* 根因 / 建议正文 → 去掉报错码与出处后的展示文本（多段结构保持，空行不动） */
function rcBody(s){
  const raw = String(s == null ? '' : s);
  if (!raw) return '';
  return raw.replace(/\r\n?/g, '\n').split('\n').map(function(ln){
    let t = rcbDropParen(ln);
    t = t.replace(RCB_JSON, ' ').replace(RCB_BARE, ' ')
         .replace(RCB_ECODE, ' ').replace(RCB_ERRC, ' ').replace(RCB_CODE, ' ');
    /* 收拾残留：空括号、连续标点、标点前的空格 */
    t = t.replace(/[（(]\s*[）)]/g, '')
         .replace(/[ \t]+/g, ' ')
         .replace(/\s*([，,。；;：:、])\s*/g, '$1')
         .replace(/[，,、]{2,}/g, '，')
         .replace(/([，,、；;])\s*([。；;])/g, '$2')
         .replace(/^[，,、；;：:\s]+/, '')
         .replace(/[，,、]\s*$/, '');
    return t;
  }).join('\n')
    /* 旧写法结尾的「已排除的干扰项」整段丢掉：读者要的是「原因 + 怎么解决」，
       排除项属于自证严谨的噪音（新契约已要求不写这一段） */
    .split(/\n{2,}/).filter(function(p){ return !/^\s*已排除/.test(p); }).join('\n\n')
    .replace(/\n{3,}/g, '\n\n').trim();
}
/* 根因 / 建议是多行文本，单元格按行渲染、分档给样式，形成层级（两种写法都兼容）：
     【结论】xxx            → .pt-lead（整行加粗，标签单独着色）
     一、xxx / 二、xxx      → .pt-item（旧的分点小标题，加粗）
     第一类（…）：xxx       → .pt-sec（冒号前短语当小标题加粗，正文同一行接着读，散文式）
     已排除的干扰项：       → .pt-item（整行就是标题）
     分析过程：/验证方式：  → .pt-sub（左缩进、标签灰一档）
   空行 → .pt-gap 留间距。改版前 td 不带 pre-wrap，换行会被吃掉，整段糊在一起 */
function richPoints(s){
  const lines = String(s == null ? '' : s).replace(/\r\n?/g, '\n').split('\n');
  const out = [];
  lines.forEach(function(ln){
    const t = String(ln == null ? '' : ln).replace(/\s+$/, '');
    const tt = t.trim();
    if (!tt){ out.push('<div class="pt-gap"></div>'); return; }
    /* 【结论】后面带正文：标签与正文分开渲染，保证结论行整行是重点 */
    const hd = tt.match(/^(【[^】]{1,8}】)([\s\S]*)$/);
    if (hd && hd[2]) {
      out.push('<div class="pt-lead"><span class="pt-tag">' + rfEsc(hd[1]) + '</span>'
        + rfEsc(hd[2].replace(/^\s+/, '')) + '</div>');
      return;
    }
    if (/^【.+】$/.test(tt)){ out.push('<div class="pt-hd">' + rfEsc(tt) + '</div>'); return; }
    /* 一、二、三（含中文序号到十）：旧的分点小标题 */
    if (/^[一二三四五六七八九十]{1,2}、/.test(tt)){ out.push('<div class="pt-item">' + rfEsc(tt) + '</div>'); return; }
    /* 分析过程 / 判定依据 / 验证方式：事实行，与正文错开一层 */
    const lab = tt.match(/^(分析过程|判定依据|依据|证据|验证方式|验证方法)([:：])([\s\S]*)$/);
    if (lab) {
      out.push('<div class="pt-sub"><span class="pt-lab">' + rfEsc(lab[1] + lab[2]) + '</span>'
        + rfEsc(lab[3].replace(/^\s+/, '')) + '</div>');
      return;
    }
    /* 散文分段：「第一类（15 个用例，MC2 多卡通信类算子）：最早在 …」——
       冒号前是短语小标题（不含句末标点，避免把长句里的冒号误判成标题），
       冒号后是同一段的正文 → 标题加粗内联，正文紧跟其后，读起来是一段话 */
    const sec = tt.match(/^([^。！？；\n]{2,30}?)([：:])([\s\S]*)$/);
    if (sec) {
      const body = sec[3].replace(/^\s+/, '');
      if (body) {
        out.push('<div class="pt-sec"><span class="pt-sec-hd">'
          + rfEsc(sec[1] + sec[2]) + '</span>' + rfEsc(body) + '</div>');
      } else {
        /* 冒号后没内容 = 单独一行的段落标题，如「已排除的干扰项：」 */
        out.push('<div class="pt-item">' + rfEsc(tt) + '</div>');
      }
      return;
    }
    out.push('<div class="pt">' + rfEsc(t) + '</div>');
  });
  return out.join('');
}
/* ============ 定性列：把根因正文里「可能是什么坏了」逐条列出来 ============
   读者只想知道「可能是哪几种情况」，所以这一列不做百分比、不做权重排序，只做编号列举：
     1. 镜像拉取超时
     2. 执行机凭据失效
   抽取口径（正文写法见 agent/agent_task.md）：正文按空行分段，分类型的段形如
   「第 N 类（M 条，…）：…更像 X，而非 Y」→ 每段取 X，抽不到因果就用括号里那句标签
   （「宿主 pinned 内存被占满」）；有「第 N 类」标记时只看这些段
   （第一段总述跳过，否则会把「这批都卡在环境准备」当成一条原因）；单一成因的正文不分段，
   取最后一段（结论段）。同一原因只留一条，最多 4 条。
   每条都原样展示完整（不收口、不加「…」），不做概率推测。 */
const RC_LEAD = /^(?:因此|所以|这说明|说明|可见|由此|从而|综上|其实|主要原因|真实原因|这批|这次|这一|这种|这类|本条|本次|本批|这|本|也|就|都|且|并|是|而|但|却|属|属于|于|条|第\s*[0-9一二三四五六七八九十]+\s*类)+/;
/* 「第一类（2 个 OmniOperator 子任务」/「第 1 类（5 条」两种写法都要认：类号后面允许空格，
   计数单位可能是「条」也可能是「个」；两个捕获组 = 类号、条数 */
const RC_CLS = /^第\s*([0-9一二三四五六七八九十]+)\s*类\s*[（(]\s*([0-9]+)\s*[条个]/;
/* [正则, 取第几个捕获组]：B 取「而是」后面那个（前面的「不是 X」是被否掉的那种）
   前四条服务于「分类式」长根因（…更像 X，而非 Y）；后四条服务于「单一成因」的短根因，
   后者通常就是一句直陈因果：「因为凭据失效」「clone 鉴权失败，所以用例没拉下来」「原因：凭据过期」 */
const RC_PATS = [
  [/更像(?:是)?\s*([^，,。；]{2,60}?)\s*[，,]?\s*(?:而非|而不是|并非)\s*[^，,。；]{2,60}/, 1],
  [/不是([^，,。；]{2,60})[，,]\s*而是\s*([^，,。；]{2,60})/, 2],
  [/更像(?:是)?\s*([^，,。；]{2,60})/, 1],
  /* 单一成因最常见的落点是「某个资源 / 凭据 / 配置坏了」：执行机上的 gitcode 凭据失效、OBS 临时凭证过期、
     编译机 JDK 版本不匹配——这类名词性结论比它后面的「所以用例没拉起来」更接近原因，
     也比泛化的「A 而不是 B」更具体（「是稳定复现而不是随机网络故障」只是现象，不是原因），所以排在它们前面 */
  [/([^，,。；：]{2,34}?(?:凭据|凭证|token|证书|密钥|权限|配置|版本|环境)[^，,。；：]{0,18}?(?:失效|过期|无效|不足|缺失|被拒|不匹配|不可用|没有权限|无权限|未授权))/, 1],
  [/([^，,。；：]{2,60}?)\s*(?:而非|而不是)\s*[^，,。；]{2,60}/, 1],
  [/(?:因为|由于)\s*([^，,。；：]{2,50})/, 1],
  [/([^，,。；：]{2,50}?)\s*[，,]?\s*(?:导致|致使|使得|造成|从而使|所以|因此)/, 1],
  [/(?:失败点|真实原因|根因|原因)(?:只有)?(?:一个)?(?:(?:是|就是)[：:，,]?|[：:，,])\s*([^，,。；]{3,60})/, 1],
];
function rcNorm(s){ return String(s == null ? '' : s).replace(/\s+/g, ' ').trim(); }

/* 收尾修饰：只去首尾空白与悬空标点。原因整句原样进单元格，不再按列宽收口、不补「…」——
   用户口径：定性要展示完全，缩略是误导；超长时由 CSS 在固定列宽内换行，行高随内容增长 */
function rcFit(s){
  return String(s == null ? '' : s).trim()
    .replace(/^[\s，,、：:]+/, '').replace(/[\s，,、：:]+$/, '');
}

/* 去掉句子开头的连接词（「这类」「因此」「第 2 类」…）与不成对的括号/引号 */
function rcClean(s){
  let t = rcNorm(s).replace(/^[\s「」『』“”'"（）()]+/, '').replace(/[\s「」『』“”'"（）()]+$/, '');
  t = t.replace(RC_LEAD, '').replace(/^[\s，,、]+/, '');
  t = t.replace(RC_LEAD, '').replace(/^[\s，,、]+/, '').replace(/[\s，,、]+$/, '');
  [['（', '）'], ['(', ')'], ['「', '」'], ['『', '』'], ['“', '”']].forEach(function(p){
    if (t.split(p[0]).length > t.split(p[1]).length){
      t = t.slice(0, t.lastIndexOf(p[0])).replace(/[\s，,、]+$/, '');
    }
  });
  return rcFit(t);
}

/* 一段正文 → 一条可能原因（按 RC_PATS 的优先级找第一个命中） */
function rcCauseOf(para){
  const clauses = rcNorm(para).split(/[；。]/);
  for (let pi = 0; pi < RC_PATS.length; pi++){
    const pat = RC_PATS[pi][0], gi = RC_PATS[pi][1];
    for (let ci = 0; ci < clauses.length; ci++){
      const m = pat.exec(rcNorm(clauses[ci]));
      if (!m) continue;
      const g = rcClean(m[gi] || '');
      if (g && g.length >= 3) return g;
    }
  }
  return '';
}

/* 「第 N 类（M 个用例，共性：宿主 pinned 内存被占满）：…」→ 括号里那句标签。
   分类段的正文经常只写证据（用例清单、偏差表现），一句「什么坏了」都没点名，
   这时括号里的标签就是这一类的定性（「宿主 pinned 内存被占满」「pod 提前退出」）；
   标签只是分组名（「量化变体」「MoE 分发合并」）时不当原因，跳过。 */
const RC_HEAD = /^第\s*[0-9一二三四五六七八九十]+\s*类\s*[（(][^）)]*?[，,]\s*([^）)]*?)\s*[）)]/;
const RC_CAUSEY = /(失效|过期|无效|不足|缺失|被拒|超时|占满|占用|耗尽|冲突|不匹配|不一致|偏移|不达标|异常|中断|退出|报错|失败|被占|拉取|鉴权|权限)/;
function rcHeadOf(para){
  const m = String(para || '').match(RC_HEAD);
  if (!m) return '';
  const t = rcClean(String(m[1]).replace(/^(?:共性|共性原因|共性特征|特点|特征|表现|现象)[：:]\s*/, ''));
  return (t && RC_CAUSEY.test(t)) ? t : '';
}

/* 根因正文 → [可能原因]（保持正文顺序，最多 4 条，每条是「什么坏了」的一句话）
   有「第 N 类（M 条…）」分段时逐类取一条：先按 RC_PATS 抽因果，抽不到再用括号里的标签；
   分类段一条都抽不到（正文只写证据、原因写在结论段）才回头扫非分类段。
   没有分类标记（单一成因的短根因）就只看结论段——第一段往往是总述，抽出来是
   「这批都卡在环境准备」这种覆盖全批的话，不该当原因。最后还用结论段首句兜底，保证不空。
   返回的是完整原因原句，页面原样展示（不做收口）。 */
function causesFromRootCause(rc){
  const paras = String(rc || '').split(/\n\s*\n/).map(rcNorm)
    .filter(function(p){ return !!p && !/^已排除的干扰项/.test(p); });
  if (!paras.length) return [];
  const cls = paras.filter(function(p){ return RC_CLS.test(p); });
  const out = [], seen = {};
  const push = function(c){
    if (c && !seen[c]){ seen[c] = 1; out.push(c); }
  };
  if (cls.length){
    cls.forEach(function(p){ push(rcCauseOf(p) || rcHeadOf(p)); });
    if (!out.length){
      paras.filter(function(p){ return !RC_CLS.test(p); })
        .forEach(function(p){ push(rcCauseOf(p)); });
    }
  } else {
    push(rcCauseOf(paras[paras.length - 1]));
  }
  if (!out.length) push(rcClean(String(paras[paras.length - 1]).split(/[；。]/)[0]));
  return out.slice(0, 4);
}
function cut(s, n){
  const t = String(s == null ? '' : s).replace(/\s+/g, ' ').trim();
  if (!t) return '--';
  return t.length > n ? t.slice(0, n) + '…' : t;
}

/* ===== 问题分类列：一个色标，参考仓的 10 类根因 =====
   大类是十个固定值（AI 在 analysis.issueType 里写，白名单校验在 tools/write_analysis.py），
   页面只负责把它染色展示。分类和「定性」是两件事：分类回答「哪一类问题」（编译 / 单测 / 依赖环境 /
   网络下载 / 资源 / 超时 / 权限 / 配置参数 / 平台 / 无法判定），
   定性回答「具体是什么坏了」——资源规格、节点、镜像、端口、ranktable 这些细节一律留在根因分析里。 */
const ISSUE_CLASSES = ['代码编译错误', '单元测试失败', '依赖/环境缺失', '网络/下载失败', '资源不足',
                       '超时', '权限问题', '配置/参数错误', '基础设施/平台', '无法判定'];
/* AI 常写的近义标签 → 十类标准名（老归档写的是上一版的六个大类，这里一并折过来，避免整列显示 --） */
const IC_ALIAS = {
  '编译错误': '代码编译错误', '编译失败': '代码编译错误', '构建失败': '代码编译错误',
  '单测失败': '单元测试失败', '用例问题': '单元测试失败', '测试失败': '单元测试失败',
  '环境问题': '依赖/环境缺失', '依赖缺失': '依赖/环境缺失', '环境缺失': '依赖/环境缺失',
  '网络问题': '网络/下载失败', '下载失败': '网络/下载失败', '网络失败': '网络/下载失败',
  '资源问题': '资源不足', '磁盘满': '资源不足', '内存不足': '资源不足',
  '运行超时': '超时', 'timeout': '超时',
  '鉴权失败': '权限问题', '认证失败': '权限问题', '凭据失效': '权限问题',
  '配置错误': '配置/参数错误', '参数错误': '配置/参数错误', '工程问题': '配置/参数错误',
  '平台问题': '基础设施/平台', '调度问题': '基础设施/平台', '工具问题': '基础设施/平台',
  '待判定': '无法判定', '未知': '无法判定'
};
/* 老归档没有 issueType 时的兜底：只认信号最强的几类关键词，认不出就不猜（分类列显示 --，
   不硬贴大类——贴错了比不贴更误导）；其余一律等重分析补字段。 */
const IC_GUESS = [
  ['权限问题', /Permission denied|403 Forbidden|Authentication failed|鉴权失败|认证失败|凭据|凭证|token 过期|证书/],
  ['资源不足', /No space left on device|Out of memory|OOM|Killed|磁盘满|空间不足/],
  ['依赖/环境缺失', /No module named|command not found|package not found|依赖缺失|镜像拉不下来/],
  ['代码编译错误', /undefined reference|syntax error|\berror:|编译失败/],
  ['单元测试失败', /AssertionError|FAILED test|精度比对不过/],
  ['网络/下载失败', /Could not resolve host|Connection timed out|404 Not Found|网络下载失败/],
  ['超时', /exceeded the time limit|timeout !!!|执行超时/]
];
/* 分类名归一到十类标准名：先认白名单原值，再认近义标签，最后按关键词兜底 */
function issueTypeOf(r){
  const an = (r || {}).analysis || {};
  const raw = String(an.issueType || '').trim();
  if (ISSUE_CLASSES.indexOf(raw) >= 0) return raw;
  if (raw && IC_ALIAS[raw]) return IC_ALIAS[raw];
  const rc = rcBody(an.rootCause || '');
  for (let i = 0; i < IC_GUESS.length; i++){
    if (IC_GUESS[i][1].test(rc)) return IC_GUESS[i][0];
  }
  return '';
}
/* 问题分类列：只给一个染色的大类标签，判不出大类就交给调用方显示 -- / 待分析 */
function classTag(type){
  const k = ISSUE_CLASSES.indexOf(String(type || ''));
  return '<span class="ic-tag' + (k >= 0 ? ' ic-c' + k : '') + '">' + rfEsc(type) + '</span>';
}
/* ===== 定性列：把根因里的「可能是哪几种情况」逐条列出来 =====
   取的是根因正文里「是什么坏了」的那句话（凭据失效 / 资源没申请到 / 镜像拉不下来…），
   一条就能说清时不带编号，多条才按 1. 2. 3. 排。AI 没写根因正文时退回 issueDetail 那一句。 */
function causeList(r){
  const an = (r || {}).analysis || {};
  const cs = causesFromRootCause(rcBody(an.rootCause || ''))
    .filter(function(c){ return !!c; });
  if (cs.length) return cs;
  const d = String(an.issueDetail || '').trim();
  return d ? [d] : [];
}
/* 定性列：原因整句原样进单元格，不做 26 字收口、不补「…」——超长时在列宽内换行，行高自然增长。
   title 仍挂同一句完整原因（列内容与 title 一致，便于悬停复制） */
function causeCell(r){
  const cs = causeList(r);
  if (!cs.length) return '';
  if (cs.length === 1){
    return '<div class="ct-item" title="' + rfEsc(cs[0]) + '">' + rfEsc(cs[0]) + '</div>';
  }
  return cs.map(function(c, k){
    return '<div class="ct-item ct-num" title="' + rfEsc(c) + '"><span class="ct-k">' + (k + 1) + '.</span>'
      + rfEsc(c) + '</div>';
  }).join('');
}
/* 2026-10-28T21:10:17 → 2026-10-28 21:10:17 */
function tm(s){
  const v = String(s == null ? '' : s).replace('T', ' ');
  return v ? v.slice(0, 19) : '--';
}
/* 根因表的「开始/结束时间」列：归档里是 2026-11-09T02:05:47（ISO，可能带毫秒/时区），
   统一压成 yyyy-MM-dd hh:mm:ss（2026-11-09 02:05:47）——标准可读格式，列内等宽对齐、扫一眼就能定位，
   需要精确到毫秒的原始值在首页「执行记录」弹窗 / 归档 JSON 里看。 */
function ymd(s){
  const m = String(s == null ? '' : s)
    .match(/(\d{4})[-\/](\d{1,2})[-\/](\d{1,2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?/);
  if (!m) return '--';
  const p2 = function(x){ return ('0' + x).slice(-2); };
  return m[1] + '-' + p2(m[2]) + '-' + p2(m[3]) + ' ' + p2(m[4]) + ':' + m[5] + ':' + (m[6] == null ? '00' : m[6]);
}
/* 一条执行记录的「排序时间」：优先结束时间，缺失/非法就用开始时间，都没有返回 null。
   归档里可能带毫秒或时区（2026-11-09T21:03:53.000Z），一律用 Date.parse 归一成毫秒。 */
function recEndMs(r){
  const cand = [r && r.endedAt, r && r.startedAt];
  for (let i = 0; i < cand.length; i++){
    const s = String(cand[i] == null ? '' : cand[i]).trim();
    if (!s) continue;
    const t = Date.parse(s.indexOf(' ') > 0 && s.indexOf('T') < 0 ? s.replace(' ', 'T') : s);
    if (!isNaN(t)) return t;
  }
  return null;
}
/* 根因表行序比较器：按结束时间**降序**（最新在上）；时间相同按构建号降序，保证顺序稳定 */
function cmpByEndDesc(a, b){
  const ta = recEndMs(a), tb = recEndMs(b);
  if (ta !== tb){
    if (ta === null) return 1;    /* 没有时间的排到最后，不要插在最前面挡住最新构建 */
    if (tb === null) return -1;
    return tb - ta;
  }
  const na = Number(a && a.buildNumber), nb = Number(b && b.buildNumber);
  if (!isNaN(na) && !isNaN(nb) && na !== nb) return nb - na;
  return String((b && b.buildNumber) || '').localeCompare(String((a && a.buildNumber) || ''));
}
/* job 日志的每一行本来就带时间戳（[2026/11/06 01:11:47.479 GMT+08:00] ...），
   而 errorMessages 这一层只有行号没有时间字段 → 从原文行首把时间抽出来，供「证据」弹窗的时间列用 */
function timeFromLine(s){
  const m = String(s == null ? '' : s)
    .match(/(\d{4})[-\/](\d{1,2})[-\/](\d{1,2})[ T](\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?)/);
  if (!m) return '';
  const p2 = function(x){ return ('0' + x).slice(-2); };
  return m[1] + '-' + p2(m[2]) + '-' + p2(m[3]) + ' ' + m[4];
}
/* 一条记录的「证据」行 = 用例级 errorMessages（带 time/loc，测试报告路线）
   + 顶层 errorMessages（脚本筛过的「重要节点」）。
   去重口径（用户要求）：**只去掉整行完全一致的**——时间、级别、任务/用例、位置（文件:行号）、
   原文全都相同才算重复行；只要时间不一致或行号不一致就保留（那往往是同一个用例的多次重试、
   或多卡并行打印在不同行上的同一条消息，各自是独立的证据）。
   这里不再合成「AI 定位」行：错误信息列已不再展示 AI 结论，证据只保留归档里的原文行。 */
function evidenceRows(r){
  if (!r) return [];
  if (r.__ev) return r.__ev;
  const out = [], seen = new Set();
  const sig = function(s){ return String(s == null ? '' : s).replace(/\s+/g, ' ').trim(); };
  const push = function(o){
    const body = sig(o.text);
    const k = [sig(o.time), sig(o.level), sig(o.loc), String(o.line == null ? '' : o.line),
               sig(o.task), sig(o.case), body].join('\u0001');
    if (body && seen.has(k)) return;
    seen.add(k);
    out.push(o);
  };
  (r.cases || []).forEach(function(c){
    (c.errorMessages || []).forEach(function(m){
      push({time: m.time || timeFromLine(m.msg), level: nz(m.level), loc: m.loc || '',
            line: m.line, text: m.msg || '', task: c.task || '', case: c.name || ''});
    });
  });
  (r.errorMessages || []).forEach(function(e){
    if (e.isSummary) return;                 /* pytest 汇总行不是一条证据 */
    push({time: e.time || timeFromLine(e.text), level: nz(e.level), loc: '',
          line: e.line, text: e.text || '', task: e.task || '', case: e.case || ''});
  });
  const withT = out.filter(function(x){ return x.time; });
  const noT = out.filter(function(x){ return !x.time; });
  withT.sort(function(a, b){ return String(a.time).localeCompare(String(b.time)); });
  r.__ev = withT.concat(noT);
  return r.__ev;
}

/* ===== 一行 = 一条执行记录（不再做「同日同报错」聚合） =====
   用户口径：构建拆开看，一次构建一行，靠「开始/结束时间」列定位，
   同一天同一报错的几次构建各自成行、各自带自己的根因/建议（分析本来就是按记录写的）。
   所以这里不再按报错指纹分组，原有 groupRecs / errSigOf / groupCases / groupEvidenceRows /
   groupAnalyses 一族聚合函数已下线。 */

/* 展示名：pipelineName（缺失才退回短名）。**只有同组件下确有重名**时才在名字后补平台
   （Nightly-CI-Version-pipeline_openUBMC（gitcode））—— 没重名的组件一个字都不加工。 */
function plLabelOf(pl, dupNames){
  pl = pl || {};
  const nm = String(pl.pipelineName || pl.pipeline || '');
  const plat = String(pl.platform || '');
  return (plat && dupNames && dupNames[nm]) ? (nm + '（' + plat + '）') : nm;
}
/* 重名名字集合：{流水线名: 1}，只收「被 2 条以上**不同身份**流水线共用」的名字。
   身份键去重是关键：同一条流水线跨多天会有多个归档目录，不能被算成两条。 */
function dupNameSet(pls){
  const owners = {};
  (pls || []).forEach(function(pl){
    const nm = String((pl || {}).pipelineName || (pl || {}).pipeline || '');
    if (!nm) return;
    (owners[nm] || (owners[nm] = {}))[plKeyOf(pl)] = 1;
  });
  const dup = {};
  Object.keys(owners).forEach(function(nm){
    if (Object.keys(owners[nm]).length > 1) dup[nm] = 1;
  });
  return dup;
}
/* 区间内该组件下的流水线（**按身份键去重**）：页头 / 区块头 / 执行记录分组都用它。
   同组件下可能有两平台重名的流水线（openUBMC 的 codearts / gitcode），按名字去重会把两条
   并成一条。返回 [{key, label, pl}]，key = 组件|pipelineId，label = 展示名（重名才补平台）。 */
function plList(ctx){
  const seen = {}, pls = [];
  ((ctx || {}).dirs || []).forEach(function(d){
    const pl = (d || {}).pl || {};
    const k = plKeyOf(pl);
    if (!k || k === '|' || seen[k]) return;
    seen[k] = 1;
    pls.push(pl);
  });
  const dup = dupNameSet(pls);
  return pls.map(function(pl){
    return {key: plKeyOf(pl), label: plLabelOf(pl, dup), pl: pl};
  }).sort(function(a, b){ return a.label < b.label ? -1 : (a.label > b.label ? 1 : 0); });
}

/* 组件级页头：标题 = 组件名（带维度），面包屑只到组件这一层（中间那层流水线清单已下线），
   概要行给区间内几条流水线、需根因的执行记录数——不再有单条流水线的「平台」字段 */
function renderHead(meta, recs, ctx){
  const el = document.getElementById('meta');
  let tasks = 0, cases = 0, errs = 0, evs = 0;
  recs.forEach(function(r){
    const c = r.counts || {};
    tasks += Number(c.tasks || 0); cases += Number(c.cases || 0);
    errs += Number(c.errorMessages || 0); evs += Number(c.evidenceFiles || 0);
  });
  const comp = (meta && (meta.comp || (meta.pl || {}).component)) || COMP;
  const pipes = plList(ctx);
  const dayN = {};
  (ctx.dirs || []).forEach(function(d){ dayN[d.date] = 1; });
  const ttl = document.getElementById('ttl');
  if (ttl) ttl.innerHTML = '<b>' + rfEsc(comp) + '</b>'
    + ' <span class="dim" style="font-weight:400;font-size:12px">'
    + rfEsc(DIM ? DIMNAME[DIM] : '全部维度') + ' · 组件级根因明细</span>';
  const crumb = document.getElementById('crumb');
  if (crumb) crumb.innerHTML = crumbHead(ctx.from, ctx.to)
    + '<b>' + rfEsc(comp) + '</b>'
    + (DIM ? '<span class="dim">（' + rfEsc(DIMNAME[DIM]) + '）</span>' : '')
    + '<span class="dim">（组件级 · 区间内 ' + Object.keys(dayN).length + ' 天有归档记录 · '
    + pipes.length + ' 条流水线 · 每条执行记录一行根因）</span>';
  /* 「任务成功但用例未 100%」的行没有根因，不能混进「需根因执行记录」的条数里，
     单独给一个数（这些行是 load() 从运行历史补的，见 okRowOf） */
  const okRows = recs.filter(function(r){ return r.__okRow; }).length;
  if (el) el.textContent = '区间 ' + ctx.from + ' ~ ' + ctx.to
    + ' · 流水线 ' + (pipes.length || '--')
    + ' · 需根因执行记录 ' + (recs.length - okRows)
    + (okRows ? ' · 任务成功但用例未 100% ' + okRows : '')
    + ' · 任务 ' + tasks + ' · 用例 ' + cases
    + ' · 错误信息 ' + errs + ' · 证据 ' + evs;
  renderDist(recs);
}

/* 失败类型分布：二级页字段，按执行记录计数（含占比）。纯展示、不可点 —— 行序由根因表自己按结束时间排。
   这里数的是归档时的规则分类（verdict.category），和「问题分类」列不是一回事：后者是 AI 判的
   十类根因（编译/单测/依赖环境/网络下载/资源/超时/权限/配置参数/平台/无法判定），
   一条记录一个大类、口径按问题性质而不是按失败形态，
   所以这一条只做「这批失败里各类型各占多少条」的概览，标题也就叫「失败类型分布」不再叫「定性分布」 */
function catOfRec(r){
  return (r.verdict || {}).category || r.category || 'unknown';
}
function renderDist(recs){
  const el = document.getElementById('dist');
  if (!el) return;
  const byCat = {};
  recs.forEach(function(r){
    /* 「任务成功但用例未 100%」的行照常计数（它们是表里真实存在的一行），
       只是分类名走 CATS.success = 「用例未 100%」，不出现英文 success */
    const k = catOfRec(r);
    byCat[k] = (byCat[k] || 0) + 1;
  });
  const ks = Object.keys(byCat).sort(function(a, b){ return byCat[b] - byCat[a]; });
  const total = recs.length || 1;
  const tags = ks.map(function(k){
    const c = CATS[k] || [k, ''];
    const p = (byCat[k] / total * 100).toFixed(byCat[k] === total ? 0 : 1);
    return '<span class="tag ' + c[1] + '">'
      + rfEsc(c[0]) + ' ' + byCat[k]
      + ' <span class="dim">' + p + '%</span></span>';
  }).join('');
  el.innerHTML = '<span class="dt" title="按归档时的规则分类统计执行记录条数'
    + '（「用例未 100%」= 任务执行成功但用例通过率没到 100% 的运行）；'
    + '「问题分类」列给的是 AI 判的十类根因（编译/单测/依赖环境/网络下载/资源/超时/权限/配置参数/平台/无法判定），两者口径不同">失败类型分布</span>'
    + (ks.length ? tags : '<span class="dim">--</span>')
    + '<span class="sp"></span><span class="dim">按执行记录计数，共 ' + recs.length + ' 条</span>';
}

let RECS = [];      /* 区间内该流水线的全部失败执行记录（平铺，一条记录一行） */
let RUNS = [];      /* 区间内该流水线的全部运行（每日归档 runHistory，按构建号去重） */
let CTX = null;     /* 当前统计区间上下文：dirs / days / from / to / runs */

/* ---------- 「任务执行成功，但用例通过率没到 100%」这一类运行 ----------
   用户口径：这类运行只需要**标记任务成功 + 给出用例比例**，不做 AI 根因、不抓失败用例清单。
   数据来源是 runHistory（load 收的 runs 里带了全部运行，含 status / cpr），
   归档的 records 只收 FAILED / CANCELED，所以它们默认不会出现在根因表里 —— 由 load 就地补一行。 */
function isOkRun(x){
  const s = String((x || {}).status || '').toUpperCase();
  return s === 'SUCCESS' || s === 'SUCCEEDED' || s === 'COMPLETED'
      || s === 'FINISH' || s === 'FINISHED' || s === 'PASS' || s === 'PASSED';
}
/* 用例通过率：归档里 cpr 是 0~100 的百分数，缺失 / 非法返回 null（不猜、不补 0） */
function cprOf(x){
  const v = (x || {}).cpr;
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  return isNaN(n) ? null : n;
}
/* 由一条 runHistory 运行合成一行「任务成功 · 用例 xx%」的记录：
   没有 tasks / cases / analysis，recRow 里走 isOk 分支 —— 不给 ▸、不给根因/建议，
   问题分类只标「任务成功」，定性写用例通过率。 */
function okRowOf(x){
  return {
    __pl: x.__pl || '',
    __plk: x.__plk || '',
    __okRow: 1,
    __cpr: cprOf(x),
    buildNumber: x.rn,
    links: {openlibing: x.url || ''},
    startedAt: x.start || '',
    endedAt: x.end || '',
    tasks: [], cases: [], errorMessages: [],
    evidence: {files: [], missing: []},
    verdict: {category: 'success'},
    analysis: {status: 'na'}
  };
}

/* 区间内没有失败记录时的提示：不能再只写一句「没有需要根因分析的失败执行记录」——
   最常见的原因是「运行全部执行成功，但测试用例通过率不是 100%」（iva=false），
   只看这一页的根因表根本看不出可用度为什么是 0%。这里直接从区间运行历史里算出
   「哪几次不是 100%、最低多少、是哪次构建」，用醒目底色摆在区块头下面。 */
function noFailNotice(st){
  const runs = (st && st.runs) || [];
  const total = st ? (st.total || 0) : 0;
  const ok = st ? (st.availDays || 0) : 0;
  if (!runs.length){
    /* 区间内一次都没跑过：这不叫「可用度 0%」，也不该让人点进去找失败记录 */
    if (st && st.ran === false){
      return '<div class="nofail mute"><span class="nf-hd">该组件在区间内 0 次运行</span>'
        + '既没有可用日，也没有可做根因分析的失败执行记录；'
        + '比率显示 <b>--</b>，不纳入统计。</div>';
    }
    const noHist = st && st.source !== 'runs';
    return '<div class="nofail mute"><span class="nf-hd">区间内没有需要根因分析的失败执行记录</span>'
      + (noHist
          ? '该区间归档里没有这个组件的运行历史（只有日级灯态），无法逐条对照执行结果。'
          : '该组件在这个区间内 0 次运行，既没有可用日，也没有可做根因分析的失败记录。')
      + '</div>';
  }
  /* 执行成功但用例没全通过：iva=false 的典型成因，也是「没有失败记录却不可用」的答案
     （用例通过率统一走全局 cprOf，与根因表「任务成功但用例未 100%」的行同一个口径） */
  const notFull = runs.filter(function(r){ const c = cprOf(r); return c !== null && c < 100; });
  if (notFull.length){
    notFull.sort(function(a, b){ return cprOf(a) - cprOf(b); });
    const low = notFull[0];
    const allOk = runs.every(function(r){
      return /COMPLETED|SUCCESS|FINISH|PASS/i.test(String(r.status || ''));
    });
    const where = (low.rn === null || low.rn === undefined || low.rn === '')
      ? (low.day || '') : ('构建 #' + low.rn + (low.day ? ' · ' + low.day : ''));
    const head = allOk
      ? '区间内 ' + runs.length + ' 次运行全部执行成功，但测试用例通过率不是 100%'
      : '区间内 ' + runs.length + ' 次运行里有 ' + notFull.length + ' 次测试用例通过率不是 100%';
    return '<div class="nofail"><span class="nf-hd">' + rfEsc(head) + '</span>'
      + '最低 <b>' + cprOf(low).toFixed(2) + '%</b>'
      + (where ? '（' + rfEsc(where) + '）' : '') + '。'
      + '版本可用度的口径是「当天要有编译通过且用例 100% 通过的运行才算可用」，'
      + '这类运行属于执行成功、用例没全过，所以当天不计为可用'
      + (total ? '（本区间可用 ' + ok + '/' + total + ' 工作日）' : '')
      + '，也不会产生失败执行记录，因此没有根因分析行。'
      + '要逐次核对通过率：回首页点「流水线数 → 查看」看该流水线的执行记录。</div>';
  }
  return '<div class="nofail mute"><span class="nf-hd">区间内没有需要根因分析的失败执行记录</span>'
    + '共 ' + runs.length + ' 次运行，均未判为失败或取消'
    + (total ? '；本区间' + DIMNAME[dimOf()] + '达标 ' + ok + '/' + total + ' 工作日' : '')
    + '。</div>';
}

/* ===== 组件级：一个组件一个区块（h3 浅蓝区块头）+ 一张 9 列固定列宽的根因明细表 =====
   组件下所有待分析流水线的失败执行记录合成一张表，靠最前面的「流水线」列区分是哪一条。
   区块头的比率 / 达标工作日按**组件级**算：rangeStats 收到的是该组件全部流水线的运行历史，
   版本可用度 = 各流水线版本可用度的算术平均（与首页组件行同源），构建成功率 = Σ编译通过 ÷ Σ编译次数。 */
function renderRecs(recs, ctx){
  const box = document.getElementById('recs');
  RECS = recs;
  const dim = dimOf();
  const st = rangeStats(ctx, dim);
  const total = st.total;
  const okDays = st.availDays;
  const rate = st.rate;
  const pipes = plList(ctx);
  /* 表里混着两类行：有根因的失败记录 + 「任务成功但用例未 100%」的补行（后者没有根因） */
  const okRows = recs.filter(function(r){ return r.__okRow; }).length;
  /* 只看数字（85.71%），不带黄/绿/红标记：灯色只在首页组件级出现，
     往下钻一律看数字 + 达标工作日数。 */
  const okWord = dim === 'bs' ? '达标' : '可用';
  const isVA = dim !== 'bs';
  const pipeN = st.pipeCount || 0;
  /* dim=va 的组件级口径 = 各流水线版本可用度的算术平均（与首页组件行同一个算法、同一套逐日重算）：
     各流水线的分母是各自「有执行记录的天数」（当日没启动、没跑过的日子不进分母），
     分母不同，所以只能逐条算比率再平均 —— 区块头的数字与首页点进来的那一行一致。
     dim=bs 仍是 Σ编译通过 ÷ Σ编译次数，分母是「区间内有运行的天数」。 */
  const denomTip = (dim === 'bs')
    ? ('区间内有运行的工作日 ' + total + ' 天，其中 ' + okDays + ' 天' + okWord)
    : ('各流水线版本可用度平均：' + pipeN
       + ' 条流水线各自「可用天数 ÷ 有执行记录天数」求和 ÷ ' + pipeN
       + '，合计可用 ' + okDays + ' 天 / 有执行记录 ' + total + ' 天');
  const denomRule = (dim === 'bs')
    ? '分母 = 区间内有运行的工作日数'
    : '分母 = 各流水线在区间内有执行记录的天数（当天有运行开始或结束才算；没启动、没跑过，以及当天只有 canceled 运行的都不计入）';
  const h3 = '<h3 title="' + DIMNAME[dim] + '（组件级）：' + (st.source === 'runs'
      ? denomTip + '（逐日按执行结果重算，统计区间一变即重算；' + denomRule + '）'
      : (st.ran
          ? '区间内归档 ' + total + ' 天，其中 ' + okDays + ' 天该组件在此维度上没有黄/红'
          : '该组件在本区间内一次都没有运行过（runsInPeriod = 0），'
            + '归档里没有可比对的执行结果，比率显示 --、不纳入统计'))
    + '">' + rfEsc(COMP)
    + ' ｜ ' + DIMNAME[dim] + ' ' + (rate == null ? '--' : (rate * 100).toFixed(2) + '%')
    /* 组件级版本可用度 = 各流水线平均：只在比率后面标明几条流水线平均，
       不再跟「可用 N/M 工作日」——N/M 就是比率本身，写出来是重复（用户口径）。 */
    + (isVA && pipeN ? '（' + pipeN + ' 条流水线平均）' : '')
    /* 区间内没跑过（或整个区间没有归档日）：写明没有执行记录、不纳入统计，
       不给「可用 0/0 工作日」这种看着像 0 分的写法 */
    + ((st.ran && total > 0)
        ? ' '
        : ' <span class="dim">｜ '
          + ((ctx && (ctx.dirs || []).length)
              ? (st.ran ? '区间内无可比对的执行结果 · 不纳入统计' : '区间内无执行记录 · 不纳入统计')
              : '区间内无归档日')
          + '</span> ')
    /* 比率之后紧跟「执行记录」入口（用户口径）：按流水线列出该组件区间内的运行，
       不再把「N 条流水线：a、b、c…」这串名字摊在区块头里（太长且不可点）。 */
    + ((ctx && (ctx.dirs || []).length)
        ? '<span class="lnk hd-exec" data-execpipes="1" title="按流水线查看该组件在区间 '
          + rfEsc(ctx.from || '') + ' ~ ' + rfEsc(ctx.to || '') + ' 内的全部执行记录'
          + (pipes.length ? '（共 ' + pipes.length + ' 条流水线）' : '') + '">执行记录</span>'
        : '')
    + (okRows
        ? '<span class="dim" style="font-weight:400;font-size:12px;margin-left:8px">'
          + '（含 ' + okRows + ' 条任务成功但用例未 100%）</span>'
        : '')
    + '</h3>';
  if (!recs.length){
    /* 区间内没有失败记录也要出区块头：比率/工作日是区间重算的结果，不是记录数的函数。
       提示按「为什么没有失败记录」分情况给（用例没到 100% / 一次没跑 / 无运行历史），
       见 noFailNotice；不再是「自己翻归档」的一句灰字。 */
    box.innerHTML = '<div class="subview">' + h3 + noFailNotice(st) + '</div>';
    return;
  }
  /* 10 列：流水线 → 流水线编号 → 开始时间 → 结束时间 → 失败任务 → 问题分类 → 定性分析
     → 错误信息 → 失败用例 → 展开
     一条执行记录一行（不再把同日同报错聚合成一行），开始/结束时间给出 yyyy-MM-dd hh:mm:ss，
     行序由 recs 自己带过来（加载时已按结束时间降序排好，见 cmpByEndDesc），
     所以是「最近结束的在最上面」，方便从最新往回看。
     最前面的「流水线」列是组件级才有的：同一张表里混着多条流水线的记录，必须能一眼看出是哪条。
     最后一列「展开」放稍大的三角按钮，整行 hover: pointer（用户口径）。
     列宽：错误信息列的内容只有「证据」一个链接，一路从 16% 收窄到 6%，让出的 10% 全部加给
     定性分析列（10% → 20%）——定性要完整展示、行高由它主导，宽度必须给够。 */
  const cols = ['12%', '7%', '10.5%', '10.5%', '10%', '9%', '20%', '6%', '12%', '3%'];
  const ths = ['流水线', '流水线编号', '开始时间', '结束时间', '失败任务', '问题分类', '定性分析', '错误信息', '失败用例', ''];
  const table = '<table class="rca rca-rec"><colgroup>'
    + cols.map(function(w){ return '<col style="width:' + w + '">'; }).join('')
    + '</colgroup><thead><tr>' + ths.map(function(t){ return '<th>' + t + '</th>'; }).join('') + '</tr></thead>'
    + '<tbody>' + recs.map(function(r, i){ return recRow(r, i); }).join('') + '</tbody></table>';
  box.innerHTML = '<div class="subview">' + h3 + table + '</div>';
}

/* ===== 区块头「执行记录」入口：本组件区间内的运行按流水线分组 =====
   取代原先摊在区块头的「N 条流水线：a、b、c…」文字。数据就是 load() 收进来的 ctx.runs
   （已按「执行结束时间落在区间内」筛过、按流水线+构建号去重）。
   版式（用户口径）：一个弹窗里按流水线分组，组内按日期倒序逐天给灯态——
   组头 = 流水线名 + 该流水线达成率（成功工作日 / 已执行工作日）+「逐条」入口；
   逐日行 = 灯 + 日期 + 「成功 x / 共 y 次」。EXEC_GROUPS 供「逐条」回调按下标取用。 */
let EXEC_GROUPS = [];
/* 该次运行在当前维度算不算「成功」：va 看归档算好的 iva；bs 看编译是否全过 */
function runOkIn(x, dim){
  if (dim === 'bs'){
    if (x.bc === null || x.bc === undefined || x.bc === '') return false;
    return (Number(x.bpc) || 0) >= Number(x.bc);
  }
  return x.iva === true;
}
/* 取消（CANCELED）的运行：计入当日「共 N 次」，但永远不算成功（灯态只看是否成功）。
   用户口径：取消也算当天真实发生过的一次执行，不能因为没跑完就从次数里消失。 */
function runCanceled(x){ return String(x.status || '').toUpperCase() === 'CANCELED'; }
function execDot(ok){
  return '<i class="' + (ok === true ? 'g' : (ok === false ? 'r' : 'n')) + '"></i>';
}
function openExecByPipe(){
  const ctx = CTX || {};
  const dim = dimOf();
  const from = ctx.from || '', to = ctx.to || '';
  const grp = {}, order = [];
  (ctx.runs || []).forEach(function(x){
    const k = x.__plk || '--';
    if (!grp[k]){ grp[k] = []; order.push(k); }
    grp[k].push(x);
  });
  /* 区间内有归档目录但一次都没跑的流水线也列出来（该组没有逐日行）。
     按**身份键**分组：同组件下两平台重名的流水线（openUBMC 的 codearts / gitcode）
     按名字分会被并成一族，两条的运行混在一个组头下。 */
  const pipeMeta = {};
  plList(ctx).forEach(function(it){
    pipeMeta[it.key] = it.label;
    if (!grp[it.key]){ grp[it.key] = []; order.push(it.key); }
  });
  if (!order.length){
    openModal('执行记录｜' + rfEsc(COMP),
      '<div class="dim">该组件在区间 ' + rfEsc(from) + ' ~ ' + rfEsc(to) + ' 内没有归档记录。</div>');
    return;
  }
  EXEC_GROUPS = order.map(function(k){
    return {key: k, label: pipeMeta[k] || k, runs: grp[k]};
  });
  /* 两条轨道各自保持「与区块头同口径」，否则点开弹窗会看到组头 100%、组件头 96%：
     va  组头 = 成功天数 / 该流水线在区间内有执行记录的天数（各流水线逐条算比率再平均）；
     bs  组头 = Σ编译通过 / Σ编译次数（用户口径：最终编译成功率 = 所有通过次数 ÷ 所有编译次数，
          各流水线的分子分母分别求和就是组件级的值，所以逐条也能加得起来）。
     「共 N 次」把取消的运行也算进去（当天确实跑过），但取消的运行不算成功、也不带编译次数。 */
  const isBS = (dim === 'bs');
  /* 组头分母 = 该流水线在区间内**有执行记录的天数**（逐日 vaDetail 累加，没启动的日子不进分母）。
     与区块头 / 首页同一套分母，组头才能和组件头对上。 */
  const denDays = isBS ? {} : vaDaysDenByPipe(ctx);
  let sumNum = 0, sumDen = 0, sumPass = 0, sumCnt = 0, sumRate = 0, sumRateN = 0;
  const blocks = EXEC_GROUPS.map(function(g, gi){
    const byDay = {};
    let pPass = 0, pCnt = 0;
    g.runs.forEach(function(x){
      if (!x.day) return;
      const c = (x.bc === null || x.bc === undefined || x.bc === '') ? 0 : (Number(x.bc) || 0);
      const p = (x.bpc === null || x.bpc === undefined || x.bpc === '') ? 0 : (Number(x.bpc) || 0);
      const d = byDay[x.day] || (byDay[x.day] = {total: 0, ok: 0, p: 0, c: 0});
      d.total += 1;
      if (runOkIn(x, dim)) d.ok += 1;
      d.p += p; d.c += c;
      pPass += p; pCnt += c;
    });
    /* 组内按日期倒序（最新的工作日排在最上面） */
    const days = Object.keys(byDay).sort(function(a, b){ return a < b ? 1 : -1; });
    let num, den, head, dayOk;
    if (isBS){
      num = pPass; den = pCnt;
      head = (pCnt > 0 ? (pPass / pCnt * 100).toFixed(2) + '%' : '--')
           + '（Σ编译通过 ' + pPass + ' / Σ编译次数 ' + pCnt + '）';
      dayOk = function(e){ return e.c > 0 && e.p >= e.c; };
    } else {
      const dd = Number(denDays[g.key]) > 0;
      den = dd ? Number(denDays[g.key]) : days.length;
      num = days.filter(function(d){ return byDay[d].ok > 0; }).length;
      head = (den > 0 ? (num / den * 100).toFixed(2) + '%' : '--')
           + '（成功 ' + num + ' / 有执行记录 ' + den + ' 天）';
      dayOk = function(e){ return e.ok > 0; };
    }
    sumNum += num; sumDen += den; sumPass += pPass; sumCnt += pCnt;
    if (!isBS && den > 0){ sumRate += num / den; sumRateN += 1; }
    const rows = days.map(function(d){
      const e = byDay[d];
      const txt = isBS ? ('编译通过 <b>' + e.p + '</b> / 编译 ' + e.c + ' 次')
                       : ('成功 <b>' + e.ok + '</b> / 共 ' + e.total + ' 次');
      return '<div class="pexec-row">' + execDot(dayOk(e))
        + '<span class="dt">' + rfEsc(d.replace(/-/g, '/')) + '</span>'
        + '<span class="rt">' + txt + '</span></div>';
    }).join('');
    return '<div class="pexec-grp">'
      + '<div class="pexec-hd"><span class="pl">' + rfEsc(g.label) + '</span>'
      + '<span class="rt">' + head
      + (g.runs.length ? '<span class="lnk" data-plruns="' + gi + '">逐条</span>' : '')
      + '</span></div>'
      + (rows || '<div class="pexec-row pexec-none"><span class="dim">区间内没有执行记录</span></div>')
      + '</div>';
  }).join('');
  openModal('执行记录 · 按流水线 · ' + rfEsc(COMP),
    '<div class="pexec-sum">区间 ' + rfEsc(from) + ' ~ ' + rfEsc(to) + ' · ' + EXEC_GROUPS.length
    + ' 条流水线' + (isBS ? ' · 合计编译通过 ' + sumPass + ' / 合计编译 ' + sumCnt + ' 次'
                          : (' · 平均（' + sumRateN + ' 条流水线可用度平均）'
                             + fmtPct(sumRateN ? sumRate / sumRateN : null)
                             + '（Σ可用 ' + sumNum + ' 天 ÷ Σ有执行记录 ' + sumDen + ' 天）'))
    + ' · 按流水线分组，组内按日期倒序</div>'
    + '<div class="pexec-lg">' + execDot(true)
    + (isBS ? '当天编译全部通过' : '当天存在成功构建') + execDot(false)
    + (isBS ? '当天有编译失败' : '有构建但无成功') + execDot(null) + '无记录（未计入）</div>'
    + '<div class="mtbl">' + blocks + '</div>'
    + '<div class="mrec-note">共 ' + EXEC_GROUPS.length + ' 条流水线，组件' + DIMNAME[dim]
    + (isBS ? ' = Σ编译通过 ÷ Σ编译次数（无编译次数的不计入）· '
            : ' = Σ流水线' + DIMNAME[dim] + ' ÷ 流水线条数（无执行记录的不计入）· ')
    + '只统计执行结束时间落在区间内的运行；取消（canceled）的运行计入当日次数，但不算成功；'
    + '点「逐条」看该流水线区间内的逐条运行。</div>');
}
/* 二级弹窗：某条流水线在区间内的逐条运行（不按天汇总，按结束时间倒序） */
function runsTable(rs, from, to){
  const sorted = rs.slice().sort(function(a, b){
    return String(b.end || '').localeCompare(String(a.end || ''));
  });
  const rows = sorted.map(function(x){
    const bn = x.url
      ? '<a href="' + rfEsc(x.url) + '" target="_blank" rel="noopener" title="在 openLiBing 查看构建 #'
        + rfEsc(nz(x.rn)) + '">#' + rfEsc(nz(x.rn)) + '</a>'
      : '<span class="mono">#' + rfEsc(nz(x.rn)) + '</span>';
    const va = (x.iva === true) ? '可用'
      : ((x.iva === false) ? '<span class="dim">不可用</span>' : '<span class="dim">--</span>');
    const bs = (x.bc === null || x.bc === undefined || x.bc === '')
      ? '<span class="dim">--</span>' : (Number(x.bpc) || 0) + '/' + Number(x.bc);
    const cpr = (x.cpr === null || x.cpr === undefined || x.cpr === '')
      ? '<span class="dim">--</span>' : Number(x.cpr).toFixed(1) + '%';
    const ftt = (x.ftt === null || x.ftt === undefined || x.ftt === '' || x.ftt === '流水线未失败')
      ? '<span class="dim">--</span>' : rfEsc(String(x.ftt));
    return '<tr>'
      + '<td class="nowrap">' + bn + '</td>'
      + '<td class="nowrap">' + statusTag(x.status) + '</td>'
      + '<td class="nowrap dim">' + ftt + '</td>'
      + '<td class="nowrap">' + va + '</td>'
      + '<td class="nowrap">' + bs + '</td>'
      + '<td class="nowrap dim">' + cpr + '</td>'
      + '<td class="nowrap dim">' + rfEsc(tm(x.start)) + '</td>'
      + '<td class="nowrap dim">' + rfEsc(tm(x.end)) + '</td>'
      + '</tr>';
  }).join('');
  return '<div class="mtbl"><table class="rca"><colgroup><col style="width:8%"><col style="width:9%">'
    + '<col style="width:12%"><col style="width:8%"><col style="width:11%">'
    + '<col style="width:9%"><col style="width:21.5%"><col style="width:21.5%"></colgroup>'
    + '<thead><tr><th>流水线编号</th><th>状态</th><th>失败类型</th><th>版本可用</th>'
    + '<th>编译成功/次数</th><th>用例通过率</th><th>开始时间</th><th>结束时间</th></tr></thead>'
    + '<tbody>' + rows + '</tbody></table></div>'
    + '<div class="mrec-note">区间 ' + rfEsc(from) + ' ~ ' + rfEsc(to)
    + ' · 区间内每一次运行逐条平铺（不按天汇总），按结束时间倒序 · 运行来自归档 pipeline.json 的 '
    + 'runHistory（按执行结束时间归属日期，只保留区间内的）· 点构建号直接在 openLiBing 打开该次执行。</div>';
}

/* 一行 = 一条执行记录（一次构建）
   任务取该记录自己的任务列表（多任务时列首条 + 「共 N 个任务」），
   用例与证据都只取这一条记录，根因/建议也只取这一条记录的分析结论——不再跨构建合并，
   避免「一行的结论其实来自另一次构建」这种对不上的情况。 */
function recRow(r, i){
  const v = r.verdict || {};
  const an = r.analysis || {};

  /* 任务：该记录内的任务名去重（一次构建可能跑多个 job，都算这条记录的上下文） */
  const tnames = [], tseen = new Set();
  (r.tasks || []).forEach(function(t){
    const n = String(t.jobName || '');
    if (!n || tseen.has(n)) return;
    tseen.add(n);
    tnames.push(n);
  });
  const taskCell = tnames.length
    ? '<b>' + rfEsc(nz(tnames[0])) + '</b>'
      + (tnames.length > 1 ? '<span class="exec-sub">共 ' + tnames.length + ' 个任务</span>' : '')
    : '<span class="dim">--</span>';

  /* 用例列：没有失败用例就只显示 --（成功运行、编译失败、日志类失败都没有用例，
     不套任何解释文案——「setup 阶段批量失败」这种说法对编译任务/日志任务本来就是错的） */
  const isOk = (v.category === 'success');
  const cases = (r.cases || []).filter(function(c){ return c && c.name; });
  let caseCell;
  if (cases.length){
    /* 用例列：第一条用例名打头，「共 N 条失败用例」是可点链接 → 弹窗里逐条给用例详情
       （用例名 / 状态 / 起止时间 / 报告里的断言原文 / 该用例日志） */
    caseCell = rfEsc(cut(cases[0].name, 70))
      + '<span class="lnk exec-sub" data-cases="' + i + '"'
      + ' title="点击查看 ' + cases.length + ' 条失败用例的详情">共 '
      + cases.length + ' 条失败用例</span>';
  } else if (r.__cpr != null){
    /* 任务执行成功、用例没全过：这一行是 load() 从运行历史里补出来的，
       没有抓失败用例清单（不需要），只给「用例比例」——这是用户口径要求的最小信息。 */
    caseCell = '<span class="cpr-cell" title="该次运行任务执行成功，测试用例通过率 '
      + Number(r.__cpr).toFixed(2) + '%，没有达到 100%">用例通过率 '
      + Number(r.__cpr).toFixed(2) + '%</span>';
  } else {
    caseCell = '<span class="dim">--</span>';
  }

  /* 证据行：这条记录的用例级 errorMessages（带时间/位置）+ job 日志错误行；
     只按「整行完全一致」去重（时间或行号不同就各自保留，见 evidenceRows）。
     错误信息列不再展示 AI 结论正文（用户口径：照那句话去证据里根本对不上这些问题）——
     这一列只留「证据」两个字，点开就是本次构建的日志/报告原始行；结论性内容只在展开区的根因/建议里。 */
  const evs = evidenceRows(r);
  const errCell = r.__okRow
    ? '<span class="dim">任务执行成功，无失败日志</span>'
    : (evs.length
        ? '<span class="lnk ev-lnk" data-evidence="' + i + '" title="点击查看日志证据：'
          + evs.length + ' 行（时间 / 级别 / 位置 / 原文）">证据</span>'
        : '<span class="dim">证据 --</span>');

  /* 流水线编号：单条记录的构建号，跳 openLiBing。行首不再带三角（用户口径），
     展开开关挪到最后一列（见下 expCell）。 */
  const flat = isOk;
  const lb = (r.links || {}).openlibing;
  const bn = nz(r.buildNumber);
  const buildCell = (lb
      ? '<a href="' + rfEsc(lb) + '" target="_blank" rel="noopener" title="在 openLiBing 查看构建 #'
        + rfEsc(bn) + '">#' + rfEsc(bn) + '</a>'
      : '<span class="mono">#' + rfEsc(bn) + '</span>')
    + (r.__err ? '<span class="exec-sub" style="color:#f5222d">记录读取失败</span>' : '');

  /* 根因：不做 AI 分析的记录（成功运行 / 指标达标的失败运行，analysis.status=na）显示 --；
     内容按行渲染（见 richPoints），行高随内容自然增长，不做任何字符截断；
     渲染前过一遍 rcBody：报错码与 文件名:行号 出处都剥掉（用户口径：只留原因与怎么解决） */
  const noAI = isOk || an.status === 'na';
  const rc = isOk ? '' : rcBody(an.rootCause || '');
  const rcCell = rc ? richPoints(rc)
    : (noAI ? '<span class="dim">--</span>' : '<span class="dim">待 AI 根因分析</span>');

  const adv = isOk ? '' : rcBody(an.advice || (((r.suggestions || [])[0]) || ''));
  const advCell = adv ? richPoints(adv)
    : (noAI ? '<span class="dim">--</span>' : '<span class="dim">待 AI 根因分析</span>');

  /* 问题分类列：只给一个染色的十类标签（代码编译错误 / 单元测试失败 / 依赖/环境缺失 / 网络/下载失败 /
     资源不足 / 超时 / 权限问题 / 配置/参数错误 / 基础设施/平台 / 无法判定），
     大类由 AI 在 analysis.issueType 里给定；不带大类说明的任何细节（那是定性列和根因分析的事）。
     不必分析的记录（analysis.status=na）没有结论 → --；还没分析的 → 待 AI 根因分析。 */
  /* 定性列：把「可能是哪几种情况」逐条列出来（不带百分比），只有一条时不编号。
     资源规格、节点、镜像、端口、通信域这些细节都放进根因分析，不在这列铺开。 */
  let clsCell, catCell;
  if (isOk){
    /* 成功运行里只有「执行成功但用例没全过」会进表：**标记任务成功 + 给出用例比例**，
       不再套「单元测试失败」分类（这次构建本身是成功的，问题在用例层面，不是任务失败）。
       没有失败用例清单时（load 补的行）只有 cpr，就直接写通过率。 */
    const cprTxt = (r.__cpr == null) ? '' : Number(r.__cpr).toFixed(2) + '%';
    clsCell = r.__okRow ? '<span class="tag green">任务成功</span>' : '<span class="dim">--</span>';
    if (r.__okRow){
      const okTxt = '执行成功，'
        + (cprTxt ? '用例通过率 ' + cprTxt + '（未达 100%）'
                  : '但有 ' + cases.length + ' 条用例没通过');
      catCell = '<div class="ct-item" title="' + rfEsc(okTxt) + '">' + rfEsc(okTxt) + '</div>';
    } else {
      catCell = '<span class="dim">--</span>';
    }
  } else {
    const ty = issueTypeOf(r);
    const cc = causeCell(r);
    const naTxt = an.status === 'na' ? '<span class="dim">--</span>'
      : '<span class="dim">待 AI 根因分析</span>';
    clsCell = ty ? classTag(ty) : naTxt;
    catCell = cc || naTxt;
  }

  /* 展开区：根因分析 + 修复建议（这两段原来各占一列，动辄上千字把整张表拉得没法扫读）。
     出处块已下线：正文里的 file:line 与单独一列胶囊都属于噪音，结论本来就该是「原因 + 怎么解决」；
     原始出处仍留在 analysis.cites，错误信息列点「证据」看到的首行「AI 定位」就是它。
     默认 hidden，点末列 ▸ 或点主行任意位置展开；只渲染一次，展开/收缩只切 hidden，不重排内容。 */
  const det = flat ? '' : '<tr class="rc-det" data-exp-of="' + i + '" hidden>'
    + '<td colspan="10" class="rc-det-cell">'
    + '<div class="rc-blk"><span class="rc-hd">根因分析</span>' + rcCell + '</div>'
    + '<div class="rc-blk"><span class="rc-hd">修复建议</span>' + advCell + '</div>'
    + '</td></tr>';

  /* rc-alt：斑马纹只按主行数奇偶（展开行 rc-det 不参与，否则底纹会跳变） */
  return '<tr class="rc-main' + (i % 2 ? ' rc-alt' : '') + '"'
    + (flat ? '' : ' data-exp-row="' + i + '"') + '>'
    + '<td class="cw1 pl-cell">' + (r.__pl ? rfEsc(r.__pl) : '<span class="dim">--</span>') + '</td>'
    + '<td class="bldg-cell">' + buildCell + '</td>'
    + '<td class="nowrap mono dt-c">' + rfEsc(ymd(r.startedAt)) + '</td>'
    + '<td class="nowrap mono dt-c">' + rfEsc(ymd(r.endedAt)) + '</td>'
    + '<td>' + taskCell + '</td>'
    + '<td class="cls-cell">' + clsCell + '</td>'
    + '<td class="cat-cell">' + catCell + '</td>'
    + '<td class="err-cell">' + errCell + '</td>'
    + '<td>' + caseCell + '</td>'
    + '<td class="exp-cell">'
    + (flat ? '' : '<span class="exp-ic" title="展开 / 收起根因分析与修复建议">▸</span>')
    + '</td>'
    + '</tr>'
    + det;
}

/* 单条记录的定位标签：#168 · 20261109 02:05:47，弹窗标题用（一条记录一行后不再有组标签） */
function buildTag(r){
  const t = ymd((r || {}).endedAt);
  return '#' + nz((r || {}).buildNumber) + (t === '--' ? '' : ' · ' + t);
}

/* ===== 弹窗骨架：两个入口共用（失败用例 / 日志证据） ===== */
function openModal(title, html){
  const m = document.getElementById('mrec');
  const t = document.getElementById('mttl');
  const b = document.getElementById('mbody');
  if (!m || !b) return;
  if (t) t.innerHTML = title;
  b.innerHTML = html;
  m.classList.add('on');
  m.scrollTop = 0;
}
function closeModal(){
  const m = document.getElementById('mrec');
  if (m) m.classList.remove('on');
}

/* ===== 用例列「共 N 条失败用例」→ 弹窗逐条给出用例详情 =====
   数据源 = 该行执行记录（pipeline.json 的 records[N]）的 cases[]（测试报告 failedCases）：
   name / state / beginTime / endTime / description（报告里的断言原文）/ localLog（归档的用例 HTML）。
   一条记录一行后，这里只列这一次构建的失败用例，不再跨构建合并。
   这里只做「有哪些用例失败、失败在哪一步」，日志原文行在「证据」弹窗里看。 */
function openCases(i){
  const r = RECS[i] || {};
  const cases = (r.cases || []).filter(function(c){ return c && c.name; });
  if (!cases.length) return;
  const base = r.__base || '';
  const rows = cases.map(function(c, k){
    /* 用例日志：优先归档里的本地 HTML（localLog），没有再给 openLiBing 的远程报告地址 */
    const lg = c.localLog ? (base + c.localLog) : (c.logUrl || '');
    const lgCell = lg
      ? '<a href="' + rfEsc(lg) + '" target="_blank" rel="noopener">用例日志</a>'
      : '<span class="dim">--</span>';
    return '<tr>'
      + '<td class="expr-txt" title="' + rfEsc(nz(c.name)) + '"><span class="dim">' + (k + 1) + '</span> ' + rfEsc(nz(c.name)) + '</td>'
      + '<td class="nowrap">' + statusTag(c.state) + '</td>'
      + '<td class="cw1 dim">' + rfEsc(nz(c.beginTime)) + '</td>'
      + '<td class="cw1 dim">' + rfEsc(nz(c.endTime)) + '</td>'
      + '<td class="expr-txt" title="' + rfEsc(nz(c.description)) + '">' + rfEsc(nz(c.description)) + '</td>'
      + '<td class="nowrap">' + lgCell + '</td>'
      + '</tr>';
  }).join('');
  openModal('失败用例（' + cases.length + ' 条）｜' + rfEsc(buildTag(r))
      + ' · ' + rfEsc(nz(r.pipeline)),
    '<div class="mtbl"><table class="rca"><colgroup><col style="width:21%"><col style="width:7%">'
    + '<col style="width:13%"><col style="width:13%"><col style="width:39%"><col style="width:7%"></colgroup>'
    + '<thead><tr><th>用例</th><th>状态</th><th>开始时间</th><th>结束时间</th>'
    + '<th>失败详情（测试报告断言原文）</th><th>日志</th></tr></thead>'
    + '<tbody>' + rows + '</tbody></table></div>'
    + '<div class="mrec-note">用例清单取自测试报告 failedCases（本次构建共 '
    + cases.length + ' 条全部失败用例），失败详情按原文完整输出、不做截断；'
    + '「用例日志」打开归档里的该用例 HTML 报告。日志原始错误行见「错误信息」列的「证据」。</div>');
}

/* ===== 错误信息列「证据」→ 弹窗逐行输出日志证据（时间 / 级别 / 位置 / 整行原文） =====
   行级信息（time / level / loc / line / 原文）直接从归档记录里取，不再堆在单元格里，
   单元格只留「证据」两个字。证据只取这一行对应的那一次构建，且只做「整行完全一致」的去重。 */
function openEvidence(i){
  const r = RECS[i] || {};
  const evs = evidenceRows(r);
  if (!evs.length) return;
  const rows = evs.map(function(x){
    /* 位置：log 行给 line（L3340），用例内错误给 loc:line（plugin.py:999:3340） */
    const loc = x.loc
      ? rfEsc(x.loc).replace(/\n/g, '<br>') + (x.line == null ? '' : ':' + rfEsc(x.line))
      : (x.line == null ? '<span class="dim">--</span>' : 'L' + rfEsc(x.line));
    const who = [nz(x.task), x.case || ''].filter(function(s){ return s && s !== '--'; }).join(' / ');
    const lv = (x.level && x.level !== '--') ? rfEsc(x.level) : '<span class="dim">--</span>';
    return '<tr>'
      + '<td class="cw1">' + (x.time ? rfEsc(x.time) : '<span class="dim">--</span>') + '</td>'
      + '<td class="cw">' + lv + '</td>'
      + '<td class="cw dim">' + rfEsc(who || '--') + '</td>'
      + '<td class="cw dim">' + loc + '</td>'
      + '<td class="expr-txt" title="' + rfEsc(nz(x.text)) + '">' + rfEsc(nz(x.text)) + '</td>'
      + '</tr>';
  }).join('');
  openModal('日志证据（' + evs.length + ' 行）｜' + rfEsc(buildTag(r))
      + ' · ' + rfEsc(nz(r.pipeline)),
    '<div class="mtbl"><table class="rca"><colgroup><col style="width:14%"><col style="width:8%">'
    + '<col style="width:20%"><col style="width:14%"><col style="width:44%"></colgroup>'
    + '<thead><tr><th>时间</th><th>级别</th><th>任务 / 用例</th><th>位置</th>'
    + '<th>证据原文（整行，不截断）</th></tr></thead>'
    + '<tbody>' + rows + '</tbody></table></div>'
    + '<div class="mrec-note">'
    + '证据全部取自本次构建的归档原文（用例级 errorMessages 与 job 日志行），'
    + '只去掉整行完全一致的重复行：时间或行号不同就各自保留（那是同一条消息在不同时刻/不同行的发生）。'
    + '行级错误行每任务最多 8 条（首尾各取一半，尾部通常是致命行），用例级消息每用例最多 3 条，'
    + 'pytest 汇总行不计入（它只列失败用例名、不含原因）。'
    + '用例级证据取自测试报告 errorMessages（自带时间与位置）；'
    + 'job 日志证据的源行本就带时间戳，已抽到「时间」列；没有时间戳的「测试报告」断言显示 --。'
    + '需要完整原文时按「位置」列的文件名:行号取同一归档目录的 pipeline.evidence.json'
      + '（证据原文已逐行内嵌，路径与行号跟「位置」列完全一致）。</div>');
}

/* 弹窗入口（记录明细页已下线，点构建号直接跳 openLiBing）：
   data-cases       → 根因表「共 N 条失败用例」→ 该次构建的失败用例详情
   data-evidence    → 根因表「证据」→ 该次构建的日志证据逐行
   data-exp-row     → 根因表主行 → 展开 / 收起该行的根因分析与修复建议全文
   注意：data-cases / data-evidence 的取值是「记录下标」（RECS[i]）——
   表格已拆成一条构建一行，不再有聚合行 */
/* 展开 / 收起：根因/建议已经从列里搬进紧跟主行的那条 rc-det 行（colspan 撑满全列），
   这里只切 hidden，不重排内容；同时把末列的大三角在 ▸ / ▾ 之间换一下 */
function toggleExp(i){
  const det = document.querySelector('tr.rc-det[data-exp-of="' + i + '"]');
  const main = document.querySelector('tr.rc-main[data-exp-row="' + i + '"]');
  if (!det) return;
  const open = det.hasAttribute('hidden');
  if (open) det.removeAttribute('hidden'); else det.setAttribute('hidden', '');
  const ic = main && main.querySelector('.exp-ic');
  if (ic) ic.textContent = open ? '▾' : '▸';
}
document.addEventListener('click', function(ev){
  const el = (ev.target && ev.target.closest)
    ? ev.target.closest('[data-cases],[data-evidence],[data-exp-row],[data-execpipes],[data-plruns],.exp-ic,#mclose,#mrec')
    : null;
  if (!el) return;
  if (el.id === 'mclose'){ closeModal(); return; }
  if (el.id === 'mrec' && ev.target === el){ closeModal(); return; }
  if (el.hasAttribute('data-execpipes')){ ev.preventDefault(); openExecByPipe(); return; }
  if (el.hasAttribute('data-plruns')){
    ev.preventDefault();
    const g = EXEC_GROUPS[Number(el.getAttribute('data-plruns'))];
    if (!g) return;
    const ctx = CTX || {};
    openModal2('执行记录（' + g.runs.length + ' 次运行）｜' + rfEsc(g.label),
      runsTable(g.runs, ctx.from || '', ctx.to || ''));
    return;
  }
  if (el.hasAttribute('data-cases')){ ev.preventDefault(); openCases(Number(el.getAttribute('data-cases'))); return; }
  if (el.hasAttribute('data-evidence')){ ev.preventDefault(); openEvidence(Number(el.getAttribute('data-evidence'))); return; }
  if (ev.target.closest('.exp-ic') || el.hasAttribute('data-exp-row')){
    /* 主行内的构建号链接 / 共 N 条失败用例 / 证据 各有自己的动作，不抢它们的点击 */
    if (ev.target.closest('a,.lnk')) return;
    ev.preventDefault();
    const row = ev.target.closest('tr.rc-main[data-exp-row]');
    if (row){ toggleExp(row.getAttribute('data-exp-row')); }
  }
});
document.addEventListener('keydown', function(ev){ if (ev.key === 'Escape') closeModal(); });

boot();
""".replace("__INDEX__", INDEX_NAME)


def workday_cal(days):
    """页面可选区间（归档首日 ~ 今天）的**法定工作日日历**，注入成页面的 WD_CAL。

    版本可用度的分母已改成「该流水线在区间内有执行记录的天数」，这份日历现在只用于
    顶部「工作日 N 天」的展示，以及老归档缺 vaDetail 时的分母兜底（见 lib/ui.py 的 vaDenOf）。
    仍按用户所选子区间覆盖全部可选范围：筛选条把最小日夹到归档首日、
    最大日放到「今天」，因此取 [min(归档首日, 今天), max(归档末日, 今天)]。

    数据源与采集同源（lib/workdays.build_workday_set：法定节假日 + 调休补班，
    优先 config.workday_overrides），日历与后端 step_2 采集用的是同一份，跨长假也不会分叉。
    取不到就返回空列表，页面自动退回周一~周五（见 lib/ui.py 的 wdInRange）。
    """
    if not days:
        return []
    today = datetime.date.today().isoformat()
    first = min(days[0][0], today)
    last = max(days[-1][0], today)
    try:
        wds, notes = build_workday_set(first, last, load_config())
    except Exception as e:                       # 日历拿不到不能挡住出页
        print("!! 工作日日历计算失败，页面退回周一~周五：%s" % e)
        return []
    cal = sorted(str(x) for x in wds)
    print("== 工作日日历 %s ~ %s：%d 个工作日（%s）"
          % (first, last, len(cal), "；".join(notes)))
    return cal


# ---------------------------------------------------------------- PR 页（triton-ascend）
# 数据源不是流水线归档，而是 tools/collect_pr_daily.py 每日从 pod-history-api 采集的
# archive/<日>/Triton_Ascend/pr_history.json（PR 维度的 CI job：执行时长 / 排队时长 / 卡时 / 成败）。
# 页面壳与其他页同源：header + toolbar + 取数脚本，区间切换逐日 fetch、404 容错（那天没采/没数据）。
PR_NAME = "pr.html"
PR_TITLE = ui.BRAND_TITLE + " · triton-ascend PR 数据"

PR_BODY = (
    '<div class="wrap">\n'
    '  ' + ui.header() + '\n'
    '  ' + ui.toolbar() + '\n'
    '  <a class="chip" href="' + INDEX_NAME + '" id="back">← 返回总览</a>\n'
    '  <h1>triton-ascend PR CI 数据 <span class="sub">数据源：pod-history-api（每日 8 点采集昨日数据）</span></h1>\n'
    '  <div class="cards" id="cards"></div>\n'
    '  <h3 class="pr-h">每日汇总</h3>\n'
    '  <div class="tbl-wrap pr-tbl">\n'
    '    <table class="rca" id="dayTbl">\n'
    '      <thead><tr>\n'
    '        <th>日期</th>\n'
    '        <th>PR 数</th>\n'
    '        <th>Job 数</th>\n'
    '        <th>失败 Job</th>\n'
    '        <th>Job 成功率</th>\n'
    '        <th>平均执行时长</th>\n'
    '        <th>平均排队时长</th>\n'
    '        <th>最大排队时长</th>\n'
    '        <th>NPU 卡时</th>\n'
    '        <th>当日版本可用</th>\n'
    '      </tr></thead>\n'
    '      <tbody id="dayTb"><tr><td colspan="10" class="loading">正在读取归档…</td></tr></tbody>\n'
    '    </table>\n'
    '  </div>\n'
    '  <h3 class="pr-h">PR 明细（区间内按 PR + 工作流聚合）</h3>\n'
    '  <div class="tbl-wrap pr-tbl">\n'
    '    <table class="rca" id="prTbl">\n'
    '      <thead><tr>\n'
    '        <th>PR</th>\n'
    '        <th>工作流</th>\n'
    '        <th>活跃天</th>\n'
    '        <th>Job 数</th>\n'
    '        <th>失败 Job</th>\n'
    '        <th>执行时长（合计 / 均值）</th>\n'
    '        <th>排队时长（均值 / 最大）</th>\n'
    '        <th>NPU 卡时</th>\n'
    '        <th>首次开始</th>\n'
    '        <th>Jobs</th>\n'
    '      </tr></thead>\n'
    '      <tbody id="prTb"><tr><td colspan="10" class="loading">正在读取归档…</td></tr></tbody>\n'
    '    </table>\n'
    '  </div>\n'
    '  <div class="tnote" id="tnote"></div>\n'
    '</div>\n'
    '<div class="modal" id="mrec">\n'
    '  <div class="modal-box">\n'
    '    <div class="modal-hd"><div class="hd-l"><span id="mttl"></span></div>'
    '<button type="button" id="mclose">关闭</button></div>\n'
    '    <div id="mbody"><div class="mtbl"><table class="rca" id="jobTbl"></table></div></div>\n'
    '  </div>\n'
    '</div>'
)

PR_EXTRA_CSS = SHARED_MODAL_CSS + """
/* PR 页：可以整页滚（数据量不定，不做一屏定高） */
.wrap{gap:0}
#back{margin:2px 0 8px;align-self:flex-start}
h1{font-size:19px;margin:2px 0 10px}
h1 .sub{font-size:12px;font-weight:400;color:#8a93a3;margin-left:10px}
/* 汇总卡：一行流式小卡（数值 + 标签） */
.cards{display:flex;flex-wrap:wrap;gap:10px;margin:2px 0 4px}
.cards .c{flex:0 0 auto;min-width:108px;background:#f7f9fc;border:1px solid var(--line2);
  border-radius:8px;padding:8px 14px}
.cards .c b{display:block;font-size:17px;font-weight:600;color:#16233a;line-height:1.5}
.cards .c span{display:block;font-size:11.5px;color:#7c8698}
/* 区块头（浅蓝底，与二级页 h3 同款） */
h3.pr-h{font-size:15px;font-weight:600;color:#16233a;line-height:1.6;
  margin:16px 0 8px;padding:8px 12px;background:#f0f5ff;border-radius:6px}
/* 两张表：定高内部滚动，页面其余部分不跳 */
.pr-tbl{flex:1 1 320px;min-height:260px;overflow:auto;border:1px solid var(--line2);border-radius:8px}
.pr-tbl table.rca{margin:0}
.pr-tbl table.rca thead th{position:sticky;top:0;z-index:2;background:#f7f9fc;text-align:center}
.pr-tbl table.rca td.ctr{text-align:center}
table.rca a.lnk{color:var(--acc);text-decoration:none}
table.rca a.lnk:hover{text-decoration:underline}
.tnote{flex:0 0 auto;font-size:11.5px;color:#7c8698;line-height:1.65;margin:9px 0 0}
.ok{color:#0a7d33;font-weight:600}
.bad{color:#c0392b;font-weight:600}
.dim{color:#9aa3af}
"""

PR_JS = r"""
/* ================= PR 页：读每日 pr_history.json，按区间聚合 =================
   数据链：archive/<年>/<年-月>/<日>/Triton_Ascend/pr_history.json（每日一份，collect_pr_daily.py 产出）
   区间切换 = 逐日 fetch（404 = 当日无 PR 数据，跳过不报错）；版本可用度取同日 _day.json 的
   pipelines[].vaDetail（component=Triton_Ascend，availDays>0 即当日可用）与区间内最新一天的
   products[].versionAvailabilityRate（累计值，口径注明「截至该日累计」）。 */
let ARCH_ROOT = '../';
const ROOT_CANDIDATES = ['../', './', '../../', '../../../'];
function probeRoot(){
  let i = 0;
  function step(){
    if (i >= ROOT_CANDIDATES.length)
      return Promise.reject(new Error('archive/_index.json → HTTP 404（已依次试过：'
        + ROOT_CANDIDATES.join('  ') + '）'));
    const cand = ROOT_CANDIDATES[i++];
    return jget(cand + 'archive/_index.json').then(function(x){
      ARCH_ROOT = cand; return x;
    }).catch(function(e){ if (isNotFound(e)) return step(); throw e; });
  }
  return step();
}
function jget(p){
  return fetch(p, {cache: 'no-store'}).then(function(r){
    if (!r.ok) throw new Error(p + ' → HTTP ' + r.status);
    return r.json();
  });
}
function isNotFound(e){
  const m = String((e && e.message) || e);
  return /HTTP 40[34]/.test(m) || /\b(404|ENOENT)\b/.test(m) || /not\s*found/i.test(m);
}
function nz(v){ return (v === null || v === undefined || v === '') ? '--' : String(v); }
/* 秒 -> 人读时长：3723 -> 1h02m，292 -> 4m52s，45 -> 45s，null -> -- */
function fmtDur(sec){
  if (sec === null || sec === undefined || isNaN(sec)) return '--';
  const s = Math.round(Number(sec));
  if (s < 60) return s + 's';
  if (s < 3600) return Math.floor(s / 60) + 'm' + String(s % 60).padStart(2, '0') + 's';
  return Math.floor(s / 3600) + 'h' + String(Math.floor((s % 3600) / 60)).padStart(2, '0') + 'm';
}
function dayPath(d){ return 'archive/' + d.slice(0,4) + '/' + d.slice(0,7) + '/' + d; }
function eachDay(f, t){
  const out = []; const d = new Date(f + 'T00:00:00'); const end = new Date(t + 'T00:00:00');
  /* 本地日期逐日推进：不能用 toISOString()（UTC 会把东八区午夜倒回前一天） */
  function ymd(x){ return x.getFullYear() + '-' + String(x.getMonth() + 1).padStart(2, '0')
    + '-' + String(x.getDate()).padStart(2, '0'); }
  while (d <= end && out.length < 400){ out.push(ymd(d)); d.setDate(d.getDate() + 1); }
  return out;
}
function avg(a){ return a.length ? a.reduce(function(x,y){ return x+y; }, 0) / a.length : null; }

const DAYS = [];          /* _index.json 的归档日（给筛选条定上下界） */
let JOBS = [];            /* 最近一次区间取回的全部 job（弹窗用） */
let PR_ROWS = [];         /* 最近一次渲染的 PR 明细行（弹窗按下标取，不做字符串比较） */

/* 逐日取 pr_history.json；同日的 _day.json 顺带取当日版本可用（一并 404 容错） */
function loadRange(f, t){
  const dates = eachDay(f, t);
  Promise.all(dates.map(function(d){
    return jget(ARCH_ROOT + dayPath(d) + '/Triton_Ascend/pr_history.json')
      .catch(function(e){ if (!isNotFound(e)) throw e; return null; });
  })).then(function(docs){
    render(docs, dates);
  }).catch(function(e){
    document.getElementById('dayTb').innerHTML =
      '<tr><td colspan="10" class="loading">读取失败：' + rfEsc(e.message) + '</td></tr>';
  });
}

function render(docs, dates){
  const jobs = [];
  const dayRows = [];
  dates.forEach(function(d, i){
    const doc = docs[i];
    if (doc && doc.jobs && doc.jobs.length){
      doc.jobs.forEach(function(j){ j._date = d; jobs.push(j); });
      dayRows.push({date: d, s: doc.summary || {}, doc: doc});
    }
  });
  JOBS = jobs;
  const tb = document.getElementById('dayTb');
  if (!dayRows.length){
    tb.innerHTML = '<tr><td colspan="10" class="loading">区间内暂无 PR CI 数据'
      + '（pr_history.json 由每日采集任务产出，当日无数据或尚未采集都会显示本提示）</td></tr>';
    document.getElementById('prTb').innerHTML =
      '<tr><td colspan="10" class="loading">区间内暂无 PR CI 数据</td></tr>';
    renderCards([], null);
    return;
  }
  /* 每日汇总行（含当日版本可用：vaDetail.availDays>0） */
  Promise.all(dayRows.map(function(r){
    return jget(ARCH_ROOT + dayPath(r.date) + '/_day.json').then(function(day){
      const p = ((day || {}).pipelines || []).filter(function(x){
        return String(x.component || '') === 'Triton_Ascend';
      })[0];
      r.va = p && p.vaDetail ? (p.vaDetail.availDays > 0 ? 'ok' : 'bad') : 'na';
    }).catch(function(){ r.va = 'na'; });
  })).then(function(){
    tb.innerHTML = dayRows.map(function(r){
      const s = r.s;
      const ok = s.jobCount ? (1 - (s.failCount || 0) / s.jobCount) : null;
      const va = r.va === 'ok' ? '<span class="ok">可用</span>'
               : (r.va === 'bad' ? '<span class="bad">不可用</span>' : '<span class="dim">--</span>');
      return '<tr>'
        + '<td>' + r.date + '</td>'
        + '<td class="ctr">' + nz(s.prCount) + '</td>'
        + '<td class="ctr">' + nz(s.jobCount) + '</td>'
        + '<td class="ctr">' + nz(s.failCount) + '</td>'
        + '<td class="ctr">' + (ok === null ? '--' : (ok * 100).toFixed(1) + '%') + '</td>'
        + '<td class="ctr">' + fmtDur(s.execSecondsAvg) + '</td>'
        + '<td class="ctr">' + fmtDur(s.waitSecondsAvg) + '</td>'
        + '<td class="ctr">' + fmtDur(s.waitSecondsMax) + '</td>'
        + '<td class="ctr">' + nz(s.cardHours) + '</td>'
        + '<td class="ctr">' + va + '</td>'
        + '</tr>';
    }).join('');
  });

  /* PR 明细：区间内按 (PR, workflow) 聚合 */
  const grp = {};
  jobs.forEach(function(j){
    if (j.prNumber === null || j.prNumber === undefined) return;   /* 分支触发的没有 PR 号，不入本表 */
    const k = j.prNumber + '|' + (j.workflow || '');
    (grp[k] = grp[k] || {pr: j.prNumber, wf: j.workflow || '--', org: j.org, repo: j.repo,
                         jobs: [], days: {}}).jobs.push(j);
    grp[k].days[j._date] = 1;
  });
  const rows = Object.keys(grp).map(function(k){ return grp[k]; })
    .sort(function(a, b){
      const la = a.jobs[0].createdAt || '', lb = b.jobs[0].createdAt || '';
      return lb.localeCompare(la) || (b.pr - a.pr);
    });
  PR_ROWS = rows;
  document.getElementById('prTb').innerHTML = rows.length ? rows.map(function(r, i){
    const execs = r.jobs.map(function(j){ return j.execSeconds || 0; });
    const waits = r.jobs.map(function(j){ return j.waitSeconds; }).filter(function(w){ return w !== null && w !== undefined; });
    const fails = r.jobs.filter(function(j){ return j.status === 'rejected'; }).length;
    const gh = 'https://github.com/' + (r.org || 'triton-lang') + '/' + (r.repo || 'triton-ascend') + '/pull/' + r.pr;
    return '<tr>'
      + '<td><a class="lnk" href="' + gh + '" target="_blank">#' + r.pr + '</a></td>'
      + '<td class="cw">' + rfEsc(r.wf) + '</td>'
      + '<td class="ctr">' + Object.keys(r.days).length + '</td>'
      + '<td class="ctr"><a class="lnk jobs-lnk" data-i="' + i + '">' + r.jobs.length + ' 个</a></td>'
      + '<td class="ctr">' + (fails ? '<span class="bad">' + fails + '</span>' : '0') + '</td>'
      + '<td class="ctr">' + fmtDur(execs.reduce(function(a,b){ return a+b; }, 0)) + ' / ' + fmtDur(avg(execs)) + '</td>'
      + '<td class="ctr">' + fmtDur(avg(waits)) + ' / ' + fmtDur(waits.length ? Math.max.apply(null, waits) : null) + '</td>'
      + '<td class="ctr">' + (r.jobs.reduce(function(a,j){ return a + (j.npu || 0) * (j.execSeconds || 0); }, 0) / 3600).toFixed(2) + '</td>'
      + '<td class="cw1">' + rfEsc(r.jobs[0].createdAt) + '</td>'
      + '<td class="ctr"><a class="lnk jobs-lnk" data-i="' + i + '">查看</a></td>'
      + '</tr>';
  }).join('') : '<tr><td colspan="10" class="loading">区间内没有带 PR 号的 job（分支触发的运行不带 PR 号）</td></tr>';

  renderCards(jobs, dayRows);
}

/* 汇总卡：PR/Job/成功率/时长/卡时 + 版本可用度（累计值，取区间内最新一天的 _day.json） */
function renderCards(jobs, dayRows){
  const el = document.getElementById('cards');
  const prs = {}; jobs.forEach(function(j){ if (j.prNumber !== null) prs[j.prNumber] = 1; });
  const execs = jobs.map(function(j){ return j.execSeconds; }).filter(function(v){ return v; });
  const waits = jobs.map(function(j){ return j.waitSeconds; }).filter(function(v){ return v !== null && v !== undefined; });
  const fails = jobs.filter(function(j){ return j.status === 'rejected'; }).length;
  const card = function(v, label){ return '<div class="c"><b>' + v + '</b><span>' + label + '</span></div>'; };
  let html = card(Object.keys(prs).length, 'PR 数')
    + card(jobs.length, 'Job 数')
    + card(fails, '失败 Job')
    + card(jobs.length ? ((1 - fails / jobs.length) * 100).toFixed(1) + '%' : '--', 'Job 成功率')
    + card(fmtDur(avg(execs)), '平均执行时长')
    + card(fmtDur(avg(waits)), '平均排队时长')
    + card((jobs.reduce(function(a,j){ return a + (j.npu || 0) * (j.execSeconds || 0); }, 0) / 3600).toFixed(2), 'NPU 卡时（小时）');
  el.innerHTML = html + '<div class="c" id="vaCard"><b>--</b><span>版本可用度</span></div>';
  if (dayRows && dayRows.length){
    const last = dayRows[dayRows.length - 1].date;
    jget(ARCH_ROOT + dayPath(last) + '/_day.json').then(function(day){
      const p = ((day || {}).products || []).filter(function(x){
        return String(x.product || '').indexOf('Triton') >= 0;
      })[0];
      const c = document.getElementById('vaCard');
      if (p && c && p.versionAvailabilityRate !== null && p.versionAvailabilityRate !== undefined){
        c.innerHTML = '<b>' + (p.versionAvailabilityRate * 100).toFixed(1) + '%</b>'
          + '<span>版本可用度（截至 ' + last + ' 累计）</span>';
      } else if (c){
        c.innerHTML = '<b>--</b><span>版本可用度（当日 _day.json 无 Triton 产品）</span>';
      }
    }).catch(function(){});
  }
}

/* Jobs 弹窗：一个 PR 的全部 job（按下标从 PR_ROWS 取，避免转义后的字符串比较） */
function showJobs(i){
  const r = PR_ROWS[i];
  if (!r) return;
  const list = r.jobs;
  document.getElementById('mttl').textContent = '#' + r.pr + ' · ' + r.wf + ' · ' + list.length + ' 个 job';
  document.getElementById('jobTbl').innerHTML =
    '<colgroup><col style="width:22%"><col style="width:8%"><col style="width:8%"><col style="width:9%">'
    + '<col style="width:9%"><col style="width:6%"><col style="width:8%"><col style="width:10%">'
    + '<col style="width:20%"></colgroup>'
    + '<thead><tr><th>Job</th><th>日期</th><th>状态</th><th>执行时长</th><th>排队时长</th>'
    + '<th>NPU</th><th>卡型</th><th>集群</th><th>开始 / 结束（北京时间）</th></tr></thead><tbody>'
    + list.map(function(j){
      const bad = j.status === 'rejected';
      return '<tr>'
        + '<td class="cw">' + rfEsc(j.job || j.pod || '--') + '</td>'
        + '<td class="ctr">' + j._date + '</td>'
        + '<td class="ctr">' + (bad ? '<span class="bad">' : '<span>') + rfEsc(j.status) + '</span></td>'
        + '<td class="ctr">' + fmtDur(j.execSeconds) + '</td>'
        + '<td class="ctr">' + fmtDur(j.waitSeconds) + '</td>'
        + '<td class="ctr">' + nz(j.npu) + '</td>'
        + '<td class="ctr">' + rfEsc(j.deviceModel || '--') + '</td>'
        + '<td class="ctr">' + rfEsc(j.cluster || '--') + '</td>'
        + '<td class="cw1">' + rfEsc(j.createdAt) + '<br>' + rfEsc(j.endedAt) + '</td>'
        + '</tr>';
    }).join('') + '</tbody>';
  document.getElementById('mrec').classList.add('on');
}
document.getElementById('mclose').addEventListener('click', function(){
  document.getElementById('mrec').classList.remove('on');
});
document.getElementById('mrec').addEventListener('click', function(e){
  if (e.target === this) this.classList.remove('on');
});

/* 初始化：先探数据根 → 拿归档日清单给筛选条定界 → 挂 onChange 逐日取数 */
probeRoot().then(function(idx){
  (idx.days || []).forEach(function(d){ DAYS.push(d); });
  const min = DAYS.length ? DAYS[0] : null;
  const max = DAYS.length ? DAYS[DAYS.length - 1] : null;
  rfInit({min: min, max: max, onChange: loadRange});
}).catch(function(e){
  document.getElementById('dayTb').innerHTML =
    '<tr><td colspan="10" class="loading">读取失败：' + rfEsc(e.message) + '</td></tr>';
  document.getElementById('prTb').innerHTML = '';
});

/* 统计口径（页面底部，一条一行） */
(function(){
  document.getElementById('tnote').innerHTML =
    '<div class="tn-hd">统计口径</div><ul class="tn-list">'
    + '<li><b>数据源</b>：pod-history-api（CI job pod 生命周期），每日 8 点采集昨日 00:00~24:00（北京时间）</li>'
    + '<li><b>执行时长</b>：job pod 存活秒数（ttl_seconds）；<b>排队时长</b>：调度等待（wait_duration）</li>'
    + '<li><b>失败 Job</b>：pod 状态 rejected；released / expired 均视为跑完（expired = 跑完后被平台清理）</li>'
    + '<li><b>NPU 卡时</b>：卡数 × 执行秒数 ÷ 3600；分支触发的运行（无 PR 号）不进 PR 明细，只进汇总</li>'
    + '<li><b>版本可用度</b>：当日可用 = 当天有成功执行且用例通过率 100%（与首页同口径）；'
        + '汇总卡为截至区间内最新采集日的累计值</li>'
    + '</ul>';
})();
"""


def write_page(path, title, body, script, extra_css, workdays=None):
    path.write_text(ui.page(title, body, script, extra_css, workdays), encoding="utf-8")
    return path


def main():
    HTML_DIR.mkdir(parents=True, exist_ok=True)
    days = iter_archive_days()
    # 两页共用同一份工作日日历：首页的分母、二级页的分母、筛选条上的「工作日 N 天」必须是同一个数
    wd_cal = workday_cal(days)
    idx = write_page(HTML_DIR / INDEX_NAME, INDEX_TITLE, INDEX_BODY,
                     CALIBER_JS + INDEX_JS, INDEX_EXTRA_CSS, wd_cal)
    # 统计口径只在首页注入（用户要求「只要首页的口径即可」）：二级页不再挂 #tnote，
    # 也就没有第二份文案需要跟着改，口径永远只有 CALIBER_JS 这一处
    det = write_page(HTML_DIR / DETAIL_NAME, DETAIL_TITLE, DETAIL_BODY,
                     DETAIL_JS, DETAIL_EXTRA_CSS, wd_cal)
    # PR 页：数据源是每日 pr_history.json（pod-history-api 采集），不读 pipeline.json，壳同源
    pr = write_page(HTML_DIR / PR_NAME, PR_TITLE, PR_BODY, PR_JS, PR_EXTRA_CSS, wd_cal)

    page_doc = doc("step_9_pages", files=[INDEX_NAME, DETAIL_NAME], dayCount=len(days))
    meta = {
        "_doc": page_doc,
        "generatedAt": page_doc["generatedAt"],
        "dayCount": len(days),
        "firstDay": days[0][0] if days else None,
        "lastDay": days[-1][0] if days else None,
        "shell": {"title": ui.BRAND_TITLE, "subtitle": ui.BRAND_SUB,
                  "order": ["品牌行（标题 + 一行副标题）", "公共日期筛选条（吸顶 · 系统级全局筛选）", "页面自身内容"],
                  "note": "首页与二级页共用 lib/ui.py 的同一份 header + toolbar，且都排在最前；"
                          "筛选条 position:sticky 吸顶，切页/滚动都不动位置，语义始终是整个系统的数据筛选。"
                          "首页表格行高在渲染后按「可用高度 / 行数」写入 --rowh，13 条数据刚好铺满表格区。"},
        "files": [
            {"name": INDEX_NAME, "role": "首页（组件级概览）",
             "reads": ["archive/_index.json", "archive/_products.json",
                       "archive/<年>/<年-月>/<日>/_day.json"],
             "fields": ["开源组件", "版本可用度", "构建成功率", "流水线数",
                        "待分析（可用度）", "待分析（编译）"],
             "drilldown": "首页不做展开：「流水线数」是蓝色可点数字，点开弹窗列出该组件全部流水线的"
                          "版本可用度与构建成功率（后三列居中、带红/黄/绿灯色），"
                          "最后一列「执行记录」是「查看」链接，点开栈式二级弹窗列出区间内每一次运行"
                          "（可再点「查看」回看该次构建的执行详情）；"
                          "「待分析流水线数」非零时也可点（同旧版 .lnk），直接进二级页 "
                          "?comp=组件&dim=va|bs 看该组件的执行记录根因明细（中间那层「组件级流水线清单」已下线）；"
                          "根因明细里点「共 N 条失败用例」/「证据」各自弹窗",
             "note": "开源组件行取自 _products.json 的全量清单（无待分析流水线的显示 0）；"
                     "表格版式与旧报告 table.ov 一致，不含任何展开/折叠行"},
            {"name": DETAIL_NAME, "role": "二级页（?comp=组件&dim=va|bs 组件级根因明细；老链接 ?key=组件|流水线 仍可打开，只取其中的组件名）",
             "reads": ["archive/<日>/_day.json", "archive/<日>/<组件>/<流水线>/pipeline.json"],
             "fields": ["流水线", "流水线编号", "开始时间", "结束时间", "任务", "用例", "错误信息", "问题分类", "定性"],
             "rowExpand": {"trigger": "每行第 2 列行首 ▸（点整行也可）；执行成功的行没有 ▸、点了也不展开",
                           "content": ["根因分析", "修复建议"],
                           "note": "根因 / 建议不再占列：这两段占着 34% 宽度会把整张表拉得没法扫读，"
                                   "改成点开该行后在下方以 colspan=9 的展开行显示全文，默认收起；"
                                   "表里只放失败记录：执行成功的运行不进表（唯一例外是「执行成功但用例没全过」，"
                                   "那种行没有根因可讲，不取证、不送 AI，只标「任务成功」+ 用例通过率，不给展开）；"
                                   "展开只切 hidden，不重排内容、不重新请求数据；"
                                   "展开区只有这两块，「出处」胶囊已下线（正文与单独的出处列表都属噪音，"
                                   "原始 cites 仍留在 analysis 里，但错误信息列已不再展示 AI 结论正文）；"
                                   "正文渲染前过一遍 rcBody：封装码（DEV-CODECI-35002）、错误码（error code is 4）、"
                                   "文件名:行号 出处以及结尾的「已排除的干扰项」整段一律剥掉，只留「原因 + 怎么解决」"},
             "note": "单层结构：一个组件一个浅蓝底 h3 区块 + 一张 9 列固定列宽（12/7/11/11/10/12/17/9/11）的 table.rca，"
                     "table-layout:fixed 保证列宽不随内容变化；"
                     "区块头比率与首页组件行同一个口径：版本可用度 = 该组件各流水线版本可用度的算术平均"
                     "（每条流水线的分母是它自己在区间内有执行记录的天数，当天有运行开始或结束才算，没启动的日子不进分母），"
                     "构建成功率 = Σ编译通过 ÷ Σ编译次数，首页点进来数字对得上；"
                     "每行 = 一次执行记录（一条构建一行，不做任何聚合）：首列「流水线」标明这条记录属于哪一条流水线"
                     "（区间内该组件可能有多条流水线、每条每天一个归档目录，全部收进同一张表）；"
                     "同一天同一流水线的多次构建各自成行，"
                     "各自带本次构建的用例、证据与 AI 根因/建议；已删除「根因编号」列；"
                     "行序按「结束时间降序」（最近结束的构建在最上面，时间缺失的排最后，同一时间按构建号降序）；"
                     "「开始时间 / 结束时间」两列取自记录的 startedAt / endedAt，格式化为 yyyy-MM-dd hh:mm:ss"
                     "（如 2026-11-09 02:05:47），字号 12.5px 略大于表内正文，用于直接定位是哪一次构建；"
                     "「失败类型分布」是纯展示的计数条（按归档时的规则分类数记录条数 + 占比），"
                     "任务执行成功但用例没跑满的记作「用例未 100%」（不再出现英文 success），"
                     "不响应点击、不做表内筛选；"
                     "「流水线编号」列单条构建号，各自跳 openLiBing；"
                     "「用例」列展示第一条失败用例名 + 可点的「共 N 条失败用例」→ 弹窗列本次构建的全部失败用例；"
                     "「错误信息」列**只放一个可点的「证据」**（用户口径：不再展示 AI 结论正文，那句话跟着证据对不上问题）"
                     "→ 弹窗逐行输出本次构建的日志/报告原始行（时间/级别/位置/原文），"
                     "证据只按「整行完全一致」去重：时间或行号不同就各自保留；"
                     "「问题分类」列只放一个染色色标，取 analysis.issueType 的十个固定类别（参考仓的 10 类根因）"
                     "（代码编译错误 / 单元测试失败 / 依赖/环境缺失 / 网络/下载失败 / 资源不足 / 超时 / "
                     "权限问题 / 配置/参数错误 / 基础设施/平台 / 无法判定）；"
                     "老归档没有 issueType 时只按最强信号兜底识别（权限 / 资源 / 依赖环境 / 编译 / 单测 / 网络 / 超时），认不出就显示 --、不硬贴标签；"
                     "「定性」列紧跟其后，把根因正文里「是什么坏了」逐条列出来（大白话、不给百分比、不做权重排序），"
                     "只有一条时不带编号，多条才按 1. 2. 3. 排；"
                     "资源规格、节点、镜像、端口、通信域这类细节只写在根因分析里，两列都不铺开；"
                     "执行成功的运行不进根因表（只有「执行成功但用例没全过」会留一行：问题分类标绿「任务成功」、"
                     "定性写「执行成功，用例通过率 xx（未达 100%）」、用例列给通过率、错误信息列写「任务执行成功，无失败日志」，"
                     "不取证、不送 AI，不计入「需根因执行记录」，在「失败类型分布」里按「用例未 100%」单独计数）；"
                     "没有根因正文（analysis.status=na）显示 --，还没分析的显示「待 AI 根因分析」；"
                     "区块头「可用 N/M 天」之后是「执行记录」入口（不再摊列「N 条流水线：a、b、c…」）："
                     "弹窗按流水线分组、组内按日期倒序逐天给灯态与「成功 x / 共 y 次」，"
                     "组头给该流水线达成率（成功天数 / 该流水线有执行记录的天数），点「逐条」再进该流水线的逐条运行；"
                     "弹窗共 2 个入口（失败用例 / 日志证据）；"
                     "同组分析对比入口已随拆行下线；"
                     "不读 step_6_records.json（单份可达 80KB+），记录正文随该流水线的 pipeline.json 一次取回"},
            {"name": PR_NAME, "role": "PR 页（triton-ascend PR CI 数据：执行时长 / 排队时长 / 卡时 / 成败）",
             "reads": ["archive/<年>/<年-月>/<日>/Triton_Ascend/pr_history.json（每日一份，tools/collect_pr_daily.py 产出）",
                       "archive/<日>/_day.json（当日版本可用 + 区间内最新累计值）"],
             "fields": ["汇总卡（PR 数 / Job 数 / 失败 Job / Job 成功率 / 平均执行 / 平均排队 / NPU 卡时 / 版本可用度累计）",
                        "每日汇总（含当日版本可用）", "PR 明细（按 PR + 工作流聚合，PR 号链接 GitHub）", "Jobs 弹窗（job 级明细）"],
             "note": "数据源为 pod-history-api（CI job pod 生命周期），与流水线归档相互独立；"
                     "执行时长 = pod 存活秒数，排队时长 = wait_duration，失败 = status rejected"
                     "（released/expired 均视为跑完）；分支触发的运行无 PR 号，只进每日汇总不进 PR 明细"},
        ],
        "dataInline": False,
        "caliber": {
            "where": "只有首页：首页表格下方的 #tnote（二级页不再放统计口径，脚本唯一来源 CALIBER_JS）",
            "format": "按条展示：一条一个 <li>（小标题加粗 + 一句说明），不是一整条长文字",
            "items": ["统计区间（执行结束时间）",
                      "版本可用度（流水线）：可用日 ÷ 该流水线在区间内有执行记录的天数（当天有运行开始或结束才算，没启动、没跑过的不进分母）；可用日 = 当天有成功执行且测试用例通过率 100%",
                      "版本可用度（组件）：各流水线之和 ÷ 流水线条数",
                      "特例（括号内一句）：GitHub 平台流水线不看用例 100%；vllm-ascend 只采集定时任务（schedule）运行",
                      "区间内无执行记录显示 --（不参与平均、不计条数、不算待分析数）",
                      "构建成功率（Σ编译通过 ÷ Σ编译次数）",
                      "区间重算与灯色判档",
                      "待分析流水线数"],
        },
        "dateBasis": {"field": "pipelineRunEndtime",
                      "rule": "页面筛选与归档归属一致，均为执行结束时间"},
        "serving": "python run_pipeline.py --serve   # 或 python -m http.server 8765 --directory out",
        "filesWritten": [{"file": idx.name, "bytes": idx.stat().st_size},
                         {"file": det.name, "bytes": det.stat().st_size},
                         {"file": pr.name, "bytes": pr.stat().st_size}],
    }
    (HTML_DIR / META_NAME).write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    print("== 第 9 步 展示页已生成（只含静态壳，业务数据运行时读 out/archive/）")
    print("== 首页   %s  %.1f KB" % (rel_html(idx), idx.stat().st_size / 1024.0))
    print("== 二级页 %s  %.1f KB" % (rel_html(det), det.stat().st_size / 1024.0))
    print("== PR 页  %s  %.1f KB" % (rel_html(pr), pr.stat().st_size / 1024.0))
    print("== 数据源 out/archive/：%d 个归档日%s"
          % (len(days), ("（%s ~ %s）" % (days[0][0], days[-1][0])) if days else "（暂无，先跑采集）"))
    print("== 说明   %s：记录生成方式/数据源/口径" % META_NAME)
    print("!! 直接双击 file:// 打开会被浏览器拦截 fetch，请用 python run_pipeline.py --serve 起本地服务访问")
    return 0


def rel_html(p):
    """打印用：相对 out/ 的路径（out/html/index.html）。"""
    return rel(p)


if __name__ == "__main__":
    sys.exit(main())
