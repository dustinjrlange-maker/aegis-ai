"""An unclassified method on a write-capable tool must fail CLOSED — require a
PIN — instead of running at the tool's (read-level) default_tier (2026-07-09
audit D2). Guards a future MCP method that ships with a hint but no explicit
tier from silently running unprivileged."""
from core.tooling import catalog, trust


def _fs():
    return catalog.get_entry("filesystem")


def _time():
    return catalog.get_entry("time")


def test_known_reads_are_explicitly_tiered_and_allowed():
    """The everyday reads must stay allowed at read tier (they're now explicitly
    classified, not relying on default_tier)."""
    fs = _fs()
    assert trust.check("read_broad", fs, "list_directory") == "allow"
    assert trust.check("read_broad", fs, "read_file") == "allow"


def test_known_writes_still_need_pin():
    fs = _fs()
    assert trust.check("read_broad", fs, "write_file") == "needs_pin"


def test_unclassified_method_on_write_capable_tool_fails_closed():
    """A method not in method_tiers on a tool that HAS write methods is treated
    as maximally dangerous — needs_pin — not run at default_tier."""
    fs = _fs()
    assert trust.required_tier(fs, "delete_everything") == "write_destructive"
    assert trust.check("read_broad", fs, "delete_everything") == "needs_pin"
    # even a broad-read install can't run it without a PIN
    assert trust.check("write_scoped_undoable", fs, "delete_everything") == "needs_pin"


def test_read_only_tool_keeps_default_tier_for_unlisted():
    """A tool with NO write methods (e.g. time) isn't forced to PIN for an
    unlisted method — it falls back to its (read) default_tier."""
    t = _time()
    assert trust.check("read_scoped", t, "get_current_time") == "allow"
    # an unlisted method on a read-only tool uses the read default, not PIN
    assert trust.required_tier(t, "some_future_read") == t.get("default_tier")
