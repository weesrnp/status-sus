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
import copy
import threading
import datetime
import json
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import deque
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "public"
CONFIG_FILE = BASE_DIR / "config.json"

def load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[Aviso] Falha ao ler config.json: {exc}")
        return {}

CONFIG = load_config()
SERVER_CONFIG = CONFIG.get("server", {})
PORT = int(os.environ.get("STATUS_SUS_PORT", os.environ.get("PORT", SERVER_CONFIG.get("port", 8765))))
HOST = SERVER_CONFIG.get("host", "0.0.0.0")
POLL_INTERVAL_MS = max(1, int(SERVER_CONFIG.get("pollIntervalMs", 30000)))
SNAPSHOT_TTL_S = 30
FORCE_MIN_INTERVAL_S = 10
STATE_LOCK = threading.RLock()
SCAN_LOCK = threading.Lock()
LAST_SCAN_AT = None
LAST_FORCE_AT = None


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
    for custom in CONFIG.get("customEndpoints", []):
        if custom.get("enabled", True):
            services.append(custom)
    return services

SERVICES_CATALOG = load_services_catalog()

# Armazenamento em memória de histórico (30 ticks)
HISTORY_STORE: dict[str, deque] = {
    s["id"]: deque(maxlen=30) for s in SERVICES_CATALOG
}

LATEST_SNAPSHOT: dict = {
    "updatedAt": None,
    "updatedAtIso": None,
    "ttlSeconds": max(1, (POLL_INTERVAL_MS + 999) // 1000),
    "forceThrottled": False,
    "scanDurationMs": 0,
    "centralizadorMeta": {
        "version": None,
        "buildDateRaw": None,
        "buildDateFormatted": None,
        "serverHeader": None,
        "f5BigIpPool": None
    },
    "services": [],
    "diagnostics": [],
    "summary": {}
}

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
        "serverHeader": None,
        "headers": {},
        "extraMeta": {key: None for key in ("version", "buildDateRaw", "buildDateFormatted", "serverHeader", "f5BigIpPool")} if svc.get("extractVersion") else {},
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
            "User-Agent": "StatusSUS-Monitor/2.0 (+https://github.com/weesrnp/status-sus)",
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
            result["serverHeader"] = headers_dict.get("Server") or headers_dict.get("X-Powered-By") or None
    except urllib.error.HTTPError as http_err:
        elapsed = int((time.perf_counter() - t0) * 1000)
        result["httpCode"] = http_err.code
        result["latencyMs"] = elapsed
        if http_err.headers:
            headers_dict = {k: v for k, v in http_err.headers.items()}
            result["headers"] = headers_dict
            result["serverHeader"] = headers_dict.get("Server") or headers_dict.get("X-Powered-By") or None
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
    if svc.get("extractVersion"):
        text = body_bytes.decode("utf-8", errors="ignore")
        ver_match = re.search(r"Vers(?:&atilde;|ã)o:\s*</font>\s*<font[^>]*>([^<]+)</font>", text, re.I)
        date_match = re.search(r'id="build-date"[^>]*>([^<]+)</font>', text, re.I)
        version_str = ver_match.group(1).strip() if ver_match else None
        build_raw = date_match.group(1).strip() if date_match else None
        build_fmt = build_raw
        if build_raw and "_" in build_raw:
            parts = build_raw.split("_")
            dparts = parts[0].split("-")
            if len(dparts) == 3:
                build_fmt = f"{dparts[2]}/{dparts[1]}/{dparts[0]} às {parts[1].replace('-', ':')}"
        set_cookie = result["headers"].get("Set-Cookie", "")
        pool_match = re.search(r"(?:^|[;,]\s*)(BIGipServer[^=;\s]+)=", set_cookie)
        pool_name = pool_match.group(1) if pool_match else None
        meta = {
            "version": version_str,
            "buildDateRaw": build_raw,
            "buildDateFormatted": build_fmt,
            "serverHeader": result["serverHeader"],
            "f5BigIpPool": pool_name
        }
        result["extraMeta"] = meta
        if version_str and result["status"] == "operational":
            result["statusLabel"] = f"ONLINE (v{version_str})"

    # Validação do Contrato WSDL do Barramento SOA-CADSUS
    if svc.get("validateWsdl") and body_bytes:
        text = body_bytes.decode("utf-8", errors="ignore")
        try:
            root = ET.fromstring(text)
            has_wsdl = root.tag == "{http://schemas.xmlsoap.org/wsdl/}definitions" and any(
                element.tag == "{http://schemas.xmlsoap.org/wsdl/}service" and
                "cadsusservice" in element.get("name", "").lower() for element in root.iter())
            operations = sorted({element.get("name") for element in root.iter()
                                 if element.tag == "{http://schemas.xmlsoap.org/wsdl/}operation" and element.get("name")})
        except ET.ParseError:
            # The response is capped at 12 KB; validate its root even if truncated.
            root_start = re.match(r"\s*(?:<\?xml[^>]*>\s*)?(<(?:[\w.-]+:)?definitions\b[^>]*>)", text)
            has_wsdl = False
            if root_start and len(body_bytes) >= 12288:
                try:
                    opening = root_start.group(1).rstrip(">/") + "/>"
                    has_wsdl = ET.fromstring(opening).tag == "{http://schemas.xmlsoap.org/wsdl/}definitions"
                except ET.ParseError:
                    pass
            operations = []
        result["extraMeta"] = {
            "wsdlValid": has_wsdl,
            "serviceContract": "CadsusService" if has_wsdl else None,
            "operations": ", ".join(operations) if has_wsdl and operations else None
        }
        if has_wsdl and result["status"] == "operational":
            result["statusLabel"] = "WSDL VÁLIDO"

    _record_history(svc["id"], result)
    return result

def _record_history(service_id: str, result: dict):
    with STATE_LOCK:
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
        result["uptimePct"] = round((up_count / total) * 100, 1) if total else None
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
    ver = cent.get("extraMeta", {}).get("version")
    bdate = cent.get("extraMeta", {}).get("buildDateFormatted")

    if cent_ok:
        diags.append({
            "id": "diag-centralizador",
            "title": "Recepção de Lotes da Atenção Primária" + (f" (Centralizador v{ver})" if ver else ""),
            "severity": "operational",
            "badge": f"BUILD {bdate.split(' ')[0]}" if bdate else "RESPONDENDO",
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
    with SCAN_LOCK:
        return _run_full_scan()


def _run_full_scan() -> dict:
    """Executa varredura concorrente de todos os serviços cadastrados."""
    global LAST_SCAN_AT
    t_start = time.perf_counter()
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        future_map = {pool.submit(probe_single_service, svc): svc["id"] for svc in SERVICES_CATALOG}
        for fut in concurrent.futures.as_completed(future_map):
            try:
                results.append(fut.result())
            except Exception as exc:
                results.append({"id": future_map[fut], "status": "outage", "error": str(exc),
                                "history": [], "uptimePct": None, "latencyMs": 0})

    order_index = {svc["id"]: idx for idx, svc in enumerate(SERVICES_CATALOG)}
    results.sort(key=lambda r: order_index.get(r.get("id", ""), 999))

    services_map = {r["id"]: r for r in results}
    diagnostics = build_intelligent_diagnostics(services_map)

    total = len(results)
    operational_cnt = sum(1 for r in results if r["status"] in ("operational", "mtls"))
    degraded_cnt = sum(1 for r in results if r["status"] == "degraded")
    outage_cnt = sum(1 for r in results if r["status"] == "outage")

    valid_latencies = [r["latencyMs"] for r in results if r.get("latencyMs", 0) > 0 and r["status"] != "outage"]
    avg_latency = int(sum(valid_latencies) / len(valid_latencies)) if valid_latencies else 0
    scan_duration = int((time.perf_counter() - t_start) * 1000)
    now_str = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    snapshot = {
        "updatedAt": now_str,
        "updatedAtIso": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "ttlSeconds": max(1, (POLL_INTERVAL_MS + 999) // 1000),
        "forceThrottled": False,
        "centralizadorMeta": {
            key: services_map.get("esus-centralizador-nacional", {}).get("extraMeta", {}).get(key)
            for key in ("version", "buildDateRaw", "buildDateFormatted", "serverHeader", "f5BigIpPool")
        },
        "scanDurationMs": scan_duration,
        "services": results,
        "diagnostics": diagnostics,
        "summary": {
            "total": total,
            "operational": operational_cnt,
            "degraded": degraded_cnt,
            "outage": outage_cnt,
            "healthScorePct": round(((operational_cnt + degraded_cnt * 0.5) / total) * 100, 1) if total else None,
            "avgLatencyMs": avg_latency
        }
    }
    with STATE_LOCK:
        LATEST_SNAPSHOT.update(snapshot)
        LAST_SCAN_AT = time.monotonic()
        return copy.deepcopy(LATEST_SNAPSHOT)


def get_status(force: bool = False, serverless: bool = False) -> tuple[dict, int]:
    global LAST_FORCE_AT
    with STATE_LOCK:
        now = time.monotonic()
        if force:
            if LAST_FORCE_AT is not None and now - LAST_FORCE_AT < FORCE_MIN_INTERVAL_S:
                snapshot = copy.deepcopy(LATEST_SNAPSHOT)
                snapshot["forceThrottled"] = True
                snapshot["ttlSeconds"] = SNAPSHOT_TTL_S if serverless else max(1, (POLL_INTERVAL_MS + 999) // 1000)
                return snapshot, 200
            LAST_FORCE_AT = now
        stale = LAST_SCAN_AT is None or now - LAST_SCAN_AT > SNAPSHOT_TTL_S
    if force:
        run_full_scan()
    elif serverless and stale:
        # Recheck after the lock: concurrent requests share the completed scan.
        with SCAN_LOCK:
            with STATE_LOCK:
                stale = LAST_SCAN_AT is None or time.monotonic() - LAST_SCAN_AT > SNAPSHOT_TTL_S
            if stale:
                _run_full_scan()
    with STATE_LOCK:
        snapshot = copy.deepcopy(LATEST_SNAPSHOT)
        snapshot["forceThrottled"] = False
        snapshot["ttlSeconds"] = SNAPSHOT_TTL_S if serverless else max(1, (POLL_INTERVAL_MS + 999) // 1000)
        return snapshot, 200


def get_probe(service_id: str) -> tuple[dict, int]:
    target = next((s for s in SERVICES_CATALOG if s["id"] == service_id), None)
    if not target:
        return {"error": f"Serviço '{service_id}' não encontrado."}, 404
    result = probe_single_service(target)
    with STATE_LOCK:
        for idx, item in enumerate(LATEST_SNAPSHOT["services"]):
            if item["id"] == service_id:
                LATEST_SNAPSHOT["services"][idx] = result
                break
    return result, 200


def scan_loop(stop_event: threading.Event):
    while not stop_event.is_set():
        started = time.monotonic()
        try:
            run_full_scan()
        except Exception as exc:
            print(f"[Aviso] Falha na varredura: {exc}")
        stop_event.wait(max(0, POLL_INTERVAL_MS / 1000 - (time.monotonic() - started)))


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
            return self._send_json(*get_status(force=force))

        if path.startswith("/api/probe/"):
            svc_id = path.split("/api/probe/", 1)[1].strip("/")
            return self._send_json(*get_probe(svc_id))

        if path == "/" or path == "/index.html":
            self.path = "/index.html"

        return super().do_GET()

def main():
    server = ThreadingHTTPServer((HOST, PORT), StatusSUSHandler)
    stop_event = threading.Event()
    scanner = threading.Thread(target=scan_loop, args=(stop_event,), daemon=True)
    scanner.start()
    print(f"[Status SUS] Servidor HTTP operando em http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        server.server_close()
        scanner.join(timeout=6)

if __name__ == "__main__":
    main()
