# apply-security-patch — Verify

## E2E 验证

- [ ] Patch 应用前 AST 通过
- [ ] Patch 应用后 AST 通过
- [ ] 目标文件原有测试（如有）通过
- [ ] `git log` 显示 commit 含 trace ref
- [ ] LACP trace 写出成功
- [ ] commit message 含 {files, why, refs}

## 单元测试

- `tests/test_apply_security_patch.py` — 5+ tests
  - 正常 patch 流程
  - AST fail 路径
  - 测试 fail 路径
  - hook 拦截路径
  - 重复执行幂等

## 回归检查

- [ ] 不破坏目标文件其他功能
- [ ] 不引入新 silent except
- [ ] 不绕开 LACP WSB（write scope 限制）
- [ ] 审计 hook (pre-commit) 通过

## 失败回滚

如果 commit 后发现 patch 有问题：
1. `git revert <commit-hash>`
2. emit trace(phase=VERIFY, outcome=FAIL, decision_id=<原trace>)
3. 写复盘到 `.audit/security_patches.md`