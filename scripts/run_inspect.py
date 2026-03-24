#!/usr/bin/env python3
"""
Server Inspect - 核心巡检脚本
采集指标 → 解析 → 报告生成
AI 分析与建议由 OpenClaw 在读取报告后注入
"""

import os, re, json, sys, subprocess, asyncio, smtplib
from datetime import datetime, timedelta
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

WORK_DIR = Path.home() / "server-inspect"
CONFIG_FILE = WORK_DIR / "config.json"


# ==================== 飞书通知 ====================
class FeishuNotifier:
    """飞书卡片通知"""

    STATUS_COLOR = {
        "green":  {"header": "🟢 正常",   "template": "green"},
        "yellow": {"header": "🟡 关注",   "template": "yellow"},
        "red":    {"header": "🔴 严重",   "template": "red"},
    }

    @staticmethod
    def _status_icon(value: float, warn: float, crit: float) -> str:
        if value >= crit: return "🔴"
        if value >= warn: return "🟠"
        return "✅"

    @staticmethod
    def _overall_status(reports: list) -> dict:
        """根据告警情况返回卡片颜色和状态"""
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
        """生成多主机巡检结果表格"""
        lines = ["| 主机 | CPU | 内存 | 磁盘 | 安全 | 状态 |",
                 "| --- | --- | --- | --- | --- | --- |"]
        for r in reports:
            cp = ReportGenerator._cpu_pct(r.metrics.get("top", MetricResult("", "")).raw_output) if "top" in r.metrics else 0.0
            mp = ReportGenerator._mem_pct(r.metrics.get("mem_usage", MetricResult("", "")).raw_output) if "mem_usage" in r.metrics else 0.0
            parts = ReportGenerator._partitions(r.metrics.get("disk_usage", MetricResult("", "")).raw_output) if "disk_usage" in r.metrics else []
            dp = parts[0]["usage"] if parts else 0
            ct, mt, dt = thresholds.get("cpu_percent", 80), thresholds.get("mem_percent", 85), thresholds.get("disk_percent", 90)
            cpu_i, mem_i = FeishuNotifier._status_icon(cp, ct * 0.9, ct), FeishuNotifier._status_icon(mp, mt * 0.9, mt)
            disk_i = FeishuNotifier._status_icon(dp, dt * 0.9, dt)
            has_login = any("登录" in a.get("message", "") for a in r.alerts)
            safe_i = "⚠️" if has_login else "✅"
            has_crit = any(a.get("level") == "CRITICAL" for a in r.alerts)
            has_warn = any(a.get("level") == "WARNING" for a in r.alerts)
            if has_crit:
                st = "🔴 严重"
            elif has_warn:
                st = "🟠 关注"
            else:
                st = "🟢 正常"
            # 用反引号包裹主机名，避免括号等特殊字符导致 markdown 表格解析错误
            name = f"`{r.name}`" if r.name else "`未知`"
            lines.append(f"| {name} | {cpu_i} {cp:.0f}% | {mem_i} {mp:.0f}% | {disk_i} {dp:.0f}% | {safe_i} | {st} |")
        return "\n".join(lines)

    @staticmethod
    def _alerts_text(reports: list) -> str:
        """生成需要关注的问题列表"""
        lines = []
        for r in reports:
            for a in r.alerts:
                icon = "🔴" if a.get("level") == "CRITICAL" else "🟠"
                lines.append(f"{icon} **{r.name}** {a.get('message', '')}")
        return "\n".join(lines) if lines else "✅ 本次巡检未发现异常"

    @staticmethod
    def _ai_suggestions(reports: list) -> str:
        """生成 AI 建议（占位，AI 分析后替换）"""
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
        """发送飞书卡片通知"""
        if not webhook_url or webhook_url.strip() == "":
            return
        ts = datetime.now().strftime("%Y-%m-%d")
        status_info = FeishuNotifier._overall_status(reports)
        total_time = sum(r.duration_ms for r in reports) // 1000

        payload = {
            "msg_type": "interactive",
            "card": {
                "schema": "2.0",
                "header": {
                    "title": {"tag": "plain_text", "content": f"🖥️ 服务器巡检报告 - {ts}"},
                    "template": status_info["template"]
                },
                "body": {
                    "elements": [
                        {
                            "tag": "div",
                            "text": {
                                "tag": "lark_md",
                                "content": f"**巡检时间** {ts} {datetime.now().strftime('%H:%M:%S')}　｜　**耗时** {total_time}s　｜　**服务器数量** {len(reports)} 台"
                            }
                        },
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
                        {"tag": "div", "text": {"tag": "lark_md", "content": f"<font color=\"grey\">📄 完整报告已保存至 {report_path}</font>"}}
                    ]
                }
            }
        }
        try:
            if HAS_AIOHTTP:
                async def post_async():
                    async with aiohttp.ClientSession() as session:
                        async with session.post(webhook_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                            return await resp.json()
                import json as _json
                result = asyncio.run(post_async())
                if result.get("code") == 0:
                    print("📮 飞书通知已发送")
                else:
                    print(f"⚠️ 飞书通知失败: {result.get('msg')}")
            else:
                import urllib.request
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(webhook_url, data=data, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                    if result.get("code") == 0:
                        print("📮 飞书通知已发送")
                    else:
                        print(f"⚠️ 飞书通知失败: {result.get('msg')}")
        except Exception as e:
            print(f"⚠️ 飞书通知异常: {e}")


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
        """根据告警情况返回颜色"""
        has_crit = any(a.get("level") == "CRITICAL" for r in reports for a in r.alerts)
        has_warn = any(a.get("level") == "WARNING" for r in reports for a in r.alerts)
        return "red" if has_crit else ("yellow" if has_warn else "green")

    @staticmethod
    def _server_table_html(reports: list, thresholds: dict) -> str:
        """生成 HTML 表格"""
        rows = []
        for r in reports:
            cp = ReportGenerator._cpu_pct(r.metrics.get("top", MetricResult("", "")).raw_output) if "top" in r.metrics else 0.0
            mp = ReportGenerator._mem_pct(r.metrics.get("mem_usage", MetricResult("", "")).raw_output) if "mem_usage" in r.metrics else 0.0
            parts = ReportGenerator._partitions(r.metrics.get("disk_usage", MetricResult("", "")).raw_output) if "disk_usage" in r.metrics else []
            dp = parts[0]["usage"] if parts else 0
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
        """生成告警 HTML"""
        alerts = []
        for r in reports:
            for a in r.alerts:
                level_class = "alert-critical" if a.get("level") == "CRITICAL" else "alert-warning" if a.get("level") == "WARNING" else "alert-info"
                icon = "🔴" if a.get("level") == "CRITICAL" else "🟠" if a.get("level") == "WARNING" else "🟡"
                alerts.append(f'<div class="alert {level_class}"><strong>{icon} [{a.get("level")}] {r.name}</strong><br>{a.get("message", "")}</div>')
        return "\n".join(alerts) if alerts else '<div class="alert alert-info">✅ 本次巡检未发现异常</div>'

    @staticmethod
    def _suggestions_html(reports: list) -> str:
        """生成建议 HTML"""
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
    def generate_html(reports: list, thresholds: dict, report_path: str, signature: str) -> str:
        """生成完整 HTML 邮件"""
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
        .chart {{ background: #f9f9f9; padding: 15px; border-radius: 6px; font-family: monospace; font-size: 12px; overflow-x: auto; margin: 10px 0; white-space: pre; }}
        .footer {{ background: #f5f5f5; padding: 20px; text-align: center; font-size: 12px; color: #999; border-top: 1px solid #eee; }}
        .signature {{ background: #f9f9f9; padding: 15px; border-radius: 6px; font-size: 12px; color: #666; margin-top: 20px; white-space: pre-wrap; font-family: monospace; }}
        code {{ background: #f0f0f0; padding: 2px 6px; border-radius: 3px; }}
    </style>
</head>
<body>
    <div class="container">
        <!-- 头部 -->
        <div class="card">
            <div class="header">
                <h1>🖥️ 服务器巡检报告</h1>
                <p>{ts.strftime('%Y年%m月%d日 %H:%M:%S')}</p>
                <div class="status-badge">{colors['status']}</div>
            </div>
        </div>

        <!-- 基本信息 -->
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

        <!-- 巡检结果 -->
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

        <!-- 告警分析 -->
        <div class="card">
            <div class="section">
                <div class="section-title">⚠️ 详细告警分析</div>
                {EmailNotifier._alerts_html(reports)}
            </div>
        </div>

        <!-- AI 建议 -->
        <div class="card">
            <div class="section">
                <div class="section-title">💡 AI 优化建议</div>
                {EmailNotifier._suggestions_html(reports)}
            </div>
        </div>

        <!-- 历史趋势 -->
        <div class="card">
            <div class="section">
                <div class="section-title">📊 历史趋势（7天）</div>
                <div class="chart">CPU 使用率趋势
Day  17   18   19   20   21   22   23
  █
  ░  ░  ░░  ░░  ░░  ░░  ░░  ░░

内存使用率趋势
Day  17   18   19   20   21   22   23
  █
  ░  ░  ░░  ░░  ░░  ░░  ░░  ░░</div>
            </div>
        </div>

        <!-- 完整报告 -->
        <div class="card">
            <div class="section">
                <div class="section-title">📄 完整报告</div>
                <p style="color: #666; font-size: 14px;">
                    完整的 Markdown 报告已作为附件发送，包含原始命令输出和详细分析。<br>
                    报告已保存至: <code>{report_path}</code>
                </p>
            </div>
        </div>

        <!-- 签名 -->
        <div class="card">
            <div class="section">
                <div class="signature">{signature}</div>
            </div>
        </div>

        <!-- 页脚 -->
        <div class="footer">
            <p>此邮件由 OpenClaw Server Inspect 自动生成，请勿直接回复。</p>
            <p>如有问题，请联系系统管理员。</p>
        </div>
    </div>
</body>
</html>"""
        return html

    @staticmethod
    def send(smtp_config: dict, reports: list, thresholds: dict, report_path: str, signature: str):
        """发送邮件"""
        if not smtp_config or not smtp_config.get("smtp_host"):
            return
        
        try:
            html_content = EmailNotifier.generate_html(reports, thresholds, report_path, signature)
            
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f"🖥️ 服务器巡检报告 - {datetime.now().strftime('%Y-%m-%d')}"
            msg['From'] = smtp_config.get("from", smtp_config.get("smtp_user"))
            msg['To'] = ", ".join(smtp_config.get("to", []))
            
            msg.attach(MIMEText(html_content, 'html', 'utf-8'))
            
            # 添加 Markdown 报告附件
            if Path(report_path).exists():
                with open(report_path, 'r', encoding='utf-8') as f:
                    report_content = f.read()
                from email.mime.base import MIMEBase
                from email import encoders
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
            except Exception as e1:
                # 如果 465 失败，尝试 587
                if port == 465:
                    print(f"⚠️ 465 端口失败，尝试 587 端口...")
                    server = smtplib.SMTP(smtp_config.get("smtp_host"), 587, timeout=30)
                    server.starttls()
                    server.login(smtp_config.get("smtp_user"), smtp_config.get("smtp_password"))
                    server.send_message(msg)
                    server.quit()
                    print(f"📧 邮件已发送到 {', '.join(smtp_config.get('to', []))}")
                else:
                    raise e1
        except Exception as e:
            print(f"⚠️ 邮件发送失败: {e}")


@dataclass
class MetricResult:
    metric_id: str
    raw_output: str
    parsed_value: any = None
    alert_level: str = "NORMAL"
    alert_message: str = ""


@dataclass
class ServerReport:
    name: str
    host: str
    timestamp: str
    duration_ms: int
    metrics: Dict[str, MetricResult] = field(default_factory=dict)
    alerts: List[Dict] = field(default_factory=list)
    overall_status: str = "NORMAL"


class Config:
    def __init__(self):
        self.data = self._load_config()

    def _load_config(self) -> dict:
        if not CONFIG_FILE.exists():
            print(f"Config not found: {CONFIG_FILE}")
            sys.exit(1)
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_servers(self, host_filter: str = None) -> List[dict]:
        servers = self.data.get("servers", [])
        if host_filter:
            return [s for s in servers if host_filter.lower() in s["name"].lower()]
        return [s for s in servers if s.get("enabled", True)]

    def get_thresholds(self) -> dict:
        return self.data.get("alert_thresholds", {})

    def get_commands(self, groups: List[str]) -> List[Tuple[str, str]]:
        g = {
            "系统基础": {
                "hostname": "hostname",
                "uptime": "uptime -p 2>/dev/null || uptime",
                "who": "who", "last": "last -n 10", "uname": "uname -a",
            },
            "CPU": {
                "top": "top -bn1 | head -20",
                "loadavg": "cat /proc/loadavg",
                "top_cpu": "ps aux --sort=-%cpu | head -6",
                "vmstat": "vmstat 1 2 | tail -1",
            },
            "内存": {
                "mem_usage": "free -h",
                "swap": "free | grep Swap",
                "top_mem": "ps aux --sort=-%mem | head -6",
                "oom": "dmesg 2>/dev/null | grep -i 'out of memory\\|oom\\|killed' | tail -5 || echo 'no oom'",
            },
            "磁盘": {
                "disk_usage": "df -h",
                "disk_inode": "df -i",
                "du_top": "timeout 5 du -sh /var/* 2>/dev/null | sort -rh | head -10 || echo 'du skipped'",
                "disk_io": "iostat -x 1 2 2>/dev/null | tail -20 || echo 'iostat not available'",
            },
            "网络": {
                "netstat_summary": "netstat -an | wc -l",
                "netstat_tcp": "netstat -an | grep tcp | awk '{print $6}' | sort | uniq -c",
                "ss_summary": "ss -s",
                "ss_listen": "ss -tlnp",
                "tcp_status": "netstat -an | grep -v LISTEN | awk '{print $6}' | sort | uniq -c | sort -rn",
            },
            "服务": {
                "service_status": "systemctl list-units --type=service --state=running | grep -E 'sshd|nginx|mysql|cron' || echo 'systemctl not available'",
                "process_count": "ps aux | wc -l",
            },
            "安全": {
                "failed_login": "grep -i 'failed password\\|auth failure' /var/log/auth.log 2>/dev/null | tail -20 || grep -i 'failed' /var/log/secure 2>/dev/null | tail -20 || echo 'no auth log'",
                "last_login": "last -n 10",
                "sudo_usage": "journalctl -t sudo 2>/dev/null | tail -10 || echo 'no sudo log'",
                "firewall": "systemctl is-active firewalld 2>/dev/null || ufw status 2>/dev/null || echo 'unknown'",
            }
        }
        commands = []
        for group in groups:
            if group in g:
                for mid, cmd in g[group].items():
                    commands.append((mid, cmd))
        return commands


class SSHExecutor:
    def __init__(self, server: dict):
        self.server = server
        self.key = os.path.expanduser(server.get("ssh_key", "~/.ssh/id_ed25519"))
        self.password = server.get("ssh_password", "")

    def execute(self, command: str, timeout: int = 30) -> Tuple[bool, str]:
        if self.server["host"] in ("127.0.0.1", "localhost", ""):
            try:
                r = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout)
                return r.returncode == 0, r.stdout + r.stderr
            except subprocess.TimeoutExpired:
                return False, "timeout"
            except Exception as e:
                return False, str(e)
        try:
            if self.password:
                cmd = ["sshpass", "-p", self.password, "ssh",
                       "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
                       "-p", str(self.server.get("ssh_port", 22)),
                       f"{self.server['ssh_user']}@{self.server['host']}", command]
            else:
                cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
                       "-o", "BatchMode=yes", "-i", self.key,
                       "-p", str(self.server.get("ssh_port", 22)),
                       f"{self.server['ssh_user']}@{self.server['host']}", command]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return r.returncode == 0, r.stdout + r.stderr
        except subprocess.TimeoutExpired:
            return False, "SSH timeout"
        except Exception as e:
            return False, str(e)

    def execute_batch(self, commands: List[Tuple[str, str]], timeout: int = 10) -> Dict[str, MetricResult]:
        results = {}
        for mid, cmd in commands:
            ok, output = self.execute(cmd, timeout=timeout)
            results[mid] = MetricResult(metric_id=mid, raw_output=output if ok else f"ERROR: {output}",
                                        alert_level="ERROR" if not ok else "OK")
        return results


class MetricParser:
    def __init__(self, thresholds: dict):
        self.thresholds = thresholds

    def parse_all(self, metrics: Dict[str, MetricResult]) -> List[Dict]:
        alerts = []
        t = self.thresholds

        # CPU
        if "top" in metrics:
            m = re.search(r'%Cpu\(s\):\s*([\d.]+)\s*us', metrics["top"].raw_output)
            cpu_pct = float(m.group(1)) if m else 0.0
            if cpu_pct >= 95:
                alerts.append({"level": "CRITICAL", "message": f"CPU 使用率 {cpu_pct:.1f}% 超过 95% 阈值"})
            elif cpu_pct >= t.get("cpu_percent", 80):
                alerts.append({"level": "WARNING", "message": f"CPU 使用率 {cpu_pct:.1f}% 超过 {t.get('cpu_percent',80)}% 阈值"})

        if "loadavg" in metrics and not metrics["loadavg"].raw_output.startswith("ERROR"):
            parts = metrics["loadavg"].raw_output.strip().split()
            if parts:
                try:
                    load_1m = float(parts[0])
                    if load_1m >= t.get("loadavg_1m", 4) * 2:
                        alerts.append({"level": "CRITICAL", "message": f"1分钟负载 {load_1m} 严重过高"})
                    elif load_1m >= t.get("loadavg_1m", 4):
                        alerts.append({"level": "WARNING", "message": f"1分钟负载 {load_1m} 偏高"})
                except: pass

        # Memory
        if "mem_usage" in metrics and not metrics["mem_usage"].raw_output.startswith("ERROR"):
            lines = metrics["mem_usage"].raw_output.strip().split("\n")
            if len(lines) >= 2:
                tm = re.search(r'Mem:\s+(\S+)', lines[1])
                um = re.search(r'Mem:\s+\S+\s+(\S+)', lines[1])
                if tm and um:
                    def to_mb(s):
                        s = s.strip()
                        if 'G' in s: return float(re.sub(r'[A-Za-z]','',s)) * 1024
                        if 'M' in s: return float(re.sub(r'[A-Za-z]','',s))
                        if 'K' in s: return float(re.sub(r'[A-Za-z]','',s)) / 1024
                        return 0.0
                    try:
                        total = to_mb(tm.group(1))
                        used = to_mb(um.group(1))
                        if total > 0:
                            mp = round((used/total)*100, 1)
                            if mp >= 95:
                                alerts.append({"level": "CRITICAL", "message": f"内存使用率 {mp}% 超过 95%"})
                            elif mp >= t.get("mem_percent", 85):
                                alerts.append({"level": "WARNING", "message": f"内存使用率 {mp}% 超过 {t.get('mem_percent',85)}% 阈值"})
                    except: pass

        if "swap" in metrics and not metrics["swap"].raw_output.startswith("ERROR"):
            m = re.search(r'Swap:\s*([\d.]+)\s+([\d.]+)', metrics["swap"].raw_output)
            if m and float(m.group(1)) > 0:
                sp = round((float(m.group(2))/float(m.group(1)))*100, 1)
                if sp >= 50:
                    alerts.append({"level": "WARNING", "message": f"Swap 使用率 {sp}%，可能存在内存压力"})

        # Disk
        if "disk_usage" in metrics and not metrics["disk_usage"].raw_output.startswith("ERROR"):
            for line in metrics["disk_usage"].raw_output.strip().split("\n")[1:]:
                parts = line.split()
                if len(parts) >= 5 and parts[0].startswith("/dev"):
                    try:
                        usage = int(parts[-2].replace('%',''))
                        mount = parts[-1]
                        if usage >= 95:
                            alerts.append({"level": "CRITICAL", "message": f"{mount} 磁盘使用率 {usage}% 超过 95%"})
                        elif usage >= t.get("disk_percent", 90):
                            alerts.append({"level": "WARNING", "message": f"{mount} 磁盘使用率 {usage}% 超过 {t.get('disk_percent',90)}% 阈值"})
                    except: pass

        # Security
        if "failed_login" in metrics and not metrics["failed_login"].raw_output.startswith("ERROR"):
            cnt = len([l for l in metrics["failed_login"].raw_output.strip().split("\n") if l and "failed" in l.lower()])
            if cnt >= 20:
                alerts.append({"level": "CRITICAL", "message": f"发现 {cnt} 次登录失败，存在暴力破解风险"})
            elif cnt >= t.get("failed_login_per_hour", 5):
                alerts.append({"level": "WARNING", "message": f"发现 {cnt} 次登录失败"})

        return alerts


class ReportGenerator:

    @staticmethod
    def _cpu_pct(top_out: str) -> float:
        m = re.search(r'%Cpu\(s\):\s*([\d.]+)\s*us', top_out)
        return float(m.group(1)) if m else 0.0

    @staticmethod
    def _mem_pct(mem_out: str) -> float:
        lines = mem_out.strip().split("\n")
        if len(lines) < 2: return 0.0
        tm = re.search(r'Mem:\s+(\S+)', lines[1])
        um = re.search(r'Mem:\s+\S+\s+(\S+)', lines[1])
        if not tm or not um: return 0.0
        def to_mb(s):
            s = s.strip()
            if 'G' in s: return float(re.sub(r'[A-Za-z]','',s)) * 1024
            if 'M' in s: return float(re.sub(r'[A-Za-z]','',s))
            return 0.0
        try:
            t, u = to_mb(tm.group(1)), to_mb(um.group(1))
            return round((u/t)*100, 1) if t > 0 else 0.0
        except: return 0.0

    @staticmethod
    def _partitions(df_out: str) -> list:
        out = []
        for line in df_out.strip().split("\n")[1:]:
            parts = line.split()
            if len(parts) >= 5 and parts[0].startswith("/dev"):
                try:
                    usage = int(parts[-2].replace('%',''))
                    mount = parts[-1]
                    out.append({"mount": mount, "usage": usage})
                except: pass
        return out

    @staticmethod
    def _icon(v: float, warn: float, crit: float) -> str:
        if v >= crit: return "🔴"
        if v >= warn: return "⚠️"
        return "✅"

    @staticmethod
    def _history(host: str, days=7) -> list:
        """读取历史数据，按时间排序返回"""
        d = WORK_DIR / "history" / host
        if not d.exists(): return []
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        records = []
        # 新格式：每次调用一个文件 YYYY-MM-DD_HHMMSS.jsonl
        for f in sorted(d.glob("*.jsonl")):
            with open(f) as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                        if r.get("timestamp","")[:10] >= cutoff:
                            records.append(r)
                    except: pass
        return records

    @staticmethod
    def _trend(host: str, metric: str, label: str, warn=None, crit=None, days=7) -> str:
        recs = ReportGenerator._history(host, days)
        if not recs:
            return f"> 无历史数据\n"
        points = []
        for r in recs:
            v = r.get(metric)
            if v is not None and v != "":
                try:
                    ts = r.get("timestamp","")[5:16]
                    points.append((ts, float(v)))
                except: pass
        if not points:
            return f"> {label} 无历史数据\n"
        vals = [p[1] for p in points]
        vmin, vmax = min(vals), max(vals)
        rng = vmax - vmin if vmax != vmin else 1
        rows = 4
        chart = [""] * rows
        for ts, v in points:
            row = min(rows-1, int((v-vmin)/rng*(rows-1)))
            char = "█"
            if crit and v >= crit: char = "█"
            elif warn and v >= warn: char = "▒"
            else: char = "░"
            for r in range(rows):
                chart[r] += "  " + ("█" if rows-1-r == row else " ")
        lines = []
        for r, line in enumerate(chart):
            pct = round(vmax - (vmax-vmin)*r/(rows-1))
            lines.append(f"  {pct:>4}% ┤{''.join('█' if c=='█' else ' ' for c in line)} {pct if r==0 else ''}")
        lines.append("  -----+" + "-"*(len(points)*2))
        lines.append("       " + " ".join(ts[2:7] for ts,_ in points))
        return f"**{label} 趋势（{days}天）**\n```\n" + "\n".join(lines) + "\n```\n"

    @staticmethod
    def generate_md_report(reports: List[ServerReport], config: dict) -> str:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        total = len(reports)
        thr = config.get("alert_thresholds", {})
        crit_n = sum(1 for r in reports for a in r.alerts if a.get("level")=="CRITICAL")
        warn_n = sum(1 for r in reports for a in r.alerts if a.get("level")=="WARNING")
        overall = "🔴 严重" if crit_n > 0 else ("🟠 关注" if warn_n > 0 else "🟢 正常")

        md = f"""┌─────────────────────────────────────────────────────────┐
│  🖥️  服务器巡检报告                                          │
│  📅  巡检时间: {ts}                   │
│  ⏱️  耗时: {sum(r.duration_ms for r in reports)//1000}s                                              │
└─────────────────────────────────────────────────────────┘

## 一、巡检概览

| 指标 | 状态 | 说明 |
|------|------|------|
| 主机数 | {total} 台 | {total} 成功 / 0 失败 |
| CPU 异常 | {"🔴 " + str(crit_n) + "台" if crit_n > 0 else "✅ 正常"} | {"存在严重告警" if crit_n > 0 else "全部低于阈值"} |
| 内存异常 | {"⚠️ 有告警" if any("内存" in a.get("message","") for r in reports for a in r.alerts) else "✅ 正常"} | - |
| 磁盘异常 | {"⚠️ 有告警" if any("磁盘" in a.get("message","") for r in reports for a in r.alerts) else "✅ 正常"} | - |
| 安全异常 | {"⚠️ 有告警" if any("登录" in a.get("message","") or "暴力" in a.get("message","") for r in reports for a in r.alerts) else "✅ 正常"} | - |

**总体评价**: {overall}

> **AI 分析**：由 OpenClaw AI 分析后填入（见下方第四节「💡 AI 优化建议」）

---

## 二、分服务器详情

"""

        for idx, r in enumerate(reports, 1):
            has_crit = any(a.get("level")=="CRITICAL" for a in r.alerts)
            has_warn = any(a.get("level")=="WARNING" for a in r.alerts)
            si = "🔴" if has_crit else ("🟠" if has_warn else "🟢")
            st = "异常" if has_crit else ("关注" if has_warn else "正常")
            hist = ReportGenerator._history(r.name, 7)
            prev = hist[-2].get("timestamp","无历史记录") if len(hist) >= 2 else "无历史记录"

            md += f"### 2.{idx} {si} {r.name}（{r.host}）\n\n**运行状态**: {si} {st} | **上次巡检**: {prev}\n\n"

            # 系统基础
            md += "#### 系统基础\n| 指标 | 值 | 状态 |\n|------|-----|------|\n"
            if "hostname" in r.metrics:
                md += f"| 主机名 | `{r.metrics['hostname'].raw_output.strip()}` | ✅ |\n"
            if "uptime" in r.metrics:
                md += f"| 运行时间 | {r.metrics['uptime'].raw_output.strip()} | ✅ |\n"
            if "uname" in r.metrics:
                v = " ".join(r.metrics["uname"].raw_output.strip().split()[:4])
                md += f"| 系统版本 | {v} | ✅ |\n"
            if "who" in r.metrics:
                cnt = len([l for l in r.metrics["who"].raw_output.strip().split("\n") if l])
                md += f"| 当前用户 | {cnt} 人登录 | ✅ |\n"
            md += "\n"

            # CPU
            cp = 0.0; lm = 0.0
            if "top" in r.metrics and not r.metrics["top"].raw_output.startswith("ERROR"):
                cp = ReportGenerator._cpu_pct(r.metrics["top"].raw_output)
            if "loadavg" in r.metrics and not r.metrics["loadavg"].raw_output.startswith("ERROR"):
                try: lm = float(r.metrics["loadavg"].raw_output.strip().split()[0])
                except: pass
            ct = thr.get("cpu_percent",80); lt = thr.get("loadavg_1m",4)
            ci = ReportGenerator._icon(cp, ct*0.9, ct); li = ReportGenerator._icon(lm, lt*1.5, lt*2)
            md += f"#### CPU 与负载\n| 指标 | 值 | 阈值 | 状态 |\n|------|-----|------|------|\n| CPU 使用率 | {cp:.1f}% | {ct}% | {ci} |\n| 1分钟负载 | {lm} | {lt} | {li} |\n\n"
            if "top_cpu" in r.metrics and not r.metrics["top_cpu"].raw_output.startswith("ERROR"):
                lines = r.metrics["top_cpu"].raw_output.strip().split("\n")
                md += "**Top 5 CPU 进程**:\n```\n" + "\n".join(l for l in lines[:6]) + "\n```\n\n"

            # 内存
            mp = 0.0
            if "mem_usage" in r.metrics and not r.metrics["mem_usage"].raw_output.startswith("ERROR"):
                mp = ReportGenerator._mem_pct(r.metrics["mem_usage"].raw_output)
            mt = thr.get("mem_percent",85); mi = ReportGenerator._icon(mp, mt*0.9, mt)
            md += f"#### 内存\n| 指标 | 值 | 阈值 | 状态 |\n|------|-----|------|------|\n| 内存使用率 | {mp}% | {mt}% | {mi} |\n\n"
            if "mem_usage" in r.metrics and not r.metrics["mem_usage"].raw_output.startswith("ERROR"):
                md += "```\n" + r.metrics["mem_usage"].raw_output.strip() + "\n```\n"
            if "top_mem" in r.metrics and not r.metrics["top_mem"].raw_output.startswith("ERROR"):
                lines = r.metrics["top_mem"].raw_output.strip().split("\n")
                md += "**Top 5 内存进程**:\n```\n" + "\n".join(l for l in lines[:6]) + "\n```\n\n"

            # 磁盘
            dt = thr.get("disk_percent",90)
            md += "#### 磁盘\n| 挂载点 | 使用率 | 阈值 | 状态 |\n|--------|--------|------|------|\n"
            if "disk_usage" in r.metrics and not r.metrics["disk_usage"].raw_output.startswith("ERROR"):
                parts = ReportGenerator._partitions(r.metrics["disk_usage"].raw_output)
                for p in parts[:5]:
                    di = ReportGenerator._icon(p["usage"], dt*0.9, dt)
                    md += f"| {p['mount']} | {p['usage']}% | {dt}% | {di} |\n"
                md += f"\n```\n{r.metrics['disk_usage'].raw_output.strip()}\n```\n"
            if "du_top" in r.metrics and not r.metrics["du_top"].raw_output.startswith("ERROR"):
                du = r.metrics["du_top"].raw_output.strip()
                if du and not du.startswith("du skipped"):
                    md += f"\n**大目录 Top10**:\n```\n{du}\n```\n"
            md += "\n"

            # 网络
            cn = 0; tw = 0
            if "netstat_summary" in r.metrics:
                try: cn = int(r.metrics["netstat_summary"].raw_output.strip())
                except: pass
            if "tcp_status" in r.metrics and not r.metrics["tcp_status"].raw_output.startswith("ERROR"):
                for line in r.metrics["tcp_status"].raw_output.strip().split("\n"):
                    if "TIME_WAIT" in line:
                        try: tw = int(line.strip().split()[0])
                        except: pass
            ct2 = thr.get("netstat_connections",5000)
            cni = ReportGenerator._icon(cn, ct2*0.8, ct2)
            twi = ReportGenerator._icon(tw, 3000, 10000)
            md += f"#### 网络\n| 指标 | 值 | 阈值 | 状态 |\n|------|-----|------|------|\n| TCP 连接数 | {cn} | {ct2} | {cni} |\n| TIME_WAIT | {tw} | 5,000 | {twi} |\n\n"
            if "ss_summary" in r.metrics and not r.metrics["ss_summary"].raw_output.startswith("ERROR"):
                md += "```\n" + r.metrics["ss_summary"].raw_output.strip() + "\n```\n"
            md += "\n"

            # 服务
            if "service_status" in r.metrics and not r.metrics["service_status"].raw_output.startswith("ERROR"):
                md += "#### 服务状态\n```\n" + r.metrics["service_status"].raw_output.strip() + "\n```\n"
                if "process_count" in r.metrics:
                    md += f"| 进程总数 | {r.metrics['process_count'].raw_output.strip()} | - | - |\n"
                md += "\n"

            # 安全
            fl = 0
            md += "#### 安全\n"
            if "failed_login" in r.metrics:
                fl_out = r.metrics["failed_login"].raw_output.strip()
                fl = len([l for l in fl_out.split("\n") if l and "failed" in l.lower()])
                fi = "✅" if fl == 0 else "⚠️"
                md += f"| 登录失败（历史） | {fl} 次 | {fi} |\n"
            if "firewall" in r.metrics:
                fw = r.metrics["firewall"].raw_output.strip().replace("\n","")
                fwi = "✅" if "active" in fw.lower() and "inactive" not in fw.lower() else "⚠️"
                md += f"| 防火墙 | `{fw}` | {fwi} |\n"
            md += "\n"
            if "last_login" in r.metrics and not r.metrics["last_login"].raw_output.startswith("ERROR"):
                md += "**最近登录**:\n```\n" + r.metrics["last_login"].raw_output.strip() + "\n```\n"
            md += "---\n\n"

        # 三、异常汇总
        md += "## 三、⚠️ 异常汇总\n\n"
        has_alerts = any(r.alerts for r in reports)
        if has_alerts:
            md += "| # | 级别 | 服务器 | 描述 |\n|---|------|--------|------|\n"
            i = 0
            for r in reports:
                for a in r.alerts:
                    i += 1
                    li = "🔴" if a.get("level")=="CRITICAL" else "🟠" if a.get("level")=="WARNING" else "🟡"
                    md += f"| {i} | {li} | {r.name} | {a.get('message','-')} |\n"
            md += "\n"
        else:
            md += "✅ 本次巡检未发现异常。\n\n"

        # 四、AI 优化建议（占位符）
        md += """## 四、💡 AI 优化建议

> 以下建议由 OpenClaw AI 根据本次巡检数据生成

<!-- AI_SUGGESTIONS -->

---

## 五、📊 历史趋势（7天）

"""

        # 每个服务器的历史趋势
        for idx, r in enumerate(reports, 1):
            cp = 0.0; mp = 0.0; dp = 0.0
            if "top" in r.metrics and not r.metrics["top"].raw_output.startswith("ERROR"):
                cp = ReportGenerator._cpu_pct(r.metrics["top"].raw_output)
            if "mem_usage" in r.metrics and not r.metrics["mem_usage"].raw_output.startswith("ERROR"):
                mp = ReportGenerator._mem_pct(r.metrics["mem_usage"].raw_output)
            if "disk_usage" in r.metrics and not r.metrics["disk_usage"].raw_output.startswith("ERROR"):
                parts = ReportGenerator._partitions(r.metrics["disk_usage"].raw_output)
                dp = parts[0]["usage"] if parts else 0

            ct = thr.get("cpu_percent",80); mt = thr.get("mem_percent",85); dt2 = thr.get("disk_percent",90)
            md += f"### {r.name} 关键指标趋势\n"
            md += ReportGenerator._trend(r.name, "cpu_pct", "CPU")
            md += ReportGenerator._trend(r.name, "mem_pct", "内存")
            md += ReportGenerator._trend(r.name, "disk_pct", "磁盘")
            md += "\n"

        # 六、附录
        md += f"""## 六、附录

### A. 巡检命令输出（完整日志）

<details>
<summary>点击展开：原始命令输出</summary>

```
"""
        for r in reports:
            md += f"\n[=== {r.name} ({r.host}) ===]\n\n"
            for mid, m in r.metrics.items():
                md += f"\n[{mid}]\n{m.raw_output}\n"
        md += """
```
</details>

### B. 巡检元数据

| 字段 | 值 |
|------|-----|
| 报告版本 | v1.0 |
| 巡检工具 | server-inspect skill |
| AI 模型 | OpenClaw（本地分析，不调用外部 LLM） |
| 报告生成时间 | """ + ts + """ |
| 配置文件版本 | """ + config.get("version","1.0") + """ |

---

*📋 此报告由 OpenClaw Server Inspect 自动生成*
*🕐 报告生成时间: """ + ts + """*
"""
        return md


async def run_inspect(host_filter: str = None, groups: List[str] = None):
    print("\n🔍 Server Inspect - 开始巡检\n")
    config = Config()
    servers = config.get_servers(host_filter)
    if not servers:
        print("❌ 没有可巡检的服务器")
        return [], ""
    print(f"📋 待巡检: {len(servers)} 台")
    for s in servers:
        print(f"   - {s['name']} ({s['host']})")

    if not groups:
        groups = ["系统基础", "CPU", "内存", "磁盘", "网络", "服务", "安全"]
    commands = config.get_commands(groups)
    thresholds = config.get_thresholds()
    print(f"📊 执行 {len(commands)} 个巡检命令\n")

    all_reports = []
    for server in servers:
        print(f"🔄 巡检 {server['name']} ({server['host']})...", end=" ", flush=True)
        start = datetime.now()
        executor = SSHExecutor(server)
        parser = MetricParser(thresholds)
        metrics = executor.execute_batch(commands)
        alerts = parser.parse_all(metrics)
        has_crit = any(a.get("level")=="CRITICAL" for a in alerts)
        has_warn = any(a.get("level")=="WARNING" for a in alerts)
        overall = "CRITICAL" if has_crit else ("WARNING" if has_warn else "NORMAL")
        dur = int((datetime.now()-start).total_seconds()*1000)
        report = ServerReport(
            name=server["name"], host=server["host"],
            timestamp=start.strftime("%Y-%m-%d %H:%M:%S"),
            duration_ms=dur, metrics=metrics, alerts=alerts,
            overall_status=overall
        )
        all_reports.append(report)
        if alerts:
            for a in alerts:
                icon = "🔴" if a.get("level")=="CRITICAL" else "🟠"
                print(f"\n   {icon} {a.get('message','')}")
        else:
            print("✅")
    print(f"\n✅ 巡检完成！共 {len(all_reports)} 台")

    report_md = ReportGenerator.generate_md_report(all_reports, config.data)

    # 保存报告
    report_dir = WORK_DIR / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    rp = report_dir / f"report_{ts_str}.md"
    with open(rp, "w", encoding="utf-8") as f:
        f.write(report_md)
    print(f"\n📄 报告: {rp}")

    # 保存日志
    log_dir = WORK_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    lp = log_dir / f"inspect_{ts_str}.log"
    with open(lp, "w", encoding="utf-8") as f:
        f.write(f"# Server Inspect Log\n# Time: {ts_str}\n\n")
        for r in all_reports:
            f.write(f"\n[=== {r.name} ({r.host}) ===]\n\n")
            for mid, m in r.metrics.items():
                f.write(f"\n[{mid}]\n{m.raw_output}\n")
    print(f"📝 日志: {lp}")

    # 保存历史：每次巡检一个独立文件
    for r in all_reports:
        hd = WORK_DIR / "history" / r.name
        hd.mkdir(parents=True, exist_ok=True)
        # 文件名格式：YYYY-MM-DD_HHMMSS.jsonl（按调用次数，不按月）
        ts_filename = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        hf = hd / f"{ts_filename}.jsonl"

        cp = ReportGenerator._cpu_pct(r.metrics.get("top",MetricResult("","")).raw_output) if "top" in r.metrics else 0.0
        mp = ReportGenerator._mem_pct(r.metrics.get("mem_usage",MetricResult("","")).raw_output) if "mem_usage" in r.metrics else 0.0
        dp = ReportGenerator._partitions(r.metrics.get("disk_usage",MetricResult("","")).raw_output)[0]["usage"] if "disk_usage" in r.metrics else 0.0

        rec = {
            "timestamp": r.timestamp, "duration_ms": r.duration_ms,
            "overall_status": r.overall_status, "alerts_count": len(r.alerts),
            "cpu_pct": cp, "mem_pct": mp, "disk_pct": dp,
            "hostname": r.metrics.get("hostname", MetricResult("","")).raw_output.strip() if "hostname" in r.metrics else ""
        }
        with open(hf, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"📊 历史: ~/server-inspect/history/")

    # 飞书通知
    notification = config.data.get("notification", {})
    webhook = notification.get("feishu_webhook", "")
    if webhook:
        FeishuNotifier.send(webhook, all_reports, thresholds, str(rp))

    # 邮件通知
    smtp_config = notification.get("email", {})
    signature = config.data.get("signature", "-- \n锐盈云技术服务（天津）有限公司")
    if smtp_config and smtp_config.get("smtp_host"):
        EmailNotifier.send(smtp_config, all_reports, thresholds, str(rp), signature)

    return all_reports, report_md


def main():
    import argparse
    p = argparse.ArgumentParser(description="Server Inspect")
    p.add_argument("--host", help="主机名过滤")
    p.add_argument("--groups", help="指标组，逗号分隔")
    args = p.parse_args()
    groups = args.groups.split(",") if args.groups else None
    reports, md = asyncio.run(run_inspect(args.host, groups))
    print("\n" + "="*60)
    print(md[:2000])
    print("\n... (完整报告见文件)")


if __name__ == "__main__":
    main()
