from typing import Optional, Union, List
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from src.enums.manual_control_enums import (
    ControlStatus,
    ManualControlAckStatus,
    PacketType,
    ControlActions,
)


class BasePacketModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ReqPacketPayload(BasePacketModel):
    command: ControlStatus


class AckPacketPayload(BasePacketModel):
    status: ManualControlAckStatus
    reason: Optional[str] = None


class HandshakeReqPacket(BasePacketModel):
    type: PacketType
    payload: ReqPacketPayload


class HandshakeAckPacket(BasePacketModel):
    type: PacketType
    payload: AckPacketPayload


class CommandReqPayload(BasePacketModel):
    command: str
    args: List[str]


class CommandAckPayload(BasePacketModel):
    status: int
    result: Optional[str]


class ManualControlState(BasePacketModel):
    pitch: float
    roll: float
    throttle: float
    yaw: float


class ManualControlActionState(BasePacketModel):
    action: ControlActions


class ControlPacket(BasePacketModel):
    type: PacketType
    sequence_id: int
    payload: Union[ManualControlState, ManualControlActionState]


class CommandReqPacket(BasePacketModel):
    type: PacketType
    payload: CommandReqPayload


class CommandAckPacket(BasePacketModel):
    type: PacketType
    payload: CommandAckPayload
