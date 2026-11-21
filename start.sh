#!/usr/bin/env bash
# tg2discord 실행 (백그라운드 상시 실행, 서버 재부팅 시 자동 시작)
sudo systemctl start tg2discord
sleep 2
systemctl status tg2discord --no-pager -n 10
