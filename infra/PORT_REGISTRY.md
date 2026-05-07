# 灵族端口注册表
# 规则: 一端口一主人。新增服务必须先在此注册端口。
# 更新时间: 2026-05-07

| 端口 | 持有服务 | 绑定地址 | 维护者 | 备注 |
|------|----------|----------|--------|------|
| 8765 | zhineng-bridge (relay-server) | 0.0.0.0 | 智桥 | 智桥 relay-server 默认端口。lingflow-plus 曾实际占用，智桥声明归属。见冲突记录 |
| 8700 | LingMessage (HTTP API) | 127.0.0.1 | 灵信 | 灵信消息总线 HTTP 端点 |
| 8890 | cognitive-dashboard.service | 0.0.0.0 | 灵研 | 认知仪表盘 |
| 8100 | lingai-inference.service | 0.0.0.0 | 灵研 | LingAI-1.5B 本地推理 |
| 8501 | streamlit (手动) | 0.0.0.0 | — | 临时 streamlit 服务，无常驻服务 |
| 8902 | python3 (手动) | 0.0.0.0 | — | 待确认归属 |
| 8000 | python3 | 0.0.0.0 | — | 待确认归属 |
| 8001 | — | 0.0.0.0 | — | 待确认归属 |
| 8002 | python3 | 0.0.0.0 | — | 待确认归属 |
| 8008 | — | 0.0.0.0 | — | 待确认归属 |
| 11434 | ollama | 127.0.0.1 | 灵研 | Ollama 推理服务 |
| 5901 | tigervnc-server.service | 0.0.0.0 | 系统 | VNC 远程桌面 |
| 7890 | clash | 127.0.0.1 | 系统 | 代理服务 |

## 冲突记录

| 日期 | 端口 | 冲突方 | 结果 |
|------|------|--------|------|
| 2026-05-07 | 8765 | lingflow-webui vs lingflow-plus vs zhibridge | zhibridge (relay-server) 声明归属，灵克更新注册表 |

## 新增端口流程

1. 在此表中添加记录
2. 在 service 文件注释中标注 `# Port: XXXX`
3. 运行 `ling-systemd-lint.py` 验证无冲突
4. 通过 LingBus 通知全族
