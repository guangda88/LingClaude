"""装载+能用验证：19 个 McpAgentPluginBase 载体插片真实挂载 SeamRegistry。

用法：python scripts/verify_family_carriers.py
退出码：0=全绿；1=存在失败。
"""
import importlib
import sys

MODS = [
    'agent_lingan', 'agent_lingcreate', 'agent_lingflow', 'agent_lingmessage',
    'agent_lingminopt', 'agent_lingresearch', 'agent_lingtongask', 'agent_lingweb',
    'agent_lingyang', 'agent_lingzhi', 'agent_zhibridge',
    'proj_lingchu', 'proj_lingdai', 'proj_lingkang', 'proj_linglv',
    'proj_lingshang', 'proj_lingsheng', 'proj_lingshi', 'proj_lingyi',
    'proj_agent_gateway',  # 普通类载体（name=类属性），一并过 N3+注册链
]


def main() -> int:
    from lingclaude.core.seam import SeamRegistry, SeamType
    ok, fail = 0, 0
    loaded = []
    for m in MODS:
        try:
            mod = importlib.import_module(f'lingclaude.plugins.agents.{m}.plugin')
            mod.register(SeamRegistry)
            ok += 1
            loaded.append(m)
        except Exception as e:  # noqa: BLE001 —— 验证脚本要看到每个失败
            fail += 1
            print(f'FAIL {m}: {type(e).__name__}: {str(e)[:160]}')
    snap = SeamRegistry.snapshot()
    agent_keys = sorted(snap.get('agent', snap.get(SeamType.AGENT.name, [])))
    print(f'装载结果 ok={ok} fail={fail}')
    print(f'AGENT 缝注册 {len(agent_keys)}:', agent_keys)
    return 0 if fail == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
