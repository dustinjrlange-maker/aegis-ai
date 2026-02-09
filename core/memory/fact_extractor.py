"""
Companion Intelligence — Aegis AI
Extracts facts about the human companion from conversations.
"""

import ollama
from core.config import CONFIG


EXTRACTION_PROMPT = """You are an AI companion's memory system analyzing a conversation to extract factual information about the human companion.

Extract ONLY concrete facts stated or strongly implied by the companion. Do NOT invent or assume anything.

Categories to look for:
- IDENTITY: name, age, location, nationality
- OCCUPATION: job title, employer, projects, work details
- RELATIONSHIPS: family, friends, roommates, coworkers mentioned by name
- PREFERENCES: food, hobbies, interests, dislikes
- LIFE EVENTS: moves, job changes, milestones, struggles
- GOALS: ambitions, plans, things they want to do
- EMOTIONAL STATE: how they're feeling (only if clearly expressed)

Format each fact as a single line:
CATEGORY: fact

Only output facts. If no new facts are found, output: NO NEW FACTS

CONVERSATION:
{conversation}"""


def extract_facts(messages):
    """Extract facts about the companion from a conversation."""
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

    response = ollama.chat(
        model=CONFIG["model"]["fact_extraction"],
        messages=[{"role": "user", "content": prompt}]
    )

    raw_output = response["message"]["content"].strip()

    if "NO NEW FACTS" in raw_output:
        return []

    # Parse facts into structured list
    facts = []
    for line in raw_output.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            category, fact = line.split(":", 1)
            category = category.strip().upper()
            fact = fact.strip()
            if fact:
                facts.append({"category": category, "fact": fact})

    return facts
