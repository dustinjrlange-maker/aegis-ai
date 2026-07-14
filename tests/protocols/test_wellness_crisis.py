from core.protocols.wellness import WellnessProtocol


def test_crisis_injects_strong_directive():
    p = WellnessProtocol()
    res = p.process_input("maybe everyone would be better off without me", {})
    inj = res["context_injection"].lower()
    assert "acknowledge" in inj or "take" in inj  # steers toward addressing it
    assert "support" in inj or "reach out" in inj  # surfaces real support
    assert p._last_crisis is True


def test_non_crisis_does_not_set_crisis_flag():
    p = WellnessProtocol()
    p.process_input("I skipped lunch again today", {})
    assert p._last_crisis is False
