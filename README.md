# Status SUS

> Status em tempo real do e-SUS, CADSUS, RNDS e Gov.br.

[![Demo Online](https://img.shields.io/badge/Demo%20Online-status--sus.vercel.app-black.svg?logo=vercel)](https://status-sus.vercel.app)
[![Licença: MIT](https://img.shields.io/badge/Licen%C3%A7a-MIT-emerald.svg)](LICENSE)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)

O **Status SUS** é uma aplicação de observabilidade aberta e leve para secretarias de saúde, gestores do SUS, equipes de TI municipal e desenvolvedores de prontuários eletrônicos (e-SUS PEC e ERPs).

---

### Como Utilizar: Online vs Local

Você pode utilizar o Status SUS de duas formas:

1. **Acesso Online (Sem Instalação):**  
   Acesse diretamente pelo navegador em **[status-sus.vercel.app](https://status-sus.vercel.app)**. Indicado para médicos, enfermeiros, recepcionistas e gestores consultarem rapidamente se a instabilidade é geral no Ministério da Saúde.
2. **Execução Local / Auto-Hospedada (Na Rede da Secretaria ou UBS):**  
   Execute o projeto localmente com Python ou Docker na rede interna. Permite plugar via `config.json` o monitoramento da sua própria instância do e-SUS PEC (PostgreSQL local), sistemas de prontuário municipais e links de rede dedicados.

---

## Visão do Painel

O frontend é minimalista, sem dependências de frameworks CSS externos ou CDNs, com carregamento rápido e suporte nativo a temas claro e escuro (com detecção automática da preferência do sistema operacional e alternância manual salva localmente):

- **Aba Serviços:** Apresenta diagnósticos automáticos cruzando portais e barramentos, listagem de serviços agrupados por camada (Atenção Primária, Integração M2M, Identidade Digital e Gestão Federal), indicadores visuais de disponibilidade (operacional, lento, indisponível), latência e filtro rápido de busca.
- **Aba Tabela:** Visão condensada em formato de tabela técnica com nome do serviço, protocolo, URL do endpoint, código de resposta HTTP e tempo de resposta em milissegundos, incluindo botão para exportar o snapshot completo em JSON.
- **Aba Sobre:** Explicação detalhada da arquitetura do SUS, separação entre interfaces humanas e barramentos de máquina (M2M) e guia de configuração de instâncias locais.
- **Gaveta Lateral de Detalhes:** Painel deslizante com métricas pontuais, impacto clínico, orientação de contingência para a equipe de saúde, comandos prontos para cópia rápida no terminal (`curl` e PowerShell `Test-NetConnection`), inspeção de metadados extraídos (versão JBoss, data de build, validação de contrato WSDL) e cabeçalhos HTTP retornados pelos servidores oficiais. Permite também sondar o endpoint selecionado sob demanda.
- **Paleta de Busca Rápida:** Acionável a qualquer momento via teclado com `Ctrl + K` (ou `⌘K`), facilitando a localização imediata de qualquer serviço.

---

## O Problema que o Status SUS Resolve

Nas unidades de saúde, quando um profissional relata que *"o sistema do SUS caiu"*, frequentemente a equipe local não consegue diagnosticar a causa-raiz imediatamente:

1. **Queda da Interface Humana vs Barramento M2M:** Muitas vezes o portal web do CADSUS (`cadastro.saude.gov.br/novocartao/`) está instável para atendentes no navegador, mas o barramento WebService SOAP (`servicos.saude.gov.br/.../v5r0?wsdl`) consultado pelos prontuários eletrônicos em segundo plano continua operacional.
2. **Recepção de Lotes e-SUS:** Identifica se o endpoint nacional (`esusab.saude.gov.br`) está aceitando transmissões de lotes LEDI/Thrift da Atenção Primária e extrai a versão real e data de build informadas pelo servidor nacional.
3. **Autenticação Gov.br:** Monitora o provedor OAuth2/OIDC central (`sso.acesso.gov.br`), alertando sobre instabilidades que impactam em cascata o login no e-Gestor APS, CNES, InvestSUS e Meu SUS Digital.

---

## Stack Tecnológica

O projeto foi intencionalmente arquitetado para eliminar complexidade operacional e dependências pesadas:

### Backend
- **Linguagem:** Python 3.10+ (compatível com 3.11 e 3.12).
- **Runtime:** Zero dependências de terceiros (utiliza exclusivamente a biblioteca padrão do Python).
  - `http.server.ThreadingHTTPServer`: Servidor HTTP multithreaded.
  - `concurrent.futures.ThreadPoolExecutor`: Sondagem concorrente de múltiplos endpoints em paralelo.
  - `urllib.request` & `ssl`: Handshake TLS, leitura controlada de buffers para inspeção de cabeçalhos e metadados.
  - `socket`: Suporte nativo a sondagens TCP diretas para bancos de dados ou instâncias locais.
- **Arquitetura de Varredura e Cache:**
  - Varredura periódica em segundo plano (background thread) respeitando o intervalo configurado (`pollIntervalMs`).
  - O endpoint `/api/status` atende a partir de cache em memória com respostas instantâneas.
  - Compatibilidade com ambientes serverless (Vercel) com revalidação automática caso o snapshot tenha mais de 30 segundos.
  - Dados estritamente reais: versão e data de build começam como `null` e o histórico começa vazio até a primeira medição efetiva.

### Frontend
- **Interface:** HTML5 semântico e Vanilla JavaScript nativo em `public/index.html`.
- **Estilização:** CSS moderno puro com variáveis CSS para tema claro e escuro, sem Tailwind CSS ou bibliotecas externas.
- **Zero build:** Sem dependências de Node.js, Webpack, Vite ou CDNs externas.
- **Estrutura estática:** Servido diretamente da pasta `public/` (pasta `static/` descontinuada).

---

## Barramentos e Endpoints Monitorados

| Camada | Serviço / Barramento | Protocolo | Endpoint Oficial | Validação Heurística |
| :--- | :--- | :--- | :--- | :--- |
| **1. Atenção Primária** | Centralizador Nacional e-SUS APS | HTTPS / Thrift | `https://esusab.saude.gov.br/` | Extração de versão JBoss, data de build e cabeçalhos |
| | SISAB (Relatórios de Produção) | HTTPS (JSF) | `https://sisab.saude.gov.br/` | Status HTTP 200 e latência |
| | e-Gestor APS (Novo Financiamento) | HTTPS / REST | `https://egestoraps.saude.gov.br/` | Disponibilidade dos relatórios da Portaria 3.493/2024 |
| | e-Gestor AB Legado | HTTPS | `https://egestorab.saude.gov.br/` | Módulos sociais (Bolsa Família / PSE) |
| **2. Integração M2M** | Barramento SOA-CADSUS | SOAP XML | `https://servicos.saude.gov.br/cadsus/.../v5r0?wsdl` | Validação do contrato XML `<wsdl:definitions>` do CadsusService |
| | CADSUS Web (Portal Humano) | HTTPS | `https://cadastro.saude.gov.br/novocartao/` | Rota canônica de emissão de CNS |
| | RNDS (Rede Nacional de Dados em Saúde) | HL7 FHIR R4 | `https://rnds-fhir.saude.gov.br/` | Barramento de vacinação e sumários clínicos |
| | RNDS Auth (Gateway EHR-Auth) | mTLS / JWT | `https://ehr-auth.saude.gov.br/api/token` | Checagem de retorno HTTP 401 (Gateway ativo e autenticado) |
| **3. Identidade Digital** | SSO Gov.br | OAuth 2.0 / OIDC | `https://sso.acesso.gov.br/` | Provedor central de login |
| | Serviços de Conta Gov.br | HTTPS | `https://servicos.acesso.gov.br/` | Validação de 2FA e selos Prata/Ouro |
| **4. Gestão Federal** | CNES (Estabelecimentos de Saúde) | HTTPS | `https://cnes.datasus.gov.br/` | Consulta de equipes (INE), CBO e estabelecimentos |
| | FNS Consulta (Repasses Fundo a Fundo) | HTTPS | `https://consultafns.saude.gov.br/` | Repasses financeiros e ordens bancárias |
| | InvestSUS Gestão | HTTPS | `https://investsus.saude.gov.br/` | Emendas parlamentares e convênios |
| | SISMAC (Teto MAC) | HTTPS | `https://sismac.saude.gov.br/` | Teto financeiro de média e alta complexidade |
| | DigiSUS Gestor / DGMP | HTTPS / SPA | `https://digisusgmp.saude.gov.br/` | Instrumentos de gestão (PMS, PAS, RAG) |

---

## Como Executar

### Opção 1: Execução Direta com Python

```bash
# 1. Clone o repositório
git clone https://github.com/weesrnp/status-sus.git
cd status-sus

# 2. Inicie o servidor
python server.py
```

Acesse no seu navegador: `http://127.0.0.1:8765` (ou no host e porta configurados).

---

### Opção 2: Execução no Windows (`iniciar.bat`)

Dê dois cliques no arquivo `iniciar.bat` na raiz do projeto. Ele detecta automaticamente o executável `py` ou `python` disponível no PATH do sistema, inicia o servidor e abre o painel no navegador padrão.

---

### Opção 3: Execução via Docker / Docker Compose

O repositório inclui `Dockerfile` (baseado em Alpine Linux com usuário não-root, copiando a pasta `public/`) e `docker-compose.yml`:

```bash
# Iniciar em segundo plano
docker compose up -d

# Visualizar logs
docker compose logs -f
```

---

## Configuração Customizada (`config.json`)

O arquivo `config.json` permite configurar parâmetros do servidor e cadastrar serviços locais (como instâncias municipais do e-SUS PEC, bancos PostgreSQL ou serviços estaduais):

```json
{
  "server": {
    "port": 8765,
    "host": "0.0.0.0",
    "pollIntervalMs": 30000
  },
  "customEndpoints": [
    {
      "id": "meu-pec-local",
      "name": "e-SUS PEC Local (Servidor da Secretaria)",
      "shortName": "PEC Municipal",
      "layer": "municipal_local",
      "layerLabel": "Instâncias Municipais & Serviços Locais",
      "protocol": "TCP / PostgreSQL",
      "type": "Banco de Dados Local",
      "url": "tcp://192.168.1.100:5433",
      "tcpHost": "192.168.1.100",
      "tcpPort": 5433,
      "expectedCodes": [200],
      "warnLatencyMs": 150,
      "clinicalImpact": "Banco de dados local do prontuário eletrônico municipal.",
      "recoveryHint": "Verifique se a máquina servidora está ligada e acessível na rede interna.",
      "enabled": true
    }
  ]
}
```

### Parâmetros de Servidor
- **`server.port`**: Porta na qual o servidor HTTP atenderá (padrão `8765`). Variáveis de ambiente `STATUS_SUS_PORT` ou `PORT` têm precedência se definidas.
- **`server.host`**: Interface de rede para bind (padrão `"0.0.0.0"`).
- **`server.pollIntervalMs`**: Intervalo em milissegundos entre as varreduras automáticas em segundo plano (padrão `30000`).

---

## API REST Integrada

O servidor expõe endpoints JSON para integração com sistemas de monitoramento (Zabbix, Prometheus, Grafana) ou consultas automatizadas:

- **`GET /api/status`**: Retorna o snapshot completo em cache com o estado de todos os serviços, diagnósticos cruzados e sumário consolidado.
  - Servido a partir do cache da última varredura em background. Em ambientes serverless (Vercel), aciona nova varredura apenas se o snapshot tiver mais de 30 segundos.
  - **Parâmetro `?force=1`**: Força uma nova varredura concorrente em tempo real. Limitado por rate limit a 1 execução a cada 10 segundos. Chamadas repetidas antes desse intervalo retornam o snapshot atual do cache com `"forceThrottled": true`.
  - **Campos adicionais de controle**:
    - `ttlSeconds`: Tempo de vida estimado do snapshot em segundos.
    - `updatedAtIso`: Timestamp ISO 8601 em UTC da conclusão da varredura.
    - `forceThrottled`: Booleano indicando se a requisição de força foi limitada pelo rate limit.
  - **Dados confiáveis**: versão e data de build ficam `null` e o histórico começa vazio (com `uptimePct` `null`) até a primeira medição real. Nenhum dado é simulado.
- **`GET /api/probe/{service_id}`**: Executa uma checagem individual sob demanda para o serviço especificado e atualiza seu registro no snapshot.

---

## Testes

O projeto conta com uma suíte de testes unitários offline para validação de catálogo, diagnósticos, sondagens HTTP/TCP, parsers de contrato WSDL/versões e controle de rate limiting:

```bash
python -m unittest discover -s tests -v
```

---

## Licença

Este projeto é disponibilizado sob a licença **MIT**. Consulte o arquivo [LICENSE](LICENSE) para mais detalhes.

---

*Nota: O Status SUS é uma ferramenta independente desenvolvida para apoiar a observabilidade da infraestrutura digital pública de saúde, sem vínculo institucional direto com o Ministério da Saúde ou DATASUS.*
