# Fault Agent Web Dashboard

Web 仪表盘，读取 Fault Agent Server 的 SQLite 数据库，以网页形式展示主机故障状态。

## 目录结构

```
fault-agent-web/
└── web.py    # Web 仪表盘（单文件，零依赖）
```

## 依赖

- Python 3.7+
- 仅使用标准库，零外部依赖

## 快速开始

```bash
python3 web.py --port 9000 --db /var/lib/fault-agent-server/reports.db
```

参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--port` / `-p` | 9000 | 监听端口 |
| `--db` | `/var/lib/fault-agent-server/reports.db` | SQLite 数据库路径 |
| `--bind` | `0.0.0.0` | 监听地址 |
| `--debug` | false | 开启调试日志 |

## 页面

### Dashboard 首页 (`/`)

所有主机概览卡片：

- 全局统计栏：主机总数、Critical / Warning / Error 总数、10 分钟内活跃主机数
- 主机卡片：主机名、sysinfo、tags、状态圆点、各类检查数量、运行时长
- 支持按严重程度排序（Critical 优先展示）

### 主机详情页 (`/host/<hostname>`)

单主机的完整信息：

- 状态汇总栏：Critical / Warning / OK 数量
- 主机元信息：Agent 版本、上报时间、运行时长、采集间隔、machine_id
- 检查结果表格（按严重程度排序，可直接展开 detail 数据）
- 历史报告时间线：最近 30 次上报的摘要统计

## JSON API

| 路径 | 说明 |
|------|------|
| `GET /api/hosts` | 所有主机的最新状态 |
| `GET /api/host/<hostname>` | 指定主机的最近一次上报 |
| `GET /api/history/<hostname>` | 指定主机的历史上报列表 |

## 截图预览

```
+--------------------------------------------------+
|  Fault Agent                          Dashboard   |
+--------------------------------------------------+
|  [4] Hosts  [1] Critical  [2] Warning  [0] Error |
+--------------------------------------------------+
|  +--------------------------------------------+  |
|  | ● web-01           [0] CRIT  [1] WARN [5] OK|
|  | Beijing-IDC  tag:dc=Beijing  tag:role=web   |
|  | [=====================] up 15d              |
|  +--------------------------------------------+  |
|  +--------------------------------------------+  |
|  | ● db-01           [1] CRIT  [1] WARN [1] OK|
|  | Shanghai-IDC  tag:dc=Shanghai              |
|  | [====================================] up 120d|
|  +--------------------------------------------+  |
+--------------------------------------------------+
```

## 许可证

MIT