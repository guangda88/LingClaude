"""项目级 sitecustomize：/dev/null 只读沙箱兼容（H18 环境修复项）。

用法：通过 PYTHONPATH 使解释器启动最早期加载本文件（早于 pytest/
subprocess/git 消费 os.devnull）：
    PYTHONPATH="$PWD:$PYTHONPATH" python -m pytest ...
或直接使用 scripts/sandbox_pytest.sh 包装脚本。

探测到 /dev/null 不可写（bwrap 只读挂载形态）时，重写 os.devnull
指向可写目录中的固定复用文件；宿主正常环境零行为（写探测成功即退出）。
"""
import os
import tempfile


def _lc_devnull_patch() -> None:
    try:
        # 真实写探测：权限位正常也可能因只读挂载被拒（本事故形态）
        with open(os.devnull, "ab"):
            pass
        return  # 正常环境，不接管
    except OSError:
        pass
    candidates = [
        p
        for p in (
            os.environ.get("LC_DEVNULL_DIR"),
            os.path.join(tempfile.gettempdir(), ".devnull-compat"),
            os.path.expanduser("~/.lingclaude/.devnull-compat"),
        )
        if p
    ]
    for path in candidates:
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
            os.close(fd)
            os.devnull = path  # type: ignore[misc]
            return
        except OSError:
            continue


_lc_devnull_patch()
