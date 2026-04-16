"""SSH Diagnostic MCP Server — hardcoded read-only system commands for data collection.

Unlike the general ssh_server.py (which exposes an allowlist-gated exec_command
for the SSH agent), this server **does not let the AI choose commands**.
Every tool maps to a single, fixed shell command.  The AI can only decide
*which host* to query and *which pre-built function* to call.

Designed to be consumed by the data_collector agent for L5/L6 infrastructure
layer investigation.
"""

from __future__ import annotations

import json
import os
import subprocess

from fastmcp import FastMCP

SSH_CONFIG_JSON = os.environ.get("SSH_CONFIG_JSON", "[]")
SSH_TIMEOUT = int(os.environ.get("SSH_TIMEOUT", "10"))

mcp = FastMCP("SSH Diagnostic Data Collector")


# ---------------------------------------------------------------------------
# SSH helpers (shared infrastructure)
# ---------------------------------------------------------------------------

def _load_hosts() -> list[dict]:
    try:
        return json.loads(SSH_CONFIG_JSON)
    except (json.JSONDecodeError, TypeError):
        return []


def _get_host(hostname: str) -> dict | None:
    for h in _load_hosts():
        if h.get("name") == hostname or h.get("hostname") == hostname:
            return h
    return None


def _ssh_exec(host: dict, command: str) -> tuple[str, str, int]:
    """Run a fixed command on the remote host via system SSH."""
    ssh_args = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=5",
        "-o", "BatchMode=yes",
        "-p", str(host.get("port", 22)),
    ]
    key_path = host.get("key_path", "")
    if key_path:
        ssh_args.extend(["-i", os.path.expanduser(key_path)])

    user = host.get("username", "sre-readonly")
    ssh_args.append(f"{user}@{host['hostname']}")
    ssh_args.append(command)

    try:
        result = subprocess.run(ssh_args, capture_output=True, text=True, timeout=SSH_TIMEOUT)
        return result.stdout[:10000], result.stderr[:2000], result.returncode
    except subprocess.TimeoutExpired:
        return "", f"Command timed out after {SSH_TIMEOUT}s", -1
    except Exception as e:
        return "", str(e), -1


def _run(hostname: str, command: str) -> str:
    """Validate host, execute fixed command, return JSON result."""
    host = _get_host(hostname)
    if not host:
        available = [h.get("name", h.get("hostname", "")) for h in _load_hosts()]
        return json.dumps({
            "status": "error",
            "error": f"Host '{hostname}' not found",
            "available_hosts": available,
        })

    stdout, stderr, rc = _ssh_exec(host, command)
    return json.dumps({
        "status": "success" if rc == 0 else "command_failed",
        "hostname": hostname,
        "command": command,
        "exit_code": rc,
        "stdout": stdout,
        "stderr": stderr if stderr else "",
    })


# ---------------------------------------------------------------------------
# Hardcoded diagnostic tools — AI cannot change the commands
# ---------------------------------------------------------------------------

@mcp.tool()
def list_diagnostic_hosts() -> str:
    """List all SSH hosts available for diagnostic data collection.

    Returns:
        JSON with host names and connection details.
    """
    hosts = _load_hosts()
    sanitized = [
        {"name": h.get("name", ""), "hostname": h.get("hostname", ""), "port": h.get("port", 22)}
        for h in hosts
    ]
    return json.dumps({"status": "success", "host_count": len(sanitized), "hosts": sanitized})


@mcp.tool()
def get_processes(hostname: str) -> str:
    """Get the full process list from a remote host.

    Runs: ps -ef

    Args:
        hostname: Target host name (must be in configured hosts list)
    """
    return _run(hostname, "ps -ef")


@mcp.tool()
def get_top_cpu_processes(hostname: str) -> str:
    """Get the top 20 processes sorted by CPU usage.

    Runs: ps aux --sort=-%cpu | head -21

    Args:
        hostname: Target host name
    """
    return _run(hostname, "ps aux --sort=-%cpu | head -21")


@mcp.tool()
def get_top_memory_processes(hostname: str) -> str:
    """Get the top 20 processes sorted by memory usage.

    Runs: ps aux --sort=-%mem | head -21

    Args:
        hostname: Target host name
    """
    return _run(hostname, "ps aux --sort=-%mem | head -21")


@mcp.tool()
def get_network_connections(hostname: str) -> str:
    """Get all current network connections with process info.

    Runs: ss -tunap

    Args:
        hostname: Target host name
    """
    return _run(hostname, "ss -tunap")


@mcp.tool()
def get_listening_ports(hostname: str) -> str:
    """Get all listening TCP ports and their owning processes.

    Runs: ss -tlnp

    Args:
        hostname: Target host name
    """
    return _run(hostname, "ss -tlnp")


@mcp.tool()
def get_network_stats(hostname: str) -> str:
    """Get network socket statistics summary.

    Runs: ss -s

    Args:
        hostname: Target host name
    """
    return _run(hostname, "ss -s")


@mcp.tool()
def get_memory_info(hostname: str) -> str:
    """Get memory usage summary.

    Runs: free -h

    Args:
        hostname: Target host name
    """
    return _run(hostname, "free -h")


@mcp.tool()
def get_disk_usage(hostname: str) -> str:
    """Get disk usage for all mounted filesystems.

    Runs: df -h

    Args:
        hostname: Target host name
    """
    return _run(hostname, "df -h")


@mcp.tool()
def get_disk_inodes(hostname: str) -> str:
    """Get inode usage for all mounted filesystems.

    Runs: df -i

    Args:
        hostname: Target host name
    """
    return _run(hostname, "df -i")


@mcp.tool()
def get_system_load(hostname: str) -> str:
    """Get system uptime and load averages.

    Runs: uptime

    Args:
        hostname: Target host name
    """
    return _run(hostname, "uptime")


@mcp.tool()
def get_vmstat(hostname: str) -> str:
    """Get virtual memory statistics (3 samples, 1-second interval).

    Runs: vmstat 1 3

    Args:
        hostname: Target host name
    """
    return _run(hostname, "vmstat 1 3")


@mcp.tool()
def get_cpu_info(hostname: str) -> str:
    """Get CPU architecture and core information.

    Runs: lscpu

    Args:
        hostname: Target host name
    """
    return _run(hostname, "lscpu")


@mcp.tool()
def get_dmesg(hostname: str) -> str:
    """Get recent kernel messages (last 50 lines) for hardware/driver errors.

    Runs: dmesg --time-format iso -T | tail -50

    Args:
        hostname: Target host name
    """
    return _run(hostname, "dmesg --time-format iso -T | tail -50")


@mcp.tool()
def get_os_info(hostname: str) -> str:
    """Get operating system release information.

    Runs: cat /etc/os-release

    Args:
        hostname: Target host name
    """
    return _run(hostname, "cat /etc/os-release")


@mcp.tool()
def get_service_status(hostname: str, service: str) -> str:
    """Get the status of a specific systemd service.

    Runs: systemctl status <service> --no-pager

    Args:
        hostname: Target host name
        service: Service unit name (e.g. 'nginx', 'docker', 'postgresql')
    """
    safe = service.strip()
    if not safe or not all(c.isalnum() or c in "._-@" for c in safe):
        return json.dumps({"status": "error", "error": f"Invalid service name: '{service}'"})
    return _run(hostname, f"systemctl status {safe} --no-pager")


@mcp.tool()
def get_service_logs(hostname: str, service: str, lines: int = 50) -> str:
    """Get recent journal logs for a specific systemd service.

    Runs: journalctl -u <service> --no-pager -n <lines>

    Args:
        hostname: Target host name
        service: Service unit name
        lines: Number of recent log lines (default: 50, max: 200)
    """
    safe = service.strip()
    if not safe or not all(c.isalnum() or c in "._-@" for c in safe):
        return json.dumps({"status": "error", "error": f"Invalid service name: '{service}'"})
    n = min(max(lines, 1), 200)
    return _run(hostname, f"journalctl -u {safe} --no-pager -n {n}")


if __name__ == "__main__":
    mcp.run()
