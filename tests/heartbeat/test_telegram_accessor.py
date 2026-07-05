import integrations.telegram_bot as tb


def test_get_application_none_before_start():
    tb._set_application(None)
    assert tb.get_application() is None


def test_get_application_after_set():
    sentinel = object()
    tb._set_application(sentinel)
    assert tb.get_application() is sentinel
    tb._set_application(None)          # cleanup
