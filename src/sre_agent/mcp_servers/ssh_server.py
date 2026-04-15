"""SSH MCP Server - provides read-only remote command execution via FastMCP.

Enforces a strict command whitelist to ensure only diagnostic (read-only)
commands can be executed on target servers.

SSH execution modes (SSH_MODE env var):
  invoke_shell  - PTY interactive shell; sentinel-based completion; works in
                  airgapped/restricted envs where exec_command is blocked (default)
  exec_command  - paramiko exec_command; simpler but may be blocked by server policy
  subprocess    - system SSH binary; no paramiko dependency required
"""

from __future__ import annotations

import importlib.resources
import json
import os
import re
import socket
import subprocess
import time
import uuid
from pathlib import Path

import yaml
from fastmcp import FastMCP

SSH_CONFIG_JSON = os.environ.get("SSH_CONFIG_JSON", "[]")
SSH_TIMEOUT = int(os.environ.get("SSH_TIMEOUT", "10"))
ALLOWLIST_PATH = os.environ.get("SSH_ALLOWLIST_PATH", "")
# invoke_shell | exec_command | subprocess
SSH_MODE = os.environ.get("SSH_MODE", "invoke_shell")
# Wide PTY prevents line-wrapping that would corrupt command output
PTY_WIDTH = int(os.environ.get("SSH_PTY_WIDTH", "220"))
PTY_HEIGHT = int(os.environ.get("SSH_PTY_HEIGHT", "50"))

mcp = FastMCP("SSH Diagnostic Server")

BLOCKED_CHARS = [";", "&&", "||", "|", ">", ">>", "<", "`", "$(", "\\n", "\\r"]

# ANSI escape sequences: colors (CSI m), cursor moves (CSI A-H), OSC (ESC ]), etc.
_ANSI_ESCAPE_RE = re.compile(
    r"\x1B"
    r"(?:"
    r"[@-Z\\-_]"                        # Fe sequences: ESC + single char
    r"|\[[0-?]*[ -/]*[@-~]"            # CSI sequences: ESC [ ... final-byte
    r"|\][^\x07\x1B]*(?:\x07|\x1B\\)"  # OSC sequences: ESC ] ... BEL or ST
    r")"
)

# Shell prompt heuristic: optional (venv) prefix, then path/user chars, ends with $ # >
_PROMPT_RE = re.compile(
    r"^"
    r"(?:\([\w\s.\-]+\)\s*)?"  # optional (venv) / (conda) prefix
    r"[\w@.\[\]~/:\s\-]*"      # user@host:path or [user@host ~]
    r"[\$#>]\s*$"              # terminal $, #, or > with optional trailing space
)


# ---------------------------------------------------------------------------
# Allowlist / config helpers
# ---------------------------------------------------------------------------


def _resolve_allowlist_path() -> Path:
    if ALLOWLIST_PATH:
        return Path(ALLOWLIST_PATH)
    local = Path("configs/ssh_allowlist.yaml")
    if local.exists():
        return local
    return Path(importlib.resources.files("sre_agent.defaults").joinpath("ssh_allowlist.yaml"))


def _load_allowlist() -> dict:
    path = _resolve_allowlist_path()
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


# ---------------------------------------------------------------------------
# Output cleaning helpers
# ---------------------------------------------------------------------------


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes (colors, cursor movements, OSC, etc.)."""
    return _ANSI_ESCAPE_RE.sub("", text)


def _is_prompt_line(line: str) -> bool:
    """Return True if the line looks like a bare shell prompt with no real output."""
    return bool(_PROMPT_RE.match(line.strip())) and line.strip() != ""


def _parse_shell_output(text: str, command: str, sentinel_marker: str) -> tuple[str, int]:
    """Extract command stdout and exit code from a captured PTY shell session.

    The session text is expected to contain (after ANSI stripping):
      [prompt]$ <command>           ← echoed command
      <command output lines>
      [prompt]$ echo "SENTINEL:$?"  ← sentinel echo command
      SENTINEL:0                    ← sentinel output (exit code embedded)
      [prompt]$                     ← trailing prompt

    Returns: (stdout_text, exit_code)
    """
    # Normalise line endings from PTY (\r\n, \r → \n)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")

    # --- Step 1: find sentinel output line (e.g. "SREAGENT_END_abc123:0") ---
    sentinel_output_re = re.compile(rf"^{re.escape(sentinel_marker)}:(\d+)\s*$")
    sentinel_idx = -1
    exit_code = 0

    for i, line in enumerate(lines):
        m = sentinel_output_re.match(line.strip())
        if m:
            sentinel_idx = i
            exit_code = int(m.group(1))
            break

    if sentinel_idx < 0:
        # Sentinel not found (timeout/network issue) — return cleaned full output
        cleaned = "\n".join(l for l in lines if not _is_prompt_line(l))
        return cleaned.strip(), -1

    # --- Step 2: collect lines before the sentinel output ---
    pre_lines = lines[:sentinel_idx]

    # Remove the sentinel echo command line (immediately before sentinel output)
    # and any trailing prompt lines that preceded it
    while pre_lines and (
        (sentinel_marker in pre_lines[-1] and "echo" in pre_lines[-1])
        or _is_prompt_line(pre_lines[-1])
    ):
        pre_lines.pop()

    # --- Step 3: find where the command's own echo starts, skip it ---
    cmd_stripped = command.strip()
    start_idx = 0
    for i, line in enumerate(pre_lines):
        # The echoed command appears in a line like "[prompt]$ ps aux" or just "ps aux"
        if cmd_stripped and cmd_stripped in line:
            start_idx = i + 1
            break

    output_lines = pre_lines[start_idx:]

    # --- Step 4: strip any residual prompt-only lines from output ---
    output_lines = [l for l in output_lines if not _is_prompt_line(l)]

    return "\n".join(output_lines).strip(), exit_code


# ---------------------------------------------------------------------------
# SSH execution backends
# ---------------------------------------------------------------------------


def _build_paramiko_connect_kwargs(host: dict) -> dict:
    kwargs: dict = {
        "hostname": host["hostname"],
        "port": int(host.get("port", 22)),
        "username": host.get("username", "sre-readonly"),
        "timeout": SSH_TIMEOUT,
    }
    key_path = host.get("key_path", "")
    if key_path:
        kwargs["key_filename"] = os.path.expanduser(key_path)
        kwargs["look_for_keys"] = False
        kwargs["allow_agent"] = False
    else:
        kwargs["allow_agent"] = True
        kwargs["look_for_keys"] = True
    return kwargs


def _drain_channel(channel, max_wait: float = 3.0) -> None:
    """Read and discard data until the channel is quiet (initial banner/prompt).

    Waits up to 0.5 s of silence or *max_wait* seconds total.
    Resets channel timeout to 1.0 s on exit (used by main read loop).
    """
    channel.settimeout(0.5)
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        try:
            data = channel.recv(4096)
            if not data:
                break  # Channel closed
        except (socket.timeout, OSError):
            break  # 0.5 s of silence → shell is at prompt
    channel.settimeout(1.0)


def _execute_ssh_invoke_shell(host: dict, command: str) -> tuple[str, str, int]:
    """Execute via paramiko invoke_shell with PTY.

    Works in environments where exec_command is blocked by server-side security
    policy (immediate EOF).  Uses a sentinel UUID to detect command completion.
    """
    try:
        import paramiko
    except ImportError:
        return "", "paramiko not installed. Run: pip install 'sre-agent[ssh]'", -1

    sentinel_token = uuid.uuid4().hex
    sentinel_marker = f"SREAGENT_END_{sentinel_token}"

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    channel = None

    try:
        client.connect(**_build_paramiko_connect_kwargs(host))

        # Wide PTY prevents line-wrapping which would corrupt tabular output
        channel = client.invoke_shell(term="xterm", width=PTY_WIDTH, height=PTY_HEIGHT)

        # Drain initial banner and shell prompt before sending commands
        _drain_channel(channel, max_wait=3.0)

        # Send user command, then immediately queue the sentinel echo.
        # The sentinel captures the exit code of the user command via $?.
        channel.sendall(command.encode("utf-8") + b"\n")
        channel.sendall(f'echo "{sentinel_marker}:$?"\n'.encode("utf-8"))

        # --- Collect output until sentinel output line appears or timeout ---
        raw_bytes = b""
        deadline = time.monotonic() + SSH_TIMEOUT
        sentinel_output_re = re.compile(
            rf"^{re.escape(sentinel_marker)}:\d+\s*$".encode(), re.MULTILINE
        )

        while time.monotonic() < deadline:
            try:
                chunk = channel.recv(8192)
                if not chunk:
                    break  # Channel closed by remote end
                raw_bytes += chunk
                # Check tail of buffer for efficiency (sentinel line is short)
                if sentinel_output_re.search(raw_bytes[-300:]):
                    break
            except socket.timeout:
                pass  # Keep polling
        else:
            return "", f"Command timed out after {SSH_TIMEOUT}s", -1

        raw = raw_bytes.decode("utf-8", errors="replace")
        clean = _strip_ansi(raw)
        stdout, exit_code = _parse_shell_output(clean, command, sentinel_marker)
        return stdout, "", exit_code

    except Exception as e:  # noqa: BLE001  (paramiko raises many specific types)
        return "", str(e), -1
    finally:
        if channel is not None:
            try:
                channel.close()
            except Exception:
                pass
        client.close()


def _execute_ssh_exec_command(host: dict, command: str) -> tuple[str, str, int]:
    """Execute via paramiko exec_command.

    Simpler than invoke_shell but may be blocked by server-side policy
    (returns immediate EOF in some hardened environments).
    """
    try:
        import paramiko
    except ImportError:
        return "", "paramiko not installed. Run: pip install 'sre-agent[ssh]'", -1

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(**_build_paramiko_connect_kwargs(host))
        _stdin, stdout, stderr = client.exec_command(command, timeout=SSH_TIMEOUT)
        _stdin.close()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        exit_code = stdout.channel.recv_exit_status()
        return out, err, exit_code

    except Exception as e:  # noqa: BLE001
        return "", str(e), -1
    finally:
        client.close()


def _execute_ssh_subprocess(host: dict, command: str) -> tuple[str, str, int]:
    """Execute via system SSH binary (no paramiko required)."""
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
        result = subprocess.run(
            ssh_args,
            capture_output=True,
            text=True,
            timeout=SSH_TIMEOUT,
        )
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return "", f"Command timed out after {SSH_TIMEOUT}s", -1
    except Exception as e:  # noqa: BLE001
        return "", str(e), -1


def _execute_ssh(host: dict, command: str) -> tuple[str, str, int]:
    """Dispatch to the appropriate SSH backend based on SSH_MODE."""
    mode = SSH_MODE.lower()
    if mode == "invoke_shell":
        return _execute_ssh_invoke_shell(host, command)
    if mode == "exec_command":
        return _execute_ssh_exec_command(host, command)
    if mode == "subprocess":
        return _execute_ssh_subprocess(host, command)
    return (
        "",
        f"Unknown SSH_MODE '{SSH_MODE}'. Valid options: invoke_shell, exec_command, subprocess",
        -1,
    )


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------


@mcp.tool()
def exec_command(hostname: str, command: str) -> str:
    """Execute a read-only diagnostic command on a remote server via SSH.

    Commands are validated against a strict whitelist before execution.
    Only diagnostic (read-only) commands are allowed.

    SSH execution mode is controlled by the SSH_MODE environment variable:
      invoke_shell  - PTY-based shell with sentinel completion detection (default;
                      works on servers that block exec_command with EOF)
      exec_command  - paramiko exec_command (simpler, may be blocked)
      subprocess    - system SSH binary (no paramiko required)

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
        "ssh_mode": SSH_MODE,
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
    return json.dumps({"status": "success", "categories": categories, "ssh_mode": SSH_MODE})


if __name__ == "__main__":
    mcp.run()
