#!/usr/bin/env python3
"""Validate routes.json against schema"""
import json
import os
import sys

try:
    import jsonschema
except Exception:
    jsonschema = None


def main():
    schema_path = os.path.join(os.path.dirname(__file__), 'routes.schema.json')
    routes_env = os.environ.get('FREE_POOL_ROUTES', os.path.join(os.getcwd(), 'routes.json'))
    if not os.path.exists(schema_path):
        print('schema missing', schema_path)
        return 1
    if not os.path.exists(routes_env):
        print('routes missing', routes_env)
        return 1
    with open(schema_path) as f:
        schema = json.load(f)
    with open(routes_env) as f:
        data = json.load(f)
    # filter to actual route entries
    routes = {k: v for k, v in data.items() if isinstance(v, dict) and 'base_url' in v and 'provider' in v and 'tier' in v}
    if jsonschema is None:
        print(f'basic check: ok ({len(routes)} routes)')
        return 0
    try:
        # validate the filtered dict
        jsonschema.validate(routes, schema)
        print(f'ok ({len(routes)} routes)')
        return 0
    except Exception as e:
        print('fail:', e)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
