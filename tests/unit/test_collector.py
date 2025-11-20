import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from mavsdk import System as MavSystem

from src.telemetry.collector import TelemetryCollector
from src.models.telemetry_data import TelemetryData


@pytest.fixture
def mock_drone():
    drone = Mock(spec=MavSystem)
    drone.telemetry = Mock()
    return drone


@pytest.fixture
def mock_telemetry_streams(mock_drone):
    """Mock all telemetry async generators."""

    position_mock = Mock()
    position_mock.latitude_deg = 47.3977
    position_mock.longitude_deg = 8.5456
    position_mock.relative_altitude_m = 10.5

    position_stream = AsyncMock()
    position_stream.__anext__ = AsyncMock(return_value=position_mock)
    mock_drone.telemetry.position.return_value = position_stream

    battery_mock = Mock()
    battery_mock.temperature_degc = 25.0
    battery_mock.voltage_v = 12.6
    battery_mock.remaining_percent = 85.0

    battery_stream = AsyncMock()
    battery_stream.__anext__ = AsyncMock(return_value=battery_mock)
    mock_drone.telemetry.battery.return_value = battery_stream

    health_mock = Mock()
    health_mock.is_gyrometer_calibration_ok = True
    health_mock.is_accelerometer_calibration_ok = True
    health_mock.is_magnetometer_calibration_ok = True
    health_mock.is_local_position_ok = True
    health_mock.is_global_position_ok = True
    health_mock.is_home_position_ok = True

    health_stream = AsyncMock()
    health_stream.__anext__ = AsyncMock(return_value=health_mock)
    mock_drone.telemetry.health.return_value = health_stream

    velocity_mock = Mock()
    velocity_mock.north_m_s = 3.0
    velocity_mock.east_m_s = 4.0
    velocity_mock.yaw_deg = 45.0

    velocity_stream = AsyncMock()
    velocity_stream.__anext__ = AsyncMock(return_value=velocity_mock)
    mock_drone.telemetry.velocity_ned.return_value = velocity_stream

    return mock_drone


@pytest.mark.asyncio
async def test_collector_initialization(mock_drone):
    collector = TelemetryCollector(mock_drone, interval_hz=1.0)

    assert collector.drone == mock_drone
    assert collector.interval == 1.0
    assert collector.queue.maxsize == 100
    assert collector.error_count == 0
    assert collector.last_error is None


@pytest.mark.asyncio
async def test_sample_telemetry(mock_telemetry_streams):
    collector = TelemetryCollector(mock_telemetry_streams, interval_hz=1.0)

    telemetry = await collector._sample_telemetry()

    assert isinstance(telemetry, TelemetryData)
    assert telemetry.position.latitude_deg == 47.3977
    assert telemetry.battery.voltage_v == 12.6
    assert telemetry.health.is_gyrometer_calibration_ok is True
    assert telemetry.velocity.ground_speed_ms == 5.0  # sqrt(3^2 + 4^2)
    assert telemetry.velocity.heading_deg == 45.0
    assert isinstance(telemetry.timestamp, float)


@pytest.mark.asyncio
async def test_collector_starts_and_collects(mock_telemetry_streams):
    collector = TelemetryCollector(mock_telemetry_streams, interval_hz=10.0)

    await collector.start()
    await asyncio.sleep(0.3)

    assert collector.queue.qsize() > 0

    await collector.stop()


@pytest.mark.asyncio
async def test_collector_stops(mock_telemetry_streams):
    collector = TelemetryCollector(mock_telemetry_streams, interval_hz=10.0)

    await collector.start()
    await asyncio.sleep(0.1)

    initial_size = collector.queue.qsize()

    await collector.stop()
    await asyncio.sleep(0.2)

    final_size = collector.queue.qsize()
    assert final_size == initial_size


@pytest.mark.asyncio
async def test_queue_full_drops_oldest(mock_telemetry_streams):
    collector = TelemetryCollector(mock_telemetry_streams, interval_hz=100.0)
    collector.queue = asyncio.Queue(maxsize=5)

    await collector.start()
    await asyncio.sleep(0.2)
    await collector.stop()

    assert collector.queue.qsize() == 5


@pytest.mark.asyncio
async def test_error_handling_continues_collection(mock_drone):
    """Test that sampling errors don't stop collection."""
    collector = TelemetryCollector(mock_drone, interval_hz=10.0)

    position_stream = AsyncMock()
    position_stream.__anext__ = AsyncMock(
        side_effect=[
            Exception("Sensor error"),
            Mock(latitude_deg=47.0, longitude_deg=8.0, relative_altitude_m=10.0),
        ]
    )
    mock_drone.telemetry.position.return_value = position_stream

    battery_stream = AsyncMock()
    battery_stream.__anext__ = AsyncMock(
        return_value=Mock(temperature_degc=25.0, voltage_v=12.6, remaining_percent=85.0)
    )
    mock_drone.telemetry.battery.return_value = battery_stream

    health_stream = AsyncMock()
    health_stream.__anext__ = AsyncMock(
        return_value=Mock(
            is_gyrometer_calibration_ok=True,
            is_accelerometer_calibration_ok=True,
            is_magnetometer_calibration_ok=True,
            is_local_position_ok=True,
            is_global_position_ok=True,
            is_home_position_ok=True,
        )
    )
    mock_drone.telemetry.health.return_value = health_stream

    velocity_stream = AsyncMock()
    velocity_stream.__anext__ = AsyncMock(
        return_value=Mock(north_m_s=3.0, east_m_s=4.0, yaw_deg=45.0)
    )
    mock_drone.telemetry.velocity_ned.return_value = velocity_stream

    await collector.start()
    await asyncio.sleep(0.3)
    await collector.stop()

    assert collector.error_count >= 1
    assert collector.last_error is not None


@pytest.mark.asyncio
async def test_ground_speed_calculation(mock_telemetry_streams):
    """Test ground speed calculation from velocity components."""
    collector = TelemetryCollector(mock_telemetry_streams, interval_hz=1.0)

    telemetry = await collector._sample_telemetry()

    assert telemetry.velocity.ground_speed_ms == pytest.approx(5.0, rel=0.01)


@pytest.mark.asyncio
async def test_multiple_samples_unique_timestamps(mock_telemetry_streams):
    """Test that consecutive samples have different timestamps."""
    collector = TelemetryCollector(mock_telemetry_streams, interval_hz=100.0)

    sample1 = await collector._sample_telemetry()
    await asyncio.sleep(0.01)
    sample2 = await collector._sample_telemetry()

    assert sample2.timestamp > sample1.timestamp
