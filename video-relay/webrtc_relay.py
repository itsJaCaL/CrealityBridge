# -*- coding: utf-8 -*-
import asyncio
import base64
import json
import logging
import os
import threading
import queue
from aiohttp import web
from signaling_channel import WebSocketClient
from webrtc_manager import WebrtcManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CrealityWebRTCRelay")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_CACHE_FILE = os.path.join(BASE_DIR, "token_cache.json")

def extract_sub_from_jwt(token_str):
    try:
        payload_part = token_str.split(".")[1]
        payload_part += "=" * ((4 - len(payload_part) % 4) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload_part).decode("utf-8"))
        return decoded.get("sub", "")
    except Exception:
        return ""

def save_cached_token(token_str):
    try:
        with open(TOKEN_CACHE_FILE, "w") as f:
            json.dump({"token": token_str}, f)
    except Exception:
        pass

def load_cached_token():
    try:
        if os.path.exists(TOKEN_CACHE_FILE):
            with open(TOKEN_CACHE_FILE, "r") as f:
                return json.load(f).get("token", "")
    except Exception:
        pass
    return ""

class DummyRecorder:
    def find_video(self, media):
        return ""

class CrealityVideoRelay:
    def __init__(self, tbid, token):
        self.tbid = str(tbid)
        self.token = str(token)
        self.websocket_queue = queue.Queue()
        self.close_queue = queue.Queue()
        self.webrtc_mgr = None
        self.ws_client = None
        self._thread = None

    def is_alive(self):
        return (
            self._thread is not None
            and self._thread.is_alive()
            and self.ws_client is not None
            and self.ws_client.connected()
        )

    def start(self):
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def update_token(self, new_token):
        self.token = str(new_token)
        if self.ws_client is not None:
            self.ws_client.token_update(self.token)
        if self.webrtc_mgr is not None:
            self.webrtc_mgr.token_update(self.token)

    def stop(self):
        if self.ws_client:
            try:
                self.ws_client.reconnect_count = 999
                self.ws_client.close()
            except Exception:
                pass
        self.close_queue.put("all")

    def _run_loop(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        url = f"wss://api.crealitycloud.com/api/cxy/ws/webrtc/signal/push/{self.tbid}"
        options = {
            "enableDataChannel": False,
            "enableLocalStream": True,
            "enableRemoteStream": False
        }

        logger.info(f"[+] Connecting WebSocket signaling to: {url}")
        try:
            self.webrtc_mgr = WebrtcManager(
                self.tbid, self.tbid, options, self.close_queue,
                self.token, region=1, recorder=DummyRecorder(), verbose=True
            )
            self.ws_client = WebSocketClient(url, self.websocket_queue, self.token)
        except Exception as e:
            logger.error(f"[-] Failed to initialize WebSocketClient: {e}")
            return

        async def run_ws():
            last_push = 0
            while True:
                await asyncio.sleep(0.1)
                if not self.websocket_queue.empty():
                    msg = self.websocket_queue.get()
                    logger.info(f"[SIGNAL] Processing message: {msg[:120]}...")
                    await self.webrtc_mgr.signaling_message_handler(self.ws_client, msg)
                if not self.close_queue.empty():
                    pid = self.close_queue.get()
                    if pid == "all":
                        logger.info("[*] Closing all peers")
                        break
                    await self.webrtc_mgr.remove_peer(pid)
                now = asyncio.get_event_loop().time()
                if now - last_push >= 30:
                    last_push = now
                    self.webrtc_mgr.push_state(self.ws_client)

        try:
            loop.run_until_complete(run_ws())
        except Exception as e:
            logger.error(f"[-] WebRTC loop error: {e}")
        finally:
            if self.ws_client:
                self.ws_client.close()

relay_instance = None

async def handle_control(request):
    global relay_instance
    try:
        data = await request.json()
        token = data.get("token")
        livestream = data.get("livestream", 1)

        if livestream == 0 and relay_instance:
            logger.info("[*] Livestream=0 received, closing active stream")
            relay_instance.stop()
            relay_instance = None
            return web.json_response({"status": "stopped"})

        if token:
            save_cached_token(token)
            sub_id = extract_sub_from_jwt(token)
            if not sub_id:
                return web.json_response({"status": "invalid_jwt"}, status=400)

            if relay_instance is not None and relay_instance.is_alive():
                logger.info(f"[TOKEN] Updated token on active WebSocket for {sub_id}")
                relay_instance.update_token(token)
            else:
                logger.info(f"[TOKEN] Starting WebSocket signaling for SUB ID: {sub_id}")
                if relay_instance is not None:
                    relay_instance.stop()
                relay_instance = CrealityVideoRelay(sub_id, token)
                relay_instance.start()
            return web.json_response({"status": "ok", "sub": sub_id})
    except Exception as e:
        logger.error(f"Control handler error: {e}")
    return web.json_response({"status": "error"}, status=400)

async def on_startup(app):
    global relay_instance
    cached_token = load_cached_token()
    if cached_token:
        sub_id = extract_sub_from_jwt(cached_token)
        if sub_id:
            logger.info(f"[*] Auto-starting WebSocket signaling from cached token for SUB ID: {sub_id}")
            relay_instance = CrealityVideoRelay(sub_id, cached_token)
            relay_instance.start()

app = web.Application()
app.on_startup.append(on_startup)
app.router.add_post("/control", handle_control)

if __name__ == "__main__":
    logger.info("[*] Starting Video Signaling Receiver on port 8555...")
    web.run_app(app, host="0.0.0.0", port=8555)
