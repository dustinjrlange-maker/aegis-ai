"""
Creative Protocol — Aegis AI
AI-assisted creative production: image generation, video editing,
audio production, asset management.

Adapters connect to external tools (ComfyUI, A1111, ffmpeg, etc.)
Each adapter is optional — the protocol works with whatever is installed.
"""

import subprocess
import shutil
from pathlib import Path
from datetime import datetime
from core.protocols.base import Protocol
from core.config import PROJECT_ROOT


class CreativeProtocol(Protocol):
    """AI-assisted creative production pipeline."""

    OUTPUT_DIR = PROJECT_ROOT / "data" / "creative_output"

    def __init__(self):
        super().__init__(
            name="creative",
            description="Creative tools — image gen, video editing, audio, asset management",
            priority=Protocol.PRIORITY_LOW,
        )
        self._adapters = {}
        self._detect_tools()

    def _detect_tools(self):
        """Detect which creative tools are available on the system."""
        # ffmpeg — video/audio processing
        self._adapters["ffmpeg"] = {
            "available": shutil.which("ffmpeg") is not None,
            "description": "Video/audio processing, format conversion, editing",
        }

        # ComfyUI — check common install locations
        comfy_paths = [
            Path.home() / "ComfyUI",
            Path("C:/ComfyUI"),
            Path("C:/AI/ComfyUI"),
        ]
        comfy_found = any(p.exists() for p in comfy_paths)
        comfy_path = next((p for p in comfy_paths if p.exists()), None)
        self._adapters["comfyui"] = {
            "available": comfy_found,
            "path": str(comfy_path) if comfy_path else None,
            "description": "Stable Diffusion image generation via ComfyUI",
        }

        # A1111 — check common install locations
        a1111_paths = [
            Path.home() / "stable-diffusion-webui",
            Path("C:/stable-diffusion-webui"),
            Path("C:/AI/stable-diffusion-webui"),
        ]
        a1111_found = any(p.exists() for p in a1111_paths)
        a1111_path = next((p for p in a1111_paths if p.exists()), None)
        self._adapters["a1111"] = {
            "available": a1111_found,
            "path": str(a1111_path) if a1111_path else None,
            "description": "Stable Diffusion image generation via AUTOMATIC1111",
        }

        # ImageMagick
        self._adapters["imagemagick"] = {
            "available": shutil.which("magick") is not None or shutil.which("convert") is not None,
            "description": "Image manipulation, conversion, batch processing",
        }

    def process_input(self, user_input, context):
        return {"input": user_input, "context_injection": "", "intercept": False, "response": ""}

    def process_output(self, response, context):
        return {"response": response, "suppress": False, "append": ""}

    # --- ffmpeg Operations ---

    def ffmpeg_convert(self, input_path, output_path, extra_args=None):
        """Convert a media file using ffmpeg."""
        if not self._adapters["ffmpeg"]["available"]:
            return {"success": False, "error": "ffmpeg not found"}

        cmd = ["ffmpeg", "-y", "-i", str(input_path)]
        if extra_args:
            cmd.extend(extra_args)
        cmd.append(str(output_path))

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            return {
                "success": result.returncode == 0,
                "output_path": str(output_path),
                "stderr": result.stderr[-500:] if result.returncode != 0 else "",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def ffmpeg_trim(self, input_path, output_path, start, duration):
        """Trim a video/audio file."""
        return self.ffmpeg_convert(
            input_path, output_path,
            extra_args=["-ss", str(start), "-t", str(duration), "-c", "copy"]
        )

    def ffmpeg_extract_audio(self, input_path, output_path):
        """Extract audio from a video file."""
        return self.ffmpeg_convert(
            input_path, output_path,
            extra_args=["-vn", "-acodec", "pcm_s16le", "-ar", "44100"]
        )

    def ffmpeg_concatenate(self, input_paths, output_path):
        """Concatenate multiple video/audio files."""
        if not self._adapters["ffmpeg"]["available"]:
            return {"success": False, "error": "ffmpeg not found"}

        # Create a temporary file list
        list_path = self.OUTPUT_DIR / "_concat_list.txt"
        self.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        with open(list_path, "w") as f:
            for p in input_paths:
                f.write(f"file '{p}'\n")

        try:
            result = subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                 "-i", str(list_path), "-c", "copy", str(output_path)],
                capture_output=True, text=True, timeout=300,
            )
            return {
                "success": result.returncode == 0,
                "output_path": str(output_path),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            list_path.unlink(missing_ok=True)

    # --- Asset Management ---

    def list_outputs(self, subfolder=None):
        """List files in the creative output directory."""
        target = self.OUTPUT_DIR / subfolder if subfolder else self.OUTPUT_DIR
        if not target.exists():
            return []

        return sorted(
            [{"name": f.name, "size_kb": f.stat().st_size // 1024,
              "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat()[:19]}
             for f in target.iterdir() if f.is_file()],
            key=lambda x: x["modified"], reverse=True,
        )

    def organize_output(self, filename, project_name):
        """Move a creative output into a project subfolder."""
        src = self.OUTPUT_DIR / filename
        dst_dir = self.OUTPUT_DIR / project_name
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / filename

        if src.exists():
            src.rename(dst)
            return {"success": True, "new_path": str(dst)}
        return {"success": False, "error": f"File not found: {filename}"}

    # --- Commands ---

    def get_commands(self):
        return [
            {"command": "creative", "description": "Creative tools status", "handler": "cmd_status_creative"},
            {"command": "outputs", "description": "List creative outputs", "handler": "cmd_outputs"},
        ]

    def cmd_status_creative(self, args=""):
        lines = ["\n  CREATIVE PROTOCOL — AVAILABLE TOOLS", "  ===================================="]
        for name, info in self._adapters.items():
            status = "AVAILABLE" if info["available"] else "not found"
            lines.append(f"    {name}: {status}")
            lines.append(f"      {info['description']}")
            if info.get("path"):
                lines.append(f"      Path: {info['path']}")
        return "\n".join(lines)

    def cmd_outputs(self, args=""):
        files = self.list_outputs(args.strip() if args.strip() else None)
        if not files:
            return "\n  No creative outputs found."

        lines = ["\n  Creative Outputs:"]
        for f in files[:20]:
            lines.append(f"    {f['name']} ({f['size_kb']}KB) — {f['modified']}")
        if len(files) > 20:
            lines.append(f"    ... and {len(files) - 20} more")
        return "\n".join(lines)

    def get_status(self):
        status = super().get_status()
        available = [n for n, a in self._adapters.items() if a["available"]]
        status["available_tools"] = available
        status["total_tools"] = len(self._adapters)
        return status
