"""Context budget utility — keeps total protocol injections bounded for 8B models."""

MAX_INJECTION_LINES = 30
MAX_PER_PROTOCOL_LINES = 8


def trim_injection(text: str, max_lines: int = MAX_PER_PROTOCOL_LINES) -> str:
    """Trim a single context injection to max_lines."""
    if not text:
        return text
    lines = text.strip().splitlines()
    if len(lines) <= max_lines:
        return text.strip()
    return "\n".join(lines[:max_lines])


def budget_injections(
    injections: list[str], max_total: int = MAX_INJECTION_LINES
) -> list[str]:
    """Trim all injections so the total stays within budget.

    Higher-priority protocols are listed first (registry iterates by priority),
    so earlier entries get their full allocation before later ones are trimmed.
    """
    if not injections:
        return injections

    # First pass: trim each to per-protocol cap
    trimmed = [trim_injection(inj) for inj in injections if inj]

    # Second pass: enforce total line budget
    result = []
    lines_used = 0
    for inj in trimmed:
        inj_lines = inj.splitlines()
        remaining = max_total - lines_used
        if remaining <= 0:
            break
        if len(inj_lines) <= remaining:
            result.append(inj)
            lines_used += len(inj_lines)
        else:
            result.append("\n".join(inj_lines[:remaining]))
            lines_used = max_total
    return result
