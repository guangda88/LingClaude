"""探测:运行时实际加载的黑名单匹配器是哪个版本"""
import sys
import inspect
import lingclaude.engine.bash as b

print("LOADED_FROM:", b.__file__)
src = inspect.getsource(b.BashExecutor._rule_matches)
print("HAS_BOUNDARY_FIX:", "(?<![\\w-])" in src or "lookbehind" in src.lower())
print("CAPTURE_SHOULD_PASS(Expect False):", b.BashExecutor._rule_matches("echo x --capture=no", "apt"))
print("QUOTED_APT_SHOULD_PASS(Expect False):", b.BashExecutor._rule_matches("grep -n APT x", "apt"))
print("SUBSTRING_WORD_SHOULD_PASS(Expect False):", b.BashExecutor._rule_matches("echo 1234", "su"))
