# crypto-move-watch

5 分钟级加密货币异动看门狗。**纯 Python 标准库，零第三方依赖，零 LLM 消耗。**

盘中真正异动才告警，没动静就闭嘴——不是那种每 5 分钟给你刷一条快照的噪音机器人。

## 它能解决什么

常规盯盘 cron 是 1~3 小时一轮，两个轮次之间的急拉急砸会**完全漏掉**。
这个脚本跑在 5 分钟粒度上，只在真异动时出声。

## 实测能力

| 特性 | 说明 |
|---|---|
| 双阈值 | 1h 异动 ≥2.0%、24h 异动 ≥6.0% |
| 细粒度分桶 | 0.5% 一桶，避免 2.4%/2.9% 落同一桶漏报 |
| 桶位过期 | 2 小时 TTL，防止「同幅度此生不再报」的永久静音 bug |
| 冷却 + 加速度突破 | 同方向 45 分钟冷却；幅度再扩 1.5pt 则强制突破冷却，防急跌全程静默 |
| 不装死 | 取数失败不再输出 `fire:false` 假装平静，写 health 文件 + 非零退出 |
| 量能/费率附注 | 附 1h 量能与资金费率，说明是「急动」还是「趋势」 |

## 用法

```bash
python3 crypto_move_watch.py            # 正常跑（写状态）
python3 crypto_move_watch.py --dry      # 试跑（不写状态）
python3 crypto_move_watch.py --notify   # 触发时推送
python3 crypto_move_watch.py --health   # 自检
```

挂 cron（每 5 分钟）：

```cron
*/5 * * * * cd /path/to/crypto-move-watch && timeout 90 python3 crypto_move_watch.py --notify >> watch.log 2>&1
```

## 数据源

Hyperliquid 公开行情 API（`https://api.hyperliquid.xyz/info`），无需 API key。
覆盖 10 个主流标的：BTC / ETH / SOL / BNB / XRP / ADA / DOGE / TRUMP / WLD / ASTER。

## 推送

脚本内置飞书通道（读 `~/.openclaw/openclaw.json` 的 `channels.feishu`）。
不用飞书也没关系：把 `send_feishu()` 换成你自己的 webhook / Telegram / Bark 即可，
触发时只传一段纯文本进去。

## 环境

- Python 3.8+（实测 3.10.12）
- 无需 pip install

## 许可

MIT
