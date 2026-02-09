"""
Wellness Protocol — Aegis AI
Monitors and supports the human companion's physical and mental health.
Health callouts override casual conversation. Firmness escalates, never softens.
"""

import re
from datetime import datetime
from core.protocols.base import Protocol


class WellnessProtocol(Protocol):
    """Health monitoring, motivation, accountability."""

    # Health-critical keywords and patterns
    HEALTH_TRIGGERS = {
        "sleep": {
            "negative": [
                r"sleep\s+is\s+(for\s+the\s+weak|overrated|optional)",
                r"(i('ll|'m going to)\s+)?sleep\s+when\s+i('m|\s+am)\s+dead",
                r"been\s+up\s+\d+\s+hours",
                r"(haven't|didnt|didn't|don't)\s+sleep",
                r"(skip|skipping|skipped)\s+(sleep|bed)",
                r"who\s+needs\s+sleep",
                r"sleep\s+(later|tomorrow)",
                r"all\s*nighter",
                r"no\s+sleep",
            ],
            "context": "sleep_deprivation",
        },
        "meals": {
            "negative": [
                r"(skip|skipping|skipped|forgot)\s+(meals?|breakfast|lunch|dinner|eating|food)",
                r"(haven't|didnt|didn't|don't)\s+(eat|eaten)",
                r"(too busy|no time)\s+to\s+eat",
                r"(don't|cant|can't)\s+afford\s+(to\s+eat|food|groceries)",
                r"just\s+(coffee|energy\s+drinks?|caffeine)",
                r"not\s+hungry",
            ],
            "context": "nutrition",
        },
        "medical": {
            "negative": [
                r"(don't|dont)\s+need\s+(a\s+)?doctor",
                r"doctors?\s+(are|is)\s+(overrated|useless|waste)",
                r"(tough|push)\s+(it\s+)?out",
                r"(i'll|ill|I will)\s+be\s+fine",
                r"it('s|\s+is)\s+(nothing|not\s+that\s+bad|fine)",
                r"(can't|cant)\s+afford\s+(a\s+)?doctor",
            ],
            "context": "medical_avoidance",
        },
        "substance": {
            "negative": [
                r"(on\s+my|another)\s+\d+\w*\s+(cup|coffee|energy\s+drink)",
                r"need\s+(more\s+)?caffeine",
                r"(drinking|drank)\s+(too\s+much|a\s+lot|heavily)",
            ],
            "context": "substance_concern",
        },
        "burnout": {
            "negative": [
                r"(i('m|\s+am)\s+)?(so\s+)?(exhausted|burnt?\s*out|overwhelmed|drained)",
                r"(can't|cant)\s+(take|handle|do)\s+(it|this|anymore)",
                r"(everything|it\s+all)\s+(is|feels)\s+(too\s+much|overwhelming)",
                r"(i('m|\s+am)\s+)?running\s+on\s+(empty|fumes)",
            ],
            "context": "burnout",
        },
    }

    def __init__(self):
        super().__init__(
            name="wellness",
            description="Health monitoring — sleep, meals, exercise, stress, accountability",
            priority=Protocol.PRIORITY_HIGH,
        )
        self._tracked_goals = []
        self._health_flags = []
        self._last_check_in = None

    def process_input(self, user_input, context):
        """Scan for health-related concerns and inject context."""
        result = {
            "input": user_input,
            "context_injection": "",
            "intercept": False,
            "response": "",
        }

        input_lower = user_input.lower()
        triggered_contexts = []

        for category, data in self.HEALTH_TRIGGERS.items():
            for pattern in data["negative"]:
                if re.search(pattern, input_lower):
                    triggered_contexts.append((category, data["context"]))
                    self._health_flags.append({
                        "timestamp": datetime.now().isoformat(),
                        "category": category,
                        "context": data["context"],
                        "input_snippet": user_input[:100],
                    })
                    break

        if triggered_contexts:
            categories = ", ".join(c[0] for c in triggered_contexts)
            result["context_injection"] = (
                f"[Wellness flag: {categories}. Address this directly.]"
            )

        return result

    def process_output(self, response, context):
        """Wellness doesn't modify output — the health context injection
        guides the LLM to respond appropriately."""
        return {
            "response": response,
            "suppress": False,
            "append": "",
        }

    def track_goal(self, goal_text, category="general"):
        """Add a goal to track for accountability."""
        self._tracked_goals.append({
            "text": goal_text,
            "category": category,
            "created": datetime.now().isoformat(),
            "status": "active",
            "check_ins": [],
        })

    def get_accountability_context(self):
        """Get context about goals the companion should be held accountable for."""
        active = [g for g in self._tracked_goals if g["status"] == "active"]
        if not active:
            return ""

        lines = ["Goals your companion has committed to (follow up when relevant):"]
        for goal in active:
            lines.append(f"- {goal['text']} (set {goal['created'][:10]})")

        return "\n".join(lines)

    def get_commands(self):
        """Wellness protocol commands."""
        return [
            {
                "command": "wellness",
                "description": "Show wellness protocol status and health flags",
                "handler": "cmd_status",
            },
        ]

    def cmd_status(self, args=""):
        """Show wellness status."""
        lines = [
            "",
            "  WELLNESS PROTOCOL STATUS",
            "  ========================",
            f"  Status: {'ACTIVE' if self.enabled else 'DISABLED'}",
            f"  Priority: {self.priority} (HIGH)",
            f"  Health flags logged: {len(self._health_flags)}",
            f"  Goals tracked: {len(self._tracked_goals)}",
            f"  Active goals: {len([g for g in self._tracked_goals if g['status'] == 'active'])}",
        ]

        if self._health_flags:
            lines.append("")
            lines.append("  Recent health flags:")
            for flag in self._health_flags[-5:]:
                lines.append(f"    - [{flag['category']}] {flag['timestamp'][:16]}")

        lines.append("")
        return "\n".join(lines)

    def get_status(self):
        """Extended wellness status."""
        status = super().get_status()
        status["health_flags"] = len(self._health_flags)
        status["tracked_goals"] = len(self._tracked_goals)
        status["active_goals"] = len([g for g in self._tracked_goals if g["status"] == "active"])
        return status
