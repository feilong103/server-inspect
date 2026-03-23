# server-inspect

> OpenClaw 技能：Linux 服务器自动化巡检与告警工具

通过 SSH 采集服务器基础指标（CPU、内存、磁盘、网络、安全），由 AI 分析数据、生成巡检报告、发送飞书/邮件通知。支持多主机并行巡检、定时任务调度、历史数据存储。

## 功能特性

- 🔍 **多指标采集**：CPU、内存、磁盘、网络、服务状态、安全基线
- 🤖 **AI 智能分析**：内置运维分析师角色，自动异常检测 + 根因分析 + 优化建议
- 📊 **Markdown 报告**：结构化报告模板，支持历史趋势对比（ASCII 图表）
- 🔔 **多渠道通知**：飞书 Webhook、邮件 SMTP
- ⏰ **定时巡检**：通过 OpenClaw cron 调度
- 📈 **历史数据**：JSON Lines 格式，按调用次数存储

## 安装

### 方式一：从 GitHub 安装（推荐）

```bash
# 克隆到本地 skills 目录
git clone https://github.com/feilong103/server-inspect.git ~/.qclaw/skills/server-inspect
```

### 方式二：从 OpenClaw Skills Hub 安装

```
/skills add server-inspect
```

## 快速开始

### 1. 初始化配置

运行 `python3 ~/server-inspect/scripts/init_inspect.py` 或让 AI 执行初始化。

按引导配置：
- 服务器列表（主机名/IP、SSH 用户、端口、密钥）
- 飞书 Webhook URL（可选）
- 邮件 SMTP（可选）
- 告警阈值（可使用默认值）

### 2. 执行巡检

```bash
python3 ~/server-inspect/scripts/run_inspect.py
```

可选参数：
```bash
--host prod-web-01    # 指定主机
--groups cpu,mem      # 指定指标组
```

### 3. 查看报告

报告自动生成在 `~/server-inspect/reports/`，AI 会读取报告并注入分析建议。

### 4. 设置定时任务

通过 OpenClaw cron 配置每日巡检：

```json
{
  "name": "server-inspect:daily",
  "schedule": { "kind": "cron", "expr": "0 9 * * *", "tz": "Asia/Shanghai" },
  "payload": {
    "kind": "agentTurn",
    "message": "请执行 server-inspect 巡检：\n1. 查找 server-inspect skill 目录（通常在 ~/.qclaw/skills/server-inspect/ 或 /Applications/QClaw.app/Contents/Resources/openclaw/config/skills/server-inspect/）\n2. 运行该目录下的 scripts/run_inspect.py\n3. 读取生成的报告（reports/ 目录下最新的 .md 文件）\n4. 找到 <!-- AI_SUGGESTIONS --> 占位符，注入 AI 分析和建议\n5. 将完整报告内容回复给我"
  },
  "sessionTarget": "isolated",
  "delivery": { "mode": "announce" }
}
```

> **提示**：AI 会自动查找 skill 目录，无需硬编码路径。数据默认保存在 `~/server-inspect/`。

## 指标分组

| 分组 | 指标 | 说明 |
|------|------|------|
| 系统基础 | hostname, uptime, who, last, uname | 主机信息、运行时间、登录用户 |
| CPU | cpu_usage, loadavg, top_cpu, vmstat | CPU 使用率、负载均值、Top 进程 |
| 内存 | mem_usage, swap, top_mem, oom | 内存/Swap 使用率、OOM 事件 |
| 磁盘 | disk_usage, disk_inode, du_top, disk_io | 磁盘使用率、inode、大目录 |
| 网络 | netstat, ss, tcp_status | 连接统计、监听端口、TCP 状态 |
| 服务 | systemctl, process_count | 关键服务状态、进程总数 |
| 安全 | failed_login, last_login, sudo_usage, firewall | 登录审计、Sudo 使用、防火墙 |

## 告警阈值

| 级别 | CPU | 内存 | 磁盘 | 负载 |
|------|-----|------|------|------|
| 🔴 Critical | ≥ 95% | ≥ 95% | ≥ 95% | ≥ 2×CPU核数 |
| 🟠 Warning | ≥ 80% | ≥ 85% | ≥ 90% | ≥ 4 |

阈值可在 `config.json` 中自定义。

## 目录结构

```
~/server-inspect/
├── config.json              # 配置文件
├── reports/                 # Markdown 巡检报告
├── logs/                    # 原始命令输出
├── history/                 # 历史数据（JSON Lines）
│   └── {主机名}/
│       └── YYYY-MM-DD_HHMMSS.jsonl
└── scripts/
    ├── init_inspect.py      # 初始化脚本
    └── run_inspect.py       # 巡检执行脚本
```

## 数据存储

| 类型 | 路径 | 说明 |
|------|------|------|
| 配置文件 | `~/server-inspect/config.json` | 服务器列表、阈值、通知配置 |
| 历史记录 | `~/server-inspect/history/{host}/YYYY-MM-DD_HHMMSS.jsonl` | 每次巡检一个文件 |
| 巡检报告 | `~/server-inspect/reports/report_YYYYMMDD_HHMMSS.md` | 含 AI 分析 |
| 原始日志 | `~/server-inspect/logs/inspect_YYYYMMDD_HHMMSS.log` | 原始命令输出 |

## 配置示例

```json
{
  "version": "1.0",
  "servers": [
    {
      "name": "prod-web-01",
      "host": "192.168.1.10",
      "ssh_user": "admin",
      "ssh_port": 22,
      "ssh_key": "~/.ssh/id_ed25519",
      "groups": ["系统基础", "CPU", "内存", "磁盘", "网络", "服务", "安全"],
      "enabled": true
    }
  ],
  "alert_thresholds": {
    "cpu_percent": 80,
    "mem_percent": 85,
    "disk_percent": 90,
    "loadavg_1m": 4
  },
  "notification": {
    "feishu_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
    "email": {}
  }
}
```

## 依赖

- Python 3.8+
- SSH 免密配置（推荐 ed25519 密钥）
- 可选：`aiohttp`（异步 HTTP，飞书通知）、`smtplib`（邮件通知）

## 相关文档

- [SKILL.md](./SKILL.md) - 技能入口文档
- [references/metrics.md](./references/metrics.md) - 巡检指标详解
- [references/init-guide.md](./references/init-guide.md) - 初始化指南
- [references/report-template.md](./references/report-template.md) - 报告模板
- [references/system-prompt.md](./references/system-prompt.md) - AI 分析提示词

## License

MIT
