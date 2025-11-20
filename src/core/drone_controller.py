from typing import AsyncIterator

from mavsdk import System as MavSystem
from mavsdk.action import ActionError
from mypy.types import AnyType

from src.exceptions.drone_excetions import *
from src.models.mission_progress import MissionProgressData

from src.enums.connection_types import ConnectionTypes


class MavsdkController:
    def __init__(self, address: str, port: int, protocol: ConnectionTypes) -> None:
        self.__connected = False
        self.address: str = address
        self.port: int = port
        self.protocol: str = protocol.value
        self.system: MavSystem = MavSystem()

    async def connect(self) -> None:
        """Connect to drone hardware."""
        connection_string: str = (
            self.protocol
            + (":///" if self.protocol == ConnectionTypes.SERIAL else "://")
            + self.address
            + ":"
            + str(self.port)
        )

        try:
            await self.system.connect(system_address=connection_string)

            async for state in self.system.core.connection_state():
                if state.is_connected:
                    break

            self.__connected = True
        except ActionError as e:
            raise DroneConnectException(e)

    async def arm(self) -> None:
        """Arm the drone."""
        try:
            await self.system.action.arm()
        except ActionError as e:
            raise DroneArmException(e)

    async def upload_mission(
        self, path_to_mission: str, return_to_launch: bool = True
    ) -> None:
        """Upload a mission."""
        try:
            await self.system.mission.set_return_to_launch_after_mission(
                return_to_launch
            )

            out = await self.system.mission_raw.import_qgroundcontrol_mission(
                path_to_mission
            )

            await self.system.mission_raw.upload_mission(out.mission_items)
        except ActionError as e:
            raise DroneUploadException(e)

    async def start_mission(self) -> None:
        try:
            await self.system.mission_raw.start_mission()
        except ActionError as e:
            raise DroneStartMissionException(e)

    async def cancel_mission(self) -> None:
        try:
            await self.system.mission_raw.clear_mission()
            await self.system.action.return_to_launch()
        except ActionError as e:
            raise DroneCancelMissionException(e)

    async def stream_mission_progress(self) -> AsyncIterator[MissionProgressData]:
        try:
            async for progress in self.system.mission_raw.mission_progress():
                yield MissionProgressData(
                    current=progress.current, total=progress.total
                )
        except ActionError as e:
            raise DroneStreamMissionProgressException(e)

    async def stream_in_air(self) -> AsyncIterator[bool]:
        try:
            async for in_air in self.system.telemetry.in_air():
                yield in_air

        except ActionError as e:
            raise DroneStreamInAirException(e)
