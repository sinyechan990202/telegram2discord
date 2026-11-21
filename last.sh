#!/usr/bin/env bash
# 지정한 텔레그램 방의 최신 메시지 N건(기본 10)을 디스코드로 연속 전송
#   ./last.sh      → 최신 10건
#   ./last.sh 3    → 최신 3건
N="${1:-10}"
cd "$(dirname "$0")"

# 상시 실행 중이면 세션 파일 충돌을 피하려고 잠깐 멈췄다가 다시 켬
WAS_RUNNING=0
if systemctl is-active --quiet tg2discord; then
  WAS_RUNNING=1
  sudo systemctl stop tg2discord
fi

.venv/bin/python tg2discord.py last "$N"

if [ "$WAS_RUNNING" = 1 ]; then
  sudo systemctl start tg2discord
  echo "상시 실행 다시 시작됨"
fi
