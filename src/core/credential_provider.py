from datetime import datetime, timezone, timedelta
import boto3
import requests

from src.models.credentials_model import CredentialsModel


class CredentialProvider:
    def __init__(
        self,
        cert_path: str,
        key_path: str,
        ca_path: str,
        role_alias: str,
        thing_name: str,
    ) -> None:
        self._cert_path = cert_path
        self._key_path = key_path
        self._ca_path = ca_path
        self._role_alias = role_alias
        self._thing_name = thing_name
        self._current_credentials: CredentialsModel | None = None

        client = boto3.client("iot", region_name="eu-north-1")
        response = client.describe_endpoint(endpointType="iot:CredentialProvider")
        self._credentials_endpoint = response["endpointAddress"]

    def get_credentials(self) -> CredentialsModel:
        now = datetime.now(timezone.utc)

        should_refresh = False
        if self._current_credentials is None:
            should_refresh = True
        else:
            expiry = datetime.fromisoformat(
                self._current_credentials.expiration.replace("Z", "+00:00")
            )
            if now >= (expiry - timedelta(minutes=5)):
                should_refresh = True

        if should_refresh:
            url = f"https://{self._credentials_endpoint}/role-aliases/{self._role_alias}/credentials"

            response = requests.get(
                url,
                cert=(self._cert_path, self._key_path),
                verify=self._ca_path,
                headers={"x-amzn-iot-thingname": self._thing_name},
                timeout=10,
            )

            if response.status_code == 200:
                data = response.json()["credentials"]
                self._current_credentials = CredentialsModel(
                    data["accessKeyId"],
                    data["secretAccessKey"],
                    data["sessionToken"],
                    data["expiration"],
                )
            else:
                raise Exception(f"Failed to get credentials: {response.text}")

        return self._current_credentials
