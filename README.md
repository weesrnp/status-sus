# Status SUS

> **Painel de Observabilidade, Telemetria e Disponibilidade de Barramentos de Saúde Pública (DATASUS / Ministério da Saúde / Gov.br)**

[![License: MIT](https://img.shields.io/badge/License-MIT-emerald.svg)](LICENSE)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Docker: Ready](https://img.shields.io/badge/Docker-Ready-2496ED.svg)](#-execu%C3%A7%C3%A3o-via-docker)
[![Dependencies: Zero](https://img.shields.io/badge/Dependencies-Zero%20(Standard%20Library)-sky.svg)](#-stack-tecnol%C3%B3gica)

O **Status SUS** é uma aplicação de observabilidade independente, leve e de código aberto voltada para secretarias de saúde, gestores do SUS, equipes de TI municipal e desenvolvedores de prontuários eletrônicos (e-SUS PEC e ERPs).

Monitora em tempo real a latência, códigos de resposta HTTP/SOAP, status de gateways criptográficos e a disponibilidade dos principais serviços digitais de saúde do Brasil.

---

## 📸 Visão do Painel

- **Visão Geral:** Cards com latência em milissegundos, gráficos sparkline SVG, barras de SLA de 24 amostras e diagnóstico de causa-raiz.
- **Matriz de Telemetria:** Tabela técnica de alta densidade inspirada em consoles de NOC/DevOps com cópia de comandos e ping individual.
- **Fluxo de Integração:** Diagrama arquitetural detalhando o trajeto dos dados: *Unidade Básica (UBS) ➔ Prontuário Eletrônico ➔ Barramentos M2M (CADSUS / RNDS) ➔ Centralizador Nacional e-SUS ➔ Base Federal (SISAB)*.
- **Gaveta de Inspeção (Slide-over):** Exibição de cabeçalhos HTTP reais retornados pelos servidores do Ministério da Saúde, metadados de build do Centralizador, validador de contratos WSDL e gerador de comandos `curl` e `Test-NetConnection`.

---

## 🎯 O Problema que o Status SUS Resolve

Nas unidades de saúde, quando um profissional relata que *"o sistema do SUS caiu"*, frequentemente a equipe local não consegue diagnosticar a causa-raiz imediatamente:
1. **Queda da Interface Humana vs Barramento M2M:** Muitas vezes o portal web do CADSUS (`cadastro.saude.gov.br/novocartao/`) está instável para atendentes no navegador, mas o barramento WebService SOAP (`servicos.saude.gov.br/.../v5r0?wsdl`) consultado pelos prontuários eletrônicos em segundo plano continua 100% operacional.
2. **Recepção de Lotes e-SUS:** Identifica se o endpoint nacional (`esusab.saude.gov.br`) está aceitando transmissões de lotes LEDI/Thrift da Atenção Primária e extrai ao vivo a versão em execução do JBoss nacional (ex: `v5.7.36`).
3. **Autenticação Gov.br:** Monitora o provedor OAuth2/OIDC central (`sso.acesso.gov.br`), alertando sobre instabilidades que impactam em cascata o login no e-Gestor APS, CNES, InvestSUS e Meu SUS Digital.

---

## 🛠️ Stack Tecnológica

O projeto foi intencionalmente arquitetado para **eliminar complexidade operacional e dependências pesadas**:

### Backend
- **Linguagem:** Python 3.10+ (Compatível com 3.11 e 3.12).
- **Runtime:** **Zero Dependências de Terceiros** (Utiliza exclusivamente a biblioteca padrão do Python).
  - `http.server.ThreadingHTTPServer`: Servidor HTTP multithreaded ultraleve com baixa pegada de memória (~20 MB de RAM).
  - `concurrent.futures.ThreadPoolExecutor`: Sondagem assíncrona concorrente de todos os 15 endpoints em paralelo em menos de 1,8 segundos.
  - `urllib.request` & `ssl`: Handshake TLS, leitura controlada de buffers parciais (12 KB) para parser de versões e validação de cabeçalhos F5 BIG-IP.
  - `socket`: Suporte nativo a sondagens via socket TCP direto para bancos de dados ou instâncias locais.

### Frontend
- **Interface:** HTML5 Semântico com Vanilla JavaScript nativo (sem overhead de build, Webpack ou Node.js).
- **Estilização:** Tailwind CSS (modo Dark com paleta sóbria de observabilidade).
- **Tipografia:** `Inter` (leitura clínica/administrativa) e `JetBrains Mono` (telemetria, tempos em ms e comandos).
- **Componentes:**
  - Command Palette modal com acionamento via `Ctrl + K` / `⌘K`.
  - Inspetor lateral de pacotes HTTP/TLS com cópia de comando em 1 clique.
  - Gráficos vetoriais SVG inline interpolados dinamicamente para cada serviço.

---

## 🛰️ Barramentos e Endpoints Monitorados

| Camada | Serviço / Barramento | Protocolo | Endpoint Oficial | Validação Heurística |
| :--- | :--- | :--- | :--- | :--- |
| **1. Atenção Primária** | Centralizador Nacional e-SUS APS | HTTPS / Thrift | `https://esusab.saude.gov.br/` | Extração de versão JBoss (ex: `v5.7.36`), data de build e pool F5 |
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

## 🚀 Como Executar

### Opção 1: Execução Direta com Python

```bash
# 1. Clone o repositório
git clone https://github.com/weesrnp/status-sus.git
cd status-sus

# 2. Inicie o servidor
python server.py
```

Acesse no seu navegador: **`http://127.0.0.1:8765`** (ou `http://localhost:8765`).

---

### Opção 2: Execução com 1 Clique (Windows)

Dê dois cliques no arquivo **`iniciar.bat`** na raiz do projeto. Ele inicializará o servidor e abrirá o painel automaticamente no seu navegador.

---

### Opção 3: Execução via Docker / Docker Compose

O repositório já inclui `Dockerfile` (baseado em Alpine Linux com usuário não-root) e `docker-compose.yml`:

```bash
# Subir com docker-compose
docker compose up -d

# Visualizar logs
docker compose logs -f
```

---

## ⚙️ Configuração Customizada (`config.json`)

Você pode adicionar endpoints locais (como a instância do e-SUS PEC do seu município, bancos PostgreSQL ou portais estaduais) sem alterar o código-fonte.

Basta editar o arquivo `config.json`:

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

---

## 🔌 API REST Integrada

O servidor expõe endpoints JSON rápidos que podem ser integrados a Grafana, Zabbix, Prometheus ou outros sistemas de monitoramento:

- `GET /api/status`: Retorna o snapshot completo de todos os serviços, latências, diagnósticos de causa-raiz e metadados.
  - Parâmetro opcional: `?force=1` (força uma nova varredura concorrente em tempo real).
- `GET /api/probe/{service_id}`: Sonda sob demanda um único serviço específico e retorna métricas atualizadas.

---

## 📄 Licença

Este projeto é disponibilizado sob a licença **MIT**. Consulte o arquivo [LICENSE](LICENSE) para mais detalhes.

---

*Nota: O Status SUS é uma ferramenta independente desenvolvida para apoiar a observabilidade da infraestrutura digital pública de saúde, sem vínculo institucional direto com o Ministério da Saúde ou DATASUS.*
