#!/bin/bash
echo "Starting HsMoa Trend Analyzer 24/7 Container..."

while true; do
  echo "[$(date)] Running analysis..."
  python main.py --mode forecast --send-report
  echo "[$(date)] Analysis finished. Sleeping for 24 hours (86400 seconds)..."
  sleep 86400
done
