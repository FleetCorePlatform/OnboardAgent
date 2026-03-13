import asyncio
import base64
import json
from typing import Optional, Callable

import boto3
import loguru
import websockets
from aiortc import (
    VideoStreamTrack,
    RTCIceServer,
    RTCSessionDescription,
    RTCConfiguration,
    RTCPeerConnection,
)
from aiortc.contrib.media import MediaRelay, MediaBlackhole
from aiortc.sdp import candidate_from_sdp
from botocore.auth import SigV4QueryAuth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
from botocore.session import Session
from loguru import logger

from src.models.credentials_model import CredentialsModel


class KinesisVideoClient:
    def __init__(
        self,
        region: str,
        channel_name: str,
        credentials: Optional[CredentialsModel],
        video_track: VideoStreamTrack,
        data_channel_callback: Callable,
        data_channel_open_callback: Optional[Callable] = None,
        data_channel_close_callback: Optional[Callable] = None,
    ):
        self.region = region
        self.credentials = credentials
        self.video_track = video_track
        self.channel_name = channel_name

        self.kinesisvideo = None
        self.endpoints = None
        self.endpoint_https = None
        self.endpoint_wss = None
        self.ice_servers = None
        self.PCMap = {}
        self.DCMap = {}

        self.pending_tasks = set()

        self.relay = MediaRelay()

        self.data_channel_callback = data_channel_callback
        self.data_channel_open_callback = data_channel_open_callback
        self.data_channel_close_callback = data_channel_close_callback

        self._running = False
        self._init_client()

    def _track_task(self, task):
        self.pending_tasks.add(task)
        task.add_done_callback(self.pending_tasks.discard)

    def send_data_message(self, message: bytes):
        for channel in self.DCMap.values():
            if channel.readyState == "open":
                channel.send(message)

    def _init_client(self):
        client_kwargs = {"region_name": self.region}

        if self.credentials:
            client_kwargs.update(
                {
                    "aws_access_key_id": self.credentials.access_key_id,
                    "aws_secret_access_key": self.credentials.secret_access_key,
                    "aws_session_token": self.credentials.session_token,
                }
            )

        self.kinesisvideo = boto3.client("kinesisvideo", **client_kwargs)

        response = self.kinesisvideo.describe_signaling_channel(
            ChannelName=self.channel_name
        )
        self.channel_arn = response["ChannelInfo"]["ChannelARN"]

    def get_signaling_channel_endpoint(self):
        if self.endpoints is None:
            try:
                endpoints = self.kinesisvideo.get_signaling_channel_endpoint(
                    ChannelARN=self.channel_arn,
                    SingleMasterChannelEndpointConfiguration={
                        "Protocols": ["HTTPS", "WSS"],
                        "Role": "MASTER",
                    },
                )
                self.endpoints = {
                    "HTTPS": next(
                        o["ResourceEndpoint"]
                        for o in endpoints["ResourceEndpointList"]
                        if o["Protocol"] == "HTTPS"
                    ),
                    "WSS": next(
                        o["ResourceEndpoint"]
                        for o in endpoints["ResourceEndpointList"]
                        if o["Protocol"] == "WSS"
                    ),
                }
                self.endpoint_https = self.endpoints["HTTPS"]
                self.endpoint_wss = self.endpoints["WSS"]
            except Exception as e:
                logger.error(f"Error getting signaling endpoints: {e}")
                raise
        return self.endpoints

    def _prepare_ice_servers(self):
        if self.credentials:
            kinesis_video_signaling = boto3.client(
                "kinesis-video-signaling",
                endpoint_url=self.endpoint_https,
                region_name=self.region,
                aws_access_key_id=self.credentials.access_key_id,
                aws_secret_access_key=self.credentials.secret_access_key,
                aws_session_token=self.credentials.session_token,
            )
        else:
            kinesis_video_signaling = boto3.client(
                "kinesis-video-signaling",
                endpoint_url=self.endpoint_https,
                region_name=self.region,
            )

        try:
            ice_server_config = kinesis_video_signaling.get_ice_server_config(
                ChannelARN=self.channel_arn, ClientId="MASTER"
            )
        except Exception as e:
            loguru.logger.error(f"Error getting ICE server config: {e}")
            return []

        iceServers = [
            RTCIceServer(urls=f"stun:stun.kinesisvideo.{self.region}.amazonaws.com:443")
        ]
        for iceServer in ice_server_config["IceServerList"]:
            iceServers.append(
                RTCIceServer(
                    urls=iceServer["Uris"],
                    username=iceServer["Username"],
                    credential=iceServer["Password"],
                )
            )
        self.ice_servers = iceServers
        return self.ice_servers

    def _create_wss_url(self):
        if self.credentials:
            auth_credentials = Credentials(
                access_key=self.credentials.access_key_id,
                secret_key=self.credentials.secret_access_key,
                token=self.credentials.session_token,
            )
        else:
            session = Session()
            auth_credentials = session.get_credentials()

        SigV4 = SigV4QueryAuth(auth_credentials, "kinesisvideo", self.region, 299)
        aws_request = AWSRequest(
            method="GET",
            url=self.endpoint_wss,
            params={"X-Amz-ChannelARN": self.channel_arn},
        )
        SigV4.add_auth(aws_request)
        return aws_request.prepare().url

    def _decode_msg(self, msg):
        try:
            data = json.loads(msg)
            if "messagePayload" in data:
                payload = json.loads(
                    base64.b64decode(data["messagePayload"].encode("ascii")).decode(
                        "ascii"
                    )
                )
                return data["messageType"], payload, data.get("senderClientId")
        except json.decoder.JSONDecodeError:
            pass
        return "", {}, ""

    def _encode_msg(self, action, payload, client_id):
        if isinstance(payload, RTCSessionDescription):
            payload_dict = {"sdp": payload.sdp, "type": payload.type}
        elif hasattr(payload, "__dict__"):
            payload_dict = payload.__dict__
        else:
            payload_dict = payload

        return json.dumps(
            {
                "action": action,
                "messagePayload": base64.b64encode(
                    json.dumps(payload_dict).encode("ascii")
                ).decode("ascii"),
                "recipientClientId": client_id,
            }
        )

    async def _handle_sdp_offer(self, payload, client_id, websocket):
        iceServers = self._prepare_ice_servers()
        configuration = RTCConfiguration(iceServers=iceServers)
        pc = RTCPeerConnection(configuration=configuration)

        self.PCMap[client_id] = pc

        @pc.on("connectionstatechange")
        async def on_connectionstatechange():
            loguru.logger.info(f"[{client_id}] connectionState: {pc.connectionState}")
            if pc.connectionState in ["failed", "closed"]:
                self.PCMap.pop(client_id, None)
                self.DCMap.pop(client_id, None)
                if self.data_channel_close_callback:
                    self.data_channel_close_callback()

        @pc.on("track")
        def on_track(track):
            MediaBlackhole().addTrack(track)

        @pc.on("datachannel")
        def on_datachannel(channel):
            loguru.logger.info(
                f"[{client_id}] Data channel established: {channel.label}"
            )
            self.DCMap[client_id] = channel

            if self.data_channel_open_callback:
                self.data_channel_open_callback()

            @channel.on("message")
            def on_message(message):
                self.data_channel_callback(message)

            @channel.on("close")
            def on_close():
                loguru.logger.info(
                    f"[{client_id}] Data channel closed: {channel.label}"
                )
                if self.data_channel_close_callback:
                    self.data_channel_close_callback()

        await pc.setRemoteDescription(
            RTCSessionDescription(sdp=payload["sdp"], type=payload["type"])
        )

        loguru.logger.debug(f"Adding video track to peer connection for {client_id}")
        pc.addTrack(self.relay.subscribe(self.video_track))

        loguru.logger.debug(f"[{client_id}] Creating SDP answer...")
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        loguru.logger.debug(f"[{client_id}] Sending SDP answer via WebSocket...")
        await websocket.send(
            self._encode_msg("SDP_ANSWER", pc.localDescription, client_id)
        )
        loguru.logger.info(f"[{client_id}] SDP Answer sent successfully.")

    async def _handle_ice_candidate(self, payload, client_id):
        if client_id in self.PCMap:
            candidate = candidate_from_sdp(payload["candidate"])
            candidate.sdpMid = payload["sdpMid"]
            candidate.sdpMLineIndex = payload["sdpMLineIndex"]
            await self.PCMap[client_id].addIceCandidate(candidate)

    async def run(self):
        self._running = True
        try:
            while self._running:
                try:
                    self.get_signaling_channel_endpoint()
                    wss_url = self._create_wss_url()
                    loguru.logger.info(f"Connecting to Signaling: {self.channel_arn}")

                    async with websockets.connect(wss_url) as websocket:
                        loguru.logger.info("Signaling Connected!")
                        async for message in websocket:
                            if not self._running:
                                break

                            loguru.logger.trace(
                                f"Raw signaling message received: {message[:200]}..."
                            )
                            msg_type, payload, client_id = self._decode_msg(message)
                            loguru.logger.trace(
                                f"Decoded Signaling Message: {msg_type} from {client_id}"
                            )

                            if msg_type == "SDP_OFFER":
                                loguru.logger.info(
                                    f"Handling SDP Offer from {client_id}"
                                )
                                task = asyncio.create_task(
                                    self._handle_sdp_offer(
                                        payload, client_id, websocket
                                    )
                                )
                                self._track_task(task)
                            elif msg_type == "ICE_CANDIDATE":
                                loguru.logger.trace(
                                    f"Handling ICE Candidate from {client_id}"
                                )
                                task = asyncio.create_task(
                                    self._handle_ice_candidate(payload, client_id)
                                )
                                self._track_task(task)

                except Exception as e:
                    loguru.logger.error(f"Signaling error: {e}. Retrying in 5s...")
                    if self._running:
                        await asyncio.sleep(5)
        finally:
            await self.stop()

    async def stop(self):
        self._running = False

        for pc in list(self.PCMap.values()):
            self._track_task(asyncio.create_task(pc.close()))

        self.PCMap.clear()
        self.DCMap.clear()

        if self.pending_tasks:
            loguru.logger.debug(
                f"Waiting for {len(self.pending_tasks)} pending tasks..."
            )
            try:
                results = await asyncio.shield(
                    asyncio.wait_for(
                        asyncio.gather(*self.pending_tasks, return_exceptions=True),
                        timeout=5.0,
                    )
                )
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        loguru.logger.error(f"Task {i} failed: {result}")
            except asyncio.TimeoutError:
                loguru.logger.warning(
                    "Timeout waiting for pending tasks, cancelling..."
                )
                for task in list(self.pending_tasks):
                    task.cancel()
            except asyncio.CancelledError:
                loguru.logger.debug(
                    "stop() was cancelled, tasks will continue in background"
                )
                raise

        loguru.logger.info("Stop complete")
