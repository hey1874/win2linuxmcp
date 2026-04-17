#!/usr/bin/env python3
"""
Board Dev MCP Server
SSH-based development tools for Linux dev boards (Raspberry Pi, Jetson, RK3588, etc.)
"""

import io
import json
import os
import select
import socket
import stat
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import paramiko
from mcp.server.fastmcp import FastMCP

try:
    import serial as pyserial
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False

mcp = FastMCP("board-dev")

# ─── Session storage ──────────────────────────────────────────────────────────

@dataclass
class BgJob:
    job_id: str
    command: str
    pid: Optional[int]
    log_path: str          # remote log file path on board
    started_at: float
    channel: Optional[object] = None  # paramiko channel for kill

@dataclass
class SSHSession:
    session_id: str
    name: str
    host: str
    port: int
    username: str
    client: paramiko.SSHClient
    _sftp: Optional[paramiko.SFTPClient] = None
    jobs: dict = field(default_factory=dict)  # job_id -> BgJob

    @property
    def sftp(self) -> paramiko.SFTPClient:
        if self._sftp is None or self._sftp.get_channel().closed:
            self._sftp = self.client.open_sftp()
        return self._sftp

_sessions: dict[str, SSHSession] = {}
_serial_ports: dict[str, object] = {}  # serial_id -> Serial instance

# Local session log dir
LOG_DIR = Path(os.environ.get("BOARD_MCP_LOG_DIR", Path.home() / ".board_mcp_logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_session(session_id: str) -> SSHSession:
    s = _sessions.get(session_id)
    if s is None:
        raise ValueError(f"Session '{session_id}' not found. Use connect() first.")
    transport = s.client.get_transport()
    if transport is None or not transport.is_active():
        raise ValueError(f"Session '{session_id}' disconnected. Use connect() to reconnect.")
    return s


def _run(session: SSHSession, command: str, timeout: int = 60) -> tuple[str, str, int]:
    """Execute command, return (stdout, stderr, exit_code)."""
    stdin, stdout, stderr = session.client.exec_command(command, timeout=timeout)
    stdout_data = stdout.read().decode("utf-8", errors="replace")
    stderr_data = stderr.read().decode("utf-8", errors="replace")
    exit_code = stdout.channel.recv_exit_status()
    return stdout_data, stderr_data, exit_code


def _log_event(session: SSHSession, event_type: str, data: dict):
    log_file = LOG_DIR / f"{session.session_id}.jsonl"
    entry = {"ts": time.time(), "type": event_type, "session": session.name, **data}
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ─── Connection tools ─────────────────────────────────────────────────────────

@mcp.tool()
def connect(
    host: str,
    username: str = "root",
    port: int = 22,
    password: str = None,
    key_path: str = None,
    name: str = None,
    timeout: int = 10,
) -> dict:
    """
    Connect to a Linux dev board via SSH.

    Args:
        host: IP address or hostname of the board
        username: SSH username (default: root)
        port: SSH port (default: 22)
        password: SSH password (optional)
        key_path: Path to SSH private key file (optional)
        name: Friendly name for this connection (default: host)
        timeout: Connection timeout in seconds

    Returns:
        dict with session_id and board info
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    connect_kwargs: dict = dict(
        hostname=host,
        port=port,
        username=username,
        timeout=timeout,
        allow_agent=True,
        look_for_keys=True,
    )
    if password:
        connect_kwargs["password"] = password
    if key_path:
        connect_kwargs["key_filename"] = os.path.expanduser(key_path)

    client.connect(**connect_kwargs)

    session_id = str(uuid.uuid4())[:8]
    session_name = name or host
    session = SSHSession(
        session_id=session_id,
        name=session_name,
        host=host,
        port=port,
        username=username,
        client=client,
    )
    _sessions[session_id] = session

    # Quick board info
    stdout, _, _ = _run(session, "uname -a && hostname && uptime", timeout=5)
    _log_event(session, "connect", {"host": host, "username": username})

    return {
        "session_id": session_id,
        "name": session_name,
        "host": host,
        "username": username,
        "board_info": stdout.strip(),
        "log_file": str(LOG_DIR / f"{session_id}.jsonl"),
    }


@mcp.tool()
def list_sessions() -> list[dict]:
    """List all active SSH sessions."""
    result = []
    for sid, s in _sessions.items():
        transport = s.client.get_transport()
        active = transport is not None and transport.is_active()
        result.append({
            "session_id": sid,
            "name": s.name,
            "host": s.host,
            "username": s.username,
            "active": active,
            "bg_jobs": len(s.jobs),
        })
    return result


@mcp.tool()
def disconnect(session_id: str) -> str:
    """Close an SSH session and clean up resources."""
    s = _get_session(session_id)
    _log_event(s, "disconnect", {})
    if s._sftp:
        try:
            s._sftp.close()
        except Exception:
            pass
    s.client.close()
    del _sessions[session_id]
    return f"Session '{s.name}' ({session_id}) closed."


# ─── Command execution ────────────────────────────────────────────────────────

@mcp.tool()
def run(session_id: str, command: str, timeout: int = 60, workdir: str = None) -> dict:
    """
    Execute a command on the board and return output.

    Args:
        session_id: Session ID from connect()
        command: Shell command to run
        timeout: Timeout in seconds (default: 60)
        workdir: Working directory to cd into first

    Returns:
        dict with stdout, stderr, exit_code
    """
    s = _get_session(session_id)
    full_cmd = f"cd {workdir} && {command}" if workdir else command
    stdout, stderr, exit_code = _run(s, full_cmd, timeout=timeout)
    _log_event(s, "run", {"command": command, "exit_code": exit_code})
    return {
        "command": command,
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "success": exit_code == 0,
    }


@mcp.tool()
def run_background(
    session_id: str,
    command: str,
    log_dir: str = "/tmp",
    workdir: str = None,
) -> dict:
    """
    Run a command in the background on the board. Output is saved to a log file.

    Args:
        session_id: Session ID from connect()
        command: Shell command to run in background
        log_dir: Directory on board to store output log (default: /tmp)
        workdir: Working directory on board

    Returns:
        dict with job_id, pid, log_path
    """
    s = _get_session(session_id)
    job_id = str(uuid.uuid4())[:6]
    log_path = f"{log_dir}/board_mcp_{job_id}.log"

    cd = f"cd {workdir} && " if workdir else ""
    # Run detached, save stdout+stderr to log, also capture PID
    wrapper = (
        f"({cd}{command}) > {log_path} 2>&1 & "
        f"echo $!"
    )
    stdout, stderr, exit_code = _run(s, wrapper, timeout=10)
    pid_str = stdout.strip()
    try:
        pid = int(pid_str)
    except ValueError:
        pid = None

    job = BgJob(
        job_id=job_id,
        command=command,
        pid=pid,
        log_path=log_path,
        started_at=time.time(),
    )
    s.jobs[job_id] = job
    _log_event(s, "bg_start", {"job_id": job_id, "command": command, "pid": pid})

    return {
        "job_id": job_id,
        "pid": pid,
        "log_path": log_path,
        "message": f"Started background job {job_id} (PID {pid}). Use job_output() to read logs.",
    }


@mcp.tool()
def job_output(session_id: str, job_id: str, lines: int = 80) -> dict:
    """
    Read output from a background job.

    Args:
        session_id: Session ID from connect()
        job_id: Job ID from run_background()
        lines: Number of lines from end of log to return (default: 80)

    Returns:
        dict with output, pid_running status
    """
    s = _get_session(session_id)
    job = s.jobs.get(job_id)
    if not job:
        raise ValueError(f"Job '{job_id}' not found in session '{session_id}'.")

    # Check if process is still running
    pid_running = False
    if job.pid:
        stdout, _, rc = _run(s, f"kill -0 {job.pid} 2>/dev/null && echo running || echo stopped")
        pid_running = "running" in stdout

    # Read log file
    stdout, stderr, rc = _run(s, f"tail -n {lines} {job.log_path} 2>/dev/null || echo '[log not found]'")

    return {
        "job_id": job_id,
        "command": job.command,
        "pid": job.pid,
        "running": pid_running,
        "log_path": job.log_path,
        "output": stdout,
    }


@mcp.tool()
def list_jobs(session_id: str) -> list[dict]:
    """List all background jobs for a session."""
    s = _get_session(session_id)
    result = []
    for job in s.jobs.values():
        pid_running = False
        if job.pid:
            stdout, _, _ = _run(s, f"kill -0 {job.pid} 2>/dev/null && echo running || echo stopped")
            pid_running = "running" in stdout
        result.append({
            "job_id": job.job_id,
            "command": job.command,
            "pid": job.pid,
            "running": pid_running,
            "log_path": job.log_path,
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(job.started_at)),
        })
    return result


@mcp.tool()
def kill_job(session_id: str, job_id: str, signal: int = 15) -> str:
    """
    Kill a background job.

    Args:
        session_id: Session ID
        job_id: Job ID from run_background()
        signal: Signal number (default: 15=SIGTERM, use 9 for SIGKILL)
    """
    s = _get_session(session_id)
    job = s.jobs.get(job_id)
    if not job:
        raise ValueError(f"Job '{job_id}' not found.")
    if not job.pid:
        return f"Job {job_id} has no PID recorded."
    stdout, stderr, rc = _run(s, f"kill -{signal} {job.pid} 2>&1")
    _log_event(s, "kill_job", {"job_id": job_id, "pid": job.pid, "signal": signal})
    return f"Sent signal {signal} to PID {job.pid}: {stdout or stderr or 'OK'}"


# ─── Log and process tools ────────────────────────────────────────────────────

@mcp.tool()
def read_log(session_id: str, path: str, lines: int = 100) -> str:
    """
    Read the last N lines of a log file on the board.

    Args:
        session_id: Session ID
        path: Path to log file on board (e.g. /var/log/syslog, /tmp/app.log)
        lines: Number of lines from end (default: 100)
    """
    s = _get_session(session_id)
    stdout, stderr, rc = _run(s, f"tail -n {lines} {path} 2>&1")
    return stdout or stderr


@mcp.tool()
def journalctl(
    session_id: str,
    unit: str = None,
    lines: int = 80,
    since: str = None,
    priority: str = None,
) -> str:
    """
    Read systemd journal logs on the board.

    Args:
        session_id: Session ID
        unit: systemd unit name (e.g. 'nginx', 'myapp.service') — omit for all
        lines: Number of lines (default: 80)
        since: Time filter like '10 minutes ago' or '2024-01-01'
        priority: Log priority: emerg/alert/crit/err/warning/notice/info/debug
    """
    s = _get_session(session_id)
    cmd = f"journalctl -n {lines} --no-pager"
    if unit:
        cmd += f" -u {unit}"
    if since:
        cmd += f" --since '{since}'"
    if priority:
        cmd += f" -p {priority}"
    stdout, stderr, _ = _run(s, cmd, timeout=15)
    return stdout or stderr


@mcp.tool()
def list_processes(session_id: str, filter: str = None) -> str:
    """
    List running processes on the board.

    Args:
        session_id: Session ID
        filter: Optional grep filter string
    """
    s = _get_session(session_id)
    cmd = "ps aux --sort=-%cpu | head -40"
    if filter:
        cmd = f"ps aux | grep -i '{filter}' | grep -v grep"
    stdout, _, _ = _run(s, cmd)
    return stdout


@mcp.tool()
def service_ctl(session_id: str, action: str, unit: str) -> dict:
    """
    Control a systemd service on the board.

    Args:
        session_id: Session ID
        action: start / stop / restart / status / enable / disable / logs
        unit: Service name (e.g. 'nginx', 'myapp.service')
    """
    s = _get_session(session_id)
    if action == "logs":
        cmd = f"journalctl -u {unit} -n 60 --no-pager"
    else:
        cmd = f"systemctl {action} {unit}"
    stdout, stderr, rc = _run(s, cmd, timeout=30)
    return {"action": action, "unit": unit, "output": stdout or stderr, "exit_code": rc}


# ─── File tools ───────────────────────────────────────────────────────────────

@mcp.tool()
def read_file(session_id: str, remote_path: str, max_kb: int = 256) -> str:
    """
    Read content of a file on the board.

    Args:
        session_id: Session ID
        remote_path: Full path to file on board
        max_kb: Maximum kilobytes to read (default: 256)
    """
    s = _get_session(session_id)
    stdout, stderr, rc = _run(s, f"head -c {max_kb * 1024} {remote_path} 2>&1")
    return stdout or stderr


@mcp.tool()
def write_file(session_id: str, remote_path: str, content: str) -> str:
    """
    Write content to a file on the board via SFTP.

    Args:
        session_id: Session ID
        remote_path: Full path on board
        content: File content to write
    """
    s = _get_session(session_id)
    with s.sftp.open(remote_path, "w") as f:
        f.write(content)
    _log_event(s, "write_file", {"path": remote_path, "bytes": len(content)})
    return f"Written {len(content)} bytes to {remote_path}"


@mcp.tool()
def upload_file(session_id: str, local_path: str, remote_path: str) -> dict:
    """
    Upload a local file to the board via SFTP.

    Args:
        session_id: Session ID
        local_path: Path on your local machine
        remote_path: Destination path on the board
    """
    s = _get_session(session_id)
    local_path = os.path.expanduser(local_path)
    size = os.path.getsize(local_path)
    s.sftp.put(local_path, remote_path)
    _log_event(s, "upload", {"local": local_path, "remote": remote_path, "bytes": size})
    return {"local": local_path, "remote": remote_path, "bytes": size, "success": True}


@mcp.tool()
def download_file(session_id: str, remote_path: str, local_path: str) -> dict:
    """
    Download a file from the board to local machine via SFTP.

    Args:
        session_id: Session ID
        remote_path: File path on the board
        local_path: Destination path on your local machine
    """
    s = _get_session(session_id)
    local_path = os.path.expanduser(local_path)
    os.makedirs(os.path.dirname(os.path.abspath(local_path)), exist_ok=True)
    s.sftp.get(remote_path, local_path)
    size = os.path.getsize(local_path)
    _log_event(s, "download", {"remote": remote_path, "local": local_path, "bytes": size})
    return {"remote": remote_path, "local": local_path, "bytes": size, "success": True}


@mcp.tool()
def list_dir(session_id: str, remote_path: str, show_hidden: bool = False) -> str:
    """
    List directory contents on the board.

    Args:
        session_id: Session ID
        remote_path: Directory path on board
        show_hidden: Include hidden files (default: False)
    """
    s = _get_session(session_id)
    flag = "-la" if show_hidden else "-l"
    stdout, stderr, _ = _run(s, f"ls {flag} --color=never {remote_path} 2>&1")
    return stdout or stderr


# ─── Git tools ────────────────────────────────────────────────────────────────

@mcp.tool()
def git_clone(
    session_id: str,
    repo_url: str,
    path: str,
    branch: str = None,
    depth: int = None,
) -> dict:
    """
    Clone a git repository on the board.

    Args:
        session_id: Session ID
        repo_url: Git repository URL (https or ssh)
        path: Destination directory on board
        branch: Branch to checkout (optional)
        depth: Shallow clone depth, e.g. 1 for latest only (optional)
    """
    s = _get_session(session_id)
    cmd = f"git clone {repo_url} {path}"
    if branch:
        cmd += f" -b {branch}"
    if depth:
        cmd += f" --depth {depth}"
    stdout, stderr, rc = _run(s, cmd, timeout=120)
    return {
        "repo": repo_url,
        "path": path,
        "stdout": stdout,
        "stderr": stderr,
        "success": rc == 0,
    }


@mcp.tool()
def git_run(session_id: str, path: str, git_command: str) -> dict:
    """
    Run any git command in a directory on the board.
    Examples: 'pull', 'status', 'log --oneline -10', 'checkout main', 'diff HEAD'

    Args:
        session_id: Session ID
        path: Path to git repo on board
        git_command: Git subcommand and args (without 'git' prefix)
    """
    s = _get_session(session_id)
    stdout, stderr, rc = _run(s, f"git -C {path} {git_command}", timeout=60)
    return {
        "command": f"git {git_command}",
        "path": path,
        "stdout": stdout,
        "stderr": stderr,
        "success": rc == 0,
    }


# ─── Board info tools ─────────────────────────────────────────────────────────

@mcp.tool()
def board_info(session_id: str) -> dict:
    """
    Get comprehensive system information from the board.
    Returns: OS, kernel, CPU, memory, disk, uptime, temperature.
    """
    s = _get_session(session_id)
    queries = {
        "hostname": "hostname",
        "os": "cat /etc/os-release 2>/dev/null | head -5 || uname -a",
        "kernel": "uname -r",
        "arch": "uname -m",
        "uptime": "uptime -p 2>/dev/null || uptime",
        "cpu": "cat /proc/cpuinfo | grep 'model name\\|Hardware\\|Revision' | head -5",
        "memory": "free -h",
        "disk": "df -h --total 2>/dev/null | tail -3",
        "temp": "cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | head -5 || echo 'N/A'",
        "network": "ip -brief addr show 2>/dev/null || ifconfig 2>/dev/null | grep -E 'inet|Link'",
    }
    results = {}
    for key, cmd in queries.items():
        stdout, _, _ = _run(s, cmd, timeout=5)
        results[key] = stdout.strip()

    # Parse temp (in millidegrees)
    temps = results.get("temp", "N/A")
    if temps != "N/A":
        try:
            vals = [int(t) / 1000 for t in temps.splitlines() if t.isdigit()]
            results["temp"] = [f"{v:.1f}°C" for v in vals]
        except Exception:
            pass

    return results


@mcp.tool()
def port_forward(
    session_id: str,
    remote_port: int,
    local_port: int = None,
    remote_host: str = "localhost",
) -> dict:
    """
    Set up SSH local port forwarding (board port → localhost).
    Useful for accessing web servers, GDB servers, etc. on the board.

    Args:
        session_id: Session ID
        remote_port: Port on the board to forward
        local_port: Local port to bind (default: same as remote_port)
        remote_host: Host on the board side (default: localhost)

    Returns:
        dict with forwarding info and how to use it
    """
    s = _get_session(session_id)
    local_port = local_port or remote_port
    transport = s.client.get_transport()
    transport.request_port_forward("", local_port)

    def _forward_worker():
        while True:
            try:
                chan = transport.accept(1)
                if chan is None:
                    continue
                # Connect to remote side
                sock = socket.create_connection((remote_host, remote_port))
                thr = threading.Thread(
                    target=_tunnel, args=(chan, sock), daemon=True
                )
                thr.start()
            except Exception:
                break

    def _tunnel(chan, sock):
        while True:
            r, _, _ = select.select([chan, sock], [], [], 1)
            if chan in r:
                data = chan.recv(1024)
                if not data:
                    break
                sock.send(data)
            if sock in r:
                data = sock.recv(1024)
                if not data:
                    break
                chan.send(data)
        chan.close()
        sock.close()

    t = threading.Thread(target=_forward_worker, daemon=True)
    t.start()

    return {
        "status": "forwarding",
        "board_port": remote_port,
        "local_port": local_port,
        "usage": f"Access at http://localhost:{local_port} or tcp://localhost:{local_port}",
    }


# ─── Deploy workflow ──────────────────────────────────────────────────────────

@mcp.tool()
def deploy_binary(
    session_id: str,
    local_binary: str,
    remote_path: str,
    run_after: bool = False,
    run_args: str = "",
    restart_service: str = None,
) -> dict:
    """
    Upload a compiled binary to the board and optionally run it.
    Typical cross-compile workflow: build locally → deploy to board → run.

    Args:
        session_id: Session ID
        local_binary: Path to compiled binary on local machine
        remote_path: Destination path on board (e.g. /usr/local/bin/myapp)
        run_after: Run the binary after upload (default: False)
        run_args: Arguments to pass when running
        restart_service: systemd service name to restart instead of direct run
    """
    s = _get_session(session_id)
    upload_result = upload_file(session_id, local_binary, remote_path)
    _run(s, f"chmod +x {remote_path}")

    result = {**upload_result, "chmod": "ok"}

    if restart_service:
        svc = service_ctl(session_id, "restart", restart_service)
        result["service"] = svc
    elif run_after:
        job = run_background(session_id, f"{remote_path} {run_args}".strip())
        result["job"] = job

    return result


# ─── Serial / UART tools ──────────────────────────────────────────────────────

@mcp.tool()
def serial_connect(port: str, baud_rate: int = 115200, name: str = None) -> dict:
    """
    Connect to a dev board via serial/UART console (USB-to-serial adapter).

    Args:
        port: Serial port path, e.g. COM3 (Windows) or /dev/ttyUSB0 (Linux)
        baud_rate: Baud rate (default: 115200)
        name: Friendly name for this connection

    Returns:
        dict with serial_id
    """
    if not HAS_SERIAL:
        raise RuntimeError("pyserial not installed. Run: pip install pyserial")
    ser = pyserial.Serial(port, baud_rate, timeout=2)
    serial_id = str(uuid.uuid4())[:6]
    _serial_ports[serial_id] = {"ser": ser, "port": port, "baud": baud_rate, "name": name or port}
    return {"serial_id": serial_id, "port": port, "baud_rate": baud_rate, "connected": True}


@mcp.tool()
def serial_send(serial_id: str, command: str, wait_ms: int = 500) -> str:
    """
    Send a command to the serial console and read response.

    Args:
        serial_id: Serial connection ID from serial_connect()
        command: Command to send (newline appended automatically)
        wait_ms: Milliseconds to wait for response (default: 500)
    """
    if not HAS_SERIAL:
        raise RuntimeError("pyserial not installed.")
    info = _serial_ports.get(serial_id)
    if not info:
        raise ValueError(f"Serial port '{serial_id}' not found.")
    ser = info["ser"]
    ser.write((command + "\n").encode("utf-8"))
    time.sleep(wait_ms / 1000)
    output = b""
    while ser.in_waiting:
        output += ser.read(ser.in_waiting)
        time.sleep(0.05)
    return output.decode("utf-8", errors="replace")


@mcp.tool()
def serial_read(serial_id: str, timeout_sec: float = 2.0) -> str:
    """Read pending output from the serial console."""
    if not HAS_SERIAL:
        raise RuntimeError("pyserial not installed.")
    info = _serial_ports.get(serial_id)
    if not info:
        raise ValueError(f"Serial port '{serial_id}' not found.")
    ser = info["ser"]
    deadline = time.time() + timeout_sec
    output = b""
    while time.time() < deadline:
        if ser.in_waiting:
            output += ser.read(ser.in_waiting)
        else:
            time.sleep(0.1)
    return output.decode("utf-8", errors="replace")


@mcp.tool()
def serial_disconnect(serial_id: str) -> str:
    """Close a serial connection."""
    info = _serial_ports.pop(serial_id, None)
    if not info:
        return f"Serial '{serial_id}' not found."
    info["ser"].close()
    return f"Serial port {info['port']} closed."


@mcp.tool()
def list_serial_ports() -> list[dict]:
    """List available serial ports on this machine."""
    if not HAS_SERIAL:
        raise RuntimeError("pyserial not installed.")
    from serial.tools import list_ports
    return [
        {"port": p.device, "description": p.description, "hwid": p.hwid}
        for p in list_ports.comports()
    ]


# ─── Session activity log ─────────────────────────────────────────────────────

@mcp.tool()
def session_log(session_id: str, lines: int = 50) -> list[dict]:
    """
    Read the local activity log for a session.
    Shows all commands run, files transferred, and connections.

    Args:
        session_id: Session ID
        lines: Number of recent entries (default: 50)
    """
    log_file = LOG_DIR / f"{session_id}.jsonl"
    if not log_file.exists():
        return []
    entries = []
    with open(log_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries[-lines:]


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
