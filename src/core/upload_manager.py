import io
import boto3
from src.core.credential_provider import CredentialProvider


class UploadManager:
    def __init__(self, credential_provider: CredentialProvider):
        self._provider = credential_provider
        self._credentials = None
        self._s3_client = None

    def _get_client(self):
        current_creds = self._provider.get_credentials()

        if self._s3_client is None or self._credentials != current_creds:
            self._credentials = current_creds

            session = boto3.Session(
                aws_access_key_id=current_creds.access_key_id,
                aws_secret_access_key=current_creds.secret_access_key,
                aws_session_token=current_creds.session_token,
            )
            self._s3_client = session.client("s3")

        return self._s3_client

    def upload_bytes(self, data: io.BytesIO, bucket: str, s3_key: str):
        client = self._get_client()
        data.seek(0)
        client.upload_fileobj(
            data, bucket, s3_key, ExtraArgs={"ContentType": "image/jpeg"}
        )
