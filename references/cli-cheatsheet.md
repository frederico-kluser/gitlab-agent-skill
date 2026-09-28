# cli-cheatsheet.md — superfície completa de comandos (glab + git)

Referência por domínio, conferida contra `glab 1.119.0` (a superfície muda entre versões:
em dúvida, `glab <comando> --help`). Todos os comandos `glab` assumem autenticação válida
(ver `auth.md`). Flags e ambiente transversais:

| Flag/Env | Efeito |
|---|---|
| `-F json` / `--output json` | saída JSON estável (ideal para agentes); em `issue list` é `-O json` |
| `--jq '.expr'` | filtra o JSON em `mr/issue/ci/release/repo list·view`; **`glab api` não tem `--jq`**: usar `glab api … \| jq '.expr'` |
| `-R grupo/sub/projeto` | projeto alvo (aceita também URL completo ou URL git) |
| `-g grupo` | grupo alvo em `mr list`, `issue list`, `repo list`, `variable`, `label`, `milestone` |
| `GITLAB_HOST=gitlab.empresa.com` | instância self-managed (omissão: gitlab.com) |
| `GITLAB_TOKEN=…` | token por ambiente (precedência sobre o config; nunca ecoar) |
| `GLAB_NO_PROMPT=1` · `NO_COLOR=1` | sem prompts e sem cor (o `glab-run.py` já os define) |
| `--paginate` | todas as páginas em `glab api` |
| `projects/grupo%2Fprojeto` | forma de referir um projeto por caminho na API (URL-encoded) |

---

## 1. Auth e tokens (ver `auth.md` para o fluxo completo)

```bash
glab auth login                                   # interativo (token ou OAuth web)
glab auth login --hostname gitlab.com --stdin < token.txt   # PAT por stdin (não fica no histórico)
glab auth login --hostname gitlab.com --device    # OAuth device flow (sem browser local)
glab auth status                                  # hosts, utilizador, onde está o token (mascarado)
glab auth logout --hostname gitlab.com
glab api personal_access_tokens/self | jq '{name, scopes, expires_at}'   # scopes/validade do token atual
glab token list -U @me -F json --jq '.[] | {name, scopes, expires_at}'   # PATs do utilizador
glab token create nome -U @me -S read_api -D 30d -F json  # cria PAT (o valor sai UMA vez — nunca ecoar)
glab token rotate <nome|id> -U @me                # roda (novo valor, o antigo morre)
glab token revoke <nome|id> -U @me                # DESTRUTIVO — confirmar
glab token create ci-bot -R grupo/projeto -A developer -S api   # token de projeto (-g grupo: de grupo)
```

## 2. Projetos / repositórios

```bash
glab repo create nome --private --group grupo --description "…" --defaultBranch main
glab repo create nome --private -s                # só cria no GitLab (sem git init/clone local)
glab repo clone grupo/projeto [dir]
glab repo clone -g grupo -p --paginate            # clona TODOS os projetos de um grupo (preserva namespace)
glab repo view [grupo/projeto] -F json --jq '{path_with_namespace, default_branch, visibility, web_url}'
glab repo list -g grupo -G -F json --jq '.[].path_with_namespace'   # projetos do grupo (+ subgrupos)
glab repo list --member -P 100 -F json            # projetos onde sou membro
glab repo search -s texto                          # procurar projetos por nome
glab repo fork grupo/projeto --clone=false
glab repo update grupo/projeto --description "…" --defaultBranch main
glab repo update grupo/projeto --archive=true      # ARQUIVA o projeto (reversível com =false)
glab repo archive grupo/projeto ./dir              # atenção: DESCARREGA um tarball (não arquiva)
glab repo transfer grupo/projeto --target-namespace outro-grupo   # DESTRUTIVO p/ URLs — confirmar
glab repo delete grupo/projeto --yes               # DESTRUTIVO — confirmar sempre
glab repo members add -R grupo/projeto --username utilizador -r developer
glab repo mirror grupo/projeto --url https://… --direction push
glab repo prune                                    # apaga branches locais cujo MR já foi mergeado
```

## 3. Git (estado local — comandos essenciais)

```bash
git status -sb                        # branch + divergência em uma linha
git switch -c feat/x main             # nova branch a partir de main
git add -A && git commit -m "msg"
git push -u origin feat/x             # publica branch e define upstream
git push -o merge_request.create -o merge_request.target=main   # push options: cria MR no push
git pull --rebase                     # atualiza sem merge commit
git pull origin main --allow-unrelated-histories   # juntar a um projeto recém-criado com README
git rebase main · git merge main · git stash / git stash pop
git tag -a v1.2.0 -m "v1.2.0" && git push origin v1.2.0
git push --force-with-lease           # só em branches PRÓPRIAS; nunca em protegidas
```

Push HTTPS com o token do glab: `bash scripts/gitlab-setup-git.sh --repo . --test`.

## 4. Merge requests

```bash
glab mr create --fill --yes -b main                 # título/descrição dos commits; faz push da branch
glab mr create -t "Título" --description-file assets/mr-body-template.md -b main -s feat/x \
  --reviewer ana --assignee @me -l bug --remove-source-branch --squash-before-merge --yes
glab mr create --draft --fill --yes                 # MR em rascunho
glab mr list -F json --jq '.[] | {iid, title, source_branch, state}'
glab mr list --reviewer @me · --author @me · -s feat/x · -t main · -M (merged) · -c (closed)
glab mr view 12 -F json --jq '{iid, state, merge_status, head_pipeline: .head_pipeline.status}'
glab mr view 12 --comments --unresolved             # discussões por resolver
glab mr diff 12 --raw                               # diff pipeable
glab mr checkout 12                                 # traz a branch do MR para o local
glab mr approve 12 · glab mr revoke 12              # aprovar / retirar aprovação
glab mr note create 12 -m "LGTM"                    # comentário (EXPERIMENTAL no 1.119)
glab mr note create 12 --file src/a.py --line 42 -m "…"   # comentário numa linha do diff
glab mr note list 12 · glab mr note resolve <discussion-id> 12
glab mr update 12 --ready · --draft · -t "novo título" · --target-branch main · -l label · -u label
glab mr rebase 12                                   # rebase da origem sobre o alvo (no servidor)
glab mr merge 12 --squash --remove-source-branch --yes      # auto-merge por omissão quando o CI passar
glab mr merge 12 --auto-merge=false --yes           # merge imediato (se os checks já passaram)
glab mr merge 12 --sha <sha> --yes                  # só se o HEAD for o revisto
glab mr close 12 · glab mr reopen 12 · glab mr delete 12 (DESTRUTIVO)
glab mr for 34                                      # cria MR (e branch) para a issue 34
```

## 5. Issues

```bash
glab issue create -t "Título" --description-file assets/issue-body-template.md -l bug -a @me --yes
glab issue list -O json · --assignee @me · -l bug · -c (fechadas) · -A (todas) · --search texto
glab issue list -g grupo -O json                    # issues de um grupo
glab issue view 34 -F json · glab issue view 34 --comments
glab issue note 34 -m "comentário"
glab issue update 34 -l doing -u todo -a @me --milestone "Sprint 5"
glab issue close 34 · glab issue reopen 34 · glab issue delete 34 (DESTRUTIVO)
glab issue board view                               # quadro de issues (TUI)
```

## 6. CI/CD — pipelines, jobs, variáveis, schedules

```bash
glab ci status                                      # pipeline da branch atual
glab ci status -b main -F json · glab ci status --live · --wait
glab ci list -F json --jq '.[] | {id, status, ref}' · -s failed · -r main
glab ci get -p 123 -F json -d                       # pipeline + detalhe dos jobs
glab ci view -b main                                # TUI: ver/correr/retry/cancel jobs
glab ci trace <job-id|job-name> -p 123              # log do job (tempo real)
glab ci run -b main --variables CHAVE:valor         # novo pipeline (-i para inputs)
glab ci run --mr                                    # pipeline de MR
glab ci retry <job-id|job-name> -p 123
glab ci cancel pipeline 123 · glab ci cancel job 456
glab ci trigger <job-name> -p 123                   # job manual
glab ci delete 123 (DESTRUTIVO)
glab ci lint                                        # valida .gitlab-ci.yml
glab ci lint --dry-run --ref main --include-jobs    # simula a criação do pipeline
glab ci artifact main build-job                     # artefactos do último pipeline da ref
glab variable list -F json · glab variable list -g grupo
glab variable get CHAVE
glab variable set CHAVE -v "$VALOR" --masked --protected   # valor por variável já existente — nunca literal
glab variable update CHAVE "$VALOR" · glab variable delete CHAVE (DESTRUTIVO)
glab variable export > vars.json (contém VALORES — tratar como segredo) · glab variable import < vars.json
glab schedule list · glab schedule create --cron "0 3 * * *" --ref main --description "noturno"
glab schedule run <id> · glab schedule update <id> --active=false · glab schedule delete <id>
glab runner list · glab securefile list
```

## 7. Releases e changelog

```bash
glab release create v1.2.0 -N "notas" -r main              # cria a tag a partir de main se não existir
glab release create v1.2.0 -F assets/release-notes-template.md ./dist/*.tar.gz
glab release list -F json · glab release view v1.2.0
glab release upload v1.2.0 ./dist/app.tar.gz
glab release download v1.2.0 -D ./dl
glab release delete v1.2.0 (DESTRUTIVO; a tag fica — apagar com git push origin :refs/tags/v1.2.0)
glab changelog generate -v 1.2.0 --from <sha> --to <sha>    # com trailers "Changelog:" nos commits
```

## 8. API (REST e GraphQL)

```bash
glab api user | jq .username
glab api projects/grupo%2Fprojeto | jq '{id, default_branch, permissions}'
glab api "projects/grupo%2Fprojeto/merge_requests?state=opened" --paginate | jq '.[].iid'
glab api "groups/grupo/projects?include_subgroups=true&per_page=100" --paginate | jq '.[].path_with_namespace'
glab api -X POST projects/grupo%2Fprojeto/issues -f title="…" -f description="…"
glab api -X PUT projects/grupo%2Fprojeto -f default_branch=main
glab api -X POST projects/grupo%2Fprojeto/archive          # arquivar projeto (DESTRUTIVO-reversível)
glab api -X DELETE projects/grupo%2Fprojeto/repository/branches/feat%2Fx   # DESTRUTIVO — confirmar
glab api projects/grupo%2Fprojeto/protected_branches | jq '.[] | {name, allow_force_push}'
glab api graphql -f query='query { currentUser { username } }'
glab api -i user                                           # inclui cabeçalhos (RateLimit-*)
```

Notas: `-f` = string, `-F` = tipo inferido (número/booleano/`@ficheiro`); passar `-f`/`-F`
muda o método para POST. `--input ficheiro.json` para corpos complexos.

## 9. Outros domínios

```bash
glab label list -F json · glab label create -n bug -c "#d9534f" · glab label delete bug
glab milestone list · glab milestone create --title "Sprint 5" --due-date 2026-10-15
glab snippet create -t "título" ficheiro.py -p (privado) · glab snippet create -t t -f nome < stdin
glab deploy-key list · glab deploy-key add chave.pub -t "ci" --can-push=false
glab ssh-key list · glab gpg-key list
glab todo list · glab user events
glab search semantic -q "onde se valida o token"           # BETA
glab alias set mrs 'mr list --reviewer=@me' · glab alias list
glab incident list · glab iteration list -g grupo
glab config get git_protocol · glab config set git_protocol https --host gitlab.com
glab check-update · glab version
```

## 10. Padrões para agentes

- Ler: `python3 scripts/glab-run.py --json -- <comando> -F json` → `ok`, `stdout`, `erro`, `solucao`.
- Descobrir o projeto do repo atual: `glab repo view -F json --jq .path_with_namespace`.
- Esperar pelo CI de um MR: `glab ci status --wait` na branch, ou `glab mr view <iid> -F json --jq .head_pipeline.status`.
- Antes de qualquer DELETE/force/merge: ler o alvo (`glab api …` GET) e mostrar o plano ao dono.
- Respostas grandes (ex.: `repository/files/<f>` devolve o ficheiro inteiro em base64) passam o portão de 32 KB do
  wrapper e chegam truncadas (JSON inválido): subir `--max-bytes`, ou pedir só metadados com
  `glab api -X HEAD -i "projects/<p>/repository/files/<f-url-encoded>?ref=main"` (cabeçalhos `X-Gitlab-Size`,
  `X-Gitlab-Blob-Id`), ou listar com `repository/tree?path=<dir>`.
