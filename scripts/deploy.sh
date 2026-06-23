#!/usr/bin/env bash
# 正式環境部署腳本（推送式），在 EC2 主機上執行。
#
# 由 .github/workflows/ci-cd.yml 透過 `ssh ... VAR=.. 'bash -s' < scripts/deploy.sh`
# 以 stdin 餵入執行。設定檔（docker-compose.prod.yml、nginx/nginx.conf）由 CI 在
# 本腳本執行「之前」用 scp 推到 deploy_dir，因此 EC2 完全不需要 git 存取。
#
# EC2 不 build 映像，只從 GHCR pull CI 已 build 好的映像。
#
# 由呼叫端透過環境變數帶入：
#   IMAGE_TAG    要部署的映像標籤（CI 帶該次 commit 的 SHA）
#   GHCR_ACTOR   GHCR 登入帳號（github.actor）
#   GHCR_TOKEN   GHCR 登入權杖（GITHUB_TOKEN，僅在本次部署期間有效）
#
# 前置（使用者在主機一次性準備，CI 不碰）：
#   - 已安裝 docker 與 docker compose plugin，且部署帳號在 docker 群組。
#   - deploy_dir 下已放好填妥機密的 .env.prod（永不進 git，CI 也不會覆寫它）。
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

# 1. 機密設定檔必須存在（由使用者一次性放置，CI 不碰）。
if [[ ! -f "$env_file" ]]; then
  echo "錯誤：找不到 $deploy_dir/$env_file，請先在主機放好機密設定再部署。" >&2
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

compose=(docker compose -f "$compose_file" --env-file "$env_file")

# 3. 登入 GHCR 並拉取本次要部署的映像（migrate 與 app 共用同一個 IMAGE_TAG）。
echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_ACTOR" --password-stdin
"${compose[@]}" pull

# 4. 滾動更新前，先記下目前線上的 app 映像，供健康檢查失敗時回滾（首次部署時為空）。
prev_app_cid="$("${compose[@]}" ps -q app 2>/dev/null || true)"
prev_app_image=""
if [[ -n "$prev_app_cid" ]]; then
  prev_app_image="$(docker inspect -f '{{.Config.Image}}' "$prev_app_cid" 2>/dev/null || true)"
fi

# 健康檢查最長等待：max_attempts × 5s。改等待時間只動這一個常數（避免兩處數字不同步）。
max_attempts=36

# 部署失敗收尾：印 log → 回滾 app 至前一版映像 → 讓部署失敗。集中於一處，兩個失敗分支共用。
rollback_app() {
  if [[ -z "$prev_app_image" ]]; then
    echo "無前一版 app 映像可回滾（可能是首次部署），請手動處理。" >&2
    return
  fi
  local prev_tag="${prev_app_image##*:}"
  echo "回滾 app 至前一版 IMAGE_TAG=$prev_tag" >&2
  # 注意：migrate 可能已套用新 schema；若失敗主因是不可逆的 migration，回滾舊 app 後仍需人工確認。
  if ! IMAGE_TAG="$prev_tag" "${compose[@]}" up -d --remove-orphans >&2; then
    echo "回滾失敗，請手動處理。" >&2
  fi
}

fail_deploy() {
  echo "$1" >&2
  "${compose[@]}" logs --tail 100 migrate app >&2 || true
  rollback_app
  exit 1
}

# compose 的 depends_on 已保證 postgres healthy → migrate 跑完成功 → app 才起。
"${compose[@]}" up -d --remove-orphans

# 5. 等 app 通過 healthcheck（最多 ~3 分鐘）；逾時或 unhealthy 即印 log、回滾、讓部署失敗。
app_cid="$("${compose[@]}" ps -q app)"
# app 容器根本沒建立（常見主因：migrate 失敗，app 的 depends_on 未滿足）→ 立即失敗，
# 不要進健康檢查迴圈乾等 ~3 分鐘還回報看不出真因的逾時。
if [[ -z "$app_cid" ]]; then
  fail_deploy "app 容器未建立（通常是 migrate 失敗），近期 migrate/app log："
fi
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

# 6. 收尾：登出 GHCR + 清理舊映像，避免磁碟被歷次 SHA tag 塞爆。
#    保留依「版本數」而非「時間」：始終留本專案映像最近 keep_versions 個版本（含目前運行
#    版 + 回滾窗），刪更舊的。用時間（until=168h）的問題是久未部署時，上一個可回滾版本可能
#    已超過保留窗被誤刪；改用版本數可確保回滾目標一定還在本機。
docker logout ghcr.io >/dev/null 2>&1 || true
docker image prune -f   # 先清 dangling（被新版取代的無 tag 舊層）

keep_versions=5
# 從目前運行的 app 容器反查本專案映像 repo（避免在腳本裡硬編 registry 路徑）。
image_ref="$(docker inspect -f '{{.Config.Image}}' "$app_cid" 2>/dev/null || true)"
image_repo="${image_ref%:*}"
if [[ -n "$image_repo" ]]; then
  # 取本專案映像、去重 image ID、保留最新 keep_versions 個，其餘（含其 tag）刪除。
  old_ids="$(docker images "$image_repo" --format '{{.ID}}' | awk '!seen[$0]++' | tail -n +"$((keep_versions + 1))")"
  if [[ -n "$old_ids" ]]; then
    # 目前運行版被容器引用，docker rmi 會失敗 → || true 容忍，不影響部署結果。
    echo "$old_ids" | xargs docker rmi >/dev/null 2>&1 || true
  fi
fi

echo "部署完成 IMAGE_TAG=$IMAGE_TAG"
