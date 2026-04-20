# board-dev MCP Server

基于 MCP（模型上下文协议）的 SSH 开发工具服务器，让 Claude 能够直接操作 Linux 开发板，支持树莓派、Jetson、RK3588 等一切可通过 SSH 访问的设备。

## 功能特性

- **SSH 会话管理** — 连接/断开，支持多会话并发
- **命令执行** — 前台和后台任务，后台任务自动捕获日志
- **文件操作** — 通过 SFTP 读写、上传、下载文件
- **系统信息** — 查看板卡信息、进程、systemd 日志、服务管理
- **Git 工作流** — 在板卡上直接克隆仓库、执行 git 命令
- **二进制部署** — 上传交叉编译产物，自动 chmod，运行或重启服务
- **端口转发** — SSH 本地隧道，将板卡端口映射到本机
- **串口/UART 控制台** — 通过 USB 转串口适配器连接（需安装 pyserial）

## 环境要求

- Python 3.10+
- 依赖：`mcp[cli]`、`paramiko`、`pyserial`（可选）、`cryptography`

## 安装

```bash
cd board_mcp
pip install -r requirements.txt
```

## 在 Claude Code 中使用

在 `.mcp.json` 中添加：

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

## 工具列表

| 工具 | 说明 |
|------|------|
| `connect` | SSH 连接到开发板 |
| `disconnect` | 关闭 SSH 会话 |
| `list_sessions` | 列出所有活跃会话 |
| `run` | 执行命令并返回输出 |
| `run_background` | 后台运行命令，日志写入文件 |
| `job_output` | 读取后台任务日志 |
| `list_jobs` | 列出后台任务 |
| `kill_job` | 终止后台任务 |
| `read_file` | 读取板卡上的文件 |
| `write_file` | 通过 SFTP 写入文件 |
| `upload_file` | 上传本地文件到板卡 |
| `download_file` | 从板卡下载文件到本机 |
| `list_dir` | 列出板卡目录内容 |
| `board_info` | 获取系统信息（OS、CPU、内存、磁盘、温度） |
| `list_processes` | 查看运行中的进程 |
| `read_log` | 读取日志文件末尾 |
| `journalctl` | 读取 systemd journal 日志 |
| `service_ctl` | 启动/停止/重启/查看服务状态 |
| `git_clone` | 在板卡上克隆 git 仓库 |
| `git_run` | 在板卡上执行任意 git 命令 |
| `deploy_binary` | 上传二进制 + chmod + 运行或重启服务 |
| `port_forward` | SSH 本地端口转发 |
| `serial_connect` | 打开串口/UART 控制台 |
| `serial_send` | 发送命令并读取响应 |
| `serial_read` | 读取串口待接收数据 |
| `serial_disconnect` | 关闭串口连接 |
| `list_serial_ports` | 列出可用串口 |
| `session_log` | 读取会话本地操作日志 |

## 许可证

MIT
