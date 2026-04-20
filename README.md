# board-dev MCP Server

An MCP (Model Context Protocol) server that gives Claude SSH-based development tools for Linux single-board computers — Raspberry Pi, Jetson, RK3588, and anything else reachable over SSH.

## Features

- **SSH session management** — connect/disconnect, multiple concurrent sessions
- **Command execution** — foreground and background jobs with log capture
- **File operations** — read, write, upload, download via SFTP
- **System inspection** — board info, processes, journald logs, service control
- **Git workflow** — clone repos and run git commands directly on the board
- **Binary deployment** — upload cross-compiled binaries, chmod, run or restart service
- **Port forwarding** — SSH local tunnel from board port to localhost
- **Serial/UART console** — connect via USB-to-serial adapter (optional pyserial)

## Requirements

- Python 3.10+
- Dependencies: `mcp[cli]`, `paramiko`, `pyserial` (optional), `cryptography`

## Installation

```bash
cd board_mcp
pip install -r requirements.txt
```

## Usage with Claude Code

Add to your `.mcp.json`:

```json
{
  "mcpServers": {
    "board-dev": {
      "command": "python",
      "args": ["/path/to/board_mcp/server.py"],
      "env": {
        "BOARD_MCP_LOG_DIR": "/path/to/logs"
      }
    }
  }
}
```

## Available Tools

| Tool | Description |
|------|-------------|
| `connect` | SSH connect to a board |
| `disconnect` | Close SSH session |
| `list_sessions` | List active sessions |
| `run` | Run command, return output |
| `run_background` | Run command detached, log to file |
| `job_output` | Read background job log |
| `list_jobs` | List background jobs |
| `kill_job` | Kill a background job |
| `read_file` | Read file on board |
| `write_file` | Write file on board via SFTP |
| `upload_file` | Upload local file to board |
| `download_file` | Download file from board |
| `list_dir` | List directory on board |
| `board_info` | OS, CPU, memory, disk, temp |
| `list_processes` | Running processes |
| `read_log` | Tail a log file |
| `journalctl` | Read systemd journal |
| `service_ctl` | start/stop/restart/status a service |
| `git_clone` | Clone repo on board |
| `git_run` | Run any git command on board |
| `deploy_binary` | Upload binary + chmod + run/restart |
| `port_forward` | SSH local port forward |
| `serial_connect` | Open serial/UART console |
| `serial_send` | Send command, read response |
| `serial_read` | Read pending serial output |
| `serial_disconnect` | Close serial connection |
| `list_serial_ports` | List available serial ports |
| `session_log` | Read local activity log for session |

## License

MIT
