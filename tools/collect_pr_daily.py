# -*- coding: utf-8 -*-
"""每日采集 triton-ascend 的 PR CI 数据（pod-history-api）并归档。

数据源：https://pod-history-api.test.osinfra.cn/api/v1/envs/history
  - 内网自签证书（跳过校验），时间一律 UTC（北京时间 = UTC + 8）
  - 只保留最近 30 天，所以每天增量采「昨日」一天，不做全量回补
  - 主路由无分页、大窗口很慢：按 1 小时一段拉取（昨日 24 段）

过滤：-workflow 结尾的 CI job pod 中 extend_env_comments.repository 含
「triton」的（当前即 triton-lang/triton-ascend，GitHub Actions）。

字段口径：
  - 执行时长 = ttl_seconds（pod 存活秒数，等价 duration）
  - 排队时长 = wait_duration（调度等待，解析成秒）
  - PR 号 = workflow_ref 里 refs/pull/<N>/merge 的 N；分支触发的（refs/heads/*）prNumber=null
  - job 失败 = status rejected；released/expired 都算「跑完」（expired=跑完后被平台清理，多数是正常结束）
  - 卡时 = npu 卡数 × ttl_seconds / 3600

归档：out/archive/<年>/<年-月>/<日>/Triton_Ascend/pr_history.json
  - 与流水线归档同层级（组件目录下），不进 _day.json 的 pipelines（那是流水线索引，PR 数据独立成文件）
  - 页面（html/pr.html）运行时按日期直接 fetch 该文件

用法：
  python tools/collect_pr_daily.py                 # 采昨日（北京时间）
  python tools/collect_pr_daily.py --date 2026-11-18
  python tools/collect_pr_daily.py --force         # 已有归档也重采覆盖
"""
import argparse
import datetime
import json
import re
import ssl
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.paths import ARCHIVE_ROOT, doc  # noqa: E402

API = "https://pod-history-api.test.osinfra.cn/api/v1/envs/history"
REPO_KEYWORD = "triton"
COMPONENT = "Triton_Ascend"
FILE_NAME = "pr_history.json"
PR_RE = re.compile(r"refs/pull/(\d+)/")
DUR_RE = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?")


def parse_dur(s):
    """'3s' / '1m20s' / '2h05m' -> 秒（int）。"""
    if s is None:
        return None
    m = DUR_RE.match(str(s).strip())
    if not m:
        return None
    h, mi, se = m.group(1), m.group(2), m.group(3)
    if not (h or mi or se):
        return None
    return int(h or 0) * 3600 + int(mi or 0) * 60 + int(float(se or 0))


def utc_to_bj(iso):
    """'2026-11-19T09:59:31+00:00' -> '2026-11-19 17:59:31'（北京时间）。"""
    if not iso:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return (dt + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return str(iso)


def fetch_hour(start_utc, end_utc, ctx, timeout=120, retries=2):
    """拉一个小时窗口的全部 pods（全量，服务端不支持按仓库过滤）。"""
    url = f"{API}?start_time={start_utc}&end_time={end_utc}"
    last_err = None
    for _ in range(retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout, context=ctx) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(3)
    raise RuntimeError(f"fetch {start_utc}~{end_utc} 失败: {last_err}")


def to_job(e):
    """一个 CI pod -> 一条 PR job 记录。"""
    c = e.get("extend_env_comments") or {}
    ref = c.get("workflow_ref") or ""
    pm = PR_RE.search(ref)
    wf = ref.split("/.github/workflows/")[-1].split("@")[0] if "/.github/workflows/" in ref else ref
    dev = ((e.get("resource_summary") or {}).get("devices") or [{}])[0]
    created = utc_to_bj(e.get("created_at"))
    ended = utc_to_bj(e.get("expires_at"))
    return {
        "prNumber": int(pm.group(1)) if pm else None,
        "org": c.get("organization"),
        "repo": c.get("repository"),
        "workflow": wf or None,
        "job": c.get("job_display_name"),
        "pod": e.get("name"),
        "status": e.get("status"),
        "execSeconds": e.get("ttl_seconds"),
        "waitSeconds": parse_dur(e.get("wait_duration")),
        "npu": dev.get("npu"),
        "deviceModel": dev.get("device_model"),
        "cluster": e.get("cluster"),
        "source": e.get("source"),
        "createdAt": created,
        "endedAt": ended,
    }


def collect(date_str, force=False):
    target = ARCHIVE_ROOT / date_str[:4] / date_str[:7] / date_str / COMPONENT / FILE_NAME
    if target.is_file() and not force:
        print(f"== 已有归档 {target}（--force 重采）")
        return 0

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    # 昨日北京时间 [00:00, 24:00) = UTC [前一日16:00, 当日16:00)，逐小时拉
    day = datetime.date.fromisoformat(date_str)
    base = datetime.datetime.combine(day, datetime.time(0)) - datetime.timedelta(hours=8)
    jobs = []
    for i in range(24):
        s = (base + datetime.timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        e = (base + datetime.timedelta(hours=i + 1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        d = fetch_hour(s, e, ctx)
        envs = d.get("envs") or []
        got = [to_job(x) for x in envs
               if x.get("name", "").endswith("-workflow")
               and REPO_KEYWORD in ((x.get("extend_env_comments") or {}).get("repository") or "").lower()]
        jobs.extend(got)
        print(f"  {s[11:16]} UTC  pods={len(envs)}  triton={len(got)}", flush=True)

    jobs.sort(key=lambda j: (j.get("createdAt") or ""))
    prs = {}
    for j in jobs:
        k = (j["prNumber"], j["workflow"])
        prs.setdefault(k, []).append(j)
    pr_list = []
    for (pn, wf), items in sorted(prs.items(), key=lambda kv: (kv[0][0] or 0, kv[0][1] or "")):
        execs = [x["execSeconds"] for x in items if x["execSeconds"]]
        waits = [x["waitSeconds"] for x in items if x["waitSeconds"] is not None]
        npus = [x["npu"] or 0 for x in items]
        pr_list.append({
            "prNumber": pn,
            "workflow": wf,
            "jobCount": len(items),
            "failCount": sum(1 for x in items if x["status"] == "rejected"),
            "execSecondsTotal": sum(execs),
            "waitSecondsAvg": round(sum(waits) / len(waits), 1) if waits else None,
            "waitSecondsMax": max(waits) if waits else None,
            "cardHours": round(sum(n * t for n, t in zip(npus, execs)) / 3600.0, 2),
            "firstStart": items[0]["createdAt"],
            "lastEnd": max((x["endedAt"] or "") for x in items) or None,
        })
    fails = sum(1 for j in jobs if j["status"] == "rejected")
    waits_all = [j["waitSeconds"] for j in jobs if j["waitSeconds"] is not None]
    execs_all = [j["execSeconds"] for j in jobs if j["execSeconds"]]
    out = {
        "_doc": doc("pr_daily",
                    title="triton-ascend PR CI 数据（pod-history-api）",
                    source="https://pod-history-api.test.osinfra.cn/api/v1/envs/history",
                    criteria=f"北京时间 {date_str} [00:00,24:00) 内创建的 triton 仓库 CI job pod；"
                             "执行时长=pod 存活秒数，排队时长=调度等待 wait_duration；"
                             "job 失败=status rejected（released/expired 均视为跑完）；"
                             "PR 号取自 workflow_ref refs/pull/<N>/merge，分支触发无 PR 号",
                    dateField="pod created_at（UTC，归属按北京时间）"),
        "date": date_str,
        "summary": {
            "repo": jobs[0]["repo"] if jobs else None,
            "jobCount": len(jobs),
            "prCount": len({j["prNumber"] for j in jobs if j["prNumber"]}),
            "failCount": fails,
            "execSecondsAvg": round(sum(execs_all) / len(execs_all), 1) if execs_all else None,
            "waitSecondsAvg": round(sum(waits_all) / len(waits_all), 1) if waits_all else None,
            "waitSecondsMax": max(waits_all) if waits_all else None,
            "cardHours": round(sum((j["npu"] or 0) * j["execSeconds"] for j in jobs
                                   if j["execSeconds"]) / 3600.0, 2),
        },
        "prs": pr_list,
        "jobs": jobs,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"== 归档 {target}  jobs={len(jobs)}  prs={len(pr_list)}  fail={fails}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="采集昨日 triton-ascend PR CI 数据")
    ap.add_argument("--date", default=(datetime.date.today() - datetime.timedelta(days=1)).isoformat(),
                    help="北京时间日期 yyyy-mm-dd，默认昨日")
    ap.add_argument("--force", action="store_true", help="已有归档也重采覆盖")
    a = ap.parse_args()
    return collect(a.date, a.force)


if __name__ == "__main__":
    sys.exit(main())
