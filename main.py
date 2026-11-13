import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import httpx

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain, Record
from astrbot.api.star import Context, Star, register

PLUGIN_NAME = "astrbot_plugin_qq_voice_text"
PLUGIN_DIR = Path(__file__).resolve().parent
WORKER_DIR = PLUGIN_DIR / "worker"
WORKER_SCRIPT = WORKER_DIR / "server.mjs"
DATA_DIR = PLUGIN_DIR.parents[1]
CONFIG_PATH = DATA_DIR / "config" / f"{PLUGIN_NAME}_config.json"
DEFAULTS = {
    "enabled": True,
    "worker_port": 18321,
    "node_path": "",
    "model_dir": "",
    "max_seconds": 120,
    "prefix": "",
    "fallback_notice": True,
    "show_error_detail": False,
}


@register(PLUGIN_NAME, "local", "QQ语音转文字", "2.0.1", "本地 Whisper 识别 QQ 语音")
class QQVoiceText(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._worker = None
        self._worker_start_lock = asyncio.Lock()

    def _cfg(self):
        cfg = dict(DEFAULTS)
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                cfg.update(data)
        except Exception:
            pass
        return cfg

    @staticmethod
    def _failure_text(cfg, error: str) -> str:
        if cfg.get("show_error_detail") and error:
            return f"[语音识别失败：{error[:160]}]"
        return "[语音识别失败]"

    def _node_path(self, cfg) -> str:
        configured = str(cfg.get("node_path") or "").strip()
        if configured and Path(configured).is_file():
            return configured
        found = shutil.which("node")
        if found:
            return found
        for candidate in (
            str(WORKER_DIR / "node.exe"),
            r"D:\SnowLuma\node.exe",
            r"C:\Program Files\nodejs\node.exe",
        ):
            if Path(candidate).is_file():
                return candidate
        return ""

    def _model_dir(self, cfg) -> str:
        configured = str(cfg.get("model_dir") or "").strip()
        if configured:
            return configured
        return str(WORKER_DIR / "models")

    async def _worker_base(self, cfg):
        port = int(cfg.get("worker_port") or 18321)
        return f"http://127.0.0.1:{port}"

    async def _health(self, base: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.get(f"{base}/health")
                return response.status_code == 200
        except Exception:
            return False

    async def _ensure_worker(self, cfg):
        base = await self._worker_base(cfg)
        if await self._health(base):
            return base

        async with self._worker_start_lock:
            if await self._health(base):
                return base
            node = self._node_path(cfg)
            if not node:
                raise RuntimeError(
                    "找不到 node.exe，请在插件配置里填写 node_path，"
                    "或安装 Node.js / SnowLuma"
                )
            if not WORKER_SCRIPT.is_file():
                raise RuntimeError(f"找不到本地识别 worker：{WORKER_SCRIPT}")

            env = os.environ.copy()
            env["ASR_PORT"] = str(int(cfg.get("worker_port") or 18321))
            env["ASR_MODEL_DIR"] = self._model_dir(cfg)
            env["ASR_MAX_SECONDS"] = str(int(cfg.get("max_seconds") or 120))
            creationflags = 0x08000000 if os.name == "nt" else 0
            self._worker = await asyncio.create_subprocess_exec(
                node,
                str(WORKER_SCRIPT),
                cwd=str(WORKER_DIR),
                env=env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=creationflags,
            )
            for _ in range(40):
                await asyncio.sleep(0.5)
                if await self._health(base):
                    logger.info(f"[qq_voice_text] local ASR worker ready on {base}")
                    return base
            raise RuntimeError("本地识别服务启动超时")

    @staticmethod
    def _record_url(result: Any) -> str:
        if not isinstance(result, dict):
            return ""
        data = result.get("data") if isinstance(result.get("data"), dict) else result
        if not isinstance(data, dict):
            return ""
        return str(data.get("url") or data.get("file") or "")

    async def _get_audio_url(self, event, record: Record) -> str:
        url = str(getattr(record, "url", "") or "").strip()
        if url:
            return url
        file_ref = str(getattr(record, "file", "") or "").strip()
        if not file_ref:
            return ""
        try:
            result = await event.bot.call_action("get_record", file=file_ref)
            return self._record_url(result)
        except Exception:
            logger.warning("[qq_voice_text] get_record failed", exc_info=True)
            return ""

    @staticmethod
    def _extract_transcript(result: Any) -> str:
        if not isinstance(result, dict):
            return ""
        return str(result.get("text") or "").strip()

    async def _transcribe(self, event, record: Record, cfg) -> str:
        base = await self._ensure_worker(cfg)
        url = await self._get_audio_url(event, record)
        if not url:
            raise RuntimeError("这条语音没有可下载的地址")

        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            audio = response.content
        if not audio:
            raise RuntimeError("下载到的语音是空的")

        suffix = Path(str(getattr(record, "file", "") or "")).suffix or ".amr"
        fd, path = tempfile.mkstemp(prefix="qq_voice_", suffix=suffix)
        os.close(fd)
        try:
            Path(path).write_bytes(audio)
            async with httpx.AsyncClient(timeout=180) as client:
                response = await client.post(
                    f"{base}/transcribe",
                    json={"path": path},
                )
            try:
                data = response.json()
            except Exception:
                data = {}
            if response.status_code != 200 or not data.get("ok"):
                error = str(data.get("error") or f"HTTP {response.status_code}")
                raise RuntimeError(error)
            text = self._extract_transcript(data)
            if not text:
                raise RuntimeError("本地模型没有识别出文字")
            duration = data.get("seconds")
            logger.info(
                f"[qq_voice_text] local ASR ok {duration}s: {len(text)} chars"
            )
            return text
        finally:
            try:
                Path(path).unlink(missing_ok=True)
            except Exception:
                pass

    @filter.event_message_type(filter.EventMessageType.ALL, priority=99999)
    async def voice_to_text(self, event: AstrMessageEvent):
        cfg = self._cfg()
        if not cfg.get("enabled", True):
            return
        messages = event.get_messages()
        records = [
            (index, comp)
            for index, comp in enumerate(messages)
            if isinstance(comp, Record)
        ]
        if not records:
            return
        if str(event.get_sender_id()) == str(event.get_self_id()):
            return

        changed = False
        for index, record in records:
            try:
                text = await self._transcribe(event, record, cfg)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "[qq_voice_text] local ASR failed "
                    f"umo={event.unified_msg_origin}: {error}",
                    exc_info=True,
                )
                if cfg.get("fallback_notice"):
                    messages[index] = Plain(self._failure_text(cfg, error))
                    changed = True
                continue
            prefix = str(cfg.get("prefix", "") or "")
            messages[index] = Plain(f"{prefix}{text}")
            changed = True

        if changed:
            event.message_obj.message = messages
            text_parts = [
                str(comp.text)
                for comp in messages
                if isinstance(comp, Plain) and getattr(comp, "text", "")
            ]
            if text_parts:
                event.message_str = " ".join(text_parts)

    async def terminate(self):
        if self._worker is not None:
            try:
                self._worker.terminate()
                await asyncio.wait_for(self._worker.wait(), timeout=5)
            except Exception:
                pass
            self._worker = None
