# 오라클 클라우드 배포 가이드 — "내가 할 일" A to Z

> PC를 꺼도 평일 14:50/16:30 자동 발송되게 오라클 무료서버(24/7)에 올린다.
> 두 프로세스: ① **리포트 스케줄러**(send-only, 자동발송) ② **명령봇**(main.py, /screen·/watch·/holdings polling).
> ⚠️ **기존에 돌리던 자동 텔레그램 봇과 같은 봇 토큰**이면 ②(polling) 충돌 → 아래 [M] 참고.

---

## A. 오라클 인스턴스 생성
1. cloud.oracle.com 로그인 → Compute → Instances → **Create Instance**
2. **Image**: Canonical Ubuntu 22.04
3. **Shape**: `VM.Standard.A1.Flex` (ARM) → **OCPU 2, RAM 12GB** 정도 (Always Free 한도 4/24 내)
4. **Region/AD**: 한국 리전(춘천 `ap-chuncheon-1` 또는 서울 `ap-seoul-1`) — KIS API 국내 IP 권장
5. SSH 키: 새로 생성 → **private key 다운로드**(`oracle.key`) 보관

## B. 접속
```bash
chmod 600 oracle.key
ssh -i oracle.key ubuntu@<공인IP>
```

## C. 서버 기본 셋업 (서버에서)
```bash
sudo apt update && sudo apt -y upgrade
sudo apt -y install python3.11 python3.11-venv git
sudo timedatectl set-timezone Asia/Seoul   # KST
timedatectl   # 확인
```

## D. 코드 가져오기
```bash
cd ~
git clone https://github.com/newbittles/daily_stock_report.git stock_report
cd stock_report
python3.11 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e .   # pyproject.toml 의존성 설치
```

## E. 시크릿(.env) 올리기 — ⚠️ 가장 중요
`.env`는 git에 없으니 **내 PC에서 서버로 복사**:
```bash
# 내 PC(로컬)에서:
scp -i oracle.key "C:\Users\af006\stock_report\.env" ubuntu@<공인IP>:~/stock_report/.env
scp -i oracle.key "C:\Users\af006\stock_report\config\holdings.yaml" ubuntu@<공인IP>:~/stock_report/config/holdings.yaml
```
- `.env`에 KIS 키·텔레그램 토큰·Gemini 키 들어있는지 확인.

## F. GitHub push 인증 (리포트 게시용)
리포트가 `docs/`를 git push → GitHub Pages 갱신. 서버에서 push하려면:
1. GitHub → Settings → Developer settings → **Personal Access Token**(repo 권한) 발급
2. 서버에서:
```bash
git config --global user.name "newbittles"
git config --global user.email "<메일>"
git remote set-url origin https://<PAT>@github.com/newbittles/daily_stock_report.git
```

## G. 동작 테스트 (수동 1회)
```bash
cd ~/stock_report
.venv/bin/python -m src.market_report.scheduler --once post   # 마감후 리포트 1회 발송
```
→ 텔레그램에 오면 성공. (KIS·Gemini·텔레그램·push 다 검증됨)

## H. 리포트 스케줄러 상시 등록 (systemd) — 핵심
```bash
sudo tee /etc/systemd/system/stock-report.service > /dev/null <<'EOF'
[Unit]
Description=Stock Report Scheduler (pre/post/holdings/dashboard)
After=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/stock_report
ExecStart=/home/ubuntu/stock_report/.venv/bin/python -m src.market_report.scheduler
Restart=always
RestartSec=30
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now stock-report
systemctl status stock-report           # 동작 확인
journalctl -u stock-report -f           # 로그 실시간
```
→ 이제 평일 14:50(마감전)·16:30(마감후)·16:35(보유종목)·16:40(대시보드) 자동.

## I. (선택) 명령봇 24/7 — /screen·/watch·/holdings
리포트 자동발송만 원하면 생략 가능. 텔레그램으로 명령도 쓰려면:
```bash
sudo tee /etc/systemd/system/stock-bot.service > /dev/null <<'EOF'
[Unit]
Description=Stock Insight Telegram Bot (commands + watchlist monitor)
After=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/stock_report
ExecStart=/home/ubuntu/stock_report/.venv/bin/python main.py
Restart=always
RestartSec=30
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now stock-bot
```

## J. PC 스케줄러 끄기 (중복 발송 방지)
서버에서 돌기 시작하면 **내 PC에서 돌리던 스케줄러는 종료**. 안 그러면 같은 리포트가 2번 발송됨.

---

## M. ⚠️ 기존 자동 텔레그램 봇과 공존
- **리포트 스케줄러(H)**: 텔레그램 `send`만 함(polling 안 함) → 기존 봇과 **충돌 없음**. 같은 서버에 그냥 추가 OK.
- **명령봇(I, main.py)**: 텔레그램 **polling**을 함. 기존 봇이 **같은 봇 토큰**을 polling 중이면 → `409 Conflict`(둘 다 죽음). 이 경우:
  - 이 프로젝트 봇 토큰은 최근 재발급한 **새 토큰**이므로, 기존 봇이 *다른* 토큰이면 문제없음.
  - 같은 토큰이면 → [I] 생략(명령봇 안 띄움)하거나, 둘 중 하나만 polling.
- 기존 봇과 **같은 오라클 서버**라면: Python/venv를 프로젝트별로 분리(이미 `~/stock_report/.venv`로 분리됨). 서비스명도 `stock-report`로 달라 충돌 없음.

## 점검 명령
```bash
systemctl status stock-report stock-bot   # 서비스 상태
journalctl -u stock-report --since today  # 오늘 로그
free -h                                     # 메모리 (ARM 12GB면 여유)
df -h                                       # 디스크
```
