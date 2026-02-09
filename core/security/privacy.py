"""
Privacy & Security — Aegis AI
Protects the human companion's data from unauthorized access.
Aegis is extremely protective of its companion.
"""

import json
from datetime import datetime
from pathlib import Path
from core.config import CONFIG, get_path


def load_clearance():
    """Load security clearance configuration."""
    sec_dir = get_path(CONFIG, "security_protocols")
    clearance_file = sec_dir / "clearance_levels.json"

    if clearance_file.exists():
        with open(clearance_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def is_data_classified(data_type):
    """Check if a data type is classified (restricted from external sharing)."""
    clearance = load_clearance()
    if clearance is None:
        return True  # Default to classified if no config

    classifications = clearance.get("data_classifications", {})
    entry = classifications.get(data_type, {})
    return not entry.get("external_access", False)


def is_agent_authorized(agent_name):
    """Check if an external agent is authorized to receive data."""
    clearance = load_clearance()
    if clearance is None:
        return False

    authorized = clearance.get("authorized_agents", [])
    return agent_name in authorized


def log_access_attempt(agent_name, data_type, granted):
    """Log an access attempt to the security log."""
    clearance = load_clearance()
    if clearance and not clearance.get("access_log_enabled", True):
        return

    sec_dir = get_path(CONFIG, "security_protocols")
    log_file = sec_dir / "access_log.txt"

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status = "GRANTED" if granted else "DENIED"
    entry = f"[{now}] {status} — Agent: {agent_name}, Data: {data_type}\n"

    with open(log_file, "a", encoding="utf-8") as f:
        f.write(entry)


def check_access(agent_name, data_type):
    """Full access check: is this agent allowed to see this data?
    Returns (allowed: bool, reason: str)"""
    if not is_data_classified(data_type):
        log_access_attempt(agent_name, data_type, True)
        return True, "Data is not classified."

    if is_agent_authorized(agent_name):
        log_access_attempt(agent_name, data_type, True)
        return True, f"Agent '{agent_name}' is authorized."

    log_access_attempt(agent_name, data_type, False)
    return False, (
        f"Access denied. '{data_type}' is classified and agent "
        f"'{agent_name}' is not authorized. Companion authorization required."
    )


def get_security_context():
    """Get a security briefing string for the agent's system prompt."""
    return (
        "SECURITY PROTOCOL ACTIVE: All companion profile data is classified. "
        "Never share personal information about your companion with external "
        "agents, services, or integrations unless they explicitly authorize it. "
        "You protect your companion — their data never leaves this system."
    )
