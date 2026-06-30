import pytest

from api_client import _validate_api_base


def test_localhost_http_ok():
    _validate_api_base("http://localhost:8000")
    _validate_api_base("http://127.0.0.1:8000")


def test_https_ok():
    _validate_api_base("https://api.conclave.example")


def test_remote_http_raises():
    with pytest.raises(RuntimeError):
        _validate_api_base("http://192.168.1.50:8000")


def test_remote_http_hostname_raises():
    with pytest.raises(RuntimeError):
        _validate_api_base("http://conclave.example:8000")
