---
name: server-inspect
description: Linux 服务器自动化巡检与告警工具。通过 SSH 采集服务器基础指标（CPU、内存、磁盘、网络、安全），由 AI 分析数据、生成巡检报告、发送飞书/邮件通知。支持多主机并行巡检、定时任务调度、历史数据存储。触发场景：(1) 巡检服务器、执行巡检命令 (2) 生成巡检报告 (3) 查看历史巡检结果 (4) 配置告警阈值 (5) 设置定时巡检任务 (6) 添加/删除巡检服务器 (7) 发送报告到飞书/邮件、推送通知。
---

# Server Inspect - Linux 服务器巡检技能

## ⚠️ AI 执行指南（必读）

**当用户触发任何功能时，必须先执行以下步骤**：

### 第一步：读取 Skill 文档
```
1. 定位 skill 目录（通常在 ~/.qclaw/skills/server-inspect/）
2. 读取 SKILL.md 了解完整流程
3. 根据 SKILL.md 的指引执行，不要自己造轮子
```

### 第二步：使用 Skill 提供的工具
```
✅ 巡检 → 执行 scripts/run_inspect.py
✅ 通知 → 调用 scripts/notifier.py 的函数
❌ 不要自己写 SSH 连接代码
❌ 不要自己写飞书/邮件发送代码
❌ 不要自己解析报告格式
```

### 第三步：严格遵循 SKILL.md
```
- 所有函数、参数、流程都在 SKILL.md 中定义
- 如果不确定，先读取 SKILL.md 再执行
- 不要根据"经验"或"常识"自己实现
```

---

## 概述

本技能通过 SSH 远程执行 Linux 命令，采集服务器基础指标，由 OpenClaw AI 分析数据并生成巡检报告。

**核心流程**：
```
读取配置 → SSH 采集 → 结构化解析 → 阈值告警判定 → 历史数据存储 → Markdown 报告生成 → AI 分析注入 → 报告通知
```

**两大核心功能**：
1. **服务器巡检** — 采集指标、生成报告、AI 分析
2. **报告通知** — 发送报告到飞书/邮件（独立功能）

---

## 功能一：服务器巡检

**触发场景**：
- 用户说"巡检"、"巡检服务器"、"执行巡检"
- 用户说"检查服务器状态"、"查看服务器指标"
- 定时任务触发

**执行流程**：AI 读取 `run_inspect.py` 的输出，加载生成的 Markdown 报告，注入 AI 分析和建议。

---

## 数据存储（自动生成）

每次巡检结束后，脚本**自动**在 `~/server-inspect/` 下生成以下文件：

```
~/server-inspect/
├── config.json              # 配置文件（需手动配置或 init 生成）
├── reports/
│   └── {主机名}_report_YYYYMMDD_HHMMSS.md   # Markdown 巡检报告（含 AI 分析）
├── logs/
│   └── {主机名}_inspect_YYYYMMDD_HHMMSS.log # 各主机的原始命令输出
└── history/
    └── {主机名}/
        └── YYYY-MMDD_HHMMSS.jsonl           # 结构化历史数据（每次一个文件）
```

### 历史数据格式（JSON Lines）

每次巡检生成一个独立的 JSONL 文件，文件名包含时间戳：

```
~/server-inspect/history/{主机名}/2026-03-23_141700.jsonl
```

每条记录格式：

```json
{"timestamp": "2026-03-23 14:17:00", "duration_ms": 21000, "overall_status": "WARNING", "alerts_count": 1, "cpu_pct": 45.2, "mem_pct": 72.1, "disk_pct": 68, "hostname": "gpt-load"}
```

**重要**：历史数据按调用次数累积，每次巡检一个文件，多次巡检后可用于趋势分析和容量预警。

### 数据生命周期

| 类型 | 路径 | 说明 |
|------|------|------|
| 巡检报告 | `~/server-inspect/reports/` | 完整 Markdown 报告，含 AI 分析 |
| 原始日志 | `~/server-inspect/logs/` | 各主机的原始命令输出 |
| 历史记录 | `~/server-inspect/history/{host}/` | 结构化 JSON Lines，按调用次数存储 |
| 配置文件 | `~/server-inspect/config.json` | 服务器列表、阈值、通知配置 |

---

## 工作流程详解

### Step 1：读取配置

脚本读取 `~/server-inspect/config.json`，获取：
- 服务器列表（名称/IP/SSH 认证信息）
- 要执行的指标组
- 告警阈值
- 通知渠道

### Step 2：SSH 采集

对每台服务器依次执行 28 个巡检命令：

| 分组 | 命令 | 超时 |
|------|------|------|
| 系统基础 | hostname, uptime, who, last, uname | 10s |
| CPU | top, cat /proc/loadavg, ps aux sort by cpu, vmstat | 10s |
| 内存 | free -h, swap, ps aux sort by mem | 10s |
| 磁盘 | df -h, df -i, du -sh /var/*, iostat | 10s |
| 网络 | netstat, ss, tcp_status | 10s |
| 服务 | systemctl, ps wc -l | 10s |
| 安全 | failed_login grep, last, sudo_usage, firewall | 10s |

**超时保护**：`du -sh /var/*` 使用 `timeout 5` 防止卡死，journalctl 类命令加 `2>/dev/null` 防止权限问题。

### Step 3：解析与告警判定

解析原始命令输出，提取数值，与阈值比对：

| 告警级别 | 触发条件 | 示例 |
|----------|---------|------|
| 🔴 Critical | cpu ≥ 95% / mem ≥ 95% / disk ≥ 95% / load ≥ 2×核数 | CPU 使用率 97% |
| 🟠 Warning | cpu ≥ 80% / mem ≥ 85% / disk ≥ 90% / load ≥ 4 | CPU 使用率 82% |
| 🟡 Info | swap > 0 / TIME_WAIT > 3000 | Swap 被使用 |

### Step 4：历史数据写入（自动）

**必须执行，否则历史趋势图无数据**：

```python
# 每台主机的结构化记录写入 JSONL
history_file = ~/server-inspect/history/{host}/YYYY-MMDD_HHMMSS.jsonl
with open(history_file, "a") as f:
    f.write(json.dumps({
        "timestamp": "...",
        "cpu_pct": 45.2,   # CPU 使用率 %
        "mem_pct": 72.1,   # 内存使用率 %
        "disk_pct": 68,    # 根分区使用率 %
        "overall_status": "WARNING",
        "alerts_count": 1
    }) + "\n")
```

同时写入原始命令日志：
```python
# 原始输出写入日志文件
log_file = ~/server-inspect/logs/{host}_inspect_YYYYMMDD_HHMMSS.log
```

### Step 5：Markdown 报告生成

调用 `ReportGenerator.generate_md_report()`，严格按 `references/report-template.md` 格式输出，**包含占位符供 AI 注入分析**：

```markdown
## 四、💡 AI 优化建议

<!-- AI_SUGGESTIONS -->
（AI 分析后替换此占位符）

## 五、📊 历史趋势（7天）

（脚本根据 history/ 目录自动渲染 ASCII 趋势图）
```

### Step 6：OpenClaw AI 分析

巡检完成后，**必须**由 AI 读取报告文件，找到 `<!-- AI_SUGGESTIONS -->` 占位符，替换为：
- 总体 AI 分析摘要
- 针对每个告警的具体建议（含操作步骤/风险提示）
- 历史趋势分析

**AI 的职责**：
- ✅ 读取 Markdown 报告文件
- ✅ 分析报告中的数据和告警
- ✅ 替换 `<!-- AI_SUGGESTIONS -->` 占位符
- ✅ 返回完整的分析报告

### Step 7：报告通知（可选）

巡检完成后，脚本会发送飞书和邮件通知（如果配置了）。

**重要**：通知内容由脚本自动从报告数据中提取，**不需要 AI 手动组织**。

**通知流程**：
```
巡检完成 → 生成报告 → 发送飞书【可选】 → 发送邮件【可选】
```

**AI 的职责**：
- ✅ 如果用户配置了通知（feishu_webhook 或 email），调用 notifier.py 的通知函数
- ❌ 不要自己组织通知内容，数据由巡检报告提供
- ❌ 不要幻想数据

---

## 功能二：报告通知（独立功能）

**⚠️ 重要：不要自己写通知代码，使用 skill 提供的 notifier.py！**

**触发场景**：
- 用户说"发送报告到飞书"、"推送飞书通知"
- 用户说"发送报告到邮件"、"发邮件通知"
- 用户说"推送报告"、"发送通知"
- 用户说"把最新的巡检报告发给我"

**执行前必读**：
```
1. 先读取 SKILL.md 了解完整流程
2. 使用 scripts/notifier.py 提供的函数
3. 不要自己写飞书/邮件发送代码
4. 数据从报告中提取，不要幻想
```

**执行流程**：

### Step 1：获取最近一次巡检报告

```python
# 查找 reports/ 目录下最新的 .md 文件
report_path = max(Path("~/server-inspect/reports").glob("*.md"), key=lambda p: p.stat().st_mtime)
```

### Step 2：读取配置文件

```python
config = json.load(open("~/server-inspect/config.json"))
feishu_webhook = config.get("notification", {}).get("feishu_webhook")
email_config = config.get("notification", {}).get("email")
thresholds = config.get("alert_thresholds", {})
```

### Step 3：提取报告内容

**从 Markdown 报告中提取**：
- 巡检时间、服务器数量、耗时
- 各主机的 CPU、内存、磁盘、安全状态
- 告警列表（级别、服务器、消息）
- AI 分析建议（如果有）

### Step 4：调用通知函数

**飞书通知**：
```python
from notifier import FeishuNotifier

FeishuNotifier.send(
    webhook_url=feishu_webhook,
    reports=reports,  # 从报告提取的数据
    thresholds=thresholds,
    report_path=str(report_path)
)
```

**邮件通知**：
```python
from notifier import EmailNotifier

EmailNotifier.send(
    smtp_config=email_config,
    reports=reports,  # 从报告提取的数据
    thresholds=thresholds,
    report_path=str(report_path)
)
```

### Step 5：处理缺失配置

**如果用户没有配置飞书 Webhook**：
```
⚠️ 未配置飞书通知。请提供飞书 Webhook URL，或在 config.json 中配置：
"notification": {
  "feishu_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"
}
```

**如果用户没有配置邮件 SMTP**：
```
⚠️ 未配置邮件通知。请提供邮件配置，或在 config.json 中配置：
"notification": {
  "email": {
    "smtp_host": "smtp.qq.com",
    "smtp_port": 465,
    "smtp_user": "your-email@qq.com",
    "smtp_password": "your-auth-code",
    "from": "your-email@qq.com",
    "to": ["recipient@example.com"]
  }
}
```

### Step 6：确认发送结果

```
✅ 飞书通知已发送
✅ 邮件已发送到 wangfl@rynnova.com
```

**AI 的职责**：
- ✅ 获取最新的巡检报告文件
- ✅ 读取配置文件中的通知配置
- ✅ 从报告中提取数据（不要幻想）
- ✅ 调用 notifier.py 的通知函数
- ✅ 提示用户提供缺失的配置
- ❌ 不要自己组织通知内容
- ❌ 不要幻想报告数据

## 代码结构

```
scripts/
├── run_inspect.py      # 核心巡检脚本（采集、解析、报告生成）
└── notifier.py         # 通知模块（飞书、邮件）
```

### notifier.py 模块

独立的通知模块，包含两个类：

**1. FeishuNotifier** — 飞书卡片通知
```python
FeishuNotifier.send(
    webhook_url: str,           # 飞书 Webhook URL
    reports: List[ServerReport], # 巡检报告对象列表
    thresholds: dict,           # 告警阈值
    report_path: str            # 报告文件路径
)
```

**2. EmailNotifier** — 邮件通知
```python
EmailNotifier.send(
    smtp_config: dict,          # SMTP 配置
    reports: List[ServerReport], # 巡检报告对象列表
    thresholds: dict,           # 告警阈值
    report_path: str            # 报告文件路径
)
```

#### 配置方式

在 `config.json` 的 `notification` 中配置 Webhook URL 或邮件 SMTP：

```json
"notification": {
  "feishu_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
  "email": {
    "smtp_host": "smtp.qq.com",
    "smtp_port": 465,
    "smtp_user": "834235688@qq.com",
    "smtp_password": "trxxclqysapjbfcc",
    "from": "834235688@qq.com",
    "to": ["wangfl@rynnova.com"]
  }
}
```


**触发时机**：
- 手动巡检时：AI 会询问"是否发送飞书/邮件通知？"
- 定时任务：配置后自动发送

---

## 报告模板结构

详见 [references/report-template.md](references/report-template.md)，共 6 章节：

1. **巡检概览**：总体状态、AI 分析摘要
2. **分服务器详情**：系统基础/CPU/内存/磁盘/网络/服务/安全
3. **异常汇总**：告警列表
4. **AI 优化建议**：OpenClaw 注入，含操作步骤
5. **历史趋势**：ASCII 趋势图
6. **附录**：原始命令输出

---

## 初始化配置（init）

首次使用必须配置 `~/server-inspect/config.json`，参考 `references/init-guide.md`。

**最小配置示例**：

```json
{
  "version": "1.0",
  "servers": [
    {
      "name": "prod-web-01",
      "host": "192.168.1.10",
      "ssh_user": "root",
      "ssh_port": 22,
      "ssh_key": "~/.ssh/id_ed25519",
      "ssh_password": "",
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
  },
  "allowed_commands": [
    "/usr/bin/hostname", "/usr/bin/uptime", "/usr/bin/top",
    "/usr/bin/free", "/bin/df", "/usr/bin/netstat",
    "/usr/bin/ss", "/usr/bin/ps", "/usr/bin/systemctl"
  ]
}
```

---

## 定时任务配置

使用 OpenClaw cron，示例：

```json
{
  "name": "server-inspect:daily",
  "schedule": { "kind": "cron", "expr": "0 9 * * *", "tz": "Asia/Shanghai" },
  "payload": {
    "kind": "agentTurn",
    "message": "请执行 server-inspect 巡检：\n1. 查找 server-inspect skill 目录\n2. 运行该目录下的 scripts/run_inspect.py\n3. 读取生成的报告（reports/ 目录下最新的 .md 文件）\n4. 找到 <!-- AI_SUGGESTIONS --> 占位符，注入 AI 分析和建议\n5. 将完整报告内容回复给我\n\n6.如果用户配置了通知工具请调用skill自带的通知函数。"
  },
  "sessionTarget": "isolated",
  "delivery": { "mode": "announce" }
}
```

> **提示**：
> - AI 会自动查找 skill 目录，无需硬编码路径
> - 数据默认保存在 `~/server-inspect/`
> - **Step 6（AI 分析）是必须的，每次都要做**
> - **Step 7（通知）调用 skill 自带的通知函数，数据部分由巡检报告提供，AI 不需要干预**

---

## 巡检指标分组

详见 [references/metrics.md](references/metrics.md)。

| 分组 | 指标 | 告警阈值 |
|------|------|---------|
| 系统基础 | hostname, uptime, who, last, uname | - |
| CPU | 使用率, 负载均值, Top进程 | ≥80% Warning, ≥95% Critical |
| 内存 | 使用率, Swap, Top进程 | ≥85% Warning, ≥95% Critical |
| 磁盘 | 分区使用率, inode, 大目录 | ≥90% Warning, ≥95% Critical |
| 网络 | TCP连接数, TIME_WAIT, 监听端口 | ≥5000 Warning |
| 服务 | 关键服务状态, 进程总数 | 非active → Warning |
| 安全 | 登录失败, 暴力破解, 防火墙 | ≥5次/小时 Warning |

---

## AI 分析提示词

详见 [references/system-prompt.md](references/system-prompt.md)。

分析时遵循：
1. **异常检测优先级**：Critical > Warning > Info
2. **根因推断**：不仅报指标异常，要推断根本原因
3. **可执行建议**：每条建议含具体命令/步骤/风险提示
4. **量化预期**：给出预期效果的具体数字
