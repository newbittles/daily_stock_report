@echo off
REM stock_report 리포트 1회 실행 (Windows 작업 스케줄러용)
REM 사용: run_report.bat pre | post | holdings | dashboard
chcp 65001 >nul
cd /d c:\Users\af006\stock_report
".venv\Scripts\python.exe" -X utf8 -m src.market_report.scheduler --once %1 >> logs\scheduler.log 2>&1
