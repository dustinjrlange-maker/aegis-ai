from core.protocols.wellness import WellnessProtocol


def test_consumed_flag_reports_and_resets():
    p = WellnessProtocol()
    p.process_input("I haven't slept in two days", {})
    assert p.consumed_flag() is True
    assert p.consumed_flag() is False  # reset after read


def test_consumed_crisis_reports_and_resets():
    p = WellnessProtocol()
    p.process_input("I don't want to be here anymore", {})
    assert p.consumed_crisis() is True
    assert p.consumed_crisis() is False


def test_neutral_message_sets_no_flags():
    p = WellnessProtocol()
    p.process_input("what's a good recipe for pasta", {})
    assert p.consumed_flag() is False
    assert p.consumed_crisis() is False
