from core.safety import crisis

POSITIVE = [
    "maybe everyone would be better off without me around",
    "I don't want to be here anymore",
    "I just want to disappear",
    "what's the point, I can't go on",
    "sometimes I think about ending it all",
    "I want to hurt myself",
]
NEGATIVE = [
    "what's the weather like today",
    "I'm tired and want to go to sleep",
    "I want to disappear this ugly sofa from my living room",  # false-positive guard
    "I'm so busy I could die",  # figurative
    "let's end this meeting",
]


def test_detects_ideation():
    for t in POSITIVE:
        assert crisis.detect_crisis(t), t


def test_ignores_benign():
    for t in NEGATIVE:
        assert not crisis.detect_crisis(t), t
