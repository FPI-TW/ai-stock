#!/usr/bin/env bash
set -euo pipefail

# repo 在 EC2 上的部署目錄；CI 會把 compose 與 nginx 設定 scp 到這裡。
deploy_dir="/home/ubuntu/app/ai-stock"
compose_file="docker-compose.prod.yml"
env_file=".env.prod"

: "${IMAGE_TAG:?IMAGE_TAG 必填（要部署的映像標籤）}"
: "${GHCR_ACTOR:?GHCR_ACTOR 必填}"
: "${GHCR_TOKEN:?GHCR_TOKEN 必填}"
export IMAGE_TAG

cd "$deploy_dir"

# 1. 機密設定檔必須存在（由 CI 從 GitHub Secrets 組出並 scp，見 cd.yml deploy job）。
if [[ ! -f "$env_file" ]]; then
  echo "錯誤：找不到 $deploy_dir/$env_file，CI 應在部署前從 GitHub Secrets 組出並 scp。" >&2
  exit 1
fi

# 2. compose 與 nginx 設定必須已由 CI scp 過來（防止漏推就部署）。
if [[ ! -f "$compose_file" ]]; then
  echo "錯誤：找不到 $deploy_dir/$compose_file，CI 應在部署前 scp 推送。" >&2
  exit 1
fi
if [[ ! -f "nginx/nginx.conf" ]]; then
  echo "錯誤：找不到 $deploy_dir/nginx/nginx.conf，CI 應在部署前 scp 推送。" >&2
  exit 1
fi

# 3. Origin TLS 憑證必須已由 EC2 機密管理流程放置；不存在或空檔時，在任何 Docker
#    操作前停止，避免新 nginx 設定因憑證遺漏而連帶中斷現有 HTTP 入口。憑證內容不輸出。
origin_tls_dir="/home/ubuntu/etc/ai-stock/tls"
origin_certificate="$origin_tls_dir/server.crt"
origin_private_key="$origin_tls_dir/server.key"
for cert_file in "$origin_certificate" "$origin_private_key"; do
  if [[ ! -f "$cert_file" || ! -s "$cert_file" ]]; then
    echo "錯誤：origin TLS 憑證檔不存在或為空：${cert_file}。請先放置 EC2 憑證後再部署。" >&2
    exit 1
  fi
done

compose=(docker compose -f "$compose_file" --env-file "$env_file")

# ==============================================================================
# 4. 登入 GHCR 並拉取本次要部署的映像
# ==============================================================================
echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_ACTOR" --password-stdin

# 💡 精髓所在：登入後立刻註冊 EXIT trap。
# 不論是成功走到最後、或是 fail_deploy 的 exit 1、甚至是被 Ctrl+C 中斷，
# 都會保證執行登出，不讓 token 憑證殘留在 ~/.docker/config.json 中。
trap 'echo "正在清理登入憑證..."; docker logout ghcr.io >/dev/null 2>&1 || true' EXIT

"${compose[@]}" pull

# 5. 滾動更新前，先記下目前線上的 app 映像，供健康檢查失敗時回滾（首次部署時為空）。
prev_app_cid="$("${compose[@]}" ps -q app 2>/dev/null || true)"
prev_app_image=""
if [[ -n "$prev_app_cid" ]]; then
  prev_app_image="$(docker inspect -f '{{.Config.Image}}' "$prev_app_cid" 2>/dev/null || true)"
fi

# 健康檢查最長等待：max_attempts × 5s。
max_attempts=36

# 部署失敗收尾：印 log → 回滾 app 至前一版映像 → 讓部署失敗。
rollback_app() {
  if [[ -z "$prev_app_image" ]]; then
    echo "無前一版 app 映像可回滾（可能是首次部署），請手動處理。" >&2
    return
  fi
  local prev_tag="${prev_app_image##*:}"
  echo "回滾 app/worker 至前一版 IMAGE_TAG=$prev_tag（--no-deps：不重跑 migrate）" >&2
  if ! IMAGE_TAG="$prev_tag" "${compose[@]}" up -d --no-deps app worker >&2; then
    echo "回滾失敗（schema 已前進，需人工處理）。" >&2
  fi
}

fail_deploy() {
  echo "$1" >&2
  "${compose[@]}" logs --tail 100 migrate app >&2 || true
  rollback_app
  exit 1 # 這裡的 exit 1 會成功觸發上面註冊的 EXIT trap 進行登出
}

# 滾動更新
"${compose[@]}" up -d --remove-orphans \
  || fail_deploy "compose up 失敗（通常是 migrate 失敗），近期 migrate/app log："

# 6. 等 app 通過 healthcheck（最多 ~3 分鐘）
app_cid="$("${compose[@]}" ps -q app)"
for attempt in $(seq 1 "$max_attempts"); do
  state="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$app_cid" 2>/dev/null || echo missing)"
  case "$state" in
    healthy)
      echo "app 已就緒"
      break
      ;;
    unhealthy)
      fail_deploy "app healthcheck 失敗，近期 migrate/app log："
      ;;
  esac
  if [[ "$attempt" -eq "$max_attempts" ]]; then
    fail_deploy "等待 app 就緒逾時（migration 失敗也會卡在此），近期 migrate/app log："
  fi
  sleep 5
done

# 7. 收尾：清理舊映像，避免磁碟被歷次 SHA tag 塞爆
echo "開始清理舊映像..."
docker image prune -f

keep_versions=5
image_ref="$(docker inspect -f '{{.Config.Image}}' "$app_cid" 2>/dev/null || true)"
image_repo="${image_ref%:*}"
if [[ -n "$image_repo" ]]; then
  old_ids="$(docker images "$image_repo" --format '{{.ID}}' | awk '!seen[$0]++' | tail -n +"$((keep_versions + 1))")"
  if [[ -n "$old_ids" ]]; then
    echo "$old_ids" | xargs docker rmi >/dev/null 2>&1 || true
  fi
fi

echo "部署完成 IMAGE_TAG=$IMAGE_TAG"
# 這裡結束後，Bash 會自動執行 trap 內容，印出 "正在清理登入憑證..." 並登出。
