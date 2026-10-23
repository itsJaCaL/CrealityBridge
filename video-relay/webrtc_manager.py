# -*- coding: utf-8 -*-
import json
import logging
import asyncio
import os
import time
import numpy as np
import cv2
import aiohttp
from av import VideoFrame
from aiortc import RTCIceCandidate, RTCSessionDescription, RTCConfiguration, RTCIceServer, RTCPeerConnection, VideoStreamTrack

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "video_config.json")

def load_video_config():
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {"camera_url": "http://127.0.0.1/webcam/?action=stream", "rotation": 0}

class RawMjpegTrack(VideoStreamTrack):
    def __init__(self, camera_url, rotation_deg=0):
        super().__init__()
        self.camera_url = camera_url
        self.k_rot = (int(rotation_deg) // 90) % 4
        self._session = None
        self._closed = False
        self._latest_img = None
        self._new_frame_event = asyncio.Event()
        
        self._scrape_task = asyncio.create_task(self._scrape_loop())

    async def _scrape_loop(self):
        while not self._closed:
            try:
                if self._session is None:
                    self._session = aiohttp.ClientSession()
                
                timeout = aiohttp.ClientTimeout(total=None, sock_read=5.0)
                async with self._session.get(self.camera_url, timeout=timeout) as response:
                    multipart = aiohttp.MultipartReader.from_response(response)
                    
                    while not self._closed:
                        part = await multipart.next()
                        if part is None:
                            break
                        
                        jpg_data = await part.read()
                        if not jpg_data.startswith(b'\xff\xd8'):
                            continue
                            
                        frame_array = np.frombuffer(jpg_data, dtype=np.uint8)
                        img = cv2.imdecode(frame_array, cv2.IMREAD_COLOR)
                        
                        if img is not None:
                            if self.k_rot != 0:
                                img = np.rot90(img, k=self.k_rot, axes=(0, 1))
                                img = np.ascontiguousarray(img)
                                
                            self._latest_img = img
                            self._new_frame_event.set()

            except Exception as e:
                logging.error(f"Stream read error: {e}")
                
            if not self._closed:
                await asyncio.sleep(1)

    async def recv(self):
        if self._closed:
            raise Exception("Track is closed")

        await self._new_frame_event.wait()
        self._new_frame_event.clear()

        img = self._latest_img
        new_frame = VideoFrame.from_ndarray(img, format="bgr24")
        
        pts, time_base = await self.next_timestamp()
        new_frame.pts = pts
        new_frame.time_base = time_base
        
        return new_frame

    def stop(self):
        super().stop()
        self._closed = True
        if self._scrape_task:
            self._scrape_task.cancel()
        if self._session:
            asyncio.create_task(self._session.close())

class WebrtcManager:
    def __init__(self, deviceName, our_peer_id, options, close_queue, token, region, recorder, verbose=False):
        self._logger = logging.getLogger("CrealityWebRTCRelay")
        self.our_peer_id = our_peer_id
        self.peers = {}
        self.options = options
        self.verbose = verbose
        self.deviceName = deviceName
        self.urls = ""
        self.username = ""
        self.credential = ""
        self.token = token
        self.websockets_client = None
        self.close_queue = close_queue

    async def signaling_message_handler(self, ws, message):
        data = json.loads(message)
        action = data.get("action")
        self.websockets_client = ws

        if action == "ice_msg":
            sdpMessage = data.get("sdpMessage", {})
            msg_type = sdpMessage.get("type")

            if msg_type == "offer":
                sdp = sdpMessage.get("data")
                peerId = data.get("from")
                iceServer = data.get("iceServers")

                if iceServer:
                    self.urls = iceServer[0].get("urls")
                    self.username = iceServer[0].get("username")
                    self.credential = iceServer[0].get("credential")

                for pid in list(self.peers.keys()):
                    await self.remove_peer(pid)

                await self.add_peer(peerId, "admin", True, "")
                await self.update_session_description(self.peers[peerId], sdp, peerId)

            elif msg_type == "candidate":
                candidateMap = sdpMessage.get("data")
                peerId = data.get("from")
                if peerId in self.peers:
                    await self.update_ice_candidate(self.peers[peerId], candidateMap)

        elif action == "push_online":
            iceServer = data.get("iceServers")
            if iceServer:
                self.urls = iceServer[0].get("urls")
                self.username = iceServer[0].get("username")
                self.credential = iceServer[0].get("credential")

    async def add_peer(self, peer_id, peer_type, polite, media):
        vcfg = load_video_config()
        cam_url = vcfg.get("camera_url", "http://127.0.0.1/webcam/?action=stream")
        rot_deg = vcfg.get("rotation", 0)

        iceServers = [{"urls": self.urls, "username": self.username, "credential": self.credential}]
        config = RTCConfiguration([RTCIceServer(s["urls"], s["username"], s["credential"]) for s in iceServers])
        pc = RTCPeerConnection(config)

        video_track = RawMjpegTrack(cam_url, rot_deg)

        self.peers[peer_id] = {
            "peerId": peer_id,
            "polite": polite,
            "rtcPeerConnection": pc,
            "localVideoStream": video_track
        }

        pc.addTransceiver("video", direction="sendonly")
        pc.addTrack(video_track)

        @pc.on("iceconnectionstatechange")
        def on_iceconnectionstatechange():
            if pc.iceConnectionState in ("failed", "disconnected", "closed"):
                self.close_queue.put(peer_id)

        @pc.on("connectionstatechange")
        def on_connectionstatechange():
            if pc.connectionState in ("failed", "disconnected", "closed"):
                self.close_queue.put(peer_id)

    async def update_session_description(self, peer, description, peerId):
        try:
            rtc_desc = RTCSessionDescription(description["sdp"], description["type"])
            pc = peer["rtcPeerConnection"]

            await pc.setRemoteDescription(rtc_desc)
            if description["type"] == "offer":
                answer = await pc.createAnswer()
                await pc.setLocalDescription(answer)

                resp = {
                    "action": "ice_msg",
                    "sdpmessage": {
                        "data": {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type},
                        "type": "answer"
                    },
                    "to": peerId
                }
                self.websockets_client.send(json.dumps(resp))

                for line in pc.localDescription.sdp.split("\r\n"):
                    if line.startswith("a=candidate"):
                        cand = {
                            "action": "ice_msg",
                            "sdpmessage": {
                                "data": {"sdpMLineIndex": 0, "sdpMid": "0", "candidate": line[2:]},
                                "type": "candidate"
                            },
                            "to": peerId
                        }
                        self.websockets_client.send(json.dumps(cand))
        except Exception as e:
            self._logger.error(f"SDP Error: {e}")

    async def update_ice_candidate(self, peer, candidate):
        pc = peer["rtcPeerConnection"]
        data = candidate.get("candidate", "")
        if not data:
            return
        try:
            parts = data.split()
            rtc_candidate = RTCIceCandidate(
                component=int(parts[1]),
                foundation=parts[0].replace("candidate:", ""),
                ip=parts[4],
                port=int(parts[5]),
                priority=int(parts[3]),
                protocol=parts[2],
                type=parts[7],
                sdpMid=str(candidate.get("sdpMid", "0")),
                sdpMLineIndex=int(candidate.get("sdpMLineIndex", 0))
            )
            await pc.addIceCandidate(rtc_candidate)
        except Exception:
            pass

    async def remove_peer(self, peerId):
        peer = self.peers.pop(peerId, None)
        if peer:
            if peer.get("localVideoStream"):
                try:
                    peer["localVideoStream"].stop()
                except Exception:
                    pass
            if peer.get("rtcPeerConnection"):
                try:
                    await peer["rtcPeerConnection"].close()
                except Exception:
                    pass

    def token_update(self, token):
        self.token = str(token)

    def push_state(self, ws):
        if ws:
            try:
                ws.send(json.dumps({
                    "action": "push_state",
                    "to": "server",
                    "pushState": {"pcPool": []}
                }))
            except Exception:
                pass
