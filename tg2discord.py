"""텔레그램 → 디스코드 메시지 전달기.

텔레그램에서 새 메시지가 오면 디스코드 Webhook(채널) 그리고/또는 디스코드 봇 DM(특정 사람)으로 전달한다.

사용법:
    python tg2discord.py login   # 최초 1회: 텔레그램 로그인 (사용자 모드는 전화번호 + 인증코드)
    python tg2discord.py chats   # 내 채팅/채널 목록과 ID 출력 (TG_SOURCE_CHATS 고를 때)
    python tg2discord.py test    # 디스코드로 테스트 메시지 1건 전송
    python tg2discord.py run     # 상시 실행 (systemd 서비스가 이걸 실행)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import timedelta, timezone
from pathlib import Path

import aiohttp
from dotenv import load_dotenv
from telethon import TelegramClient, events, utils

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

log = logging.getLogger("tg2discord")

DISCORD_API = "https://discord.com/api/v10"
DISCORD_TEXT_LIMIT = 2000


# ───────────────────────── 설정 ─────────────────────────


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _csv(name: str) -> list[str]:
    return [x.strip() for x in _env(name).split(",") if x.strip()]


def _chat_ref(x: str) -> int | str:
    """'-1001234', '1234' → int, '@name' / 'name' → str."""
    return int(x) if x.lstrip("-").isdigit() else x


class Config:
    def __init__(self) -> None:
        self.api_id = int(_env("TG_API_ID") or 0)
        self.api_hash = _env("TG_API_HASH")
        self.bot_token = _env("TG_BOT_TOKEN")  # 비우면 사용자 계정 모드
        self.session = str(BASE_DIR / (_env("TG_SESSION") or "tg2discord"))
        self.source_chats = [_chat_ref(x) for x in _csv("TG_SOURCE_CHATS")]
        self.forward_outgoing = _env("FORWARD_OUTGOING").lower() in ("1", "true", "yes")

        self.webhook_url = _env("DISCORD_WEBHOOK_URL")
        self.discord_bot_token = _env("DISCORD_BOT_TOKEN")
        self.discord_user_ids = _csv("DISCORD_USER_IDS")

        self.max_file_bytes = int(float(_env("MAX_FILE_MB") or 10) * 1024 * 1024)
        self.tz = timezone(timedelta(hours=float(_env("TZ_OFFSET_HOURS") or 9)))

    @property
    def mode(self) -> str:
        return "bot" if self.bot_token else "user"

    def validate(self, need_telegram: bool = True, need_discord: bool = True) -> None:
        errors = []
        if need_telegram and (not self.api_id or not self.api_hash):
            errors.append("TG_API_ID / TG_API_HASH 가 비어 있음 (https://my.telegram.org 에서 발급)")
        if need_discord:
            if not self.webhook_url and not self.discord_user_ids:
                errors.append("DISCORD_WEBHOOK_URL 또는 DISCORD_USER_IDS 중 하나는 있어야 함")
            if self.discord_user_ids and not self.discord_bot_token:
                errors.append("DISCORD_USER_IDS 로 DM 을 보내려면 DISCORD_BOT_TOKEN 필요")
        if errors:
            sys.exit("설정 오류:\n  - " + "\n  - ".join(errors))


# ───────────────────────── 디스코드 ─────────────────────────


def split_text(text: str, limit: int = DISCORD_TEXT_LIMIT) -> list[str]:
    """디스코드 2000자 제한에 맞게 자르기. 가능하면 줄바꿈 기준으로 자른다."""
    if not text:
        return [""]
    chunks: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:  # 적당한 줄바꿈이 없으면 그냥 자름
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    chunks.append(text)
    return chunks


class Discord:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.http: aiohttp.ClientSession | None = None
        self.dm_channels: dict[str, str] = {}  # user_id → DM channel_id

    async def __aenter__(self) -> "Discord":
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120))
        return self

    async def __aexit__(self, *exc) -> None:
        if self.http:
            await self.http.close()

    def _bot_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bot {self.cfg.discord_bot_token}"}

    async def _request(self, method: str, url: str, *, build_data=None, **kw) -> dict:
        """429(rate limit)·5xx 는 재시도. build_data 는 multipart 를 매 시도마다 새로 만드는 함수."""
        assert self.http is not None
        for attempt in range(6):
            if build_data is not None:
                kw["data"] = build_data()
            try:
                async with self.http.request(method, url, **kw) as resp:
                    if resp.status == 429:
                        body = await resp.json(content_type=None)
                        wait = float(body.get("retry_after", 1)) + 0.25
                        log.warning("디스코드 rate limit, %.1fs 대기", wait)
                        await asyncio.sleep(wait)
                        continue
                    if resp.status >= 500:
                        raise aiohttp.ClientResponseError(
                            resp.request_info, resp.history, status=resp.status
                        )
                    if resp.status >= 400:
                        text = await resp.text()
                        raise RuntimeError(f"디스코드 {resp.status}: {text[:300]}")
                    if resp.status == 204:
                        return {}
                    return await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                wait = min(2**attempt, 30)
                log.warning("디스코드 요청 실패(%s), %ds 후 재시도", e, wait)
                await asyncio.sleep(wait)
        raise RuntimeError("디스코드 요청 재시도 횟수 초과")

    async def _dm_channel(self, user_id: str) -> str:
        if user_id not in self.dm_channels:
            data = await self._request(
                "POST",
                f"{DISCORD_API}/users/@me/channels",
                headers=self._bot_headers(),
                json={"recipient_id": user_id},
            )
            self.dm_channels[user_id] = data["id"]
        return self.dm_channels[user_id]

    async def _post(self, url: str, headers: dict, content: str, file: tuple[str, bytes] | None) -> None:
        payload = {
            "content": content,
            # 텔레그램 본문에 @everyone 등이 있어도 디스코드에서 알림이 터지지 않게
            "allowed_mentions": {"parse": []},
        }
        if file is None:
            await self._request("POST", url, headers=headers, json=payload)
            return
        name, blob = file

        def build() -> aiohttp.FormData:
            form = aiohttp.FormData()
            form.add_field("payload_json", json.dumps(payload), content_type="application/json")
            form.add_field("files[0]", blob, filename=name)
            return form

        await self._request("POST", url, headers=headers, build_data=build)

    async def send(self, text: str, file: tuple[str, bytes] | None = None) -> None:
        chunks = split_text(text)
        targets: list[tuple[str, dict]] = []
        if self.cfg.webhook_url:
            targets.append((self.cfg.webhook_url + "?wait=true", {}))
        for uid in self.cfg.discord_user_ids:
            cid = await self._dm_channel(uid)
            targets.append((f"{DISCORD_API}/channels/{cid}/messages", self._bot_headers()))

        for url, headers in targets:
            for i, chunk in enumerate(chunks):
                is_last = i == len(chunks) - 1
                await self._post(url, headers, chunk, file if is_last else None)


# ───────────────────────── 텔레그램 → 디스코드 ─────────────────────────


async def build_text(event, cfg: Config) -> str:
    msg = event.message
    chat = await event.get_chat()
    sender = await event.get_sender()
    chat_name = utils.get_display_name(chat) or str(event.chat_id)
    sender_name = utils.get_display_name(sender) if sender else ""
    when = msg.date.astimezone(cfg.tz).strftime("%Y-%m-%d %H:%M")

    header = f"**[{chat_name}]**"
    if sender_name and sender_name != chat_name:
        header += f" {sender_name}"
    header += f" · {when}"
    if msg.fwd_from:
        header += " · (전달됨)"

    body = msg.message or ""
    if not body and msg.media and msg.file is None:
        body = f"_({type(msg.media).__name__.replace('MessageMedia', '')} 메시지)_"
    return f"{header}\n{body}".rstrip()


async def extract_file(event, cfg: Config) -> tuple[tuple[str, bytes] | None, str]:
    """(첨부파일, 추가 안내문) 반환."""
    msg = event.message
    f = msg.file
    if f is None:
        return None, ""
    size = f.size or 0
    if size > cfg.max_file_bytes:
        return None, f"\n_(첨부 {f.name or ''} {size / 1048576:.1f}MB — 용량 초과로 생략)_"
    blob = await msg.download_media(file=bytes)
    if not blob:
        return None, ""
    name = f.name or f"telegram_{msg.id}{f.ext or ''}"
    return (name, blob), ""


async def worker(queue: asyncio.Queue, discord: Discord, cfg: Config) -> None:
    """메시지 순서를 지키려고 하나씩 순차 전송."""
    while True:
        event = await queue.get()
        try:
            text = await build_text(event, cfg)
            file, note = await extract_file(event, cfg)
            await discord.send(text + note, file)
            log.info("전달 완료: chat=%s msg=%s", event.chat_id, event.message.id)
        except Exception:
            log.exception("전달 실패: chat=%s msg=%s", event.chat_id, getattr(event.message, "id", "?"))
        finally:
            queue.task_done()


def make_client(cfg: Config) -> TelegramClient:
    return TelegramClient(cfg.session, cfg.api_id, cfg.api_hash)


async def ensure_authorized(client: TelegramClient, cfg: Config) -> None:
    if cfg.mode == "bot":
        await client.start(bot_token=cfg.bot_token)
        return
    await client.connect()
    if not await client.is_user_authorized():
        sys.exit("텔레그램 로그인이 안 되어 있음. 먼저 `python tg2discord.py login` 을 실행하세요.")


async def cmd_login(cfg: Config) -> None:
    cfg.validate(need_discord=False)
    client = make_client(cfg)
    if cfg.mode == "bot":
        await client.start(bot_token=cfg.bot_token)
    else:
        await client.start()  # 전화번호 / 인증코드 / 2단계 비밀번호를 터미널에서 입력
    me = await client.get_me()
    print(f"로그인 완료: {utils.get_display_name(me)} (id={me.id}, 모드={cfg.mode})")
    print(f"세션 파일: {cfg.session}.session  ← 비밀번호와 같으니 절대 공유하지 마세요")
    await client.disconnect()


async def cmd_chats(cfg: Config) -> None:
    cfg.validate(need_discord=False)
    if cfg.mode == "bot":
        sys.exit("봇 모드에서는 채팅 목록을 볼 수 없습니다. 봇에게 메시지를 보낸 뒤 run 로그의 chat 값을 쓰세요.")
    client = make_client(cfg)
    await ensure_authorized(client, cfg)
    print(f"{'ID':>16}  {'종류':<6} 이름")
    async for d in client.iter_dialogs(limit=200):
        kind = "채널" if d.is_channel and not d.is_group else "그룹" if d.is_group else "개인"
        uname = getattr(d.entity, "username", None)
        print(f"{d.id:>16}  {kind:<6} {d.name}" + (f"  (@{uname})" if uname else ""))
    await client.disconnect()


async def cmd_test(cfg: Config) -> None:
    cfg.validate(need_telegram=False)
    async with Discord(cfg) as discord:
        await discord.send("✅ tg2discord 테스트 메시지 — 이게 보이면 디스코드 설정 정상")
    print("디스코드 테스트 전송 완료")


async def cmd_run(cfg: Config) -> None:
    cfg.validate()
    if cfg.mode == "user" and not cfg.source_chats:
        log.warning("TG_SOURCE_CHATS 가 비어 있음 → 내 계정에 오는 '모든' 메시지를 전달합니다")

    client = make_client(cfg)
    await ensure_authorized(client, cfg)
    queue: asyncio.Queue = asyncio.Queue()

    async with Discord(cfg) as discord:
        kwargs: dict = {"chats": cfg.source_chats or None}
        if not cfg.forward_outgoing:
            kwargs["incoming"] = True

        @client.on(events.NewMessage(**kwargs))
        async def _on_message(event):
            await queue.put(event)

        task = asyncio.create_task(worker(queue, discord, cfg))
        me = await client.get_me()
        log.info(
            "실행 중: %s (모드=%s), 소스=%s, 대상=%s",
            utils.get_display_name(me),
            cfg.mode,
            cfg.source_chats or "전체",
            ", ".join(
                (["webhook"] if cfg.webhook_url else [])
                + [f"DM:{u}" for u in cfg.discord_user_ids]
            ),
        )
        try:
            await client.run_until_disconnected()
        finally:
            task.cancel()


COMMANDS = {"login": cmd_login, "chats": cmd_chats, "test": cmd_test, "run": cmd_run}


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("telethon").setLevel(logging.WARNING)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd not in COMMANDS:
        sys.exit(f"사용법: python {Path(__file__).name} [{'|'.join(COMMANDS)}]")
    asyncio.run(COMMANDS[cmd](Config()))


if __name__ == "__main__":
    main()
