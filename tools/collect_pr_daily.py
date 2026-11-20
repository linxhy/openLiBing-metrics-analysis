# -*- coding: utf-8 -*-
"""每日采集 openLiBing eCDashboard 上 Triton_Ascend（projectId=300130）的 PR 看板原值并归档。

数据源（只取看板原值，不做任何额外聚合 / 计算 / 分析）：
  页面：https://www.openlibing.com/apps/eCDashboard?projectId=300130
  接口：POST /gateway/openlibing-ops/manage/ops-overview/main
        单日区间 startDate == endDate（每天 8 点采昨日），与在看板手动选同一天的口径一致
  字典：POST /gateway/openlibing-metric/manage/digital/metric/dict
        只随档保存本页涉及指标的官方名称 / 口径定义 / 阈值判档（看板元数据，不是二次分析）

采集方式：复用 lib/cdp_exec.run_ol_hook_collect —— 通过 CDP 在业务脚本执行前预装 XHR hook，
reload 后让看板页面自己发起带完整鉴权（vben access token）的请求，hook 把 ops-overview/main
请求体改写为昨日单日（startDate=endDate）并截获响应，全程复用浏览器登录态，无需单独配置 token。
相比页面内裸 fetch：不会因缺鉴权头被 WAF 挂起，也不依赖长 awaitPromise（规避 node 间歇崩溃）。

归档：
  out/archive/<年>/<年-月>/<日>/Triton_Ascend/pr_dashboard.json   看板 record 整份原样落盘
  out/archive/_pr_index.json                                     PR 归档日索引（页面运行时读它定日期）

用法：
  python tools/collect_pr_daily.py                 # 采昨日（北京时间）
  python tools/collect_pr_daily.py --date 2026-11-19
  python tools/collect_pr_daily.py --force         # 已有归档也重采覆盖
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.browser import ensure_browser  # noqa: E402
from lib.cdp_exec import load_config, run_ol_hook_collect  # noqa: E402
from lib.paths import ARCHIVE_ROOT, doc  # noqa: E402

PROJECT_ID = "300130"
PROJECT_NAME = "Triton_Ascend"
COMPONENT = "Triton_Ascend"
FILE_NAME = "pr_dashboard.json"
INDEX_NAME = "_pr_index.json"
DASHBOARD_URL = "https://www.openlibing.com/apps/eCDashboard?projectId=" + PROJECT_ID
MAIN_API = "/gateway/openlibing-ops/manage/ops-overview/main"
DICT_API = "/gateway/openlibing-metric/manage/digital/metric/dict"

# 本页展示涉及的官方指标编号（字典里的主键），随档保存官方名称与口径；
# record 字段与指标编号的对应关系在展示层 step_9_pages.py 的 FIELD_SPEC 中维护
METRIC_CODES = [
    10001,  # PR流水线执行次数
    10002,  # PR执行时长[P90]
    10003,  # PR代码检查任务时长（min）
    10004,  # PR构建任务时长（min）
    10005,  # PR测试任务时长（min）
    10010,  # PR资源排队时长(min)
    10011,  # PR流水线构建任务排队时长(min)
    10012,  # PR流水线测试任务排队时长（min）
    10018,  # 版本可用度
    10025,  # Nightly CI编译成功率
    10033,  # NPU消耗
    10046,  # PR平均消耗NPU卡时
    10056,  # PR构建资源排队时长(min)[P90]
    10057,  # PR测试资源排队时长(min)[P90]
    10067,  # PR合入时长
    10068,  # TTFHW时长
    10082,  # PR平均执行时长
    10093,  # PR流水线启动时长
]


def archive_path(date_str):
    return ARCHIVE_ROOT / date_str[:4] / date_str[:7] / date_str / COMPONENT / FILE_NAME


def update_index(date_str, target, stat_date):
    """把当日归档登记进 archive/_pr_index.json（按日期升序去重 upsert）。"""
    idx_path = ARCHIVE_ROOT / INDEX_NAME
    days = []
    if idx_path.is_file():
        try:
            days = json.loads(idx_path.read_text(encoding="utf-8")).get("days") or []
        except ValueError:
            days = []
    rel_path = target.relative_to(ARCHIVE_ROOT.parent).as_posix()
    entry = {"date": date_str, "path": rel_path, "bytes": target.stat().st_size, "statDate": stat_date}
    days = [d for d in days if d.get("date") != date_str]
    days.append(entry)
    days.sort(key=lambda d: d.get("date") or "")
    payload = {
        "_doc": doc("pr_daily",
                    title="PR 看板归档索引（openLiBing eCDashboard）",
                    source=DASHBOARD_URL,
                    criteria="每个登记日对应一份 Triton_Ascend/pr_dashboard.json；"
                             "由 tools/collect_pr_daily.py 每日采集后 upsert，页面运行时读取本索引定日期"),
        "generatedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "projectId": int(PROJECT_ID),
        "projectName": PROJECT_NAME,
        "dayCount": len(days),
        "days": days,
    }
    idx_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return idx_path


def _parse_capture_body(cap, label):
    """解析 hook 截获的响应体字符串为 JSON；失败时带上下文抛错。"""
    if not cap or not cap.get("body"):
        raise RuntimeError("%s 响应为空（hook 未截获到响应体）" % label)
    try:
        payload = json.loads(cap["body"])
    except ValueError as exc:
        raise RuntimeError("%s 响应 JSON 解析失败: %s" % (label, exc))
    if payload.get("code") != 200:
        raise RuntimeError("%s 网关返回非 200：%s"
                           % (label, json.dumps(payload, ensure_ascii=False)[:300]))
    return payload


def _pick_metric_dict(dict_payload, codes):
    """从 metric/dict 全量字典里只挑本页涉及的指标编号，保留官方名称/口径/阈值（看板元数据）。"""
    dict_all = (dict_payload or {}).get("data") or {}
    picked = {}
    for code in codes:
        hit = dict_all.get(str(code))
        if hit:
            picked[str(code)] = {
                "metricCode": hit.get("metricCode"),
                "metricName": hit.get("metricName"),
                "definition": hit.get("definition"),
                "calculationFormula": hit.get("calculationFormula"),
                "thresholdJudgment": hit.get("thresholdJudgment"),
                "judgmentRule": hit.get("judgmentRule"),
            }
    return picked


def collect(date_str, force=False):
    target = archive_path(date_str)
    if target.is_file() and not force:
        print("== 已有归档 %s（--force 重采）" % target)
        return 0

    cfg = load_config()
    if not ensure_browser(cfg):
        raise RuntimeError("调试浏览器未就绪，无法采集 openLiBing 看板（请先登录后重试）")

    page_size = int((cfg.get("collect") or {}).get("t1_page_size", 100))
    print("== 采集 openLiBing eCDashboard  %s  projectId=%s（%s）" % (date_str, PROJECT_ID, PROJECT_NAME))
    hook = run_ol_hook_collect(date_str, PROJECT_ID, page_size, cfg,
                               "pr_dashboard_latest.json", label="pr_dashboard", retries=3)

    main_cap = hook.get("main") or {}
    if not main_cap.get("rewritten"):
        raise RuntimeError("ops-overview/main 请求体未被改写成单日区间（rewritten=false），放弃归档避免口径错误")
    try:
        post_data = json.loads(main_cap.get("postData") or "{}")
    except ValueError:
        post_data = {}
    if post_data.get("startDate") != date_str or post_data.get("endDate") != date_str:
        raise RuntimeError("ops-overview/main 实际请求区间不是单日 %s：%s"
                           % (date_str, json.dumps(post_data, ensure_ascii=False)))

    main_payload = _parse_capture_body(main_cap, "ops-overview/main")
    records = ((main_payload.get("data") or {}).get("records")) or []
    record = next((r for r in records if str(r.get("projectId")) == str(PROJECT_ID)), None)
    if not record:
        raise RuntimeError("ops-overview/main 返回中找不到 projectId=%s（%s），返回项目：%s"
                           % (PROJECT_ID, PROJECT_NAME,
                              [r.get("projectName") for r in records]))

    metric_dict = {}
    if hook.get("dict"):
        try:
            dict_payload = _parse_capture_body(hook["dict"], "metric/dict")
            metric_dict = _pick_metric_dict(dict_payload, METRIC_CODES)
        except RuntimeError as exc:
            print("!! 指标字典解析失败（不影响主数据归档）: %s" % exc)

    # 只做信封封装与原样落盘：record 内字段一个不删、一个不改、一个不算
    out = {
        "_doc": doc("pr_daily",
                    title="Triton_Ascend PR 工程能力看板（openLiBing eCDashboard 原值）",
                    source=DASHBOARD_URL,
                    criteria="单日区间 startDate=endDate=%s 经 XHR hook 改写后由页面发起 %s，"
                             "record 整份为看板返回原值，不做任何二次聚合 / 计算 / 分析；"
                             "指标口径见 metricDict（官方指标字典）"
                             % (date_str, MAIN_API),
                    dateField="statDate（看板统计日）"),
        "date": date_str,
        "source": {
            "dashboardUrl": DASHBOARD_URL,
            "mainApi": MAIN_API,
            "dictApi": DICT_API,
            "projectId": int(PROJECT_ID),
            "projectName": PROJECT_NAME,
        },
        "projectId": record.get("projectId"),
        "projectName": record.get("projectName"),
        "statDate": record.get("statDate"),
        "passStatus": record.get("passStatus"),
        "metricDict": metric_dict,
        "record": record,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    idx_path = update_index(date_str, target, record.get("statDate"))
    print("== 归档 %s  字段=%d  灯色=%s  statDate=%s"
          % (target, len(record), record.get("passStatus"), record.get("statDate")))
    print("== 索引 %s" % idx_path)
    return 0


def main():
    ap = argparse.ArgumentParser(description="采集昨日 openLiBing eCDashboard Triton_Ascend PR 看板原值")
    ap.add_argument("--date",
                    default=(datetime.date.today() - datetime.timedelta(days=1)).isoformat(),
                    help="北京时间日期 yyyy-mm-dd，默认昨日")
    ap.add_argument("--force", action="store_true", help="已有归档也重采覆盖")
    a = ap.parse_args()
    return collect(a.date, a.force)


if __name__ == "__main__":
    sys.exit(main())
