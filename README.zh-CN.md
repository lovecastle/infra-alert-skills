# infra-alert-skills

[English](README.md) · [Tiếng Việt](README.vi.md) · **简体中文**

一组 Agent Skills：让 Claude 为小规模基础设施（几台 VPS、一台 GPU 机器、Cloudflare 上的服务）安装并运维监控，
出问题时和恢复时都推送到聊天工具。支持 Telegram、企业微信群机器人、飞书/Lark、钉钉、Bark、ntfy、Server酱、
PushPlus 以及任意 webhook。

你只需说明要监控什么。Claude 会先查看服务器、给出监控方案、安装单文件探针、验证告警确实能送达，之后还能帮你
分析每条告警的原因。

```
[infrawatch] 🔴 URGENT | vps-01: 1 problem, 1 resolved
🔴 disk /: 96% used, 1.8 GB free
✅ http api: HTTP 200 (after 12m)
#urgent #vps_01
```

它源自我们监控 [allshorts.net](https://allshorts.net) 的做法（几台服务器、一台 GPU 机器和 Cloudflare，全部推送到
一个 Telegram 群），并在参考了 Uptime Kuma、Gatus、Healthchecks、哪吒监控、Beszel、青龙等项目之后重写为通用版本。
我们在安全、反爬虫和告警上的思路见 [docs/approach.md](docs/approach.md)，参考过的项目见 [docs/prior-art.md](docs/prior-art.md)（均为英文）。

## 包含的技能

| Skill | 作用 |
|---|---|
| [`setup-alert-channel`](skills/setup-alert-channel/SKILL.md) | 创建告警渠道并发送测试消息：Telegram 机器人与频道/群组、chat id、安全保存 token；也包含企业微信、飞书、钉钉等八种渠道 |
| [`monitor-hosts`](skills/monitor-hosts/SKILL.md) | 通过 SSH 在 Linux 服务器上安装 `infrawatch.py`：磁盘、内存、负载、systemd 服务、Docker 容器、HTTP/TCP 端点、证书到期、NVIDIA 显卡、重启、OOM，以及可选的安全基线 |
| [`monitor-cloudflare`](skills/monitor-cloudflare/SKILL.md) | 部署一个 Cloudflare Worker（免费套餐即可）：每分钟从外部拨测你的 URL，并在某台主机停止发送心跳时告警 |
| [`triage-alerts`](skills/triage-alerts/SKILL.md) | 收到告警后用只读命令定位原因、给出修复建议，并调整噪音告警 |
| [`run-ops-agents`](skills/run-ops-agents/SKILL.md) | 把 agent 放进 Telegram 告警群：lead 盯着未恢复的事件并分派，agent 排查、按策略修复、用按钮请求审批、汇报，并响应 `@` |

两个组件，可单独使用，配合更可靠：

- **`infrawatch.py`**（每台主机上）：单个 Python 文件，只用标准库，由 systemd timer 每分钟运行一次。没有常驻进程，
  没有数据库，不需要安装任何依赖。
- **Worker**（主机之外）：一台已经宕机的机器无法自己报告，Worker 负责发现它。主机也会反过来检查 Worker 的
  `/health`，双方互相监视。

## 安装

在 Claude Code 中：

```
/plugin marketplace add lovecastle/infra-alert-skills
/plugin install infra-alerts@infra-alert-skills
```

或使用 [skills CLI](https://github.com/vercel-labs/skills)（适用于 Claude Code 及其他读取 `SKILL.md` 的 agent）：

```bash
npx skills add lovecastle/infra-alert-skills
```

这些技能需要 agent 具备 shell（会用到 `ssh`、`scp`、`curl`、`npx wrangler`）。

## 使用

用自己的话说就行，中文也可以：

| 你想要 | 可以这样说 |
|---|---|
| 配置告警渠道 | “帮我配置一个企业微信群机器人，用来接收服务器告警”／“帮我创建 Telegram 告警频道” |
| 监控一台服务器 | “在 `vps-01`（我的 ssh 别名）上安装监控，有问题推送到钉钉” |
| 监控显卡机器 | “`rtx` 这台机器用 RTX 显卡跑推理服务，把显卡也监控上，任务挂了通知我” |
| 知道服务器或网站彻底宕机 | “部署 Cloudflare 上的监控：拨测 https://example.com，vps-01 没有心跳时告警” |
| 分析一条告警 | “收到这条告警：`[infrawatch] 🔴 vps-01: memory: 97% used`，怎么回事？” |
| 维护期间静默 | “把 vps-01 的告警静默一小时” |
| 告警分级用中文，每级一个话题 | “告警分类用中文显示，紧急告警发到群里的‘紧急’话题” |
| 群里的值班 agent | “在告警群里放一个 agent：严重告警 5 分钟没人处理就自动排查，可以重启服务，其他操作先问” |

Claude 在修改服务器或在你的 Cloudflare 账号中创建资源之前都会先征得同意，并在启用监控前给你看一次试运行
（`infrawatch.py check`）的结果。

## 国内用户

- **渠道**：企业微信群机器人（`QYWX_KEY`）、飞书（`FSKEY`／`FSSECRET`）、钉钉（`DD_BOT_TOKEN`／`DD_BOT_SECRET`）
  开箱即用；个人可用 Bark（`BARK_PUSH`）、Server酱（`PUSH_KEY`）、PushPlus（`PUSH_PLUS_TOKEN`）。
  变量名沿用青龙 `sendNotify` 的约定，已有配置可以直接粘贴。
- **关键词安全设置**：钉钉、飞书机器人若使用“自定义关键词”，请填 `infra`，每条消息标题都包含它；也支持加签。
- **Telegram**：服务器无法访问时，把 `TG_API_HOST` 指向你自己的反向代理；Cloudflare Worker 本身可以直接访问
  Telegram。
- 各渠道的获取方式、接口与限制：[other-channels.md](skills/setup-alert-channel/references/other-channels.md)。

## 告警规则

- **只推送变化**：故障开始和恢复各一条，并附持续时间，不会每分钟重复。
- **确认后再推送**：主机上连续 3 次异常，Worker 连续 2 次拨测失败；偶发抖动不打扰任何人。
- **防抖**：已触发的告警要回落到阈值以下一定幅度才算恢复。
- **每次运行最多一条消息**，合并所有变化；新故障有提示音，恢复与日报静默推送。
- **严重故障每 4 小时提醒一次**，警告只说一次。
- **发送失败的消息会保留**，下次运行时补发。
- **维护静默**：`silence 2h` 期间继续检测，结束时汇报仍未恢复的问题。
- **每日报告兼作存活证明**：没收到日报，说明监控本身出了问题。
- **互相监视**：主机向 Worker 发心跳，主机也检查 Worker 的 `/health`。
- **每条消息都有分类**：🔴 紧急（`#urgent`）、🟠 关注（`#attention`）、🛡 安全（`#security`）、✅ 恢复（`#resolved`）、
  📊 统计（`#stats`）。标题里的词在 `[labels]` 中自定义（默认英文）；`#` 标签固定，便于在群里筛选；Telegram 上每类
  可以进自己的话题。[详情](skills/monitor-hosts/references/checks.md#categories)。


## 群里的 Agent（可选）

监控把消息推到群里；[`run-ops-agents`](skills/run-ops-agents/SKILL.md) 让群里始终有“人”在看。`opsagent.py` 运行一个或
多个 agent，每个 agent 是告警群里的一个 Telegram 机器人：

- **Lead agent 盯着所有未恢复的事件**（来自 Worker 的 `/incidents`）。严重事件会附两个按钮：*I take it*（我来处理）
  和 *Start now*（立即开始）。
- **等待时间内无人认领**（默认 5 分钟），负责该主机的 agent 开始处理：通过 SSH 用无界面模式的 Claude Code 排查，
  遵循 `triage-alerts`。
- **只自动执行你写明的策略允许的操作**（重启指定服务、清理日志）。其余操作先发方案（发现了什么、具体命令、
  影响范围、如何回滚、如何验证），附 *Approve* / *Refuse* 按钮。30 分钟无人答复视为拒绝。
- **处理完在群里汇报**：原因、做了什么、结果、遗留问题。若 15 分钟后事件仍未恢复，会大声交还给管理员。
- **管理员直接在群里指挥和讨论**：`@agent <指令或问题>`，或回复它的消息。群里可以有多个 agent，各管一块；
  lead 负责分派。命令：`/status`、`/pause`、`/resume`、`/stop`。

安全不靠模型的判断：只有你列出的 Telegram 用户 id 说话才算数（在群里不等于有权限）；审批是由守护进程校验的按钮；
agent 用一个 sudoers 列表很短的账号登录服务器；`/pause` 可随时叫停。授权前请先读
[access.md](skills/run-ops-agents/references/access.md)。

空闲时没有费用：只有在有事可做时才启动 Claude Code。目前 agent 只支持 Telegram 群。

## 会安装什么

每台主机（一条命令即可卸载，见技能说明）：

```
/opt/infrawatch/infrawatch.py                       探针
/etc/infrawatch/config.ini                          监控项与阈值，不含密钥
/etc/infrawatch/notify.env                          token，仅 root 可读（600）
/etc/systemd/system/infrawatch.{service,timer}      每分钟运行
/var/lib/infrawatch/state.json                      未恢复的告警、基线
```

Cloudflare 账号中：一个 Worker（`infra-alerts`）和一个 D1 数据库，这个规模在免费额度之内
（[具体数字](skills/monitor-cloudflare/references/limits.md)）。

不会向我们或任何第三方上报数据。

## 测试情况

版本 0.2.0，如实说明：

- `infrawatch.py`：46 个测试，在 Python 3.8 与 3.11 上运行；采集部分用 `df`、`/proc`、`systemctl`、`docker`、
  `nvidia-smi`、`ss`、`journalctl` 的真实输出样本测试，告警逻辑与命令行用本地模拟的 Telegram 做端到端测试。
- Worker：16 个测试（Node，使用真实 SQL 引擎代替 D1），并在 Cloudflare 运行时（`wrangler dev`）中完整跑过
  故障、恢复、心跳与各接口。
- `opsagent.py`：19 个测试，其中一个用本地服务器模拟 Telegram 和 Worker、用替身程序代替 `claude -p`，完整跑通
  守护进程（线程、长轮询、按钮、审批）。命令行参数已被真实的 Claude Code 2.1 接受；成功时的结构化输出格式依据其
  文档，尚未在已登录环境中实测。**尚未在真实 Telegram 和真实事件上运行过。** 建议先把 `grace_minutes` 调大、
  sudoers 列表保持很短，并观察它。
- **尚未完成**：这一版探针还没有由作者在生产 Linux 主机上运行过；Telegram 以外的渠道按各自官方文档实现，
  但没有经过真实服务发送验证。所以安装技能要求 Claude 先在你的主机上运行 `check` 和 `test` 并与你一起确认输出。
  如果某项检查在你的系统上读数有误，欢迎提 issue 并附上 `check` 的输出。

## 暂不包含

历史数据、图表与状态页（可与 Beszel、哪吒监控等搭配使用）；Cloudflare 分析数据告警；Windows/macOS 主机；
Telegram 以外渠道上的 agent；监控本身的自动处置（`infrawatch.py` 只负责告知，
处置交给人或按策略行事的 agent）。

## 许可证

MIT，见 [LICENSE](LICENSE)。
