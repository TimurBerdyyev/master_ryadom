#!/usr/bin/env bash
# End-to-end check of a running stack through nginx — the same path real users take.
#   ./deploy/smoke_test.sh                       (local docker compose: http://localhost:8080)
#   ./deploy/smoke_test.sh https://your-domain   (a deployed server)
# Uses throwaway test data; needs EMAIL_PROVIDER=console to read the sign-up code (skipped otherwise).
set -euo pipefail
BASE="${1:-http://localhost:8080}"
API="$BASE/api"
pass() { echo "  ✓ $1"; }
fail() { echo "  ✗ $1"; exit 1; }
json() { python3 -c "import sys,json; print(json.load(sys.stdin)$1)"; }

echo "Проверка $BASE"
curl -fsS "$API/health" | grep -q '"ok"' && pass "API отвечает (/api)" || fail "API /health"
curl -fsS "$BASE/" | grep -q "Мастер рядом" && pass "сайт открывается" || fail "главная страница"
curl -fsS "$BASE/js/icons.js" >/dev/null && pass "статика отдаётся" || fail "статика"
CATS=$(curl -fsS "$API/categories" | json '.__len__()')
[ "$CATS" -gt 0 ] && pass "категории в базе: $CATS (миграции и сид прошли)" || fail "категории"

STAMP=$(date +%s)
EMAIL="smoke$STAMP@example.com"
PHONE="+99670$(printf '%07d' $((STAMP % 10000000)))"
CODE=$(curl -fsS -X POST "$API/auth/send-code" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"purpose\":\"register\"}" | json '.get("debug_code") or ""')
if [ -z "$CODE" ]; then
  pass "письмо с кодом отправлено (реальная почта — регистрацию проверьте вручную)"
  exit 0
fi
TOKEN=$(curl -fsS -X POST "$API/auth/register" -H 'Content-Type: application/json' \
  -d "{\"name\":\"Smoke\",\"phone\":\"$PHONE\",\"email\":\"$EMAIL\",\"password\":\"smoke-pass-1\",\"code\":\"$CODE\",\"city\":\"Бишкек\",\"accept_agreement\":true}" \
  | json '["access_token"]')
pass "регистрация мастера с кодом из письма и договором"
curl -fsS "$API/masters/me" -H "Authorization: Bearer $TOKEN" | grep -q '"agreement_accepted":true' \
  && pass "договор мастера принят" || fail "договор мастера"
# Clients have no account: a request returns a private token.
CLIENT_PHONE="+99677$(printf '%07d' $((STAMP % 10000000)))"
REQ=$(curl -fsS -X POST "$API/requests" -H 'Content-Type: application/json' \
  -d "{\"name\":\"Smoke\",\"phone\":\"$CLIENT_PHONE\",\"category_id\":1,\"city\":\"Бишкек\",\"description\":\"smoke test\"}")
REQ_TOKEN=$(echo "$REQ" | json '["token"]')
pass "заявка без регистрации создана (#$(echo "$REQ" | json '["request"]["id"]'))"
# A tiny valid PNG: checks that the non-root backend can write to the uploads volume.
printf '\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82' > /tmp/smoke.png
PHOTO=$(curl -fsS -X POST "$API/requests/$REQ_TOKEN/photos" -F "files=@/tmp/smoke.png;type=image/png" | json '[0]["url"]')
curl -fsS "$API$PHOTO" -o /dev/null && pass "фото загружено и отдаётся ($PHOTO)" || fail "фото"
AVATAR=$(curl -fsS -X POST "$API/masters/me/avatar" -H "Authorization: Bearer $TOKEN" -F "file=@/tmp/smoke.png;type=image/png" | json '["user"]["photo"]')
curl -fsS "$API$AVATAR" -o /dev/null && pass "фото мастера загружено ($AVATAR)" || fail "фото мастера"
echo "Всё работает."
