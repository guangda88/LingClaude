#!/usr/bin/env python3
"""apply_env_expand_patch.py — 给 lingcode/pkg/config/config.go 应用 ${ENV} 展开补丁

用法: python3 apply_env_expand_patch.py <lingcode_root>
安全: 幂等（已打补丁则跳过）；自动备份；纯文本插入，不依赖 go toolchain。
"""
import sys
from pathlib import Path

IMPORT_OLD = '''import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)'''

IMPORT_NEW = '''import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)'''

ANCHOR = '// applyEnvOverrides 应用环境变量覆盖'

FUNCS = '''// envPattern 匹配 ${ENV_VAR} 形式的环境变量引用
var envPattern = regexp.MustCompile(`\\$\\{([A-Za-z_][A-Za-z0-9_]*)\\}`)

// expandEnvVars 将字符串中的 ${ENV_VAR} 替换为环境变量值
// 未设置的变量替换为空字符串，避免把字面量当凭据使用
func expandEnvVars(s string) string {
	if !strings.Contains(s, "${") {
		return s
	}
	return envPattern.ReplaceAllStringFunc(s, func(m string) string {
		name := envPattern.FindStringSubmatch(m)[1]
		return os.Getenv(name)
	})
}

// expandConfigEnv 展开配置中所有支持环境变量的字段
func expandConfigEnv(config *Config) {
	config.Model.APIKey = expandEnvVars(config.Model.APIKey)
	config.Model.BaseURL = expandEnvVars(config.Model.BaseURL)
	for name, p := range config.Routing.Providers {
		p.APIKey = expandEnvVars(p.APIKey)
		p.BaseURL = expandEnvVars(p.BaseURL)
		config.Routing.Providers[name] = p
	}
	if config.Advisor.Model != "" {
		config.Advisor.Model = expandEnvVars(config.Advisor.Model)
	}
}

'''

LOAD_OLD = '''	// 应用环境变量覆盖
	applyEnvOverrides(config)

	return config, nil
}'''

LOAD_NEW = '''	// 应用环境变量覆盖
	applyEnvOverrides(config)

	// 展开 ${ENV_VAR} 引用（在环境变量覆盖之后，保留覆盖优先级）
	expandConfigEnv(config)

	return config, nil
}'''


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python3 apply_env_expand_patch.py <lingcode_root>", file=sys.stderr)
        return 2
    root = Path(sys.argv[1])
    path = root / "pkg/config/config.go"
    if not path.exists():
        print(f"[错误] 不存在: {path}", file=sys.stderr)
        return 2
    src = path.read_text()

    if "expandEnvVars" in src:
        print("[OK] config.go 已包含 ${ENV} 展开，跳过")
        return 0

    # 备份
    bak = path.with_suffix(path.suffix + f".bak.{__import__('time').strftime('%Y%m%d_%H%M%S')}")
    bak.write_text(src)
    print(f"[备份] {bak}")

    # 1) import
    if IMPORT_OLD in src:
        src = src.replace(IMPORT_OLD, IMPORT_NEW, 1)
        print("[1/3] import 已更新 (regexp/strings)")
    elif "regexp" in src and "strings" in src:
        print("[1/3] import 已包含所需包，跳过")
    else:
        print("[警告] import 块格式异常，跳过", file=sys.stderr)

    # 2) 插入函数
    if ANCHOR in src:
        src = src.replace(ANCHOR, FUNCS + ANCHOR, 1)
        print("[2/3] expandEnvVars/expandConfigEnv 已插入")
    else:
        print("[错误] 找不到 applyEnvOverrides 锚点", file=sys.stderr)
        return 1

    # 3) LoadConfig 调用
    if LOAD_OLD in src:
        src = src.replace(LOAD_OLD, LOAD_NEW, 1)
        print("[3/3] LoadConfig 已接入 expandConfigEnv")
    else:
        print("[警告] LoadConfig 锚点未命中，请手动接入 expandConfigEnv", file=sys.stderr)

    path.write_text(src)
    print("[OK] config.go 补丁已应用")
    return 0


if __name__ == "__main__":
    sys.exit(main())
