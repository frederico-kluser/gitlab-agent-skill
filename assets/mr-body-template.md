<!-- Modelo de descrição de Merge Request — usar com: glab mr create --description-file assets/mr-body-template.md -->
## Objetivo

<!-- O QUE este MR faz e PORQUÊ. 1–3 frases. Ligar a issue: "Closes #34". -->

## Alterações

- <!-- mudança 1 (ficheiro/componente principal) -->
- <!-- mudança 2 -->

## Como verificar

```bash
# comandos para reproduzir/testar localmente
```

## Checklist

- [ ] Testes locais executados (sem regressões)
- [ ] Pipeline verde (`glab ci status --wait`)
- [ ] Documentação/README atualizada (se aplicável)
- [ ] Nenhum segredo/token adicionado ao código, ao histórico ou às variáveis de CI em claro
- [ ] Commits com mensagens claras

## Riscos / Notas

<!-- rollback, migrações, dependências, screenshots -->
