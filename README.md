# telegram2discord — 텔레그램 → 디스코드 메시지 전달

텔레그램에 새 메시지가 오면 디스코드 **채널(Webhook)** 그리고/또는 **특정 사람 DM(봇)** 으로 바로 전달한다.
오라클 `tg2x` 인스턴스(Ubuntu)에서 systemd 서비스로 상시 실행하는 것을 기준으로 한다.

- 텍스트 + 사진/파일 첨부 전달 (기본 10MB 초과 파일은 생략하고 안내문만)
- 여러 장 묶음(앨범)은 디스코드 메시지 1건에 사진 여러 장으로 묶어서 전달
- 디스코드 2000자 제한 자동 분할, rate limit(429) 자동 대기 후 재시도
- 본문의 `@everyone` 같은 멘션은 알림이 울리지 않게 차단
- 메시지 순서 보장(순차 전송)

## 대표 사용법: 텔레그램 방 하나 → 디스코드

내가 참여 중인 텔레그램 방 하나에 새 글이 올라오면 텍스트·사진 그대로 디스코드로 복사.

```
TG_BOT_TOKEN=                 # 비움 = 내 계정으로 로그인 (관리자가 아니어도 됨)
TG_SOURCE_CHATS=-100xxxxxxxx  # `tg2discord.py chats` 로 확인한 그 방 ID 하나
DISCORD_WEBHOOK_URL=...       # 받을 디스코드 채널
```

## 모드 선택

| 텔레그램 쪽 | 설정 | 전달 범위 |
|---|---|---|
| **사용자 모드** | `TG_BOT_TOKEN` 비움 | 내 계정이 볼 수 있는 모든 채팅·채널 (남의 채널 구독 포함) |
| **봇 모드** | `TG_BOT_TOKEN` 입력 | 봇에게 온 메시지 / 봇이 들어가 있는 그룹·채널만 |

| 디스코드 쪽 | 설정 |
|---|---|
| **채널** | `DISCORD_WEBHOOK_URL` (서버 설정 → 연동 → 웹후크) |
| **특정 사람 DM** | `DISCORD_BOT_TOKEN` + `DISCORD_USER_IDS` (받는 사람이 봇과 같은 서버에 있어야 함) |

둘 다 채우면 양쪽 모두 전송.

## 준비물

1. **텔레그램 API ID/Hash** — https://my.telegram.org → API development tools (모드 상관없이 필요)
2. (봇 모드일 때) @BotFather 에서 봇 토큰
3. 디스코드 Webhook URL, 또는 디스코드 봇 토큰 + 받는 사람 사용자 ID
   - 사용자 ID: 디스코드 설정 → 고급 → 개발자 모드 켜기 → 상대 프로필 우클릭 → "사용자 ID 복사"
   - 봇 만들기: https://discord.com/developers/applications → New Application → Bot → Reset Token,
     OAuth2 → URL Generator 에서 `bot` 체크 후 생성된 URL로 내 서버에 초대

## 서버(tg2x) 배포

```bash
# 내 PC/폰에서
ssh -i ssh-key-2026-11-20.key ubuntu@<서버_공인IP>

# 서버에서
git clone https://github.com/sinyechan990202/telegram2discord.git
cd telegram2discord
./install.sh                                   # venv + 패키지 + systemd 등록
nano ~/.config/tg2discord/.env                 # 값 채우기 (git 폴더 밖 → pull 해도 유지)

.venv/bin/python tg2discord.py login           # 텔레그램 로그인 (사용자 모드: 전화번호 → 인증코드)
.venv/bin/python tg2discord.py chats           # (선택) 채팅 ID 확인 → .env 의 TG_SOURCE_CHATS 에 입력
.venv/bin/python tg2discord.py test            # 디스코드에 테스트 메시지 오는지 확인
.venv/bin/python tg2discord.py last            # 지정한 방의 최신 메시지 1건을 지금 디스코드로 복사 (last 5 → 5건)

sudo systemctl start tg2discord                # 상시 실행
journalctl -u tg2discord -f                    # 로그
```

저장소가 private 이면 `git clone` 대신 PC에서 `scp -i ssh-key-2026-11-20.key -r telegram2discord ubuntu@<서버_공인IP>:~/` 로 복사.

설정(`.env`)과 로그인 세션은 **`~/.config/tg2discord/`** 에 저장된다. 저장소 폴더 밖이라 `git pull`·재clone·폴더 삭제에도 유지된다.
예전 버전처럼 저장소 폴더에 `.env` 가 있으면 처음 실행할 때 자동으로 옮겨진다.

`.env` 를 바꾼 뒤에는 `sudo systemctl restart tg2discord`.

## 주의

- `~/.config/tg2discord/tg2discord.session` 파일은 **텔레그램 계정 로그인 그 자체**다. 유출되면 계정 탈취와 같으니 공유·커밋 금지 (`.gitignore` 처리됨).
- 사용자 모드에서 `TG_SOURCE_CHATS` 를 비우면 개인 대화까지 전부 디스코드로 넘어간다. 필요한 채팅만 지정할 것.
