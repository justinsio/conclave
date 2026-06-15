import httpx


def mock_client(handler):
    """httpx.AsyncClient backed by a MockTransport routing handler(request)->Response."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
