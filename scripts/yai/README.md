# YAI 凭证冷备降级链（W6）

目标：YAI 凭证出问题时，**常态零人工、异常最小人工**——过期自动续签（既有三重自愈）、
烧毁可观测+自动降级+一键恢复、恢复后自动冷备。

## 链路全景

```
                ┌─ 正常过期 ──→ 三重自愈(既有, 不动):
                │   S2 2h cron(refresh_yai_jwt.sh) + proxy3 进程内 auth_refresh + yai-rescue.timer
                │   续签成功 → POST /admin/env 热更 + 原子持久化写 ~/.ling_keys.env
                │
YAI 401/失效 ──┤
                │                 ┌─ 凭据版本库(新): data/credentials_mirror/ling_keys_*.env ×12
                └─ rt 烧毁 ──→   │   scripts/yai/credentials_snapshot.sh   (W6-①)
                                  ├─ 业务侧(既有, 实测已通): proxy3 fallback 链
                                  │   route_selector.py:214 同模型跨 provider 兜底, yai 排链尾(:165)
                                  │   → 烧毁期请求不挂死, 自动落免费池/其他 provider
                                  ├─ 烧毁观测(新): scripts/yai/yai_burnout_monitor.py   (W6-②)
                                  │   语义: /tmp/proxy3_yai_auth_expired mtime <180s = 持续失败=烧毁进行中
                                  └─ 一键恢复(新): scripts/yai/yai_relogin_onelink.sh   (W6-③)
                                      人工只做: 9228 Chrome 里重登 ai.yitang.top → 跑它
                                      (收割→热更→冷备→终验 一条龙, 复用 fix_yai_ws403.sh)
```

冷启动恢复：宿主重启后 `start_proxy3.sh:3` 自动 `source ~/.ling_keys.env`——冷源在，
链路自愈。若冷源被坏写污染：从 `data/credentials_mirror/` 取上一份好快照覆盖回去。

## 宿主安装（一次性）

```bash
# 1) 快照 cron（热更落盘后 10 分钟同步镜像；内容不变自动跳过）
(crontab -l 2>/dev/null; echo '10 * * * * bash /home/ai/lingclaude/scripts/yai/credentials_snapshot.sh >> /tmp/yai_snapshot.log 2>&1') | crontab -

# 2) 烧毁告警 cron（每 5 分钟; BURNOUT 时进 syslog + wall 提醒）
(crontab -l 2>/dev/null; echo '*/5 * * * * python3 /home/ai/lingclaude/scripts/yai/yai_burnout_monitor.py >/dev/null 2>&1 || { logger -t yai-burnout "YAI BURNOUT: 需人工重登, 重登后跑 scripts/yai/yai_relogin_onelink.sh"; wall "⚠ YAI 凭证烧毁: 重登 ai.yitang.top 后跑 scripts/yai/yai_relogin_onelink.sh"; }') | crontab -
```

（W1 装配器 `scripts/install_lingk_systemd.sh` 已内置第 0 份快照——安装即有基线。）

## 故障处置速查

| 症状 | 判定 | 动作 |
|---|---|---|
| 请求偶尔慢, 无告警 | 正常过期续签 | 无（自愈中） |
| BURNOUT 告警 | rt 烧毁 | 9228 Chrome 重登 → `bash scripts/yai/yai_relogin_onelink.sh` |
| 烧毁反复发生 | 快照/收割环节坏写 | `ls -t data/credentials_mirror/` 找上一份好快照覆盖 `~/.ling_keys.env` |
| 宿主重启后 YAI 死 | 冷源缺失/污染 | 同上覆盖 → 重启 proxy3 → `service_health_snapshot.py` 验 |

## 边界（诚实声明）

- 冷备快照**未加密**（本机 root/ai 可读）。防“设备丢失泄凭证”需 Vault 集成，见外部方案 ac-B，未实施。
- 烧毁后的自动恢复上限=「重登一次」；全自动账密重登经四家评审一致放弃（风控对抗无底洞）。
