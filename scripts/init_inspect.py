#!/usr/bin/env python3
"""
Server Inspect - 初始化脚本
引导用户完成服务器巡检配置
"""

import os
import json
import sys
import subprocess
import re
from pathlib import Path
from datetime import datetime

WORK_DIR = Path.home() / ".qclaw" / "server-inspect"
CONFIG_FILE = WORK_DIR / "config.json"


def print_step(num, total, title):
    print(f"\n{'='*50}")
    print(f"  Step {num}/{total}: {title}")
    print('='*50)


def print_success(msg):
    print(f"  ✅ {msg}")


def print_info(msg):
    print(f"  ℹ️  {msg}")


def print_warn(msg):
    print(f"  ⚠️  {msg}")


def input_servers():
    """收集服务器信息"""
    print("\n请配置要巡检的服务器（输入空行结束）：")
    servers = []
    while True:
        line = input("\n  主机别名（如 prod-web-01，回车跳过）: ").strip()
        if not line:
            break
        name = line
        host = input(f"  主机 IP/域名（{name}）: ").strip()
        if not host:
            print_warn("主机地址不能为空，跳过此台")
            continue
        ssh_user = input(f"  SSH 用户（{name}@{host}）: ").strip() or "root"
        ssh_port = input(f"  SSH 端口（{name}@{host}）: ").strip() or "22"
        ssh_key = input(f"  SSH 私钥路径（默认 ~/.ssh/id_ed25519）: ").strip() or ""

        groups = input("  巡检指标组（逗号分隔，如 cpu,mem,disk,network,security）: ").strip() or "cpu,mem,disk,network,security"

        servers.append({
            "name": name,
            "host": host,
            "ssh_user": ssh_user,
            "ssh_port": int(ssh_port),
            "ssh_key": ssh_key or "~/.ssh/id_ed25519",
            "groups": [g.strip() for g in groups.split(",")],
            "enabled": True,
            "labels": []
        })
        print_success(f"已添加: {name} ({host})")

    return servers


def input_feishu():
    """收集飞书通知配置"""
    print("\n\n飞书机器人通知配置（可选，跳过请直接回车）:")
    webhook = input("  Webhook URL: ").strip()
    if webhook:
        # 验证格式
        if not webhook.startswith("https://open.feishu.cn"):
            print_warn("URL 格式可能不对，但暂时保存")
        print_success("飞书 Webhook 已配置")
    return webhook


def input_email():
    """收集邮件配置"""
    print("\n\n邮件通知配置（可选，跳过请直接回车）:")
    use_email = input("  是否启用邮件通知？(y/N): ").strip().lower()
    if use_email != 'y':
        return {}

    smtp_host = input("  SMTP 服务器: ").strip()
    smtp_port = input("  SMTP 端口（默认 587）: ").strip() or "587"
    smtp_user = input("  SMTP 用户名: ").strip()
    smtp_password = input("  SMTP 密码/App密码: ").strip()
    to_str = input("  收件人（逗号分隔）: ").strip()

    return {
        "smtp_host": smtp_host,
        "smtp_port": int(smtp_port),
        "smtp_user": smtp_user,
        "smtp_password": smtp_password,
        "from": smtp_user,
        "to": [e.strip() for e in to_str.split(",") if e.strip()]
    }


def generate_default_thresholds():
    """生成默认告警阈值"""
    return {
        "cpu_percent": 80,
        "mem_percent": 85,
        "disk_percent": 90,
        "disk_inode_percent": 90,
        "loadavg_1m": 4,
        "swap_percent": 50,
        "netstat_connections": 5000,
        "failed_login_per_hour": 5,
        "tcp_timewait": 5000
    }


def generate_allowed_commands():
    """生成巡检命令白名单"""
    return [
        "/usr/bin/hostname",
        "/usr/bin/uptime",
        "/usr/bin/w",
        "/usr/bin/who",
        "/usr/bin/last",
        "/usr/bin/top",
        "/usr/bin/free",
        "/bin/df",
        "/usr/bin/netstat",
        "/usr/bin/ss",
        "/usr/bin/ps",
        "/usr/bin/systemctl",
        "/bin/journalctl",
        "/usr/bin/dmesg",
        "/usr/bin/vmstat",
        "/usr/bin/iostat",
        "/usr/bin/mpstat",
        "/usr/bin/ls",
        "/usr/bin/cat",
        "/usr/bin/grep",
        "/usr/bin/awk",
        "/usr/bin/sort",
        "/usr/bin/head",
        "/usr/bin/tail",
        "/usr/bin/find",
        "/usr/bin/du",
        "/usr/bin/ping",
        "/usr/bin/ssh"
    ]


def create_default_config(servers, feishu_webhook, email_config):
    """生成默认配置"""
    return {
        "version": "1.0",
        "servers": servers,
        "alert_thresholds": generate_default_thresholds(),
        "notification": {
            "feishu_webhook": feishu_webhook,
            "email": email_config
        },
        "allowed_commands": generate_allowed_commands(),
        "report_template": "standard",
        "history_retention_days": 90,
        "created_at": datetime.now().isoformat()
    }


def test_ssh_connection(server):
    """测试 SSH 连接"""
    try:
        key = server.get("ssh_key", "~/.ssh/id_ed25519")
        key = os.path.expanduser(key)

        cmd = [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=5",
            "-i", key,
            "-p", str(server.get("ssh_port", 22)),
            f"{server['ssh_user']}@{server['host']}",
            "echo ok && uname -a && cat /proc/cpuinfo | grep 'processor' | wc -l && free -h | head -2"
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

        if result.returncode == 0 and "ok" in result.stdout:
            return True, result.stdout
        else:
            return False, result.stderr
    except Exception as e:
        return False, str(e)


def save_config(config):
    """保存配置文件"""
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    print_success(f"配置文件已保存到: {CONFIG_FILE}")


def create_directories():
    """创建必要目录"""
    dirs = ["history", "reports", "logs"]
    for d in dirs:
        (WORK_DIR / d).mkdir(parents=True, exist_ok=True)
    print_success(f"目录结构已创建: {WORK_DIR}")
    for d in dirs:
        print_info(f"  - {WORK_DIR/d}")


def print_summary(config):
    """打印配置摘要"""
    print("\n" + "="*50)
    print("  📋 配置摘要")
    print("="*50)

    print(f"\n  🖥️  服务器数量: {len(config['servers'])} 台")
    for s in config['servers']:
        status = "🟢" if s.get('enabled', True) else "⚪"
        print(f"    {status} {s['name']} ({s['host']}) - {s['ssh_user']}@{s['host']}:{s['ssh_port']}")

    print(f"\n  📊 告警阈值:")
    for k, v in config['alert_thresholds'].items():
        print(f"    - {k}: {v}")

    print(f"\n  🔔 通知渠道:")
    if config['notification']['feishu_webhook']:
        print(f"    - 飞书 Webhook: ✅ 已配置")
    else:
        print(f"    - 飞书 Webhook: ❌ 未配置")
    if config['notification']['email']:
        print(f"    - 邮件 SMTP: ✅ 已配置")
    else:
        print(f"    - 邮件 SMTP: ❌ 未配置")

    print(f"\n  📂 存储路径:")
    print(f"    - 配置: {CONFIG_FILE}")
    print(f"    - 历史: {WORK_DIR}/history/")
    print(f"    - 报告: {WORK_DIR}/reports/")


def main():
    print("\n" + "="*50)
    print("  🔍 Server Inspect 初始化向导")
    print("  Linux 服务器自动化巡检工具")
    print("="*50)

    # Step 1: 服务器列表
    print_step(1, 4, "配置服务器列表")
    servers = input_servers()

    if not servers:
        print_warn("未添加任何服务器，将创建仅本机巡检配置")
        servers = [{
            "name": "localhost",
            "host": "127.0.0.1",
            "ssh_user": os.environ.get("USER", "root"),
            "ssh_port": 22,
            "ssh_key": "",
            "groups": ["cpu", "mem", "disk", "network"],
            "enabled": True,
            "labels": ["本机"]
        }]

    # Step 2: 飞书配置
    print_step(2, 4, "配置通知渠道")
    feishu_webhook = input_feishu()

    # Step 3: 邮件配置
    email_config = input_email()

    # Step 4: 测试连接 + 保存
    print_step(4, 4, "测试连接并保存配置")

    # 测试服务器连接
    test_results = []
    for s in servers:
        if s['host'] != '127.0.0.1' and s['host'] != 'localhost':
            print(f"\n  测试连接 {s['name']} ({s['host']})...", end=" ")
            ok, msg = test_ssh_connection(s)
            if ok:
                print_success("成功")
                test_results.append((s['name'], True, msg))
            else:
                print_warn(f"失败: {msg[:50]}")
                test_results.append((s['name'], False, msg))
        else:
            print_success(f"{s['name']} (本机跳过连接测试)")
            test_results.append((s['name'], True, "localhost"))

    # 生成并保存配置
    config = create_default_config(servers, feishu_webhook, email_config)
    create_directories()
    save_config(config)

    # 打印摘要
    print_summary(config)

    # 后续步骤说明
    print("\n" + "="*50)
    print("  ✅ 初始化完成！")
    print("="*50)

    print("""
  后续步骤:

  1. 将巡检命令加入白名单（推荐）
     - 请确认是否需要我将以下命令加入 exec 白名单:
       /usr/bin/hostname, /usr/bin/uptime, /usr/bin/top,
       /usr/bin/free, /bin/df, /usr/bin/netstat, ...
     - 输入 'y' 确认，或手动编辑 gateway 配置

  2. 执行首次巡检
     > inspect

  3. 查看巡检报告
     > report

  4. 设置定时任务
     > cron add daily

  5. 配置邮件通知（如果需要）
     > notify config email
    """)

    return config


if __name__ == "__main__":
    main()
