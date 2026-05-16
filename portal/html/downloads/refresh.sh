#!/usr/bin/env bash
# Vega VPN · 客户端镜像刷新脚本
# 目的：把核心 VPN 客户端从 GitHub release 镜像到本地，绕过 GFW 对 github.com 的阻断
# Usage: bash portal/html/downloads/refresh.sh
# Output: portal/html/downloads/*.{apk,exe,dmg,zip,tar.gz} + index.json

set -uo pipefail

DL_DIR="$(cd "$(dirname "$0")" && pwd)"
TMP_DIR="${DL_DIR}/.tmp"
LOG_FILE="${DL_DIR}/refresh.log"
INDEX_FILE="${DL_DIR}/index.json"

mkdir -p "${TMP_DIR}"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "${LOG_FILE}"; }

# 镜像目标定义
# 格式: repo|asset-regex|local-filename
# regex 用 grep -E 匹配 release JSON 的 browser_download_url
TARGETS=(
  "2dust/v2rayNG|arm64-v8a\.apk$|v2rayng-latest-arm64.apk"
  "2dust/v2rayNG|universal\.apk$|v2rayng-latest-universal.apk"
  "MatsuriDayo/NekoBoxForAndroid|arm64-v8a\.apk$|nekobox-latest-arm64.apk"
  "clash-verge-rev/clash-verge-rev|x64-setup\.exe$|clash-verge-rev-latest-win64.exe"
  "clash-verge-rev/clash-verge-rev|aarch64\.dmg$|clash-verge-rev-latest-mac-arm64.dmg"
  "clash-verge-rev/clash-verge-rev|x64\.dmg$|clash-verge-rev-latest-mac-x64.dmg"
  "MatsuriDayo/nekoray|windows64\.zip$|nekoray-latest-win64.zip"
  "SagerNet/sing-box|linux-amd64\.tar\.gz$|sing-box-latest-linux-amd64.tar.gz"
  "hiddify/hiddify-app|Android-arm64\.apk$|hiddify-latest-android-arm64.apk"
  "hiddify/hiddify-app|Windows-Setup-x64\.exe$|hiddify-latest-windows-x64.exe"
  "hiddify/hiddify-app|MacOS\.dmg$|hiddify-latest-macos.dmg"
  "hiddify/hiddify-app|Linux-x64\.AppImage$|hiddify-latest-linux-x64.AppImage"
)

# 启动 index.json 数组
ENTRIES=()

fetch_release_url() {
  local repo="$1" regex="$2"
  # 取 latest release 的 assets URL；用 grep -E 兜底 jq
  local url
  url=$(curl -fsSL --max-time 30 \
    -H "Accept: application/vnd.github+json" \
    -H "User-Agent: vega-vpn-mirror" \
    "https://api.github.com/repos/${repo}/releases/latest" \
    | jq -r '.assets[].browser_download_url' 2>/dev/null \
    | grep -E "${regex}" \
    | head -n1)
  echo "${url}"
}

for target in "${TARGETS[@]}"; do
  IFS='|' read -r repo regex local_name <<< "${target}"
  log "处理 ${repo} → ${local_name}"

  url=$(fetch_release_url "${repo}" "${regex}")
  if [[ -z "${url}" ]]; then
    log "  [跳过] 未在 ${repo} 最新 release 找到匹配 /${regex}/ 的 asset"
    continue
  fi
  log "  源: ${url}"

  tmp_file="${TMP_DIR}/${local_name}"
  if ! curl -fL --max-time 600 -o "${tmp_file}" "${url}"; then
    log "  [失败] 下载错误，删除残留"
    rm -f "${tmp_file}"
    continue
  fi

  size=$(stat -c%s "${tmp_file}")
  sha=$(sha256sum "${tmp_file}" | awk '{print $1}')
  mv "${tmp_file}" "${DL_DIR}/${local_name}"
  log "  [完成] ${local_name} ${size} bytes sha256=${sha:0:16}…"

  ENTRIES+=("$(jq -nc \
    --arg name "${local_name}" \
    --arg repo "${repo}" \
    --arg src "${url}" \
    --arg sha "${sha}" \
    --argjson size "${size}" \
    --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    '{name:$name, repo:$repo, source_url:$src, sha256:$sha, size:$size, downloaded_at:$ts}')")
done

# 拼装 index.json
printf '{\n  "updated_at": "%s",\n  "files": [\n    %s\n  ]\n}\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  "$(IFS=,; echo "${ENTRIES[*]}" | sed 's/,/,\n    /g')" \
  > "${INDEX_FILE}"

rmdir "${TMP_DIR}" 2>/dev/null || true
log "全部完成，index.json 已更新（${#ENTRIES[@]} 个文件）"
