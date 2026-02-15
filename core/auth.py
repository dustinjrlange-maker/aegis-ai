"""
Authentication & User Management — Aegis AI
Handles user registration, login, session tokens, and per-user data directories.
Local-only passcode authentication — no cloud services.
"""

import json
import secrets
import logging
from datetime import datetime, timedelta
from pathlib import Path

import bcrypt as _bcrypt

from core.config import PROJECT_ROOT


def _hash_passcode(passcode: str) -> str:
    """Hash a passcode with bcrypt."""
    return _bcrypt.hashpw(passcode.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")


def _verify_passcode(passcode: str, hashed: str) -> bool:
    """Verify a passcode against a bcrypt hash."""
    return _bcrypt.checkpw(passcode.encode("utf-8"), hashed.encode("utf-8"))

logger = logging.getLogger(__name__)

USERS_FILE = PROJECT_ROOT / "data" / "users.json"
USERS_DIR = PROJECT_ROOT / "data" / "users"
SESSION_EXPIRY_HOURS = 24

# In-memory session store: {token: {username, login_time, last_activity}}
active_sessions: dict[str, dict] = {}


# --- User Registry ---

def load_users() -> dict:
    """Load the user registry from disk."""
    if USERS_FILE.exists():
        try:
            return json.loads(USERS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("Could not load users.json: %s", e)
    return {}


def save_users(users: dict):
    """Save the user registry to disk."""
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    USERS_FILE.write_text(
        json.dumps(users, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def create_user(username: str, display_name: str, passcode: str) -> dict:
    """Create a new user account with hashed passcode and directory tree."""
    username = username.lower().strip()
    if not username or not username.isalnum():
        raise ValueError("Username must be alphanumeric")
    if len(passcode) < 4:
        raise ValueError("Passcode must be at least 4 characters")

    users = load_users()
    if username in users:
        raise ValueError(f"Username '{username}' already exists")

    # Hash the passcode
    hashed = _hash_passcode(passcode)

    # Create user entry
    users[username] = {
        "display_name": display_name or username.title(),
        "passcode_hash": hashed,
        "created": datetime.now().isoformat(),
        "preferences": {},
    }
    save_users(users)

    # Create per-user directory tree
    _create_user_dirs(username)

    logger.info("Created user account: %s", username)
    return users[username]


def verify_user(username: str, passcode: str) -> bool:
    """Verify a user's passcode against stored hash."""
    users = load_users()
    user = users.get(username.lower().strip())
    if not user:
        return False
    return _verify_passcode(passcode, user["passcode_hash"])


def change_passcode(username: str, old_passcode: str, new_passcode: str) -> bool:
    """Change a user's passcode. Returns True on success."""
    if not verify_user(username, old_passcode):
        return False
    if len(new_passcode) < 4:
        raise ValueError("Passcode must be at least 4 characters")

    users = load_users()
    username = username.lower().strip()
    users[username]["passcode_hash"] = _hash_passcode(new_passcode)
    save_users(users)
    return True


def get_user_data_dir(username: str) -> Path:
    """Get the data directory for a specific user."""
    return USERS_DIR / username.lower().strip()


def _create_user_dirs(username: str):
    """Create the per-user directory tree."""
    user_dir = get_user_data_dir(username)
    subdirs = [
        "conversation_logs",
        "session_journals",
        "knowledge_base",
        "security_protocols",
        "personal_logs",
        "personal_logs/audio",
    ]
    for subdir in subdirs:
        (user_dir / subdir).mkdir(parents=True, exist_ok=True)


# --- User Preferences ---

def load_user_preferences(username: str) -> dict:
    """Load a user's preferences (pack selections, settings)."""
    users = load_users()
    user = users.get(username.lower().strip(), {})
    return user.get("preferences", {})


def save_user_preferences(username: str, prefs: dict):
    """Save a user's preferences."""
    users = load_users()
    username = username.lower().strip()
    if username not in users:
        return
    users[username]["preferences"] = prefs
    save_users(users)


# --- Session Tokens ---

def generate_session_token() -> str:
    """Generate a cryptographically secure session token."""
    return secrets.token_urlsafe(32)


def create_session(username: str) -> str:
    """Create a new session for a user, returning the token."""
    token = generate_session_token()
    now = datetime.now()
    active_sessions[token] = {
        "username": username.lower().strip(),
        "login_time": now.isoformat(),
        "last_activity": now.isoformat(),
    }
    return token


def validate_token(token: str) -> str | None:
    """Validate a session token. Returns username if valid, None otherwise."""
    session = active_sessions.get(token)
    if not session:
        return None

    # Check expiry
    last = datetime.fromisoformat(session["last_activity"])
    if datetime.now() - last > timedelta(hours=SESSION_EXPIRY_HOURS):
        del active_sessions[token]
        return None

    # Update last activity
    session["last_activity"] = datetime.now().isoformat()
    return session["username"]


def invalidate_token(token: str):
    """Remove a session token (logout)."""
    active_sessions.pop(token, None)


def get_current_user(request) -> str | None:
    """FastAPI dependency — extract and validate user from Authorization header."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    return validate_token(token)


def user_exists() -> bool:
    """Check if any users exist in the registry."""
    return bool(load_users())
