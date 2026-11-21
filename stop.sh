#!/usr/bin/env bash
# tg2discord 정지
sudo systemctl stop tg2discord
systemctl is-active tg2discord >/dev/null && echo "아직 실행 중" || echo "정지됨"
