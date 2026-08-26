import requests
import os
import sys
import re
from datetime import date, timedelta

URL = "https://focvs-ax4b.com"
USUARIO = os.getenv("AX4B_USER")
SENHA = os.getenv("AX4B_PASS")
UID = 115

HOJE = date.today()

# não roda fim de semana
if HOJE.weekday() >= 5:
    print("⛔ Fim de semana")
    sys.exit(0)

# ── helpers ───────────────────────────────────────────────────────────────────

def buscar_feriados_nacionais(ano: int) -> set:
    try:
        r = requests.get(
            f"https://brasilapi.com.br/api/feriados/v1/{ano}",
            timeout=10,
        )
        r.raise_for_status()
        return {date.fromisoformat(f["date"]) for f in r.json()}
    except Exception as e:
        print(f"⚠️ Não foi possível buscar feriados ({e}) — continuando sem filtro de feriados")
        return set()

def dias_uteis_mes_ate_hoje(feriados: set) -> list:
    inicio = HOJE.replace(day=1)
    dias = []
    d = inicio
    while d <= HOJE:
        if d.weekday() < 5 and d not in feriados:
            dias.append(d)
        d += timedelta(days=1)
    return dias

# ── buscar feriados ───────────────────────────────────────────────────────────
print(f"➡️ Buscando feriados nacionais de {HOJE.year}")
feriados = buscar_feriados_nacionais(HOJE.year)
if feriados:
    feriados_mes = sorted(f for f in feriados if f.month == HOJE.month)
    if feriados_mes:
        print(f"📅 Feriados no mês: {[f.strftime('%d/%m') for f in feriados_mes]}")
    else:
        print(f"📅 Nenhum feriado nacional este mês")

if HOJE in feriados:
    print(f"⛔ Hoje ({HOJE.strftime('%d/%m/%Y')}) é feriado nacional")
    sys.exit(0)

session = requests.Session()
session.headers.update({"Content-Type": "application/json"})

_rpc_id = 0
def rpc(model, method, args=None, kwargs=None):
    global _rpc_id
    _rpc_id += 1
    payload = {
        "id": _rpc_id,
        "jsonrpc": "2.0",
        "method": "call",
        "params": {
            "model": model,
            "method": method,
            "args": args or [],
            "kwargs": {
                "context": {
                    "lang": "pt_BR",
                    "tz": "America/Sao_Paulo",
                    "uid": UID,
                    "allowed_company_ids": [1],
                    "params": {"menu_id": 424, "action": 591},
                    "is_timesheet": 1,
                },
                **(kwargs or {}),
            },
        },
    }
    r = session.post(f"{URL}/web/dataset/call_kw/{model}/{method}", json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        print(f"❌ Erro RPC [{model}/{method}]: {data['error']['data']['message']}")
        sys.exit(1)
    return data["result"]

# ── 1. CSRF + LOGIN ───────────────────────────────────────────────────────────
print("➡️ Obtendo CSRF token")
resp = session.get(f"{URL}/web/login", timeout=30)
resp.raise_for_status()

match = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp.text)
if not match:
    print("❌ CSRF token não encontrado")
    sys.exit(1)

csrf_token = match.group(1)
print(f"✅ CSRF obtido: {csrf_token[:20]}...")

print("➡️ Fazendo login")

# remove Content-Type JSON para o form POST
session.headers.pop("Content-Type", None)

resp = session.post(
    f"{URL}/web/login",
    data={
        "csrf_token": csrf_token,
        "login": USUARIO,
        "password": SENHA,
        "redirect": "",
    },
    allow_redirects=True,
    timeout=30,
)

# DEBUG
print(f"🔍 URL final: {resp.url}")
print(f"🔍 Status: {resp.status_code}")
print(f"🔍 USUARIO: '{USUARIO}'")
print(f"🔍 SENHA: '{SENHA[:2]}***'" if SENHA else "🔍 SENHA: None")
print(f"🔍 Cookies: {dict(session.cookies)}")

if "/web/login" in resp.url:
    print("❌ Login falhou — verifique AX4B_USER e AX4B_PASS")
    sys.exit(1)

# restaura Content-Type para as chamadas RPC
session.headers.update({"Content-Type": "application/json"})

print("✅ Login OK")

# ── 2. BUSCAR APONTAMENTOS DO MÊS ATUAL ──────────────────────────────────────
inicio_mes = HOJE.replace(day=1).strftime("%Y-%m-%d")
hoje_iso = HOJE.strftime("%Y-%m-%d")

print(f"➡️ Buscando apontamentos de {inicio_mes} até {hoje_iso}")
result = rpc(
    model="account.analytic.line",
    method="web_search_read",
    kwargs={
        "domain": [
            "&", "&",
            ["project_id", "!=", False],
            ["user_id", "=", UID],
            "&",
            ["date", ">=", inicio_mes],
            ["date", "<=", hoje_iso],
        ],
        "fields": ["id", "date"],
        "order": "date desc",
        "limit": 100,
        "offset": 0,
        "count_limit": 200,
    },
)

datas_existentes = {r["date"] for r in result.get("records", [])}
print(f"📋 Apontamentos no mês: {len(datas_existentes)}")

# ── 3. CALCULAR DIAS FALTANTES ────────────────────────────────────────────────
todos_uteis = dias_uteis_mes_ate_hoje(feriados)
faltantes = [d for d in todos_uteis if d.strftime("%Y-%m-%d") not in datas_existentes]

if not faltantes:
    print("✅ Nenhuma data faltante — tudo em dia!")
    sys.exit(0)

print(f"⚠️ Faltantes: {[d.strftime('%d/%m/%Y') for d in faltantes]}")

# ── 4. BUSCAR TEMPLATE ────────────────────────────────────────────────────────
print("➡️ Buscando template")
template_result = rpc(
    model="account.analytic.line",
    method="web_search_read",
    kwargs={
        "domain": [
            "&",
            ["project_id", "!=", False],
            ["user_id", "=", UID],
        ],
        "fields": [
            "id", "date", "project_id", "task_id", "name",
            "employee_id", "unit_amount", "ax4b_squad_id",
            "ax4b_billable", "ax4b_waived_hours",
            "ax4b_justification_type", "ax4b_justification",
        ],
        "order": "date desc",
        "limit": 1,
        "offset": 0,
        "count_limit": 1,
    },
)

records = template_result.get("records", [])
if not records:
    print("❌ Nenhum apontamento anterior encontrado para usar como template")
    sys.exit(1)

t = records[0]
print(f"📌 Template: id={t['id']} | {t['date']} | {t['project_id'][1]} | {t['task_id'][1]}")

# ── 5. CRIAR APONTAMENTOS FALTANTES ──────────────────────────────────────────
criados = []
for dia in faltantes:
    iso = dia.strftime("%Y-%m-%d")
    texto = dia.strftime("%d/%m/%Y")
    print(f"➡️ Criando {texto}")

    novo_id = rpc(
        model="account.analytic.line",
        method="create",
        args=[{
            "date":                    iso,
            "user_id":                 UID,
            "employee_id":             t["employee_id"][0],
            "project_id":              t["project_id"][0],
            "task_id":                 t["task_id"][0],
            "name":                    t["name"],
            "unit_amount":             t["unit_amount"],
            "ax4b_squad_id":           t["ax4b_squad_id"] if t["ax4b_squad_id"] else False,
            "ax4b_billable":           t["ax4b_billable"],
            "ax4b_waived_hours":       t["ax4b_waived_hours"],
            "ax4b_justification_type": t["ax4b_justification_type"] if t["ax4b_justification_type"] else False,
            "ax4b_justification":      t["ax4b_justification"] if t["ax4b_justification"] else False,
            "ax4b_state":              "draft",
        }],
    )

    print(f"✅ Criado id={novo_id} para {texto}")
    criados.append((novo_id, texto))

print(f"\n🎉 {len(criados)} apontamento(s) criado(s):")
for novo_id, texto in criados:
    print(f"   • id={novo_id} | {texto}")

print("✅ BOT FINALIZADO")
