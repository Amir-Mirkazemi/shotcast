import boto3
from moto import mock_aws

from shotcast.storage import LocalStore, S3Store, get_store


def test_local_roundtrip_and_listing(tmp_path):
    s = LocalStore(tmp_path)
    assert not s.exists("raw/a.json")
    s.write_bytes("raw/a.json", b"{}")
    s.write_bytes("raw/sub/b.json", b"[]")
    assert s.exists("raw/a.json") and s.read_bytes("raw/a.json") == b"{}"
    assert s.list_keys("raw") == ["raw/a.json", "raw/sub/b.json"]
    assert not list(tmp_path.rglob("*.tmp")), "temp files must not be left behind"


def test_local_overwrite_is_clean(tmp_path):
    s = LocalStore(tmp_path)
    s.write_bytes("k", b"1")
    s.write_bytes("k", b"22")
    assert s.read_bytes("k") == b"22"


@mock_aws
def test_s3_store_roundtrip_with_prefix():
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="test-bucket")
    s = S3Store("test-bucket", "shotcast", client=boto3.client("s3", region_name="us-east-1"))
    assert not s.exists("raw/x.json")
    s.write_bytes("raw/x.json", b"hi")
    assert s.exists("raw/x.json") and s.read_bytes("raw/x.json") == b"hi"
    assert s.list_keys("raw") == ["raw/x.json"]
    assert s.uri("raw/x.json") == "s3://test-bucket/shotcast/raw/x.json"


def test_get_store_picks_backend(tmp_path, monkeypatch):
    assert isinstance(get_store(str(tmp_path)), LocalStore)
    monkeypatch.setenv("SHOTCAST_ROOT", str(tmp_path))
    assert isinstance(get_store(), LocalStore)
