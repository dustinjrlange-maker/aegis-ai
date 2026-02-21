"""
Social Media Manager -- Aegis AI
Project-based social media post management.
"""

import json
import uuid
from datetime import datetime
from pathlib import Path


class SocialMediaManager:
    """Manages social media projects and posts."""

    def __init__(self, data_dir):
        self._file = Path(data_dir) / "social_media.json"
        self._projects = []
        self._load()

    def _load(self):
        if self._file.exists():
            try:
                with open(self._file, "r", encoding="utf-8") as f:
                    self._projects = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._projects = []

    def _save(self):
        self._file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._file, "w", encoding="utf-8") as f:
            json.dump(self._projects, f, indent=2, ensure_ascii=False)

    def add_project(self, name: str):
        """Create a new social media project."""
        project = {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "accounts": [],
            "posts": [],
            "created": datetime.now().isoformat(),
        }
        self._projects.append(project)
        self._save()
        return project

    def get_project(self, project_id: str):
        """Get a project by ID."""
        for p in self._projects:
            if p["id"] == project_id:
                return p
        return None

    def delete_project(self, project_id: str) -> bool:
        """Delete a project by ID."""
        before = len(self._projects)
        self._projects = [p for p in self._projects if p["id"] != project_id]
        if len(self._projects) < before:
            self._save()
            return True
        return False

    def add_account(self, project_id: str, platform: str, handle: str):
        """Add a social media account to a project."""
        for p in self._projects:
            if p["id"] == project_id:
                p["accounts"].append({
                    "platform": platform,
                    "handle": handle,
                })
                self._save()
                return p
        return None

    def remove_account(self, project_id: str, platform: str):
        """Remove an account from a project by platform."""
        for p in self._projects:
            if p["id"] == project_id:
                p["accounts"] = [a for a in p["accounts"] if a["platform"] != platform]
                self._save()
                return p
        return None

    def add_post(self, project_id: str, content: str, platform: str = "",
                 status: str = "draft"):
        """Add a post to a project."""
        for p in self._projects:
            if p["id"] == project_id:
                post = {
                    "id": uuid.uuid4().hex[:12],
                    "content": content,
                    "platform": platform,
                    "status": status,
                    "created": datetime.now().isoformat(),
                }
                p["posts"].append(post)
                self._save()
                return post
        return None

    def update_post_status(self, project_id: str, post_id: str, status: str):
        """Update a post's status (draft -> posted)."""
        for p in self._projects:
            if p["id"] == project_id:
                for post in p["posts"]:
                    if post["id"] == post_id:
                        post["status"] = status
                        self._save()
                        return post
        return None

    def delete_post(self, project_id: str, post_id: str) -> bool:
        """Delete a post from a project."""
        for p in self._projects:
            if p["id"] == project_id:
                before = len(p["posts"])
                p["posts"] = [x for x in p["posts"] if x["id"] != post_id]
                if len(p["posts"]) < before:
                    self._save()
                    return True
        return False

    def list_projects(self):
        """List all projects."""
        return list(self._projects)
