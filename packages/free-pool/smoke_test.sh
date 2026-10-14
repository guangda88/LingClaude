#!/bin/bash
set -e
cd "$(dirname "$0")"
python3 -c "
import free_pool, sync_free_models, route_selector, health, budget
print('imports ok')
"
python3 validate_routes.py
echo 'smoke ok'
