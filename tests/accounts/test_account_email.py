# tests/accounts/test_account_email.py
from core.protocols import google_tools as gt


class _FakeService:
    def __init__(self, email=None, raises=False):
        self._email = email
        self._raises = raises

    def users(self):
        return self

    def getProfile(self, userId=None):
        return self

    def execute(self):
        if self._raises:
            raise RuntimeError("boom")
        return {"emailAddress": self._email}


def test_get_account_email_ok(monkeypatch):
    monkeypatch.setattr(gt, "_get_gmail_service",
                        lambda creds: _FakeService(email="x@y.com"))
    assert gt.get_account_email(object()) == "x@y.com"


def test_get_account_email_no_service(monkeypatch):
    monkeypatch.setattr(gt, "_get_gmail_service", lambda creds: None)
    assert gt.get_account_email(object()) == ""


def test_get_account_email_swallows_errors(monkeypatch):
    monkeypatch.setattr(gt, "_get_gmail_service",
                        lambda creds: _FakeService(raises=True))
    assert gt.get_account_email(object()) == ""


def test_build_auth_url_default_prompt_is_consent():
    import inspect
    sig = inspect.signature(gt.build_auth_url)
    assert sig.parameters["prompt"].default == "consent"
