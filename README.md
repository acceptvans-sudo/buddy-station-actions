# Buddy 加油站 · GitHub Actions 自动化

每天定时在**云端 runner** 完成 **签到** + **派猫猫旅行**，并把结果推送到**飞书**。
无需任何本地常开设备。

```
┌─────────────────┐   ①导出令牌    ┌─────────────────┐
│  本机（一次性）  │ ──────────────▶ │  GitHub Secret  │
│  已登录客户端    │                │ WB_REFRESH_TOKEN│
└─────────────────┘                 └────────┬────────┘
                                           │ ②定时拉取
                                           ▼
                            ┌──────────────────────────────┐
                            │   Actions（ubuntu-latest）   │
                            │  刷新令牌 → 签到 → 派猫闭环  │
                            └───────────────┬──────────────┘
                                            │ ③推送
                                            ▼
                                    ┌────────────────┐
                                    │   飞书机器人卡片 │
                                    │ 签到区│猫猫区   │
                                    └────────────────┘
```

## 功能特性

| 需求 | 实现 |
|---|---|
| 每天定时跑一次，单次内完成签到 + 派猫 | `schedule: cron` 单 job 内顺序执行 |
| 先领取已到家的积分，再判断能否派新一趟 | 先查 `state`，`arrived` → 先 `claim` |
| 当天已派过则跳过 | 派遣前查 `daily_limit_reached`，为真则不发写请求 |
| 签到与猫猫分开展示 | 飞书卡片用 `hr` 分割为独立区块 |
| **猫猫失败不影响签到结论** | 旅行异常全部收敛在自身结果内；退出码只看签到 |
| 云端无登录态 → 本机导出 token | `export_credentials.py` 注入 Secret |
| token 明文不入库、不进日志 | 仅存 Secret；输出只显示脱敏指纹 |
| 解释器路径不硬编码 | `command -v python3` 动态获取 |
| 接口名以实测为准 | 见 [`docs/API-EVIDENCE.md`](docs/API-EVIDENCE.md) |

## 目录结构

```
buddy-station-actions/
├── .github/workflows/buddy-station.yml   # 每日定时任务
├── scripts/
│   ├── buddy_station.py                  # 云端主脚本（纯标准库）
│   ├── export_credentials.py             # 本机凭据导出器
│   ├── probe_readonly.py                 # 只读接口探针（核对接口用）
│   ├── probe_refresh.py                  # 刷新链路探针
│   ├── probe_rotation.py                 # 令牌轮换行为探针
│   └── probe_crossdomain.py              # 跨域可用性探针
├── docs/API-EVIDENCE.md                  # 脱敏接口实测证据
└── .gitignore
```

主脚本仅依赖 Python 3.8+ **标准库**，无需 `pip install`。

---

## 部署步骤

### 1. 推送代码到仓库

```bash
cd buddy-station-actions
git init && git add . && git commit -m "feat: Buddy 加油站每日签到与派猫猫"
git remote add origin https://github.com/<owner>/<repo>.git
git push -u origin main
```

### 2. 探测本机登录态（不接触令牌明文）

```bash
python scripts/export_credentials.py --check
```

预期输出（仅脱敏）：

```
登录态文件：…\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info
refresh_token：eyJhbG...4OTA (len=700)
✅ 本机登录态可用于导出凭据。
```

### 3. 写入 GitHub Secret

需要已安装并登录 [gh CLI](https://cli.github.com/)（`gh auth login`）：

```bash
python scripts/export_credentials.py --repo <owner>/<repo>
```

该命令会把长效令牌写入仓库 Secret `WB_REFRESH_TOKEN`。

> **令牌不落盘、不进命令行参数**：脚本经 `gh secret set` 直接写入，
> 不产生临时文件，也不进入 shell 历史。

手动配置等价路径：仓库 → Settings → Secrets and variables → Actions → New repository secret。

### 4. 配置飞书推送（可选）

在飞书群里添加「自定义机器人」，复制 Webhook 地址，然后添加仓库 Secret：

| Secret 名 | 必填 | 说明 |
|---|---|---|
| `WB_REFRESH_TOKEN` | ✅ | 长效令牌（由步骤 3 写入） |
| `WB_ACCESS_TOKEN` | ❌ | 短期令牌，仅作刷新失败时的回落 |
| `FEISHU_WEBHOOK` | ❌ | 机器人 Webhook，不填则跳过推送 |
| `FEISHU_SECRET` | ❌ | 机器人「签名校验」密钥，开了加签才需要 |
| `GH_PAT` | ❌ | 令牌自动续期用，详见步骤 5 |

### 5. 配置令牌自动续期（可选，但强烈建议）

**背景**：长效令牌的 JWT `exp` 在**签发时固定为 +30 天**，服务端不会因为「被使用」
而延长它。每次刷新虽然会签发一个 `exp` 更晚的新令牌，但若不写回 Secret，
Secret 里那个旧令牌到期后整条链路就会断（本仓库原配置即 2026-11-21 到期）。

配置 `GH_PAT` 后，任务每次运行都会把新令牌自动写回 Secret，有效期不断滚动，
**从此无需人工续期，也彻底不依赖本机**。

**创建 PAT**：

1. 打开 <https://github.com/settings/personal-access-tokens/new>
2. **Token name**：`buddy-station-rotation`
3. **Expiration**：`1 year`
4. **Resource owner**：选你的账号
5. **Repository access** → 选 **Only select repositories**
   → 下拉框里**只勾 `buddy-station-actions`**（不要选 All repositories）
6. **Repository permissions** → 展开 **Secrets** → 选 **Read and write**
7. 点 **Generate token**，复制那串 `github_pat_...`（**只显示一次**）

**添加为仓库 Secret**：

- 仓库 → **Settings** → 左侧 **Secrets and variables** → **Actions**
- → **New repository secret**
- Name：`GH_PAT`（必须完全一致）
- Secret：粘贴 `github_pat_...`
- → **Add secret**

> ⚠️ 权限只给到「这一个仓库的 Secret」，动不了代码、动不了其他仓库。
> 不配置也能正常运行，只是令牌到期后需在本机重跑一次 `export_credentials.py`。

### 6. 手动触发验证

Actions 页面 → 该 workflow → **Run workflow**。约 10 秒后应看到绿色对勾，
且飞书群收到卡片。日志中若出现 `✅ 长效令牌已回写，有效期自动续期`，
说明自动续期也生效了。

---

## 飞书卡片效果

卡片把**签到结果**与**猫猫状态**分为两个独立区块：

```
┌──────────────────────────────────────┐
│ Buddy 加油站 · 每日签到 + 派猫猫      │  ← 绿色=成功 / 红色=签到失败
├──────────────────────────────────────┤
│ ✅ 签到成功                           │  ← 签到区块
│ · 明细：签到成功                      │
│ · 积分余额：600                       │
│ · 连续签到：6 天                      │
├──────────────────────────────────────┤
│ 🐱 猫猫旅行（旅行中）                 │  ← 猫猫区块（独立，互不影响）
│ · 地点：健身房                        │
│ · 到达倒计时：还需 4 小时 0 分        │
│ · ✅ 领取旅行积分 +9                  │
│ · ✅ 已派 Buddy 前往【健身房】        │
│ · Buddy 手记：亲爱的铲屎官……         │
├──────────────────────────────────────┤
│ GitHub Actions · 2026-10-22 13:36:21 │
└──────────────────────────────────────┘
```

---

## 退出码约定

| 退出码 | 含义 | workflow 行为 |
|---|---|---|
| `0` | 签到成功（含「今日已签」幂等）；猫猫成功/部分失败/跳过均归此类 | ✅ 绿色通过 |
| `1` | **签到失败** | ❌ 标红告警 |
| `2` | 凭据缺失或令牌刷新失败 | ❌ 标红，提示重跑导出脚本 |

> **猫猫部分失败不会让任务变红** —— 这是需求明确要求的行为，已通过注入故障实测验证
> （见 `docs/API-EVIDENCE.md` 第 7 节）。

---

## 设计要点与已知约束

### 为什么用长效令牌而非短期令牌

实测（`scripts/probe_rotation.py` + JWT payload 解码）：

- 长效令牌有效期 **≈30 天**，短期 accessToken **≈28 天**。
- 每次刷新都会**轮换**出新令牌，旧令牌在**宽限期内仍可用**（可反复使用）。
- ⚠️ **长效令牌的 `exp` 在签发时固定为 +30 天，服务端不会因「被使用」而延长它**
  （JWT payload 实测：`iat` 与 `exp` 恒差 30 天，反复刷新同一个令牌 exp 不变）。

所以只把长效令牌存进 Secret 并不够 —— 到了它的 `exp` 那天链路会断。
配置 `GH_PAT` 后，每次运行把新令牌自动写回 Secret，有效期滚动向前，
**彻底不需要人工续期，也彻底不依赖本机**。

若不配置 `GH_PAT`：约每 30 天需在本机重跑一次 `export_credentials.py` 续期。

### 为什么旅行域名和签到域名不同

这是最容易踩的坑，两者的差异是硬事实：

| 用途 | 域名 | 路径前缀 |
|---|---|---|
| 签到 / 状态 | `www.codebuddy.cn` | **带** `/v2` |
| 派猫猫旅行 | `www.workbuddy.cn` | **不带** `/v2` |

写成 `/v2/activity/growth/buddy/travel/status` 必然 404。详见证据文档第 3 节。

### cron 时间为何是 `11 1 * * *`

- GitHub 的 `schedule` 用 **UTC**，且**整点是高峰期**，排队延迟可能达数分钟。
- `11 1 * * *` = UTC 01:11 = **北京时间 09:11**，错开整点高峰。
- GitHub 承诺 cron 触发时间在整点后 15 分钟内，但不保证精确 —— 这是平台特性，无法消除。

### 安全边界

- 脚本**只读**登录态文件，绝不修改/删除。
- 写操作**仅限 3 个已验证接口**：签到、旅行领取、旅行派遣。绝不触碰兑换/抽奖。
- 领取是纯收益操作；派遣前**必查** `daily_limit_reached`，达上限绝不发写请求。
- 任何输出（stdout / 日志 / 飞书卡片）**均不含令牌明文**，仅脱敏指纹。
- `WB_REFRESH_TOKEN` 等 secret 通过 `env` 注入，不出现在命令行参数与日志中。
- `.gitignore` 已忽略 `.env`，避免本机调试凭据误提交。

### 本机调试（可选）

```bash
python scripts/export_credentials.py --out-env .env
set -a && . ./.env && set +a        # Git Bash
python scripts/buddy_station.py
```

`.env` 已被 `.gitignore` 忽略，且脚本会将其权限设为 600。

---

## 常见问题

| 现象 | 原因与处理 |
|---|---|
| 退出码 2「缺少凭据」 | Secret 未配置。在本机重跑 `export_credentials.py --repo <owner>/<repo>` |
| 退出码 1（签到失败） | 令牌可能过期。已配 `GH_PAT` 的会自动续期；未配则重新导出并覆盖 Secret |
| 定时任务没触发 | GitHub 仓库 60 天无活动会自动**禁用**定时任务，需手动去 Actions 页启用一次 |
| 猫猫区块显示「状态查询失败」 | 旅行域服务异常或令牌跨域失效；**不影响签到结论**，任务仍为绿色 |
| 飞书收不到卡片 | `FEISHU_WEBHOOK` 未配置；或机器人被移出群；或开了加签但未配 `FEISHU_SECRET` |
| 日历显示某天没签到，但卡片说签了 | 正常。客户端日历反映「本机活跃日」，卡片显示的是**后端签到记录**（以卡片为准） |
| 部署后想立刻验证 | 在 Actions 页点 **Run workflow** 手动触发 |

---

## 验收记录

**最近一次端到端运行**（2026-10-26 08:47，北京时间，run_id `40251041071`）：

- 令牌刷新 ✅
- 签到 ✅（当日已签，幂等短路）
- 猫猫闭环 ✅（识别达上限并跳过派遣，不发写请求）
- 飞书推送 ✅（`push.status: ok`）
- **令牌自动回写 ✅**（`WB_REFRESH_TOKEN` 的 updated_at 已从
  10-22 14:44 刷新到 10-26 08:47，有效期自动续期）
- 整体结论：成功，退出码 0 ✅

历史累计：

- 定时任务已稳定触发 **12+ 次** `event=schedule`，全部 success
- 每日首次运行完成真实签到（`ok`），后续窗口幂等跳过（`skipped`）
- 猫猫失败隔离 ✅（注入故障后 `overall_ok` 仍为 true，退出码仍为 0）
- 积分口径已修正为**账户总积分**（`get-user-resource-summary`），
  与客户端显示一致；卡片同时标注「本期活动累计」

完整脱敏返回见 [`docs/API-EVIDENCE.md`](docs/API-EVIDENCE.md)。

---

## 现在的运行形态

**完全脱离本机**：不需要开电脑、不需要开 WorkBuddy 客户端、不需要定期换令牌。

| 环节 | 由谁执行 |
|---|---|
| 定时触发 | GitHub 云端 runner（每天 4 个窗口） |
| 签到 + 派猫 | 云端 runner，纯标准库脚本 |
| 令牌续期 | 云端 runner 自动回写 Secret（`GH_PAT`） |
| 结果通知 | 飞书 Webhook 直连 |

唯一硬约束：**仓库连续 60 天无活动，GitHub 会自动禁用定时任务**（去 Actions 页点一次
启用即可）。日常使用碰不到。
