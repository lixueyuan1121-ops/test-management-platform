#!/usr/bin/env bash
# 测试管理平台 · 开发机侧发布脚本（合并到 main → 按需 build 前端 → 推送）
#
# 用法（在任意 worktree 或主检出里执行，不依赖脚本自身位置）：
#   bash scripts/release.sh                  # 合并「当前所在分支」到 main
#   bash scripts/release.sh feat/bugfix0901  # 合并「指定分支」到 main
#   NO_PUSH=1 bash scripts/release.sh        # 只合并+build+提交，不推送（留给你 review）
#
# 做了什么：
#   1. 定位主检出（frontend/node_modules 只装在那里，worktree 里 build 不了）
#   2. 主检出切 main、拉最新，把目标分支合并进来
#   3. 只有 frontend/src 真变了才 npm run build，dist 单独一个 commit
#   4. push origin main，并打印服务器侧还需要做什么
#
# 前置：主检出工作区必须干净。代码冲突交给你手动解决；
#      frontend/dist 的冲突脚本自动处理（反正紧接着要重新 build 覆盖）。
set -euo pipefail

# ---- 0. 解析目标分支（必须在跳去主检出之前拿，否则拿到的是 main 自己）----
BRANCH="${1:-}"
if [ -z "$BRANCH" ]; then
  BRANCH="$(git rev-parse --abbrev-ref HEAD)"
  if [ "$BRANCH" = "HEAD" ]; then
    echo "!! 当前是 detached HEAD，判断不出要合并哪个分支。" >&2
    echo "   请显式指定：bash scripts/release.sh <分支名>" >&2
    exit 1
  fi
fi

# ---- 1. 定位主检出 ----
# `git worktree list --porcelain` 第一条永远是主检出；
# 各 worktree 的 node_modules / backend/.venv 都不共享，构建只能在主检出做。
MAIN="$(git worktree list --porcelain | head -1 | sed 's/^worktree //')"
if [ -z "$MAIN" ] || [ ! -d "$MAIN/.git" ]; then
  echo "!! 定位主检出失败（拿到: '$MAIN'）" >&2
  exit 1
fi
echo "==> 主检出: $MAIN"
echo "==> 待合并分支: $BRANCH"
cd "$MAIN"

# ---- 2. 主检出必须干净（未跟踪文件不算）----
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "!! 主检出有未提交改动，先处理干净再发布：" >&2
  git status --short --untracked-files=no >&2
  exit 1
fi

# ---- 3. 切 main、拉最新 ----
echo "==> 切到 main 并拉取最新"
if ! git checkout main; then
  echo "!! 切 main 失败。多半是 main 被别的 worktree 占用了（git worktree list 看一下）" >&2
  exit 1
fi
git pull --no-rebase origin main

# ---- 4. 合并目标分支 ----
if [ "$BRANCH" = "main" ]; then
  echo "==> 目标就是 main，跳过合并"
elif git merge-base --is-ancestor "$BRANCH" HEAD 2>/dev/null; then
  echo "==> $BRANCH 已在 main 里，跳过合并"
else
  echo "==> 合并 $BRANCH → main"
  if ! git merge --no-edit "$BRANCH"; then
    CONFLICTS="$(git diff --name-only --diff-filter=U)"
    OUTSIDE_DIST="$(echo "$CONFLICTS" | grep -v '^frontend/dist/' || true)"
    if [ -n "$OUTSIDE_DIST" ]; then
      echo "!! 合并冲突，需要你手动解决这些文件：" >&2
      echo "$OUTSIDE_DIST" >&2
      echo "   解决后 git commit，然后重跑本脚本（会自动跳过已完成的合并）。" >&2
      exit 1
    fi
    # 冲突全在构建产物里：取哪边都无所谓，下面会重新 build 覆盖
    echo "   冲突仅在 frontend/dist（构建产物），自动放行，稍后重新 build 覆盖"
    while IFS= read -r f; do
      [ -z "$f" ] && continue
      git checkout --ours -- "$f" 2>/dev/null || git rm -f --quiet -- "$f" 2>/dev/null || true
      git add -- "$f" 2>/dev/null || true
    done <<< "$CONFLICTS"
    git commit --no-edit
  fi
fi

# ---- 5. 只有 frontend/src 真变了才 build ----
# 口径：本次待推的全部改动（origin/main..HEAD）里有没有碰 frontend/src。
# 没碰就别 build——普通 build 不清 dist/assets，白跑一次只会平添一批新 hash 文件。
echo "==> 检查 frontend/src 是否有改动"
if git diff --quiet origin/main..HEAD -- frontend/src; then
  echo "   frontend/src 无改动，跳过 build"
else
  git diff --stat origin/main..HEAD -- frontend/src | tail -n 1

  if [ ! -d frontend/node_modules ]; then
    echo "!! 主检出缺 frontend/node_modules，先跑一次：cd $MAIN/frontend && npm install" >&2
    exit 1
  fi

  # 依赖清单变了才装依赖
  if ! git diff --quiet origin/main..HEAD -- frontend/package.json frontend/package-lock.json; then
    echo "==> package.json/lock 有变化，npm install"
    (cd frontend && npm install)
  fi

  # 用普通 build：只增量产出本次改动对应的 hash 文件，diff 聚焦、可 review。
  # 别换成 build:clean——它会先清空 dist/assets，把上千个历史文件一起删掉，diff 没法看。
  echo "==> npm run build"
  (cd frontend && npm run build)

  if [ -n "$(git status --porcelain -- frontend/dist)" ]; then
    git add frontend/dist
    git commit -m "chore(dist): rebuild 前端产物"
    echo "   dist 已单独提交"
  else
    echo "   build 后 dist 无变化，无需提交"
  fi
fi

# ---- 6. 推送 ----
AHEAD="$(git rev-list --count origin/main..HEAD)"
if [ "$AHEAD" = "0" ]; then
  echo "==> 没有待推送的提交，结束"
  exit 0
fi
echo "==> 待推送 $AHEAD 个提交："
git log --oneline origin/main..HEAD

if [ -n "${NO_PUSH:-}" ]; then
  echo "==> NO_PUSH 已设置，跳过推送。确认无误后自己跑：git -C $MAIN push origin main"
  exit 0
fi
echo "==> 推送 origin main"
git push origin main

# ---- 7. 服务器侧不会自动生效，提示一下 ----
cat <<'EOF'

==> 推送完成。服务器侧还需要手动生效（uvicorn 没开 --reload）：
    Linux(:4173)    bash scripts/update.sh
    Windows(:8000)  git pull → 关掉 "TP-Backend" cmd 窗口 → 重新双击 start-all.bat
EOF
