# troubleshooting.md — erros comuns do glab/git no GitLab → causa → correção

Formato usado por todos os erros desta skill:
`Erro: <o quê> — Solução: <o que fazer a seguir>` (exit code ≠ 0).
O `glab` sai com `1` em qualquer erro (a causa vem no texto, ex.:
`glab: 404 Project Not Found (HTTP 404)`). O wrapper `glab-run.py` distingue:
`2` cancelado · `3` glab não instalado · `4` auth/scope/papel · `5` rate limit ·
`6` rede/timeout/5xx · `7` erro interno.

## 1. Autenticação e autorização

| Erro (sintoma) | Causa provável | Correção |
|---|---|---|
| `401 Unauthorized` | Token ausente, inválido, revogado ou expirado | `bash scripts/gitlab-doctor.sh`; novo token e `glab auth login --hostname <host> --stdin < ficheiro` |
| `403 Forbidden` / `insufficient_scope` | Token sem o scope da operação (ex.: `read_api` a escrever) | Ver scopes (`glab api personal_access_tokens/self | jq .scopes`) e criar token com `api`/`write_repository` — `auth.md` §3 |
| `403 Forbidden` com scope certo | Papel insuficiente no projeto/grupo | `glab api projects/<caminho> | jq .permissions`; pedir Developer (push/MR) ou Maintainer (protegidas, variáveis) |
| `404 Project Not Found` em projeto que existe | Sem acesso: token de outra conta/namespace ou não é membro (o GitLab esconde privados) | `glab api user | jq .username`; confirmar membership no grupo; usar o token certo — `auth.md` §4 |
| `remote: HTTP Basic: Access denied` / `Authentication failed` (git) | Credencial git errada ou expirada | `bash scripts/gitlab-setup-git.sh --repo . --test`; se o global estiver errado, `git credential reject` com cuidado (ver §5) |
| `remote: The project you were looking for could not be found or you don't have permission to view it.` (git) | Igual ao 404 da API, mas pelo git: a credencial usada não vê o projeto | Ligar o repo ao token certo: `gitlab-setup-git.sh --repo . --test` |
| `You are not allowed to push code to this project` | Papel < Developer | Pedir papel; confirmar com `.permissions` |
| `Token … expired` / aviso de validade no doctor | Token perto do fim/expirado | `glab token rotate <nome> -U @me` e novo `glab auth login --stdin` |
| `glab: command not found` | glab não instalado | `sudo pacman -S glab` (se o mirror falhar com 404, é a base desatualizada: preferir o binário oficial em `~/.local/bin`, com checksum, a um `pacman -Sy` parcial) |
| Diálogo do KWallet/keyring bloqueia | Token no keyring e sessão sem ecrã | `glab auth login --stdin --insecure-storage` (ficheiro 0600) — `auth.md` §1 |

## 2. Push, branches protegidas e histórico

| Erro | Causa | Correção |
|---|---|---|
| `You are not allowed to push code to protected branches on this project` | Branch protegida para o seu papel | Abrir MR (`glab mr create`); ou Maintainer ajusta Settings → Repository → Protected branches |
| `You are not allowed to force push code to a protected branch` | Force push desligado na protegida (omissão) | Nunca forçar; integrar com merge/rebase normal. `glab api projects/<c>/protected_branches | jq '.[]|{name,allow_force_push}'` |
| `pre-receive hook declined` | Regra do servidor (proteção, push rules, tamanho, commit sem sign-off) | Ler as linhas `remote:` anteriores; corrigir o commit (ex.: `git commit --amend -s`) e voltar a fazer push |
| `! [rejected] main -> main (fetch first)` num projeto novo | O projeto foi criado com README/licença («Initial commit») | `git pull origin main --allow-unrelated-histories` (resolver o conflito do README) e `git push -u origin main` — em vez do `push -f` que o GitLab sugere |
| `non-fast-forward` / `Updates were rejected` | O remoto tem commits novos | `git pull --rebase && git push`; `--force-with-lease` só em branches próprias |
| `src refspec main does not match any` | Branch local com outro nome (`master`) | `git branch -M main` e repetir |
| `remote: GitLab: … file too large` | Limite de tamanho do projeto/ficheiro | Git LFS ou remover o binário do histórico (`git filter-repo`, com confirmação do dono) |

## 3. Merge requests

| Erro | Causa | Correção |
|---|---|---|
| `Another open merge request already exists for this source branch` | MR já aberto | `glab mr list -s <branch>` e `glab mr update <iid>` |
| `406 Branch cannot be merged` / `merge_status: cannot_be_merged` | Conflitos | `glab mr rebase <iid>` ou `git pull --rebase origin <alvo>`, resolver, push |
| `405 Method Not Allowed` no merge | MR em draft, pipeline obrigatório a falhar, discussões por resolver ou aprovações em falta | `glab mr view <iid> -F json --jq '{draft, detailed_merge_status}'`; `glab mr update <iid> --ready`; resolver discussões; esperar o CI (`glab ci status --wait`) |
| Merge fica «agendado» e não acontece | `--auto-merge` (omissão do `glab mr merge`) à espera do pipeline | Esperar o CI ou `glab mr merge <iid> --auto-merge=false --yes` se já passou |
| `401/403` ao aprovar | Autor não pode aprovar o próprio MR (regra do projeto) ou papel insuficiente | Pedir aprovação a outro membro |

## 4. CI/CD

| Erro | Causa | Correção |
|---|---|---|
| `yaml invalid` / pipeline não arranca | `.gitlab-ci.yml` inválido | `glab ci lint` e `glab ci lint --dry-run --ref <branch> --include-jobs` |
| Job `pending` para sempre / `stuck` | Sem runner com as tags do job | `glab runner list`; ver `tags:` do job; ativar shared runners no projeto |
| `glab ci trace` sem saída | Job ainda não começou ou nome ambíguo | Passar o ID do job e `-p <pipeline>` |
| Variável não chega ao job | Variável `protected` numa branch não protegida, ou `masked` com valor inválido | `glab variable get CHAVE`; ajustar `--protected`/ambiente (`-s`) |
| `ci run --variables` rejeitado | Pipelines de MR não aceitam variáveis | Correr pipeline de branch (`-b`) ou usar `-i` (inputs) |

## 5. Credenciais git nesta máquina

| Sintoma | Causa | Correção |
|---|---|---|
| Push para um grupo novo dá `could not be found` mas o browser mostra o projeto | O helper global (`git-credential-guard` → credential-store, utilizador `oauth2`) usa outro token, sem acesso a esse grupo | `bash scripts/gitlab-setup-git.sh --repo . --test` (usa o token do glab só neste repositório) |
| Um 401 «apaga» a credencial de todos os repos gitlab.com | Comportamento do credential-store com `erase` | Já mitigado: o `git-credential-guard` bloqueia `erase` em gitlab.com e o helper desta skill só encaminha `get` |
| Não sei que helper o repo está a usar | Helpers herdados global → local | `git config --show-origin --get-all credential.https://gitlab.com.helper` · `bash scripts/gitlab-doctor.sh` |

## 6. Rate limits e rede

| Erro | Causa | Correção |
|---|---|---|
| `429 Too Many Requests` / `Retry later` | Limite do gitlab.com (por utilizador/IP/endpoint) | Esperar `Retry-After`/`RateLimit-Reset` (`glab api -i user` mostra os cabeçalhos); `glab-run.py` já retenta |
| `502/503/504` | Incidente ou manutenção do GitLab | https://status.gitlab.com; `glab-run.py` retenta com backoff |
| `x509: certificate signed by unknown authority` | CA corporativa/self-managed | `glab config set ca_cert /caminho/ca.pem --host <host>`; nunca `skip_tls_verify` em produção |
| `dial tcp: lookup … no such host` | DNS/VPN/host errado | Confirmar `GITLAB_HOST`/`--hostname` e a VPN |

## 7. Estratégia de diagnóstico (quando nada do quadro se aplica)

1. `bash scripts/gitlab-doctor.sh` — glab/git/auth/scopes/validade/helper.
2. `GLAB_DEBUG=1 glab <comando>` — mostra os comandos git e detalhes de DNS (sem o token).
3. `glab api -i <endpoint>` — cabeçalhos HTTP (status, RateLimit-*).
4. `GIT_TRACE=1 GIT_CURL_VERBOSE=0 git ls-remote origin` — que helper o git chama (sem valores).
5. Reproduzir com o `glab` puro para isolar wrapper vs glab.
6. Procurar a mensagem exata em <https://docs.gitlab.com/cli/> e
   <https://gitlab.com/gitlab-org/cli/-/issues>.
