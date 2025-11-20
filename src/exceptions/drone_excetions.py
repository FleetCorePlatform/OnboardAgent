class DroneException(Exception):
    """Base for all drone operation failures"""

    pass


class DroneConnectException(DroneException):
    pass


class DroneArmException(DroneException):
    pass


class DroneUploadException(DroneException):
    pass


class DroneStartMissionException(DroneException):
    pass


class DroneCancelMissionException(DroneException):
    pass


class DroneStreamMissionProgressException(DroneException):
    pass


class DroneStreamInAirException(DroneException):
    pass
