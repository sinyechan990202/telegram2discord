#!/usr/bin/env bash
# tg2x(Ubuntu) 서버에서 실행: 가상환경 + 패키지 설치 + systemd 서비스 등록
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
USER_NAME="$(id -un)"

sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip

python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install --upgrade pip
"$DIR/.venv/bin/pip" install -r "$DIR/requirements.txt"

# 설정은 git 폴더 밖(~/.config/tg2discord)에 보관 → git pull 해도 안 지워짐
CONF="$HOME/.config/tg2discord"
mkdir -p "$CONF" && chmod 700 "$CONF"
if [ -f "$DIR/.env" ] && [ ! -f "$CONF/.env" ]; then
  mv "$DIR/.env" "$CONF/.env"            # 예전 위치에 있던 설정 이동
fi
if [ ! -f "$CONF/.env" ]; then
  cp "$DIR/.env.example" "$CONF/.env"
  echo ">> $CONF/.env 를 만들었습니다. 값을 채운 뒤 다음 단계를 진행하세요."
fi
chmod 600 "$CONF/.env"

sed -e "s#__DIR__#$DIR#g" -e "s#__USER__#$USER_NAME#g" "$DIR/tg2discord.service" \
  | sudo tee /etc/systemd/system/tg2discord.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable tg2discord >/dev/null

cat <<MSG

설치 완료. 다음 순서로 진행:
  1) nano $CONF/.env                    # 값 채우기 (git 폴더 밖이라 pull 해도 유지)
  2) $DIR/.venv/bin/python $DIR/tg2discord.py login   # 텔레그램 로그인 (최초 1회)
  3) $DIR/.venv/bin/python $DIR/tg2discord.py test    # 디스코드 테스트
  4) sudo systemctl start tg2discord    # 상시 실행 시작
     journalctl -u tg2discord -f         # 로그 보기
MSG
