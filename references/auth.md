# auth.md — autenticação, tokens, papéis e credenciais git (glab + git)

## 1. Onde o `glab` vai buscar a credencial (ordem de precedência)

1. **Variáveis de ambiente** — `GITLAB_TOKEN`, `GITLAB_ACCESS_TOKEN` ou `OAUTH_TOKEN`
   (ganham ao ficheiro). Em CI: `CI_JOB_TOKEN` com `GLAB_ENABLE_CI_AUTOLOGIN=true`.
   `GITLAB_HOST` escolhe a instância (omissão: gitlab.com).
2. **Credencial guardada pelo `glab auth login`** — no keyring do sistema (Secret Service/
   KWallet) quando existe, ou em `~/.config/glab-cli/config.yml` (texto simples, por host)
   com `--insecure-storage` ou sem keyring. `glab auth status` diz qual.

**Nesta máquina:** o token vive em `~/.config/glab-cli/config.yml` (0600, pasta 0700),
host `gitlab.com`. Escolheu-se o ficheiro e não o KWallet porque o keyring pode abrir um
diálogo gráfico de desbloqueio e bloquear agentes sem ecrã. É o mesmo modelo do `gh`
(`~/.config/gh/hosts.yml`).

Regra de segurança desta skill: **nenhum comando pode imprimir o valor de um token**.
`glab auth status --show-token`, `glab config get token` e `cat config.yml` estão proibidos em
contexto de agente. Para passar um token a outro programa use pipe/stdin, nunca argumento.

## 2. Login

```bash
glab auth login                                            # interativo (token ou web/OAuth)
glab auth login --hostname gitlab.com --stdin < token.txt  # PAT por stdin (recomendado)
glab auth login --hostname gitlab.com --stdin --git-protocol https --api-protocol https \
  --insecure-storage < token.txt                           # sem keyring (agentes headless)
glab auth login --hostname gitlab.com --web                # OAuth no browser
glab auth login --hostname gitlab.com --device             # OAuth device flow (GitLab ≥ 17.9)
glab auth login --hostname gitlab.empresa.com --api-host gitlab.empresa.com:443 --stdin < t.txt
glab auth status                                           # confirma (token mascarado)
glab auth logout --hostname gitlab.com
```

Nunca `--token <valor>` na linha de comandos (fica no histórico e no `ps`). Se o token
chegou pelo chat, passe-o com `printf '%s' '…' | glab auth login --stdin …` (o `printf` é
builtin: não aparece na lista de processos) e peça ao dono para o rodar depois.

## 3. Tipos de token do GitLab

| Prefixo | Tipo | Notas |
|---|---|---|
| `glpat-` | Personal/Project/Group access token | O comum. Formatos novos trazem sufixo de routing (`….01.…`). |
| `gloas-` | Segredo de aplicação OAuth | |
| `gldt-` | Deploy token | Só registry/repositório, por projeto/grupo. |
| `glrt-` / `glrtr-` | Runner authentication token | |
| `glcbt-` | CI/CD job token | Efémero, por job. |
| `glptt-` | Pipeline trigger token | |
| `glft-` | Feed token | |

Todos são redigidos pelos scripts desta skill (`[VALOR OCULTO]`).

### Scopes (PAT) por operação

| Operação | Scope mínimo |
|---|---|
| Ler projetos/MR/issues/pipelines pela API | `read_api` |
| Criar/editar MR, issues, releases, variáveis, labels; `glab api -X POST/PUT/DELETE` | `api` |
| `git clone/fetch` por HTTPS | `read_repository` |
| `git push` por HTTPS | `write_repository` |
| Ler o próprio utilizador | `read_user` (incluído em `api`) |
| Registry de contentores | `read_registry` / `write_registry` |
| Criar runners | `create_runner` · gerir: `manage_runner` |
| Rodar o próprio token | `self_rotate` |
| GitLab Duo | `ai_features` |

Ver scopes e validade do token atual (só leitura, nunca o valor):
`glab api personal_access_tokens/self | jq '{name, scopes, expires_at, active}'`
ou `bash scripts/gitlab-doctor.sh`.

### Papéis (o scope não chega: o utilizador também precisa do papel)

| Papel | Pode |
|---|---|
| Guest (10) | ver issues (projetos privados: pouco mais) |
| Reporter (20) | ler código, issues, MRs; `git clone` |
| Developer (30) | push para branches **não protegidas**, criar MR, correr pipelines |
| Maintainer (40) | push/merge em branches protegidas (se permitido), gerir variáveis, releases, membros |
| Owner (50) | tudo no grupo/projeto, incluindo apagar e transferir |

Ver o papel efetivo: `glab api projects/grupo%2Fprojeto | jq .permissions`
(`project_access` e `group_access`: `access_level`).

## 4. 404 em vez de 403

O GitLab responde **404 Not Found** (e não 403) a projetos/grupos privados quando o token
não tem acesso, para não revelar que existem. Portanto: 404 num projeto que se vê no
browser = o token é de outra conta, é um token de grupo/projeto doutro namespace, ou a
conta não é membro. Confirmar com `glab api user | jq .username` e com
`glab api groups/<grupo> | jq .full_path`.

## 5. Tokens de grupo/projeto e rotação

```bash
glab token list -U @me -F json --jq '.[] | {name, scopes, expires_at, active}'
glab token create bot-ci -R grupo/projeto -A developer -S api,write_repository -D 90d -F json
glab token create bot-grupo -g grupo -A maintainer -S api -D 30d
glab token rotate <nome|id> -U @me       # novo valor; o anterior deixa de funcionar
glab token revoke <nome|id> -U @me       # DESTRUTIVO — confirmar
```

O valor de um token criado/rodado sai **uma única vez** no stdout: encaminhe-o logo para
`glab auth login --stdin` ou para o cofre, sem o mostrar.

Depois de trocar o token: `glab auth login --hostname gitlab.com --stdin < novo.txt` e
`bash scripts/gitlab-doctor.sh`. Nada mais precisa de mudar: o git dos repositórios ligados
com `gitlab-setup-git.sh` vai buscar o token ao `glab` em cada operação.

## 6. git credential helper

O `glab` sabe servir de credential helper do git (`glab auth git-credential`). Esta skill
liga-o com `scripts/gitlab-setup-git.sh`:

```bash
bash scripts/gitlab-setup-git.sh --repo . --test     # ESTE repositório (config local) + teste de leitura
bash scripts/gitlab-setup-git.sh --check --repo .    # só mostra o estado
bash scripts/gitlab-setup-git.sh --undo --repo .     # desliga
bash scripts/gitlab-setup-git.sh --global            # TODOS os remotes https://gitlab.com (pedir OK ao dono)
```

O que escreve (âmbito local ou global):

```ini
[credential "https://gitlab.com"]
    helper =
    helper = "!f() { test \"$1\" = get && /home/…/glab auth git-credential get; }; f"
```

- A linha vazia limpa os helpers herdados (ex.: o `git-credential-guard` global desta
  máquina); a seguinte usa o glab.
- Só `get` é encaminhado: se o git receber um 401 e mandar `erase`, o token do glab fica
  intacto (o guard desta máquina protege o credential-store pelo mesmo motivo).
- **Por repositório** é o padrão porque o git não casa credenciais por prefixo de caminho
  (`credential.https://gitlab.com/grupo.helper` só casaria com esse URL exato). O
  `--global` troca a credencial de TODOS os remotes gitlab.com da máquina.

Nesta máquina, o global para `gitlab.com` é o `git-credential-guard` (credential-store
com proteção contra `erase`), com a credencial `oauth2` usada nos repositórios do grupo
`grupofleury`. Os repositórios ligados com `--repo` usam o token do glab.

SSH em alternativa: `glab ssh-key add ~/.ssh/id_ed25519.pub -t "máquina"` e remote
`git@gitlab.com:grupo/projeto.git` (testar com `ssh -T git@gitlab.com`).

## 7. Boas práticas (obrigatórias nesta skill)

1. Nunca imprimir, commitar, colar em issues/MRs ou escrever em logs valores de token.
2. Preferir tokens com scopes mínimos e validade curta para automação; tokens de projeto/
   grupo para bots.
3. `bash scripts/gitlab-doctor.sh` antes da primeira operação de cada sessão.
4. Em 401/403/404, verificar token, scopes, papel e conta **antes** de concluir que é bug.
5. Variáveis de CI: `glab variable set CHAVE -v "$VAR_JA_EXISTENTE" --masked` — nunca com o
   valor literal num comando que fica no histórico.
