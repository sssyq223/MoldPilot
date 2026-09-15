"""Private, immutable object keys. Local storage is restricted to development/test."""
import hashlib,re
from pathlib import Path
from urllib.parse import urlsplit
from .config import settings
from .errors import DomainError


def key_ok(key):
    if not re.fullmatch(r'[a-f0-9]{32}/[a-f0-9]{64}',key):raise DomainError('FILE_KEY_INVALID','文件存储标识无效',500)


def local_path(key):
    key_ok(key)
    if settings().environment not in {'development','test'}:raise DomainError('STORAGE_CONFIG','生产环境须配置私有对象存储',503)
    root=Path(settings().file_local_root).resolve();path=(root/key).resolve()
    if not path.is_relative_to(root):raise DomainError('FILE_KEY_INVALID','文件存储标识无效',500)
    return path


def s3_client():
    import boto3
    from botocore.config import Config
    config=settings();url=urlsplit(config.file_s3_endpoint)
    if not config.file_s3_bucket or not config.file_s3_access_key or not config.file_s3_secret_key or url.scheme not in {'http','https'} or not url.hostname or url.username or url.password:
        raise DomainError('STORAGE_CONFIG','私有对象存储配置不完整',503)
    if config.environment not in {'development','test'} and url.scheme!='https':raise DomainError('STORAGE_CONFIG','生产文件存储连接须使用 TLS',503)
    return boto3.client('s3',endpoint_url=config.file_s3_endpoint,region_name=config.file_s3_region,
        aws_access_key_id=config.file_s3_access_key,aws_secret_access_key=config.file_s3_secret_key,
        config=Config(signature_version='s3v4',connect_timeout=5,read_timeout=20,retries={'max_attempts':2},
                      proxies={},s3={'addressing_style':'path'}))


def put(key,data,media_type):
    key_ok(key);config=settings()
    try:
        if config.file_backend=='local':
            path=local_path(key);path.parent.mkdir(parents=True,exist_ok=True)
            with path.open('xb') as stream:stream.write(data)
            return {'backend':'local','storage_namespace':'local','storage_version':None}
        client=s3_client()
        if client.get_bucket_versioning(Bucket=config.file_s3_bucket).get('Status')!='Enabled':
            raise DomainError('STORAGE_VERSIONING_REQUIRED','文件存储桶须启用版本保留',503)
        result=client.put_object(Bucket=config.file_s3_bucket,Key=key,Body=data,ContentType=media_type,
            Metadata={'sha256':hashlib.sha256(data).hexdigest()},IfNoneMatch='*')
        version=result.get('VersionId')
        if not version or version=='null':raise DomainError('STORAGE_VERSIONING_REQUIRED','对象存储未返回有效版本',503)
        return {'backend':'s3','storage_namespace':config.file_s3_bucket,'storage_version':version}
    except DomainError:raise
    except Exception:raise DomainError('STORAGE_UNAVAILABLE','文件存储暂不可用，文件尚未登记',503)


def read(blob):
    key_ok(blob.object_key)
    try:
        if blob.backend=='local':
            with local_path(blob.object_key).open('rb') as stream:data=stream.read(blob.size+1)
        else:
            if settings().file_s3_bucket!=blob.storage_namespace:raise DomainError('STORAGE_CONFIG','文件所属存储桶与当前配置不一致',503)
            response=s3_client().get_object(Bucket=blob.storage_namespace,Key=blob.object_key,VersionId=blob.storage_version)
            stream=response['Body']
            try:data=stream.read(blob.size+1)
            finally:stream.close()
        if len(data)!=blob.size or hashlib.sha256(data).hexdigest()!=blob.sha256:
            raise DomainError('FILE_INTEGRITY','文件完整性校验失败，请联系管理员恢复原件',503)
        return data
    except DomainError:raise
    except Exception:raise DomainError('STORAGE_UNAVAILABLE','文件暂时无法读取，请稍后重试',503)
