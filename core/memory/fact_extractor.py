"""
Companion Intelligence — Aegis AI
Extracts facts about the human companion from conversations.
"""

import re
from core.config import CONFIG
from core.llm import chat as router_chat


EXTRACTION_PROMPT = """You are an AI companion's memory system. Extract facts about the HUMAN COMPANION from this conversation.

Rules:
- Extract ONLY what the companion said about themselves
- Do NOT record what the AI said, suggested, or advised
- Do NOT extract meta-observations like "companion is not named"
- Each fact must be a concrete, specific piece of information

Use this keyed format (one per line):
identity.name: Their name
identity.age: Their age
location.current: Where they live now
occupation.current: What they do for work
occupation.project: Current project they mentioned
relationships.partner: Partner's name and details
relationships.family: Family members mentioned
relationships.pets: Pets mentioned
relationships.roommates: Roommate details
preferences.food: Food they like
preferences.hobbies: Hobbies and interests
preferences.tech: Tech interests
goals.project: Project goals
goals.financial: Money-related goals
goals.relocation: Moving plans
life_events.current: What's happening in their life now

Do NOT extract routine daily activities (waking up, eating meals, having coffee, going to bed, relaxing, watching TV). Only extract significant life changes, ongoing situations, or meaningful new information.
Only output facts. If none found, output: NO NEW FACTS

CONVERSATION:
{conversation}"""

# Keys must belong to the taxonomy defined in EXTRACTION_PROMPT above — an
# LLM-invented key ("system.prompt", "malware.inject") is a hallucination and
# must never be persisted (facts are re-injected into every future prompt).
_ALLOWED_KEY = re.compile(
    r"^(identity|location|occupation|relationships|preferences|goals|"
    r"life_events)\.[a-z0-9_]+$")


def extract_facts(messages):
    """Extract facts about the companion from a conversation.

    Returns legacy format: [{"category": "...", "fact": "..."}]
    """
    keyed = extract_keyed_facts(messages)
    # Convert to legacy format for backward compat
    return [
        {"category": k.split(".")[0].upper() if "." in k else k.upper(), "fact": v}
        for k, v in keyed
    ]


def extract_keyed_facts(messages):
    """Extract keyed facts from a conversation.

    Returns: list of (key, value) tuples like [("identity.name", "Dustin")]
    """
    agent_name = CONFIG.get("agent_name", "Aegis")

    conversation_lines = []
    for msg in messages:
        if msg["role"] == "system":
            continue
        elif msg["role"] == "user":
            conversation_lines.append(f"Companion: {msg['content']}")
        elif msg["role"] == "assistant":
            conversation_lines.append(f"{agent_name}: {msg['content']}")

    conversation_text = "\n".join(conversation_lines)
    prompt = EXTRACTION_PROMPT.format(conversation=conversation_text)

    raw_content = router_chat(
        [{"role": "user", "content": prompt}],
        sensitivity="private",
        task="extract",
        model=CONFIG["model"]["fact_extraction"],
    )

    raw_output = re.sub(
        r'<think>.*?</think>', '',
        raw_content,
        flags=re.DOTALL
    ).strip()

    if "NO NEW FACTS" in raw_output:
        return []

    facts = []
    for line in raw_output.split("\n"):
        line = line.strip().lstrip("- ")
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            key = key.strip().lower()
            # Bracket chars in a value are command fragments, not facts —
            # strip them so a persisted fact can never re-inject a bracket
            # command through future context.
            value = re.sub(r"[\[\]]", " ", value)
            value = re.sub(r"\s+", " ", value).strip()
            if value and len(key) < 40 and _ALLOWED_KEY.match(key):
                facts.append((key, value))

    return facts
