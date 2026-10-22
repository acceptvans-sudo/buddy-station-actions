# 接口实测证据（脱敏）

> 本文档所有内容均来自**真实请求**的脱敏返回，不采信任何网上流传的旧接口名。
> 采集时间：2026-10-22（北京时间），采集环境：Windows 本机已登录 WorkBuddy。
> 脱敏规则：令牌仅保留 `前6...后4`，`requestId` / `record_id` / `buddy_id` 保留原值以证明其为真实响应。

---

## 0. 结论速览

| 接口 | 方法 | 状态 |
|---|---|---|
| 令牌刷新 | POST | ✅ HTTP 200 / code 0 |
| 加油站状态查询 | POST | ✅ HTTP 200 / code 0 |
| 旅行状态 | GET | ✅ HTTP 200 / code 0 |
| 旅行地点配置 | GET | ✅ HTTP 200 / code 0 |
| 能量余额 | GET | ✅ HTTP 200 / code 0 |
| Buddy 配额 | GET | ✅ HTTP 200 / code 0 |
| 旅行领取（写） | POST | ✅ HTTP 200 / code 0（端到端运行中实测成功） |
| 旅行派出（写） | POST | ✅ HTTP 200 / code 0（端到端运行中实测成功） |

**关键差异（易错点）**：签到与旅行**不在同一域名**，且旅行路径**不带 `/v2` 前缀**。写错任一项均返回 404。

---

## 1. 令牌刷新（云端取凭据的入口）

```
POST https://copilot.tencent.com/v2/plugin/auth/token/refresh
Headers:
  X-Refresh-Token: <长效令牌>
  X-Auth-Refresh-Source: plugin
  X-Domain: copilot.tencent.com
```

实测返回（脱敏）：

```json
{
  "code": 0,
  "msg": "OK",
  "data": {
    "accessToken": "eyJhbG...NnSg",
    "refreshToken": "eyJhbG...Ch5A",
    "tokenType": "Bearer",
    "domain": "copilot.tencent.com",
    "scope": "...",
    "expiresIn": 2419200,
    "refreshExpiresIn": 2592000,
    "sessionState": "...",
    "notBeforePolicy": "..."
  }
}
```

**轮换行为实测**（决定了云端架构）：

```
初始 refresh_token：eyJhbG...4OTA (len=700)

[第1次刷新] HTTP 200 code=0 → 新令牌 eyJhbG...Ch5A，轮换=True
  refreshExpiresIn=2592000（≈30.0 天）
  accessToken expiresIn=2419200（≈28.0 天）

[用旧令牌再次刷新] HTTP 200 code=0 → 旧令牌仍可用：存在宽限期

[用新令牌刷新] HTTP 200 code=0 → 再次轮换=True
结论：轮换链可持续 = 是
```

> **架构含义**：长效令牌有效期约 30 天，且每次刷新都会轮换出新令牌。
> 旧令牌在宽限期内仍可用，因此**把长效令牌存进 Secret 即可长期运行**，
> 无需在每次运行后回写 Secret。约每 30 天在本机重新导出一次以续期。

**跨域可用性实测**（刷新出的 accessToken 可直接驱动两个域名的接口）：

```
刷新：HTTP 200 code=0  domain=copilot.tencent.com

签到状态查询 → HTTP 200 code=0 ✅（www.codebuddy.cn）
旅行状态     → HTTP 200 code=0 ✅（www.workbuddy.cn）

结论：刷新令牌可直接驱动业务接口 = 是
```

---

## 2. 加油站状态查询（签到前置判断）

```
POST https://www.codebuddy.cn/v2/billing/meter/checkin-activity-status
Authorization: Bearer <accessToken>
Content-Type: application/json
```

```json
{
  "code": 0,
  "msg": "OK",
  "requestId": "dd1b7ee9-fd98-47ee-9ab1-bd41214a6897",
  "data": {
    "active": true,
    "today_checked_in": true,
    "streak_days": 6,
    "daily_credit": 100,
    "today_credit": 100,
    "is_streak_day": false,
    "next_streak_day": 0,
    "streak_bonus_days": 0,
    "streak_bonus_credit": 0,
    "checkin_dates": ["2026-10-22", "2026-10-21", "2026-10-20",
                      "2026-10-16", "2026-10-15", "2026-10-14"],
    "week_checkin_days": 3,
    "week_progress": [false, true, true, true, false, false, false],
    "total_credits": 600,
    "start_time": "2026-09-30 00:00:00",
    "end_time": "2026-11-30 23:59:59",
    "theme_name": "Buddy加油站",
    "season": 10,
    "activity_name": "Buddy应用",
    "claim_button_text": "立即领取",
    "action_button": {
      "show": true,
      "text": "体验Buddy应用",
      "action": "workbuddy://home?openBuddySwitcher=true"
    }
  }
}
```

> `today_checked_in: true` 是本次运行「跳过写请求」的直接依据 —— 完成一次对话即记录当天，
> 此时再发签到写请求只会得到幂等拒绝。

---

## 3. 旅行状态（派猫决策依据）

```
GET https://www.workbuddy.cn/activity/growth/buddy/travel/status
```

```json
{
  "code": 0,
  "msg": "OK",
  "requestId": "d8d947a7-8a82-414d-8c98-1f987ebe7569",
  "data": {
    "state": "arrived",
    "buddy_id": 18006721,
    "record_id": 25434499,
    "location": { "id": 1, "code": "coffee", "name": "咖啡馆",
                  "duration_hours": 2, "cover_url": "" },
    "depart_at": 1792564369,
    "arrive_at": 1792571569,
    "server_now": 1792646938,
    "letter": { "id": 1, "text": "亲爱的铲屎官：…", "guide_text": "…" },
    "use_deeplink": "workbuddy://discover?cardId=interactive-annual-report",
    "daily_limit_reached": false,
    "duration_hours": 2,
    "reward_credit": 9
  }
}
```

> 本次运行前状态为 `arrived`（已到达待领取），`reward_credit: 9`，
> `daily_limit_reached: false` → 触发「先领后派」闭环。

**状态机**：`idle`（可派） / `traveling`（等待到达） / `arrived`（可领取）。

---

## 4. 旅行地点配置

```
GET https://www.workbuddy.cn/activity/growth/buddy/travel/config
```

```json
{
  "code": 0,
  "msg": "OK",
  "data": {
    "locations": [
      { "id": 1, "code": "coffee", "name": "咖啡馆",
        "duration_hours_min": 1, "duration_hours_max": 4,
        "reward_credit_min": 5, "reward_credit_max": 10, "sort": 1 },
      { "id": 2, "code": "mall", "name": "商场店铺", "...": "同上结构" },
      { "id": 3, "code": "gym", "name": "健身房", "...": "同上结构" },
      { "id": 4, "code": "ancient_town", "name": "古镇客栈", "...": "同上结构" }
    ]
  }
}
```

> 四个地点的时长区间（1–4 小时）与积分区间（5–10）**完全相同**，随机选择即可。

---

## 5. 能量与配额

```
GET https://www.workbuddy.cn/activity/growth/energy
```
```json
{ "code": 0, "msg": "OK",
  "data": { "balance": 3, "total_consumed": 60, "total_earned": 63 } }
```

```
GET https://www.workbuddy.cn/activity/growth/buddy/quota
```
```json
{ "code": 0, "msg": "OK",
  "data": { "affordable": 0, "balance": 3, "cost_per_open": 10, "max_open_count": 5 } }
```

> 能量/配额用于「开启 Buddy 盲盒」，**与旅行派遣无关**，派遣不消耗能量。

---

## 6. 写接口（端到端运行中实测成功）

本次真实运行中，两个写接口均返回 `code: 0`：

| 接口 | 方法 | 请求体 | 结果 |
|---|---|---|---|
| `/activity/growth/buddy/travel/claim` | POST | `{}` | ✅ code 0，领取 +9 积分 |
| `/activity/growth/buddy/travel/depart` | POST | `{"location_id": 3}` | ✅ code 0，派往健身房 |

签到写接口 `/v2/billing/meter/daily-checkin` 本次未发出 —— 因 `today_checked_in: true`
触发幂等短路，避免无谓写请求（符合幂等设计）。

---

## 7. 端到端运行结果（验收证据）

```
[token] refresh_token 刷新（eyJhbG...BMWQ (len=1318)）
[签到] skipped —— 今日已签到，无需重复签到
[旅行] ✅ 领取旅行积分 +9（到账前显示 9）
[旅行] ✅ 已派 Buddy 前往【健身房】
[旅行] 最终状态：旅行中 @ 健身房
[推送] skipped —— 未配置 FEISHU_WEBHOOK，跳过推送

整体结论：成功（签到=skipped，猫猫=成功）
```

结构化结果（`RESULT_JSON`，已脱敏）：

```json
{
  "started_at": "2026-10-22 13:36:18",
  "token_source": "refresh_token 刷新（eyJhbG...BMWQ (len=1318)）",
  "checkin": { "result": "skipped", "message": "今日已签到，无需重复签到",
               "credits": 600, "streak": 6 },
  "travel": {
    "available": true, "ok": true,
    "steps": ["✅ 领取旅行积分 +9（到账前显示 9）",
              "✅ 已派 Buddy 前往【健身房】"],
    "state": "traveling", "state_text": "旅行中",
    "location": "健身房", "remaining_text": "4 小时 0 分",
    "reward_credit": 5, "energy": 3, "claimed_credit": 9,
    "departed": true, "depart_location": "健身房",
    "letter": "亲爱的铲屎官：…——Buddy   来自 · 咖啡馆"
  },
  "overall_ok": true,
  "duration_sec": 3.2
}
```

**退出码：0（成功）**

### 失败隔离验证

将旅行域名指向不可达地址后重跑：

```json
{
  "checkin": { "result": "skipped", "message": "今日已签到，无需重复签到",
               "credits": 600, "streak": 6 },
  "travel": { "available": false, "ok": false,
              "steps": ["❌ 查询旅行状态失败，已跳过全部旅行写操作"],
              "error": "旅行状态接口不可用" },
  "overall_ok": true
}
```

→ 猫猫部分**完全失败**时，`overall_ok` 仍为 `true`，退出码仍为 0。
达成「猫猫部分失败不得影响签到结论」的要求。

### 凭据缺失验证

```
整体结论：失败（签到=failed）
message: 缺少凭据：请注入 WB_REFRESH_TOKEN（推荐）或 WB_ACCESS_TOKEN。…
退出码: 2
```

→ 配置问题与业务失败用不同退出码（2 vs 1）区分，便于告警定级。