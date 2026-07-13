from core.protocols.wellness import WellnessProtocol


def test_self_defeat_injects_firmness():
    p = WellnessProtocol()
    res = p.process_input("I'm useless at this, I should just quit everything", {})
    inj = res["context_injection"].lower()
    # steers toward honest pushback, not agreement
    assert "don't simply agree" in inj or "honest" in inj or "gently challenge" in inj
    assert p._last_crisis is False  # self-defeat is not crisis
