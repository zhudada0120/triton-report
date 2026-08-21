#!/usr/bin/env bash
# =============================================================================
# vllm-report 完整流水线脚本
# 将 daily-commit.yml 中所有步骤整合为一个可在本地执行的脚本。
# 需要先准备好：
#   1. vllm 和 vllm-ascend 的本地仓库（或让脚本自动 clone）
#   2. Python 3.11+ 环境，已安装 requirements.txt
#   3. 环境变量 LLM_API_KEY（用于 Phase 1 分析）
#   4. opencode CLI（用于 Phase 2 深度分析，可选）
# =============================================================================

set -euo pipefail

# ---------- 默认配置 ----------
DATA_DIR="${DATA_DIR:-$(pwd)/data}"
REPOS_DIR="${REPOS_DIR:-$(pwd)/repos}"
VLLM_REPO_PATH="${VLLM_REPO_PATH:-${REPOS_DIR}/vllm}"
ASCEND_REPO_PATH="${ASCEND_REPO_PATH:-${REPOS_DIR}/vllm-ascend}"
DATE="${DATE:-}"
FORCE="${FORCE:-false}"
# 默认处理所有仓库；可设为 vllm / vllm-ascend
REPO="${REPO:-}"

# ---------- 颜色 ----------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
step()  { echo -e "\n${CYAN}════════════════════════════════════════════════════════════${NC}"; echo -e "${CYAN}  >>> $*${NC}"; echo -e "${CYAN}════════════════════════════════════════════════════════════${NC}"; }
substep() { echo -e "  ${YELLOW}->${NC} $*"; }

# ---------- 帮助 ----------
usage() {
    cat <<EOF
用法: $0 [选项]

选项:
  --repo REPO              只处理指定仓库：vllm 或 vllm-ascend（默认：全部）
  --date YYYY-MM-DD       目标日期（默认：北京时间昨天）
  --vllm-path PATH         vllm 本地仓库路径
  --ascend-path PATH       vllm-ascend 本地仓库路径
  --data-dir PATH          data 目录路径（默认: ./data）
  --force                  强制重新获取和重新分析
  --skip-fetch             跳过 fetch 步骤
  --skip-analyze           跳过 analyze 步骤
  --skip-deep-analyze      跳过 deep_analyze 步骤
  --skip-build-index       跳过 build_index 步骤
  --skip-track-adaptation  跳过 track_adaptation 步骤
  --skip-clean             跳过 clean_stale_data 步骤
  --skip-pull              不 git pull 更新本地仓库
  -h, --help               显示此帮助信息

环境变量:
  LLM_API_KEY               DeepSeek API Key（Phase 1 必需）
  OPENCODE_AUTH_TOKEN       OpenAI 兼容 API Key（仅当 opencode 配置未内置时使用）

示例:
  # 基本用法（分析昨天的 commit）
  LLM_API_KEY=sk-xxx ./run_pipeline.sh

  # 指定日期和本地仓库
  LLM_API_KEY=sk-xxx ./run_pipeline.sh --date 2026-07-27 \
    --vllm-path ~/code/vllm --ascend-path ~/code/vllm-ascend

  # 只处理 vllm
  LLM_API_KEY=sk-xxx ./daily_refresh.sh --repo vllm

  # 只处理 vllm-ascend
  LLM_API_KEY=sk-xxx ./daily_refresh.sh --repo vllm-ascend

  # 只执行 fetch 和 analyze，跳过深度分析
  LLM_API_KEY=sk-xxx ./daily_refresh.sh --skip-deep-analyze --skip-build-index
EOF
    exit 0
}

# ---------- 参数解析 ----------
SKIP_FETCH=false
SKIP_ANALYZE=false
SKIP_DEEP_ANALYZE=false
SKIP_BUILD_INDEX=false
SKIP_TRACK_ADAPTATION=false
SKIP_CLEAN=false
SKIP_PULL=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo)           REPO="$2"; shift 2 ;;
        --date)           DATE="$2"; shift 2 ;;
        --vllm-path)      VLLM_REPO_PATH="$2"; shift 2 ;;
        --ascend-path)    ASCEND_REPO_PATH="$2"; shift 2 ;;
        --data-dir)       DATA_DIR="$2"; shift 2 ;;
        --force)          FORCE=true; shift ;;
        --skip-fetch)     SKIP_FETCH=true; shift ;;
        --skip-analyze)   SKIP_ANALYZE=true; shift ;;
        --skip-deep-analyze) SKIP_DEEP_ANALYZE=true; shift ;;
        --skip-build-index) SKIP_BUILD_INDEX=true; shift ;;
        --skip-track-adaptation) SKIP_TRACK_ADAPTATION=true; shift ;;
        --skip-clean)     SKIP_CLEAN=true; shift ;;
        --skip-pull)      SKIP_PULL=true; shift ;;
        -h|--help)        usage ;;
        *)                err "未知参数: $1"; usage ;;
    esac
done

# ---------- 日期 & 仓库过滤 ----------
if [ -z "$DATE" ]; then
    TZ=Asia/Shanghai DATE=$(date -d yesterday +%Y-%m-%d)
fi
info "目标日期: $DATE"

if [ -n "$REPO" ]; then
    case "$REPO" in
        vllm)          DO_VLLM=true; DO_ASCEND=false ;;
        vllm-ascend)   DO_VLLM=false; DO_ASCEND=true ;;
        *)             err "不支持的仓库: $REPO（可选: vllm / vllm-ascend）"; exit 1 ;;
    esac
    info "仅处理仓库: $REPO"
else
    DO_VLLM=true
    DO_ASCEND=true
fi

FORCE_FLAG=""
if [ "$FORCE" = "true" ]; then
    FORCE_FLAG="--force"
fi

PULL_FLAG=""
if [ "$SKIP_PULL" = "true" ]; then
    PULL_FLAG="--skip-pull"
fi

# ---------- 前置检查 ----------
step "1/9  前置检查"

# 检查 Python
if ! command -v python3 &>/dev/null; then
    err "python3 未找到，请安装 Python 3.11+"
    exit 1
fi
info "Python: $(python3 --version)"

# 检查 requirements.txt 是否已安装
substep "检查 Python 依赖..."
python3 -c "import requests" 2>/dev/null || {
    warn "依赖未安装，执行 pip install -r requirements.txt ..."
    pip install -r requirements.txt
}
ok "依赖检查完成"

# 检查 LLM_API_KEY
if [ "$SKIP_ANALYZE" = "false" ]; then
    if [ -z "${LLM_API_KEY:-}" ]; then
        err "LLM_API_KEY 环境变量未设置。Phase 1 分析需要 DeepSeek API Key。"
        err "设置方式: export LLM_API_KEY=sk-xxx"
        exit 1
    fi
    info "LLM_API_KEY 已设置"
fi

# 检查 opencode（Phase 2）
if [ "$SKIP_DEEP_ANALYZE" = "false" ]; then
    if command -v opencode &>/dev/null; then
        info "opencode CLI: $(opencode --version 2>/dev/null || echo 'found')"
    else
        warn "opencode CLI 未找到。Phase 2 深度分析将跳过。"
        warn "安装方式: npm install -g opencode-ai"
        SKIP_DEEP_ANALYZE=true
    fi
fi

# 创建目录
mkdir -p "$DATA_DIR" "$REPOS_DIR"

# ---------- 仓库检查 ----------
step "2/9  仓库检查"

ensure_repo() {
    local repo_name="$1"
    local local_path="$2"
    local repo_url="$3"

    if [ -d "$local_path/.git" ]; then
        info "$repo_name 仓库已存在: $local_path"
        if [ "$SKIP_PULL" = "false" ]; then
            substep "拉取最新代码..."
            git -C "$local_path" pull --ff-only origin main 2>&1 || warn "pull 失败（非 fast-forward），继续使用当前状态"
        fi
    else
        substep "克隆 $repo_name ..."
        git clone --depth 1 "$repo_url" "$local_path"
        ok "克隆完成: $local_path"
    fi
}

ensure_repo "vllm" "$VLLM_REPO_PATH" "https://github.com/vllm-project/vllm.git"
if [ "$DO_ASCEND" = "true" ]; then
    ensure_repo "vllm-ascend" "$ASCEND_REPO_PATH" "https://github.com/vllm-project/vllm-ascend.git"
fi

# 检查 vllm-ascend 的 baseline 文件（仅处理 ascend 时需要）
if [ "$DO_ASCEND" = "true" ]; then
    BASELINE_FILE="${ASCEND_REPO_PATH}/.github/vllm-main-verified.commit"
    if [ -f "$BASELINE_FILE" ]; then
        BASELINE_SHA=$(cat "$BASELINE_FILE" | tr -d '[:space:]')
        info "vllm-ascend baseline SHA: ${BASELINE_SHA:0:12}"
    else
        warn "baseline 文件不存在: $BASELINE_FILE"
        warn "track_adaptation 步骤可能需要 --since 参数"
    fi
fi

ok "仓库准备完毕"

# ---------- Step 3: Fetch vllm commits ----------
if [ "$SKIP_FETCH" = "false" ] && [ "$DO_VLLM" = "true" ]; then
    step "3/9  获取 vllm commit 数据"
    substep "执行: python src/data/fetch_commits.py --repo vllm-project/vllm --local-repo $VLLM_REPO_PATH --date $DATE $FORCE_FLAG"
    python src/data/fetch_commits.py \
        --repo vllm-project/vllm \
        --local-repo "$VLLM_REPO_PATH" \
        --date "$DATE" \
        $FORCE_FLAG
    ok "vllm commits 获取完成"
else
    step "3/9  跳过 fetch vllm commits"
fi

# ---------- Step 4: Analyze vllm commits (Phase 1) ----------
if [ "$SKIP_ANALYZE" = "false" ] && [ "$DO_VLLM" = "true" ]; then
    step "4/9  分析 vllm commits (Phase 1)"
    COMMIT_FILE="${DATA_DIR}/vllm/commits/${DATE}.json"
    if [ -f "$COMMIT_FILE" ]; then
        substep "执行: python src/data/analyze_commits.py --repo vllm-project/vllm --date $DATE --local-repo $VLLM_REPO_PATH --data-dir $DATA_DIR $FORCE_FLAG"
        LLM_API_KEY="${LLM_API_KEY}" python src/data/analyze_commits.py \
            --repo vllm-project/vllm \
            --date "$DATE" \
            --local-repo "$VLLM_REPO_PATH" \
            --data-dir "$DATA_DIR" \
            $FORCE_FLAG
        ok "vllm Phase 1 分析完成"
    else
        warn "未找到 commit 数据: $COMMIT_FILE，跳过 vllm 分析"
    fi
else
    step "4/9  跳过分析 vllm commits"
fi

# ---------- Step 5: Deep analyze vllm commits (Phase 2) ----------
if [ "$SKIP_DEEP_ANALYZE" = "false" ] && [ "$DO_VLLM" = "true" ]; then
    step "5/9  深度分析 vllm commits (Phase 2)"
    ANALYSIS_FILE="${DATA_DIR}/vllm/analysis/${DATE}.json"
    if [ -f "$ANALYSIS_FILE" ]; then
        substep "执行: python src/data/deep_analyze_commits.py --repo vllm-project/vllm --date $DATE --local-repo $VLLM_REPO_PATH --data-dir $DATA_DIR"
        python src/data/deep_analyze_commits.py \
            --repo vllm-project/vllm \
            --date "$DATE" \
            --local-repo "$VLLM_REPO_PATH" \
            --data-dir "$DATA_DIR"
        ok "vllm Phase 2 深度分析完成"
    else
        warn "未找到 Phase 1 分析结果: $ANALYSIS_FILE，跳过深度分析"
    fi
else
    step "5/9  跳过深度分析 vllm commits"
fi

# ---------- Step 6: Fetch vllm-ascend commits ----------
if [ "$SKIP_FETCH" = "false" ] && [ "$DO_ASCEND" = "true" ]; then
    step "6/9  获取 vllm-ascend commit 数据"
    substep "执行: python src/data/fetch_commits.py --repo vllm-project/vllm-ascend --local-repo $ASCEND_REPO_PATH --date $DATE $FORCE_FLAG"
    python src/data/fetch_commits.py \
        --repo vllm-project/vllm-ascend \
        --local-repo "$ASCEND_REPO_PATH" \
        --date "$DATE" \
        $FORCE_FLAG
    ok "vllm-ascend commits 获取完成"
else
    step "6/9  跳过 fetch vllm-ascend commits"
fi

# ---------- Step 7: Analyze vllm-ascend commits ----------
if [ "$SKIP_ANALYZE" = "false" ] && [ "$DO_ASCEND" = "true" ]; then
    step "7/9  分析 vllm-ascend commits"
    COMMIT_FILE_ASCEND="${DATA_DIR}/vllm-ascend/commits/${DATE}.json"
    if [ -f "$COMMIT_FILE_ASCEND" ]; then
        substep "执行: python src/data/analyze_commits.py --repo vllm-project/vllm-ascend --date $DATE --data-dir $DATA_DIR $FORCE_FLAG"
        LLM_API_KEY="${LLM_API_KEY}" python src/data/analyze_commits.py \
            --repo vllm-project/vllm-ascend \
            --date "$DATE" \
            --data-dir "$DATA_DIR" \
            $FORCE_FLAG
        ok "vllm-ascend 分析完成"
    else
        warn "未找到 commit 数据: $COMMIT_FILE_ASCEND，跳过 vllm-ascend 分析"
    fi
else
    step "7/9  跳过分析 vllm-ascend commits"
fi

# ---------- Step 8: Build search index ----------
if [ "$SKIP_BUILD_INDEX" = "false" ] && { [ "$DO_VLLM" = "true" ] || [ "$DO_ASCEND" = "true" ]; }; then
    step "8/9  构建搜索索引"
    substep "执行: python src/data/build_index.py --data-dir $DATA_DIR"
    python src/data/build_index.py --data-dir "$DATA_DIR"
    ok "搜索索引构建完成"
else
    step "8/9  跳过构建搜索索引"
fi

# ---------- Step 9: Track adaptation ----------
if [ "$SKIP_TRACK_ADAPTATION" = "false" ] && [ "$DO_VLLM" = "true" ]; then
    step "9/9  跟踪适配状态"
    substep "执行: python src/data/track_adaptation.py init --ascend-repo-path $ASCEND_REPO_PATH --data-dir $DATA_DIR $FORCE_FLAG"
    python src/data/track_adaptation.py init \
        --ascend-repo-path "$ASCEND_REPO_PATH" \
        --data-dir "$DATA_DIR" \
        $FORCE_FLAG
    ok "适配状态跟踪完成"
else
    step "9/9  跳过跟踪适配状态"
fi

# ---------- Clean stale data ----------
if [ "$SKIP_CLEAN" = "false" ]; then
    step "清理过期数据（无对应分析的 commit 文件）"
    substep "执行: python .github/scripts/clean_stale_data.py --data-dir $DATA_DIR"
    python .github/scripts/clean_stale_data.py --data-dir "$DATA_DIR"
    ok "清理完成"
fi

# ---------- 完成 ----------
echo -e "\n${GREEN}========================================${NC}"
echo -e "${GREEN}  流水线执行完毕！${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "  日期:    $DATE"
echo -e "  仓库:    $([ "$DO_VLLM" = "true" ] && echo -n "vllm ") $([ "$DO_ASCEND" = "true" ] && echo -n "vllm-ascend")"
echo -e "  数据:    $DATA_DIR"
echo -e "  vllm:    $VLLM_REPO_PATH"
echo -e "  ascend:  $ASCEND_REPO_PATH"
echo -e "${GREEN}========================================${NC}"