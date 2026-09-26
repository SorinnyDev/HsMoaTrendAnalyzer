#!/bin/bash
echo "Starting HsMoa Trend Analyzer 24/7 Container..."

# 환경 변수 기본값 설정
SCHEDULE_TIME=${CRAWLER_SCHEDULE_TIME:-"07:00"}
RUN_STARTUP=${RUN_ON_STARTUP:-"false"}

echo "Configured to run daily at: $SCHEDULE_TIME"
echo "Run on startup: $RUN_STARTUP"

# 시작 시 즉시 실행 옵션
if [ "$RUN_STARTUP" = "true" ] || [ "$RUN_STARTUP" = "True" ]; then
    echo "[$(date)] Running initial startup analysis..."
    python main.py --mode forecast --send-report
    echo "[$(date)] Initial analysis finished."
fi

# 스케줄링 무한 루프
while true; do
    # 현재 시간 (HH:MM 형식) 추출
    CURRENT_TIME=$(date +%H:%M)
    
    if [ "$CURRENT_TIME" = "$SCHEDULE_TIME" ]; then
        echo "[$(date)] Scheduled time reached ($SCHEDULE_TIME). Running analysis..."
        python main.py --mode forecast --send-report
        echo "[$(date)] Analysis finished. Waiting for the next day..."
        
        # 같은 분(minute) 내에 중복 실행을 막기 위해 61초 대기
        sleep 61
    else
        # 30초마다 시간 확인
        sleep 30
    fi
done
