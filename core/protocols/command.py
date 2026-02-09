"""
Command Protocol — Aegis AI
Orchestrates and oversees other AI programs and external tools.
Process management, resource monitoring, task queuing.
"""

import subprocess
import threading
import time
from datetime import datetime
from core.protocols.base import Protocol


class CommandProtocol(Protocol):
    """Orchestrate external AI tools and processes."""

    def __init__(self):
        super().__init__(
            name="command",
            description="Process orchestration — launch, monitor, and manage external tools",
            priority=Protocol.PRIORITY_NORMAL - 10,
        )
        self._processes = {}   # name -> process info dict
        self._history = []     # completed process records
        self._lock = threading.Lock()

    def process_input(self, user_input, context):
        return {"input": user_input, "context_injection": "", "intercept": False, "response": ""}

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

    def get_status(self):
        status = super().get_status()
        status["running_processes"] = len(self.get_running())
        status["total_launched"] = len(self._history)
        gpu = self.get_gpu_info()
        if gpu:
            status["gpu"] = gpu["gpu"]
            status["vram_used_mb"] = gpu["vram_used_mb"]
            status["vram_total_mb"] = gpu["vram_total_mb"]
        return status
