#!/usr/bin/env python3
"""
Server Inspect - 核心巡检脚本
采集指标 → 解析 → AI 分析 → 生成报告
"""

import os
import re
import json
import sys
import subprocess
import asyncio
import smtplib
from datetime import datetime, timedelta
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from enum import Enum

# 可选依赖
try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

WORK_DIR = Path.home() / ".qclaw" / "server-inspect"
CONFIG_FILE = WORK_DIR / "config.json"


class AlertLevel(Enum):
    CRITICAL = "🔴 Critical"
    WARNING = "🟠 Warning"
    INFO = "🟡 Info"
    NORMAL = "🟢 Normal"


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
    ai_summary: str = ""
    ai_suggestions: List[str] = field(default_factory=list)
    overall_status: str = "NORMAL"


class Config:
    def __init__(self):
        self.data = self._load_config()

    def _load_config(self) -> dict:
        if not CONFIG_FILE.exists():
            print(f"❌ 配置文件不存在: {CONFIG_FILE}")
            print("   请先运行 init 初始化配置")
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
        """返回 (metric_id, command) 列表"""
        metrics_groups = {
            "系统基础": {
                "hostname": "hostname",
                "uptime": "uptime -p 2>/dev/null || uptime",
                "who": "who",
                "last": "last -n 10",
                "uname": "uname -a",
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
                "oom": "dmesg 2>/dev/null | grep -i 'out of memory\\|oom\\|killed process' | tail -5 || echo 'no oom events'",
            },
            "磁盘": {
                "disk_usage": "df -h",
                "disk_inode": "df -i",
                "du_top": "timeout 5 du -sh /var/* 2>/dev/null | sort -rh | head -10 || echo 'du skipped (timeout or no access)'",
                "disk_io": "iostat -x 1 2 2>/dev/null | tail -20 || echo 'iostat not available'",
            },
            "网络": {
                "netstat_summary": "netstat -an | wc -l",
                "netstat_tcp": "netstat -an | grep tcp | awk '{print $6}' | sort | uniq -c",
                "ss_summary": "ss -s",
                "ss_listen": "ss -tlnp",
                "tcp_status": "netstat -an | grep -v LISTEN | awk '{print $6}' | sort | uniq -c | sort -rn",
                "bandwidth": "cat /proc/net/dev | awk 'NR>2 {print $1,$2,$10}'",
            },
            "服务": {
                "service_status": "systemctl list-units --type=service --state=running | grep -E 'sshd|nginx|mysql|httpd|cron' || echo 'systemctl not available'",
                "process_count": "ps aux | wc -l",
            },
            "安全": {
                "failed_login": "grep -i 'failed password\\|authentication failure' /var/log/auth.log 2>/dev/null | tail -20 || grep -i 'failed password' /var/log/secure 2>/dev/null | tail -20 || echo 'no auth log'",
                "last_login": "last -n 10",
                "sudo_usage": "journalctl -t sudo 2>/dev/null | tail -10 || echo 'no sudo log'",
                "firewall": "systemctl is-active firewalld 2>/dev/null || ufw status 2>/dev/null || echo 'firewall status unknown'",
            }
        }

        commands = []
        for group in groups:
            if group in metrics_groups:
                for metric_id, cmd in metrics_groups[group].items():
                    commands.append((metric_id, cmd))
        return commands


class SSHExecutor:
    def __init__(self, server: dict):
        self.server = server
        self.key = os.path.expanduser(server.get("ssh_key", "~/.ssh/id_ed25519"))
        self.password = server.get("ssh_password", "")

    def execute(self, command: str, timeout: int = 30) -> Tuple[bool, str]:
        if self.server["host"] in ("127.0.0.1", "localhost", ""):
            # 本机执行
            try:
                result = subprocess.run(
                    command, shell=True, capture_output=True,
                    text=True, timeout=timeout
                )
                return result.returncode == 0, result.stdout + result.stderr
            except subprocess.TimeoutExpired:
                return False, "Command timeout"
            except Exception as e:
                return False, str(e)

        # SSH 远程执行
        try:
            if self.password:
                # 密码认证：使用 sshpass
                cmd = [
                    "sshpass", "-p", self.password,
                    "ssh",
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "ConnectTimeout=10",
                    "-p", str(self.server.get("ssh_port", 22)),
                    f"{self.server['ssh_user']}@{self.server['host']}",
                    command
                ]
            else:
                # 密钥认证
                cmd = [
                    "ssh",
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "ConnectTimeout=10",
                    "-o", "BatchMode=yes",
                    "-i", self.key,
                    "-p", str(self.server.get("ssh_port", 22)),
                    f"{self.server['ssh_user']}@{self.server['host']}",
                    command
                ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return result.returncode == 0, result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            return False, "SSH timeout"
        except Exception as e:
            return False, str(e)

    def execute_batch(self, commands: List[Tuple[str, str]], timeout: int = 10) -> Dict[str, MetricResult]:
        results = {}
        for metric_id, cmd in commands:
            ok, output = self.execute(cmd, timeout=timeout)
            results[metric_id] = MetricResult(
                metric_id=metric_id,
                raw_output=output if ok else f"ERROR: {output}",
                alert_level="ERROR" if not ok else "OK"
            )
        return results


class MetricParser:
    """指标解析器 - 将原始命令输出解析为结构化数据"""

    def __init__(self, thresholds: dict):
        self.thresholds = thresholds

    def parse_cpu(self, metrics: Dict[str, MetricResult]) -> Dict:
        """解析 CPU 指标"""
        result = {"usage_percent": 0, "load_1m": 0, "load_5m": 0, "alerts": []}

        # 解析 top 输出
        if "top" in metrics:
            top_output = metrics["top"].raw_output
            # 提取 CPU 使用率: %Cpu(s): 12.3 us, ...
            match = re.search(r'%Cpu\(s\):\s*([\d.]+)\s*us', top_output)
            if match:
                result["usage_percent"] = float(match.group(1))

        # 解析负载
        if "loadavg" in metrics:
            loadavg = metrics["loadavg"].raw_output.strip()
            if not loadavg.startswith("ERROR"):
                parts = loadavg.split()
                if len(parts) >= 3:
                    try:
                        result["load_1m"] = float(parts[0])
                        result["load_5m"] = float(parts[1])
                    except ValueError:
                        pass

        # 告警判断
        threshold = self.thresholds.get("cpu_percent", 80)
        if result["usage_percent"] >= 95:
            result["alerts"].append({
                "level": "CRITICAL",
                "message": f"CPU 使用率 {result['usage_percent']:.1f}% 超过 95% 阈值"
            })
        elif result["usage_percent"] >= threshold:
            result["alerts"].append({
                "level": "WARNING",
                "message": f"CPU 使用率 {result['usage_percent']:.1f}% 超过 {threshold}% 阈值"
            })

        load_threshold = self.thresholds.get("loadavg_1m", 4)
        if result["load_1m"] >= load_threshold * 2:
            result["alerts"].append({
                "level": "CRITICAL",
                "message": f"1分钟负载 {result['load_1m']} 过高"
            })
        elif result["load_1m"] >= load_threshold:
            result["alerts"].append({
                "level": "WARNING",
                "message": f"1分钟负载 {result['load_1m']} 偏高"
            })

        return result

    def parse_memory(self, metrics: Dict[str, MetricResult]) -> Dict:
        """解析内存指标"""
        result = {"total_gb": 0, "used_gb": 0, "usage_percent": 0, "swap_percent": 0, "alerts": []}

        if "mem_usage" in metrics:
            output = metrics["mem_usage"].raw_output
            if not output.startswith("ERROR"):
                lines = output.strip().split("\n")
                if len(lines) >= 2:
                    # Mem: 行
                    mem_match = re.search(r'Mem:\s*([\d.]+[GMK]?)\s+([\d.]+[GMK]?)\s+([\d.]+[GMK]?)', lines[1])
                    if mem_match:
                        def parse_size(s):
                            if 'G' in s: return float(s.replace('Gi','').replace('G','')) * 1024
                            if 'M' in s: return float(s.replace('Mi','').replace('M',''))
                            if 'K' in s: return float(s.replace('Ki','').replace('K','')) / 1024
                            return 0
                        total = parse_size(mem_match.group(1))
                        used = parse_size(mem_match.group(2))
                        if total > 0:
                            result["usage_percent"] = (used / total) * 100
                            result["total_gb"] = total / 1024
                            result["used_gb"] = used / 1024

        if "swap" in metrics:
            swap_output = metrics["swap"].raw_output
            if not swap_output.startswith("ERROR"):
                swap_match = re.search(r'Swap:\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)', swap_output)
                if swap_match:
                    total = float(swap_match.group(1))
                    used = float(swap_match.group(2))
                    if total > 0:
                        result["swap_percent"] = (used / total) * 100

        # 告警
        threshold = self.thresholds.get("mem_percent", 85)
        if result["usage_percent"] >= 95:
            result["alerts"].append({
                "level": "CRITICAL",
                "message": f"内存使用率 {result['usage_percent']:.1f}% 超过 95%"
            })
        elif result["usage_percent"] >= threshold:
            result["alerts"].append({
                "level": "WARNING",
                "message": f"内存使用率 {result['usage_percent']:.1f}% 超过 {threshold}%"
            })

        if result["swap_percent"] >= 50:
            result["alerts"].append({
                "level": "WARNING",
                "message": f"Swap 使用率 {result['swap_percent']:.1f}%，可能存在内存压力"
            })

        return result

    def parse_disk(self, metrics: Dict[str, MetricResult]) -> Dict:
        """解析磁盘指标"""
        result = {"partitions": [], "alerts": []}

        if "disk_usage" in metrics:
            output = metrics["disk_usage"].raw_output
            if not output.startswith("ERROR"):
                lines = output.strip().split("\n")
                for line in lines[1:]:  # 跳过标题行
                    parts = line.split()
                    if len(parts) >= 5:
                        mount = parts[-1] if not parts[-1].startswith('/dev') else parts[-2]
                        usage_str = parts[-2].replace('%', '')
                        try:
                            usage = int(usage_str)
                            size = parts[1]
                            result["partitions"].append({
                                "mount": mount,
                                "usage_percent": usage,
                                "size": size
                            })

                            threshold = self.thresholds.get("disk_percent", 90)
                            if usage >= 95:
                                result["alerts"].append({
                                    "level": "CRITICAL",
                                    "message": f"{mount} 磁盘使用率 {usage}% 超过 95%"
                                })
                            elif usage >= threshold:
                                result["alerts"].append({
                                    "level": "WARNING",
                                    "message": f"{mount} 磁盘使用率 {usage}% 超过 {threshold}%"
                                })
                        except ValueError:
                            pass

        return result

    def parse_security(self, metrics: Dict[str, MetricResult]) -> Dict:
        """解析安全指标"""
        result = {"failed_login_count": 0, "alerts": []}

        if "failed_login" in metrics:
            output = metrics["failed_login"].raw_output
            if not output.startswith("ERROR"):
                count = len([l for l in output.strip().split("\n") if l and "failed" in l.lower()])
                result["failed_login_count"] = count

                threshold = self.thresholds.get("failed_login_per_hour", 5)
                if count >= 20:
                    result["alerts"].append({
                        "level": "CRITICAL",
                        "message": f"过去记录中发现 {count} 次登录失败，可能存在暴力破解"
                    })
                elif count >= threshold:
                    result["alerts"].append({
                        "level": "WARNING",
                        "message": f"过去记录中发现 {count} 次登录失败"
                    })

        return result

    def parse_network(self, metrics: Dict[str, MetricResult]) -> Dict:
        """解析网络指标"""
        result = {"tcp_connections": 0, "time_wait": 0, "alerts": []}

        if "netstat_summary" in metrics:
            try:
                output = metrics["netstat_summary"].raw_output.strip()
                if not output.startswith("ERROR"):
                    result["tcp_connections"] = int(output)
            except:
                pass

        if "tcp_status" in metrics:
            output = metrics["tcp_status"].raw_output
            if not output.startswith("ERROR"):
                for line in output.strip().split("\n"):
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        try:
                            count = int(parts[0])
                            status = parts[1]
                            if "TIME_WAIT" in status:
                                result["time_wait"] = count
                        except ValueError:
                            pass

        # 告警
        threshold = self.thresholds.get("netstat_connections", 5000)
        if result["tcp_connections"] >= 10000:
            result["alerts"].append({
                "level": "CRITICAL",
                "message": f"TCP 连接数 {result['tcp_connections']} 过高"
            })
        elif result["tcp_connections"] >= threshold:
            result["alerts"].append({
                "level": "WARNING",
                "message": f"TCP 连接数 {result['tcp_connections']} 偏高"
            })

        return result


class FeishuNotifier:
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    async def send(self, content: str):
        if not self.webhook_url:
            print("⚠️ 未配置飞书 Webhook")
            return False

        if not HAS_AIOHTTP:
            print("⚠️ 未安装 aiohttp，跳过飞书通知")
            return False

        payload = {
            "msg_type": "markdown",
            "content": {
                "text": content
            }
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.webhook_url, json=payload, timeout=5) as resp:
                    if resp.status == 200:
                        return True
                    else:
                        print(f"❌ 飞书通知失败: {resp.status}")
                        return False
        except Exception as e:
            print(f"❌ 飞书通知异常: {e}")
            return False


class EmailNotifier:
    def __init__(self, config: dict):
        self.config = config

    def send(self, subject: str, html_content: str) -> bool:
        if not self.config.get("smtp_host"):
            print("⚠️ 未配置邮件 SMTP")
            return False

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self.config.get("from", self.config.get("smtp_user"))
            msg["To"] = ",".join(self.config.get("to", []))

            msg.attach(MIMEText(html_content, "html", "utf-8"))

            server = smtplib.SMTP(self.config["smtp_host"], self.config["smtp_port"])
            server.starttls()
            server.login(self.config["smtp_user"], self.config["smtp_password"])
            server.sendmail(msg["From"], self.config["to"], msg.as_string())
            server.quit()
            return True
        except Exception as e:
            print(f"❌ 邮件发送失败: {e}")
            return False


class ReportGenerator:
    """报告生成器"""

    @staticmethod
    def generate_md_report(server_reports: List[ServerReport], config: dict) -> str:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        total_servers = len(server_reports)
        alert_counts = {"CRITICAL": 0, "WARNING": 0, "INFO": 0}

        for report in server_reports:
            for alert in report.alerts:
                level = alert.get("level", "INFO")
                if level in alert_counts:
                    alert_counts[level] += 1

        # 生成概览
        status_emoji = "🟢" if alert_counts["CRITICAL"] == 0 and alert_counts["WARNING"] == 0 else "🟠"
        if alert_counts["CRITICAL"] > 0:
            status_emoji = "🔴"

        md = f"""# 🖥️ 服务器巡检报告

| 项目 | 值 |
|------|-----|
| 📅 巡检时间 | {timestamp} |
| 🖥️ 服务器数量 | {total_servers} 台 |
| ⚠️ 严重告警 | {alert_counts['CRITICAL']} 个 |
| ⚠️ 警告 | {alert_counts['WARNING']} 个 |
| 📊 总体状态 | {status_emoji} |

---

## 一、巡检概览

"""

        # 按服务器生成详情
        for report in server_reports:
            status = "🟢" if not report.alerts else "🔴" if any(a.get("level") == "CRITICAL" for a in report.alerts) else "🟠"
            md += f"""### {status} {report.name}（{report.host}）

**巡检时间**: {report.timestamp} | **耗时**: {report.duration_ms}ms

"""

            # ─── 系统基础 ───
            md += "#### 系统基础\n"
            md += "| 指标 | 值 |\n|------|-----|\n"
            if "hostname" in report.metrics:
                val = report.metrics["hostname"].raw_output.strip()
                md += f"| 主机名 | `{val}` |\n"
            if "uptime" in report.metrics:
                val = report.metrics["uptime"].raw_output.strip()
                md += f"| 运行时间 | {val} |\n"
            if "uname" in report.metrics:
                val = " ".join(report.metrics["uname"].raw_output.strip().split()[:4])
                md += f"| 系统版本 | {val} |\n"
            if "who" in report.metrics:
                lines = [l for l in report.metrics["who"].raw_output.strip().split("\n") if l]
                md += f"| 登录用户 | {len(lines)} 人 |\n"
            if "last" in report.metrics and not report.metrics["last"].raw_output.startswith("ERROR"):
                lines = [l for l in report.metrics["last"].raw_output.strip().split("\n") if l]
                md += f"| 最近登录 | {len(lines)} 条记录 |\n"
            md += "\n"

            # ─── CPU & 负载 ───
            if "top" in report.metrics or "loadavg" in report.metrics:
                md += "#### CPU & 负载\n"
                if "loadavg" in report.metrics and not report.metrics["loadavg"].raw_output.startswith("ERROR"):
                    md += f"| 负载均值 | {report.metrics['loadavg'].raw_output.strip()} |\n"
                if "vmstat" in report.metrics and not report.metrics["vmstat"].raw_output.startswith("ERROR"):
                    md += f"| 虚拟内存 | {report.metrics['vmstat'].raw_output.strip()} |\n"
                if "top" in report.metrics:
                    md += f"\n```\n{report.metrics['top'].raw_output[:500]}\n```\n"
                if "top_cpu" in report.metrics and not report.metrics["top_cpu"].raw_output.startswith("ERROR"):
                    md += f"\n**Top CPU 进程**:\n```\n{report.metrics['top_cpu'].raw_output}\n```\n"
                md += "\n"

            # ─── 内存 ───
            if "mem_usage" in report.metrics:
                md += "#### 内存\n"
                md += f"```\n{report.metrics['mem_usage'].raw_output}\n```\n"
                if "swap" in report.metrics:
                    md += f"```\n{report.metrics['swap'].raw_output}\n```\n"
                if "top_mem" in report.metrics and not report.metrics["top_mem"].raw_output.startswith("ERROR"):
                    md += f"\n**Top 内存进程**:\n```\n{report.metrics['top_mem'].raw_output}\n```\n"
                md += "\n"

            # ─── 磁盘 ───
            if "disk_usage" in report.metrics:
                md += "#### 磁盘\n"
                md += f"```\n{report.metrics['disk_usage'].raw_output}\n```\n"
                if "disk_inode" in report.metrics and not report.metrics["disk_inode"].raw_output.startswith("ERROR"):
                    md += f"\n**Inode 使用率**:\n```\n{report.metrics['disk_inode'].raw_output}\n```\n"
                if "du_top" in report.metrics and not report.metrics["du_top"].raw_output.startswith("ERROR"):
                    md += f"\n**大目录 Top10**:\n```\n{report.metrics['du_top'].raw_output}\n```\n"
                md += "\n"

            # ─── 网络 ───
            has_net = any(k in report.metrics for k in ["netstat_summary", "ss_summary", "ss_listen", "tcp_status", "bandwidth"])
            if has_net:
                md += "#### 网络\n"
                if "ss_summary" in report.metrics and not report.metrics["ss_summary"].raw_output.startswith("ERROR"):
                    md += f"```\n{report.metrics['ss_summary'].raw_output}\n```\n"
                if "tcp_status" in report.metrics and not report.metrics["tcp_status"].raw_output.startswith("ERROR"):
                    md += f"\n**TCP 连接状态**:\n```\n{report.metrics['tcp_status'].raw_output}\n```\n"
                if "ss_listen" in report.metrics and not report.metrics["ss_listen"].raw_output.startswith("ERROR"):
                    md += f"\n**监听端口**:\n```\n{report.metrics['ss_listen'].raw_output}\n```\n"
                if "netstat_summary" in report.metrics:
                    val = report.metrics["netstat_summary"].raw_output.strip()
                    md += f"| TCP 连接总数 | {val} |\n"
                md += "\n"

            # ─── 服务 ───
            if "service_status" in report.metrics:
                md += "#### 服务状态\n"
                output = report.metrics["service_status"].raw_output
                md += f"```\n{output}\n```\n"
                if "process_count" in report.metrics:
                    val = report.metrics["process_count"].raw_output.strip()
                    md += f"| 进程总数 | {val} |\n"
                md += "\n"

            # ─── 安全 ───
            has_sec = any(k in report.metrics for k in ["failed_login", "last_login", "sudo_usage", "firewall"])
            if has_sec:
                md += "#### 安全\n"
                if "failed_login" in report.metrics:
                    output = report.metrics["failed_login"].raw_output.strip()
                    count = len([l for l in output.split("\n") if l and "failed" in l.lower()])
                    status_icon = "✅" if count == 0 else "⚠️"
                    md += f"| 登录失败 | {status_icon} {count} 次 |\n"
                if "last_login" in report.metrics and not report.metrics["last_login"].raw_output.startswith("ERROR"):
                    md += f"\n**最近登录**:\n```\n{report.metrics['last_login'].raw_output}\n```\n"
                if "sudo_usage" in report.metrics and not report.metrics["sudo_usage"].raw_output.startswith("ERROR"):
                    md += f"\n**Sudo 使用**:\n```\n{report.metrics['sudo_usage'].raw_output}\n```\n"
                if "firewall" in report.metrics:
                    md += f"| 防火墙 | `{report.metrics['firewall'].raw_output.strip()}` |\n"
                md += "\n"

            # AI 摘要
            if report.ai_summary:
                md += f"> **AI 分析**: {report.ai_summary}\n\n"

            md += "---\n\n"

        # 告警汇总
        if any(report.alerts for report in server_reports):
            md += "## 二、⚠️ 告警汇总\n\n"
            md += "| 级别 | 服务器 | 指标 | 描述 |\n"
            md += "|------|--------|------|------|\n"

            for report in server_reports:
                for alert in report.alerts:
                    level = alert.get("level", "INFO")
                    level_icon = "🔴" if level == "CRITICAL" else "🟠" if level == "WARNING" else "🟡"
                    md += f"| {level_icon} {level} | {report.name} | {alert.get('metric', '-')} | {alert.get('message', '')} |\n"

            md += "\n"

        # AI 建议
        all_suggestions = []
        for report in server_reports:
            all_suggestions.extend(report.ai_suggestions)

        if all_suggestions:
            md += "## 三、💡 AI 优化建议\n\n"
            for i, suggestion in enumerate(all_suggestions[:5], 1):
                md += f"### 建议 {i}\n{suggestion}\n\n"

        # 页脚
        md += f"""---

*📋 此报告由 OpenClaw Server Inspect 自动生成*
*🕐 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*
"""

        return md


async def run_inspect(host_filter: str = None, groups: List[str] = None):
    """执行巡检主流程"""
    print("\n🔍 Server Inspect - 开始巡检\n")

    config = Config()
    servers = config.get_servers(host_filter)

    if not servers:
        print("❌ 没有找到可巡检的服务器")
        return

    print(f"📋 待巡检服务器: {len(servers)} 台")
    for s in servers:
        print(f"   - {s['name']} ({s['host']})")
    print()

    # 确定巡检指标组
    if not groups:
        groups = ["系统基础", "CPU", "内存", "磁盘", "网络", "安全"]

    thresholds = config.get_thresholds()
    commands = config.get_commands(groups)

    print(f"📊 将执行 {len(commands)} 个巡检命令\n")

    all_reports = []
    start_time = datetime.now()

    for server in servers:
        print(f"🔄 巡检 {server['name']} ({server['host']})...")
        server_start = datetime.now()

        executor = SSHExecutor(server)
        parser = MetricParser(thresholds)

        # 并行执行所有命令
        metrics = executor.execute_batch(commands)

        # 解析指标
        cpu_stats = parser.parse_cpu(metrics)
        mem_stats = parser.parse_memory(metrics)
        disk_stats = parser.parse_disk(metrics)
        net_stats = parser.parse_network(metrics)
        sec_stats = parser.parse_security(metrics)

        # 收集所有告警
        all_alerts = []
        all_alerts.extend(cpu_stats.get("alerts", []))
        all_alerts.extend(mem_stats.get("alerts", []))
        all_alerts.extend(disk_stats.get("alerts", []))
        all_alerts.extend(net_stats.get("alerts", []))
        all_alerts.extend(sec_stats.get("alerts", []))

        # 确定总体状态
        has_critical = any(a.get("level") == "CRITICAL" for a in all_alerts)
        has_warning = any(a.get("level") == "WARNING" for a in all_alerts)
        overall = "CRITICAL" if has_critical else "WARNING" if has_warning else "NORMAL"

        duration_ms = int((datetime.now() - server_start).total_seconds() * 1000)

        report = ServerReport(
            name=server["name"],
            host=server["host"],
            timestamp=server_start.strftime("%Y-%m-%d %H:%M:%S"),
            duration_ms=duration_ms,
            metrics=metrics,
            alerts=all_alerts,
            overall_status=overall
        )

        all_reports.append(report)

        # 打印状态
        if all_alerts:
            for alert in all_alerts:
                level_icon = "🔴" if alert.get("level") == "CRITICAL" else "🟠"
                print(f"   {level_icon} {alert.get('message', '')}")
        else:
            print_success(f"{server['name']} 无告警")

    print(f"\n✅ 巡检完成！共 {len(all_reports)} 台服务器")

    # 生成报告
    report_md = ReportGenerator.generate_md_report(all_reports, config.data)

    # 保存报告
    report_dir = WORK_DIR / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = report_dir / f"report_{timestamp_str}.md"

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_md)

    print(f"\n📄 报告已保存: {report_path}")

    # 保存历史数据
    for report in all_reports:
        history_dir = WORK_DIR / "history" / report.name
        history_dir.mkdir(parents=True, exist_ok=True)
        month_str = datetime.now().strftime("%Y-%m")
        history_file = history_dir / f"{month_str}.jsonl"

        record = {
            "timestamp": report.timestamp,
            "duration_ms": report.duration_ms,
            "overall_status": report.overall_status,
            "alerts_count": len(report.alerts),
            "metrics": {k: v.raw_output[:200] for k, v in report.metrics.items()}
        }

        with open(history_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # 发送通知
    notification_config = config.data.get("notification", {})
    feishu_webhook = notification_config.get("feishu_webhook", "")

    if feishu_webhook:
        print("\n📤 发送飞书通知...")
        notifier = FeishuNotifier(feishu_webhook)

        # 生成摘要
        summary_text = f"**服务器巡检报告**\n"
        summary_text += f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        summary_text += f"🖥️ {len(all_reports)} 台服务器\n"

        critical_count = sum(1 for r in all_reports if r.overall_status == "CRITICAL")
        warning_count = sum(1 for r in all_reports if r.overall_status == "WARNING")

        if critical_count > 0:
            summary_text += f"🔴 严重告警: {critical_count} 台\n"
        if warning_count > 0:
            summary_text += f"🟠 警告: {warning_count} 台\n"
        if critical_count == 0 and warning_count == 0:
            summary_text += f"🟢 全部正常\n"

        summary_text += f"\n📄 完整报告: {report_path}"

        ok = await notifier.send(summary_text)
        if ok:
            print_success("飞书通知已发送")

    return all_reports, report_md


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Server Inspect - Linux 服务器巡检工具")
    parser.add_argument("--host", "-H", help="指定巡检的主机名（模糊匹配）")
    parser.add_argument("--groups", "-G", help="指定巡检的指标组（逗号分隔）")
    parser.add_argument("--async", dest="use_async", action="store_true", help="使用异步执行（多主机并行）")

    args = parser.parse_args()

    groups = args.groups.split(",") if args.groups else None

    # 执行巡检
    reports, md = asyncio.run(run_inspect(args.host, groups))

    # 打印报告
    print("\n" + "="*60)
    print(md)


if __name__ == "__main__":
    main()
