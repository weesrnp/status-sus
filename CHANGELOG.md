# Changelog

## 2026-10-23 — Revisão geral: UI minimalista, cache no backend e testes

PR [#1](https://github.com/weesrnp/status-sus/pull/1), branch `refactor/ui-minimalista`.

### Problemas encontrados na revisão e como foram corrigidos

| # | Problema | Impacto | Correção |
|---|---|---|---|
| 1 | O frontend chamava `/api/status?force=1` a cada 30s, então **cada visitante** disparava uma varredura completa dos 15 endpoints do governo | Com o demo público, a carga nos servidores do Ministério crescia com o número de visitantes | `/api/status` passou a servir o último resultado. O servidor local varre em background a cada `pollIntervalMs`; na Vercel só varre se o resultado tiver mais de 30s. `?force=1` ficou limitado a 1 varredura a cada 10s (fora disso devolve o cache com `forceThrottled: true`) |
| 2 | Ao iniciar, o servidor inventava 12 medições "operacionais" por serviço (`seed_initial_history`) | As barras de disponibilidade e o percentual mostravam 100% sem nenhuma medição real | Função removida. O histórico começa vazio e `uptimePct` fica `null` até a primeira medição |
| 3 | Versão (`5.7.36`), data de build e pool F5 do Centralizador estavam fixos no código e eram usados quando a extração falhava | O painel podia exibir uma versão que não era a do servidor | Valores fixos removidos. Os campos ficam `null` quando não são extraídos; o pool F5 é lido do cookie real |
| 4 | O WSDL do CADSUS era validado por busca de texto | Uma página HTML de erro com as palavras "definitions" e "CadsusService" passava como contrato válido | Validação com `xml.etree`: exige a raiz `wsdl:definitions` e um elemento `CadsusService`. As operações listadas vêm do próprio WSDL |
| 5 | `server.port`, `server.host` e `server.pollIntervalMs` do `config.json` eram ignorados | Configurar a porta ou o intervalo pelo arquivo não tinha efeito | Os três são respeitados; `STATUS_SUS_PORT`/`PORT` continuam com prioridade sobre a porta |
| 6 | `public/` e `static/` eram cópias idênticas; o Dockerfile copiava `static/`, mas o servidor preferia `public/` | Risco de editar um arquivo e publicar o outro | `static/` removida; o Dockerfile copia `public/` |
| 7 | `iniciar.bat` apontava para `C:\Users\wesle\AppData\...\python.exe` | Só funcionava na máquina original | Usa `py` ou `python` do PATH |
| 8 | O User-Agent das sondagens apontava para `github.com/open-sus/status-sus` | Os administradores dos servidores consultados não chegariam ao projeto certo | Aponta para `github.com/weesrnp/status-sus` |
| 9 | No celular (abaixo de 768px) a navegação por abas sumia | Não havia como chegar à tabela nem à página Sobre | Abas sempre visíveis, com rolagem horizontal se faltar espaço |
| 10 | Conteúdo dinâmico (nomes, URLs, rótulos do `config.json`) era inserido com `innerHTML` sem escape | Um `config.json` com HTML quebraria a página ou injetaria código | Todo conteúdo dinâmico passa por escape |
| 11 | Gaveta de detalhes e busca (Ctrl+K) não controlavam o foco | Com teclado, o Tab saía da gaveta e alcançava o campo da busca fechada | `role="dialog"` e `aria-modal`; o resto da página fica `inert` enquanto estão abertas; o foco entra ao abrir e volta ao fechar |
| 12 | Tailwind carregado por CDN em produção, com classes inválidas (`py-0.2`) | Aviso no console, dependência externa e CSS gerado no navegador | CSS próprio com variáveis; sem CDN e sem fontes externas |
| 13 | Os três arquivos de `api/` repetiam o mesmo handler | Correções precisavam ser feitas em três lugares | Handler único em `api/_common.py`; cada função coloca a raiz do projeto no `sys.path` antes de importar |

### Interface

A página foi reescrita no formato de status page, com menos elementos:

- Uma frase de status em destaque ("Todos os sistemas operacionais" ou "N serviços indisponíveis"), com a hora da última verificação e a contagem até a próxima
- Serviços em listas por camada, cada um com status, latência e barra das últimas 30 medições, no lugar dos cards com texto longo e dos quatro KPIs
- Abas **Serviços / Tabela / Sobre**; o diagrama "Fluxo de Integração" virou uma lista curta na aba Sobre
- Tema claro e escuro: segue o sistema e tem botão para alternar
- Contraste de texto ajustado para WCAG AA (4,5:1) nos dois temas
- A gaveta de detalhes mantém impacto, orientação, comando de terminal, metadados e cabeçalhos HTTP; os dois últimos ficam recolhidos

### Contrato da API

`GET /api/status` mantém os campos anteriores e acrescenta:

| Campo | Descrição |
|---|---|
| `ttlSeconds` | Intervalo entre varreduras, em segundos |
| `updatedAtIso` | Horário da última varredura em ISO 8601 (UTC) |
| `forceThrottled` | `true` quando um `?force=1` foi recusado pelo limite de 10s |

Campos que antes tinham valores simulados agora podem ser `null`: `centralizadorMeta.*`, `uptimePct`, `serverHeader`, `summary.healthScorePct` (sem serviços).

### Testes

`tests/test_server.py` com 28 testes `unittest`, sem acesso à internet (`urlopen` e `socket` são simulados):

- classificação de status (rápido, lento, mTLS 401, HTTP 500, timeout, TCP aberto/recusado)
- validação de WSDL, inclusive o falso positivo do item 4
- extração de versão e build do Centralizador, e `null` quando não há extração
- os três cenários do diagnóstico CADSUS
- resumo e `healthScorePct` da varredura
- leitura do `config.json`
- limite do `?force=1`, cache e expiração de 30s na Vercel
- `404` para serviço desconhecido no `/api/probe`

```bash
python -m unittest discover -s tests -v
```

### Verificação

- Testes: 28/28 passando
- Interface testada no navegador em 1280px e 390px, temas claro e escuro: gaveta, foco por teclado, Ctrl+K, filtro e aba Tabela, sem erros no console
- Deploy de preview na Vercel: build concluído com sucesso (a URL de preview exige login da Vercel, então a API não foi chamada por fora)
- Não testado: build do Docker

### Como o trabalho foi dividido

A revisão foi feita no Maestri, com cinco agentes trabalhando em arquivos separados para não haver conflito:

| Agente | Responsabilidade |
|---|---|
| Claude Code | Revisão inicial, contrato da API, nova interface, testes visuais no navegador, revisão final e integração |
| Codex-L | Backend: `server.py`, `api/`, Dockerfile, `iniciar.bat` |
| Codex #2 | Suíte de testes e QA da interface (desktop, celular, teclado). Encontrou o falso positivo do WSDL e os problemas de foco da gaveta |
| Antigravity | Atualização do README |
| Antigravity #2 | Análise do deploy na Vercel (import de `api/_common.py`, empacotamento de `server.py`/`config.json`, `public/` como estático) |
