# server-inspect

> OpenClaw 技能：Linux 服务器自动化巡检与告警工具

通过 SSH 采集服务器基础指标（CPU、内存、磁盘、网络、安全），由 AI 分析数据、生成巡检报告、发送飞书/邮件通知。支持多主机并行巡检、定时任务调度、历史数据存储。

## 功能特性

- 🔍 **多指标采集**：CPU、内存、磁盘、网络、服务状态、安全基线
- 🤖 **AI 智能分析**：内置运维分析师角色，自动异常检测 + 根因分析 + 优化建议
- 📊 **Markdown 报告**：结构化报告模板，支持历史趋势对比
- 🔔 **多渠道通知**：飞书 Webhook、邮件 SMTP
- ⏰ **定时巡检**：通过 OpenClaw cron 调度
- 📈 **历史数据**：JSON Lines 格式持久化

## 安装

### 方法一：从 OpenClaw Skills Hub 安装（推荐）

在 OpenClaw 中运行：

```
/skills add server-inspect
```

### 方法二：手动安装

将本仓库克隆到 OpenClaw 技能目录：

```bash
git clone https://github.com/feilong103/server-inspect.git
# 将 server-inspect 目录复制到 OpenClaw skills 目录
```

## 快速开始

### 1. 初始化配置

```
运行：init
```

按引导配置：
- 服务器列表（主机名/IP、SSH 用户、端口）
- 飞书 Webhook URL（可选）
- 邮件 SMTP（可选）
- 告警阈值（可使用默认值）

### 2. 执行巡检

```
运行：inspect
```

默认巡检所有已配置服务器，也可指定：

```
inspect --host prod-web-01    # 指定主机
inspect --group cpu,mem       # 指定指标组
```

### 3. 查看报告

```
运行：report
```

报告自动保存到 `~/server-inspect/reports/`

### 4. 设置定时任务

```
运行：cron add daily
```

## 指标分组

| 分组 | 指标 | 说明 |
|------|------|------|
| 系统基础 | hostname, uptime, who, last | 主机信息、运行时间、登录用户 |
| CPU | cpu_usage, loadavg, top_cpu | CPU 使用率、负载均值、Top 进程 |
| 内存 | mem_usage, swap, oom | 内存/Swap 使用率、OOM 事件 |
| 磁盘 | disk_usage, disk_inode, du_top | 磁盘使用率、inode、大目录 |
| 网络 | netstat, ss, bandwidth | 连接统计、监听端口、带宽 |
| 安全 | failed_login, sudo_usage | 登录失败、Sudo 使用审计 |

## 告警阈值

| 级别 | CPU | 内存 | 磁盘 | 负载 |
|------|-----|------|------|------|
| 🔴 Critical | ≥ 95% | ≥ 95% | ≥ 95% | ≥ 2×CPU核数 |
| 🟠 Warning | ≥ 80% | ≥ 85% | ≥ 90% | ≥ 1.5×CPU核数 |

阈值可在 `config.json` 中自定义。

## 目录结构

```
server-inspect/
├── SKILL.md                      # 技能入口
├── references/
│   ├── metrics.md               # 巡检指标详解
│   ├── init-guide.md            # 初始化指南
│   ├── report-template.md       # 报告模板
│   └── system-prompt.md         # AI 分析提示词
└── scripts/
    ├── init_inspect.py          # 初始化脚本
    └── inspect.py               # 巡检执行脚本
```

## 数据存储

| 类型 | 路径 |
|------|------|
| 配置文件 | `~/server-inspect/config.json` |
| 历史记录 | `~/server-inspect/history/{host}/YYYY-MM-DD_HHMMSS.jsonl`（每次巡检一个文件）|
| 巡检报告 | `~/server-inspect/reports/{timestamp}.md` |

## 配置示例

```json
{
  "servers": [
    {
      "name": "prod-web-01",
      "host": "192.168.1.10",
      "ssh_user": "admin",
      "ssh_port": 22,
      "groups": ["系统基础", "CPU", "内存", "磁盘", "网络", "安全"],
      "enabled": true
    }
  ],
  "alert_thresholds": {
    "cpu_percent": 80,
    "mem_percent": 85,
    "disk_percent": 90
  },
  "notification": {
    "feishu_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"
  }
}
```

## 依赖

- Python 3.8+
- SSH 免密配置（推荐 ed25519 密钥）
- 可选：`aiohttp`（异步 HTTP，飞书通知）、`smtplib`（邮件通知）

## License

MIT
