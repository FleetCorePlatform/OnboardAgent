import asyncio
from typing import Optional

from awscrt import mqtt_request_response, mqtt
from awsiot import mqtt_connection_builder, mqtt5_client_builder, iotjobs
from awsiot.iotjobs import (
    JobExecutionSummary,
    GetPendingJobExecutionsResponse,
    DescribeJobExecutionResponse,
)
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
        self._init_base_client(
            cert_path, private_key_path, ca_file_path, endpoint, thing_name, timeout
        )
        self._init_jobs_client(cert_path, private_key_path, endpoint, timeout)

    def _init_base_client(
        self,
        cert_path: str,
        private_key_path: str,
        ca_file_path: str,
        endpoint: str,
        thing_name: str,
        timeout: int,
    ):
        self.base_mqtt_client = mqtt_connection_builder.mtls_from_path(
            cert_filepath=cert_path,
            pri_key_filepath=private_key_path,
            ca_filepath=ca_file_path,
            endpoint=endpoint,
            client_id=thing_name,
            clean_session=False,
            keep_alive_secs=timeout,
        )

    def _init_jobs_client(
        self, cert_path: str, private_key_path: str, endpoint: str, timeout: int
    ):
        self.jobs_mqtt_client = mqtt5_client_builder.mtls_from_path(
            endpoint=endpoint,
            cert_filepath=cert_path,
            pri_key_filepath=private_key_path,
            clean_session=False,
            timeout=timeout,
        )

        rr_options = mqtt_request_response.ClientOptions(
            max_request_response_subscriptions=2,
            max_streaming_subscriptions=2,
            operation_timeout_in_seconds=timeout,
        )

        self.jobs_client = iotjobs.IotJobsClientV2(self.jobs_mqtt_client, rr_options)

    async def connect(self) -> None:
        try:
            self.base_mqtt_client.connect().result()
            self.jobs_mqtt_client.start()
        except Exception as e:
            raise MqttConnectionException(e)

    async def disconnect(self) -> None:
        self.base_mqtt_client.disconnect()
        self.jobs_mqtt_client.stop()

    def subscribe(self, topic: str, callback):
        future, _ = self.base_mqtt_client.subscribe(
            topic=topic, qos=mqtt.QoS.AT_LEAST_ONCE, callback=callback
        )
        future.result()

    def publish(self, topic: str, message: str):
        try:
            self.base_mqtt_client.publish(
                topic=topic, payload=message, qos=mqtt.QoS.AT_LEAST_ONCE
            )
        except Exception as e:
            raise MqttPublishException(e)

    def get_next_queued_job(self) -> Optional[JobExecutionSummary]:
        req = iotjobs.GetPendingJobExecutionsRequest(thing_name=self.thing_name)
        response: GetPendingJobExecutionsResponse = (
            self.jobs_client.get_pending_job_executions(req).result()
        )
        return response.queued_jobs[0] if response.queued_jobs else None

    def describe_job(self, job_id: str) -> DescribeJobExecutionResponse:
        req = iotjobs.DescribeJobExecutionRequest(
            thing_name=self.thing_name, job_id=job_id
        )
        return self.jobs_client.describe_job_execution(req).result()

    def get_job_document(
        self, job_response: DescribeJobExecutionResponse
    ) -> Optional[Job]:
        try:
            return Job.model_validate(job_response.execution.job_document)
        except ValidationError:
            return None

    def update_job_status(self, job_id: str, status: JobStatus) -> None:
        req = iotjobs.UpdateJobExecutionRequest(
            thing_name=self.thing_name, job_id=job_id, status=status.name
        )
        self.jobs_client.update_job_execution(req).result()
