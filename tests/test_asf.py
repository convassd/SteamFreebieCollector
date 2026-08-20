import requests
import pytest

from steam_freebie_collector.asf import (
    AsfAuthenticationError,
    AsfClient,
    AsfHealthError,
    SubmissionOutcome,
)


class FakeResponse:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text
        self.ok = 200 <= status < 400

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, *, get_response=None, post_response=None, post_error=None):
        self.get_response = get_response
        self.post_response = post_response
        self.post_error = post_error
        self.posts = []
        self.gets = []

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        if isinstance(self.get_response, Exception):
            raise self.get_response
        return self.get_response

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        if self.post_error:
            raise self.post_error
        return self.post_response


def test_health_uses_read_only_endpoint_and_authentication_header():
    session = FakeSession(get_response=FakeResponse(payload={"Success": True}))
    client = AsfClient("http://localhost:1242/", ipc_password="secret", session=session)
    client.wait_until_healthy(0, 0.1)
    assert session.gets[0][0] == "http://localhost:1242/Api/ASF"
    assert session.gets[0][1]["headers"]["Authentication"] == "secret"


def test_health_fails_immediately_on_auth_error():
    session = FakeSession(get_response=FakeResponse(status=401, payload={"Success": False}))
    client = AsfClient("http://localhost:1242", session=session)
    with pytest.raises(AsfAuthenticationError):
        client.wait_until_healthy(10, 1)


def test_health_timeout_is_read_only():
    session = FakeSession(get_response=requests.ConnectionError("offline"))
    client = AsfClient("http://localhost:1242", session=session)
    with pytest.raises(AsfHealthError):
        client.wait_until_healthy(0, 1)
    assert not session.posts


def test_submit_posts_exact_contract():
    session = FakeSession(post_response=FakeResponse(payload={"Success": True, "Message": "OK", "Result": "done"}))
    client = AsfClient("http://localhost:1242", session=session)
    result = client.submit("!ALA s/1706211")
    assert result.outcome is SubmissionOutcome.SUBMITTED
    url, kwargs = session.posts[0]
    assert url == "http://localhost:1242/Api/Command"
    assert kwargs["json"] == {"Command": "!ALA s/1706211"}


def test_submit_rejects_noncanonical_command_before_network():
    session = FakeSession(post_response=FakeResponse(payload={"Success": True}))
    client = AsfClient("http://localhost:1242", session=session)
    with pytest.raises(ValueError):
        client.submit("!EXIT")
    assert not session.posts


def test_lifecycle_exit_uses_fixed_literal_command():
    session = FakeSession(post_response=FakeResponse(payload={"Success": True}))
    client = AsfClient("http://localhost:1242", session=session)
    client.request_exit()
    assert session.posts[0][1]["json"] == {"Command": "!exit"}


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (requests.ConnectTimeout("connect"), SubmissionOutcome.PRE_SEND_FAILURE),
        (requests.ReadTimeout("read"), SubmissionOutcome.UNKNOWN),
        (requests.ConnectionError("reset"), SubmissionOutcome.UNKNOWN),
    ],
)
def test_submit_classifies_transport_failures(error, outcome):
    client = AsfClient("http://localhost:1242", session=FakeSession(post_error=error))
    assert client.submit("!ALA a/1").outcome is outcome


def test_malformed_success_response_is_unknown():
    response = FakeResponse(payload=ValueError("bad json"), text="not-json")
    client = AsfClient("http://localhost:1242", session=FakeSession(post_response=response))
    assert client.submit("!ALA a/1").outcome is SubmissionOutcome.UNKNOWN
