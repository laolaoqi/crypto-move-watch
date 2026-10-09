# crypto-move-watch

5 分钟级加密货币异动看门狗。**零依赖（纯标准库）**，直连 Hyperliquid 公开行情 API，只在真正异动时输出告警 —— 不烧 LLM token。

## 覆盖标的

BTC / ETH / SOL / BNB / XRP / ADA / DOGE / TRUMP / WLD / ASTER

## 快速开始

```bash
# 需要 Python 3.8+，无需安装任何第三方库
python3 crypto_move_watch.py --dry     # 试跑，不写状态
python3 crypto_move_watch.py           # 正常跑（写状态）
python3 crypto_move_watch.py --health  # 自检
```

## 输出

单行 JSON：

```json
{"fire": true, "state": {"buckets": {"WLD": {"b": "24hdn6.5"}}, "cool": {...}}}
```

- `fire: true` —— 有标的触发阈值（1h 异动 ≥2.0%，或 24h 异动 ≥6.0%）
- `fire: false` —— 本轮无异常

## 告警推送（可选）

默认**不推送**。如需推送到自建告警渠道，设置环境变量：

```bash
export CRYPTO_WATCH_CHAT_ID="你的目标会话ID"   # 留空则不推送
```

再带 `--notify` 运行即触发时推送。

## 阈值配置

改脚本顶部常量即可：

| 常量 | 默认 | 含义 |
|---|---|---|
| `H1_TH` | 2.0 | 1 小时异动阈值 % |
| `D24_TH` | 6.0 | 24 小时异动阈值 % |
| `COOLDOWN` | 45min | 同方向冷却 |
| `BUCKET` | 0.5 | 幅度分桶粒度 % |

## 挂 cron（每 5 分钟）

```cron
*/5 * * * * cd /path/to/crypto-move-watch && python3 crypto_move_watch.py >> /tmp/cmw.log 2>&1
```

## 依赖

**无。** 仅 Python 标准库（`json` / `os` / `sys` / `time` / `urllib.request`）。

## 许可

MIT
