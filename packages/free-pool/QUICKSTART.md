# Quick Start

## 1. Install
```bash
pip install -e .
# or from source
```

## 2. Configure
```bash
cp .env.example .env
# edit FREE_POOL_ROUTES, ports, admin key as needed
cp routes.example.json routes.json
```

## 3. Sync free models
```bash
free-pool-sync
# or
python3 sync_free_models.py
```

## 4. Start bridges (optional but recommended for opencode/codearts)
```bash
# in separate terminals
python3 oc_free_bridge.py  # port 4100
python3 codearts_bridge.py # port 4110
```

## 5. Start gateway
```bash
free-pool-serve
# or
python3 gateway_main.py
```

## 6. Smoke test
```bash
curl http://127.0.0.1:8765/healthz
curl http://127.0.0.1:8765/v1/models
```
