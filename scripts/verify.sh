#!/usr/bin/env bash
# 全量验证并生成证据报告。SPEC §17。
#
# 最终交付报告的「验证证据」与「测试数据结果」两节**必须是本脚本产出的原样粘贴**。
# 报告中出现任何未在 docs/validation-report.md 里出现的命令或数字，视为交付失败。
#
# 设计要点：
#   - 每一步都把命令、stdout/stderr、退出码写进报告
#   - **失败不中断**（要看到全貌，不是第一个红灯就停）
#   - 末尾按累计失败数返回单一退出码

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$REPO_ROOT/backend"
FRONTEND="$REPO_ROOT/frontend"
PY="$BACKEND/.venv/Scripts/python.exe"
[ -x "$PY" ] || PY="$BACKEND/.venv/bin/python"
REPORT="$REPO_ROOT/docs/validation-report.md"

FAILED=0
STEP=0

# 这份报告会进仓库（且可能是公开仓库）。绝对路径会带上开发机的用户名，
# 与 SPEC §12.1 / §15.1「错误信息不含服务器绝对路径」是同一条红线——
# 报告头部小心写了未展开的 $REPO_ROOT，但 run() 打印的命令行里 $PY 是展开的，
# 每一步都会把 /c/Users/<某人>/... 原样写进去。三种写法都要抹掉：
#   /c/Users/x/repo（Git Bash）、C:/Users/x/repo（多数工具输出）、C:\Users\x\repo（pip 等）
_WIN_FWD="$(cd "$REPO_ROOT" && pwd -W 2>/dev/null || printf '%s' "$REPO_ROOT")"
_WIN_BACK="${_WIN_FWD//\//\\}"
_WIN_BACK_ESC="${_WIN_BACK//\\/\\\\}"

sanitize() {
  sed -e "s|$REPO_ROOT|<repo>|g" \
      -e "s|$_WIN_FWD|<repo>|g" \
      -e "s|$_WIN_BACK_ESC|<repo>|g"
}

mkdir -p "$REPO_ROOT/docs"

{
  echo "# 验证证据报告"
  echo
  echo "> **本文件由 \`scripts/verify.sh\` 自动生成，禁止手写。**"
  echo "> 每一步都记录了实际命令、完整输出与退出码。失败不中断，跑完全部再汇总。"
  echo
  echo '```'
  echo "生成时间（本机）: $(date '+%Y-%m-%d %H:%M:%S %z')"
  echo "仓库根           : \$REPO_ROOT"
  echo "Python 解释器    : \$BACKEND/.venv"
  echo '```'
  echo
  echo "---"
  echo
} > "$REPORT"

run() {
  local title="$1"; shift
  local workdir="$1"; shift
  STEP=$((STEP + 1))

  local output status
  output="$(cd "$workdir" && "$@" 2>&1)"
  status=$?

  if [ $status -ne 0 ]; then
    FAILED=$((FAILED + 1))
  fi

  {
    echo "## $STEP. $title"
    echo
    echo "命令：\`$(printf '%s' "$*" | sanitize)\`"
    echo
    echo "退出码：**$status**$([ $status -eq 0 ] && echo ' ✅' || echo ' ❌')"
    echo
    echo '```'
    # 输出过长时保留头尾，中间省略——避免报告变成日志倾倒
    if [ "$(printf '%s' "$output" | wc -l)" -gt 80 ]; then
      printf '%s\n' "$output" | head -40 | sanitize
      echo "…（中间省略 $(( $(printf '%s' "$output" | wc -l) - 60 )) 行）…"
      printf '%s\n' "$output" | tail -20 | sanitize
    else
      printf '%s\n' "$output" | sanitize
    fi
    echo '```'
    echo
  } >> "$REPORT"

  printf '[%s] %-42s exit=%d\n' "$([ $status -eq 0 ] && echo ' OK ' || echo 'FAIL')" "$title" "$status"
}

echo "=== 后端 ==="
run "Python 版本"            "$BACKEND" "$PY" -V
run "pip 版本"               "$BACKEND" "$PY" -m pip --version
run "后端格式化检查"          "$BACKEND" "$PY" -m ruff format --check .
run "后端 lint"              "$BACKEND" "$PY" -m ruff check app tests tools
run "后端类型检查"            "$BACKEND" "$PY" -m mypy app
run "比较规则文档与注册表同步" "$BACKEND" env PYTHONIOENCODING=utf-8 "$PY" -m tools.gen_docs --check
run "后端全部测试"            "$BACKEND" "$PY" -m pytest -q
# 硬约束 #14：上一步的「N passed」是反选之后的数字，反选数量必须一并摆出来。
# PYTHONIOENCODING：Windows 下 stdout 被重定向时按 GBK 编码，中文会进报告变成乱码。
run "零 skip / MVP-1 反选数量" "$BACKEND" env PYTHONIOENCODING=utf-8 "$PY" -m tools.mvp1_report
run "Golden 测试（单独）"     "$BACKEND" "$PY" -m pytest -q -m "golden and not mvp1"
# SPEC §17：每组一张表由 pytest 的 sessionfinish 钩子写 docs/golden-report.md（实为 16 组）。
# 把总览表也收进本报告：文件缺失时 head 直接非 0，不会出现「跑绿了但没产出证据」。
run "Golden 指标报告总览"     "$REPO_ROOT" head -30 docs/golden-report.md
run "架构守卫测试"            "$BACKEND" "$PY" -m pytest -q tests/test_guards.py
# Gate-0 第 16 条：运行时产出的枚举值必须是声明全集的子集
run "只产已声明枚举（enum_subset）" "$BACKEND" "$PY" -m pytest -q -m "enum_subset and not mvp1"
run "确定性：PYTHONHASHSEED=1" "$BACKEND" env PYTHONHASHSEED=1 "$PY" -m pytest -q
# Gate-0 第 15 条：连跑 3 次逐字节一致。必须**跨进程且换哈希种子**——
# 同进程内跑三次共用同一个种子下的迭代顺序，三次当然一样，抓不到「用了未排序的 set」。
run "确定性：跨进程 3 次逐字节一致" "$BACKEND" \
  env PYTHONIOENCODING=utf-8 "$PY" -m tools.determinism

echo
echo "=== 前端 ==="
if [ -f "$FRONTEND/package.json" ]; then
  run "前端 lint"       "$FRONTEND" npm run lint
  run "前端类型检查"     "$FRONTEND" npm run typecheck
  run "前端单元测试"     "$FRONTEND" npm test
  run "前端构建"         "$FRONTEND" npm run build
else
  STEP=$((STEP + 1))
  FAILED=$((FAILED + 1))
  {
    echo "## $STEP. 前端"
    echo
    echo "退出码：**跳过** ❌ —— 未找到 \`frontend/package.json\`，前端未交付。"
    echo
  } >> "$REPORT"
  echo "[FAIL] 前端未交付（缺 package.json）"
fi

echo
echo "=== 汇总 ==="
{
  echo "---"
  echo
  echo "## 汇总"
  echo
  echo "- 步骤总数：$STEP"
  echo "- 失败步骤：**$FAILED**"
  echo
  if [ $FAILED -eq 0 ]; then
    echo "全部步骤退出码为 0。"
  else
    echo "**存在失败步骤，交付不完整。** 逐条见上文，不得以「应该没问题」带过。"
  fi
  echo
} >> "$REPORT"

printf '步骤 %d，失败 %d\n' "$STEP" "$FAILED"
echo "证据报告：$REPORT"
exit $(( FAILED > 0 ? 1 : 0 ))
