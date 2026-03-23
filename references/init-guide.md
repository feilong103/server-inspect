# 初始化指南（init 命令详解）

## 准备工作

### 1. 确认 SSH 免密访问

在执行初始化前，确保已配置 SSH 免密登录目标服务器：

```bash
# 生成本机 SSH 密钥（如尚未生成）
ssh-keygen -t ed25519 -C "openclaw-inspect"

# 复制公钥到目标服务器
ssh-copy-id -i ~/.ssh/id_ed25519.pub admin@192.168.1.10
```

### 2. 确认 Python 环境（可选，用于结构化解析）

```bash
python3 --version  # 推荐 3.8+
```

---

## 初始化步骤详解

### Step 1：创建工作目录

```bash
mkdir -p ~/.qclaw/server-inspect/{history,reports,logs}
```

### Step 2：生成配置文件

运行 `init` 后，AI 会引导填写以下信息：

#### 必填项

| 配置项 | 说明 | 示例 |
|-------|------|------|
| 服务器列表 | 主机名/IP、SSH 用户、端口 | prod-web-01, 192.168.1.10, admin, 22 |
| SSH 连接方式 | 密码/密钥 | 密钥（推荐） |
| 指标分组 | 选择要巡检的指标组 | CPU、内存、磁盘、网络、安全 |

#### 选填项

| 配置项 | 说明 | 默认值 |
|-------|------|-------|
| 飞书 Webhook URL | 机器人通知地址 | 空（仅本地报告） |
| 邮件 SMTP | 发件服务器配置 | 空 |
| 告警阈值 | 覆盖默认阈值 | 见 metrics.md |
| 报告语言 | 中文/English | 中文 |

### Step 3：SSH 连接测试

初始化时会**自动测试**每台服务器的连通性：

```
测试连接 prod-web-01 (192.168.1.10:22)...
✅ 连接成功
   - 系统: Linux 5.4.0
   - CPU: 4 核
   - 内存: 7.6G

测试连接 prod-db-01 (192.168.1.20:22)...
✅ 连接成功
   - 系统: Linux 4.18.0
   - CPU: 8 核
   - 内存: 31G
```

### Step 4：命令白名单注册

初始化完成后，会提示是否将巡检命令加入白名单：

```
检测到以下巡检命令需要加白：
  /usr/bin/hostname   /usr/bin/uptime   /usr/bin/top
  /usr/bin/free      /bin/df           /usr/bin/netstat
  /usr/bin/ss        /usr/bin/ps       /usr/bin/systemctl
  /bin/journalctl    /usr/bin/last     /usr/bin/who
  ...

是否将上述命令加入 exec 白名单？（需要重启 Gateway）
  1) 是，立即加入并重启
  2) 否，先跳过（后续手动配置）
```

**重要**：如果拒绝，巡检时命令需要逐个审批。

### Step 5：测试巡检

初始化完成后自动运行一次轻量级巡检验证：

```
✅ 初始化完成！正在执行首次巡检验证...
```

---

## 配置文件手动编辑

配置文件位于 `~/.qclaw/server-inspect/config.json`，可直接编辑：

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
      "groups": ["系统基础", "CPU", "内存", "磁盘", "网络", "安全"],
      "enabled": true,
      "labels": ["生产", "Web"]
    },
    {
      "name": "prod-db-01",
      "host": "192.168.1.20",
      "ssh_user": "dba",
      "ssh_port": 22,
      "groups": ["系统基础", "CPU", "内存", "磁盘", "服务"],
      "enabled": true,
      "labels": ["生产", "数据库"]
    },
    {
      "name": "dev-server",
      "host": "192.168.1.100",
      "ssh_user": "dev",
      "ssh_port": 22,
      "groups": ["系统基础", "CPU", "内存"],
      "enabled": false,
      "labels": ["开发"]
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
      "smtp_host": "smtp.company.com",
      "smtp_port": 587,
      "smtp_user": "alarm@company.com",
      "smtp_password": "",
      "from": "alarm@company.com",
      "to": ["admin@company.com", "ops@company.com"]
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
    "/bin/journalctl"
  ]
}
```

---

## 常见问题

### Q: 如何添加新的巡检服务器？

运行 `inspect --add-server`，或直接编辑 `config.json` 的 `servers` 数组。

### Q: 如何自定义巡检命令？

在 `allowed_commands` 中添加新的命令路径，并更新 `metrics_groups` 映射。

### Q: 如何仅巡检特定服务器？

```bash
inspect --host prod-web-01
inspect --group cpu,mem
```

### Q: 飞书通知需要什么权限？

在飞书群中添加「自定义机器人」，获取 Webhook 地址即可，无需特殊权限。

### Q: SSH 密码方式支持吗？

支持，但建议使用密钥方式，避免密码明文存储。
