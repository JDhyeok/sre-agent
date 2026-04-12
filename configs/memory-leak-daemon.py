#!/usr/bin/env python3
"""Slowly grow container RSS toward ~90% of cgroup memory limit (test scenario).

Reads cgroup v2 memory.max or v1 memory.limit_in_bytes, then allocates in chunks
with a sleep between chunks so Prometheus/cAdvisor can observe the climb.
"""
from __future__ import annotations

import os
import sys
import time

# Fraction of cgroup limit to allocate in this process (heap only; add ~20–35 MiB
# interpreter RSS so total container working set approaches ~90% of limit).
TARGET_RATIO = float(os.environ.get("MEMORY_LEAK_TARGET_RATIO", "0.72"))
# Bytes allocated per step (default 3 MiB)
CHUNK_BYTES = int(os.environ.get("MEMORY_LEAK_CHUNK_BYTES", str(3 * 1024 * 1024)))
# Pause between allocations (seconds)
STEP_SLEEP = float(os.environ.get("MEMORY_LEAK_STEP_SLEEP", "1"))


def read_cgroup_memory_limit_bytes() -> int:
    """Return cgroup memory limit in bytes, or a conservative fallback."""
    candidates = [
        "/sys/fs/cgroup/memory.max",
        "/sys/fs/cgroup/memory/memory.limit_in_bytes",
    ]
    for path in candidates:
        try:
            with open(path, encoding="utf-8") as f:
                raw = f.read().strip()
            if raw in ("max", "Max", ""):
                continue
            val = int(raw)
            if val > 1024 * 1024:  # ignore tiny / bogus
                return val
        except OSError:
            continue
    fallback = int(os.environ.get("MEMORY_LEAK_FALLBACK_LIMIT_BYTES", str(96 * 1024 * 1024)))
    print(f"[memory-leak] no cgroup limit found, using fallback {fallback} bytes", flush=True)
    return fallback


def main() -> None:
    limit = read_cgroup_memory_limit_bytes()
    target = int(limit * TARGET_RATIO)
    print(
        f"[memory-leak] cgroup_limit={limit} target_bytes={target} "
        f"ratio={TARGET_RATIO} chunk={CHUNK_BYTES} sleep={STEP_SLEEP}s",
        flush=True,
    )

    chunks: list[bytearray] = []
    allocated = 0

    while True:
        if allocated >= target:
            print(
                f"[memory-leak] reached target (~{allocated} bytes). Holding for stability; "
                "restart container to reset.",
                flush=True,
            )
            time.sleep(60)
            continue

        step = min(CHUNK_BYTES, target - allocated)
        # Touch pages so RSS tracks allocation
        buf = bytearray(step)
        buf[0] = 1
        buf[-1] = 2
        chunks.append(buf)
        allocated += step
        print(f"[memory-leak] allocated_total={allocated} ({100 * allocated / limit:.1f}% of limit)", flush=True)
        time.sleep(STEP_SLEEP)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
