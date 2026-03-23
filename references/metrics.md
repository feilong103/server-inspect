# 巡检指标详解

## 指标分组与命令对照表

### 🔰 系统基础（System Basic）

| 指标 ID | 名称 | 命令 | 阈值 | 异常说明 |
|---------|------|------|------|---------|
| hostname | 主机名 | `/usr/bin/hostname` | - | - |
| uptime | 运行时间 | `/usr/bin/uptime -p` | < 5min | 刚重启 |
| who | 登录用户 | `/usr/bin/who` | - | 异常用户数 |
| last | 最近登录 | `/usr/bin/last -n 10` | - | 非白名单 IP |
| uname | 系统信息 | `/usr/bin/uname -a` | - | - |

### ⚙️ CPU 与负载（CPU & Load）

| 指标 ID | 名称 | 命令 | 阈值 | 异常说明 |
|---------|------|------|------|---------|
| cpu_usage | CPU 使用率 | `top -bn1 \| head -5` | > 80% | CPU 持续高位 |
| loadavg | 负载均值 | `cat /proc/loadavg` | 1m > 4 | 负载过高 |
| top_cpu | TOP 5 进程 | `ps aux --sort=-%cpu \| head -6` | - | 占用过高进程 |
| mpstat | 多核详情 | `mpstat -P ALL 1 1` | - | CPU 不均衡 |
| vmstat | 虚拟内存 | `vmstat 1 3` | - | r > 4*CPU核数 |

### 🧠 内存（Memory）

| 指标 ID | 名称 | 命令 | 阈值 | 异常说明 |
|---------|------|------|------|---------|
| mem_usage | 内存使用率 | `/usr/bin/free -h` | > 85% | 内存不足 |
| swap | Swap 使用 | `/usr/bin/free | grep Swap` | > 50% | Swap 频繁使用 |
| oom | OOM 事件 | `journalctl -k \| grep -i "out of memory" \| tail -5` | ≥ 1 | 内存压力严重 |
| smaps | 大内存进程 | `ps aux --sort=-%mem \| head -5` | - | 内存泄漏排查 |

### 💾 磁盘（Disk）

| 指标 ID | 名称 | 命令 | 阈值 | 异常说明 |
|---------|------|------|------|---------|
| disk_usage | 磁盘使用率 | `/bin/df -h` | > 90% | 磁盘空间不足 |
| disk_inode | inode 使用 | `/bin/df -i` | > 90% | 小文件耗尽 |
| disk_io | IO 读写 | `iostat -xz 1 2` | await > 20ms | IO 瓶颈 |
| du_top | 目录大小 | `du -sh /var/* 2>/dev/null \| sort -rh \| head -10` | - | 异常大目录 |
| lsof_open | 打开文件数 | `lsof + / 2>/dev/null \| wc -l` | > 100000 | 文件句柄耗尽 |

### 🌐 网络（Network）

| 指标 ID | 名称 | 命令 | 阈值 | 异常说明 |
|---------|------|------|------|---------|
| netstat_conn | 连接统计 | `netstat -an \| wc -l` | > 5000 | 连接数异常 |
| ss_summary | Socket 统计 | `ss -s` | - | - |
| port_listen | 监听端口 | `ss -tlnp` | 非白名单端口 | 异常开放端口 |
| bandwidth | 网卡带宽 | `cat /proc/net/dev \| awk '{print $1,$2,$10}'` | 带宽 > 80% | 流量异常 |
| tcp_status | TCP 状态 | `netstat -an \| grep tcp \| awk '{print $6}' \| sort \| uniq -c` | TIME_WAIT > 5000 | 连接泄漏 |
| arp | ARP 表 | `arp -a` | - | 异常 ARP 条目 |

### 🔧 服务（Services）

| 指标 ID | 名称 | 命令 | 阈值 | 异常说明 |
|---------|------|------|------|---------|
| service_ssh | SSH 服务 | `/usr/bin/systemctl is-active sshd` | 非 active | SSH 异常 |
| service_nginx | Nginx | `/usr/bin/systemctl is-active nginx` | 非 active | Web 不可用 |
| service_mysql | MySQL | `/usr/bin/systemctl is-active mysql` | 非 active | 数据库异常 |
| service_httpd | Apache | `/usr/bin/systemctl is-active httpd` | 非 active | 服务异常 |
| service_cron | Cron | `/usr/bin/systemctl is-active cron` | 非 active | 定时任务失效 |
| service_fail2ban | Fail2ban | `/usr/bin/systemctl is-active fail2ban` | 非 active | 安全防护失效 |

### 🔒 安全（Security）

| 指标 ID | 名称 | 命令 | 阈值 | 异常说明 |
|---------|------|------|------|---------|
| failed_login | 登录失败 | `grep "Failed password" /var/log/auth.log \| tail -20` | ≥ 5 次/小时 | 暴力破解 |
| ssh_brute | SSH 暴力破解 | `grep "Invalid user\\|Failed password" /var/log/auth.log \| tail -10` | ≥ 10 次 | 攻击痕迹 |
| sudo_usage | Sudo 使用 | `journalctl -t sudo \| tail -20` | - | 异常提权 |
| selinux | SELinux | `getenforce` | Disabled（高安全场景） | 未启用 |
| firewall | 防火墙 | `systemctl is-active firewalld` | - | 防火墙状态 |
| su_user | SU 使用记录 | `grep "su:" /var/log/auth.log \| tail -10` | - | 异常用户切换 |
| crontab | 定时任务 | `crontab -l && ls /etc/cron.d/` | - | 异常任务 |

### 📦 应用层（Application，可扩展）

| 指标 ID | 名称 | 命令 | 阈值 |
|---------|------|------|------|
| nginx_conn | Nginx 连接数 | `ss -tn \| grep :80 \| wc -l` | > 1000 |
| mysql_threads | MySQL 线程 | `mysql -e "SHOW PROCESSLIST;" \| wc -l` | > 200 |
| redis_mem | Redis 内存 | `redis-cli info memory \| grep used_memory_human` | - |
| docker_ps | Docker 容器 | `docker ps --format "{{.Names}} {{.Status}}"` | 有 exited 容器 |
| k8s_pods | K8s Pod 状态 | `kubectl get pods` | 有非 Running |

---

## 告警阈值速查

| 级别 | 指标 | 阈值 | 说明 |
|------|------|------|------|
| 🔴 Critical | cpu_percent | ≥ 95% | CPU 严重过载 |
| 🔴 Critical | mem_percent | ≥ 95% | 内存严重不足 |
| 🔴 Critical | disk_percent | ≥ 95% | 磁盘空间耗尽 |
| 🔴 Critical | loadavg_1m | ≥ CPU核数×2 | 系统极度繁忙 |
| 🟠 Warning | cpu_percent | ≥ 80% | CPU 使用率高 |
| 🟠 Warning | mem_percent | ≥ 85% | 内存使用率高 |
| 🟠 Warning | disk_percent | ≥ 85% | 磁盘使用率高 |
| 🟠 Warning | loadavg_1m | ≥ CPU核数×1.5 | 负载偏高 |
| 🟠 Warning | failed_login | ≥ 5次/小时 | 疑似暴力破解 |
| 🟡 Info | swap_percent | ≥ 10% | Swap 开始使用 |
| 🟡 Info | tcp_timewait | ≥ 5000 | TIME_WAIT 连接多 |
