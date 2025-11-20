import asyncio
import base64
from typing import Optional

import cbor2
import loguru

from src.core.mqtt_manager import MqttManager
from src.telemetry.collector import TelemetryCollector


class TelemetryPublisher:
    def __init__(
        self,
        collector: TelemetryCollector,
        mqtt: MqttManager,
        topic: str,
        batch_size: int = 10,
    ):
        self.collector = collector
        self.mqtt = mqtt
        self.topic = topic
        self.batch_size = batch_size
        self._running = False
        self._task = None
        self.error_count = 0
        self.last_error: Optional[Exception] | None = None

    async def start(self):
        """Start publishing telemetry batches."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._publish_topic())

    async def stop(self):
        """Stop publishing and flush remaining batch."""
        self._running = False
        if self._task:
            await self._task

    async def _publish_topic(self):
        """Consume queue and publish in batches."""
        batch = []

        while self._running:
            try:
                telemetry = await asyncio.wait_for(
                    self.collector.queue.get(), timeout=3.5
                )
                batch.append(telemetry.model_dump())

                if len(batch) >= self.batch_size:
                    self._publish_batch(batch)
                    batch = []

            except asyncio.TimeoutError:
                if batch:
                    self._publish_batch(batch)
                    batch = []

            except Exception as e:
                self.error_count += 1
                self.last_error = e
                pass

        if batch:
            self._publish_batch(batch)

    def _publish_batch(self, batch: list):
        """Publish batch to MQTT."""
        try:
            cbor_bytes: bytes = cbor2.dumps(batch)
            encoded = base64.b64encode(cbor_bytes).decode("ascii")

            self.mqtt.publish(self.topic, encoded)
        except Exception as e:
            self.error_count += 1
            self.last_error = e
            pass
