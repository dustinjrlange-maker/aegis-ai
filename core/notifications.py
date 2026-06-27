"""
Aegis AI — Notification Service
Session-scoped in-memory notification store. No disk persistence.
"""

import uuid
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass
class Notification:
    id: str
    type: str
    title: str
    body: str
    source_id: str
    created: str
    read: bool = False

    def to_dict(self):
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "body": self.body,
            "source_id": self.source_id,
            "created": self.created,
            "read": self.read,
        }


class NotificationService:
    """In-memory notification store, scoped to a single session."""

    MAX_NOTIFICATIONS = 50

    def __init__(self):
        self._notifications: list[Notification] = []
        self._seen_sources: set[str] = set()

    def add(self, type: str, title: str, body: str, source_id: str = "") -> Notification | None:
        """Add a notification with dedup. Returns the notification or None if deduped."""
        dedup_key = f"{type}:{source_id}" if source_id else ""
        if dedup_key and dedup_key in self._seen_sources:
            return None

        notif = Notification(
            id=uuid.uuid4().hex[:12],
            type=type,
            title=title,
            body=body,
            source_id=source_id,
            created=datetime.now().isoformat(),
        )
        self._notifications.insert(0, notif)
        if dedup_key:
            self._seen_sources.add(dedup_key)

        # Cap size
        while len(self._notifications) > self.MAX_NOTIFICATIONS:
            self._notifications.pop()

        return notif

    def get_all(self) -> list[dict]:
        """Return all notifications as dicts, newest first."""
        return [n.to_dict() for n in self._notifications]

    def get_unread_count(self) -> int:
        return sum(1 for n in self._notifications if not n.read)

    def mark_read(self, notif_id: str) -> bool:
        for n in self._notifications:
            if n.id == notif_id:
                n.read = True
                return True
        return False

    def mark_all_read(self):
        for n in self._notifications:
            n.read = True

    def dismiss(self, notif_id: str) -> bool:
        """Remove a notification but keep its source in seen set."""
        for i, n in enumerate(self._notifications):
            if n.id == notif_id:
                self._notifications.pop(i)
                return True
        return False

    def dismiss_all(self):
        """Remove all notifications but keep seen sources."""
        self._notifications.clear()

    def generate_from_tasks(self, ops_protocol) -> None:
        """Scan overdue and due-within-24h tasks, add notifications."""
        if not ops_protocol:
            return
        try:
            now = datetime.now()
            tomorrow = now + timedelta(hours=24)
            for task in ops_protocol.get_pending_tasks():
                due = task.get("due")
                if not due:
                    continue
                try:
                    due_date_str = (due or "")[:10]
                    due_time_str = task.get("due_time") or "23:59"
                    due_dt = datetime.strptime(f"{due_date_str} {due_time_str}", "%Y-%m-%d %H:%M")
                except (ValueError, TypeError):
                    continue
                task_id = str(task.get("id", ""))
                if due_dt < now:
                    self.add(
                        type="overdue",
                        title=f"Overdue: {task.get('text', 'Task')}",
                        body=f"Was due {due_dt.strftime('%b %d')}",
                        source_id=f"task_{task_id}",
                    )
                elif due_dt < tomorrow:
                    self.add(
                        type="due_soon",
                        title=f"Due soon: {task.get('text', 'Task')}",
                        body=f"Due {due_dt.strftime('%b %d %H:%M')}",
                        source_id=f"task_{task_id}",
                    )
        except Exception as e:
            logger.warning("Error generating task notifications: %s", e)

    def generate_from_events(self, event_manager) -> None:
        """Scan today's events, add notifications."""
        if not event_manager:
            return
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            events = event_manager.list_events(start_date=today, end_date=today)
            for ev in events:
                self.add(
                    type="event",
                    title=f"Today: {ev.get('title', 'Event')}",
                    body=ev.get("time_start", "All day") or "All day",
                    source_id=f"event_{ev.get('id', '')}",
                )
        except Exception as e:
            logger.warning("Error generating event notifications: %s", e)
