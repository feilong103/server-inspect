#!/usr/bin/env python3
"""
Server Inspect - 通知脚本
发送巡检报告到飞书/邮件

用法：
    python3 notify.py              # 发送所有通知（飞书 + 邮件）
    python3 notify.py --feishu     # 只发送飞书
    python3 notify.py --email      # 只发送邮件
"""

import json
import sys
import re
import smtplib
from datetime import datetime
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from dataclasses import dataclass, field

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

WORK_DIR = Path.home() / "server-inspect"
CONFIG_FILE = WORK_DIR / "config.json"


# ==================== 数据结构 ====================

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


# ==================== 飞书通知 ====================

class FeishuNotifier:
    """飞书卡片通知"""

    STATUS_COLOR = {
        "green": {"header": "🟢 正常", "template": "green"},
        "yellow": {"header": "🟡 关注", "template": "yellow"},
        "red": {"header": "🔴 严重", "template": "red"},
    }

    @staticmethod
    def _overall_status(reports: list) -> dict:
        has_crit = any(a.get("level") == "CRITICAL" for r in reports for a in r.alerts)
        has_warn = any(a.get("level") == "WARNING" for r in reports for a in r.alerts)
        if has_crit:
            return FeishuNotifier.STATUS_COLOR["red"]
        elif has_warn:
            return FeishuNotifier.STATUS_COLOR["yellow"]
        else:
            return FeishuNotifier.STATUS_COLOR["green"]

    @staticmethod
    def _server_table(reports: list, thresholds: dict) -> str:
        lines = ["| 主机 | CPU | 内存 | 磁盘 | 安全 | 状态 |", "| --- | --- | --- | --- | --- | --- |"]
        for r in reports:
            cp = getattr(r, 'cpu_pct', 0.0)
            mp = getattr(r, 'mem_pct', 0.0)
            dp = getattr(r, 'disk_pct', 0)
            ct, mt, dt = thresholds.get("cpu_percent", 80), thresholds.get("mem_percent", 85), thresholds.get("disk_percent", 90)
            cpu_i = "✅" if cp < ct * 0.9 else ("🟠" if cp < ct else "🔴")
            mem_i = "✅" if mp < mt * 0.9 else ("🟠" if mp < mt else "🔴")
            disk_i = "✅" if dp < dt * 0.9 else ("🟠" if dp < dt else "🔴")
            has_login = any("登录" in a.get("message", "") for a in r.alerts)
            safe_i = "⚠️" if has_login else "✅"
            has_crit = any(a.get("level") == "CRITICAL" for a in r.alerts)
            has_warn = any(a.get("level") == "WARNING" for a in r.alerts)
            st = "🔴 严重" if has_crit else ("🟠 关注" if has_warn else "🟢 正常")
            name = f"`{r.name}`" if r.name else "`未知`"
            lines.append(f"| {name} | {cpu_i} {cp:.0f}% | {mem_i} {mp:.0f}% | {disk_i} {dp:.0f}% | {safe_i} | {st} |")
        return "\n".join(lines)

    @staticmethod
    def _alerts_text(reports: list) -> str:
        lines = []
        for r in reports:
            for a in r.alerts:
                icon = "🔴" if a.get("level") == "CRITICAL" else "🟠"
                lines.append(f"{icon} **{r.name}** {a.get('message', '')}")
        return "\n".join(lines) if lines else "✅ 本次巡检未发现异常"

    @staticmethod
    def _ai_suggestions(reports: list) -> str:
        suggestions = []
        for r in reports:
            for a in r.alerts:
                if a.get("level") == "CRITICAL":
                    suggestions.append(f"1. 紧急处理 {r.name}：{a.get('message', '')}")
                    break
        for r in reports:
            for a in r.alerts:
                if a.get("level") == "WARNING":
                    suggestions.append(f"2. 关注 {r.name}：{a.get('message', '')}")
                    break
        return "\n".join(suggestions[:3]) if suggestions else "继续保持，当前状态良好"

    @staticmethod
    def send(webhook_url: str, reports: list, thresholds: dict, report_path: str):
        if not webhook_url or webhook_url.strip() == "":
            return False

        ts = datetime.now().strftime("%Y-%m-%d")
        status_info = FeishuNotifier._overall_status(reports)
        total_time = sum(r.duration_ms for r in reports) // 1000

        payload = {
            "msg_type": "interactive",
            "card": {
                "schema": "2.0",
                "header": {
                    "title": {"tag": "plain_text", "content": f"🖥️ 服务器巡检报告 - {ts}"},
                    "template": status_info["template"],
                },
                "body": {
                    "elements": [
                        {"tag": "div", "text": {"tag": "lark_md", "content": f"**巡检时间** {ts} {datetime.now().strftime('%H:%M:%S')}　｜　**耗时** {total_time}s　｜　**服务器数量** {len(reports)} 台"}},
                        {"tag": "hr"},
                        {"tag": "div", "text": {"tag": "lark_md", "content": "**📊 巡检结果概览（多主机列表）**"}},
                        {"tag": "div", "text": {"tag": "lark_md", "content": FeishuNotifier._server_table(reports, thresholds)}},
                        {"tag": "hr"},
                        {"tag": "div", "text": {"tag": "lark_md", "content": "**⚠️ 需要关注的问题**"}},
                        {"tag": "div", "text": {"tag": "lark_md", "content": FeishuNotifier._alerts_text(reports)}},
                        {"tag": "hr"},
                        {"tag": "div", "text": {"tag": "lark_md", "content": "**💡 AI 建议**"}},
                        {"tag": "div", "text": {"tag": "lark_md", "content": FeishuNotifier._ai_suggestions(reports)}},
                        {"tag": "hr"},
                        {"tag": "div", "text": {"tag": "lark_md", "content": f"<font color=\"grey\">📄 完整报告已保存至 {report_path}</font>"}},
                    ]
                },
            },
        }

        try:
            if HAS_AIOHTTP:
                import asyncio
                async def post_async():
                    async with aiohttp.ClientSession() as session:
                        async with session.post(webhook_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                            return await resp.json()
                result = asyncio.run(post_async())
            else:
                import urllib.request
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(webhook_url, data=data, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
            
            if result.get("code") == 0:
                print("📮 飞书通知已发送")
                return True
            else:
                print(f"⚠️ 飞书通知失败: {result.get('msg')}")
                return False
        except Exception as e:
            print(f"⚠️ 飞书通知异常: {e}")
            return False


# ==================== 邮件通知 ====================

class EmailNotifier:
    """邮件通知（HTML 格式）"""

    STATUS_COLOR = {
        "green": {"bg": "#E8F5E9", "border": "#4CAF50", "text": "#2E7D32", "status": "🟢 正常"},
        "yellow": {"bg": "#FFF9C4", "border": "#FBC02D", "text": "#F57F17", "status": "🟡 关注"},
        "red": {"bg": "#FFEBEE", "border": "#F44336", "text": "#C62828", "status": "🔴 严重"},
    }

    @staticmethod
    def _overall_status(reports: list) -> str:
        has_crit = any(a.get("level") == "CRITICAL" for r in reports for a in r.alerts)
        has_warn = any(a.get("level") == "WARNING" for r in reports for a in r.alerts)
        return "red" if has_crit else ("yellow" if has_warn else "green")

    @staticmethod
    def _server_table_html(reports: list, thresholds: dict) -> str:
        rows = []
        for r in reports:
            cp = getattr(r, 'cpu_pct', 0.0)
            mp = getattr(r, 'mem_pct', 0.0)
            dp = getattr(r, 'disk_pct', 0)
            ct, mt, dt = thresholds.get("cpu_percent", 80), thresholds.get("mem_percent", 85), thresholds.get("disk_percent", 90)
            cpu_i = "✅" if cp < ct * 0.9 else ("🟠" if cp < ct else "🔴")
            mem_i = "✅" if mp < mt * 0.9 else ("🟠" if mp < mt else "🔴")
            disk_i = "✅" if dp < dt * 0.9 else ("🟠" if dp < dt else "🔴")
            has_login = any("登录" in a.get("message", "") for a in r.alerts)
            safe_i = "⚠️" if has_login else "✅"
            has_crit = any(a.get("level") == "CRITICAL" for a in r.alerts)
            has_warn = any(a.get("level") == "WARNING" for a in r.alerts)
            st = "🔴 严重" if has_crit else ("🟠 关注" if has_warn else "🟢 正常")
            rows.append(f"<tr><td><strong>{r.name}</strong></td><td>{cpu_i} {cp:.0f}%</td><td>{mem_i} {mp:.0f}%</td><td>{disk_i} {dp:.0f}%</td><td>{safe_i}</td><td>{st}</td></tr>")
        return "\n".join(rows)

    @staticmethod
    def _alerts_html(reports: list) -> str:
        alerts = []
        for r in reports:
            for a in r.alerts:
                level_class = "alert-critical" if a.get("level") == "CRITICAL" else "alert-warning" if a.get("level") == "WARNING" else "alert-info"
                icon = "🔴" if a.get("level") == "CRITICAL" else "🟠" if a.get("level") == "WARNING" else "🟡"
                alerts.append(f'<div class="alert {level_class}"><strong>{icon} [{a.get("level")}] {r.name}</strong><br>{a.get("message", "")}</div>')
        return "\n".join(alerts) if alerts else '<div class="alert alert-info">✅ 本次巡检未发现异常</div>'

    @staticmethod
    def _suggestions_html(reports: list) -> str:
        suggestions = []
        idx = 1
        for r in reports:
            for a in r.alerts:
                if a.get("level") == "CRITICAL":
                    suggestions.append(f'<div class="suggestion"><strong>建议 {idx}: 紧急 — {r.name} 告警处理</strong><br>问题: {a.get("message", "")}<br>操作步骤:<br>1. 立即检查服务器状态<br>2. 查看相关日志<br>3. 采取应急措施<br>预期效果: 恢复服务正常运行</div>')
                    idx += 1
                    break
        for r in reports:
            for a in r.alerts:
                if a.get("level") == "WARNING":
                    suggestions.append(f'<div class="suggestion"><strong>建议 {idx}: 关注 — {r.name} 监控</strong><br>问题: {a.get("message", "")}<br>操作步骤:<br>1. 持续监控该指标<br>2. 准备应急预案<br>预期效果: 提前发现问题</div>')
                    idx += 1
                    break
        return "\n".join(suggestions[:5]) if suggestions else '<div class="suggestion"><strong>建议: 继续保持</strong><br>当前状态良好，继续保持现有配置。</div>'

    @staticmethod
    def generate_html(reports: list, thresholds: dict, report_path: str) -> str:
        status_color = EmailNotifier._overall_status(reports)
        colors = EmailNotifier.STATUS_COLOR[status_color]
        total_time = sum(r.duration_ms for r in reports) // 1000
        ts = datetime.now()

        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        * {{ margin: 0; padding: 0; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 800px; margin: 0 auto; background: #f5f5f5; padding: 20px; }}
        .card {{ background: white; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); overflow: hidden; margin-bottom: 20px; }}
        .header {{ background: linear-gradient(135deg, {colors['border']} 0%, {colors['border']}dd 100%); color: white; padding: 30px; text-align: center; }}
        .header h1 {{ font-size: 28px; margin-bottom: 10px; }}
        .header p {{ font-size: 14px; opacity: 0.9; }}
        .status-badge {{ display: inline-block; background: white; color: {colors['border']}; padding: 8px 16px; border-radius: 20px; font-weight: bold; margin-top: 10px; }}
        .section {{ padding: 25px; border-bottom: 1px solid #eee; }}
        .section:last-child {{ border-bottom: none; }}
        .section-title {{ font-size: 18px; font-weight: bold; color: {colors['border']}; margin-bottom: 15px; display: flex; align-items: center; }}
        .section-title::before {{ content: ''; display: inline-block; width: 4px; height: 20px; background: {colors['border']}; margin-right: 10px; border-radius: 2px; }}
        .info-grid {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 15px; margin-bottom: 20px; }}
        .info-item {{ background: #f9f9f9; padding: 15px; border-radius: 6px; border-left: 3px solid {colors['border']}; }}
        .info-item .label {{ font-size: 12px; color: #999; text-transform: uppercase; margin-bottom: 5px; }}
        .info-item .value {{ font-size: 20px; font-weight: bold; color: #333; }}
        table {{ width: 100%; border-collapse: collapse; margin: 15px 0; }}
        th {{ background: {colors['bg']}; color: {colors['text']}; padding: 12px; text-align: left; font-weight: 600; border: 1px solid {colors['border']}; }}
        td {{ padding: 12px; border: 1px solid #eee; }}
        tr:nth-child(even) {{ background: #f9f9f9; }}
        .alert {{ padding: 15px; border-radius: 6px; margin: 10px 0; border-left: 4px solid; }}
        .alert-critical {{ background: #FFEBEE; border-color: #F44336; color: #C62828; }}
        .alert-warning {{ background: #FFF9C4; border-color: #FBC02D; color: #F57F17; }}
        .alert-info {{ background: #E3F2FD; border-color: #2196F3; color: #1565C0; }}
        .suggestion {{ background: #F3E5F5; border-left: 4px solid #9C27B0; padding: 15px; margin: 10px 0; border-radius: 4px; }}
        .suggestion strong {{ color: #6A1B9A; }}
        .footer {{ background: #f5f5f5; padding: 20px; text-align: center; font-size: 12px; color: #999; border-top: 1px solid #eee; }}
        code {{ background: #f0f0f0; padding: 2px 6px; border-radius: 3px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            <div class="header">
                <h1>🖥️ 服务器巡检报告</h1>
                <p>{ts.strftime('%Y年%m月%d日 %H:%M:%S')}</p>
                <div class="status-badge">{colors['status']}</div>
            </div>
        </div>

        <div class="card">
            <div class="section">
                <div class="section-title">📊 巡检概览</div>
                <div class="info-grid">
                    <div class="info-item">
                        <div class="label">巡检时间</div>
                        <div class="value">{ts.strftime('%H:%M:%S')}</div>
                    </div>
                    <div class="info-item">
                        <div class="label">耗时</div>
                        <div class="value">{total_time}s</div>
                    </div>
                    <div class="info-item">
                        <div class="label">服务器数量</div>
                        <div class="value">{len(reports)} 台</div>
                    </div>
                </div>
            </div>
        </div>

        <div class="card">
            <div class="section">
                <div class="section-title">📈 巡检结果详情</div>
                <table>
                    <thead>
                        <tr>
                            <th>主机</th>
                            <th>CPU</th>
                            <th>内存</th>
                            <th>磁盘</th>
                            <th>安全</th>
                            <th>状态</th>
                        </tr>
                    </thead>
                    <tbody>
                        {EmailNotifier._server_table_html(reports, thresholds)}
                    </tbody>
                </table>
            </div>
        </div>

        <div class="card">
            <div class="section">
                <div class="section-title">⚠️ 详细告警分析</div>
                {EmailNotifier._alerts_html(reports)}
            </div>
        </div>

        <div class="card">
            <div class="section">
                <div class="section-title">💡 AI 优化建议</div>
                {EmailNotifier._suggestions_html(reports)}
            </div>
        </div>

        <div class="card">
            <div class="section">
                <div class="section-title">📄 完整报告</div>
                <p style="color: #666; font-size: 14px;">
                    完整的 Markdown 报告已作为附件发送，包含原始命令输出和详细分析。<br>
                    报告已保存至: <code>{report_path}</code>
                </p>
            </div>
        </div>

        <div class="footer">
            <p>此邮件由 OpenClaw Server Inspect 自动生成，请勿直接回复。</p>
            <p>如有问题，请联系系统管理员。</p>
        </div>
    </div>
</body>
</html>"""
        return html

    @staticmethod
    def send(smtp_config: dict, reports: list, thresholds: dict, report_path: str):
        if not smtp_config or not smtp_config.get("smtp_host"):
            return False
        
        try:
            html_content = EmailNotifier.generate_html(reports, thresholds, report_path)
            
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f"🖥️ 服务器巡检报告 - {datetime.now().strftime('%Y-%m-%d')}"
            msg['From'] = smtp_config.get("from", smtp_config.get("smtp_user"))
            msg['To'] = ", ".join(smtp_config.get("to", []))
            
            msg.attach(MIMEText(html_content, 'html', 'utf-8'))
            
            # 添加 Markdown 报告附件
            if Path(report_path).exists():
                with open(report_path, 'r', encoding='utf-8') as f:
                    report_content = f.read()
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(report_content.encode('utf-8'))
                encoders.encode_base64(part)
                part.add_header('Content-Disposition', 'attachment', filename=Path(report_path).name)
                msg.attach(part)
            
            # 发送邮件（优先用 SSL 465，失败则用 STARTTLS 587）
            port = smtp_config.get("smtp_port", 465)
            try:
                if port == 465:
                    server = smtplib.SMTP_SSL(smtp_config.get("smtp_host"), port, timeout=30)
                else:
                    server = smtplib.SMTP(smtp_config.get("smtp_host"), port, timeout=30)
                    server.starttls()
                server.login(smtp_config.get("smtp_user"), smtp_config.get("smtp_password"))
                server.send_message(msg)
                server.quit()
                print(f"📧 邮件已发送到 {', '.join(smtp_config.get('to', []))}")
                return True
            except Exception as e1:
                # 如果 465 失败，尝试 587
                if port == 465:
                    server = smtplib.SMTP(smtp_config.get("smtp_host"), 587, timeout=30)
                    server.starttls()
                    server.login(smtp_config.get("smtp_user"), smtp_config.get("smtp_password"))
                    server.send_message(msg)
                    server.quit()
                    print(f"📧 邮件已发送到 {', '.join(smtp_config.get('to', []))}")
                    return True
                else:
                    raise e1
        except Exception as e:
            print(f"⚠️ 邮件发送失败: {e}")
            return False


# ==================== 报告数据提取 ====================

def extract_from_report(report_path: Path, config: dict) -> list:
    """从 Markdown 报告和历史数据中提取 ServerReport 对象"""
    thresholds = config.get("alert_thresholds", {})
    reports = []
    
    # 读取报告内容
    with open(report_path, 'r', encoding='utf-8') as f:
        report_content = f.read()
    
    # 从报告中提取服务器信息
    server_pattern = r'### \d+\.\d+ [🟢🟠🔴] (.+?)（(.+?)）'
    servers_info = re.findall(server_pattern, report_content)
    
    for name, host in servers_info:
        alerts = []
        
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
        
        # 从报告中提取告警
        alert_pattern = rf'\| [🔴🟠🟡] (\w+) \| {re.escape(name)} \| (.+?) \|'
        found_alerts = re.findall(alert_pattern, report_content)
        for level, message in found_alerts:
            alerts.append({"level": level.upper(), "message": message.strip()})
        
        # 如果没有从表格中提取到告警，基于阈值判定
        if not alerts:
            if cpu_pct >= thresholds.get("cpu_percent", 80):
                alerts.append({
                    "level": "CRITICAL" if cpu_pct >= 95 else "WARNING",
                    "message": f"CPU 使用率 {cpu_pct:.1f}% 超过阈值"
                })
            if mem_pct >= thresholds.get("mem_percent", 85):
                alerts.append({
                    "level": "CRITICAL" if mem_pct >= 95 else "WARNING",
                    "message": f"内存使用率 {mem_pct:.1f}% 超过阈值"
                })
            if disk_pct >= thresholds.get("disk_percent", 90):
                alerts.append({
                    "level": "CRITICAL" if disk_pct >= 95 else "WARNING",
                    "message": f"磁盘使用率 {disk_pct}% 超过阈值"
                })
        
        # 从日志中提取登录失败信息
        log_files = sorted((WORK_DIR / "logs").glob("*.log"))
        if log_files:
            latest_log = log_files[-1]
            with open(latest_log) as f:
                content = f.read()
                section_start = content.find(f"[=== {name} (")
                if section_start != -1:
                    section_end = content.find("[===", section_start + 10)
                    section = content[section_start:section_end] if section_end != -1 else content[section_start:]
                    failed_count = len([l for l in section.split("\n") if "Failed password" in l])
                    if failed_count >= 5:
                        alerts.append({
                            "level": "WARNING",
                            "message": f"发现 {failed_count} 次登录失败，存在暴力破解风险"
                        })
        
        overall_status = "CRITICAL" if any(a["level"] == "CRITICAL" for a in alerts) else \
                        "WARNING" if any(a["level"] == "WARNING" for a in alerts) else "NORMAL"
        
        report = ServerReport(
            name=name,
            host=host,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            duration_ms=0,
            metrics={},
            alerts=alerts,
            overall_status=overall_status
        )
        # 添加属性用于通知
        report.cpu_pct = cpu_pct
        report.mem_pct = mem_pct
        report.disk_pct = disk_pct
        reports.append(report)
    
    return reports


# ==================== 主函数 ====================

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
    feishu_sent = False
    if send_feishu:
        feishu_webhook = notification.get("feishu_webhook", "")
        if feishu_webhook:
            print("\n📤 发送飞书通知...")
            feishu_sent = FeishuNotifier.send(feishu_webhook, reports, thresholds, str(latest_report))
        else:
            print("\n⚠️ 未配置飞书 Webhook")
            print("请在 config.json 中配置：")
            print('  "notification": {')
            print('    "feishu_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"')
            print('  }')
    
    # 6. 发送邮件通知
    send_email = not args.feishu or args.email
    email_sent = False
    if send_email:
        email_config = notification.get("email", {})
        if email_config and email_config.get("smtp_host"):
            print("\n📤 发送邮件通知...")
            email_sent = EmailNotifier.send(email_config, reports, thresholds, str(latest_report))
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
    
    # 7. 输出结果
    if feishu_sent or email_sent:
        print("\n✅ 通知发送完成!")
    else:
        print("\n⚠️ 未发送任何通知，请检查配置")


if __name__ == "__main__":
    main()