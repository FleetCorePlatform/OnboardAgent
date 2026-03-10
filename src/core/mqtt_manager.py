import asyncio
from typing import Optional, Dict, Callable

from awscrt import mqtt5, mqtt_request_response
from awsiot import mqtt5_client_builder, iotjobs
from awsiot.iotjobs import (
    JobExecutionSummary,
    DescribeJobExecutionResponse,
)
from loguru import logger
from pydantic import ValidationError

from src.enums.job_status import JobStatus
from src.exceptions.mqtt_exceptions import MqttConnectionException, MqttPublishException
from src.models.job_document import Job


class MqttManager:
    def __init__(
        self,
        cert_path: str,
        private_key_path: str,
        ca_file_path: str,
        endpoint: str,
        thing_name: str,
        timeout: int,
    ):
        self.thing_name = thing_name
        self.timeout = timeout
        self._subscriptions: Dict[str, Callable] = {}
        self._connected_future = asyncio.Future()

        self.client = mqtt5_client_builder.mtls_from_path(
            endpoint=endpoint,
            cert_filepath=cert_path,
            pri_key_filepath=private_key_path,
            ca_filepath=ca_file_path,
            client_id=thing_name,
            clean_session=False,
            session_expiry_interval_sec=3600,
            on_publish_received=self._on_publish_received,
            on_lifecycle_stopped=self._on_lifecycle_stopped,
            on_lifecycle_connection_success=self._on_lifecycle_connection_success,
            on_lifecycle_connection_failure=self._on_lifecycle_connection_failure,
        )

        rr_options = mqtt_request_response.ClientOptions(
            max_request_response_subscriptions=2,
            max_streaming_subscriptions=2,
            operation_timeout_in_seconds=timeout,
        )

        self.jobs_client = iotjobs.IotJobsClientV2(self.client, rr_options)

    def _on_publish_received(self, publish_packet_data):
        publish_packet = publish_packet_data.publish_packet
        if not publish_packet:
            return

        topic = publish_packet.topic
        if not topic:
            return

        payload = publish_packet.payload
        logger.debug(f"Received message on {topic}")

        callback = self._subscriptions.get(topic)
        if callback:
            callback(topic, payload)

    def _on_lifecycle_stopped(self, stop_event_data):
        logger.info("MQTT Client stopped")

    def _on_lifecycle_connection_success(self, success_event_data):
        logger.info("MQTT Connection Success")
        if not self._connected_future.done():
            self._connected_future.set_result(True)

    def _on_lifecycle_connection_failure(self, failure_event_data):
        logger.error(f"MQTT Connection Failure: {failure_event_data.exception}")
        if not self._connected_future.done():
            self._connected_future.set_exception(failure_event_data.exception)

    async def connect(self) -> None:
        try:
            logger.info(f"Connecting MQTT5 client: {self.thing_name}")
            self.client.start()
            await asyncio.wait_for(self._connected_future, timeout=self.timeout)
        except Exception as e:
            logger.error(f"MQTT connection failed: {e}")
            raise MqttConnectionException(e)

    async def disconnect(self) -> None:
        self.client.stop()

    async def subscribe(self, topic: str, callback: Callable):
        logger.info(f"Subscribing to {topic}...")
        self._subscriptions[topic] = callback

        subscribe_packet = mqtt5.SubscribePacket(
            subscriptions=[
                mqtt5.Subscription(topic_filter=topic, qos=mqtt5.QoS.AT_LEAST_ONCE)
            ]
        )

        try:
            future = self.client.subscribe(subscribe_packet)
            await asyncio.to_thread(future.result, self.timeout)
            logger.info(f"Subscribed to {topic}")
        except Exception as e:
            logger.warning(f"Subscription to {topic} timed out or failed: {e}")
            if not isinstance(e, TimeoutError):
                pass

    def publish(self, topic: str, message: str):
        try:
            publish_packet = mqtt5.PublishPacket(
                topic=topic,
                payload=message.encode("utf-8"),
                qos=mqtt5.QoS.AT_LEAST_ONCE,
            )
            self.client.publish(publish_packet)
        except Exception as e:
            raise MqttPublishException(e)

    async def get_next_queued_job(self) -> Optional[JobExecutionSummary]:
        try:
            req = iotjobs.GetPendingJobExecutionsRequest(thing_name=self.thing_name)
            future = self.jobs_client.get_pending_job_executions(req)
            response = await asyncio.to_thread(future.result)
            return response.queued_jobs[0] if response.queued_jobs else None
        except Exception as e:
            logger.error(f"Failed to get pending jobs: {e}")
            raise

    async def describe_job(self, job_id: str) -> DescribeJobExecutionResponse:
        req = iotjobs.DescribeJobExecutionRequest(
            thing_name=self.thing_name, job_id=job_id
        )
        future = self.jobs_client.describe_job_execution(req)
        return await asyncio.to_thread(future.result)

    def get_job_document(
        self, job_response: DescribeJobExecutionResponse
    ) -> Optional[Job]:
        try:
            if not job_response or not job_response.execution:
                return None
            return Job.model_validate(job_response.execution.job_document)
        except ValidationError:
            return None

    async def update_job_status(self, job_id: str, status: JobStatus) -> None:
        req = iotjobs.UpdateJobExecutionRequest(
            thing_name=self.thing_name, job_id=job_id, status=status.name
        )
        future = self.jobs_client.update_job_execution(req)
        await asyncio.to_thread(future.result)
