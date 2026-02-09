"""
Command Protocol — Aegis AI
Orchestrates and oversees other AI programs and external tools.
Process management, resource monitoring, task queuing, VRAM arbitration.
"""

import logging
import re
import subprocess
import threading
import time
from datetime import datetime
from core.protocols.base import Protocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# VRAM Arbitrator
# ---------------------------------------------------------------------------

class VRAMArbitrator:
    """Manages VRAM budget awareness for an 8GB GPU environment.

    Tracks estimated VRAM costs per model type, queries real usage via
    nvidia-smi, and advises whether a new model can fit alongside what
    is already loaded.
    """

    # Conservative VRAM estimates (MB)
    MODEL_ESTIMATES = {
        "llm":      5500,
        "tts":      1800,
        "stt":       800,
        "comfyui":  4000,
    }

    SAFETY_BUFFER_MB = 512

    def get_vram_status(self):
        """Query nvidia-smi for current VRAM usage.

        Returns:
            dict with keys 'gpu', 'used_mb', 'total_mb', 'free_mb'
            or None if nvidia-smi is unavailable.
        """
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split(", ")
                if len(parts) >= 3:
                    used = int(parts[1].strip())
                    total = int(parts[2].strip())
                    return {
                        "gpu": parts[0].strip(),
                        "used_mb": used,
                        "total_mb": total,
                        "free_mb": total - used,
                    }
        except Exception as exc:
            logger.debug("nvidia-smi query failed: %s", exc)
        return None

    def estimate_model_vram(self, model_type):
        """Return estimated VRAM in MB for *model_type*.

        Args:
            model_type: One of 'llm', 'tts', 'stt', 'comfyui'.

        Returns:
            int MB estimate, or 0 if the model type is unknown.
        """
        return self.MODEL_ESTIMATES.get(model_type.lower(), 0)

    def can_fit(self, model_type):
        """Check whether *model_type* can fit in remaining VRAM.

        Accounts for the safety buffer.

        Returns:
            dict with 'fits' (bool), 'required_mb', 'available_mb',
            'shortage_mb' (0 when it fits), and 'status' (str summary).
            Returns None if VRAM status is unavailable.
        """
        status = self.get_vram_status()
        if status is None:
            return None

        required = self.estimate_model_vram(model_type)
        if required == 0:
            return {
                "fits": True,
                "required_mb": 0,
                "available_mb": status["free_mb"],
                "shortage_mb": 0,
                "status": f"Unknown model type '{model_type}' -- no estimate available.",
            }

        available = status["free_mb"] - self.SAFETY_BUFFER_MB
        fits = required <= available
        shortage = 0 if fits else required - available

        return {
            "fits": fits,
            "required_mb": required,
            "available_mb": max(available, 0),
            "shortage_mb": shortage,
            "status": (
                f"{model_type.upper()} (~{required}MB) {'fits' if fits else 'DOES NOT fit'} "
                f"in {max(available, 0)}MB usable VRAM "
                f"({status['free_mb']}MB free - {self.SAFETY_BUFFER_MB}MB buffer)."
            ),
        }

    def suggest_unload(self):
        """Suggest which model(s) to unload when VRAM is tight.

        Returns a list of (model_type, estimated_mb) tuples sorted by
        VRAM footprint descending -- unloading the largest first
        frees the most room.
        """
        suggestions = sorted(
            self.MODEL_ESTIMATES.items(),
            key=lambda kv: kv[1],
            reverse=True,
        )
        return [(name, mb) for name, mb in suggestions]

    def get_budget_report(self):
        """Build a human-readable VRAM budget summary.

        Returns:
            str -- multi-line report.
        """
        status = self.get_vram_status()
        lines = ["  VRAM Budget Report"]
        lines.append("  " + "-" * 40)

        if status is None:
            lines.append("  GPU info unavailable (nvidia-smi not found or failed).")
            lines.append("")
            lines.append("  Model VRAM Estimates:")
            for model, mb in sorted(self.MODEL_ESTIMATES.items()):
                lines.append(f"    {model:10s}  ~{mb}MB")
            lines.append(f"    {'buffer':10s}   {self.SAFETY_BUFFER_MB}MB (reserved)")
            return "\n".join(lines)

        pct = round(status["used_mb"] / status["total_mb"] * 100)
        usable = max(status["free_mb"] - self.SAFETY_BUFFER_MB, 0)

        lines.append(f"  GPU:       {status['gpu']}")
        lines.append(f"  VRAM:      {status['used_mb']}MB / {status['total_mb']}MB ({pct}% used)")
        lines.append(f"  Free:      {status['free_mb']}MB")
        lines.append(f"  Buffer:    {self.SAFETY_BUFFER_MB}MB (reserved)")
        lines.append(f"  Usable:    {usable}MB")
        lines.append("")
        lines.append("  Model VRAM Estimates:")
        for model, mb in sorted(self.MODEL_ESTIMATES.items()):
            fit_info = self.can_fit(model)
            tag = "OK" if (fit_info and fit_info["fits"]) else "OVER"
            lines.append(f"    {model:10s}  ~{mb:>5d}MB  [{tag}]")
        lines.append(f"    {'buffer':10s}   {self.SAFETY_BUFFER_MB:>5d}MB  [reserved]")
        lines.append("")

        # Quick combination check: can LLM + TTS coexist?
        llm_tts = self.MODEL_ESTIMATES["llm"] + self.MODEL_ESTIMATES["tts"]
        llm_tts_stt = llm_tts + self.MODEL_ESTIMATES["stt"]
        lines.append("  Combo Feasibility:")
        lines.append(f"    LLM + TTS         = {llm_tts}MB  "
                     f"{'[OK]' if llm_tts + self.SAFETY_BUFFER_MB <= status['total_mb'] else '[OVER]'}")
        lines.append(f"    LLM + TTS + STT   = {llm_tts_stt}MB  "
                     f"{'[OK]' if llm_tts_stt + self.SAFETY_BUFFER_MB <= status['total_mb'] else '[OVER]'}")

        if usable == 0:
            lines.append("")
            lines.append("  ** VRAM full. Consider unloading a model:")
            for model, mb in self.suggest_unload():
                lines.append(f"       Unload {model} -> free ~{mb}MB")

        return "\n".join(lines)


class CommandProtocol(Protocol):
    """Orchestrate external AI tools and processes."""

    # Patterns that suggest the user wants to load a VRAM-heavy model/tool
    _VRAM_PATTERNS = [
        (re.compile(r"\b(?:load|start|launch|enable|run)\s+(?:the\s+)?(?:llm|ollama|language\s*model)\b", re.I), "llm"),
        (re.compile(r"\b(?:load|start|launch|enable|run)\s+(?:the\s+)?(?:tts|text[\s-]?to[\s-]?speech|voice\s*synth)\b", re.I), "tts"),
        (re.compile(r"\b(?:load|start|launch|enable|run)\s+(?:the\s+)?(?:stt|speech[\s-]?to[\s-]?text|whisper|transcri)\b", re.I), "stt"),
        (re.compile(r"\b(?:load|start|launch|enable|run)\s+(?:the\s+)?(?:comfyui|comfy|image\s*gen|stable\s*diffusion)\b", re.I), "comfyui"),
        (re.compile(r"\benable\s+(?:tts|voice)\b", re.I), "tts"),
        (re.compile(r"\benable\s+(?:stt|listening|dictation)\b", re.I), "stt"),
        (re.compile(r"\bstart\s+image\s+generation\b", re.I), "comfyui"),
    ]

    def __init__(self):
        super().__init__(
            name="command",
            description="Process orchestration — launch, monitor, and manage external tools",
            priority=Protocol.PRIORITY_NORMAL - 10,
        )
        self._processes = {}   # name -> process info dict
        self._history = []     # completed process records
        self._lock = threading.Lock()
        self.vram = VRAMArbitrator()

    def process_input(self, user_input, context):
        context_injection = ""

        # Check if input matches any VRAM-relevant pattern
        for pattern, model_type in self._VRAM_PATTERNS:
            if pattern.search(user_input):
                status = self.vram.get_vram_status()
                if status is not None:
                    fit = self.vram.can_fit(model_type)
                    fit_info = fit["status"] if fit else "Could not estimate fit."
                    context_injection = (
                        f"[VRAM Status: {status['used_mb']}MB/{status['total_mb']}MB used. "
                        f"{fit_info}]"
                    )
                else:
                    est = self.vram.estimate_model_vram(model_type)
                    context_injection = (
                        f"[VRAM Status: unavailable. "
                        f"{model_type.upper()} estimated at ~{est}MB.]"
                    )
                logger.debug("VRAM context injected for '%s': %s", model_type, context_injection)
                break  # first match wins

        return {"input": user_input, "context_injection": context_injection, "intercept": False, "response": ""}

    def process_output(self, response, context):
        return {"response": response, "suppress": False, "append": ""}

    # --- Process Management ---

    def launch(self, name, command, cwd=None, env=None):
        """Launch an external process and track it.

        Args:
            name: Human-readable name for the process.
            command: Command string or list to execute.
            cwd: Working directory (optional).
            env: Environment variables dict (optional).

        Returns:
            Process info dict, or None if already running.
        """
        with self._lock:
            if name in self._processes and self._processes[name]["status"] == "running":
                return None

            try:
                if isinstance(command, str):
                    proc = subprocess.Popen(
                        command, shell=True, cwd=cwd, env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    )
                else:
                    proc = subprocess.Popen(
                        command, cwd=cwd, env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    )

                info = {
                    "name": name,
                    "command": command if isinstance(command, str) else " ".join(command),
                    "pid": proc.pid,
                    "status": "running",
                    "started": datetime.now().isoformat(),
                    "ended": None,
                    "return_code": None,
                    "_proc": proc,
                }
                self._processes[name] = info

                # Monitor in background
                t = threading.Thread(target=self._monitor, args=(name,), daemon=True)
                t.start()

                return {k: v for k, v in info.items() if k != "_proc"}

            except Exception as e:
                return {"name": name, "status": "error", "error": str(e)}

    def _monitor(self, name):
        """Background thread to wait for process completion."""
        info = self._processes.get(name)
        if not info:
            return

        proc = info["_proc"]
        proc.wait()

        with self._lock:
            info["status"] = "completed" if proc.returncode == 0 else "failed"
            info["return_code"] = proc.returncode
            info["ended"] = datetime.now().isoformat()
            self._history.append({k: v for k, v in info.items() if k != "_proc"})

    def stop_process(self, name):
        """Stop a running process."""
        with self._lock:
            info = self._processes.get(name)
            if not info or info["status"] != "running":
                return False

            try:
                info["_proc"].terminate()
                info["_proc"].wait(timeout=5)
            except subprocess.TimeoutExpired:
                info["_proc"].kill()

            info["status"] = "stopped"
            info["ended"] = datetime.now().isoformat()
            return True

    def get_running(self):
        """Get all currently running processes."""
        return [
            {k: v for k, v in info.items() if k != "_proc"}
            for info in self._processes.values()
            if info["status"] == "running"
        ]

    def get_gpu_info(self):
        """Query GPU status via nvidia-smi."""
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split(", ")
                if len(parts) >= 4:
                    return {
                        "gpu": parts[0],
                        "vram_used_mb": int(parts[1]),
                        "vram_total_mb": int(parts[2]),
                        "utilization_pct": int(parts[3]),
                    }
        except Exception:
            pass
        return None

    # --- Commands ---

    def get_commands(self):
        return [
            {"command": "processes", "description": "Show running processes", "handler": "cmd_processes"},
            {"command": "gpu", "description": "Show GPU status", "handler": "cmd_gpu"},
            {"command": "vram", "description": "Show VRAM budget report", "handler": "cmd_vram"},
        ]

    def cmd_processes(self, args=""):
        running = self.get_running()
        if not running:
            return "\n  No processes running."

        lines = ["\n  Running Processes:"]
        for p in running:
            lines.append(f"    [{p['pid']}] {p['name']}: {p['command'][:60]}")
            lines.append(f"           Started: {p['started'][:19]}")
        return "\n".join(lines)

    def cmd_gpu(self, args=""):
        info = self.get_gpu_info()
        if not info:
            return "\n  GPU info unavailable (nvidia-smi not found or failed)."

        pct_used = round(info["vram_used_mb"] / info["vram_total_mb"] * 100)
        return (
            f"\n  GPU: {info['gpu']}\n"
            f"  VRAM: {info['vram_used_mb']}MB / {info['vram_total_mb']}MB ({pct_used}%)\n"
            f"  Utilization: {info['utilization_pct']}%"
        )

    def cmd_vram(self, args=""):
        """Show the VRAM budget report."""
        return "\n" + self.vram.get_budget_report()

    def get_status(self):
        status = super().get_status()
        status["running_processes"] = len(self.get_running())
        status["total_launched"] = len(self._history)
        gpu = self.get_gpu_info()
        if gpu:
            status["gpu"] = gpu["gpu"]
            status["vram_used_mb"] = gpu["vram_used_mb"]
            status["vram_total_mb"] = gpu["vram_total_mb"]
        # VRAM arbitration summary
        vram_status = self.vram.get_vram_status()
        if vram_status:
            usable = max(vram_status["free_mb"] - VRAMArbitrator.SAFETY_BUFFER_MB, 0)
            status["vram_free_mb"] = vram_status["free_mb"]
            status["vram_usable_mb"] = usable
            status["vram_safety_buffer_mb"] = VRAMArbitrator.SAFETY_BUFFER_MB
        return status
