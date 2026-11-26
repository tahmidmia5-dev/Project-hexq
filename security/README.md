# Security self-test kit (Termux)

Application-layer security checks for **your own** web app
(`https://project-hex.onrender.com/app`), designed to run from a
**non-rooted Android phone** via Termux and to stay friendly to
Render-hosted apps.

> ⚠️ **Authorized use only.** Run this *only* against a domain you own or
> have explicit written permission to test. Scanning other people's
> systems is illegal in most countries. This kit is deliberately
> non-destructive and rate-limited.

## Why no port scanning?

Your app runs on **Render**, a managed platform. You own your *application*
(the code, routes, auth) — but **not** the server or network. So this kit
only does HTTP application-layer checks and never runs `nmap`/port scans or
stress tests against Render's infrastructure (which would breach their ToS
and tell you nothing useful anyway — TLS and ports are Render's to manage).

## 1. Install Termux

Get Termux from **F-Droid** (the Play Store build is outdated):
https://f-droid.org/packages/com.termux/

## 2. One-time setup

```bash
pkg update && pkg upgrade -y

# Core tools (pkg install is all-or-nothing, so install the reliable ones first)
pkg install -y git curl openssl-tool dnsutils python

# ffuf is optional (only needed for --discover) and lives in tur-repo:
pkg install -y tur-repo && pkg install -y ffuf

termux-setup-storage   # optional, for saving reports to your phone
```

> **Note on Termux package names:** use `dnsutils` (not `bind-tools`) for
> `dig`, and `ffuf` only installs after adding `tur-repo`. `pkg install`
> aborts entirely if any single name is invalid, so install core tools
> separately from optional ones.

Optional heavier tools:
```bash
# nuclei (template-based vuln scanner) — needs Go
pkg install -y golang
go install -v github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest
# add Go bin to PATH (persist in ~/.bashrc):
echo 'export PATH=$PATH:$HOME/go/bin' >> ~/.bashrc && source ~/.bashrc
nuclei -update-templates

# sqlmap (only test YOUR OWN forms/params)
pip install sqlmap
```

## 3. Get this kit onto your phone

```bash
git clone https://github.com/tahmidmia5-dev/Project-hexq.git
cd Project-hexq/security
chmod +x webcheck.sh
```
(Or `git pull` if you already cloned it.)

## 4. Run the checks

Baseline (quiet, safe — start here):
```bash
./webcheck.sh https://project-hex.onrender.com/app
```

With content discovery (noisier, rate-limited to 10 req/s):
```bash
./webcheck.sh https://project-hex.onrender.com/app --discover
```

Save a report to review later:
```bash
./webcheck.sh https://project-hex.onrender.com/app --discover | tee ~/storage/shared/webcheck-$(date +%F).txt
```

## What each section tells you

| # | Section | Look for |
|---|---|---|
| 1 | Reachability | Right final URL, sane redirect count |
| 2 | Security headers | `[!] MISSING` lines — add those headers |
| 3 | Cookie flags | Session cookies must have HttpOnly + Secure + SameSite |
| 4 | TLS summary | Cert validity dates (Render manages this) |
| 5 | CORS | A reflected `evil.example` Origin is a real bug |
| 6 | Sensitive files | Any `[-] EXPOSED` is urgent (esp. `.env`, `.git`) |
| 7 | Content discovery | Unexpected admin/debug/api endpoints |
| 8 | nuclei | Known CVEs / misconfigs |

## Fixing what you find (Render apps)

- **Missing security headers** → set them in your app framework
  (Express: `helmet`; Flask: `flask-talisman`; or Render response headers).
- **Cookies missing flags** → set `HttpOnly`, `Secure`, `SameSite=Lax/Strict`
  on every session/auth cookie.
- **Exposed `.env` / `.git`** → never serve your project root statically;
  move secrets to Render **Environment** settings and rotate any leaked key.
- **CORS reflection** → allowlist specific origins; never combine
  `Allow-Origin: *` reflection with `Allow-Credentials: true`.

## Manual checks the script can't automate

- **Auth / IDOR**: log in as user A, try to read user B's data by changing
  an ID in the URL or request body.
- **Rate limiting**: can you brute-force login or spam an endpoint?
- **Input validation**: test your own forms for XSS (`"><script>` in a field
  you control) and SQLi (use `sqlmap` only on *your* parameters).
- **Secrets in client JS**: view-source / check bundled JS for leaked API keys.

## Responsible testing checklist

- [ ] It's **your** domain (`project-hex.onrender.com`).
- [ ] You're testing the **app layer only**, not Render's infra.
- [ ] You keep the rate gentle (defaults already do).
- [ ] You fix findings and **rotate any exposed secret** immediately.
