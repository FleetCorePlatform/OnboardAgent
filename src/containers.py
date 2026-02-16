from dependency_injector import containers, providers

from src.config import Config
from src.core.mqtt_manager import MqttManager
from src.core.drone_controller import MavsdkController
from src.core.state_machine import StateMachine
from src.core.upload_manager import UploadManager
from src.telemetry.collector import TelemetryCollector
from src.telemetry.publisher import TelemetryPublisher
from src.core.stream_handler import StreamHandler
from src.coordinator import JobCoordinator
from src.core.kinesis_video_manager import KinesisVideoClient
from src.core.credential_provider import CredentialProvider


class ApplicationContainer(containers.DeclarativeContainer):
    event_loop = providers.Dependency()
    config_path = providers.Dependency()

    config = providers.Singleton(Config, config_file=config_path)

    mqtt = providers.Singleton(
        MqttManager,
        cert_path=config.provided.cert_filepath,
        private_key_path=config.provided.pri_key_filepath,
        ca_file_path=config.provided.ca_filepath,
        endpoint=config.provided.endpoint,
        thing_name=config.provided.thing_name,
        timeout=30,
    )

    drone = providers.Singleton(
        MavsdkController,
        address=config.provided.drone_address,
        port=config.provided.drone_port,
        protocol=config.provided.drone_connection_type,
    )

    state_machine = providers.Singleton(StateMachine)

    telemetry_collector = providers.Singleton(
        TelemetryCollector,
        device_name=config.provided.thing_name,
        drone=drone.provided.system,
        interval_hz=config.provided.telemetry_sample_interval,
    )

    telemetry_publisher = providers.Singleton(
        TelemetryPublisher,
        collector=telemetry_collector,
        mqtt=mqtt,
        topic=config.provided.telemetry_topic,
        batch_size=config.provided.telemetry_sample_count,
    )

    kvs_client_factory = providers.Factory(
        KinesisVideoClient,
        thing_name=config.provided.thing_name,
    )

    credential_provider = providers.Singleton(
        CredentialProvider,
        cert_path=config.provided.cert_filepath,
        key_path=config.provided.pri_key_filepath,
        ca_path=config.provided.ca_filepath,
        role_alias=config.provided.role_alias,
        thing_name=config.provided.thing_name,
    )

    upload_manager = providers.Singleton(
        UploadManager, credential_provider=credential_provider
    )

    stream_handler = providers.Singleton(
        StreamHandler,
        device_name=config.provided.thing_name,
        port=config.provided.stream_port,
        yolo_path=config.provided.yolo_model_path,
        sample_rate=config.provided.stream_sample_rate,
        mqtt=mqtt,
        alert_topic=config.provided.alert_topic,
        presence_confirmation_frames=config.provided.presence_confirmation_frames,
        confidence_threshold=config.provided.detection_confidence_threshold,
        channel_arn=config.provided.channel_arn,
        kvs_client_factory=kvs_client_factory.provider,
        credential_provider=credential_provider,
        upload_manager=upload_manager,
        coordinate_stream=drone.provided.coordinate_stream,
    )

    coordinator = providers.Singleton(
        JobCoordinator,
        config=config,
        mqtt=mqtt,
        drone=drone,
        state=state_machine,
        collector=telemetry_collector,
        publisher=telemetry_publisher,
        streamer=stream_handler,
        loop=event_loop,
    )
