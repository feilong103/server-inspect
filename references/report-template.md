# 巡检报告模板

## 报告结构

```
┌─────────────────────────────────────────────────────────┐
│  🖥️  服务器巡检报告                                          │
│  📅  巡检时间: 2026-03-23 09:00:00 (GMT+8)                   │
│  ⏱️  耗时: 45s                                              │
└─────────────────────────────────────────────────────────┘

## 一、巡检概览

| 指标 | 状态 | 说明 |
|------|------|------|
| 主机数 | 3 台 | 3 成功 / 0 失败 |
| CPU 异常 | ⚠️ 1台 | prod-web-01 CPU 92% |
| 内存异常 | ✅ 正常 | 全部低于阈值 |
| 磁盘异常 | 🔴 2台 | web-01/db-01 磁盘 > 90% |
| 安全异常 | ✅ 正常 | 无登录失败告警 |

**总体评价**: 🟠 关注

> AI 分析：本次巡检发现 3 处需要关注的问题，其中 prod-web-01 磁盘空间紧张，建议尽快扩容或清理；prod-db-01 CPU 使用率偏高，建议排查是否有慢查询。

---

## 二、分服务器详情

### 2.1 🖥️ prod-web-01（192.168.1.10）

**运行状态**: 🟢 正常 | **上次巡检**: 2026-03-22 09:00

#### 系统基础
| 指标 | 值 | 状态 |
|------|-----|------|
| 主机名 | prod-web-01 | ✅ |
| 运行时间 | 128 天 14 小时 | ✅ |
| 当前用户 | 3 人登录 | ✅ |
| 系统版本 | Ubuntu 22.04.4 LTS | ✅ |

#### CPU 与负载
| 指标 | 值 | 阈值 | 状态 |
|------|-----|------|------|
| CPU 使用率 | 92.3% | 80% | 🔴 告警 |
| 1分钟负载 | 5.42 | 4.0 | 🔴 告警 |
| 5分钟负载 | 4.81 | - | ⚠️ 偏高 |
| 15分钟负载 | 3.22 | - | ✅ |

**Top 5 CPU 进程**:
```
 PID  USER    %CPU  %MEM  COMMAND
3121  nginx   45.2  2.1   nginx: worker
4233  www-data 38.7  1.8   php-fpm: pool
5122  mysql   8.3   12.4  mysqld
```

#### 内存
| 指标 | 值 | 阈值 | 状态 |
|------|-----|------|------|
| 内存总量 | 7.6G | - | - |
| 已用 | 5.8G | - | - |
| 使用率 | 76.3% | 85% | 🟢 正常 |
| Swap | 512M / 2G | 50% | 🟢 正常 |

#### 磁盘
| 挂载点 | 使用率 | 阈值 | 状态 |
|--------|--------|------|------|
| / | 87% | 90% | ⚠️ 关注 |
| /boot | 23% | - | ✅ |
| /var | 91% | 90% | 🔴 告警 |
| /data | 67% | - | ✅ |

#### 网络
| 指标 | 值 | 阈值 | 状态 |
|------|-----|------|------|
| TCP 连接数 | 1,245 | 5,000 | ✅ |
| TIME_WAIT | 342 | 5,000 | ✅ |
| 监听端口 | 12 个 | - | ✅ |
| 带宽使用 | 45% | 80% | ✅ |

#### 安全
| 指标 | 结果 | 状态 |
|------|------|------|
| 登录失败（1h） | 0 次 | ✅ |
| SSH 暴力破解 | 0 次 | ✅ |
| 防火墙 | active | ✅ |
| Sudo 提权 | 3 次正常 | ✅ |

---

## 三、⚠️ 异常汇总

| # | 级别 | 服务器 | 指标 | 当前值 | 阈值 | 建议 |
|---|------|--------|------|--------|------|------|
| 1 | 🔴 Critical | prod-web-01 | /var 磁盘 | 91% | 90% | 扩容或清理 /var/log |
| 2 | 🔴 Critical | prod-web-01 | CPU | 92.3% | 80% | 排查 nginx/php-fpm 进程 |
| 3 | 🟠 Warning | prod-db-01 | CPU | 83.5% | 80% | 检查慢查询 |
| 4 | 🟠 Warning | prod-web-01 | 负载 | 5.42 | 4.0 | 关注是否有请求风暴 |

---

## 四、💡 AI 优化建议

### 建议 1: 紧急 — 清理 /var 分区（prod-web-01）

**问题**: /var 分区使用率 91%，剩余空间不足 5GB，存在服务中断风险。

**操作建议**:
```bash
# 查看 /var/log 大文件
du -sh /var/log/*
# 清理旧的日志（保留最近 7 天）
find /var/log -name "*.log" -mtime +7 -delete
# 清理旧的压缩日志
find /var/log -name "*.gz" -mtime +30 -delete
# 查看 apt 缓存
du -sh /var/cache/apt/archives/
# 清理 apt 缓存
apt-get clean
```

**预计释放**: 3~8 GB（根据日志量）

---

### 建议 2: 中期 — 优化 PHP-FPM 配置（prod-web-01）

**问题**: PHP-FPM 进程占用 CPU 高（38.7%），可能导致响应慢。

**操作建议**:
```bash
# 检查当前 PHP-FPM 配置
cat /etc/php/8.1/fpm/pool.d/www.conf | grep -E "pm.max_children|pm.start_servers|pm.min_spare"
# 建议调整为（根据 7.6G 内存）:
# pm.max_children = 20
# pm.start_servers = 5
# pm.min_spare = 3
# pm.max_spare = 10
```

---

### 建议 3: 规划 — 数据库连接池优化（prod-db-01）

**问题**: MySQL 连接数偏高，可能存在连接泄漏。

**操作建议**:
```sql
-- 检查当前连接
SHOW STATUS LIKE 'Threads_connected';
-- 检查最大连接数
SHOW VARIABLES LIKE 'max_connections';
-- 检查长时间运行的查询
SHOW FULL PROCESSLIST;
```

---

## 五、📊 历史趋势（7天）

```
prod-web-01 CPU%
Day  17   18   19   20   21   22   23
  █
  █  █  █▒  █▒  ██  █▒  ██  ██
  ▒           ▒  ▒
  
  图例: █ 正常(<80%)  ▒ 警告(80-90%)  █ Critical(>90%)
```

**趋势分析**: CPU 使用率近 3 天持续偏高，建议关注业务流量变化。

---

## 六、附录

### A. 巡检命令输出（完整日志）

<details>
<summary>点击展开：原始命令输出</summary>

```
[hostname]
prod-web-01

[uptime -p]
up 18 weeks, 4 days, 14 hours 23 minutes

[top -bn1]
top - 09:00:15 up 128 days, 14:23,  3 users,  load average: 5.42, 4.81, 3.22
Tasks: 234 total,   2 running, 232 sleeping,   0 stopped,   0 zombie
%Cpu(s): 92.3 us,  3.1 sy,  0.0 ni,  4.6 id,  0.0 wa,  0.0 hi,  0.0 si,  0.0 st
KiB Mem :  7990004 total,  1894524 free,  5123456 used,   972024 buff/cache
KiB Swap:  2097148 total,  1584320 free,   512828 used.

[free -h]
              total        used        free      shared  buff/cache   available
Mem:          7.6Gi       4.9Gi       1.8Gi       234Mi       1.1Gi       2.4Gi
Swap:         2.0Gi       501Mi       1.5Gi

[df -h]
Filesystem      Size  Used Avail Use% Mounted on
/dev/sda1        50G   44G  5.0G  91% /var
...
```
</details>

### B. 巡检元数据

| 字段 | 值 |
|------|-----|
| 报告版本 | v1.0 |
| 巡检工具 | server-inspect skill |
| AI 模型 | qclaw/modelroute |
| 报告生成时间 | 2026-03-23 09:00:45 |
| 配置文件版本 | 1.0 |
