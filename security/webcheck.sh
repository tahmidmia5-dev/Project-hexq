#!/data/data/com.termux/files/usr/bin/env bash
#
# webcheck.sh — safe, application-layer security recon for YOUR OWN web app.
# Built for non-rooted Termux, friendly to Render-hosted apps.
#
#   ETHICAL USE ONLY. Only run this against a domain you own or are
#   explicitly authorized in writing to test. You are responsible for
#   how you use it.
#
# Usage:
#   ./webcheck.sh https://project-hex.onrender.com/app
#   ./webcheck.sh https://project-hex.onrender.com/app --discover   # + content discovery (noisier)
#
# What it does (all non-destructive):
#   1. Reachability + redirect chain
#   2. Security header audit
#   3. Cookie flag audit
#   4. TLS summary (via openssl)
#   5. CORS misconfig probe
#   6. Sensitive-file exposure check (.env, .git, backups, etc.)
#   7. Optional: gentle content discovery (needs ffuf, rate-limited)
#   8. Optional: nuclei scan (if installed)
#
set -uo pipefail

TARGET="${1:-}"
MODE="${2:-}"
if [[ -z "$TARGET" ]]; then
  echo "Usage: $0 <https://your-app-url> [--discover]"
  exit 1
fi

# --- derive base URL + host ---
PROTO="$(echo "$TARGET" | grep -oE '^https?')"; PROTO="${PROTO:-https}"
HOST="$(echo "$TARGET" | sed -E 's#^https?://##; s#/.*##')"
BASE="${PROTO}://${HOST}"
UA="webcheck-selftest/1.0 (+authorized security review)"
RATE_DELAY=0.4   # seconds between discovery requests — keep it gentle on Render

c() { printf '\033[%sm%s\033[0m' "$1" "$2"; }       # color
hdr() { echo; c "1;36" "══ $1"; echo; echo; }       # section header
ok()   { c "1;32" "  [+] "; echo "$1"; }
warn() { c "1;33" "  [!] "; echo "$1"; }
bad()  { c "1;31" "  [-] "; echo "$1"; }
info() { c "0;37" "  [ ] "; echo "$1"; }

need() { command -v "$1" >/dev/null 2>&1; }

echo
c "1;35" "  webcheck — authorized self-test"; echo
info "Target : $TARGET"
info "Base   : $BASE"
info "Host   : $HOST"
info "Time   : $(date -u +%FT%TZ)"

# =====================================================================
hdr "1. Reachability & redirect chain"
curl -sS -A "$UA" --max-time 25 -o /dev/null -w \
  '  final_url=%{url_effective}\n  http_code=%{http_code}\n  redirects=%{num_redirects}\n  time_total=%{time_total}s\n  server_ip=%{remote_ip}\n' \
  -L "$TARGET" 2>&1 || bad "Could not reach target."

# =====================================================================
hdr "2. Security header audit"
HEADERS="$(curl -sS -A "$UA" --max-time 25 -D - -o /dev/null -L "$TARGET" 2>/dev/null)"
echo "$HEADERS" | sed 's/^/    /'
echo
check_hdr() {
  local name="$1" advice="$2"
  if echo "$HEADERS" | grep -iq "^$name:"; then
    ok "$name present"
  else
    warn "$name MISSING — $advice"
  fi
}
check_hdr "strict-transport-security" "add HSTS: max-age=63072000; includeSubDomains; preload"
check_hdr "content-security-policy"   "add a CSP to blunt XSS"
check_hdr "x-content-type-options"    "add: nosniff"
check_hdr "x-frame-options"           "add DENY/SAMEORIGIN (or frame-ancestors in CSP) to stop clickjacking"
check_hdr "referrer-policy"           "add: strict-origin-when-cross-origin"
check_hdr "permissions-policy"        "lock down camera/mic/geo etc."
# Info-leak headers that SHOULD be absent:
for leak in "server" "x-powered-by" "x-aspnet-version"; do
  if echo "$HEADERS" | grep -iq "^$leak:"; then
    val="$(echo "$HEADERS" | grep -i "^$leak:" | head -1 | cut -d: -f2- | tr -d '\r' | sed 's/^ //')"
    warn "$leak exposed: '$val' (version/tech disclosure — consider hiding)"
  fi
done

# =====================================================================
hdr "3. Cookie flag audit"
COOKIES="$(echo "$HEADERS" | grep -i '^set-cookie:')"
if [[ -z "$COOKIES" ]]; then
  info "No cookies set on this response."
else
  echo "$COOKIES" | while IFS= read -r line; do
    name="$(echo "$line" | sed -E 's/^set-cookie: *//I; s/=.*//' | tr -d '\r')"
    flags=""
    echo "$line" | grep -iq "httponly"  || flags+="HttpOnly "
    echo "$line" | grep -iq "secure"    || flags+="Secure "
    echo "$line" | grep -iq "samesite"  || flags+="SameSite "
    if [[ -n "$flags" ]]; then
      warn "cookie '$name' missing: $flags"
    else
      ok "cookie '$name' well-flagged"
    fi
  done
fi

# =====================================================================
hdr "4. TLS summary"
if need openssl; then
  echo | openssl s_client -connect "${HOST}:443" -servername "$HOST" 2>/dev/null \
    | openssl x509 -noout -subject -issuer -dates 2>/dev/null | sed 's/^/    /' \
    || warn "TLS inspection failed (Render terminates TLS; usually fine)."
else
  info "openssl not installed — skipping (pkg install openssl-tool)."
fi

# =====================================================================
hdr "5. CORS misconfiguration probe"
CORS="$(curl -sS -A "$UA" --max-time 20 -H "Origin: https://evil.example" -D - -o /dev/null "$TARGET" 2>/dev/null | grep -i '^access-control-allow-')"
if [[ -z "$CORS" ]]; then
  ok "No CORS headers reflected to a foreign Origin."
else
  echo "$CORS" | sed 's/^/    /'
  if echo "$CORS" | grep -iq "evil.example"; then
    bad "Origin is REFLECTED — if Allow-Credentials is true this is exploitable."
  elif echo "$CORS" | grep -iq "allow-origin: \*"; then
    warn "Wildcard CORS — fine for public APIs, risky if responses are sensitive."
  fi
fi

# =====================================================================
hdr "6. Sensitive-file exposure"
SENSITIVE=(
  ".env" ".git/config" ".git/HEAD" ".gitignore"
  "config.json" "config.js" "package.json" "composer.json"
  "backup.zip" "backup.sql" "db.sqlite3" "dump.sql"
  ".DS_Store" "robots.txt" "security.txt" ".well-known/security.txt"
  "server-status" "debug" "phpinfo.php" ".htaccess"
)
for path in "${SENSITIVE[@]}"; do
  code="$(curl -sS -A "$UA" --max-time 15 -o /dev/null -w '%{http_code}' "${BASE}/${path}" 2>/dev/null)"
  case "$code" in
    200) bad "EXPOSED ${BASE}/${path}  (HTTP 200) — review immediately" ;;
    401|403) info "protected ${path} (HTTP $code)" ;;
    301|302) info "redirect ${path} (HTTP $code)" ;;
    *) : ;;  # 404 etc. — stay quiet
  esac
  sleep "$RATE_DELAY"
done
ok "Sensitive-file sweep complete."

# =====================================================================
if [[ "$MODE" == "--discover" ]]; then
  hdr "7. Content discovery (gentle)"
  WL="$(dirname "$0")/wordlists/common-small.txt"
  if need ffuf && [[ -f "$WL" ]]; then
    info "Running ffuf, rate-limited to 10 req/s, filtering 404s…"
    ffuf -u "${BASE}/FUZZ" -w "$WL" -rate 10 -t 10 -mc 200,201,204,301,302,307,401,403 \
      -H "User-Agent: $UA" -s 2>/dev/null | sed 's/^/    /'
    ok "Discovery complete."
  else
    warn "ffuf or wordlist missing. Install: pkg install ffuf"
  fi
else
  hdr "7. Content discovery"
  info "Skipped. Re-run with --discover to enable (noisier)."
fi

# =====================================================================
hdr "8. nuclei (template-based vuln scan)"
if need nuclei; then
  info "Running nuclei with safe severity (info,low,medium) and rate limit…"
  nuclei -u "$TARGET" -severity info,low,medium,high,critical \
    -rate-limit 20 -timeout 10 -silent -H "User-Agent: $UA" 2>/dev/null | sed 's/^/    /'
  ok "nuclei complete."
else
  info "nuclei not installed. Install via Go (see security/README.md)."
fi

echo
c "1;35" "  Done. Review [!] and [-] lines first."; echo
echo
