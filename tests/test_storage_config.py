import pytest
from pydantic import ValidationError

from app.config import Settings


def test_non_development_storage_cannot_use_local_files():
    with pytest.raises(ValidationError, match="AGENT_FILE_BACKEND=s3"):
        Settings(_env_file=None, environment="production", file_backend="local")


def test_s3_storage_requires_private_endpoint_and_credentials():
    with pytest.raises(ValidationError, match="AGENT_FILE_S3_SECRET_KEY"):
        Settings(
            _env_file=None,
            environment="production",
            file_backend="s3",
            file_s3_endpoint="https://s3.example.com",
            file_s3_bucket="moldpilot-prod-files",
            file_s3_access_key="access",
        )


def test_production_s3_storage_requires_https():
    with pytest.raises(ValidationError, match="HTTPS"):
        Settings(
            _env_file=None,
            environment="production",
            file_backend="s3",
            file_s3_endpoint="http://s3.example.com",
            file_s3_bucket="moldpilot-prod-files",
            file_s3_access_key="access",
            file_s3_secret_key="secret",
        )
