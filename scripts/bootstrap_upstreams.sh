#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
upstream_root="${project_root}/upstream"
mkdir -p "$upstream_root"

clone_if_missing() {
  local name="$1"
  local url="$2"
  local commit="$3"
  local target="${upstream_root}/${name}"
  if [[ -d "${target}/.git" ]]; then
    echo "已存在：${target}"
    return
  fi
  git clone --filter=blob:none --no-checkout "$url" "$target"
  git -C "$target" checkout --detach "$commit"
}

clone_if_missing \
  "zotero-arxiv-daily" \
  "https://github.com/TideDra/zotero-arxiv-daily.git" \
  "f3f73ce053f75ace2b15e38299890af7d530e214"
clone_if_missing \
  "vla-wam-daily" \
  "https://github.com/i6bimua/vla-wam-daily.git" \
  "b6ae8dcfb059fd61bc3d2987b25507b4b8979237"
