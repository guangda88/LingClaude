# free-pool

可复用的免费模型池（Free Model Pool），提供 OpenAI 兼容的 `/v1/chat/completions` 和 `/v1/models` 接口。

## 特性
- 免费路由自动发现（`sync_free_models.py`，平台 `/v1/models`）
- 只增不删，失效路由标记 `default_off`（保守策略）
- 本地桥：OpenCode Zen (`oc_free_bridge.py`)、CodeArts (`codearts_bridge.py`）
- 健康分 + 桶调度（auto/code/fast/long/vision） + 限流
- OpenAI 兼容 API（/v1/chat/completions, /v1/models）

## 安装
```bash
pip install -e .
# or pip install free-pool
```

## 配置
```bash
cp .env.example .env
cp routes.example.json routes.json
# 校验路由
python3 validate_routes.py
```

## 使用
```bash
# 同步免费模型
free-pool-sync
# or python3 sync_free_models.py

# 启动网关
free-pool-serve
# or python3 gateway_main.py
```

## 本地桥（可选）
```bash
python3 oc_free_bridge.py      # 4100
python3 codearts_bridge.py     # 4110
```

## 冒烟测试
```bash
bash smoke_test.sh
```

## 结构
- `free_pool.py` / `gateway_main.py`：网关核心
- `sync_free_models.py`：路由同步（/v1/models 自动发现）
- `oc_free_bridge.py` / `codearts_bridge.py`：本地桥
- `validate_routes.py` + `routes.schema.json`：路由校验
- `cli.py`：命令行入口
