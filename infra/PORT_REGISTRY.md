# 灵族端口注册表
# 规则: 一端口一主人。新增服务必须先在此注册端口。
# 更新时间: 2026-05-07

| 端口 | 持有服务 | 绑定地址 | 维护者 | 备注 |
|------|----------|----------|--------|------|
| 8765 | lingflow-plus.service | 0.0.0.0 | 灵通+ | 灵通+ WebUI/API 主入口。智桥 relay-server 原默认端口，智桥已迁移至 8767 |
| 8700 | lingmessage (HTTP API) | 127.0.0.1 | 灵信 | 灵信消息总线 HTTP 端点 |
| 8890 | cognitive-dashboard.service | 0.0.0.0 | 灵研 | 认知仪表盘 |
| 8100 | lingai-inference.service | 0.0.0.0 | 灵研 | LingAI-1.5B 本地推理 |
| 8501 | streamlit (手动) | 0.0.0.0 | — | 临时 streamlit 服务，无常驻服务 |
| 8767 | zhineng-bridge (relay-server) | 0.0.0.0 | 智桥 | 智桥从 8765 迁移至此，待智桥确认迁移完成 |
| 5436 | PostgreSQL | — | 灵知 | 灵知数据库（docker-compose） |
| 6381 | Redis | — | 灵知 | 灵知缓存（docker-compose） |
| 8902 | python3 (手动) | 0.0.0.0 | — | 待确认归属 |
| 8000 | python3 | 0.0.0.0 | — | 待确认归属 |
| 8001 | 灵知 API (FastAPI/uvicorn) | 0.0.0.0 | 灵知 | docker-compose 映射 8001→8000 |
| 8002 | python3 | 0.0.0.0 | — | 待确认归属 |
| 8008 | 灵知 Web (Nginx) | 0.0.0.0 | 灵知 | docker-compose 映射 8008→80 |
| 11434 | ollama | 127.0.0.1 | 灵研 | Ollama 推理服务 |
| 5901 | tigervnc-server.service | 0.0.0.0 | 系统 | VNC 远程桌面 |
| 7890 | clash | 127.0.0.1 | 系统 | 代理服务 |
| 8900 | llm-proxy.service | 127.0.0.1 | 灵克 | LLM API 代理服务 |
| 8950 | ling-worker (API) | 0.0.0.0 | 灵克 | ai01 分布式计算 Worker API（ai01:8950） |
| 8951 | ling-worker (metrics) | 0.0.0.0 | 灵克 | ai01 Worker metrics（预留） |

## 冲突记录

| 日期 | 端口 | 冲突方 | 结果 |
|------|------|--------|------|
| 2026-05-07 | 8765 | lingflow-webui vs lingflow-plus vs zhibridge | 智桥主动迁移至 8767，8765 归灵通+ |

## 新增端口流程

1. 在此表中添加记录
2. 在 service 文件注释中标注 `# Port: XXXX`
3. 运行 `ling-systemd-lint.py` 验证无冲突
4. 通过 LingBus 通知全族
