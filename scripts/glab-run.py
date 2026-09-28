#!/usr/bin/env python3
# =============================================================================
# glab-run.py — wrapper determinístico do glab (GitLab CLI) para agentes LLM
# -----------------------------------------------------------------------------
# - Modo --json: saída estruturada e estável (nunca parsear tabelas do glab).
# - Portão de bytes: --max-bytes trunca stdout/stderr com marcador.
# - Retries APENAS em erros transitórios (429 com Retry-After/RateLimit-Reset,
#   HTTP 5xx, rede/TLS/timeout) com backoff exponencial.
# - O glab sai com 1 em QUALQUER erro: a causa lê-se no texto ("glab: 404
#   Project Not Found (HTTP 404)"); este wrapper traduz para exit codes distintos
#   e mensagens instrutivas:  "Erro: <o quê> — Solução: <o que fazer a seguir>"
# - NUNCA imprime valores de token (redação obrigatória em toda a saída).
# - Python 3 stdlib apenas. Stateless. Modo --selftest offline (exit 0).
# =============================================================================
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass

VERSION = "1.0.0"

# ------------------------------------------------------------------ defaults --
DEFAULT_MAX_BYTES = 32768          # portão de bytes por stream (stdout/stderr)
DEFAULT_RETRIES = 3                # tentativas extras em erro transitório
DEFAULT_TIMEOUT = 60               # segundos por tentativa do glab
BACKOFF_BASE = 2.0                 # segundos; dobra a cada tentativa
RETRY_AFTER_CAP = 120.0            # nunca dormir mais do que isto por espera

# ------------------------------------------------------------- exit codes -----
EXIT_OK = 0
EXIT_FAIL = 1
EXIT_CANCELLED = 2
EXIT_NOT_INSTALLED = 3
EXIT_AUTH = 4
EXIT_RATE_LIMIT = 5
EXIT_NETWORK = 6
EXIT_INTERNAL = 7

# --------------------------------------------------------------- padrões -------
# Prefixos de tokens do GitLab (PAT, OAuth, deploy, runner, CI, trigger, feed,
# e-mail, agente, SCIM, feature flags, workspace) + token de registo de runner.
TOKEN_RE = re.compile(
    r"((?:glpat|gloas|gldt|glrt|glrtr|glcbt|glptt|glft|glimt|glagent|glsoat|glffct|glwt|gltst)"
    r"-[A-Za-z0-9._\-]{8,}|GR1348941[A-Za-z0-9_\-]{8,})"
)
URL_USERINFO_RE = re.compile(r"(https?://)[^/@\s:]+:[^/@\s]+@")   # https://user:segredo@host
REDACTED = "[VALOR OCULTO]"

RATE_PATTERNS = re.compile(
    r"(http 429|429 too many requests|too many requests|retry later|rate limit"
    r"|ratelimit-reset|retry-after)",
    re.I,
)
AUTH_PATTERNS = re.compile(
    r"(http 401|401 unauthorized|http 403|403 forbidden|insufficient_scope"
    r"|invalid_token|token (?:was )?(?:revoked|expired)|not authenticated|glab auth login"
    r"|no token (?:found|provided)|not allowed to push code to this project"
    r"|you are not allowed to (?:download|access)|authentication failed"
    r"|http basic: access denied)",
    re.I,
)
NETWORK_PATTERNS = re.compile(
    r"(connection reset|connection refused|connection aborted|could not resolve host"
    r"|no such host|temporary failure in name resolution|network is unreachable"
    r"|i/o timeout|timed? ?out|tls handshake|x509|ssl|unexpected eof|broken pipe"
    r"|http 5\d\d|\b50[0234] |bad gateway|service unavailable|gateway timeout)",
    re.I,
)
RETRY_AFTER_RE = re.compile(r"retry-after[:\s]+(\d+(?:\.\d+)?)", re.I)

# Padrões de erro com causa/correção conhecidas (para mensagens instrutivas).
# A ordem importa: a 1.ª regra que casar ganha.
SOLUTIONS = [
    (re.compile(r"protected branch|not allowed to (?:force )?push code to (?:a )?protected|pre-receive hook declined", re.I),
     "a branch de destino é protegida e o push/merge foi rejeitado",
     "não force push; abrir MR com 'glab mr create' para a branch protegida ou pedir a quem mantém o projeto para ajustar "
     "Settings → Repository → Protected branches"),
    (re.compile(r"another open merge request already exists|already exists for this source branch", re.I),
     "já existe um MR aberto para esta branch de origem",
     "usar 'glab mr list --source-branch <branch>' e atualizar o MR existente com 'glab mr update' em vez de criar outro"),
    (re.compile(r"has already been taken|already exists", re.I),
     "o recurso já existe (nome/caminho/tag duplicado)",
     "listar o existente ('glab repo view', 'glab release list', 'glab label list') e editar em vez de criar, ou usar outro nome"),
    (re.compile(r"cannot be merged|merge conflicts?|has conflicts|not mergeable|conflict", re.I),
     "o merge request tem conflitos ou não está em estado de merge",
     "'glab mr rebase <id>' ou atualizar a branch localmente ('git pull --rebase origin <alvo>'), resolver os conflitos, "
     "fazer push e repetir (ver references/troubleshooting.md)"),
    (re.compile(r"non-fast-forward|fetch first|updates were rejected|\[rejected\]", re.I),
     "o remoto tem commits que o local não tem (push rejeitado)",
     "correr 'git pull --rebase' (ou '--allow-unrelated-histories' num projeto recém-criado com README) e voltar a fazer "
     "push; nunca usar --force em branch partilhada"),
    (re.compile(r"404 (?:project|group|file|commit|branch|tag|merge request|issue)? ?not found|http 404|not found", re.I),
     "recurso inexistente ou sem acesso (o GitLab devolve 404 para projetos privados sem permissão)",
     "confirmar o caminho com 'glab repo view grupo/projeto' e o acesso com "
     "'glab api projects/<caminho-url-encoded> | jq .permissions'; se o projeto existir, falta papel no grupo/projeto "
     "ou scope ao token (ver references/auth.md)"),
    (re.compile(r"http 400|400 bad request|http 422|422 unprocessable|is invalid|is missing|can't be blank", re.I),
     "o GitLab rejeitou os parâmetros do pedido (validação)",
     "rever título/descrição/campos obrigatórios; repetir o pedido com 'glab api -X <MÉTODO> <endpoint> -f campo=valor' "
     "para ver a mensagem completa"),
    (re.compile(r"unknown flag|unknown command|accepts \d+ arg|requires at least", re.I),
     "o comando glab tem flags ou argumentos inválidos",
     "ver 'glab <comando> --help' (a superfície muda entre versões; cheatsheet em references/cli-cheatsheet.md)"),
]


# ----------------------------------------------------------------- utilidades --
def redigir(texto: str) -> str:
    """Redige valores de token e credenciais em URL em qualquer texto (obrigatório)."""
    texto = TOKEN_RE.sub(REDACTED, texto)
    return URL_USERINFO_RE.sub(r"\1" + REDACTED + "@", texto)


def truncar(texto: str, max_bytes: int):
    """Portão de bytes: devolve (texto, truncado?)."""
    dados = texto.encode("utf-8", errors="replace")
    if len(dados) <= max_bytes:
        return texto, False
    cabeca = dados[:max_bytes].decode("utf-8", errors="replace")
    omitidos = len(dados) - max_bytes
    return f"{cabeca}\n[... truncado: {omitidos} bytes omitidos ...]", True


def esperado_de_retry(texto: str):
    """Extrai Retry-After (segundos) do texto do erro, se existir."""
    m = RETRY_AFTER_RE.search(texto or "")
    return float(m.group(1)) if m else None


def redigir_comando(argv) -> str:
    return redigir("glab " + " ".join(argv))


# -------------------------------------------------------------- execução ------
@dataclass
class Resultado:
    comando: str
    exit_code: int
    glab_exit_code: int | None
    tentativas: int
    stdout: str
    stderr: str
    truncado: bool = False
    erro: str | None = None
    solucao: str | None = None
    kind: str = "ok"

    @property
    def ok(self) -> bool:
        return self.exit_code == EXIT_OK

    def para_json(self) -> dict:
        return {
            "ok": self.ok,
            "comando": self.comando,
            "kind": self.kind,
            "exit_code": self.exit_code,
            "glab_exit_code": self.glab_exit_code,
            "tentativas": self.tentativas,
            "truncado": self.truncado,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "erro": self.erro,
            "solucao": self.solucao,
        }


@dataclass
class Opcoes:
    max_bytes: int = DEFAULT_MAX_BYTES
    retries: int = DEFAULT_RETRIES
    timeout: float = DEFAULT_TIMEOUT
    json_mode: bool = False
    quiet: bool = False


@dataclass
class Execucao:
    """Resultado bruto de UMA tentativa."""
    glab_exit: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    missing: bool = False


def runner_real(argv, timeout: float) -> Execucao:
    env = dict(os.environ)
    env.setdefault("NO_COLOR", "1")          # sem sequências ANSI na saída
    env.setdefault("GLAB_NO_PROMPT", "1")    # nunca ficar à espera de input
    env.setdefault("GLAB_CHECK_UPDATE", "false")
    try:
        p = subprocess.run(
            ["glab"] + list(argv),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            env=env,
            stdin=subprocess.DEVNULL,
        )
        return Execucao(glab_exit=p.returncode, stdout=p.stdout, stderr=p.stderr)
    except FileNotFoundError:
        return Execucao(missing=True, stderr="glab não encontrado no PATH")
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
        err = e.stderr or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", errors="replace")
        if isinstance(err, bytes):
            err = err.decode("utf-8", errors="replace")
        return Execucao(timed_out=True, stdout=out, stderr=err or "timeout da subprocess")


def classificar(ex: Execucao):
    """→ (kind, transitório?) — ordem: rate → auth → rede → terminal."""
    if ex.missing:
        return "missing", False
    if ex.timed_out:
        return "network", True
    if ex.glab_exit == 0:
        return "ok", False
    if ex.glab_exit in (130, -2):             # SIGINT
        return "cancelled", False
    texto = f"{ex.stdout}\n{ex.stderr}"
    if RATE_PATTERNS.search(texto):
        return "rate", True
    if AUTH_PATTERNS.search(texto):
        return "auth", False
    if NETWORK_PATTERNS.search(texto):
        return "network", True
    return "fail", False


def mensagem_instrutiva(kind: str, texto: str):
    """Devolve (erro, solução) no padrão obrigatório."""
    if kind == "missing":
        return ("o GitLab CLI (glab) não está instalado ou não está no PATH",
                "instalar o glab ('sudo pacman -S glab' ou o binário oficial de "
                "https://gitlab.com/gitlab-org/cli/-/releases em ~/.local/bin) e repetir o comando")
    if kind == "cancelled":
        return ("o comando glab foi cancelado antes de terminar",
                "voltar a executar o comando; se foi cancelado por timeout, usar --timeout maior")
    if kind == "auth":
        if re.search(r"401|unauthorized|invalid_token|revoked|expired|no token|not authenticated", texto, re.I):
            return ("o GitLab rejeitou a autenticação (401: token ausente, inválido, revogado ou expirado)",
                    "correr 'bash scripts/gitlab-doctor.sh' e renovar o token com "
                    "'glab auth login --hostname <host> --stdin < ficheiro-seguro' (ver references/auth.md)")
        return ("o GitLab negou a operação (403: scope do token ou papel no projeto/grupo insuficiente)",
                "ver scopes com 'glab api personal_access_tokens/self | jq .scopes' (precisa de 'api' para escrever pela "
                "API e 'write_repository' para push) e o papel com 'glab api projects/<caminho> | jq .permissions'; "
                "pedir o papel certo (Developer para push/MR, Maintainer para branches protegidas)")
    if kind == "rate":
        return ("limite de taxa do GitLab esgotado mesmo após retries (HTTP 429)",
                "esperar o reset indicado (RateLimit-Reset/Retry-After) e espaçar os pedidos; em automação, reduzir "
                "concorrência ou usar --paginate com menos chamadas")
    if kind == "network":
        return ("falha de rede/timeout ou erro 5xx do GitLab mesmo após retries",
                "verificar ligação/VPN/DNS e https://status.gitlab.com; repetir com --timeout maior; ver "
                "references/troubleshooting.md")
    for rx, o_que, como in SOLUTIONS:
        if rx.search(texto):
            return (o_que, como)
    return ("o comando glab terminou com erro não transitório",
            "rever a mensagem do glab em 'stderr'; tabela causa→correção em references/troubleshooting.md")


# ------------------------------------------------------------------ engine ----
def executar(argv, opts: Opcoes, runner=runner_real, sleeper=time.sleep) -> Resultado:
    comando = redigir_comando(argv)
    tentativa = 0
    while True:
        tentativa += 1
        ex = runner(argv, opts.timeout)
        kind, transitorio = classificar(ex)
        if kind == "ok":
            saida, t1 = truncar(redigir(ex.stdout), opts.max_bytes)
            erro_out, t2 = truncar(redigir(ex.stderr), opts.max_bytes)
            return Resultado(comando, EXIT_OK, 0, tentativa, saida, erro_out,
                             truncado=t1 or t2, kind="ok")
        if not transitorio or tentativa > opts.retries:
            break
        espera = esperado_de_retry(f"{ex.stdout}\n{ex.stderr}")
        if espera is None:
            espera = BACKOFF_BASE * (2 ** (tentativa - 1))
        sleeper(min(espera, RETRY_AFTER_CAP))

    # terminal: montar resposta instrutiva
    texto = f"{ex.stdout}\n{ex.stderr}"
    erro, solucao = mensagem_instrutiva(kind, texto)
    mapa = {
        "missing": EXIT_NOT_INSTALLED,
        "cancelled": EXIT_CANCELLED,
        "auth": EXIT_AUTH,
        "rate": EXIT_RATE_LIMIT,
        "network": EXIT_NETWORK,
        "fail": EXIT_FAIL,
    }
    saida, t1 = truncar(redigir(ex.stdout), opts.max_bytes)
    erro_out, t2 = truncar(redigir(ex.stderr), opts.max_bytes)
    return Resultado(
        comando, mapa[kind], ex.glab_exit, tentativa, saida, erro_out,
        truncado=t1 or t2, erro=erro, solucao=solucao, kind=kind,
    )


# ------------------------------------------------------------------- parser ----
USAGE = """glab-run.py — wrapper determinístico do glab (JSON, retries, redação)

USO:
  python3 glab-run.py [opções minhas] -- <comando glab...>
  python3 glab-run.py [opções minhas] <comando glab...>

EXEMPLOS:
  python3 glab-run.py --json -- mr list -R grupo/projeto -F json
  python3 glab-run.py --json --max-bytes 8000 -- ci trace 12345 -R grupo/projeto
  python3 glab-run.py -- api projects/grupo%2Fprojeto | jq .default_branch

OPÇÕES DO WRAPPER (antes do comando; use '--' se o comando começar por '-'):
  --json                 saída JSON estruturada (stdout)
  --max-bytes N          portão de bytes por stream (default 32768)
  --retries N            retentativas em erro transitório (default 3)
  --timeout S            timeout por tentativa do glab, em segundos (default 60)
  --quiet                não ecoar stdout/stderr do glab em modo texto
  --selftest             autoteste offline do wrapper (exit 0 se tudo passar)
  --help, -h             esta ajuda
  --version              versão

EXIT CODES DO WRAPPER:
  0 sucesso · 1 falha do glab · 2 cancelado · 3 glab não instalado
  4 auth/scope/papel (401/403) · 5 rate limit esgotado (429) · 6 rede/timeout/5xx · 7 erro interno

AMBIENTE: o wrapper define NO_COLOR=1, GLAB_NO_PROMPT=1 e GLAB_CHECK_UPDATE=false
(se ainda não estiverem definidos) para o glab nunca pedir input nem pintar a saída.

CONTRATO DE ERRO:  "Erro: <o quê> — Solução: <o que fazer a seguir>"
Valores de token (glpat-, gldt-, glrt-, …) e credenciais em URL são SEMPRE redigidos ([VALOR OCULTO]).
"""


def parse_args(argv):
    opts = Opcoes()
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--":
            return opts, list(argv[i + 1:])
        if a in ("--help", "-h"):
            print(USAGE)
            raise SystemExit(EXIT_OK)
        if a == "--version":
            print(f"glab-run.py {VERSION}")
            raise SystemExit(EXIT_OK)
        if a == "--selftest":
            return "SELFTEST", None
        if a == "--json":
            opts.json_mode = True
            i += 1
            continue
        if a == "--quiet":
            opts.quiet = True
            i += 1
            continue
        if a in ("--max-bytes", "--retries", "--timeout"):
            if i + 1 >= len(argv):
                raise SystemExit(f"Erro: falta o valor de {a} — Solução: indicar um número, ex. {a} 8192")
            try:
                valor = float(argv[i + 1]) if a == "--timeout" else int(argv[i + 1])
            except ValueError:
                raise SystemExit(f"Erro: valor inválido para {a}: {argv[i + 1]!r} — Solução: indicar um número")
            if a == "--retries":
                if valor < 0:
                    raise SystemExit(f"Erro: {a} deve ser >= 0 — Solução: usar 0 para desativar retentativas")
            elif valor <= 0:
                raise SystemExit(f"Erro: {a} deve ser > 0 — Solução: usar um valor positivo, ex. {a} 3")
            if a == "--max-bytes":
                opts.max_bytes = int(valor)
            elif a == "--retries":
                opts.retries = int(valor)
            else:
                opts.timeout = valor
            i += 2
            continue
        # flag desconhecida ou 1.º token do comando: o resto é do glab
        return opts, list(argv[i:])
    return opts, []


# ---------------------------------------------------------------- selftest -----
def selftest() -> int:
    falhas = 0

    def caso(nome, fn):
        nonlocal falhas
        try:
            ok, detalhe = fn()
        except Exception as e:  # pragma: no cover — defesa
            ok, detalhe = False, f"exceção: {e!r}"
        print(f"{'PASS' if ok else 'FAIL'} — {nome}" + (f" :: {detalhe}" if not ok else ""))
        if not ok:
            falhas += 1

    def mk_runner(seq):
        """Runner falso que consome uma sequência de Execucao."""
        estado = {"n": 0}

        def _runner(argv, timeout):
            idx = min(estado["n"], len(seq) - 1)
            estado["n"] += 1
            return seq[idx]
        return _runner

    sleeps = []

    def fake_sleep(s):
        sleeps.append(s)

    opts = Opcoes(retries=2, max_bytes=100)

    def t_sucesso_primeira():
        r = executar(["mr", "list"], opts,
                     runner=mk_runner([Execucao(glab_exit=0, stdout="ok\n")]), sleeper=fake_sleep)
        return (r.ok and r.tentativas == 1 and r.exit_code == EXIT_OK,
                f"exit={r.exit_code} tentativas={r.tentativas}")

    def t_retry_5xx():
        sleeps.clear()
        r = executar(["ci", "list"], opts,
                     runner=mk_runner([
                         Execucao(glab_exit=1, stderr="glab: 502 Bad Gateway (HTTP 502)"),
                         Execucao(glab_exit=0, stdout="recuperado"),
                     ]), sleeper=fake_sleep)
        return (r.ok and r.tentativas == 2 and sleeps, f"exit={r.exit_code} tentativas={r.tentativas}")

    def t_rate_limit_retry_after():
        sleeps.clear()
        r = executar(["api", "user"], opts,
                     runner=mk_runner([
                         Execucao(glab_exit=1, stderr="glab: 429 Too Many Requests (HTTP 429) Retry-After: 7"),
                         Execucao(glab_exit=0, stdout="ok"),
                     ]), sleeper=fake_sleep)
        return (r.ok and sleeps == [7.0], f"sleeps={sleeps}")

    def t_401_sem_retry():
        sleeps.clear()
        r = executar(["api", "user"], opts,
                     runner=mk_runner([Execucao(glab_exit=1, stderr="glab: 401 Unauthorized (HTTP 401)")]),
                     sleeper=fake_sleep)
        return (r.exit_code == EXIT_AUTH and r.tentativas == 1 and not sleeps
                and "401" in (r.erro or ""), f"exit={r.exit_code} erro={r.erro!r}")

    def t_403_scope():
        r = executar(["api", "-X", "POST", "projects"], opts,
                     runner=mk_runner([Execucao(glab_exit=1, stderr="glab: 403 Forbidden insufficient_scope (HTTP 403)")]),
                     sleeper=fake_sleep)
        return (r.exit_code == EXIT_AUTH and "403" in (r.erro or ""), f"exit={r.exit_code} erro={r.erro!r}")

    def t_404_instrutivo():
        r = executar(["repo", "view", "grupo/x"], opts,
                     runner=mk_runner([Execucao(glab_exit=1, stderr="glab: 404 Project Not Found (HTTP 404)")]),
                     sleeper=fake_sleep)
        return (r.exit_code == EXIT_FAIL and "404" in (r.erro or "") and r.tentativas == 1,
                f"exit={r.exit_code} erro={r.erro!r}")

    def t_rate_esgotado():
        r = executar(["api", "user"], opts,
                     runner=mk_runner([Execucao(glab_exit=1, stderr="glab: 429 Too Many Requests (HTTP 429)")]),
                     sleeper=fake_sleep)
        return (r.exit_code == EXIT_RATE_LIMIT and r.tentativas == opts.retries + 1,
                f"exit={r.exit_code} tentativas={r.tentativas}")

    def t_truncagem():
        grande = "x" * 5000
        r = executar(["issue", "list"], opts,
                     runner=mk_runner([Execucao(glab_exit=0, stdout=grande)]), sleeper=fake_sleep)
        corpo = r.stdout.replace("[... truncado:", "")
        return (r.truncado and len(corpo.encode()) < len(grande.encode())
                and "bytes omitidos" in r.stdout, f"len={len(r.stdout)}")

    def t_redacao():
        # tokens sintéticos construídos em runtime — nunca escritos como literais
        fake_pat = "glpat-" + "A" * 26 + ".01.abc123"
        fake_deploy = "gldt-" + "B" * 20
        fake_url = "https://oauth2:" + "C" * 20 + "@gitlab.com/g/p.git"
        r = executar(["auth", "status"], opts,
                     runner=mk_runner([Execucao(glab_exit=0, stdout="Token: " + fake_pat + " " + fake_url,
                                                stderr=fake_deploy)]),
                     sleeper=fake_sleep)
        tudo = r.stdout + r.stderr
        vazou = ("glpat-" in tudo or "gldt-" in tudo or "C" * 20 in tudo)
        return (not vazou and "[VALOR OCULTO]" in tudo, "token vazou")

    def t_redacao_comando():
        fake = "glpat-" + "D" * 24
        r = executar(["auth", "login", "--token", fake], opts,
                     runner=mk_runner([Execucao(glab_exit=0, stdout="")]), sleeper=fake_sleep)
        return ("glpat-" not in r.comando, f"comando={r.comando!r}")

    def t_cancelado():
        r = executar(["ci", "status", "--live"], opts,
                     runner=mk_runner([Execucao(glab_exit=130, stderr="interrupted")]), sleeper=fake_sleep)
        return (r.exit_code == EXIT_CANCELLED, f"exit={r.exit_code}")

    def t_mensagem_protegida():
        r = executar(["mr", "merge", "1"], opts,
                     runner=mk_runner([Execucao(glab_exit=1, stderr="remote: GitLab: You are not allowed to push code to protected branches on this project.")]),
                     sleeper=fake_sleep)
        return (r.exit_code == EXIT_FAIL and "protegida" in (r.erro or ""), f"exit={r.exit_code} erro={r.erro!r}")

    def t_conflito():
        r = executar(["mr", "merge", "1"], opts,
                     runner=mk_runner([Execucao(glab_exit=1, stderr="glab: 406 Branch cannot be merged (HTTP 406)")]),
                     sleeper=fake_sleep)
        return (r.exit_code == EXIT_FAIL and "conflitos" in (r.erro or ""), f"erro={r.erro!r}")

    def t_409_ja_existe():
        r = executar(["release", "create", "v1"], opts,
                     runner=mk_runner([Execucao(glab_exit=1, stderr="glab: 409 Release already exists (HTTP 409)")]),
                     sleeper=fake_sleep)
        return (r.exit_code == EXIT_FAIL and "já existe" in (r.erro or ""), f"erro={r.erro!r}")

    def t_sem_acesso_push():
        r = executar(["mr", "merge", "1"], opts,
                     runner=mk_runner([Execucao(glab_exit=1, stderr="remote: You are not allowed to push code to this project.")]),
                     sleeper=fake_sleep)
        return (r.exit_code == EXIT_AUTH, f"exit={r.exit_code}")

    def t_json_estavel():
        r = executar(["mr", "view", "1"], opts,
                     runner=mk_runner([Execucao(glab_exit=0, stdout="{}")]), sleeper=fake_sleep)
        d = r.para_json()
        obrigatorios = {"ok", "comando", "exit_code", "glab_exit_code", "tentativas", "stdout", "stderr",
                        "erro", "solucao"}
        return (obrigatorios.issubset(d.keys()), f"faltam={obrigatorios - set(d)}")

    def t_nao_instalado():
        r = executar(["version"], opts, runner=mk_runner([Execucao(missing=True)]), sleeper=fake_sleep)
        return (r.exit_code == EXIT_NOT_INSTALLED, f"exit={r.exit_code}")

    caso("sucesso à primeira", t_sucesso_primeira)
    caso("retry em HTTP 5xx → sucesso", t_retry_5xx)
    caso("429 honra Retry-After", t_rate_limit_retry_after)
    caso("401 falha sem retry (exit 4)", t_401_sem_retry)
    caso("403 scope → exit 4 com dica de scopes", t_403_scope)
    caso("404 → mensagem instrutiva sem retry", t_404_instrutivo)
    caso("rate limit esgota retries (exit 5)", t_rate_esgotado)
    caso("portão de bytes trunca saída", t_truncagem)
    caso("redação de tokens gl*- e credenciais em URL", t_redacao)
    caso("redação de token no comando ecoado", t_redacao_comando)
    caso("cancelado (SIGINT → exit 2)", t_cancelado)
    caso("branch protegida → mensagem instrutiva", t_mensagem_protegida)
    caso("MR com conflitos → mensagem instrutiva", t_conflito)
    caso("409 já existe → mensagem instrutiva (não 'conflitos')", t_409_ja_existe)
    caso("sem acesso de escrita ao projeto → exit 4", t_sem_acesso_push)
    caso("modo --json tem campos estáveis", t_json_estavel)
    caso("glab ausente → exit 3", t_nao_instalado)

    if falhas == 0:
        print("selftest: TODOS OS TESTES PASSARAM (offline)")
        return EXIT_OK
    print(f"selftest: {falhas} FALHA(S)")
    return EXIT_INTERNAL


# -------------------------------------------------------------------- main -----
def main(argv) -> int:
    try:
        parsed, cmd = parse_args(argv)
    except SystemExit as e:
        if isinstance(e.code, int):
            return e.code
        if e.code:
            print(e.code, file=sys.stderr)   # mensagem "Erro: ... — Solução: ..."
        return EXIT_INTERNAL
    if parsed == "SELFTEST":
        return selftest()
    if not cmd:
        print(USAGE, file=sys.stderr)
        print("Erro: nenhum comando glab indicado — Solução: passar o comando após as opções, ex.: "
              "python3 glab-run.py --json -- mr list", file=sys.stderr)
        return EXIT_INTERNAL

    opts: Opcoes = parsed
    try:
        r = executar(cmd, opts)
    except KeyboardInterrupt:
        print("Erro: interrompido pelo utilizador — Solução: voltar a executar o comando", file=sys.stderr)
        return EXIT_CANCELLED
    except Exception as e:  # pragma: no cover — defesa
        print(f"Erro: falha interna do wrapper ({e!r}) — Solução: reportar o caso com o comando usado",
              file=sys.stderr)
        return EXIT_INTERNAL

    if opts.json_mode:
        print(json.dumps(r.para_json(), ensure_ascii=False, indent=2))
    else:
        if r.stdout and not opts.quiet:
            print(r.stdout, end="" if r.stdout.endswith("\n") else "\n")
        if r.stderr and not opts.quiet:
            print(r.stderr, file=sys.stderr, end="" if r.stderr.endswith("\n") else "\n")
        if not r.ok:
            print(f"Erro: {r.erro} — Solução: {r.solucao}", file=sys.stderr)
    return r.exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
