# 단축어(Shortcuts)가 호출하는 a-Shell 배치 스크립트
#   단축어 a-Shell "Execute Command":  sh ~/Documents/stock_agent/run_stock.sh morning
#                                      sh ~/Documents/stock_agent/run_stock.sh evening
# 인자를 생략하면 실행 시각으로 모드를 판단한다 (12시 전 morning, 이후 evening).
MODE=${1:-auto}

# 1. 작업 디렉토리 이동 및 환경 로드
cd ~/Documents/stock_agent/

# [보완] 2. 하트비트 확인 메일 발송 (실행 시작 알림)
python3 heartbeat.py --stage start --mode $MODE >> pipeline.log 2>&1

# 3. 메인 파이썬 엔진 실행 (로그 기록)
python3 main_pipeline.py --mode $MODE >> pipeline.log 2>&1

# 4. 작업 완료 후 a-Shell 세션 안전 종료
exit
