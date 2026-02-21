"""
File Manager -- Aegis AI
Handles file uploads with text extraction for chat context.
"""

import json
import uuid
import shutil
from datetime import datetime
from pathlib import Path


class FileManager:
    """Manages uploaded files with metadata and text extraction."""

    MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

    def __init__(self, data_dir):
        self._dir = Path(data_dir)
        self._file = self._dir / "uploads.json"
        self._upload_dir = self._dir / "uploads"
        self._upload_dir.mkdir(parents=True, exist_ok=True)
        self._uploads = []
        self._load()

    def _load(self):
        if self._file.exists():
            try:
                with open(self._file, "r", encoding="utf-8") as f:
                    self._uploads = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._uploads = []

    def _save(self):
        self._file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._file, "w", encoding="utf-8") as f:
            json.dump(self._uploads, f, indent=2, ensure_ascii=False)

    def upload_file(self, original_name: str, file_bytes: bytes, mime_type: str = ""):
        """Upload a file and extract text content if possible."""
        if len(file_bytes) > self.MAX_FILE_SIZE:
            return {"error": f"File too large (max {self.MAX_FILE_SIZE // 1024 // 1024} MB)"}

        file_id = uuid.uuid4().hex[:12]
        ext = Path(original_name).suffix
        stored_name = f"{file_id}{ext}"
        stored_path = self._upload_dir / stored_name

        with open(stored_path, "wb") as f:
            f.write(file_bytes)

        # Extract text content
        text_content = self._extract_text(stored_path, mime_type, file_bytes)

        entry = {
            "id": file_id,
            "filename": stored_name,
            "original_name": original_name,
            "mime_type": mime_type,
            "size_bytes": len(file_bytes),
            "text_content": text_content,
            "uploaded": datetime.now().isoformat(),
        }
        self._uploads.append(entry)
        self._save()
        return entry

    def _extract_text(self, path: Path, mime_type: str, file_bytes: bytes) -> str:
        """Best-effort text extraction from uploaded file."""
        suffix = path.suffix.lower()

        # Plain text files
        if suffix in (".txt", ".md", ".csv", ".log", ".json", ".py", ".js",
                       ".html", ".css", ".yml", ".yaml", ".toml", ".ini",
                       ".cfg", ".xml", ".sh", ".bat"):
            try:
                return file_bytes.decode("utf-8", errors="replace")[:50000]
            except Exception:
                return ""

        # PDF
        if suffix == ".pdf":
            try:
                import fitz  # PyMuPDF
                doc = fitz.open(str(path))
                text = ""
                for page in doc:
                    text += page.get_text()
                doc.close()
                return text[:50000]
            except ImportError:
                return "[PDF text extraction requires PyMuPDF: pip install pymupdf]"
            except Exception:
                return ""

        # DOCX
        if suffix == ".docx":
            try:
                import docx
                doc = docx.Document(str(path))
                text = "\n".join(p.text for p in doc.paragraphs)
                return text[:50000]
            except ImportError:
                return "[DOCX extraction requires python-docx: pip install python-docx]"
            except Exception:
                return ""

        return ""

    def list_files(self):
        """List all uploaded files (metadata only, no text content)."""
        return [{k: v for k, v in u.items() if k != "text_content"}
                for u in self._uploads]

    def get_file(self, file_id: str):
        """Get full file metadata including text content."""
        for u in self._uploads:
            if u["id"] == file_id:
                return u
        return None

    def get_text(self, file_id: str) -> str:
        """Get just the text content of a file."""
        for u in self._uploads:
            if u["id"] == file_id:
                return u.get("text_content", "")
        return ""

    def get_file_path(self, file_id: str) -> Path | None:
        """Get the actual file path on disk."""
        for u in self._uploads:
            if u["id"] == file_id:
                p = self._upload_dir / u["filename"]
                if p.exists():
                    return p
        return None

    def delete_file(self, file_id: str) -> bool:
        """Delete a file and its metadata."""
        for u in self._uploads:
            if u["id"] == file_id:
                file_path = self._upload_dir / u["filename"]
                if file_path.exists():
                    file_path.unlink()
                self._uploads = [x for x in self._uploads if x["id"] != file_id]
                self._save()
                return True
        return False
