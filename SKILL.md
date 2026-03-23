---
name: server-inspect
description: Linux 服务器自动化巡检与告警工具。通过 SSH 采集服务器基础指标（CPU、内存、磁盘、网络、安全），由 AI 分析数据、生成巡检报告、发送飞书/邮件通知。支持多主机并行巡检、定时任务调度、历史数据存储。触发场景：(1) 巡检服务器、执行巡检命令 (2) 生成巡检报告 (3) 查看历史巡检结果 (4) 配置告警阈值 (5) 设置定时巡检任务 (6) 添加/删除巡检服务器。
---

# Server Inspect - Linux 服务器巡检技能

## 功能概览

| 命令 | 说明 |
|------|------|
| `inspect` | 执行单次巡检（默认本机，可指定 SSH 目标） |
| `init` | 初始化巡检配置（服务器列表、指标组、告警阈值、通知渠道） |
| `report` | 查看/生成巡检报告 |
| `notify` | 手动触发巡检结果通知 |
| `cron` | 注册/查看/删除定时巡检任务 |
| `alert` | 告警阈值配置与测试 |
| `history` | 查询历史巡检记录 |

## 巡检指标分组

详见 [references/metrics.md](references/metrics.md)，核心分组：

- **系统基础**：主机名、运行时间、登录用户、开机状态
- **CPU**：使用率、负载均值、TOP 进程
- **内存**：使用率、Swap、OOM 事件
- **磁盘**：分区使用率、inode、IO 等待
- **网络**：连接数、带宽、端口监听
- **服务**：关键服务运行状态（SSH、Nginx、MySQL 等）
- **安全**：登录失败、异常用户、SELinux、防火墙

## 工作流程

```
用户触发 inspect
    ↓
读取配置（server-inspect.json）
    ↓
采集层：SSH/本地执行白名单命令 → 原始输出
    ↓
解析层：结构化解析 → JSON 指标数据
    ↓
分析层：AI 异常检测 + 根因分析 + 优化建议
    ↓
报告层：Markdown 报告 → 飞书/邮件通知
    ↓
存储层：JSON Lines 历史记录
```

## 初始化流程（init）

首次使用必须运行初始化：

1. 创建工作目录 `~/.qclaw/server-inspect/`
2. 生成 `config.json` 配置文件（服务器列表 + 指标组）
3. 生成 SSH 白名单命令列表
4. 将白名单 patch 到 gateway config（用户确认后）
5. 配置飞书 Webhook URL（可选）
6. 配置邮件 SMTP（可选）

详见 [references/init-guide.md](references/init-guide.md)。

## 巡检命令执行机制

- 所有巡检命令必须先在 `config.json` 的 `allowed_commands` 中声明
- 通过 `gateway.config.patch` 将命令白名单写入 `plugins.entries.device-pair.config.exec.allowCommands`
- SSH 执行格式：`ssh user@host '命令'`
- 本机执行：直接 exec 白名单命令
- 所有命令输出**不记录敏感信息**（密码、密钥等）

## 报告模板结构

详见 [references/report-template.md](references/report-template.md)。

## 数据存储

- 历史记录：`~/.qclaw/server-inspect/history/{host}/{YYYY-MM}.jsonl`
- 配置文件：`~/.qclaw/server-inspect/config.json`
- 报告输出：`~/.qclaw/server-inspect/reports/{timestamp}.md`

## AI 分析提示词

内置运维分析师角色，详见 [references/system-prompt.md](references/system-prompt.md)。

## 定时任务

使用 OpenClaw cron，`sessionTarget: isolated`，建议巡检时间：
- 日常巡检：每天 09:00（GMT+8）
- 高危巡检：每周一 09:00
- 月度报告：每月 1 日 09:00

---

## 快速开始

### 首次使用

```
请运行：init
```

按提示配置：
1. 服务器列表（主机名/IP、SSH 用户、SSH 端口）
2. 飞书 Webhook URL（可选，跳过则仅本地报告）
3. 邮件通知配置（可选）
4. 告警阈值（可先使用默认值）

### 执行巡检

```
运行：inspect
```

默认巡检配置文件中所有服务器，也可指定：
```
inspect --host prod-web-01
inspect --group cpu,disk
```

### 查看报告

```
运行：report
```

### 设置定时任务

```
运行：cron add daily
```

---

## 配置文件格式（config.json）

```json
{
  "version": "1.0",
  "servers": [
    {
      "name": "prod-web-01",
      "host": "192.168.1.10",
      "ssh_user": "admin",
      "ssh_port": 22,
      "groups": ["系统基础", "CPU", "内存", "磁盘", "网络", "服务", "安全"],
      "enabled": true
    }
  ],
  "metrics_groups": {
    "系统基础": ["hostname", "uptime", "who", "last"],
    "CPU": ["cpu_usage", "loadavg", "top_cpu"],
    "内存": ["mem_usage", "swap", "oom"],
    "磁盘": ["disk_usage", "disk_io"],
    "网络": ["netstat", "bandwidth"],
    "服务": ["services"],
    "安全": ["failed_login", "异常用户"]
  },
  "alert_thresholds": {
    "cpu_percent": 80,
    "mem_percent": 85,
    "disk_percent": 90,
    "loadavg_1m": 4,
    "disk_inode_percent": 90,
    "netstat_connections": 5000
  },
  "notification": {
    "feishu_webhook": "",
    "email": {
      "smtp_host": "",
      "smtp_port": 587,
      "smtp_user": "",
      "smtp_password": "",
      "to": []
    }
  },
  "allowed_commands": [
    "/usr/bin/hostname",
    "/usr/bin/uptime",
    "/usr/bin/w",
    "/usr/bin/last",
    "/usr/bin/top",
    "/usr/bin/free",
    "/bin/df",
    "/usr/bin/netstat",
    "/usr/bin/ss",
    "/usr/bin/ps",
    "/usr/bin/systemctl",
    "/bin/journalctl",
    "/usr/bin/dmesg"
  ],
  "report_template": "standard",
  "history_retention_days": 90
}
```
