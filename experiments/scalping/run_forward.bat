@echo off
REM B' 갭눌림 EOD 포워드테스트 — 매 영업일 장마감 후 자동 실행용 래퍼
REM Windows 작업 스케줄러에서 이 .bat 를 호출한다.
cd /d C:\Users\af006\stock_report
.venv\Scripts\python.exe experiments\scalping\forward_eod.py --max 20 >> experiments\scalping\out\forward.log 2>&1
