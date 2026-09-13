#!/usr/bin/env python3
"""分析 journal 中被拦截的命令，细分类：测试构造 vs 真实误杀。"""
import json, glob, re
from collections import Counter

# 全量扫描：提取所有 blocked_cmd 的完整命令 + 归类
blocked_full = []
for fp in glob.glob('/home/ai/lingclaude/.lingclaude/journals/*.jsonl'):
    try:
        with open(fp) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    if r.get('type') != 'tool_result':
                        continue
                    preview = r.get('output_preview', '') or ''
                    if '命令被阻止' in preview:
                        m = re.search(r'命令被阻止: (.+)', preview)
                        cmd = m.group(1) if m else preview
                        blocked_full.append((fp.split('/')[-1], cmd[:400]))
                except Exception:
                    pass
    except Exception:
        pass

print('total blocked:', len(blocked_full))

cat = Counter()
samples = {}
for fn, cmd in blocked_full:
    # 测试构造特征
    if 'BashExecutor()' in cmd and ('DANGER' in cmd or 'for cmd in' in cmd or 'cases' in cmd or '_ALWAYS_BLOCKED' in cmd):
        c = 'test_construct_blacklist'
    elif 'redact' in cmd or 'contains_sensitive' in cmd:
        c = 'test_construct_redact'
    elif '_probe' in cmd or 'sandbox' in cmd.lower() or 'bwrap' in cmd.lower():
        c = 'test_construct_sandbox_probe'
    elif 'grep -rn "s' + 'k-' in cmd or 'grep -R "api_key' in cmd:
        c = 'credential_search_grep'
    elif 'git commit' in cmd or 'COMMIT_MSG' in cmd or 'PYEOF' in cmd:
        c = 'git_commit_msg'
    elif 'ulimit' in cmd or 'sudo -n' in cmd:
        c = 'env_probe'
    elif 'lsattr' in cmd or 'touch ~/.ling' in cmd:
        c = 'env_probe_mount'
    elif 'cat >' in cmd or '>> ' in cmd or '> .env' in cmd:
        c = 'write_file_cmd'
    elif 'audit_check' in cmd:
        c = 'audit_check'
    elif 'df -h' in cmd or 'mount | grep' in cmd or 'ls -ld' in cmd:
        c = 'disk_probe'
    elif 'sed -n' in cmd or 'config.yaml' in cmd:
        c = 'config_read'
    elif 'cat tmp' in cmd or 'head -c' in cmd:
        c = 'tmp_read'
    elif 'pytest' in cmd or 'python -m pytest' in cmd:
        c = 'pytest_run'
    elif 'secret_scan' in cmd or 'secret-scan' in cmd:
        c = 'secret_scan_run'
    else:
        c = 'other_real'
    cat[c] += 1
    samples.setdefault(c, []).append((fn, cmd[:220]))

for k, v in cat.most_common():
    print(f'\n=== {k}: {v} ===')
    for fn, cmd in samples[k][:6]:
        print(f'  [{fn}] {cmd}')
