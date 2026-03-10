import asyncio
from asyncio import Task
from typing import Callable, Optional
from cbor2 import loads, dumps
from loguru import logger

from src.core.drone_controller import MavsdkController
from src.enums.manual_control_enums import (
    PacketType,
    ManualControlAckStatus,
    ControlStatus,
    ControlActions,
)
from src.models.manual_control import (
    ControlPacket,
    HandshakeReqPacket,
    HandshakeAckPacket,
    AckPacketPayload,
    ManualControlState,
    ManualControlActionState,
    CommandReqPayload,
    CommandAckPacket,
    CommandAckPayload,
    CommandReqPacket,
)


class ManualController:
    def __init__(
        self,
        drone: MavsdkController,
        try_take_control_cb: Callable[[], bool],
        release_control_cb: Callable[[], None],
        send_data_msg: Callable[[str], None],
    ):
        self._drone = drone
        self._try_take_control = try_take_control_cb
        self._release_control = release_control_cb
        self._active = False
        self._control_sequence_id = 0
        self._send_data_msg = send_data_msg

        self._telemetry_streamer_task = Optional[Task] | None

    async def handle_packet(self, packet_bytes: bytes) -> bytes | None:
        try:
            packet = self.parse_packet(packet_bytes)
            if isinstance(packet, HandshakeReqPacket):
                ack = await self._handle_handshake_req_packet(packet)
                if ack:
                    return dumps(ack.model_dump(mode="json"))
            elif isinstance(packet, ControlPacket):
                await self._handle_control_packet(packet)
            elif isinstance(packet, CommandReqPacket):
                ack_payload = await self._handle_command_packet(packet)
                if ack_payload:
                    ack_packet = CommandAckPacket(
                        type=PacketType.CMD_ACK, payload=ack_payload
                    )
                    return dumps(ack_packet.model_dump(mode="json"))
        except Exception as e:
            logger.error(f"Unhandled exception occurred: {e}")
        return None

    async def send_telemetry(self):
        data = await self._drone.get_telemetry()
        # packet = TelemetryPacket(...) // TODO: Implement telemetry packet sending
        self._send_data_msg(dumps(packet.model_dump(mode="json")))

    async def start_telemetry_streaming(self):
        while self._active:
            asyncio.create_task(self.send_telemetry())
            await asyncio.sleep(0.5)


    def parse_packet(self, packet: bytes):
        data = loads(packet)
        packet_type_raw = data.get("type")

        if isinstance(packet_type_raw, str):
            packet_type = PacketType[packet_type_raw]
        else:
            packet_type = PacketType(packet_type_raw)

        if packet_type == PacketType.CONTROL:
            return ControlPacket.model_validate(data)
        elif packet_type == PacketType.HANDSHAKE_REQ:
            return HandshakeReqPacket.model_validate(data)
        elif packet_type == PacketType.CMD_REQ:
            return CommandReqPacket.model_validate(data)
        else:
            raise ValueError(f"Unhandled PacketType: {packet_type}")

    async def _handle_handshake_req_packet(
        self, packet: HandshakeReqPacket
    ) -> HandshakeAckPacket | None:
        command = packet.payload.command

        if command == ControlStatus.STOP_MANUAL_CONTROL:
            if not self._active:
                return HandshakeAckPacket(
                    type=PacketType.HANDSHAKE_ACK,
                    payload=AckPacketPayload(
                        status=ManualControlAckStatus.DENIED,
                        reason="Not in manual control mode",
                    ),
                )

            self._active = False
            self._release_control()
            return HandshakeAckPacket(
                type=PacketType.HANDSHAKE_ACK,
                payload=AckPacketPayload(status=ManualControlAckStatus.STOPPED),
            )

        elif command == ControlStatus.START_MANUAL_CONTROL:
            if self._active:
                return HandshakeAckPacket(
                    type=PacketType.HANDSHAKE_ACK,
                    payload=AckPacketPayload(status=ManualControlAckStatus.ACCEPTED),
                )

            if not self._try_take_control():
                return HandshakeAckPacket(
                    type=PacketType.HANDSHAKE_ACK,
                    payload=AckPacketPayload(
                        status=ManualControlAckStatus.DENIED,
                        reason="State transition not allowed",
                    ),
                )

            healthy = await self._drone.check_system_health()
            if not healthy:
                self._release_control()
                return HandshakeAckPacket(
                    type=PacketType.HANDSHAKE_ACK,
                    payload=AckPacketPayload(
                        status=ManualControlAckStatus.DENIED,
                        reason="System is not healthy",
                    ),
                )

            try:
                await self._drone.system.manual_control.set_manual_control_input(
                    0.0, 0.0, 0.5, 0.0
                )
                await asyncio.sleep(0.5)
                await self._drone.system.manual_control.start_position_control()
            except Exception as e:
                logger.error(f"Failed to initialize manual control: {e}")
                self._release_control()
                return HandshakeAckPacket(
                    type=PacketType.HANDSHAKE_ACK,
                    payload=AckPacketPayload(
                        status=ManualControlAckStatus.DENIED,
                        reason="Failed to initialize manual control",
                    ),
                )

            self._active = True
            self._telemetry_streamer_task = asyncio.create_task(self.start_telemetry_streaming())
            return HandshakeAckPacket(
                type=PacketType.HANDSHAKE_ACK,
                payload=AckPacketPayload(status=ManualControlAckStatus.ACCEPTED),
            )

    async def _handle_control_packet(self, packet: ControlPacket):
        if not self._active:
            return

        if packet.sequence_id <= self._control_sequence_id:
            return

        self._control_sequence_id = packet.sequence_id

        if isinstance(packet.payload, ManualControlState):
            await self._dispatch_control_state(packet.payload)
        elif isinstance(packet.payload, ManualControlActionState):
            await self._dispatch_control_action(packet.payload)

    async def _handle_command_packet(
        self, packet: CommandReqPacket
    ) -> CommandAckPayload | None:
        logger.debug(f"Received command packet: {packet}")

        return await self._dispatch_command(packet.payload)

    async def _dispatch_control_state(self, state: ManualControlState):
        try:
            await self._drone.system.manual_control.set_manual_control_input(
                state.pitch, state.roll, state.throttle, state.yaw
            )
        except Exception as e:
            logger.error(f"Failed to set manual control input: {e}")

    async def _dispatch_control_action(self, action_state: ManualControlActionState):
        if action_state.action == ControlActions.TAKEOFF:
            await self._drone.system.action.arm()
            await self._drone.system.action.takeoff()
        elif action_state.action == ControlActions.LAND:
            await self._drone.system.action.land()
            await self._drone.system.action.disarm()

    async def _dispatch_command(self, payload: CommandReqPayload) -> CommandAckPayload:
        args_str = " ".join(payload.args) if payload.args else ""
        command_str = f"{payload.command.strip()} {args_str}".strip() + "\n"

        completion_event = asyncio.Event()
        output_buffer = []

        logger.debug(f"Awaiting command response for: {command_str}")

        async def listen():
            try:
                async for output in self._drone.system.shell.receive():
                    output_buffer.append(output)
                    logger.debug(f"Shell output: {output.strip()}")
                    output_lower = output.lower()
                    if (
                        "pxh>" in output_lower
                        or "done" in output_lower
                        or "failed" in output_lower
                        or "error" in output_lower
                        or "warn" in output_lower
                    ):
                        completion_event.set()
            except asyncio.CancelledError:
                pass

        task = asyncio.create_task(listen())
        await asyncio.sleep(0.1)

        try:
            await self._drone.system.shell.send(command_str)
            await asyncio.wait_for(completion_event.wait(), timeout=30.0)
            status_code = 0
        except asyncio.TimeoutError:
            logger.warning(f"Shell command timeout: {command_str.strip()}")
            status_code = 2
        except Exception as e:
            logger.error(f"Shell command failure {command_str.strip()}: {e}")
            output_buffer.append(str(e))
            status_code = 1
        finally:
            task.cancel()

        lines = "".join(output_buffer).splitlines()

        filtered_lines = [
            line
            for line in lines
            if "pxh>" not in line
            and "nsh>" not in line
            and line.strip() != payload.command.strip()
        ]

        result_str = "\n".join(filtered_lines).strip()

        return CommandAckPayload(
            status=status_code, result=result_str if result_str else None
        )
