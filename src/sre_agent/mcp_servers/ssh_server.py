"""SSH MCP Server - provides read-only remote command execution via FastMCP.

Enforces a strict command whitelist to ensure only diagnostic (read-only)
commands can be executed on target servers.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import yaml
from fastmcp import FastMCP

SSH_CONFIG_JSON = os.environ.get("SSH_CONFIG_JSON", "[]")
SSH_TIMEOUT = int(os.environ.get("SSH_TIMEOUT", "10"))
ALLOWLIST_PATH = os.environ.get("SSH_ALLOWLIST_PATH", "configs/ssh_allowlist.yaml")

mcp = FastMCP("SSH Diagnostic Server")

BLOCKED_CHARS = [";", "&&", "||", "|", ">", ">>", "<", "`", "$(", "\\n", "\\r"]


def _load_allowlist() -> dict:
    path = Path(ALLOWLIST_PATH)
    if not path.exists():
        return {"allowed_commands": {}, "blocked_chars": BLOCKED_CHARS}
    with open(path) as f:
        return yaml.safe_load(f) or {"allowed_commands": {}, "blocked_chars": BLOCKED_CHARS}


def _load_hosts() -> list[dict]:
    try:
        return json.loads(SSH_CONFIG_JSON)
    except (json.JSONDecodeError, TypeError):
        return []


def _contains_blocked_chars(command: str, blocked: list[str]) -> str | None:
    for char in blocked:
        if char in command:
            return char
    return None


def _validate_command(command: str, allowlist: dict) -> tuple[bool, str]:
    """Validate a command against the allowlist. Returns (is_valid, reason)."""
    blocked = allowlist.get("blocked_chars", BLOCKED_CHARS)
    found = _contains_blocked_chars(command, blocked)
    if found:
        return False, f"Blocked character/sequence found: '{found}'"

    all_patterns = []
    for category in allowlist.get("allowed_commands", {}).values():
        for entry in category:
            all_patterns.append(entry)

    command_stripped = command.strip()

    for entry in all_patterns:
        pattern = entry["pattern"]

        if "{" not in pattern:
            if command_stripped == pattern:
                return True, "exact match"
        else:
            params = entry.get("params", {})
            regex_pattern = re.escape(pattern)
            for param_name, param_spec in params.items():
                placeholder = re.escape("{" + param_name + "}")
                param_regex = param_spec.get("regex", r"[a-zA-Z0-9._-]+")
                regex_pattern = regex_pattern.replace(placeholder, f"({param_regex})")

            if re.fullmatch(regex_pattern, command_stripped):
                return True, f"pattern match: {pattern}"

    return False, f"Command not in allowlist: '{command_stripped}'"


def _get_host_config(hostname: str, hosts: list[dict]) -> dict | None:
    for host in hosts:
        if host.get("name") == hostname or host.get("hostname") == hostname:
            return host
    return None


def _execute_ssh(host: dict, command: str) -> tuple[str, str, int]:
    """Execute command via SSH subprocess (using system ssh for simplicity).

    Falls back to paramiko if the 'paramiko' extra is installed.
    """
    ssh_args = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=5",
        "-o", f"BatchMode=yes",
        "-p", str(host.get("port", 22)),
    ]

    key_path = host.get("key_path", "")
    if key_path:
        expanded = os.path.expanduser(key_path)
        ssh_args.extend(["-i", expanded])

    user = host.get("username", "sre-readonly")
    target = f"{user}@{host['hostname']}"
    ssh_args.append(target)
    ssh_args.append(command)

    try:
        result = subprocess.run(
            ssh_args,
            capture_output=True,
            text=True,
            timeout=SSH_TIMEOUT,
        )
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return "", f"Command timed out after {SSH_TIMEOUT}s", -1
    except Exception as e:
        return "", str(e), -1


@mcp.tool()
def exec_command(hostname: str, command: str) -> str:
    """Execute a read-only diagnostic command on a remote server via SSH.

    Commands are validated against a strict whitelist before execution.
    Only diagnostic (read-only) commands are allowed.

    Args:
        hostname: Target host name or IP (must be in configured hosts list)
        command: The diagnostic command to run (must match allowlist)

    Returns:
        JSON with command output, stderr, exit code, and validation status.

    Allowed command categories:
        - Process: ps -ef, ps aux, top -bn1
        - Network: ss -tlnp, ss -s, ss -tunap, netstat -an, netstat -tlnp
        - Disk: df -h, df -i
        - Memory/CPU: free -h, uptime, vmstat 1 3, cat /proc/loadavg, cat /proc/meminfo
        - Service: systemctl status <service>, systemctl is-active <service>,
                   journalctl -u <service> --no-pager -n <lines>
        - System: uname -a, dmesg --time-format iso -T, cat /etc/os-release, lscpu
    """
    allowlist = _load_allowlist()
    is_valid, reason = _validate_command(command, allowlist)

    if not is_valid:
        return json.dumps({
            "status": "rejected",
            "hostname": hostname,
            "command": command,
            "reason": reason,
            "hint": "Only whitelisted read-only commands are allowed. Check the allowlist configuration.",
        })

    hosts = _load_hosts()
    host = _get_host_config(hostname, hosts)

    if not host:
        available = [h.get("name", h.get("hostname", "")) for h in hosts]
        return json.dumps({
            "status": "error",
            "hostname": hostname,
            "error": f"Host '{hostname}' not found in configuration",
            "available_hosts": available,
        })

    stdout, stderr, exit_code = _execute_ssh(host, command)

    return json.dumps({
        "status": "success" if exit_code == 0 else "command_failed",
        "hostname": hostname,
        "command": command,
        "exit_code": exit_code,
        "stdout": stdout[:10000],
        "stderr": stderr[:2000] if stderr else "",
        "validation": reason,
    })


@mcp.tool()
def list_available_hosts() -> str:
    """List all configured SSH hosts that can be targeted for diagnostics.

    Returns:
        JSON with list of available hostnames and their connection details.
    """
    hosts = _load_hosts()
    sanitized = []
    for host in hosts:
        sanitized.append({
            "name": host.get("name", ""),
            "hostname": host.get("hostname", ""),
            "port": host.get("port", 22),
            "username": host.get("username", ""),
        })
    return json.dumps({"status": "success", "host_count": len(sanitized), "hosts": sanitized})


@mcp.tool()
def list_allowed_commands() -> str:
    """List all commands that are allowed to be executed via SSH.

    Returns:
        JSON with categorized list of allowed command patterns.
    """
    allowlist = _load_allowlist()
    categories = {}
    for category, commands in allowlist.get("allowed_commands", {}).items():
        categories[category] = [
            {"pattern": cmd["pattern"], "description": cmd.get("description", "")}
            for cmd in commands
        ]
    return json.dumps({"status": "success", "categories": categories})


if __name__ == "__main__":
    mcp.run()
