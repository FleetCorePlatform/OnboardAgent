import asyncio
from datetime import datetime
from math import sqrt
from typing import Optional

import loguru
from mavsdk import System as MavSystem

from src.config import Config
from src.models.telemetry_data import TelemetryData, Position, Battery, Health, Velocity


class TelemetryCollector:
    def __init__(self, config: Config, drone: MavSystem, interval_hz: float) -> None:
        self.device_name = config.thing_name
        self.drone = drone
        self.interval = 1.0 / interval_hz
        self.queue = asyncio.Queue(maxsize=100)
        self.__running = False
        self.error_count = 0
        self.last_error: Optional[Exception] = None

    async def start(self):
        """Start collecting telemetry at fixed rate."""
        self.__running = True
        asyncio.create_task(self._collect_loop())

    async def stop(self):
        self.__running = False

    async def _collect_loop(self):
        """Sample telemetry at fixed intervals."""
        while self.__running:
            try:
                telemetry = await self._sample_telemetry()

                try:
                    self.queue.put_nowait(telemetry)
                except asyncio.QueueFull:
                    self.queue.get_nowait()

                    self.queue.put_nowait(telemetry)
            except Exception as e:
                self.error_count += 1
                self.last_error = e

            await asyncio.sleep(self.interval)

    async def _sample_telemetry(self) -> TelemetryData:
        """Sample telemetry at fixed rate."""
        position_raw, battery_raw, health_raw, velocity_raw, heading_raw = (
            await asyncio.gather(
                self.drone.telemetry.position().__anext__(),
                self.drone.telemetry.battery().__anext__(),
                self.drone.telemetry.health().__anext__(),
                self.drone.telemetry.velocity_ned().__anext__(),
                self.drone.telemetry.heading().__anext__(),
            )
        )

        ground_speed: float = sqrt(velocity_raw.east_m_s**2 + velocity_raw.north_m_s**2)

        data: TelemetryData = TelemetryData(
            device_name=self.device_name,
            timestamp=datetime.now().timestamp(),
            position=Position(**position_raw.__dict__),
            battery=Battery(**battery_raw.__dict__),
            health=Health(**health_raw.__dict__),
            velocity=Velocity(
                ground_speed_ms=ground_speed, heading_deg=heading_raw.heading_deg
            ),
        )

        return data
