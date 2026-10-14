#!/usr/bin/env bash
# ai01_cpu_probe.sh — CPU 特性探测 + 微基准 一键脚本 (2026-10-13, 灵克)
# 用法:  bash ai01_cpu_probe.sh                 # 在 ai01 上直接跑
#        REPORT_DIR=/tmp bash ai01_cpu_probe.sh # 指定报告目录
# 特性:  无 root 也可跑; 缺 openssl/stress-ng 自动降级; 全程只读+微负载(≤20s)
# 目标机: ai01 (用户声明 CPU=i7-10700, Comet Lake 10代)。注: 外部规格页当
#         日全部不可达(ARK/TechPowerUp 404, wiki DNS污染, cpu-monkey 403),
#         "预期值"为训练数据记忆·未验证, 一切以本脚本实测输出为准。
set -u
OUT="${REPORT_DIR:-.}/cpu_report_$(hostname)_$(date +%Y%m%d_%H%M%S).txt"
exec > >(tee "$OUT") 2>&1

say() { echo; echo "===== $* ====="; }

say "0. 身份锚 (防连错机器)"
hostname; date '+%F %T %z'; uname -r; uptime

say "1. CPU 身份与拓扑"
grep -m1 "model name" /proc/cpuinfo
lscpu 2>/dev/null | grep -E "Model name|^CPU\(s\)|Thread|Core|Socket|NUMA node|Vendor|CPU family|^Model:|CPU MHz|CPU max|CPU min|Virtualization|L1d|L1i|L2|L3"
grep -m1 microcode /proc/cpuinfo || echo "microcode: (cpuinfo 未暴露)"
echo "物理核簇数(去重 socket+core): $(lscpu -p=Core,Socket 2>/dev/null | grep -v '^#' | sort -u | wc -l)"
grep -m1 "model name" /proc/cpuinfo | grep -q "10700" \
  && echo "  型号含 10700: 与用户声明一致" \
  || echo "  ⚠ 型号不含 10700, 实际: $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2)"

say "2. 关键特性旗标解析"
FLAGS=$(grep -m1 '^flags' /proc/cpuinfo)
chk() { if echo "$FLAGS" | grep -qw "$1"; then echo "  [Y] $2 ($1)"; else echo "  [.] $2 ($1)"; fi; }
echo "-- 向量/矩阵 (AI/科学计算相关) --"
chk avx512f      "AVX-512 基础"
chk avx512_bf16  "AVX512-BF16  (BF16 低精度训练/推理)"
chk avx512_vnni  "AVX512-VNNI  (INT8 推理加速)"
chk avx_vnni     "AVX-VNNI     (VNNI 的 256bit 版)"
chk amx_tile     "AMX 矩阵扩展 (Tile)"
chk amx_int8     "AMX-INT8"
chk amx_bf16     "AMX-BF16"
echo "-- 常用硬件加速 --"
chk aes   "AES-NI  (磁盘/网络加密)"
chk sha_ni "SHA-NI (git/tls 校验加速)"
chk vaes  "VAES    (向量化 AES)"
chk gfni  "GFNI    (伽罗瓦域)"
echo "-- 较新指令 (可判断 CPU 代际新旧) --"
chk serialize "SERIALIZE"
chk umonitor  "WAITPKG/umwait (低功耗自旋等待)"
chk movdiri   "MOVDIRI"
chk enqcmd    "ENQCMD"
chk clwb      "CLWB (持久内存写回)"
echo "-- 安全/虚拟化/资源管控 --"
chk pku   "PKU   (内存保护键)"
chk ibt   "CET-IBT (间接分支跟踪)"
chk shstk "CET-SHSTK (影子栈)"
chk sgx   "SGX"
chk tme   "TME   (总内存加密)"
chk vmx   "VMX   (可做 KVM 宿主)"
chk svm   "SVM   (AMD 可做 KVM 宿主)"
chk rdt_a "RDT-A (L3 缓存分配 CAT)"
chk mba   "MBA   (内存带宽分配)"
chk smep  "SMEP"
echo "-- AMD 内存加密 (SME/SEV, 无输出即 Intel 或不可见) --"
grep -i 'sme\|sev' /proc/cpuinfo 2>/dev/null | head -2
sudo -n dmesg 2>/dev/null | grep -im1 'AMD Memory Encryption\|SEV' || echo "  (无 sudo 或非 AMD)"

say "1.5 预期对照 (i7-10700, 记忆值·未验证 — DIFF≠故障, 以实测列为准)"
EXP_CORES=8; EXP_THREADS=16
AC=$(lscpu -p=Core,Socket 2>/dev/null | grep -v '^#' | sort -u | wc -l)
AT=$(nproc 2>/dev/null || echo '?')
if [ "${AC:-0}" = "$EXP_CORES" ] && [ "${AT:-0}" = "$EXP_THREADS" ]; then J=OK; else J=DIFF; fi
echo "  核心/线程   预期=${EXP_CORES}C/${EXP_THREADS}T  实测=${AC:-?}C/${AT}T  $J"
expf() { if echo "$FLAGS" | grep -qw "$1"; then got=Y; else got=N; fi
         [ "$got" = "$2" ] && r=OK || r=DIFF
         printf "  %-12s 预期=%s 实测=%s %s\n" "$3" "$2" "$got" "$r"; }
expf avx512f     Y "AVX-512"
expf avx512_vnni Y "AVX512-VNNI(INT8)"
expf avx512_bf16 N "AVX512-BF16"
expf avx_vnni    N "AVX-VNNI(256bit)"
expf amx_tile    N "AMX"
expf aes         Y "AES-NI"
expf sha_ni      N "SHA-NI"
L3=$(lscpu 2>/dev/null | grep -E '^L3' | awk '{print $3}')
MAXG=$(lscpu 2>/dev/null | grep -iE 'cpu (max|最大)' | grep -oE '[0-9]+\.[0-9]+' | head -1)
echo "  L3 Cache    预期=16MiB(记忆)    实测=${L3:-?} MiB"
echo "  睿频上限    预期=4800MHz(记忆)  实测=${MAXG:-?} MHz"
if   echo "$FLAGS" | grep -qw avx512bw && echo "$FLAGS" | grep -qw avx512cd \
  && echo "$FLAGS" | grep -qw avx512dq && echo "$FLAGS" | grep -qw avx512vl; then V=v3
elif echo "$FLAGS" | grep -qw avx2; then V=v2; else V=v1; fi
echo "  x86-64-v 级别(旗标推定): $V (Comet Lake 预期 v3·记忆)"

say "3. 频率与功耗治理 (影响基准解读)"
for f in scaling_governor scaling_driver; do
  v=$(cat /sys/devices/system/cpu/cpu0/cpufreq/$f 2>/dev/null) && echo "  $f = $v"
done
v=$(cat /sys/devices/system/cpu/intel_pstate/no_turbo 2>/dev/null) && echo "  intel_pstate.no_turbo = $v (1=睿频被禁)"
v=$(cat /sys/devices/system/cpu/amd_pstate/status 2>/dev/null) && echo "  amd_pstate.status = $v"
lscpu 2>/dev/null | grep -E "CPU max|CPU min"
echo "  当前各核 MHz(采样1次): $(grep '^cpu MHz' /proc/cpuinfo | awk '{printf "%s ", int($4)}')"
command -v turbostat >/dev/null && echo "  turbostat 可用(可深测 C-state/实际频率)" || echo "  turbostat 不可用(apt install linux-tools-common 后可深测)"

say "4. 微基准 (单核吞吐 + 多核扩展)"
echo "-- 4.1 openssl 单核 (各 3 秒) --"
if command -v openssl >/dev/null; then
  openssl version
  openssl speed -elapsed -seconds 3 aes-256-gcm 2>/dev/null | tail -2
  openssl speed -elapsed -seconds 3 sha256 2>/dev/null | tail -2
else
  echo "  openssl 缺失, 跳过"
fi
echo "-- 4.2 stress-ng 多核扩展 (各 5 秒, 混合整数/浮点) --"
if command -v stress-ng >/dev/null; then
  N=$(nproc)
  for k in 1 $((N / 2 > 1 ? N / 2 : 1)) $N; do
    echo "  workers=$k :"
    stress-ng --cpu "$k" --cpu-method all --metrics-brief -t 5 -q 2>&1 | tail -2 | sed 's/^/    /'
  done
  echo "  解读: 对比 workers=1 与 workers=N 的 bogo ops/s, 比值≈多核扩展度"
else
  echo "  stress-ng 缺失 (apt install stress-ng 后重跑本脚本可得多核扩展数据)"
fi

say "5. 完成"
echo "报告文件: $(realpath "$OUT" 2>/dev/null || echo "$OUT")"
echo "回传建议: 仍在此机执行  cat $OUT  后整段粘贴给灵克解读"
