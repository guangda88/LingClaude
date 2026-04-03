# 贡献指南

感谢你对灵克的关注！本文档说明如何参与贡献。

## 快速开始

```bash
git clone https://github.com/your-org/LingClaude.git
cd LingClaude
pip install -e ".[dev]"
python3 -m pytest tests/ -v
```

## 开发原则

贡献前请阅读 [PRINCIPLES.md](PRINCIPLES.md)，特别是：

- **3 个硬依赖**：不轻易添加新依赖
- **测试先行**：新功能先写测试，再写实现
- **安全默认**：新增工具必须声明安全边界
- **提交规范**：`<type>: <中文 subject>`，详见 PRINCIPLES.md 第八条

## 代码风格

```
from __future__ import annotations    # 每个文件必须
pathlib.Path                           # 禁止 os.path
tuple[...]                             # 返回不可变集合
@dataclass(frozen=True)                # 值对象必须冻结
str, Enum                              # 枚举用此模式
全类型注解                              # 所有公开函数
```

命名：变量/函数 `snake_case`，类 `PascalCase`，常量 `UPPER_SNAKE`。

## 提交流程

1. Fork 仓库，从 `main` 创建分支
2. 开发 + 测试
3. 确保所有测试通过：`python3 -m pytest tests/ -v`
4. 提交，使用规范格式：

```
feat: 添加 XXX 功能
fix: 修复 YYY 在 ZZZ 情况下的崩溃
docs: 更新贡献指南
```

5. 创建 Pull Request

## PR 要求

- 所有测试通过
- 新功能有对应测试（无网络环境可跑）
- 公开 API 使用 `Result[T]` 返回
- 值对象使用 `@dataclass(frozen=True)`
- 如果添加了新工具，必须在 PR 描述中声明安全边界

## 报告 Bug

使用 [GitHub Issues](../../issues)，请包含：

- 复现步骤
- 期望行为 vs 实际行为
- Python 版本和操作系统
- 相关配置（脱敏后）

## 提出功能建议

同样使用 Issues，描述：

- 解决什么问题
- 你的使用场景
- 预期行为

## 项目结构

```
lingclaude/
├── core/           # 基础层：类型、配置、会话、权限、查询引擎
├── engine/         # 工具执行层：bash、文件操作、编码运行时
├── self_optimizer/ # 自优化框架：触发、评估、优化、学习
├── model/          # 模型层：ModelProvider 抽象、OpenAI、Anthropic
└── cli/            # 命令行界面
```

## 许可证

贡献的代码采用 MIT 许可证，与项目一致。
