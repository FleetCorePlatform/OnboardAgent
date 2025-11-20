from pydantic import BaseModel


class Position(BaseModel):
    latitude_deg: float
    longitude_deg: float
    relative_altitude_m: float


class Battery(BaseModel):
    temperature_degc: float
    voltage_v: float
    remaining_percent: float


class Health(BaseModel):
    is_gyrometer_calibration_ok: bool
    is_accelerometer_calibration_ok: bool
    is_magnetometer_calibration_ok: bool
    is_local_position_ok: bool
    is_global_position_ok: bool
    is_home_position_ok: bool


class Velocity(BaseModel):
    ground_speed_ms: float
    heading_deg: float


class TelemetryData(BaseModel):
    timestamp: float
    position: Position
    battery: Battery
    health: Health
    velocity: Velocity
