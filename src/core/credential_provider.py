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
        endpoint: str,
        credentials_endpoint: str | None = None,
    ) -> None:
        from loguru import logger

        self._cert_path = cert_path
        self._key_path = key_path
        self._ca_path = ca_path
        self._role_alias = role_alias
        self._thing_name = thing_name
        self._current_credentials: CredentialsModel | None = None

        logger.info(f"Initializing CredentialProvider for thing: {self._thing_name}")

        if credentials_endpoint:
            self._credentials_endpoint = credentials_endpoint
            logger.info(
                f"Using configured IoT Credentials endpoint: {self._credentials_endpoint}"
            )
        elif ".iot." in endpoint:
            base_endpoint = endpoint.split(".iot.")[0]
            if base_endpoint.endswith("-ats"):
                base_endpoint = base_endpoint[:-4]

            self._credentials_endpoint = (
                f"{base_endpoint}.credentials.iot.{endpoint.split('.iot.')[1]}"
            )
            logger.info(
                f"Credential provider endpoint set to: {self._credentials_endpoint}"
            )
        else:
            try:
                client = boto3.client("iot", region_name="eu-north-1")
                response = client.describe_endpoint(
                    endpointType="iot:CredentialProvider"
                )
                self._credentials_endpoint = response["endpointAddress"]
                logger.info(
                    f"IoT Credentials endpoint from boto3: {self._credentials_endpoint}"
                )
            except Exception as e:
                logger.error(f"Failed to describe IoT endpoint: {e}")
                raise

    def get_credentials(self) -> CredentialsModel:
        from loguru import logger

        logger.debug("get_credentials called")
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
                    access_key_id=data["accessKeyId"],
                    secret_access_key=data["secretAccessKey"],
                    session_token=data["sessionToken"],
                    expiration=data["expiration"],
                )
            else:
                raise Exception(f"Failed to get credentials: {response.text}")

        if self._current_credentials is None:
            raise Exception("Failed to obtain credentials: unknown error")

        return self._current_credentials
