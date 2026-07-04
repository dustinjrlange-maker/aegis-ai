from server.chat_pipeline import evaluate_escalation


class _Cfg:
    def __init__(self, esc, consent):
        self.cloud_trouble_escalation = esc
        self.trouble_private_consent = consent


def test_non_private_trouble_escalates():
    out = evaluate_escalation("no that's wrong", streak=0,
                              cfg=_Cfg(True, True), key_present=True)
    assert out.action == "escalate"
    assert out.new_streak == 1


def test_private_trouble_with_consent_prompts():
    out = evaluate_escalation("no, my bank account number is wrong", streak=0,
                              cfg=_Cfg(True, True), key_present=True)
    assert out.action == "consent"
    assert "financial" in out.reason


def test_private_trouble_without_consent_escalates():
    out = evaluate_escalation("no, my bank account is wrong", streak=0,
                              cfg=_Cfg(True, False), key_present=True)
    assert out.action == "escalate"


def test_no_key_stays_local():
    out = evaluate_escalation("no that's wrong", streak=0,
                              cfg=_Cfg(True, True), key_present=False)
    assert out.action == "local"


def test_feature_off_stays_local():
    out = evaluate_escalation("no that's wrong", streak=0,
                              cfg=_Cfg(False, True), key_present=True)
    assert out.action == "local"
