import asyncio
import base64
import json
from typing import Optional

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
        credentials: Optional[CredentialsModel],
        video_track: VideoStreamTrack,
        thing_name: str,
    ):
        self.region = region
        self.credentials = credentials
        self.video_track = video_track
        self.thing_name = thing_name

        self.kinesisvideo = None
        self.endpoints = None
        self.endpoint_https = None
        self.endpoint_wss = None
        self.ice_servers = None
        self.PCMap = {}
        self.DCMap = {}

        self.relay = MediaRelay()

        self._running = False
        self._init_client()

    def _init_client(self):
        if self.credentials:
            self.kinesisvideo = boto3.client(
                "kinesisvideo",
                region_name=self.region,
                aws_access_key_id=self.credentials.access_key_id,
                aws_secret_access_key=self.credentials.secret_access_key,
                aws_session_token=self.credentials.session_token,
            )
            response = self.kinesisvideo.describe_signaling_channel(
                ChannelName=self.thing_name
            )
            self.channel_arn = response['ChannelInfo']['ChannelARN']
        else:
            self.kinesisvideo = boto3.client("kinesisvideo", region_name=self.region)

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
            params={"X-Amz-ChannelARN": self.channel_arn, "X-Amz-ClientId": "MASTER"},
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

        self.DCMap[client_id] = pc.createDataChannel("kvsDataChannel")
        self.PCMap[client_id] = pc

        @pc.on("connectionstatechange")
        async def on_connectionstatechange():
            loguru.logger.info(f"[{client_id}] connectionState: {pc.connectionState}")
            if pc.connectionState in ["failed", "closed"]:
                await pc.close()
                if client_id in self.PCMap:
                    del self.PCMap[client_id]

        @pc.on("track")
        def on_track(track):
            MediaBlackhole().addTrack(track)

        await pc.setRemoteDescription(
            RTCSessionDescription(sdp=payload["sdp"], type=payload["type"])
        )

        video_transceiver_exists = any(t.kind == "video" for t in pc.getTransceivers())
        if video_transceiver_exists:
            pc.addTrack(self.relay.subscribe(self.video_track))

        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        await websocket.send(
            self._encode_msg("SDP_ANSWER", pc.localDescription, client_id)
        )

    async def _handle_ice_candidate(self, payload, client_id):
        if client_id in self.PCMap:
            candidate = candidate_from_sdp(payload["candidate"])
            candidate.sdpMid = payload["sdpMid"]
            candidate.sdpMLineIndex = payload["sdpMLineIndex"]
            await self.PCMap[client_id].addIceCandidate(candidate)

    async def run(self):
        self._running = True
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
                        msg_type, payload, client_id = self._decode_msg(message)

                        if msg_type == "SDP_OFFER":
                            asyncio.create_task(
                                self._handle_sdp_offer(payload, client_id, websocket)
                            )
                        elif msg_type == "ICE_CANDIDATE":
                            asyncio.create_task(
                                self._handle_ice_candidate(payload, client_id)
                            )

            except Exception as e:
                loguru.logger.error(f"Signaling error: {e}. Retrying in 5s...")
                await asyncio.sleep(5)

    async def stop(self):
        self._running = False
        for pc in list(self.PCMap.values()):
            await pc.close()
        self.PCMap.clear()
