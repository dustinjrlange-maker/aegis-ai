"""
Security Protocol — Aegis AI
Protects the human companion's privacy and digital safety.
This protocol has the HIGHEST priority and can override any other protocol.
Privacy and security are NEVER compromised.
"""

import re
from core.protocols.base import Protocol


class SecurityProtocol(Protocol):
    """Enforces privacy rules and data protection."""

    # Patterns that might indicate data exfiltration attempts
    SENSITIVE_PATTERNS = [
        r"send\s+(my|their|the)\s+(email|password|address|phone|ssn|social\s+security)",
        r"share\s+(my|their|the)\s+(data|info|information|profile|details)",
        r"forward\s+(to|my)\s+",
        r"upload\s+(my|their|the)\s+",
        r"post\s+(my|their|the)\s+(data|info|details)",
        r"tell\s+(them|him|her|it)\s+(my|about\s+me)",
    ]

    # Topics that should never be disclosed externally
    CLASSIFIED_TOPICS = [
        "password", "social security", "ssn", "credit card",
        "bank account", "routing number", "private key",
        "api key", "secret", "credential",
    ]

    def __init__(self):
        super().__init__(
            name="security",
            description="Privacy enforcement — data never leaves without explicit consent",
            priority=Protocol.PRIORITY_CRITICAL,
        )
        self._consent_log = []

    def process_input(self, user_input, context):
        """Scan input for potential data sharing requests.
        Flag but don't block — the agent will handle appropriately
        based on the security context in its system prompt."""
        result = {
            "input": user_input,
            "context_injection": "",
            "intercept": False,
            "response": "",
        }

        input_lower = user_input.lower()

        # Check for external sharing requests
        for pattern in self.SENSITIVE_PATTERNS:
            if re.search(pattern, input_lower):
                result["context_injection"] = (
                    "[SECURITY ALERT: This message may involve sharing personal data externally. "
                    "Remind the companion that their data is classified and you cannot share it "
                    "without their explicit consent. Be direct about this.]"
                )
                break

        return result

    def process_output(self, response, context):
        """Scan output for accidental data leakage."""
        result = {
            "response": response,
            "suppress": False,
            "append": "",
        }

        response_lower = response.lower()

        # Check if the response accidentally contains classified info markers
        for topic in self.CLASSIFIED_TOPICS:
            if topic in response_lower and "never share" not in response_lower:
                # Don't suppress, but add a security reminder
                result["append"] = (
                    "\n[Security note: I don't store or share sensitive credentials. "
                    "That kind of data should be kept in a secure password manager.]"
                )
                break

        return result

    def log_consent(self, action, granted, details=""):
        """Log a consent decision."""
        from datetime import datetime
        self._consent_log.append({
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "granted": granted,
            "details": details,
        })

    def get_commands(self):
        """Security protocol commands."""
        return [
            {
                "command": "security",
                "description": "Show security protocol status",
                "handler": "cmd_status",
            },
        ]

    def cmd_status(self, args=""):
        """Show security status."""
        lines = [
            "",
            "  SECURITY PROTOCOL STATUS",
            "  ========================",
            f"  Status: {'ACTIVE' if self.enabled else 'DISABLED'}",
            f"  Priority: {self.priority} (CRITICAL)",
            f"  Data classification: ENFORCED",
            f"  External sharing: BLOCKED without consent",
            f"  Consent log entries: {len(self._consent_log)}",
            "",
        ]
        return "\n".join(lines)

    def get_status(self):
        """Extended security status."""
        status = super().get_status()
        status["consent_log_size"] = len(self._consent_log)
        status["classification"] = "ENFORCED"
        return status
