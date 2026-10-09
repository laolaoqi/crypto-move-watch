#!/usr/bin/env python3
"""币价异动看门狗 v2 —— 5分钟级、只在真正异动时告警(零 LLM 消耗)。

背景: "10币信号双通道" cron 3 小时一轮, 9/11 20:00-22:00 急涨卡在两轮之间 → 空窗无告警。

v2 按三方评审(交易/可靠性/红队)修复:
  [静默失败] 取数失败不再输出 fire:false 装"平静" → 写 health 文件 + 非零退出
  [追高滞后] 基准改用「当前价 vs 最近完成K线开盘价」并附 1h 量能与资金费率,
             让告警说明"急动 vs 趋势", 而非单纯事后播报
  [桶位截断] 2.4%/2.9% 曾落同桶漏报 → 改 0.5% 细粒度桶
  [重复刷屏] 回落不再清桶 → 同方向 45min 冷却, 冷却内不重复报
  [永久静音] 桶位仅存本次不记时间 → 命中过 2.0% 后同幅度此生不再报,
             急跌只收一条后面全程静默 → 桶位加 2h 过期 + 幅度再扩 1.5pt 强制突破冷却
  [漏币]     单币超时不再静默跳过, 计入 health

用法:
  python3 crypto_move_watch.py            # 正常(写状态)
  python3 crypto_move_watch.py --dry      # 试跑(不写状态)
  python3 crypto_move_watch.py --notify   # 触发时飞书直发
  python3 crypto_move_watch.py --health   # 自检
"""
import json, os, sys, time, urllib.request
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(BASE, "trading", "state")
STATE_F = os.path.join(STATE_DIR, "move_watch_state.json")
PENDING_F = os.path.join(STATE_DIR, "move_watch_pending.json")
HEALTH_F = os.path.join(STATE_DIR, "move_watch_health.json")
COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "TRUMP", "WLD", "ASTER"]
# 2026-09-21 修复: 协议基准/下限 funding, 年化恒为≈10.95%, 曾被整数渲染成"+11%"伪装成行情
FUND_FLOOR = 0.0000125
TZ = timezone(timedelta(hours=8))
H1_TH = 2.0          # 1h 异动阈值 %
D24_TH = 6.0         # 24h 异动阈值 %
COOLDOWN = 45 * 60   # 同方向冷却(秒), 防重复刷屏
BUCKET = 0.5         # 桶粒度 %, 防 2.4/2.9 落同桶
BUCKET_TTL = 2 * 3600  # 桶位寿命(秒): 过期即允许同桶重报, 防「永久静音」
ACCEL_STEP = 1.5     # 幅度较上次告警再扩大该值(百分点) → 突破冷却, 防急跌全程静默
RETRY_GAP = 30 * 60  # 投递失败后, 同一积压消息的重试间隔(秒)
MAX_TRIES = 8        # 积压消息最大重试次数, 超出放弃并保持 send_ok=false 让看门狗报警
DEADLINE = 70.0      # 单轮总预算(秒): meta 8s + 10币×4s = 48s, 留足余量防末位币被系统性截断(cron timeout 90s)
API = "https://api.hyperliquid.xyz/info"
# 推送目标: 默认关闭(开源产品不带私有 chat_id)。需推送时用环境变量显式指定。
#   export CRYPTO_WATCH_CHAT_ID="oc_xxx"
ALERT_CHAT_ID = os.environ.get("CRYPTO_WATCH_CHAT_ID", "").strip()
CFG = os.path.expanduser("~/.openclaw/openclaw.json")


def post(payload, timeout=6):
    req = urllib.request.Request(
        API, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def now():
    return datetime.now(TZ)


def write_health(ok, detail, send_ok=None):
    """ok = 取数数据面健康; send_ok = 投递面(独立字段, 不覆盖数据面)。"""
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        h = {}
        try:
            h = json.load(open(HEALTH_F))
        except Exception:
            pass
        h.update({"ok": ok, "detail": detail[:200],
                  "ts": now().strftime("%F %T"), "epoch": int(time.time())})
        if send_ok is not None:
            h["send_ok"] = send_ok
            h["send_ts"] = h["ts"]
        tmp = HEALTH_F + ".tmp"
        json.dump(h, open(tmp, "w"), ensure_ascii=False)
        os.replace(tmp, HEALTH_F)
    except Exception:
        pass


def send_feishu(text):
    try:
        cfg = json.load(open(CFG))
        acct = (cfg.get("channels", {}).get("feishu", {})
                .get("accounts", {}).get("yiheyuan", {}))
        aid, sec = acct.get("appId", ""), acct.get("appSecret", "")
        if not aid or not sec:
            return "no-cred"
        body = json.dumps({"app_id": aid, "app_secret": sec}).encode()
        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            tok = json.loads(r.read()).get("tenant_access_token", "")
        if not tok:
            return "no-token"
        msg = json.dumps({"receive_id": ALERT_CHAT_ID, "msg_type": "text",
                          "content": json.dumps({"text": text}, ensure_ascii=False)},
                         ensure_ascii=False).encode()
        req2 = urllib.request.Request(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            data=msg, headers={"Content-Type": "application/json",
                               "Authorization": "Bearer " + tok})
        with urllib.request.urlopen(req2, timeout=8) as r2:
            return "sent" if json.loads(r2.read()).get("code") == 0 else "fail"
    except Exception as e:
        return "err:" + str(e)[:50]


def main():
    args = sys.argv[1:]
    dry = "--dry" in args
    do_notify = "--notify" in args
    t0 = time.time()

    px, d24, d1, vol, fund, fund_raw = {}, {}, {}, {}, {}, {}
    try:
        d = post({"type": "metaAndAssetCtxs"}, timeout=8)
        names = [u["name"] for u in d[0]["universe"]]
        arr = d[1]
        for i, n in enumerate(names):
            if n in COINS and i < len(arr):
                c = arr[i]
                m = float(c.get("markPx") or 0)
                p = float(c.get("prevDayPx") or 0)
                px[n] = m
                if p:
                    d24[n] = (m - p) / p * 100.0
                # 费率: 字段缺失/非数值时置 None(播报为 N/A), 绝不静默补0冒充实测值
                fv = c.get("funding")
                if fv is None or fv == "":
                    fund[n] = None
                else:
                    try:
                        _fr = float(fv)
                        fund_raw[n] = _fr
                        fund[n] = _fr * 100 * 24 * 365  # 年化%
                    except (TypeError, ValueError):
                        fund[n] = None
    except Exception as e:
        write_health(False, "metaAndAssetCtxs: " + str(e)[:80])
        print(json.dumps({"health": "fail", "err": str(e)[:80]}, ensure_ascii=False))
        sys.exit(2)

    nowms = int(t0 * 1000)
    skipped = []
    for n in COINS:
        if time.time() - t0 > DEADLINE:
            skipped.append(n)
            continue
        try:
            cs = post({"type": "candleSnapshot", "req": {
                "coin": n, "interval": "1h",
                "startTime": nowms - 4 * 3600 * 1000, "endTime": nowms}}, timeout=4)
            if cs:
                # 评审修正: cs[-1] 是「正在形成」的K线(整点基准归零→系统性漏报窗),
                # 只取已收盘K线 (t + 1h <= now) 作基准
                closed = [k for k in cs if int(k["t"]) + 3600 * 1000 <= nowms]
                base = closed[-1] if closed else None
                if base is not None:
                    o = float(base["o"])
                    cur = px.get(n, float(base["c"]))
                    if o:
                        d1[n] = (cur - o) / o * 100.0   # 当前价 vs 最近已收盘K线开盘
                    try:
                        vol[n] = float(base["v"])
                    except Exception:
                        pass
        except Exception:
            skipped.append(n)

    if not px:
        write_health(False, "no price")
        sys.exit(2)
    # 数据面健康单独计算, 留到最后与投递面 send_ok 一起写(避免二次写覆盖)
    data_ok = not skipped
    data_detail = ("skipped=%s" % ",".join(skipped)) if skipped else ("n=%d skipped=-" % len(px))

    st = {"buckets": {}, "cool": {}}
    try:
        st = json.load(open(STATE_F))
    except Exception:
        pass
    old_b = st.get("buckets", {})
    old_c = st.get("cool", {})
    new_b, new_c = dict(old_b), dict(old_c)
    hits = []
    tnow = time.time()

    for n in COINS:
        v1, v24 = d1.get(n), d24.get(n)
        why = dirn = None
        if v1 is not None and abs(v1) >= H1_TH:
            dirn, why = ("up" if v1 > 0 else "dn"), "1小时 %+.2f%%" % v1
            b = "1h%s%.1f" % (dirn, round(abs(v1) / BUCKET) * BUCKET)
        elif v24 is not None and abs(v24) >= D24_TH:
            dirn, why = ("up" if v24 > 0 else "dn"), "24小时 %+.2f%%" % v24
            b = "24h%s%.1f" % (dirn, round(abs(v24) / BUCKET) * BUCKET)
        if dirn is None:
            continue
        ck = "%s:%s" % (n, dirn)
        mag = abs(v1) if (v1 is not None and abs(v1) >= H1_TH) else abs(v24 or 0)
        # 上轮桶位(兼容旧字符串格式): 桶相同且未过期 → 视为已报过, 压制
        prev = old_b.get(n)
        if isinstance(prev, dict):
            prev_key, prev_ts = prev.get("b"), float(prev.get("ts", 0))
        else:
            prev_key, prev_ts = prev, 0.0
        same_bucket = (prev_key == b) and (tnow - prev_ts < BUCKET_TTL)
        # 冷却: 同币同方向 COOLDOWN 内不重复报(回落也不清桶, 防重复刷屏)
        cool_ok = tnow - float(old_c.get(ck, 0)) >= COOLDOWN
        # 加速突破: 幅度较上次告警再扩大 ACCEL_STEP → 急跌/急涨不再被冷却吞掉
        bypass = mag >= float(old_c.get(ck + "|mag", 0)) + ACCEL_STEP
        if same_bucket and not bypass:
            continue
        if not cool_ok and not bypass:
            continue
        new_b[n] = {"b": b, "ts": tnow}
        new_c[ck] = tnow
        new_c[ck + "|mag"] = mag
        hits.append((n, why, px.get(n), v1, v24, vol.get(n), fund.get(n)))

    state_out = {"buckets": new_b, "cool": new_c, "ts": now().strftime("%F %T")}

    def build_msg(hs):
        t = now()
        # 2026-09-28 立齐指令/v1.2: 含价格一律表格
        out = ["⚠️ 币价异动 · %s" % t.strftime("%H:%M"), ""]
        out.append("| 标的 | 现价 | 异动 | 1h量 | 费率年化 |")
        out.append("|---|---|---|---|---|")
        _floor_hit = False
        for n, why, p, v1, v24, v, f in hs:
            vol = ("%.0f" % v) if v else "—"
            if f is not None:
                _raw = fund_raw.get(n)
                if _raw is not None and abs(_raw - FUND_FLOOR) < 1e-12:
                    ftxt = "%+.2f%%（协议基准下限）" % f
                    _floor_hit = True
                else:
                    ftxt = "%+.2f%%" % f
            else:
                ftxt = "N/A(取数失败)"
            out.append("| %s | $%s | %s | %s | %s |" % (n, p, why, vol, ftxt))
        out.append("")
        out.append("🟡 观察 —— 事实播报，非交易提示")
        out.append("缺：入场/止损/止盈/杠杆/仓位/赔率RR（故不可执行）")
        out.append("数据源：Hyperliquid metaAndAssetCtxs ｜ 数据时刻：%s" % t.strftime("%F %H:%M"))
        if _floor_hit:
            out.append("备注：标「协议基准下限」的币 funding=1.25e-5/h（年化≈10.95%），是协议基准值，非市场拥挤信号。")
        out.append("说明：异动为已发生事实，方向不可由本播报推知。")
        return "\n".join(out)

    pend = {}
    try:
        pend = json.load(open(PENDING_F))
    except Exception:
        pass
    # 本轮新命中 → 按原文入待发队列(发送成功才清空), 避免「失败即整轮丢失」
    if hits:
        pend["msg"] = build_msg(hits)
        pend["queued_at"] = tnow
        if not pend.get("tries"):
            pend["tries"] = 0

    out = {"fire": bool(hits), "state": state_out}
    send_ok = None
    if do_notify and not dry and pend.get("msg"):
        tries = int(pend.get("tries", 0))
        last = float(pend.get("last_try", 0))
        if tries >= MAX_TRIES:
            # 400 等永久性错误: 不无限原样重试; 保持 send_ok=false 让看门狗持续告警
            send_ok = False
            out["notify"] = "give-up(%d)" % tries
            print("[FAIL] 积压消息重试 %d 次仍失败, 停止重试(告警保持)" % tries, flush=True)
        elif tries > 0 and (tnow - last) < RETRY_GAP:
            send_ok = None   # 限频: 未到重试间隔, 本轮静默等待
            out["notify"] = "retry-wait(%ds)" % int(RETRY_GAP - (tnow - last))
        else:
            r = send_feishu(pend["msg"])
            out["notify"] = r
            pend["tries"] = tries + 1
            pend["last_try"] = tnow
            send_ok = (r == "sent")
            if send_ok:
                pend["msg"] = ""
                pend["tries"] = 0
            else:
                print("[FAIL] 发送未成功(%s), 待发队列保留(第%d次)" % (r, pend["tries"]), flush=True)

    # 去重状态无条件落盘: 本轮异动已安全进待发队列, 不再靠「不落盘」来触发重报(那是九轮刷屏根因)
    if not dry:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = STATE_F + ".tmp"
        json.dump(state_out, open(tmp, "w"), ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_F)
        tmp2 = PENDING_F + ".tmp"
        json.dump(pend, open(tmp2, "w"), ensure_ascii=False, indent=2)
        os.replace(tmp2, PENDING_F)

    # 可选闸门: 若环境里存在 validate_alert 则做发布前合规校验, 否则跳过。
    # 开源产品不内置该私有校验器 —— 缺失必须静默降级, 不得阻断正常告警(DEF: 打包漏依赖)。
    if pend.get("msg"):
        try:
            from validate_alert import validate as _va
            _r = _va({"kind": "fact", "data_time": now().strftime("%Y-%m-%dT%H:%M:%S+08:00"),
                      "state": "🟡 观察", "text": pend["msg"]})
            if not _r["ok"]:
                print("[FAIL] 铁律校验未过: %s" % _r["fails"], flush=True)
                data_ok = False
                data_detail = "ironrule: " + ";".join(_r["fails"])[:60]
        except ImportError:
            pass  # 无私有校验器 → 正常放行
        except Exception as _e:
            print("[warn] 铁律校验器异常(已降级放行): %s" % _e, flush=True)

    # 数据面 ok 与投递面 send_ok 分开写, 互不覆盖
    write_health(data_ok, data_detail, send_ok=send_ok)
    print(json.dumps(out, ensure_ascii=False))
    if send_ok is False:
        sys.exit(3)


if __name__ == "__main__":
    main()
