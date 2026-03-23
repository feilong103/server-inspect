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

WORK_DIR = Path.home() / ".qclaw" / "server-inspect"
CONFIG_FILE = WORK_DIR / "config.json"


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
        d = WORK_DIR / "history" / host
        if not d.exists(): return []
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        records = []
        for f in d.glob("*.jsonl"):
            with open(f) as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                        if r.get("timestamp","")[:10] >= cutoff:
                            records.append(r)
                    except: pass
        return sorted(records, key=lambda x: x.get("timestamp",""))

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

    # 保存历史
    for r in all_reports:
        hd = WORK_DIR / "history" / r.name
        hd.mkdir(parents=True, exist_ok=True)
        ms = datetime.now().strftime("%Y-%m")
        hf = hd / f"{ms}.jsonl"

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
    print(f"📊 历史: ~/.qclaw/server-inspect/history/")

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
