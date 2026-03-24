# server-inspect

> OpenClaw 技能：Linux 服务器自动化巡检与告警工具

通过 SSH 采集服务器基础指标（CPU、内存、磁盘、网络、安全），由 AI 分析数据、生成巡检报告、发送飞书/邮件通知。支持多主机并行巡检、定时任务调度、历史数据存储。

## 功能特性

- 🔍 **多指标采集**：CPU、内存、磁盘、网络、服务状态、安全基线
- 🤖 **AI 智能分析**：内置运维分析师角色，自动异常检测 + 根因分析 + 优化建议
- 📊 **Markdown 报告**：结构化报告模板，支持历史趋势对比（ASCII 图表）
- 🔔 **报告通知**：独立的飞书/邮件通知功能，可随时发送最新报告
- ⏰ **定时巡检**：通过 OpenClaw cron 调度
- 📈 **历史数据**：JSON Lines 格式，按调用次数存储

## 两大核心功能

### 功能一：服务器巡检

**触发场景**：
- 用户说"巡检"、"巡检服务器"、"执行巡检"
- 用户说"检查服务器状态"、"查看服务器指标"
- 定时任务触发

**执行流程**：
1. SSH 连接服务器
2. 执行 28 个巡检命令
3. 解析数据、判定告警
4. 生成 Markdown 报告
5. AI 注入分析建议

### 功能二：报告通知

**触发场景**：
- 用户说"发送报告到飞书"、"推送飞书通知"
- 用户说"发送报告到邮件"、"发邮件通知"
- 用户说"推送报告"、"发送通知"
- 用户说"把最新的巡检报告发给我"

**执行流程**：
1. 获取最近一次巡检报告
2. 读取配置文件中的通知配置
3. 从报告中提取数据（CPU、内存、磁盘、告警等）
4. 调用 `notifier.py` 发送通知
5. 提示用户提供缺失的配置（如果需要）

## 安装

### 方式一：从 GitHub 安装（推荐）

```bash
git clone https://github.com/feilong103/server-inspect.git ~/.qclaw/skills/server-inspect
```

### 方式二：从 OpenClaw Skills Hub 安装

```
/skills add server-inspect
```

## 快速开始

### 1. 初始化配置

运行初始化脚本：

```bash
python3 ~/server-inspect/scripts/init_inspect.py
```

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

### 3. AI 分析报告

巡检完成后，AI 会：
1. 读取生成的 Markdown 报告
2. 分析数据和告警
3. 注入优化建议（替换 `<!-- AI_SUGGESTIONS -->` 占位符）
4. 返回完整分析报告

### 4. 发送通知（可选）

如果配置了飞书或邮件，脚本会自动发送通知。

### 5. 设置定时任务

通过 OpenClaw cron 配置每日巡检：

```json
{
  "name": "server-inspect:daily",
  "schedule": { "kind": "cron", "expr": "0 9 * * *", "tz": "Asia/Shanghai" },
  "payload": {
    "kind": "agentTurn",
    "message": "请执行 server-inspect 巡检：\n1. 查找 server-inspect skill 目录\n2. 运行该目录下的 scripts/run_inspect.py\n3. 读取生成的报告（reports/ 目录下最新的 .md 文件）\n4. 找到 <!-- AI_SUGGESTIONS --> 占位符，注入 AI 分析和建议\n5. 将完整报告内容回复给我\n6. 如果用户配置了通知工具请调用 skill 自带的通知函数"
  },
  "sessionTarget": "isolated",
  "delivery": { "mode": "announce" }
}
```

## 工作流程

```
读取配置 → SSH 采集 → 结构化解析 → 阈值告警判定 → 历史数据存储 
    ↓
Markdown 报告生成 → AI 分析注入 → 飞书通知【可选】→ 邮件通知【可选】
```

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
    ├── run_inspect.py       # 巡检执行脚本
    └── notifier.py          # 通知模块（飞书、邮件）
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
    "email": {
      "smtp_host": "smtp.qq.com",
      "smtp_port": 465,
      "smtp_user": "your-email@qq.com",
      "smtp_password": "your-auth-code",
      "from": "your-email@qq.com",
      "to": ["recipient@example.com"]
    }
  }
}
```

## 依赖

- Python 3.8+
- SSH 免密配置（推荐 ed25519 密钥）
- 可选：`aiohttp`（异步 HTTP，飞书通知）

## 常见问题

### Q: 如何添加新的巡检服务器？

编辑 `config.json` 的 `servers` 数组，添加新的服务器配置，或运行 `init_inspect.py` 重新初始化。

### Q: 如何自定义告警阈值？

编辑 `config.json` 的 `alert_thresholds` 字段：

```json
"alert_thresholds": {
  "cpu_percent": 80,      # CPU 告警阈值
  "mem_percent": 85,      # 内存告警阈值
  "disk_percent": 90,     # 磁盘告警阈值
  "loadavg_1m": 4         # 1分钟负载告警阈值
}
```

### Q: 如何只巡检特定主机或指标？

```bash
python3 ~/server-inspect/scripts/run_inspect.py --host prod-web-01
python3 ~/server-inspect/scripts/run_inspect.py --groups cpu,mem,disk
```

### Q: 飞书通知需要什么权限？

在飞书群中添加「自定义机器人」，获取 Webhook 地址即可，无需特殊权限。

### Q: 邮件通知支持哪些邮箱？

支持所有 IMAP/SMTP 邮箱服务，包括：
- QQ 邮箱（smtp.qq.com:465）
- 网易邮箱（smtp.163.com:587）
- Gmail（smtp.gmail.com:587）
- 企业邮箱等

### Q: SSH 密码方式支持吗？

支持，但建议使用密钥方式，避免密码明文存储。在 `config.json` 中配置 `ssh_password` 字段。

### Q: 历史数据如何用于趋势分析？

脚本自动从 `history/` 目录读取历史数据，生成 7 天趋势图表。每次巡检会新增一个 JSONL 文件，包含 CPU、内存、磁盘使用率等关键指标。

### Q: 如何查看原始巡检日志？

原始命令输出保存在 `~/server-inspect/logs/` 目录，文件名格式为 `inspect_YYYYMMDD_HHMMSS.log`。

### Q: 定时任务如何配置？

使用 OpenClaw cron 功能，参考上面的"设置定时任务"部分。

## 相关文档

- [SKILL.md](./SKILL.md) - 技能详细文档
- [references/metrics.md](./references/metrics.md) - 巡检指标详解
- [references/init-guide.md](./references/init-guide.md) - 初始化指南
- [references/report-template.md](./references/report-template.md) - 报告模板
- [references/system-prompt.md](./references/system-prompt.md) - AI 分析提示词

## License

MIT
