# core/reply_shaping.py
"""Pack-driven reply cleaning with per-mode length budgets.

All persona cleaning (think-blocks, emoji, curly quotes, asterisk narration,
exclamation->period, filler phrases, word replacements) applies in EVERY mode —
the anti-roleplay defenses are deliberately decoupled from length. Only the
sentence cap + newline collapse vary by mode:

  casual    3 sentences (the historical behavior, byte-identical)
  emotional 6 sentences — room for presence, not padding
  task      uncapped, structure preserved (cloud drafts must survive intact)
"""
import re

MODE_SENTENCE_BUDGETS = {"casual": 3, "emotional": 6, "task": None}


def build_filler_cleaner(personality_pack):
    """Build a response cleaner from the personality pack's filler phrases."""
    filler_data = personality_pack.get("filler_phrases", [])
    word_replacements = {}

    if isinstance(filler_data, list):
        phrases = filler_data
    elif isinstance(filler_data, dict):
        phrases = filler_data
    else:
        phrases = []

    # If pack has structured filler data with word replacements
    if isinstance(personality_pack.get("filler_phrases"), list):
        phrases = personality_pack["filler_phrases"]
    else:
        # Load from pack — filler_phrases.json has {"phrases": [...], "word_replacements": {...}}
        pack_data = personality_pack.get("filler_phrases", [])
        if isinstance(pack_data, dict):
            phrases = pack_data.get("phrases", [])
            word_replacements = pack_data.get("word_replacements", {})
        else:
            phrases = pack_data if isinstance(pack_data, list) else []

    def clean_reply(text, mode="casual"):
        """Post-process agent response using pack-specific filters."""
        # Strip qwen3 thinking blocks (chain-of-thought reasoning)
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()

        # Strip emoji (qwen3 likes to add them, cp1252 console can't handle them)
        text = re.sub(r'[\U00010000-\U0010ffff]', '', text)

        # Normalize curly quotes
        text = text.replace("‘", "'").replace("’", "'")
        text = text.replace("“", '"').replace("”", '"')

        # Strip third-person narration (*smiles warmly*) — but NOT markdown
        # bold: **hi** contains the inner match *hi*, and stripping it turned
        # correct answers into "**" (live 4B smoke, 2026-07-03). Lookarounds
        # restrict the match to single-asterisk pairs.
        text = re.sub(r'(?<!\*)\*[^*\n]+\*(?!\*)\s*', '', text)
        text = re.sub(r'^[a-z].*?[,\.]\s*"', '"', text)
        text = text.strip('"')

        # Replace exclamation marks with periods
        text = text.replace("!", ".")

        # Strip filler phrases
        for phrase in phrases:
            base = phrase.rstrip(".,")
            pattern = re.escape(base) + r'\b[.,]?\s*'
            text = re.sub(pattern, "", text, flags=re.IGNORECASE)

        # Replace words that leak chatbot tone
        for word, replacement in word_replacements.items():
            text = re.sub(rf'\b{word}\b', replacement, text, flags=re.IGNORECASE)

        # Clean up whitespace
        text = re.sub(r'  +', ' ', text)
        text = re.sub(r'\n ', '\n', text)
        text = text.strip()
        text = re.sub(r'^\.\s*', '', text)
        text = re.sub(r'\s+([.?,])', r'\1', text)    # no space before . ? ,
        text = re.sub(r'\.(?:\s*\.)+', '.', text)     # collapse ".." / "..." / ". ." -> "."
        text = text.strip()

        # Mode-aware length budget. Models ignore "keep it short" instructions,
        # so the cap is enforced here; task mode is uncapped so escalated
        # drafts survive intact.
        budget = MODE_SENTENCE_BUDGETS.get(mode, 3)
        if budget is not None:
            has_list = bool(re.search(r'(?m)^[\s]*(?:\d+\.|[-*])\s', text))
            if not has_list:
                text = re.sub(r'\s*\n\s*', ' ', text)
                sentences = re.split(r'(?<=[.?])\s+', text)
                sentences = [s for s in sentences if s.strip()]
                if len(sentences) > budget:
                    sentences = sentences[:budget]
                while sentences and (
                    len(sentences[-1].split()) <= 2
                    and not sentences[-1].rstrip('.').endswith(('?', '.'))
                ):
                    sentences.pop()
                if sentences:
                    text = ' '.join(sentences)
                    if not text.endswith(('.', '?')):
                        text += '.'

        # Final punctuation normalize (the join above can re-introduce " ." / "..")
        text = re.sub(r'\s+([.?,])', r'\1', text)
        text = re.sub(r'\.(?:\s*\.)+', '.', text)
        return text.strip()

    return clean_reply
