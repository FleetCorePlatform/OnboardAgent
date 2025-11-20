class MqttException(Exception):
    pass


class MqttPublishException(MqttException):
    pass


class MqttConnectionException(MqttException):
    pass
