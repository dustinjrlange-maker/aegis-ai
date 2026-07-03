"""Phase 4B tool-autocall tests (Task 1: catalog hints + config helper)."""


def test_catalog_entries_have_method_hints():
    from core.tooling import catalog
    fs = catalog.get_entry("filesystem")
    assert fs["method_hints"]["list_directory"] == "path=<dir>"
    assert "content=" in fs["method_hints"]["write_file"]
    t = catalog.get_entry("time")
    assert "timezone=" in t["method_hints"]["get_current_time"]


def test_autocall_enabled_default_true(monkeypatch):
    import core.config
    from core.protocols import tooling
    monkeypatch.setattr(core.config, "CONFIG", {})
    assert tooling._autocall_enabled() is True


def test_autocall_enabled_reads_config(monkeypatch):
    import core.config
    from core.protocols import tooling
    monkeypatch.setattr(core.config, "CONFIG", {"tooling": {"autocall_enabled": False}})
    assert tooling._autocall_enabled() is False


def _install(monkeypatch, tool_ids):
    """Point registry.installed_ids at a fixed list for the tooling protocol."""
    from core.tooling import registry
    monkeypatch.setattr(registry, "installed_ids", lambda u: list(tool_ids))


def test_injection_lists_installed_tool_methods(monkeypatch):
    import core.config
    from core.protocols import tooling
    monkeypatch.setattr(core.config, "CONFIG", {"tooling": {"autocall_enabled": True}})
    _install(monkeypatch, ["filesystem"])
    p = tooling.ToolingProtocol(username="switch")
    out = p.process_input("hi", {})
    inj = out["context_injection"]
    assert "[TOOL:" in inj
    assert "filesystem.list_directory path=<dir>" in inj
    assert out["intercept"] is False


def test_injection_empty_when_toggle_off(monkeypatch):
    import core.config
    from core.protocols import tooling
    monkeypatch.setattr(core.config, "CONFIG", {"tooling": {"autocall_enabled": False}})
    _install(monkeypatch, ["filesystem"])
    p = tooling.ToolingProtocol(username="switch")
    assert p.process_input("hi", {})["context_injection"] == ""


def test_injection_empty_when_no_tools(monkeypatch):
    import core.config
    from core.protocols import tooling
    monkeypatch.setattr(core.config, "CONFIG", {"tooling": {"autocall_enabled": True}})
    _install(monkeypatch, [])
    p = tooling.ToolingProtocol(username="switch")
    assert p.process_input("hi", {})["context_injection"] == ""


def _reg_installed(monkeypatch, installed_ids):
    from core.tooling import registry
    monkeypatch.setattr(registry, "get",
                        lambda u, t: {"trust_tier": "read_broad"} if t in installed_ids else None)


def test_parse_stashes_structured_call_and_strips(monkeypatch):
    import core.config
    from core.protocols import tooling
    monkeypatch.setattr(core.config, "CONFIG", {"tooling": {"autocall_enabled": True}})
    _reg_installed(monkeypatch, ["filesystem"])
    p = tooling.ToolingProtocol(username="switch")
    out = p.process_output("Let me check. [TOOL: filesystem.list_directory path=C:/x]", {})
    assert "[TOOL:" not in out["response"]
    calls = p.get_pending_tool_calls()
    assert calls == [{"tool_id": "filesystem", "method": "list_directory",
                      "args": {"path": "C:/x"}}]
    assert p.get_rejections() == []


def test_parse_rejects_uninstalled_tool(monkeypatch):
    import core.config
    from core.protocols import tooling
    monkeypatch.setattr(core.config, "CONFIG", {"tooling": {"autocall_enabled": True}})
    _reg_installed(monkeypatch, [])                      # nothing installed
    p = tooling.ToolingProtocol(username="switch")
    p.process_output("[TOOL: filesystem.read_file path=x]", {})
    assert p.get_pending_tool_calls() == []
    assert p.get_rejections() == ["filesystem.read_file"]


def test_parse_rejects_unknown_method(monkeypatch):
    import core.config
    from core.protocols import tooling
    monkeypatch.setattr(core.config, "CONFIG", {"tooling": {"autocall_enabled": True}})
    _reg_installed(monkeypatch, ["filesystem"])
    p = tooling.ToolingProtocol(username="switch")
    p.process_output("[TOOL: filesystem.teleport path=x]", {})
    assert p.get_pending_tool_calls() == []
    assert p.get_rejections() == ["filesystem.teleport"]


def test_parse_ignores_non_tool_brackets(monkeypatch):
    import core.config
    from core.protocols import tooling
    monkeypatch.setattr(core.config, "CONFIG", {"tooling": {"autocall_enabled": True}})
    _reg_installed(monkeypatch, ["filesystem"])
    p = tooling.ToolingProtocol(username="switch")
    out = p.process_output("Sure. [REMEMBER: milk]", {})
    assert out["response"] == "Sure. [REMEMBER: milk]"   # untouched
    assert p.get_pending_tool_calls() == []


def test_parse_noop_when_toggle_off(monkeypatch):
    import core.config
    from core.protocols import tooling
    monkeypatch.setattr(core.config, "CONFIG", {"tooling": {"autocall_enabled": False}})
    _reg_installed(monkeypatch, ["filesystem"])
    p = tooling.ToolingProtocol(username="switch")
    out = p.process_output("[TOOL: filesystem.list_directory path=x]", {})
    assert out["response"] == "[TOOL: filesystem.list_directory path=x]"   # left intact
    assert p.get_pending_tool_calls() == []


def test_parse_survives_corrupt_catalog(monkeypatch):
    """A broken catalog must not raise through process_output."""
    import core.config
    from core.protocols import tooling
    from core.tooling import catalog
    monkeypatch.setattr(core.config, "CONFIG", {"tooling": {"autocall_enabled": True}})
    _reg_installed(monkeypatch, ["filesystem"])
    monkeypatch.setattr(catalog, "get_entry", lambda t: (_ for _ in ()).throw(OSError("locked")))
    p = tooling.ToolingProtocol(username="switch")
    # must not raise; a call it can't validate is treated as a rejection
    out = p.process_output("[TOOL: filesystem.read_file path=x]", {})
    assert "[TOOL:" not in out["response"]
    assert p.get_pending_tool_calls() == []


def test_parse_rejects_hyphenated_unknown_tool(monkeypatch):
    import core.config
    from core.protocols import tooling
    monkeypatch.setattr(core.config, "CONFIG", {"tooling": {"autocall_enabled": True}})
    _reg_installed(monkeypatch, ["filesystem"])
    p = tooling.ToolingProtocol(username="switch")
    p.process_output("[TOOL: some-server.do_thing x=1]", {})   # matches regex now, not installed
    assert p.get_pending_tool_calls() == []
    assert p.get_rejections() == ["some-server.do_thing"]


import asyncio


class _FakeTooling:
    """Serves a list of pending calls per round; advance() loads the next round."""
    def __init__(self, rounds):
        self._rounds = rounds
        self._i = 0
        self._rej = []

    def get_pending_tool_calls(self):
        return self._rounds[self._i] if self._i < len(self._rounds) else []

    def get_rejections(self):
        return self._rej

    def advance(self):
        self._i += 1


def _run(**kw):
    from core.tooling import autocall
    base = dict(sensitivity="personal", task_tag="chat_task", model="qwen")
    base.update(kw)
    return asyncio.run(autocall.run_tool_loop(**base))


def test_loop_ok_reprompts_and_synthesizes():
    tooling = _FakeTooling([[{"tool_id": "filesystem", "method": "list_directory",
                              "args": {"path": "X"}}], []])
    calls, routed = [], []

    def call_tool(u, t, m, a):
        calls.append((t, m, a)); return {"status": "ok", "result": ["a.txt", "b.txt"]}

    def router(convo, s, t, model):
        routed.append(convo); return ("You have a.txt and b.txt.", "META2")

    def process_output(reply):
        tooling.advance(); return {"response": reply, "suppress": False}

    final, meta, pin = _run(
        username="switch", tooling=tooling, convo=[{"role": "user", "content": "ls"}],
        reply="checking", raw_reply="checking [TOOL: x]", route_meta="META1",
        router=router, call_tool=call_tool, process_output=process_output,
        clean_reply=lambda x: x)
    assert calls == [("filesystem", "list_directory", {"path": "X"})]
    assert final == "You have a.txt and b.txt."
    assert meta == "META2" and pin == []
    assert len(routed) == 1
    # the re-prompt convo carries Pike's own call + the tool result
    assert any(m["role"] == "assistant" and "[TOOL: x]" in m["content"] for m in routed[0])
    assert any(m["role"] == "user" and "a.txt" in m["content"] for m in routed[0])


def test_loop_needs_pin_appends_note_no_reprompt():
    tooling = _FakeTooling([[{"tool_id": "filesystem", "method": "write_file",
                              "args": {}}], []])
    routed = []

    def call_tool(u, t, m, a):
        return {"status": "needs_pin", "tool_id": "filesystem",
                "method": "write_file", "required_tier": "write_destructive",
                "message": "..."}

    def router(convo, s, t, model):
        routed.append(convo); return ("x", "M")

    final, meta, pin = _run(
        username="switch", tooling=tooling, convo=[{"role": "user", "content": "w"}],
        reply="on it", raw_reply="on it", route_meta="M0",
        router=router, call_tool=call_tool,
        process_output=lambda r: {"response": r, "suppress": False}, clean_reply=lambda x: x)
    assert "needs your PIN" in final and "write_file" in final
    assert routed == []                # only needs_pin -> no re-prompt
    assert meta == "M0" and len(pin) == 1


def test_loop_error_is_fed_back():
    tooling = _FakeTooling([[{"tool_id": "time", "method": "get_current_time",
                              "args": {}}], []])
    routed = []

    def call_tool(u, t, m, a):
        return {"status": "error", "message": "boom"}

    def router(convo, s, t, model):
        routed.append(convo); return ("sorry, that failed", "M2")

    def process_output(reply):
        tooling.advance(); return {"response": reply, "suppress": False}

    final, meta, pin = _run(
        username="switch", tooling=tooling, convo=[{"role": "user", "content": "t"}],
        reply="checking", raw_reply="checking", route_meta="M0",
        router=router, call_tool=call_tool, process_output=process_output,
        clean_reply=lambda x: x)
    assert len(routed) == 1
    assert any(m["role"] == "user" and "failed: boom" in m["content"] for m in routed[0])
    assert final == "sorry, that failed"


def test_loop_round_cap_stops_at_three():
    always = [{"tool_id": "time", "method": "get_current_time", "args": {}}]

    class Always:
        def get_pending_tool_calls(self): return always
        def get_rejections(self): return []

    n = {"c": 0}

    def call_tool(u, t, m, a):
        n["c"] += 1; return {"status": "ok", "result": ["t"]}

    def router(convo, s, t, model):
        return ("still going", "M")

    final, meta, pin = _run(
        username="switch", tooling=Always(), convo=[{"role": "user", "content": "x"}],
        reply="r", raw_reply="r", route_meta="M0",
        router=router, call_tool=call_tool,
        process_output=lambda r: {"response": r, "suppress": False},
        clean_reply=lambda x: x)
    assert n["c"] == 3                  # exactly max_rounds executions


def test_loop_exception_falls_back_to_preloop_reply():
    class Always:
        def get_pending_tool_calls(self):
            return [{"tool_id": "time", "method": "get_current_time", "args": {}}]
        def get_rejections(self): return []

    def call_tool(u, t, m, a):
        raise RuntimeError("kaboom")

    def router(convo, s, t, model):
        return ("unused", "M")

    final, meta, pin = _run(
        username="switch", tooling=Always(), convo=[{"role": "user", "content": "x"}],
        reply="preloop reply", raw_reply="preloop", route_meta="M0",
        router=router, call_tool=call_tool,
        process_output=lambda r: {"response": r, "suppress": False},
        clean_reply=lambda x: x)
    assert final == "preloop reply"     # degraded gracefully, no raise
    assert meta == "M0"


def test_loop_pin_note_deduped_across_rounds():
    tooling = _FakeTooling([
        [{"tool_id": "time", "method": "get_current_time", "args": {}},
         {"tool_id": "filesystem", "method": "write_file", "args": {}}],
        [{"tool_id": "filesystem", "method": "write_file", "args": {}}],
    ])

    def call_tool(u, t, m, a):
        if m == "write_file":
            return {"status": "needs_pin", "tool_id": "filesystem",
                    "method": "write_file", "required_tier": "write_destructive",
                    "message": "..."}
        return {"status": "ok", "result": ["12:00"]}

    def router(convo, s, t, model):
        return ("done", "M")

    def process_output(reply):
        tooling.advance(); return {"response": reply, "suppress": False}

    final, meta, pin = _run(
        username="switch", tooling=tooling, convo=[{"role": "user", "content": "x"}],
        reply="r", raw_reply="r", route_meta="M0",
        router=router, call_tool=call_tool, process_output=process_output,
        clean_reply=lambda x: x)
    assert final.count("needs your PIN") == 1      # note appears once despite 2 rounds
    assert len(pin) == 1


def test_format_tool_result_caps_large_listing():
    from core.tooling import autocall
    big = "\n".join(f"[FILE] f{i}.txt" for i in range(300))   # 300 lines in one string
    out = autocall._format_tool_result([big])
    assert out.count("\n") <= autocall.MAX_RESULT_LINES        # capped by lines
    assert "truncated" in out
    assert len(out) <= autocall.MAX_RESULT_CHARS + 100         # char cap (+marker)


def test_format_tool_result_small_unchanged():
    from core.tooling import autocall
    assert autocall._format_tool_result(["hi"]) == "hi"
    assert autocall._format_tool_result([]) == "(empty)"


def test_loop_reprompt_has_answer_framing():
    tooling = _FakeTooling([[{"tool_id": "time", "method": "get_current_time",
                              "args": {}}], []])
    routed = []

    def call_tool(u, t, m, a):
        return {"status": "ok", "result": ["3:44 PM"]}

    def router(convo, s, t, model):
        routed.append(convo); return ("It's 3:44 PM.", "M")

    def process_output(reply):
        tooling.advance(); return {"response": reply, "suppress": False}

    final, meta, pin = _run(
        username="switch", tooling=tooling, convo=[{"role": "user", "content": "time?"}],
        reply="checking", raw_reply="checking", route_meta="M0",
        router=router, call_tool=call_tool, process_output=process_output,
        clean_reply=lambda x: x)
    resultmsg = [m for m in routed[0] if m["role"] == "user"
                 and "[Tool results" in m["content"]][0]["content"]
    assert "answer my original question now" in resultmsg
    assert "Do NOT call the same tool again" in resultmsg
    assert "3:44 PM" in resultmsg                                 # result still present


def test_injection_includes_approved_dirs(monkeypatch):
    """Filesystem's approved dirs are surfaced so Pike uses real paths."""
    import core.config
    from core.protocols import tooling
    from core.tooling import registry
    monkeypatch.setattr(core.config, "CONFIG", {"tooling": {"autocall_enabled": True}})
    monkeypatch.setattr(registry, "installed_ids", lambda u: ["filesystem"])
    monkeypatch.setattr(registry, "get", lambda u, t: {
        "config": {"approved_dirs": ["C:/Users/dusti/Documents"]}} if t == "filesystem" else None)
    p = tooling.ToolingProtocol(username="switch")
    inj = p.process_input("hi", {})["context_injection"]
    assert "use absolute paths under: C:/Users/dusti/Documents" in inj


def test_parse_shorthand_without_tool_prefix(monkeypatch):
    """qwen drops the TOOL: prefix under format drift — accept validated shorthand."""
    import core.config
    from core.protocols import tooling
    monkeypatch.setattr(core.config, "CONFIG", {"tooling": {"autocall_enabled": True}})
    _reg_installed(monkeypatch, ["filesystem"])
    p = tooling.ToolingProtocol(username="switch")
    out = p.process_output("[filesystem.read_file path=C:/x/a.txt]", {})
    assert p.get_pending_tool_calls() == [{"tool_id": "filesystem", "method": "read_file",
                                           "args": {"path": "C:/x/a.txt"}}]
    assert "[filesystem" not in out["response"]        # stripped


def test_parse_shorthand_nonvalidating_left_untouched(monkeypatch):
    """Shorthand that isn't an installed tool+method is prose — leave it alone."""
    import core.config
    from core.protocols import tooling
    monkeypatch.setattr(core.config, "CONFIG", {"tooling": {"autocall_enabled": True}})
    _reg_installed(monkeypatch, ["filesystem"])
    p = tooling.ToolingProtocol(username="switch")
    out = p.process_output("See [example.com link] and [foo.bar x=1] for info.", {})
    assert out["response"] == "See [example.com link] and [foo.bar x=1] for info."
    assert p.get_pending_tool_calls() == []
    assert p.get_rejections() == []                    # no false rejections from prose


def test_parse_strict_and_shorthand_not_double_counted(monkeypatch):
    """A strict [TOOL: x.y] also matches the shorthand pattern — count once."""
    import core.config
    from core.protocols import tooling
    monkeypatch.setattr(core.config, "CONFIG", {"tooling": {"autocall_enabled": True}})
    _reg_installed(monkeypatch, ["filesystem"])
    p = tooling.ToolingProtocol(username="switch")
    p.process_output("[TOOL: filesystem.read_file path=x]", {})
    assert len(p.get_pending_tool_calls()) == 1
