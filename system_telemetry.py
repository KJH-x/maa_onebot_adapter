from __future__ import annotations

import subprocess
from typing import Any


class SystemTelemetryCollector:
    def collect(self) -> dict[str, Any]:
        mem_percent, mem_used_gb, mem_total_gb = self._get_mem_info()
        return {
            "cpu": self._get_cpu_usage(),
            "gpu": self._get_gpu_usage(),
            "mem": {
                "percent": mem_percent,
                "used_gb": mem_used_gb,
                "total_gb": mem_total_gb,
            },
        }

    def _get_cpu_usage(self) -> float:
        try:
            result = subprocess.run(
                ["typeperf", r"\Processor(_Total)\% Processor Time", "-sc", "1"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in result.stdout.strip().split("\n"):
                if '"' in line and "," in line:
                    parts = line.split('","')
                    if len(parts) >= 2:
                        try:
                            return round(float(parts[1].replace('"', '')), 1)
                        except ValueError:
                            pass
        except Exception:
            pass
        return 0.0

    def _get_gpu_usage(self) -> float:
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return round(float(result.stdout.strip().split("\n")[0]), 1)
        except Exception:
            pass

        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-Command",
                    'Get-Counter "\\GPU Engine(*)\\Utilization Percentage" -MaxSamples 1 | '
                    'Select-Object -ExpandProperty CounterSamples | '
                    'Where-Object {$_.CookedValue -gt 0} | '
                    'Measure-Object CookedValue -Maximum | '
                    'Select-Object -ExpandProperty Maximum',
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return round(float(result.stdout.strip() or 0), 1)
        except Exception:
            pass
        return 0.0

    def _get_mem_info(self) -> tuple[float, float, float]:
        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-Command",
                    '$os = Get-CimInstance Win32_OperatingSystem; '
                    '"$($os.TotalVisibleMemorySize),$($os.FreePhysicalMemory)"',
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            parts = result.stdout.strip().split(",")
            if len(parts) >= 2:
                total_kb = int(parts[0])
                free_kb = int(parts[1])
                used_kb = total_kb - free_kb
                total_gb = total_kb / (1024 ** 2)
                used_gb = used_kb / (1024 ** 2)
                percent = used_kb / total_kb * 100 if total_kb > 0 else 0
                return round(percent, 1), round(used_gb, 1), round(total_gb, 1)
        except Exception:
            pass
        return 0.0, 0.0, 0.0
