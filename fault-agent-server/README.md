# Fault Agent Server

集中收集服务器，接收 Fault Agent 上报的主机故障检查结果并存入 SQLite。

## 目录结构

```
fault-agent-server/
└── server.py    # 收集服务器（单文件，零依赖）
```

## 依赖

- Python 3.7+
- 仅使用标准库，零外部依赖

## 快速开始

### 启动服务器

```bash
python3 server.py --port 8000 --db /var/lib/fault-agent-server/reports.db
```

参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--port` / `-p` | 8000 | 监听端口 |
| `--db` | `/var/lib/fault-agent-server/reports.db` | SQLite 数据库路径 |
| `--bind` | `0.0.0.0` | 监听地址 |
| `--bearer-token-path` | (空) | Bearer Token 文件路径，为空则不启用认证 |
| `--debug` | false | 开启调试日志 |

### 带认证启动

```bash
echo "my-secret-token" > /etc/fault-agent-server/auth.token
python3 server.py --port 8000 --bearer-token-path /etc/fault-agent-server/auth.token
```

Agent 端在 `config.yaml` 中配置相同的 token 文件路径：

```yaml
server:
  bearer_token_path: /etc/fault-agent/auth.token
```

## API

### POST /api/v1/reports

接收 Agent 上报的检查结果。

**Request body:** 完整的 JSON report（见 Agent README）

**Response 200:**
```json
{
  "status": "accepted",
  "hostname": "web-01",
  "reported_at": "2026-05-08T14:30:00Z",
  "checks_count": 21
}
```

### GET /api/v1/health

健康检查。

```json
{ "status": "ok", "version": "1.0.0" }
```

## 数据库

### reports 表

| 列 | 类型 | 说明 |
|----|------|------|
| id | INTEGER | 自增主键 |
| hostname | TEXT | 主机名 |
| machine_id | TEXT | 机器唯一标识 |
| sysinfo | TEXT | 自定义标签字符串 |
| tags | TEXT | 自定义标签字典（JSON） |
| reported_at | TEXT | 上报时间 |
| received_at | TEXT | 接收时间 |
| uptime_seconds | REAL | 系统运行时间 |
| agent_version | TEXT | Agent 版本 |
| report_json | TEXT | 完整上报 JSON |

## 与其他组件配合

```bash
# 启动服务器（默认端口 8000）
python3 server.py --port 8000 --db /var/lib/fault-agent-server/reports.db

# 启动 Web 仪表盘（读取同一数据库，默认端口 9000）
python3 ../fault-agent-web/web.py --port 9000 --db /var/lib/fault-agent-server/reports.db
```

## 许可证

MIT