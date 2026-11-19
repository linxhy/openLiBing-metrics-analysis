@echo off
rem Daily task: collect yesterday's triton-ascend PR CI data (pod-history-api)
rem and push the archive to GitHub. Scheduled at 08:00 every day via
rem schtasks task "TritonAscend_PR_Daily" (runs only when the user is logged on).
rem Log: out\state\pr_daily.log
cd /d "%~dp0.."
set "PY=C:\Users\陈昕伟\AppData\Local\Programs\Python\Python311\python.exe"
set "LOG=out\state\pr_daily.log"
echo ==== %date% %time% daily_pr_task start ==== >> "%LOG%"
"%PY%" -X utf8 tools\collect_pr_daily.py >> "%LOG%" 2>&1
if errorlevel 1 (
  echo collect FAILED, skip push >> "%LOG%"
  exit /b 1
)
git add out/archive >> "%LOG%" 2>&1
git diff --cached --quiet >> "%LOG%" 2>&1
if errorlevel 1 (
  git commit -m "daily: triton-ascend PR CI data (pod-history-api)" >> "%LOG%" 2>&1
  git push >> "%LOG%" 2>&1
  echo ==== pushed ==== >> "%LOG%"
) else (
  echo nothing to commit >> "%LOG%"
)
echo ==== %date% %time% daily_pr_task done ==== >> "%LOG%"
