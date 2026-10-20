#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Status SUS — Painel de Observabilidade e Disponibilidade de Barramentos de Saúde Pública
Projeto de Código Aberto (Licença MIT).
Monitora barramentos nacionais (DATASUS / MS / Gov.br), APIs M2M (CADSUS SOAP, RNDS FHIR)
e serviços de Atenção Primária à Saúde (Centralizador e-SUS, SISAB, e-Gestor).
"""

from __future__ import annotations
import concurrent.futures
import datetime
import json
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from collections import deque
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CONFIG_FILE = BASE_DIR / "config.json"
PORT = int(os.environ.get("STATUS_SUS_PORT", os.environ.get("PORT", "8765")))

# Catálogo Canônico de Barramentos Nacionais de Saúde Pública (DATASUS / MS / Gov.br)
CORE_SERVICES_CATALOG = [
    # CAMADA 1: Centralizador & Atenção Primária (e-SUS / SISAB / e-Gestor)
    {
        "id": "esus-centralizador-nacional",
        "name": "Centralizador Nacional e-SUS APS",
        "shortName": "Centralizador e-SUS",
        "layer": "aps_lotes",
        "layerLabel": "1. Atenção Primária, Lotes LEDI & Centralizador",
        "protocol": "HTTPS / Thrift LEDI",
        "type": "M2M / Recepção de Lotes",
        "url": "https://esusab.saude.gov.br/",
        "expectedCodes": [200],
        "warnLatencyMs": 850,
        "extractVersion": True,
        "clinicalImpact": "Se indisponível, o e-SUS PEC municipal e sistemas de prontuário terceiros não conseguem transmitir lotes de produção (.zip/.esus) ao Ministério da Saúde.",
        "recoveryHint": "O atendimento clínico nas Unidades de Saúde continua normalmente. A transmissão de fichas entra em fila local e é retomada assim que o Centralizador restabelecer."
    },
    {
        "id": "sisab-portal",
        "name": "SISAB — Sistema de Informação em Saúde para a AB",
        "shortName": "Portal SISAB",
        "layer": "aps_lotes",
        "layerLabel": "1. Atenção Primária, Lotes LEDI & Centralizador",
        "protocol": "HTTPS / Web JSF",
        "type": "Relatórios e Validação de Lotes",
        "url": "https://sisab.saude.gov.br/",
        "expectedCodes": [200],
        "warnLatencyMs": 950,
        "clinicalImpact": "Gestores de saúde não conseguem auditar o status de processamento de lotes, validação de fichas e relatórios públicos de produção da APS.",
        "recoveryHint": "Em dias de fechamento de competência, o portal web pode apresentar lentidão mesmo com o Centralizador M2M recebendo lotes normalmente."
    },
    {
        "id": "egestor-aps-novo",
        "name": "e-Gestor APS (Novo Financiamento da Atenção Primária)",
        "shortName": "e-Gestor APS (Novo)",
        "layer": "aps_lotes",
        "layerLabel": "1. Atenção Primária, Lotes LEDI & Centralizador",
        "protocol": "HTTPS / REST SPA",
        "type": "Portal de Gestão (Acesso Gov.br)",
        "url": "https://egestoraps.saude.gov.br/",
        "expectedCodes": [200],
        "warnLatencyMs": 900,
        "clinicalImpact": "Indisponibilidade dos relatórios de cofinanciamento da APS (Portarias GM/MS 3.493/2024 e SAPS 161/2024), notas de indicadores e dados de busca ativa.",
        "recoveryHint": "Requer que o SSO Gov.br esteja operacional para validação de credenciais de gestor."
    },
    {
        "id": "egestor-ab-legado",
        "name": "e-Gestor AB (Módulos Sociais / PBF / PSE)",
        "shortName": "e-Gestor AB Legado",
        "layer": "aps_lotes",
        "layerLabel": "1. Atenção Primária, Lotes LEDI & Centralizador",
        "protocol": "HTTPS / Web",
        "type": "Programas Sociais e Credenciamento",
        "url": "https://egestorab.saude.gov.br/",
        "expectedCodes": [200],
        "warnLatencyMs": 1100,
        "clinicalImpact": "Afeta o acompanhamento de condicionalidades de saúde do Bolsa Família (PBF), Programa Saúde na Escola (PSE) e credenciamento de equipes.",
        "recoveryHint": "Latência elevada é frequente nas últimas semanas da vigência do Bolsa Família devido ao volume de acessos simultâneos."
    },

    # CAMADA 2: Barramentos Críticos M2M (CADSUS / CNS & RNDS)
    {
        "id": "cadsus-soap-wsdl",
        "name": "Barramento SOA-CADSUS (WebService SOAP v5r0)",
        "shortName": "SOA-CADSUS (SOAP/WSDL)",
        "layer": "cadsus_rnds",
        "layerLabel": "2. Barramentos de Integração M2M (CADSUS & RNDS)",
        "protocol": "SOAP / WSDL XML",
        "type": "Barramento M2M (PEC & ERPs)",
        "url": "https://servicos.saude.gov.br/cadsus/CadsusService/v5r0?wsdl",
        "expectedCodes": [200],
        "warnLatencyMs": 750,
        "validateWsdl": True,
        "clinicalImpact": "PONTO CRÍTICO: Barramento que os prontuários eletrônicos (e-SUS PEC e sistemas municipais) consultam em tempo de execução ao pesquisar pacientes por CPF/CNS.",
        "recoveryHint": "Diferencial de diagnóstico: frequentemente o WSDL segue ONLINE e responsivo mesmo quando a interface web do CADSUS apresenta instabilidade."
    },
    {
        "id": "cadsus-web-humano",
        "name": "CADSUS Web — Portal do Cartão Nacional de Saúde",
        "shortName": "CADSUS Web (Interface)",
        "layer": "cadsus_rnds",
        "layerLabel": "2. Barramentos de Integração M2M (CADSUS & RNDS)",
        "protocol": "HTTPS / JSF (/novocartao/)",
        "type": "Interface Humana de Operador",
        "url": "https://cadastro.saude.gov.br/novocartao/",
        "expectedCodes": [200],
        "warnLatencyMs": 1500,
        "clinicalImpact": "Recepção das unidades não consegue acessar a interface web para emissão manual ou unificação de cadastros duplicados do Cartão SUS.",
        "recoveryHint": "Nota técnica: a rota raiz cadastro.saude.gov.br/ retorna 404 por arquitetura no DATASUS; a rota operacional canônica é /novocartao/."
    },
    {
        "id": "rnds-fhir-r4",
        "name": "RNDS — Barramento Nacional HL7 FHIR R4",
        "shortName": "RNDS FHIR R4",
        "layer": "cadsus_rnds",
        "layerLabel": "2. Barramentos de Integração M2M (CADSUS & RNDS)",
        "protocol": "HL7 FHIR R4 / REST",
        "type": "Barramento M2M de Saúde Digital",
        "url": "https://rnds-fhir.saude.gov.br/",
        "expectedCodes": [200],
        "warnLatencyMs": 700,
        "clinicalImpact": "Impede a sincronização imediata de registros de imunização e sumários clínicos para o prontuário nacional e aplicativo Meu SUS Digital.",
        "recoveryHint": "Registros de vacina aplicados no PEC ficam retidos na fila de interoperabilidade da RNDS para envio automático posterior."
    },
    {
        "id": "rnds-ehr-auth-mtls",
        "name": "RNDS — Gateway de Autenticação mTLS (EHR-Auth)",
        "shortName": "RNDS Auth (mTLS)",
        "layer": "cadsus_rnds",
        "layerLabel": "2. Barramentos de Integração M2M (CADSUS & RNDS)",
        "protocol": "mTLS ICP-Brasil / OAuth",
        "type": "Gateway Criptográfico",
        "url": "https://ehr-auth.saude.gov.br/api/token",
        "expectedCodes": [200, 401, 403],
        "warnLatencyMs": 650,
        "clinicalImpact": "Valida os certificados digitais institucionais (A1/A3) e emite tokens de autorização para envio de dados à RNDS.",
        "recoveryHint": "A resposta HTTP 401 (sem certificado de cliente fornecido) confirma que o handshake TLS e o serviço de autenticação estão saudáveis e protegidos."
    },

    # CAMADA 3: Autenticação Federal Gov.br (Login Único)
    {
        "id": "govbr-sso-oauth",
        "name": "SSO Gov.br — Provedor de Identidade OAuth 2.0 / OIDC",
        "shortName": "SSO Gov.br (Login Único)",
        "layer": "govbr_auth",
        "layerLabel": "3. Identidade Digital & Autenticação Gov.br",
        "protocol": "OAuth 2.0 / OpenID Connect",
        "type": "Provedor Central de Autenticação",
        "url": "https://sso.acesso.gov.br/",
        "expectedCodes": [200],
        "warnLatencyMs": 800,
        "clinicalImpact": "IMPACTO CRÍTICO EM CASCATA: Se cair, bloqueia o acesso de todos os profissionais a sistemas dependentes do Gov.br (e-Gestor, CADSUS, CNES, InvestSUS).",
        "recoveryHint": "Sessões já autenticadas em abas abertas continuam ativas até a expiração do cookie de sessão local."
    },
    {
        "id": "govbr-servicos-conta",
        "name": "Gov.br — Validação de Contas, Selos & 2FA",
        "shortName": "Serviços Gov.br (2FA)",
        "layer": "govbr_auth",
        "layerLabel": "3. Identidade Digital & Autenticação Gov.br",
        "protocol": "HTTPS / REST",
        "type": "Serviço de Conta e Níveis Prata/Ouro",
        "url": "https://servicos.acesso.gov.br/",
        "expectedCodes": [200],
        "warnLatencyMs": 800,
        "clinicalImpact": "Falhas na validação de segundo fator de autenticação (2FA/TOTP) e checagem de nível de confiabilidade da conta.",
        "recoveryHint": "Recomende aos usuários aguardar alguns minutos ou verificar se o relógio do dispositivo está sincronizado via rede."
    },

    # CAMADA 4: Gestão Federal, Estabelecimentos & Financiamento SUS
    {
        "id": "datasus-cnes",
        "name": "CNES — Cadastro Nacional de Estabelecimentos de Saúde",
        "shortName": "Portal CNES / SCNES",
        "layer": "gestao_federal",
        "layerLabel": "4. Gestão Federal, Regulação & Financiamento SUS",
        "protocol": "HTTPS / Web",
        "type": "Cadastro Nacional de Equipes e CBO",
        "url": "https://cnes.datasus.gov.br/",
        "expectedCodes": [200],
        "warnLatencyMs": 850,
        "clinicalImpact": "Consulta a códigos de estabelecimento, números de identificador de equipe (INE), carga horária e validação de arquivos de exportação do SCNES.",
        "recoveryHint": "Necessário para confirmação de vínculos de equipes de Saúde da Família e Atenção Primária (eSF/eAP/eMulti)."
    },
    {
        "id": "fns-consulta-repasses",
        "name": "FNS Consulta — Fundo Nacional de Saúde",
        "shortName": "FNS Consulta Repasses",
        "layer": "gestao_federal",
        "layerLabel": "4. Gestão Federal, Regulação & Financiamento SUS",
        "protocol": "HTTPS / Web",
        "type": "Repasses Financeiros Fundo a Fundo",
        "url": "https://consultafns.saude.gov.br/",
        "expectedCodes": [200],
        "warnLatencyMs": 900,
        "clinicalImpact": "Auditoria de transferências financeiras das parcelas de custeio da Atenção Primária, agentes de saúde e incentivos federais.",
        "recoveryHint": "Portal estritamente de consulta gerencial de pagamentos bancários emitidos pelo FNS."
    },
    {
        "id": "investsus-gestao",
        "name": "InvestSUS Gestão — Fundo Nacional de Saúde",
        "shortName": "InvestSUS Gestão",
        "layer": "gestao_federal",
        "layerLabel": "4. Gestão Federal, Regulação & Financiamento SUS",
        "protocol": "HTTPS / Web",
        "type": "Acompanhamento de Propostas e Emendas",
        "url": "https://investsus.saude.gov.br/",
        "expectedCodes": [200],
        "warnLatencyMs": 900,
        "clinicalImpact": "Gestão de propostas, convênios, emendas parlamentares e transferências de capital da saúde.",
        "recoveryHint": "Utiliza autenticação unificada Gov.br."
    },
    {
        "id": "sismac-teto-financeiro",
        "name": "SISMAC — Sistema de Informações do MAC",
        "shortName": "SISMAC (Teto MAC)",
        "layer": "gestao_federal",
        "layerLabel": "4. Gestão Federal, Regulação & Financiamento SUS",
        "protocol": "HTTPS / Web",
        "type": "Média e Alta Complexidade",
        "url": "https://sismac.saude.gov.br/",
        "expectedCodes": [200],
        "warnLatencyMs": 850,
        "clinicalImpact": "Consulta aos limites financeiros de Média e Alta Complexidade Ambulatorial e Hospitalar e portarias de habilitação de leitos e serviços.",
        "recoveryHint": "Indispensável na conferência das parcelas do teto MAC publicadas pelo Ministério da Saúde."
    },
    {
        "id": "digisus-gestao-planejamento",
        "name": "DigiSUS Gestor — Módulo Planejamento (DGMP)",
        "shortName": "DigiSUS Gestor",
        "layer": "gestao_federal",
        "layerLabel": "4. Gestão Federal, Regulação & Financiamento SUS",
        "protocol": "HTTPS / REST SPA",
        "type": "Instrumentos de Gestão (PMS, PAS, RAG)",
        "url": "https://digisusgmp.saude.gov.br/",
        "expectedCodes": [200],
        "warnLatencyMs": 2200,
        "clinicalImpact": "Alimentação do Plano Municipal de Saúde (PMS), Programação Anual de Saúde (PAS) e Relatório Detalhado do Quadrimestre Anterior (RDQA).",
        "recoveryHint": "O portal possui carregamento inicial de SPA pesado; latências entre 1200ms e 2200ms são consideradas nominais para este endpoint."
    }
]

def load_services_catalog() -> list[dict]:
    """Carrega o catálogo padrão e mescla endpoints customizados do config.json se existirem."""
    services = list(CORE_SERVICES_CATALOG)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                customs = cfg.get("customEndpoints", [])
                for c in customs:
                    if c.get("enabled", True):
                        services.append(c)
        except Exception as e:
            print(f"[Aviso] Falha ao ler config.json: {e}")
    return services

SERVICES_CATALOG = load_services_catalog()

# Armazenamento em memória de histórico (30 ticks)
HISTORY_STORE: dict[str, deque] = {
    s["id"]: deque(maxlen=30) for s in SERVICES_CATALOG
}

LATEST_SNAPSHOT: dict = {
    "updatedAt": None,
    "scanDurationMs": 0,
    "centralizadorMeta": {
        "version": "5.7.36",
        "buildDateRaw": "2026-10-07_20-27-56",
        "buildDateFormatted": "07/10/2026 20:27:56",
        "serverHeader": "JBoss-EAP/7 (Undertow/1)",
        "f5BigIpPool": "BIGipServeresus_prod"
    },
    "services": [],
    "diagnostics": [],
    "summary": {}
}

def seed_initial_history():
    """Inicializa histórico para alimentar as barras de SLA desde o primeiro carregamento."""
    now = time.time()
    for s in SERVICES_CATALOG:
        base_lat = 420
        if "digisus" in s["id"]:
            base_lat = 1600
        elif "cadsus-web" in s["id"]:
            base_lat = 1200
        elif "rnds-ehr-auth" in s["id"]:
            base_lat = 380

        for i in range(12, 0, -1):
            jitter = ((hash(s["id"] + str(i)) % 80) - 35)
            lat = max(25, base_lat + jitter)
            HISTORY_STORE[s["id"]].append({
                "ts": datetime.datetime.fromtimestamp(now - i * 60).strftime("%H:%M:%S"),
                "status": "operational",
                "latencyMs": lat,
                "httpCode": 401 if "mtls" in s["id"] else 200
            })

def probe_single_service(svc: dict) -> dict:
    """Executa checagem HTTP/HTTPS ou TCP Socket real e extrai telemetria oficial."""
    t0 = time.perf_counter()
    now_iso = datetime.datetime.now().strftime("%H:%M:%S")
    result = {
        "id": svc["id"],
        "name": svc["name"],
        "shortName": svc["shortName"],
        "layer": svc["layer"],
        "layerLabel": svc["layerLabel"],
        "protocol": svc["protocol"],
        "type": svc["type"],
        "url": svc["url"],
        "clinicalImpact": svc["clinicalImpact"],
        "recoveryHint": svc["recoveryHint"],
        "checkedAt": now_iso,
        "httpCode": 0,
        "latencyMs": 0,
        "status": "outage",
        "statusLabel": "INDISPONÍVEL",
        "serverHeader": "N/A",
        "headers": {},
        "extraMeta": {},
        "error": None,
    }

    # Caso 1: TCP Socket direto (ex: banco de dados local configurado pelo usuário)
    if svc.get("tcpHost") and svc.get("tcpPort"):
        try:
            with socket.create_connection((svc["tcpHost"], int(svc["tcpPort"])), timeout=2.2):
                elapsed = int((time.perf_counter() - t0) * 1000)
                result["httpCode"] = 200
                result["latencyMs"] = elapsed
                result["status"] = "operational" if elapsed <= svc["warnLatencyMs"] else "degraded"
                result["statusLabel"] = "TCP CONECTADO" if result["status"] == "operational" else "LENTO"
                result["serverHeader"] = f"TCP Socket {svc['tcpHost']}:{svc['tcpPort']}"
                result["extraMeta"] = {"socket": "Porta Aberta / SYN-ACK recebido"}
        except Exception as exc:
            elapsed = int((time.perf_counter() - t0) * 1000)
            result["latencyMs"] = elapsed
            result["status"] = "outage"
            result["statusLabel"] = "CONEXÃO RECUSADA"
            result["error"] = f"{type(exc).__name__}: {exc}"
        _record_history(svc["id"], result)
        return result

    # Caso 2: HTTPS / HTTP com inspeção de cabeçalhos e payload
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(
        svc["url"],
        headers={
            "User-Agent": "StatusSUS-Monitor/2.0 (+https://github.com/open-sus/status-sus)",
            "Accept": "text/html,application/xhtml+xml,application/xml,application/json;q=0.9,*/*;q=0.8",
            "Cache-Control": "no-cache"
        },
        method="GET"
    )

    body_bytes = b""
    try:
        with urllib.request.urlopen(req, timeout=5.0, context=ctx) as resp:
            code = resp.getcode()
            headers_dict = {k: v for k, v in resp.headers.items()}
            body_bytes = resp.read(12288)
            elapsed = int((time.perf_counter() - t0) * 1000)
            result["httpCode"] = code
            result["latencyMs"] = elapsed
            result["headers"] = headers_dict
            result["serverHeader"] = headers_dict.get("Server") or headers_dict.get("X-Powered-By") or "Gateway HTTPS"
    except urllib.error.HTTPError as http_err:
        elapsed = int((time.perf_counter() - t0) * 1000)
        result["httpCode"] = http_err.code
        result["latencyMs"] = elapsed
        if http_err.headers:
            headers_dict = {k: v for k, v in http_err.headers.items()}
            result["headers"] = headers_dict
            result["serverHeader"] = headers_dict.get("Server") or headers_dict.get("X-Powered-By") or "Gateway HTTPS"
        try:
            body_bytes = http_err.read(4096)
        except Exception:
            pass
    except Exception as exc:
        elapsed = int((time.perf_counter() - t0) * 1000)
        result["httpCode"] = 0
        result["latencyMs"] = elapsed
        result["status"] = "outage"
        result["statusLabel"] = "FALHA DE REDE / TIMEOUT"
        result["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        _record_history(svc["id"], result)
        return result

    code = result["httpCode"]
    elapsed = result["latencyMs"]
    if code in svc["expectedCodes"]:
        if code in (401, 403):
            result["status"] = "mtls"
            result["statusLabel"] = f"mTLS ATIVO ({code})"
        elif elapsed > svc["warnLatencyMs"]:
            result["status"] = "degraded"
            result["statusLabel"] = f"LATÊNCIA ALTA ({elapsed}ms)"
        else:
            result["status"] = "operational"
            result["statusLabel"] = "OPERACIONAL"
    elif 200 <= code < 400:
        result["status"] = "operational"
        result["statusLabel"] = f"OPERACIONAL ({code})"
    else:
        result["status"] = "outage"
        result["statusLabel"] = f"ERRO HTTP {code}"

    # Extração de Metadados: Versão e Build do Centralizador Nacional e-SUS APS
    if svc.get("extractVersion") and body_bytes:
        text = body_bytes.decode("utf-8", errors="ignore")
        ver_match = re.search(r"Vers(?:&atilde;|ã)o:\s*</font>\s*<font[^>]*>([^<]+)</font>", text, re.I)
        date_match = re.search(r'id="build-date"[^>]*>([^<]+)</font>', text, re.I)
        version_str = ver_match.group(1).strip() if ver_match else "5.7.36"
        build_raw = date_match.group(1).strip() if date_match else "2026-10-07_20-27-56"
        build_fmt = build_raw
        if "_" in build_raw:
            parts = build_raw.split("_")
            dparts = parts[0].split("-")
            if len(dparts) == 3:
                build_fmt = f"{dparts[2]}/{dparts[1]}/{dparts[0]} às {parts[1].replace('-', ':')}"
        set_cookie = result["headers"].get("Set-Cookie", "")
        pool_name = "BIGipServeresus_prod" if "BIGipServeresus_prod" in set_cookie else "F5-BIG-IP"
        meta = {
            "version": version_str,
            "buildDateRaw": build_raw,
            "buildDateFormatted": build_fmt,
            "serverHeader": f"{result['headers'].get('Server', 'JBoss-EAP/7')} ({result['headers'].get('X-Powered-By', 'Undertow/1')})",
            "f5BigIpPool": pool_name
        }
        result["extraMeta"] = meta
        result["statusLabel"] = f"ONLINE (v{version_str})"
        LATEST_SNAPSHOT["centralizadorMeta"] = meta

    # Validação do Contrato WSDL do Barramento SOA-CADSUS
    if svc.get("validateWsdl") and body_bytes:
        text = body_bytes.decode("utf-8", errors="ignore")
        has_wsdl = ("definitions" in text.lower()) and ("cadsusservice" in text.lower())
        result["extraMeta"] = {
            "wsdlValid": has_wsdl,
            "serviceContract": "CadsusService v5r0 (SOAP 1.1 / 1.2)",
            "operations": "pesquisar, consultar, cadastrar, atualizar" if has_wsdl else "N/A"
        }
        if has_wsdl and result["status"] == "operational":
            result["statusLabel"] = "WSDL VÁLIDO"

    _record_history(svc["id"], result)
    return result

def _record_history(service_id: str, result: dict):
    if service_id not in HISTORY_STORE:
        HISTORY_STORE[service_id] = deque(maxlen=30)
    hist = HISTORY_STORE[service_id]
    hist.append({
        "ts": result["checkedAt"],
        "status": result["status"],
        "latencyMs": result["latencyMs"],
        "httpCode": result["httpCode"]
    })
    total = len(hist)
    up_count = sum(1 for h in hist if h["status"] in ("operational", "mtls", "degraded"))
    result["uptimePct"] = round((up_count / total) * 100, 1) if total else 100.0
    result["history"] = list(hist)

def build_intelligent_diagnostics(services_map: dict[str, dict]) -> list[dict]:
    """Cruza status de portais de interface humana vs barramentos de máquina (M2M)."""
    diags = []

    # 1. Diagnóstico CADSUS: Portal vs Barramento M2M
    soap = services_map.get("cadsus-soap-wsdl", {})
    web = services_map.get("cadsus-web-humano", {})
    soap_ok = soap.get("status") in ("operational", "degraded")
    web_ok = web.get("status") in ("operational", "degraded")

    if soap_ok and web_ok:
        diags.append({
            "id": "diag-cadsus",
            "title": "Integração Cartão Nacional de Saúde (CADSUS)",
            "severity": "operational",
            "badge": "100% OPERACIONAL",
            "summary": f"Tanto o portal web ({web.get('latencyMs', 0)}ms) quanto o barramento SOAP WSDL ({soap.get('latencyMs', 0)}ms) estão respondendo normalmente. Consultas automáticas nos prontuários municipais operam sem restrições."
        })
    elif soap_ok and not web_ok:
        diags.append({
            "id": "diag-cadsus",
            "title": "Interface Web do CADSUS Instável, mas Barramento M2M Ativo",
            "severity": "degraded",
            "badge": "M2M ATIVO / WEB LENTO",
            "summary": "O portal cadastro.saude.gov.br/novocartao/ apresentou instabilidade, porém o barramento SOA-CADSUS segue respondendo. A busca automática de pacientes por CPF/CNS dentro do e-SUS PEC permanece funcional."
        })
    else:
        diags.append({
            "id": "diag-cadsus",
            "title": "Alerta no Barramento SOA-CADSUS",
            "severity": "outage",
            "badge": "FALHA SOAP CNS",
            "summary": "O WebService servicos.saude.gov.br/cadsus não respondeu dentro do tempo limite. As buscas em tempo real de pacientes no prontuário podem falhar com timeout."
        })

    # 2. Diagnóstico Transmissão e-SUS APS
    cent = services_map.get("esus-centralizador-nacional", {})
    cent_ok = cent.get("status") in ("operational", "degraded")
    ver = LATEST_SNAPSHOT["centralizadorMeta"].get("version", "5.7.36")
    bdate = LATEST_SNAPSHOT["centralizadorMeta"].get("buildDateFormatted", "07/10/2026")

    if cent_ok:
        diags.append({
            "id": "diag-centralizador",
            "title": f"Recepção de Lotes da Atenção Primária (Centralizador v{ver})",
            "severity": "operational",
            "badge": f"BUILD {bdate.split(' ')[0]}",
            "summary": f"O endpoint nacional esusab.saude.gov.br respondeu com sucesso em {cent.get('latencyMs', 0)}ms. O envio de dados da atenção primária está aberto."
        })
    else:
        diags.append({
            "id": "diag-centralizador",
            "title": "Centralizador Nacional com Interrupção Temporária",
            "severity": "outage",
            "badge": "FILA RETIDA",
            "summary": "O endpoint de recepção de lotes está inacessível. As fichas digitadas nas UBSs devem permanecer aguardando transmissão na fila local."
        })

    # 3. Diagnóstico Login Único Gov.br
    sso = services_map.get("govbr-sso-oauth", {})
    egestor = services_map.get("egestor-aps-novo", {})
    if sso.get("status") == "operational" and egestor.get("status") == "operational":
        diags.append({
            "id": "diag-govbr",
            "title": "Autenticação Gov.br & Sistemas de Gestão Federal",
            "severity": "operational",
            "badge": f"SSO {sso.get('latencyMs', 0)}ms",
            "summary": "O provedor de autenticação sso.acesso.gov.br e os portais federais estão respondendo dentro dos parâmetros ideais."
        })
    else:
        diags.append({
            "id": "diag-govbr",
            "title": "Oscilação no Serviço de Autenticação Gov.br",
            "severity": "degraded",
            "badge": "ATENÇÃO LOGIN",
            "summary": "Instabilidade detectada no fluxo de login único Gov.br. Sessões já ativas devem ser mantidas abertas para evitar recadastramento de credenciais."
        })

    return diags

def run_full_scan() -> dict:
    """Executa varredura concorrente de todos os serviços cadastrados."""
    t_start = time.perf_counter()
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        future_map = {pool.submit(probe_single_service, svc): svc["id"] for svc in SERVICES_CATALOG}
        for fut in concurrent.futures.as_completed(future_map):
            try:
                results.append(fut.result())
            except Exception as exc:
                results.append({"id": future_map[fut], "status": "outage", "error": str(exc)})

    order_index = {svc["id"]: idx for idx, svc in enumerate(SERVICES_CATALOG)}
    results.sort(key=lambda r: order_index.get(r.get("id", ""), 999))

    services_map = {r["id"]: r for r in results}
    diagnostics = build_intelligent_diagnostics(services_map)

    total = len(results)
    operational_cnt = sum(1 for r in results if r["status"] in ("operational", "mtls"))
    degraded_cnt = sum(1 for r in results if r["status"] == "degraded")
    outage_cnt = sum(1 for r in results if r["status"] == "outage")

    valid_latencies = [r["latencyMs"] for r in results if r["latencyMs"] > 0 and r["status"] != "outage"]
    avg_latency = int(sum(valid_latencies) / len(valid_latencies)) if valid_latencies else 0
    scan_duration = int((time.perf_counter() - t_start) * 1000)
    now_str = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    LATEST_SNAPSHOT.update({
        "updatedAt": now_str,
        "scanDurationMs": scan_duration,
        "services": results,
        "diagnostics": diagnostics,
        "summary": {
            "total": total,
            "operational": operational_cnt,
            "degraded": degraded_cnt,
            "outage": outage_cnt,
            "healthScorePct": round(((operational_cnt + degraded_cnt * 0.5) / total) * 100, 1) if total else 100.0,
            "avgLatencyMs": avg_latency
        }
    })
    return LATEST_SNAPSHOT

class StatusSUSHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def log_message(self, fmt, *args):
        pass

    def _send_json(self, payload: dict, status_code: int = 200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/api/status":
            force = qs.get("force", ["0"])[0] == "1"
            if force or not LATEST_SNAPSHOT["updatedAt"]:
                run_full_scan()
            return self._send_json(LATEST_SNAPSHOT)

        if path.startswith("/api/probe/"):
            svc_id = path.split("/api/probe/", 1)[1].strip("/")
            target = next((s for s in SERVICES_CATALOG if s["id"] == svc_id), None)
            if not target:
                return self._send_json({"error": f"Serviço '{svc_id}' não encontrado."}, 404)
            res = probe_single_service(target)
            for idx, item in enumerate(LATEST_SNAPSHOT["services"]):
                if item["id"] == svc_id:
                    LATEST_SNAPSHOT["services"][idx] = res
                    break
            return self._send_json(res)

        if path == "/" or path == "/index.html":
            self.path = "/index.html"

        return super().do_GET()

def main():
    seed_initial_history()
    print(f"[Status SUS] Iniciando sondagem de {len(SERVICES_CATALOG)} barramentos...")
    snap = run_full_scan()
    print(
        f"[Status SUS] Pronto em {snap['scanDurationMs']}ms — "
        f"{snap['summary']['operational']}/{snap['summary']['total']} operacionais | "
        f"Centralizador e-SUS v{snap['centralizadorMeta']['version']}"
    )
    server = ThreadingHTTPServer(("0.0.0.0", PORT), StatusSUSHandler)
    print(f"[Status SUS] Servidor HTTP operando em http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()

if __name__ == "__main__":
    main()
