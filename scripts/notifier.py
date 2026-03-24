#!/usr/bin/env python3
"""
Server Inspect - 通知模块
独立的飞书和邮件通知函数
"""

import json
import smtplib
from datetime import datetime
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False


class FeishuNotifier:
    """飞书卡片通知"""

    STATUS_COLOR = {
        "green": {"header": "🟢 正常", "template": "green"},
        "yellow": {"header": "🟡 关注", "template": "yellow"},
        "red": {"header": "🔴 严重", "template": "red"},
    }

    @staticmethod
    def _status_icon(value: float, warn: float, crit: float) -> str:
        if value >= crit:
            return "🔴"
        if value >= warn:
            return "🟠"
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
        lines = [
            "| 主机 | CPU | 内存 | 磁盘 | 安全 | 状态 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for r in reports:
            # 这里简化处理，实际应该从 metrics 中提取
            cp = 0.0
            mp = 0.0
            dp = 0
            ct, mt, dt = (
                thresholds.get("cpu_percent", 80),
                thresholds.get("mem_percent", 85),
                thresholds.get("disk_percent", 90),
            )
            cpu_i = "✅" if cp < ct * 0.9 else ("🟠" if cp < ct else "🔴")
            mem_i = "✅" if mp < mt * 0.9 else ("🟠" if mp < mt else "🔴")
            disk_i = "✅" if dp < dt * 0.9 else ("🟠" if dp < dt else "🔴")
            has_login = any("登录" in a.get("message", "") for a in r.alerts)
            safe_i = "⚠️" if has_login else "✅"
            has_crit = any(a.get("level") == "CRITICAL" for a in r.alerts)
            has_warn = any(a.get("level") == "WARNING" for a in r.alerts)
            st = "🔴 严重" if has_crit else ("🟠 关注" if has_warn else "🟢 正常")
            name = f"`{r.name}`" if r.name else "`未知`"
            lines.append(
                f"| {name} | {cpu_i} {cp:.0f}% | {mem_i} {mp:.0f}% | {disk_i} {dp:.0f}% | {safe_i} | {st} |"
            )
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
                    suggestions.append(
                        f"1. 紧急处理 {r.name}：{a.get('message', '')}"
                    )
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
                    "title": {
                        "tag": "plain_text",
                        "content": f"🖥️ 服务器巡检报告 - {ts}",
                    },
                    "template": status_info["template"],
                },
                "body": {
                    "elements": [
                        {
                            "tag": "div",
                            "text": {
                                "tag": "lark_md",
                                "content": f"**巡检时间** {ts} {datetime.now().strftime('%H:%M:%S')} ｜ **耗时** {total_time}s ｜ **服务器数量** {len(reports)} 台",
                            },
                        },
                        {"tag": "hr"},
                        {
                            "tag": "div",
                            "text": {
                                "tag": "lark_md",
                                "content": "**📊 巡检结果概览（多主机列表）**",
                            },
                        },
                        {
                            "tag": "div",
                            "text": {
                                "tag": "lark_md",
                                "content": FeishuNotifier._server_table(reports, thresholds),
                            },
                        },
                        {"tag": "hr"},
                        {
                            "tag": "div",
                            "text": {
                                "tag": "lark_md",
                                "content": "**⚠️ 需要关注的问题**",
                            },
                        },
                        {
                            "tag": "div",
                            "text": {
                                "tag": "lark_md",
                                "content": FeishuNotifier._alerts_text(reports),
                            },
                        },
                        {"tag": "hr"},
                        {
                            "tag": "div",
                            "text": {
                                "tag": "lark_md",
                                "content": "**💡 AI 建议**",
                            },
                        },
                        {
                            "tag": "div",
                            "text": {
                                "tag": "lark_md",
                                "content": FeishuNotifier._ai_suggestions(reports),
                            },
                        },
                        {"tag": "hr"},
                        {
                            "tag": "div",
                            "text": {
                                "tag": "lark_md",
                                "content": f"<font color=\"grey\">📄 完整报告已保存至 {report_path}</font>",
                            },
                        },
                    ]
                },
            },
        }

        try:
            if HAS_AIOHTTP:
                import asyncio

                async def post_async():
                    async with aiohttp.ClientSession() as session:
                        async with session.post(
                            webhook_url,
                            json=payload,
                            timeout=aiohttp.ClientTimeout(total=10),
                        ) as resp:
                            return await resp.json()

                result = asyncio.run(post_async())
                if result.get("code") == 0:
                    print("📮 飞书通知已发送")
                else:
                    print(f"⚠️ 飞书通知失败: {result.get('msg')}")
            else:
                import urllib.request

                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    webhook_url,
                    data=data,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                    if result.get("code") == 0:
                        print("📮 飞书通知已发送")
                    else:
                        print(f"⚠️ 飞书通知失败: {result.get('msg')}")
        except Exception as e:
            print(f"⚠️ 飞书通知异常: {e}")


class EmailNotifier:
    """邮件通知（HTML 格式）"""

    STATUS_COLOR = {
        "green": {
            "bg": "#E8F5E9",
            "border": "#4CAF50",
            "text": "#2E7D32",
            "status": "🟢 正常",
        },
        "yellow": {
            "bg": "#FFF9C4",
            "border": "#FBC02D",
            "text": "#F57F17",
            "status": "🟡 关注",
        },
        "red": {
            "bg": "#FFEBEE",
            "border": "#F44336",
            "text": "#C62828",
            "status": "🔴 严重",
        },
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
            # 从 metrics 中提取数据（需要解析原始输出）
            # 这里简化处理，使用默认值，实际应该从 metrics 中解析
            cp = 0.0
            mp = 0.0
            dp = 0
            
            # 尝试从 metrics 中获取 top 输出并解析 CPU
            if "top" in r.metrics:
                import re
                m = re.search(r'%Cpu\(s\):\s*([\d.]+)\s*us', r.metrics["top"].raw_output)
                if m:
                    cp = float(m.group(1))
            
            # 尝试从 metrics 中获取内存使用率
            if "mem_usage" in r.metrics:
                import re
                lines = r.metrics["mem_usage"].raw_output.strip().split("\n")
                if len(lines) >= 2:
                    tm = re.search(r'Mem:\s+(\S+)', lines[1])
                    um = re.search(r'Mem:\s+\S+\s+(\S+)', lines[1])
                    if tm and um:
                        def to_mb(s):
                            s = s.strip()
                            if 'G' in s: return float(re.sub(r'[A-Za-z]','',s)) * 1024
                            if 'M' in s: return float(re.sub(r'[A-Za-z]','',s))
                            return 0.0
                        try:
                            t, u = to_mb(tm.group(1)), to_mb(um.group(1))
                            mp = round((u/t)*100, 1) if t > 0 else 0.0
                        except: pass
            
            # 尝试从 metrics 中获取磁盘使用率
            if "disk_usage" in r.metrics:
                import re
                for line in r.metrics["disk_usage"].raw_output.strip().split("\n")[1:]:
                    parts = line.split()
                    if len(parts) >= 5 and parts[0].startswith("/dev"):
                        try:
                            dp = int(parts[-2].replace('%',''))
                            break  # 只取第一个分区
                        except: pass
            
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
                level_class = (
                    "alert-critical"
                    if a.get("level") == "CRITICAL"
                    else "alert-warning"
                    if a.get("level") == "WARNING"
                    else "alert-info"
                )
                icon = (
                    "🔴"
                    if a.get("level") == "CRITICAL"
                    else "🟠"
                    if a.get("level") == "WARNING"
                    else "🟡"
                )
                alerts.append(
                    f'<div class="alert {level_class}"><strong>{icon} [{a.get("level")}] {r.name}</strong><br>{a.get("message", "")}</div>'
                )
        return (
            "\n".join(alerts)
            if alerts
            else '<div class="alert alert-info">✅ 本次巡检未发现异常</div>'
        )

    @staticmethod
    def _suggestions_html(reports: list) -> str:
        """生成建议 HTML"""
        suggestions = []
        idx = 1
        for r in reports:
            for a in r.alerts:
                if a.get("level") == "CRITICAL":
                    suggestions.append(
                        f'<div class="suggestion"><strong>建议 {idx}: 紧急 - {r.name} 告警处理</strong><br>问题: {a.get("message", "")}<br>操作步骤:<br>1. 立即检查服务器状态<br>2. 查看相关日志<br>3. 采取应急措施<br>预期效果: 恢复服务正常运行</div>'
                    )
                    idx += 1
                    break
        for r in reports:
            for a in r.alerts:
                if a.get("level") == "WARNING":
                    suggestions.append(
                        f'<div class="suggestion"><strong>建议 {idx}: 关注 - {r.name} 监控</strong><br>问题: {a.get("message", "")}<br>操作步骤:<br>1. 持续监控该指标<br>2. 准备应急预案<br>预期效果: 提前发现问题</div>'
                    )
                    idx += 1
                    break
        return (
            "\n".join(suggestions[:5])
            if suggestions
            else '<div class="suggestion"><strong>建议: 继续保持</strong><br>当前状态良好，继续保持现有配置。</div>'
        )

    @staticmethod
    def generate_html(
        reports: list, thresholds: dict, report_path: str
    ) -> str:
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
    def send(
        smtp_config: dict, reports: list, thresholds: dict, report_path: str
    ):
        """发送邮件"""
        if not smtp_config or not smtp_config.get("smtp_host"):
            return
        
        try:
            html_content = EmailNotifier.generate_html(
                reports, thresholds, report_path
            )

            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"🖥️ 服务器巡检报告 - {datetime.now().strftime('%Y-%m-%d')}"
            msg["From"] = smtp_config.get("from", smtp_config.get("smtp_user"))
            msg["To"] = ", ".join(smtp_config.get("to", []))

            msg.attach(MIMEText(html_content, "html", "utf-8"))

            # 添加 Markdown 报告附件
            if Path(report_path).exists():
                with open(report_path, "r", encoding="utf-8") as f:
                    report_content = f.read()
                part = MIMEBase("application", "octet-stream")
                part.set_payload(report_content.encode("utf-8"))
                encoders.encode_base64(part)
                part.add_header(
                    "Content-Disposition",
                    "attachment",
                    filename=Path(report_path).name,
                )
                msg.attach(part)

            # 发送邮件（优先用 SSL 465，失败则用 STARTTLS 587）
            port = smtp_config.get("smtp_port", 465)
            try:
                if port == 465:
                    server = smtplib.SMTP_SSL(
                        smtp_config.get("smtp_host"), port, timeout=30
                    )
                else:
                    server = smtplib.SMTP(
                        smtp_config.get("smtp_host"), port, timeout=30
                    )
                    server.starttls()
                server.login(
                    smtp_config.get("smtp_user"), smtp_config.get("smtp_password")
                )
                server.send_message(msg)
                server.quit()
                print(f"📧 邮件已发送到 {', '.join(smtp_config.get('to', []))}")
            except Exception as e1:
                # 如果 465 失败，尝试 587
                if port == 465:
                    print(f"⚠️ 465 端口失败，尝试 587 端口...")
                    server = smtplib.SMTP(
                        smtp_config.get("smtp_host"), 587, timeout=30
                    )
                    server.starttls()
                    server.login(
                        smtp_config.get("smtp_user"), smtp_config.get("smtp_password")
                    )
                    server.send_message(msg)
                    server.quit()
                    print(f"📧 邮件已发送到 {', '.join(smtp_config.get('to', []))}")
                else:
                    raise e1
        except Exception as e:
            print(f"⚠️ 邮件发送失败: {e}")
