#!/usr/bin/env python3
"""
发送巡检通知 - 飞书 + 邮件
独立脚本，可直接调用，无需 agent 实现提取逻辑

用法：
    python3 send_notifications.py              # 发送最新报告
    python3 send_notifications.py --feishu     # 只发送飞书
    python3 send_notifications.py --email      # 只发送邮件
"""

import json
import sys
import re
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field

# 添加 scripts 目录到路径
sys.path.insert(0, str(Path(__file__).parent / "scripts"))

try:
    from notifier import FeishuNotifier, EmailNotifier
except ImportError:
    print("❌ 无法导入 notifier 模块，请确保 scripts/notifier.py 存在")
    sys.exit(1)

WORK_DIR = Path.home() / "server-inspect"
CONFIG_FILE = WORK_DIR / "config.json"


@dataclass
class MetricResult:
    metric_id: str
    raw_output: str


@dataclass
class ServerReport:
    name: str
    host: str
    timestamp: str
    duration_ms: int
    metrics: dict = field(default_factory=dict)
    alerts: list = field(default_factory=list)
    overall_status: str = "NORMAL"


def extract_from_report(report_path: Path, config: dict) -> list:
    """从 Markdown 报告和历史数据中提取 ServerReport 对象"""
    thresholds = config.get("alert_thresholds", {})
    reports = []
    
    # 读取报告内容
    with open(report_path, 'r', encoding='utf-8') as f:
        report_content = f.read()
    
    # 从报告中提取服务器信息
    # 匹配格式：### 2.1 🟠 OpenClaw（172.16.180.181）
    server_pattern = r'### \d+\.\d+ [🟢🟠🔴] (.+?)（(.+?)）'
    servers_info = re.findall(server_pattern, report_content)
    
    for name, host in servers_info:
        alerts = []
        metrics = {}
        
        # 查找该服务器的历史数据
        history_dir = WORK_DIR / "history" / name
        cpu_pct = 0.0
        mem_pct = 0.0
        disk_pct = 0
        
        if history_dir.exists():
            history_files = sorted(history_dir.glob("*.jsonl"))
            if history_files:
                latest_history = history_files[-1]
                with open(latest_history) as f:
                    for line in f:
                        try:
                            rec = json.loads(line)
                            cpu_pct = rec.get("cpu_pct", 0.0)
                            mem_pct = rec.get("mem_pct", 0.0)
                            disk_pct = rec.get("disk_pct", 0)
                        except:
                            pass
        
        # 从报告中提取该服务器的告警信息
        # 匹配格式：| 🔴 Critical | OpenClaw | ... |
        alert_pattern = rf'\| [🔴🟠🟡] (\w+) \| {re.escape(name)} \| (.+?) \|'
        found_alerts = re.findall(alert_pattern, report_content)
        for level, message in found_alerts:
            alerts.append({"level": level.upper(), "message": message.strip()})
        
        # 如果没有从表格中提取到告警，尝试从报告中解析
        if not alerts:
            # 检查 CPU 告警
            if cpu_pct >= thresholds.get("cpu_percent", 80):
                alerts.append({
                    "level": "CRITICAL" if cpu_pct >= 95 else "WARNING",
                    "message": f"CPU 使用率 {cpu_pct:.1f}% 超过阈值 {thresholds.get('cpu_percent', 80)}%"
                })
            
            # 检查内存告警
            if mem_pct >= thresholds.get("mem_percent", 85):
                alerts.append({
                    "level": "CRITICAL" if mem_pct >= 95 else "WARNING",
                    "message": f"内存使用率 {mem_pct:.1f}% 超过阈值 {thresholds.get('mem_percent', 85)}%"
                })
            
            # 检查磁盘告警
            if disk_pct >= thresholds.get("disk_percent", 90):
                alerts.append({
                    "level": "CRITICAL" if disk_pct >= 95 else "WARNING",
                    "message": f"磁盘使用率 {disk_pct}% 超过阈值 {thresholds.get('disk_percent', 90)}%"
                })
        
        # 从日志文件中提取登录失败信息
        log_files = sorted((WORK_DIR / "logs").glob("*.log"))
        if log_files:
            latest_log = log_files[-1]
            with open(latest_log) as f:
                content = f.read()
                section_start = content.find(f"[=== {name} (")
                if section_start != -1:
                    section_end = content.find("[===", section_start + 10)
                    section = content[section_start:section_end] if section_end != -1 else content[section_start:]
                    
                    # 统计登录失败次数
                    failed_count = len([l for l in section.split("\n") if "Failed password" in l])
                    if failed_count >= 5:
                        alerts.append({
                            "level": "WARNING",
                            "message": f"发现 {failed_count} 次登录失败，存在暴力破解风险"
                        })
        
        # 创建 ServerReport 对象
        overall_status = "CRITICAL" if any(a["level"] == "CRITICAL" for a in alerts) else \
                        "WARNING" if any(a["level"] == "WARNING" for a in alerts) else "NORMAL"
        
        report = ServerReport(
            name=name,
            host=host,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            duration_ms=0,
            metrics=metrics,
            alerts=alerts,
            overall_status=overall_status
        )
        reports.append(report)
    
    return reports


def main():
    import argparse
    parser = argparse.ArgumentParser(description="发送巡检通知")
    parser.add_argument("--feishu", action="store_true", help="只发送飞书")
    parser.add_argument("--email", action="store_true", help="只发送邮件")
    args = parser.parse_args()
    
    # 1. 检查配置文件
    if not CONFIG_FILE.exists():
        print(f"❌ 配置文件不存在: {CONFIG_FILE}")
        print("请先运行巡检或手动创建配置文件")
        sys.exit(1)
    
    config = json.load(open(CONFIG_FILE))
    thresholds = config.get("alert_thresholds", {})
    notification = config.get("notification", {})
    
    # 2. 检查报告目录
    reports_dir = WORK_DIR / "reports"
    if not reports_dir.exists():
        print(f"❌ 报告目录不存在: {reports_dir}")
        print("请先执行巡检生成报告")
        sys.exit(1)
    
    # 3. 获取最新报告
    report_files = list(reports_dir.glob("report_*.md"))
    if not report_files:
        print(f"❌ 未找到报告文件")
        print("请先执行巡检生成报告")
        sys.exit(1)
    
    latest_report = max(report_files, key=lambda p: p.stat().st_mtime)
    print(f"📄 读取报告: {latest_report}")
    
    # 4. 从报告中提取数据
    reports = extract_from_report(latest_report, config)
    if not reports:
        print("❌ 无法从报告中提取服务器信息")
        sys.exit(1)
    
    print(f"📊 提取到 {len(reports)} 台服务器的数据")
    for r in reports:
        status_icon = "🔴" if r.overall_status == "CRITICAL" else "🟠" if r.overall_status == "WARNING" else "🟢"
        print(f"   {status_icon} {r.name} ({r.host}) - {len(r.alerts)} 个告警")
    
    # 5. 发送飞书通知
    send_feishu = not args.email or args.feishu
    if send_feishu:
        feishu_webhook = notification.get("feishu_webhook", "")
        if feishu_webhook:
            print("\n📤 发送飞书通知...")
            FeishuNotifier.send(feishu_webhook, reports, thresholds, str(latest_report))
        else:
            print("\n⚠️ 未配置飞书 Webhook")
            print("请在 config.json 中配置：")
            print('  "notification": {')
            print('    "feishu_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"')
            print('  }')
    
    # 6. 发送邮件通知
    send_email = not args.feishu or args.email
    if send_email:
        email_config = notification.get("email", {})
        if email_config and email_config.get("smtp_host"):
            print("\n📤 发送邮件通知...")
            EmailNotifier.send(email_config, reports, thresholds, str(latest_report))
        else:
            print("\n⚠️ 未配置邮件 SMTP")
            print("请在 config.json 中配置：")
            print('  "notification": {')
            print('    "email": {')
            print('      "smtp_host": "smtp.qq.com",')
            print('      "smtp_port": 465,')
            print('      "smtp_user": "your-email@qq.com",')
            print('      "smtp_password": "your-auth-code",')
            print('      "from": "your-email@qq.com",')
            print('      "to": ["recipient@example.com"]')
            print('    }')
            print('  }')
    
    print("\n✅ 通知发送完成!")


if __name__ == "__main__":
    main()