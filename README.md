# gitlab-agent-skill

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Agent skill** que dá a um agente de código o controlo **total** do GitLab pelo terminal:
`glab` (CLI oficial da GitLab) + `git`. Cobre autenticação e tokens (PAT `glpat-`),
projetos e repositórios, branches, *merge requests*, issues, CI/CD (pipelines, jobs,
variáveis, schedules), releases, labels, milestones, snippets e `glab api` (REST/GraphQL).
É a irmã da [`github-agent-skill`](https://github.com/frederico-kluser/github-agent-skill),
com o mesmo contrato.

## Porque existe

Os agentes erram em GitLab de duas formas: inventam sintaxe de `glab`/`API` e tratam
credenciais como texto. Esta skill troca isso por **regras de ouro** (diagnóstico antes
de agir, nunca imprimir tokens, confirmação antes de destruir, projeto explícito) e por
**scripts determinísticos** que retentam, truncam a saída, redigem segredos e devolvem
erros no formato `Erro: … — Solução: …`.

## O que traz

| Caminho | Função |
|---|---|
| `SKILL.md` | contrato da skill: quando usar, regras de ouro, fluxo, mapa de comandos |
| `scripts/gitlab-doctor.sh` | diagnóstico **read-only** da instalação/login/remotos (nunca imprime tokens) |
| `scripts/glab-run.py` | wrapper do `glab`: retries, portão de bytes, redação de segredos, envelope `--json` |
| `scripts/gitlab-setup-git.sh` | configura credenciais git por repositório/global sem deixar tokens em claro |
| `scripts/link-skill-global.sh` | liga a skill em todos os *skill roots* da máquina (`--check`/`--dry-run`) |
| `references/auth.md` | tokens (PAT/group/project), `glab auth`, credenciais git, SSH, OAuth |
| `references/cli-cheatsheet.md` | superfície completa de comandos por domínio, com exemplos |
| `references/troubleshooting.md` | tabela erro → causa → correção (401/403/404, CI, tokens expirados) |
| `assets/` | templates de corpo de MR, de issue e de release notes |

## Instalação

Copie a pasta para um dos diretórios de skills do seu agente (ou ligue-a em todos):

```bash
# um agente específico, por exemplo
ln -s "$PWD" ~/.claude/skills/gitlab-agent-skill

# ou todos os roots detectados na máquina (idempotente)
bash scripts/link-skill-global.sh            # aplicar
bash scripts/link-skill-global.sh --check    # portão (exit code)
```

Pré-requisitos: `glab` (CLI oficial), `git` e Python 3 (só stdlib).

## Gatilhos

Falar em GitLab, `glab`, MR/merge request, pipeline, `.gitlab-ci.yml`, `gitlab.com`,
push para GitLab, token `glpat`, ou erros 401/403/404 do GitLab.

## Segurança

Nenhum script escreve chaves ou tokens em ficheiros ou na saída — o doctor é
estritamente read-only e o wrapper **redige** padrões de token (`glpat-`, `gloas-`,
`glrt-`, …) antes de mostrar qualquer coisa. Tokens entram por variável de ambiente,
`read -s` ou credential helper.

## Licença

MIT — ver [LICENSE](LICENSE).
