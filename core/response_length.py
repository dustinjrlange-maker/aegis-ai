"""Detect when the user explicitly asks for a longer / more structured answer.

Pike's replies are hard-capped to a few sentences in casual mode
(`reply_shaping.MODE_SENTENCE_BUDGETS`). That keeps ordinary chat tight, but it
also truncates answers the user genuinely wants in full ("give me the detailed
itemized list"). This is the single, testable predicate the chat pipeline uses
to lift the cap for exactly those turns — by shaping them as `task` mode.

Deliberately precise: a bare "more" does NOT count (too broad — "one more
task", "more coffee"). Only explicit length/detail/structure requests do.
"""
import re

_DETAIL_CUE = re.compile(
    r"\b("
    r"detailed|in\s+detail|details|"
    r"itemi[sz]e[d]?|"
    r"in\s+full|full\s+(?:breakdown|list|detail|rundown|version)|"
    r"breakdown|break\s+(?:it|them|this)\s+down|"
    r"everything|"
    r"expand|elaborate|"
    r"more\s+detail|"
    r"list\s+(?:them|out|it)|"
    r"longer|long\s+version|"
    r"complete\s+(?:list|breakdown|rundown)"
    r")\b",
    re.IGNORECASE,
)


def wants_detailed_answer(text) -> bool:
    """True when *text* explicitly asks for a longer/structured answer."""
    if not text:
        return False
    return bool(_DETAIL_CUE.search(text))


def effective_shaping_mode(turn_mode: str, user_input) -> str:
    """The mode to shape the reply with, lifting the length cap only when the
    user explicitly asked for detail on an otherwise-casual turn.

    `emotional` is never lengthened (grief presence stays short); `task` is
    already uncapped. Only `casual` + an explicit detail request escalates.
    """
    if turn_mode == "casual" and wants_detailed_answer(user_input):
        return "task"
    return turn_mode
