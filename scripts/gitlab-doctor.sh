#!/usr/bin/env bash
# =============================================================================
# gitlab-doctor.sh — diagnóstico READ-ONLY do ambiente GitLab (glab + git)
# -----------------------------------------------------------------------------
# - Valida instalação do glab/git, autenticação (glab auth status), scopes e
#   validade do token (API personal_access_tokens/self, só leitura), permissões
#   do ficheiro de configuração, credential helper do git e repositório local.
# - Imprime uma tabela de estado. NUNCA imprime valores de token: só presença,
#   host/utilizador, nomes de scopes e datas.
# - Erros no formato:  "Erro: <o quê> — Solução: <o que fazer a seguir>"
# - Modos: (default) diagnóstico vivo | --selftest | --help | --version
#   Opção: --host <hostname> (default: GITLAB_HOST ou gitlab.com)
# =============================================================================
set -u

VERSION="1.0.0"
HOST="${GITLAB_HOST:-gitlab.com}"
HOST="${HOST#https://}"; HOST="${HOST#http://}"; HOST="${HOST%%/*}"
EXPIRY_WARN_DAYS=14

# ---------------------------------------------------------------- formatação --
BOLD=""; RESET=""
if [ -t 1 ]; then BOLD=$'\033[1m'; RESET=$'\033[0m'; fi

say()  { printf '%s\n' "$*"; }
err()  { printf '%s\n' "$*" >&2; }

# Mensagem instrutiva canónica. Uso: die <exit> "<o quê>" "<solução>"
die() {
  local code="$1" what="$2" how="$3"
  err "Erro: ${what} — Solução: ${how}"
  exit "$code"
}

# ------------------------------------------------------- redação de segredos --
# Qualquer valor que pareça token GitLab (ou credencial em URL) vira [VALOR OCULTO].
redact() {
  sed -E \
    -e 's/(glpat|gloas|gldt|glrt|glrtr|glcbt|glptt|glft|glimt|glagent|glsoat|glffct|glwt|gltst)-[A-Za-z0-9._-]{8,}/[VALOR OCULTO]/g' \
    -e 's/GR1348941[A-Za-z0-9_-]{8,}/[VALOR OCULTO]/g' \
    -e 's#(https?://)[^/@[:space:]:]+:[^/@[:space:]]+@#\1[VALOR OCULTO]@#g' \
    -e 's/(Token[^:]*:[[:space:]]+)[^[:space:]]+/\1[VALOR OCULTO]/g'
}

# ------------------------------------------------------- saída em tabela ------
LINE="+------------------------+--------------+----------------------------------------------------+"
# Padding por CARACTERES (o printf do bash conta bytes e desalinha acentos). Trunca ao limite.
pad() { local s="${1:0:$2}"; printf '%s%*s' "$s" $(( $2 - ${#s} )) ''; }
row()  { printf '| %s | %s | %s |\n' "$(pad "$1" 22)" "$(pad "$2" 12)" "$(pad "$3" 50)"; }
table_header() {
  printf '%s\n' "${BOLD}=== gitlab-doctor v${VERSION} — diagnóstico READ-ONLY (${HOST}) ===${RESET}"
  printf '%s\n' "$LINE"
  row "VERIFICAÇÃO" "ESTADO" "DETALHE"
  printf '%s\n' "$LINE"
}
table_footer() { printf '%s\n' "$LINE"; }

OK="OK"; WARN="AVISO"; FAIL="FALHA"; SKIP="N/D"

glab_q() {  # glab sem cor, sem prompts, sem verificação de updates
  NO_COLOR=1 GLAB_NO_PROMPT=1 GLAB_CHECK_UPDATE=false glab "$@"
}

# ============================================================ verificações ====

check_tool() {  # $1=binário  $2=rótulo
  local bin="$1" label="$2" ver
  if command -v "$bin" >/dev/null 2>&1; then
    ver="$("$bin" --version 2>/dev/null | head -n 1 | redact)"
    row "$label" "$OK" "${ver:-instalado} · $(command -v "$bin")"
    return 0
  fi
  row "$label" "$FAIL" "binário '$bin' não encontrado no PATH"
  return 1
}

check_env_tokens() {
  local names="GITLAB_TOKEN GITLAB_ACCESS_TOKEN OAUTH_TOKEN CI_JOB_TOKEN"
  local found="" n
  for n in $names; do
    if [ -n "$(eval "printf '%s' \"\${$n:-}\"")" ]; then found="$found $n"; fi
  done
  if [ -n "$found" ]; then
    row "Variáveis de token" "$WARN" "definidas (têm precedência sobre o config):$found"
  else
    row "Variáveis de token" "$SKIP" "nenhuma (usa o token guardado pelo glab)"
  fi
  if [ -n "${GITLAB_HOST:-}" ]; then
    row "GITLAB_HOST" "$OK" "$GITLAB_HOST"
  else
    row "GITLAB_HOST" "$SKIP" "não definido (usa gitlab.com)"
  fi
  return 0
}

# Extrai informação segura de `glab auth status` (stdin):
# "hosts=host/utilizador,…" "fonte=…" "armazenamento=…"
parse_auth_status() {
  awk '
    /Logged in to / {
      host = ""; user = ""; src = ""
      for (i = 1; i <= NF; i++) {
        if ($i == "to") host = $(i+1)
        if ($i == "as") user = $(i+1)
      }
      if (match($0, /\([^)]*\)/)) src = substr($0, RSTART+1, RLENGTH-2)
      n++; hosts = hosts (hosts ? "," : "") host "/" user
      if (src != "") fonte = src
    }
    /plaintext/ { armazenamento = "ficheiro (texto simples)" }
    /keyring/ && !/To store/ { if (armazenamento == "") armazenamento = "keyring do sistema" }
    END {
      printf "sessoes=%d\n", n + 0
      printf "hosts=%s\n", (hosts ? hosts : "-")
      printf "fonte=%s\n", (fonte ? fonte : "-")
      printf "armazenamento=%s\n", (armazenamento ? armazenamento : "-")
    }
  '
}

check_auth() {
  local out code summary sessoes hosts armaz
  if ! command -v glab >/dev/null 2>&1; then
    row "Autenticação glab" "$SKIP" "glab indisponível — ver linha acima"
    return 2
  fi
  out="$(glab_q auth status --hostname "$HOST" 2>&1 | redact)"
  code=$?
  summary="$(printf '%s\n' "$out" | parse_auth_status)"
  sessoes="$(printf '%s\n' "$summary" | awk -F= '/^sessoes=/{print $2}')"
  hosts="$(printf '%s\n' "$summary" | awk -F= '/^hosts=/{print $2}')"
  armaz="$(printf '%s\n' "$summary" | awk -F= '/^armazenamento=/{print $2}')"
  if [ "$code" -eq 0 ] && [ "${sessoes:-0}" -ge 1 ]; then
    row "Autenticação glab" "$OK" "$hosts"
    row "Armazenamento token" "$([ "$armaz" = "ficheiro (texto simples)" ] && echo "$WARN" || echo "$OK")" "$armaz"
    return 0
  fi
  if printf '%s' "$out" | grep -qiE 'not logged|no token|401|unauthorized|has not been authenticated|glab auth login'; then
    row "Autenticação glab" "$FAIL" "sem sessão válida em $HOST"
    return 1
  fi
  row "Autenticação glab" "$WARN" "$(printf '%s' "$out" | grep -v '^\s*$' | head -n 1)"
  return 1
}

# Scopes e validade do token (PAT/token de grupo/projeto). Só leitura.
check_token_info() {
  local json info
  command -v glab >/dev/null 2>&1 || { row "Scopes do token" "$SKIP" "glab indisponível"; return 0; }
  json="$(glab_q api --hostname "$HOST" personal_access_tokens/self 2>/dev/null)" || {
    row "Scopes do token" "$SKIP" "não é PAT (OAuth/job token) ou sem read_api"
    return 0
  }
  info="$(printf '%s' "$json" | python3 -c '
import json, sys, datetime as dt
d = json.load(sys.stdin)
exp = d.get("expires_at")
dias = ""
if exp:
    dias = (dt.date.fromisoformat(exp) - dt.date.today()).days
print("nome=" + str(d.get("name") or "-"))
print("scopes=" + ",".join(d.get("scopes") or []))
print("expira=" + str(exp or "nunca"))
print("dias=" + str(dias))
print("ativo=" + str(bool(d.get("active")) and not d.get("revoked")))
' 2>/dev/null)" || { row "Scopes do token" "$WARN" "resposta inesperada da API"; return 0; }
  local nome scopes expira dias ativo estado
  nome="$(printf '%s\n' "$info" | awk -F= '/^nome=/{print $2}')"
  scopes="$(printf '%s\n' "$info" | awk -F= '/^scopes=/{print $2}')"
  expira="$(printf '%s\n' "$info" | awk -F= '/^expira=/{print $2}')"
  dias="$(printf '%s\n' "$info" | awk -F= '/^dias=/{print $2}')"
  ativo="$(printf '%s\n' "$info" | awk -F= '/^ativo=/{print $2}')"
  row "Token (nome)" "$([ "$ativo" = "True" ] && echo "$OK" || echo "$FAIL")" "$nome · ativo=$ativo"
  estado="$OK"
  case ",$scopes," in *,api,*|*,write_repository,*) ;; *) estado="$WARN" ;; esac
  row "Scopes do token" "$estado" "$scopes"
  estado="$OK"
  if [ -n "$dias" ] && [ "$dias" -lt "$EXPIRY_WARN_DAYS" ] 2>/dev/null; then estado="$WARN"; fi
  row "Validade do token" "$estado" "expira $expira${dias:+ (faltam $dias dias)}"
  return 0
}

check_config_perms() {
  local dir="${GLAB_CONFIG_DIR:-$HOME/.config/glab-cli}" f pf pd
  f="$dir/config.yml"
  if [ ! -f "$f" ]; then row "Config do glab" "$SKIP" "$f não existe"; return 0; fi
  pf="$(stat -c '%a' "$f" 2>/dev/null)"; pd="$(stat -c '%a' "$dir" 2>/dev/null)"
  if [ "$pf" = "600" ] && [ "$pd" = "700" ]; then
    row "Config do glab" "$OK" "$f (0$pf, pasta 0$pd)"
  else
    row "Config do glab" "$WARN" "permissões 0$pf/0$pd — corrigir: chmod 600 f; chmod 700 dir"
  fi
  return 0
}

check_credential_helper() {
  local url="https://${HOST}" glob loc
  glob="$(git config --global --get-regexp '^credential\.' 2>/dev/null | grep -iE "helper" | grep -iE "^credential\.helper |${HOST}" | awk '{ $1=""; print substr($0,2) }' | grep -v '^$' | tail -n 1)"
  row "Helper git (global)" "$( [ -n "$glob" ] && echo "$OK" || echo "$WARN")" "${glob:-nenhum — git pede credenciais}"
  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    loc="$(git config --local --get-all "credential.${url}.helper" 2>/dev/null | grep -v '^$' | tail -n 1)"
    if printf '%s' "$loc" | grep -q 'glab auth git-credential'; then
      row "Helper git (este repo)" "$OK" "glab (ligado por gitlab-setup-git.sh --repo)"
    else
      row "Helper git (este repo)" "$SKIP" "herda o global — ligar: gitlab-setup-git.sh --repo ."
    fi
  fi
  return 0
}

check_git_repo() {
  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    local br remote
    br="$(git branch --show-current 2>/dev/null)"
    remote="$(git remote get-url origin 2>/dev/null | redact | sed -E 's#(https?://)[^@/]+@#\1#')"
    row "Repositório local" "$OK" "branch=${br:-?} · ${remote:-sem origin}"
  else
    row "Repositório local" "$SKIP" "cwd não é um repositório git"
  fi
  return 0
}

# ================================================================= selftest ====
run_selftest() {
  local failed=0 t parsed

  # tokens sintéticos construídos em runtime — nunca escritos como literais
  local fake_pat fake_deploy fake_runner fake_url
  fake_pat="glpat-$(printf 'A%.0s' $(seq 1 26)).01.abc123"
  fake_deploy="gldt-$(printf 'B%.0s' $(seq 1 20))"
  fake_runner="glrt-$(printf 'C%.0s' $(seq 1 20))"
  fake_url="https://oauth2:$(printf 'D%.0s' $(seq 1 20))@gitlab.com/g/p.git"

  t="redação de PAT (glpat-, formato com .01.)"
  if printf '%s\n' "x $fake_pat y" | redact | grep -q 'glpat-'; then say "FAIL — $t"; failed=1; else say "PASS — $t"; fi

  t="redação de deploy/runner tokens (gldt-, glrt-)"
  if printf '%s\n' "$fake_deploy $fake_runner" | redact | grep -qE 'gldt-|glrt-'; then say "FAIL — $t"; failed=1; else say "PASS — $t"; fi

  t="redação de credencial em URL"
  if printf '%s\n' "$fake_url" | redact | grep -q 'DDDDDDDD'; then say "FAIL — $t"; failed=1; else say "PASS — $t"; fi

  t="redação do valor de 'Token ...:' (mascarado pelo glab)"
  if printf '%s\n' "  ✓ Token found: ****masked****" | redact | grep -q 'masked'; then say "FAIL — $t"; failed=1; else say "PASS — $t"; fi

  t="parsing de glab auth status (host/utilizador/armazenamento)"
  parsed="$(printf '%s\n' \
    "gitlab.com" \
    "  ✓ Logged in to gitlab.com as octocat (/home/x/.config/glab-cli/config.yml)" \
    "  ✓ Git operations for gitlab.com configured to use https protocol." \
    "  ✓ Token [VALOR OCULTO] in configuration file (plaintext): ****" | parse_auth_status)"
  if printf '%s' "$parsed" | grep -q '^sessoes=1$' \
     && printf '%s' "$parsed" | grep -q 'hosts=gitlab.com/octocat' \
     && printf '%s' "$parsed" | grep -q 'texto simples'; then
    say "PASS — $t"
  else
    say "FAIL — $t :: $parsed"; failed=1
  fi

  t="tabela renderiza sem colisão"
  if row "teste" "$OK" "detalhe qualquer" >/dev/null 2>&1; then say "PASS — $t"; else say "FAIL — $t"; failed=1; fi

  if [ "$failed" -eq 0 ]; then say "selftest: TODOS OS TESTES PASSARAM (offline)"; return 0; fi
  say "selftest: HÁ FALHAS"
  return 1
}

# ===================================================================== main ====
usage() {
  cat <<'EOF'
gitlab-doctor.sh — diagnóstico READ-ONLY do ambiente GitLab (glab + git)

USO:
  bash gitlab-doctor.sh                    # diagnóstico vivo (tabela) de gitlab.com
  bash gitlab-doctor.sh --host <hostname>  # outra instância (ou GITLAB_HOST)
  bash gitlab-doctor.sh --selftest         # autoteste offline da lógica do script
  bash gitlab-doctor.sh --help

O QUE VERIFICA (nunca escreve, nunca muta estado):
  - presença/versão de glab e git
  - variáveis de token (APENAS presença — valores nunca são lidos) e GITLAB_HOST
  - autenticação via `glab auth status` (host, utilizador, onde está guardado)
  - nome, scopes e validade do token (GET personal_access_tokens/self)
  - permissões do ~/.config/glab-cli/config.yml (0600) e da pasta (0700)
  - credential helper do git (global e do repo atual) e repositório local

SAÍDA: tabela + código de saída
  0 = ambiente pronto | 1 = problema de instalação/estado | 4 = não autenticado
  (mensagens de erro no formato: "Erro: <o quê> — Solução: <o que fazer>")

SEGURANÇA: valores de token são sempre redigidos ([VALOR OCULTO]).
EOF
}

main() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --help|-h) usage; exit 0 ;;
      --version) say "gitlab-doctor.sh ${VERSION}"; exit 0 ;;
      --selftest) run_selftest; exit $? ;;
      --host) [ $# -ge 2 ] || die 1 "falta o valor de --host" "indicar o hostname, ex.: --host gitlab.com"
              HOST="$2"; shift ;;
      *) die 1 "argumento desconhecido: '$1'" "correr 'bash gitlab-doctor.sh --help' para ver os modos suportados" ;;
    esac
    shift
  done

  table_header
  local status_glab=0 status_auth=0

  check_tool glab "glab instalado" || status_glab=1
  check_tool git  "git instalado"  || true
  check_env_tokens
  check_auth || status_auth=$?
  [ "$status_auth" -eq 0 ] && check_token_info
  check_config_perms
  check_credential_helper
  check_git_repo
  table_footer

  if [ "$status_glab" -ne 0 ]; then
    die 1 "o GitLab CLI (glab) não está instalado ou não está no PATH" \
          "instalar o glab ('sudo pacman -S glab' ou o binário oficial de https://gitlab.com/gitlab-org/cli/-/releases em ~/.local/bin) e voltar a correr este diagnóstico"
  fi
  if [ "$status_auth" -eq 2 ]; then
    die 1 "não foi possível verificar a autenticação porque o glab está indisponível" \
          "instalar/arranjar o glab e voltar a correr 'bash scripts/gitlab-doctor.sh'"
  fi
  if [ "$status_auth" -ne 0 ]; then
    die 4 "o glab não está autenticado em ${HOST} (glab auth status falhou)" \
          "correr 'glab auth login --hostname ${HOST} --stdin < ficheiro-com-token' (PAT com scopes api e write_repository) ou exportar GITLAB_TOKEN; detalhes em references/auth.md"
  fi

  say "Ambiente pronto para operar o GitLab (${HOST}) via glab + git."
  exit 0
}

main "$@"
