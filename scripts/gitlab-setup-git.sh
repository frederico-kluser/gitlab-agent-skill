#!/usr/bin/env bash
# =============================================================================
# gitlab-setup-git.sh — liga o git (HTTPS) à credencial guardada pelo glab
# -----------------------------------------------------------------------------
# O equivalente do `gh auth setup-git` para o GitLab. Escreve, no âmbito pedido,
#   credential.https://<host>.helper = ""                      (limpa helpers herdados)
#   credential.https://<host>.helper = !f(){ … glab auth git-credential get …}; f
# - Âmbitos: --repo [dir] (config LOCAL do repositório; omissão) · --global
#   --repo não mexe na credencial dos outros repositórios da máquina.
# - Só encaminha `get`: um `erase` do git (após um 401) nunca apaga o token do glab.
# - Idempotente (repetir = NO-OP). --dry-run mostra sem escrever; --check só lê;
#   --undo remove o que este script escreveu (no --global repõe o backup).
# - Nunca lê nem imprime o valor do token.
# - Erros no formato:  "Erro: <o quê> — Solução: <o que fazer a seguir>"
# =============================================================================
set -uo pipefail

VERSION="1.0.0"
HOST="${GITLAB_HOST:-gitlab.com}"
HOST="${HOST#https://}"; HOST="${HOST#http://}"; HOST="${HOST%%/*}"
BAK_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/gitlab-agent-skill"

die() { printf 'Erro: %s — Solução: %s\n' "$2" "$3" >&2; exit "$1"; }
say() { printf '%s\n' "$*"; }

usage() {
  cat <<'EOF'
gitlab-setup-git.sh — liga o git (HTTPS) à credencial do glab

USO:
  bash gitlab-setup-git.sh --repo [dir]      # config local do repositório (omissão: .)
  bash gitlab-setup-git.sh --global          # TODOS os remotes https://<host> (pede OK ao dono)
  opções:  --host <hostname>  (omissão: GITLAB_HOST ou gitlab.com)
           --check            só mostra o estado (não escreve)
           --test             depois de ligar/verificar, testa 'git ls-remote --heads origin' (leitura)
           --undo             remove a ligação (no --global repõe o helper anterior guardado em backup)
           --dry-run          mostra o que faria, sem escrever
           --selftest         autoteste offline (repo temporário)

SAÍDA: 0 ok · 1 erro de uso/estado · 4 glab sem sessão no host · 6 teste de ls-remote falhou
EOF
}

glab_bin() { command -v glab 2>/dev/null; }

helper_line() {  # helper que só encaminha `get` para o glab (caminho absoluto)
  local g; g="$(glab_bin)"
  printf '!f() { test "$1" = get && %s auth git-credential get; }; f' "$g"
}

# git config no âmbito: $SCOPE_ARGS = (--local -C dir) ou (--global)
gcfg() { git "${GIT_C[@]}" config "${SCOPE[@]}" "$@"; }

estado_atual() { gcfg --get-all "credential.https://${HOST}.helper" 2>/dev/null || true; }

ja_ligado() {  # lista exatamente: "" e a linha do glab
  local want cur
  want="$(printf '\n%s' "$(helper_line)")"
  cur="$(estado_atual)"
  [ "$cur" = "$want" ]
}

MODE=apply; SCOPE_KIND=repo; DIR="."; DO_TEST=0
parse() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --repo) SCOPE_KIND=repo; if [ $# -ge 2 ] && [ "${2#-}" = "$2" ]; then DIR="$2"; shift; fi ;;
      --global) SCOPE_KIND=global ;;
      --host) [ $# -ge 2 ] || die 1 "falta o valor de --host" "ex.: --host gitlab.com"; HOST="$2"; shift ;;
      --check) MODE=check ;;
      --undo) MODE=undo ;;
      --dry-run) MODE=dryrun ;;
      --test) DO_TEST=1 ;;
      --selftest) MODE=selftest ;;
      --version) say "gitlab-setup-git.sh $VERSION"; exit 0 ;;
      -h|--help) usage; exit 0 ;;
      *) die 1 "argumento desconhecido: '$1'" "ver 'bash gitlab-setup-git.sh --help'" ;;
    esac
    shift
  done
}

preparar_scope() {
  if [ "$SCOPE_KIND" = repo ]; then
    git -C "$DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
      || die 1 "'$DIR' não é um repositório git" "passar o caminho do repositório: --repo <dir>"
    GIT_C=(-C "$DIR"); SCOPE=(--local)
    ALVO="repo $(cd "$DIR" && git rev-parse --show-toplevel)"
  else
    GIT_C=(); SCOPE=(--global); ALVO="global (~/.gitconfig)"
  fi
}

testar() {
  local n
  if n="$(GIT_TERMINAL_PROMPT=0 git "${GIT_C[@]}" ls-remote --heads origin 2>&1)"; then
    say "  teste: git ls-remote origin → OK ($(printf '%s\n' "$n" | grep -c . ) ref(s) de branch)"
    return 0
  fi
  printf '%s\n' "$n" | sed -E 's#(https?://)[^@/[:space:]]+@#\1#g' | tail -n 2 >&2
  die 6 "o teste de leitura 'git ls-remote origin' falhou com a credencial ligada" \
        "correr 'bash scripts/gitlab-doctor.sh' (scopes: read_repository/write_repository; papel no projeto) e confirmar o URL do origin"
}

aplicar() {
  command -v glab >/dev/null 2>&1 || die 1 "o glab não está instalado" "instalar o glab e repetir"
  NO_COLOR=1 GLAB_NO_PROMPT=1 glab auth status --hostname "$HOST" >/dev/null 2>&1 \
    || die 4 "o glab não tem sessão válida em ${HOST}" "correr 'glab auth login --hostname ${HOST} --stdin < ficheiro-com-token' e repetir"
  say "== gitlab-setup-git — $ALVO · https://${HOST}"
  if ja_ligado; then
    say "  OK   já ligado ao glab (NO-OP)"
  elif [ "$MODE" = dryrun ]; then
    say "  ACT  (dry-run) credential.https://${HOST}.helper = \"\" + '$(helper_line)'"
    return 0
  else
    if [ "$SCOPE_KIND" = global ]; then
      mkdir -p "$BAK_DIR" && chmod 700 "$BAK_DIR"
      local bak="$BAK_DIR/global-helper-${HOST}.bak"
      if [ ! -f "$bak" ]; then estado_atual > "$bak"; chmod 600 "$bak"; say "  BAK  helper global anterior guardado em $bak"; fi
    fi
    gcfg --unset-all "credential.https://${HOST}.helper" 2>/dev/null || true
    gcfg --add "credential.https://${HOST}.helper" ""
    gcfg --add "credential.https://${HOST}.helper" "$(helper_line)"
    say "  ACT  credential.https://${HOST}.helper → glab (só 'get')"
  fi
  [ "$DO_TEST" -eq 1 ] && testar
  return 0
}

verificar() {
  say "== gitlab-setup-git --check — $ALVO · https://${HOST}"
  if ja_ligado; then say "  OK   ligado ao glab"; else
    local cur; cur="$(estado_atual)"
    say "  --   não ligado ao glab neste âmbito${cur:+ (helpers: $(printf '%s' "$cur" | tr '\n' '|'))}"
  fi
  [ "$DO_TEST" -eq 1 ] && testar
  return 0
}

desfazer() {
  say "== gitlab-setup-git --undo — $ALVO · https://${HOST}"
  if ! estado_atual | grep -q 'glab auth git-credential'; then say "  OK   nada a remover"; return 0; fi
  if [ "$MODE" = dryrun ]; then say "  ACT  (dry-run) removeria a ligação"; return 0; fi
  gcfg --unset-all "credential.https://${HOST}.helper" 2>/dev/null || true
  if [ "$SCOPE_KIND" = global ] && [ -f "$BAK_DIR/global-helper-${HOST}.bak" ]; then
    while IFS= read -r v; do gcfg --add "credential.https://${HOST}.helper" "$v"; done < "$BAK_DIR/global-helper-${HOST}.bak"
    rm -f "$BAK_DIR/global-helper-${HOST}.bak"
    say "  ACT  helper global anterior reposto"
  else
    say "  ACT  ligação removida"
  fi
}

selftest() {
  local tmp failed=0 t out
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' RETURN
  git init -q "$tmp/r"
  HOST="gitlab.example.test"; DIR="$tmp/r"; SCOPE_KIND=repo; preparar_scope
  export PATH="$tmp/bin:$PATH"; mkdir -p "$tmp/bin"
  printf '#!/bin/sh\nexit 0\n' > "$tmp/bin/glab"; chmod +x "$tmp/bin/glab"   # glab falso (auth status OK)

  t="aplica a ligação no repo"
  MODE=apply aplicar >/dev/null && ja_ligado && say "PASS — $t" || { say "FAIL — $t"; failed=1; }
  t="idempotente (2.ª vez = NO-OP)"
  out="$(MODE=apply aplicar)"; printf '%s' "$out" | grep -q 'NO-OP' && say "PASS — $t" || { say "FAIL — $t"; failed=1; }
  t="não toca no --global"
  git config --global --get-all "credential.https://${HOST}.helper" >/dev/null 2>&1 && { say "FAIL — $t"; failed=1; } || say "PASS — $t"
  t="helper só encaminha 'get' (erase é no-op)"
  estado_atual | grep -q 'test "$1" = get' && say "PASS — $t" || { say "FAIL — $t"; failed=1; }
  t="undo remove a ligação"
  MODE=apply desfazer >/dev/null; [ -z "$(estado_atual)" ] && say "PASS — $t" || { say "FAIL — $t"; failed=1; }

  [ "$failed" -eq 0 ] && { say "selftest: TODOS OS TESTES PASSARAM (offline)"; return 0; }
  say "selftest: HÁ FALHAS"; return 1
}

main() {
  parse "$@"
  if [ "$MODE" = selftest ]; then selftest; exit $?; fi
  preparar_scope
  case "$MODE" in
    check) verificar ;;
    undo|dryrun) if [ "$MODE" = undo ]; then desfazer; else aplicar; fi ;;
    apply) aplicar ;;
  esac
}

main "$@"
