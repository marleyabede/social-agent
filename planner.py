"""
planner.py — Planejador semanal autônomo do Social Agent · Salão 365°
Roda toda segunda-feira às 09h BRT via scheduler.py
Cria cards no ClickUp com todos os campos preenchidos.
"""

import os
import json
import random
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import anthropic
import requests

# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [planner] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("planner")

# ─── Env vars ────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY  = os.environ["ANTHROPIC_API_KEY"]
CLICKUP_API_TOKEN  = os.environ["CLICKUP_API_TOKEN"]
CLICKUP_LIST_ID    = os.environ["CLICKUP_LIST_ID"]

BRT = ZoneInfo("America/Sao_Paulo")

# ─── ClickUp custom field IDs (preencher após criar os campos no ClickUp) ────
# Obter via: GET https://api.clickup.com/api/v2/list/{list_id}/field
# Substituir os valores abaixo pelos IDs reais após configuração inicial.

CF_PERSONA  = os.environ.get("CF_PERSONA",  "PREENCHER")   # dropdown
CF_FUNIL    = os.environ.get("CF_FUNIL",    "PREENCHER")   # dropdown
CF_FORMATO  = os.environ.get("CF_FORMATO",  "PREENCHER")   # dropdown
CF_REDES    = os.environ.get("CF_REDES",    "PREENCHER")   # labels/multi
CF_HOOK     = os.environ.get("CF_HOOK",     "PREENCHER")   # dropdown
CF_DATA_PUB = os.environ.get("CF_DATA_PUB", "PREENCHER")   # date
CF_HORARIO  = os.environ.get("CF_HORARIO",  "PREENCHER")   # short text
CF_GANCHO   = os.environ.get("CF_GANCHO",   "PREENCHER")   # long text
CF_TEMA_ID  = os.environ.get("CF_TEMA_ID",  "PREENCHER")   # short text (ex: J-T01)

# ─── YOUR_TOPICS ─────────────────────────────────────────────────────────────
# Estrutura: (id, persona, funil, tema, formato, redes, hook_type)
# hook_type: "numero" | "dor" | "promessa" | "erro"

YOUR_TOPICS = [
    # ── JESSICA · TOFU ───────────────────────────────────────────────────────
    ("J-T01", "jessica", "tofu", "Quanto uma manicure perde por mês com agenda bagunçada",         "reels",     ["instagram", "tiktok", "youtube"], "numero"),
    ("J-T02", "jessica", "tofu", "A rotina real de uma manicure autônoma num dia cheio",             "reels",     ["instagram", "tiktok"],             "promessa"),
    ("J-T03", "jessica", "tofu", "3 sinais de que seu atendimento está crescendo",                  "carrossel", ["instagram"],                       "promessa"),
    ("J-T04", "jessica", "tofu", "Por que manicure autônoma trabalha muito e sobra pouco",          "reels",     ["instagram", "tiktok", "youtube"],   "numero"),
    ("J-T05", "jessica", "tofu", "Como parecer mais profissional sem gastar nada",                  "card",      ["instagram", "tiktok"],             "dor"),
    ("J-T06", "jessica", "tofu", "O erro que faz manicure perder cliente fiel sem entender por quê","reels",     ["instagram", "tiktok"],             "erro"),
    ("J-T07", "jessica", "tofu", "5 coisas que toda manicure profissional faz diferente",           "carrossel", ["instagram"],                       "promessa"),
    ("J-T08", "jessica", "tofu", "Quanto vale a hora de uma manicure autônoma",                     "card",      ["instagram", "tiktok"],             "numero"),
    # ── JESSICA · MOFU ───────────────────────────────────────────────────────
    ("J-M01", "jessica", "mofu", "Como acabar de vez com o no-show sem brigar com cliente",         "reels",     ["instagram", "tiktok", "youtube"],   "numero"),
    ("J-M02", "jessica", "mofu", "Como cobrar sinal sem perder cliente — script pronto",            "carrossel", ["instagram"],                       "promessa"),
    ("J-M03", "jessica", "mofu", "Como organizar a agenda da semana em 10 minutos na segunda",      "reels",     ["instagram", "tiktok"],             "dor"),
    ("J-M04", "jessica", "mofu", "Por que misturar dinheiro pessoal com o do atendimento é armadilha","carrossel",["instagram"],                      "dor"),
    ("J-M05", "jessica", "mofu", "Como lidar com cliente que cancela na última hora",               "story",     ["instagram"],                       "erro"),
    ("J-M06", "jessica", "mofu", "Como montar uma lista de espera e nunca ficar sem cliente",       "reels",     ["instagram", "tiktok"],             "promessa"),
    ("J-M07", "jessica", "mofu", "Como fidelizar cliente de manicure — o que realmente funciona",  "card",      ["instagram", "tiktok"],             "numero"),
    # ── JESSICA · BOFU ───────────────────────────────────────────────────────
    ("J-B01", "jessica", "bofu", "Agendamento online para manicure: como funciona na prática",      "reels",     ["instagram", "tiktok", "youtube"],   "promessa"),
    ("J-B02", "jessica", "bofu", "Como ativar agendamento online pelo Instagram sendo autônoma",    "carrossel", ["instagram"],                       "promessa"),
    ("J-B03", "jessica", "bofu", "Checklist: você já está pronta para sair do WhatsApp?",           "story",     ["instagram"],                       "erro"),
    ("J-B04", "jessica", "bofu", "3 apps que manicure autônoma usa para organizar tudo",            "carrossel", ["instagram"],                       "numero"),
    ("J-B05", "jessica", "bofu", "Quanto tempo você perde por semana gerenciando pelo WhatsApp",    "card",      ["instagram", "tiktok"],             "numero"),
    # ── CARLA · TOFU ─────────────────────────────────────────────────────────
    ("C-T01", "carla",   "tofu", "Por que dona de salão trabalha 12h e ainda sente que está devendo","reels",    ["instagram", "tiktok", "youtube"],   "dor"),
    ("C-T02", "carla",   "tofu", "Como organizar a rotina do salão quando você é dona, profissional e caixa","carrossel",["instagram"],               "dor"),
    ("C-T03", "carla",   "tofu", "3 sinais que seu salão está crescendo — mesmo que pareça o contrário","card",  ["instagram", "tiktok"],             "promessa"),
    ("C-T04", "carla",   "tofu", "A conta que toda dona de salão precisa fazer todo mês",           "reels",     ["instagram", "tiktok", "youtube"],   "erro"),
    ("C-T05", "carla",   "tofu", "Como contratar profissional sem quebrar o salão no primeiro mês", "reels",     ["instagram", "tiktok"],             "numero"),
    ("C-T06", "carla",   "tofu", "Por que clientes do salão somem depois de 3 meses",               "carrossel", ["instagram"],                       "dor"),
    ("C-T07", "carla",   "tofu", "Quanto um salão pequeno precisa faturar para ser lucrativo",      "card",      ["instagram", "tiktok"],             "numero"),
    ("C-T08", "carla",   "tofu", "O erro mais caro que donos de salão cometem no primeiro ano",     "reels",     ["instagram", "tiktok", "youtube"],   "erro"),
    # ── CARLA · MOFU ─────────────────────────────────────────────────────────
    ("C-M01", "carla",   "mofu", "Como calcular comissão de cabeleireiro sem errar nem prejudicar o salão","carrossel",["instagram"],                 "numero"),
    ("C-M02", "carla",   "mofu", "Como o no-show destrói o financeiro do salão — com números reais","reels",     ["instagram", "tiktok", "youtube"],   "numero"),
    ("C-M03", "carla",   "mofu", "Como separar as finanças do salão das suas pessoais de vez",      "carrossel", ["instagram"],                       "dor"),
    ("C-M04", "carla",   "mofu", "Como reduzir cancelamentos de última hora no salão",              "story",     ["instagram"],                       "dor"),
    ("C-M05", "carla",   "mofu", "Como criar uma escala de trabalho justa para a equipe do salão",  "reels",     ["instagram", "tiktok"],             "erro"),
    ("C-M06", "carla",   "mofu", "Como precificar serviços do salão sem perder cliente nem lucro",  "card",      ["instagram", "tiktok"],             "erro"),
    ("C-M07", "carla",   "mofu", "Como montar um cardápio de serviços que vende sozinho",           "carrossel", ["instagram"],                       "promessa"),
    # ── CARLA · BOFU ─────────────────────────────────────────────────────────
    ("C-B01", "carla",   "bofu", "O que um app de gestão para salão precisa ter — checklist completo","carrossel",["instagram"],                       "promessa"),
    ("C-B02", "carla",   "bofu", "Como escolher um sistema para salão sem se arrepender em 3 meses","reels",     ["instagram", "tiktok", "youtube"],   "erro"),
    ("C-B03", "carla",   "bofu", "5 recursos que toda dona de salão precisa no sistema de agendamento","card",   ["instagram", "tiktok"],             "numero"),
    ("C-B04", "carla",   "bofu", "Quanto tempo você recupera por semana com agendamento automático","story",     ["instagram"],                       "numero"),
    ("C-B05", "carla",   "bofu", "Como o Salão 365° resolve os 3 maiores problemas do salão pequeno","reels",   ["instagram", "tiktok"],             "promessa"),
    # ── LÉO · TOFU ───────────────────────────────────────────────────────────
    ("L-T01", "leo",     "tofu", "Quanto fatura uma barbearia por mês — referências reais por porte","reels",    ["instagram", "tiktok", "youtube"],   "numero"),
    ("L-T02", "leo",     "tofu", "A rotina real de um dono de barbearia que está crescendo",         "reels",     ["instagram", "tiktok"],             "promessa"),
    ("L-T03", "leo",     "tofu", "Como montar uma barbearia lucrativa do zero — o que ninguém conta","carrossel", ["instagram"],                       "erro"),
    ("L-T04", "leo",     "tofu", "Fila de espera ou agendamento: qual modelo faz barbearia ganhar mais","reels",  ["instagram", "tiktok", "youtube"],  "numero"),
    ("L-T05", "leo",     "tofu", "Por que barbeiro pede demissão — e como manter o melhor da equipe","carrossel", ["instagram"],                       "dor"),
    ("L-T06", "leo",     "tofu", "Como organizar o dia a dia da barbearia sem depender de anotação", "reels",     ["instagram", "tiktok"],             "erro"),
    ("L-T07", "leo",     "tofu", "Quanto custa abrir uma barbearia — planilha real de investimento", "card",      ["instagram", "tiktok"],             "numero"),
    ("L-T08", "leo",     "tofu", "O erro que impede barbearia de sair do lucro zero todo mês",       "story",     ["instagram"],                       "erro"),
    # ── LÉO · MOFU ───────────────────────────────────────────────────────────
    ("L-M01", "leo",     "mofu", "Como calcular comissão de barbeiro sem prejudicar o caixa",        "carrossel", ["instagram"],                       "numero"),
    ("L-M02", "leo",     "mofu", "Como fidelizar cliente na barbearia — o que funciona de verdade",  "reels",     ["instagram", "tiktok", "youtube"],   "numero"),
    ("L-M03", "leo",     "mofu", "Como controlar o estoque de produtos da barbearia sem surpresa",   "card",      ["instagram", "tiktok"],             "dor"),
    ("L-M04", "leo",     "mofu", "Como lidar com barbeiro que traz os próprios clientes e quer sair","reels",     ["instagram", "tiktok"],             "dor"),
    ("L-M05", "leo",     "mofu", "Como aumentar o ticket médio da barbearia sem aumentar preço",     "story",     ["instagram"],                       "numero"),
    ("L-M06", "leo",     "mofu", "Como reduzir no-show na barbearia com agendamento online",         "carrossel", ["instagram"],                       "numero"),
    ("L-M07", "leo",     "mofu", "Como treinar barbeiro novo sem parar a produção do dia",           "reels",     ["instagram", "tiktok"],             "erro"),
    # ── LÉO · BOFU ───────────────────────────────────────────────────────────
    ("L-B01", "leo",     "bofu", "Melhor sistema para barbearia em 2026 — o que avaliar antes de escolher","carrossel",["instagram"],                  "numero"),
    ("L-B02", "leo",     "bofu", "Como o Salão 365° funciona para barbearia com mais de 1 barbeiro", "reels",     ["instagram", "tiktok", "youtube"],   "promessa"),
    ("L-B03", "leo",     "bofu", "Quanto uma barbearia economiza por mês com sistema de gestão",     "card",      ["instagram", "tiktok"],             "numero"),
    ("L-B04", "leo",     "bofu", "Checklist: sua barbearia está pronta para escalar?",               "story",     ["instagram"],                       "erro"),
    ("L-B05", "leo",     "bofu", "Migrar de app para o Salão 365°: quanto tempo leva e como fazer",  "reels",     ["instagram", "tiktok"],             "dor"),
]

# ─── Horários de publicação por dia/formato ───────────────────────────────────

SCHEDULE = {
    # (dia_semana 0=seg, formato) -> horário BRT
    (0, "reels"):     "09:00",   # segunda — reels alcance
    (1, "card"):      "12:00",   # terça   — card meio-dia
    (2, "carrossel"): "11:00",   # quarta  — carrossel manhã
    (2, "story"):     "18:00",   # quarta  — story tarde
    (3, "reels"):     "09:00",   # quinta  — reels alcance
    (4, "carrossel"): "11:00",   # sexta   — carrossel BOFU
    (5, "card"):      "10:00",   # sábado  — card leve (opcional)
}

# Distribuição alvo da semana (moderada 5–7 posts)
WEEK_PLAN = [
    {"dia": 0, "formato": "reels",     "funil_pref": "tofu",             "persona_pref": None},
    {"dia": 1, "formato": "card",      "funil_pref": "mofu",             "persona_pref": None},
    {"dia": 2, "formato": "carrossel", "funil_pref": "mofu",             "persona_pref": None},
    {"dia": 2, "formato": "story",     "funil_pref": "bofu",             "persona_pref": None},
    {"dia": 3, "formato": "reels",     "funil_pref": "tofu",             "persona_pref": None},
    {"dia": 4, "formato": "carrossel", "funil_pref": "bofu",             "persona_pref": None},
    {"dia": 5, "formato": "card",      "funil_pref": "tofu",             "persona_pref": None},
]

# ─── ClickUp API client ───────────────────────────────────────────────────────

CU_BASE = "https://api.clickup.com/api/v2"

def cu_headers() -> dict:
    return {"Authorization": CLICKUP_API_TOKEN, "Content-Type": "application/json"}


def get_recent_tasks(days: int = 28) -> list[dict]:
    """Busca tasks criadas nos últimos N dias para deduplicação de temas."""
    since_ms = int((datetime.now(BRT) - timedelta(days=days)).timestamp() * 1000)
    url = f"{CU_BASE}/list/{CLICKUP_LIST_ID}/task"
    params = {"date_created_gt": since_ms, "include_closed": "true", "page": 0}
    tasks = []
    while True:
        resp = requests.get(url, headers=cu_headers(), params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("tasks", [])
        tasks.extend(batch)
        if not data.get("last_page", True):
            params["page"] += 1
        else:
            break
    log.info(f"[ClickUp] {len(tasks)} tasks recentes carregadas ({days}d)")
    return tasks


def extract_used_ids(tasks: list[dict]) -> set[str]:
    """Extrai IDs de temas já usados (campo CF_TEMA_ID) das tasks recentes."""
    used = set()
    for t in tasks:
        for cf in t.get("custom_fields", []):
            if cf.get("id") == CF_TEMA_ID and cf.get("value"):
                used.add(cf["value"])
    return used


def create_task(
    title: str,
    description: str,
    due_date_str: str,   # "YYYY-MM-DD"
    custom_fields: list[dict],
) -> dict:
    """Cria uma task no ClickUp e retorna o objeto criado."""
    due_dt = datetime.strptime(due_date_str, "%Y-%m-%d").replace(tzinfo=BRT)
    due_ms = int(due_dt.timestamp() * 1000)

    payload = {
        "name": title,
        "description": description,
        "status": "backlog",
        "due_date": due_ms,
        "due_date_time": False,
        "custom_fields": custom_fields,
    }
    url = f"{CU_BASE}/list/{CLICKUP_LIST_ID}/task"
    resp = requests.post(url, headers=cu_headers(), json=payload, timeout=15)
    resp.raise_for_status()
    task = resp.json()
    log.info(f"[ClickUp] Task criada: {task['id']} — {title}")
    return task

# ─── Claude API ──────────────────────────────────────────────────────────────

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def ask_claude(prompt: str, max_tokens: int = 800) -> str:
    msg = _client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def suggest_topic_via_claude(
    persona: str,
    funil: str,
    formato: str,
    used_themes: list[str],
) -> dict:
    """Usa Claude para sugerir tema quando o pool fixo está esgotado."""

    persona_desc = {
        "jessica": "manicure/nail designer autônoma, 22–38 anos, atende em casa, agenda pelo WhatsApp",
        "carla":   "dona de salão pequeno, 28–45 anos, 2–4 cadeiras, trabalha 10–12h/dia",
        "leo":     "dono de barbearia, 25–40 anos, 1–3 barbeiros, tech-friendly",
    }
    funil_desc = {
        "tofu": "topo de funil — ainda não percebeu que precisa de sistema (conteúdo educativo/identificação)",
        "mofu": "meio de funil — consciente do problema, buscando solução",
        "bofu": "fundo de funil — considerando um app de gestão (CTA direto para Salão 365°)",
    }

    used_str = "\n".join(f"- {t}" for t in used_themes[-20:]) if used_themes else "nenhum ainda"

    prompt = f"""Você é estrategista de conteúdo para redes sociais no nicho de beleza brasileiro.

Sugira 1 tema para um {formato} voltado para:
- Persona: {persona_desc[persona]}
- Funil: {funil_desc[funil]}

Temas já usados recentemente (não repetir):
{used_str}

Responda SOMENTE com JSON válido, sem markdown:
{{
  "tema": "título do conteúdo",
  "gancho": "primeiras palavras do vídeo/card (até 15 palavras)",
  "hook_type": "numero | dor | promessa | erro"
}}"""

    raw = ask_claude(prompt, max_tokens=300)
    # strip markdown se vier
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip().rstrip("`").strip()
    return json.loads(raw)

# ─── Lógica de seleção de temas ──────────────────────────────────────────────

GANCHO_MAP = {
    # tema_id -> gancho pré-escrito (do YOUR_TOPICS)
    "J-T01": "Você anotou no papel, no WhatsApp e na cabeça. Sabe quantos furos isso gera?",
    "J-T02": "Das 8h às 21h — o que ninguém mostra por trás das unhas perfeitas",
    "J-T03": "Se você tá recusando cliente, isso já é um sinal",
    "J-T04": "10 clientes por semana, R$ 80 cada. Deveria ser R$ 800. Sobrou quanto?",
    "J-T05": "Sua cliente decide se volta ou não antes mesmo de sentar na cadeira",
    "J-T06": "Ela sumiu depois de 2 anos. Não foi o preço.",
    "J-T07": "Não é técnica. É o que acontece antes e depois do atendimento.",
    "J-T08": "Pega a calculadora. Vai se surpreender com o resultado.",
    "J-M01": "Confirmação pelo WhatsApp gera 30% de furo. Esse número vai mudar depois desse vídeo.",
    "J-M02": "Tem uma frase que faz cliente pagar sinal sem questionamento. Vou te mostrar.",
    "J-M03": "Se você improvisa agenda todo dia, você já perdeu dinheiro antes de começar",
    "J-M04": "Você acha que tá lucrando. Mas o dinheiro vai pra onde?",
    "J-M05": "Política de cancelamento não é grosseria. É profissionalismo.",
    "J-M06": "Quando uma cliente cancela, você já tem quem ocupa o horário em 5 minutos?",
    "J-M07": "Cliente fiel gasta 3x mais que cliente novo. Você tá investindo em qual?",
    "J-B01": "Cliente agenda sozinha, você recebe, confirma e não precisa ficar no WhatsApp",
    "J-B02": "Botão de agendamento no perfil. Funciona mesmo sem site.",
    "J-B03": "7 perguntas que separam quem vai crescer de quem vai continuar apagando incêndio",
    "J-B04": "Agenda, caixa e cliente num lugar só. Sem planilha, sem caderno.",
    "J-B05": "Cronometra amanhã. A resposta vai te incomodar.",
    "C-T01": "Você é a primeira a chegar e a última a sair. E o lucro vai pra onde?",
    "C-T02": "Três funções, uma pessoa, e o dia que nunca fecha. Como sobreviver?",
    "C-T03": "Fila de espera é problema ou oportunidade? Depende do que você faz com ela.",
    "C-T04": "Não é o faturamento. É o que sobra depois de pagar tudo.",
    "C-T05": "Novo cabeleireiro na equipe: quanto você precisa ter na reserva?",
    "C-T06": "Ela foi embora sem falar nada. E você não sabe nem por quê.",
    "C-T07": "R$ 15 mil de faturamento. Quanto sobra de lucro real?",
    "C-T08": "Não é o aluguel. Não é o produto. É esse aqui.",
    "C-M01": "45% de comissão parece justo. Mas você calculou o que sobra pro salão?",
    "C-M02": "2 furos por semana, R$ 120 cada. Por ano, isso vira quanto?",
    "C-M03": "Conta no mesmo banco, cartão igual. Você sabe o que é lucro ou acha que sabe?",
    "C-M04": "Confirmação manual, mensagem no grupo, lembrança no WhatsApp. Dá trabalho e ainda falha.",
    "C-M05": "Profissional insatisfeito com horário vai embora. E você recomeça do zero.",
    "C-M06": "Aumentar preço com medo de perder cliente é o erro mais comum. Tem jeito melhor.",
    "C-M07": "Preço sem contexto parece caro. Contexto certo, o cliente pede upgrade.",
    "C-B01": "Antes de assinar qualquer sistema, veja se ele tem essas 7 funções",
    "C-B02": "Tem donos que trocam de sistema todo ano. O problema não é o sistema.",
    "C-B03": "Se o seu sistema não tem esses 5, você está no produto errado",
    "C-B04": "Donos de salão com sistema recuperam em média 6h por semana. O que você faria com isso?",
    "C-B05": "No-show, financeiro e equipe. Resolve os 3 ou não precisa.",
    "L-T01": "Barbearia com 2 cadeiras, 6 cortes por dia cada. Faz a conta.",
    "L-T02": "Não é só cortar cabelo. É gerir equipe, caixa e cliente ao mesmo tempo.",
    "L-T03": "Cadeira, tesoura e cliente. Mas o que quebra a barbearia não é falta de cliente.",
    "L-T04": "Fila parece movimento. Mas quanto você perde quando o cara vai embora sem cortar?",
    "L-T05": "O bom barbeiro não vai embora por salário. É isso aqui.",
    "L-T06": "Caderninho, WhatsApp e memória. Qual dos três vai te trair primeiro?",
    "L-T07": "R$ 15 mil? R$ 40 mil? Depende do que você não está contando.",
    "L-T08": "Movimento cheio, conta vazia. O problema é esse aqui.",
    "L-M01": "50% parece justo pro barbeiro. Mas o salão paga aluguel, produto e imposto com o quê?",
    "L-M02": "Cliente que volta toda semana vale 40x mais que cliente novo. Você tá cuidando dele?",
    "L-M03": "Acabou o produto no meio do dia. Quanto isso custou?",
    "L-M04": "Ele vai. Os clientes vão com ele? Depende do que você fez antes disso.",
    "L-M05": "Oferta de barba depois do corte aumenta ticket em 35%. Com uma frase.",
    "L-M06": "Cliente que agendou online fura 3x menos. Já testou?",
    "L-M07": "Treino paralelo ou separado? Um deles quebra o caixa no primeiro mês.",
    "L-B01": "Tem mais de 10 apps no mercado. A diferença que importa está nesses 4 pontos.",
    "L-B02": "Agenda individual por barbeiro, comissão automática, relatório diário. Funciona assim.",
    "L-B03": "Só o no-show zero já paga o sistema. O resto é lucro.",
    "L-B04": "8 perguntas. Se você responder não em 3 ou mais, precisa resolver antes de abrir outra unidade.",
    "L-B05": "Medo de perder histórico de cliente na migração? Tem como não perder nada.",
}


def select_topic(
    formato: str,
    funil_pref: str,
    used_ids: set[str],
    personas_count: dict,   # {"jessica": N, "carla": N, "leo": N}
    funils_count: dict,     # {"tofu": N, "mofu": N, "bofu": N}
    hooks_used: list[str],  # hooks já escolhidos essa semana
) -> dict:
    """
    Seleciona o melhor tema para o slot respeitando:
    - Formato preferencial
    - Funil preferido (mas aceita outro se necessário)
    - Persona menos usada na semana
    - Hook type diferente dos 2 últimos
    - Não repetir ID usado nas últimas 4 semanas
    """

    # Persona menos usada
    persona_pref = min(personas_count, key=personas_count.get)

    # Candidatos do pool fixo
    candidates = [
        t for t in YOUR_TOPICS
        if t[0] not in used_ids
        and t[4] == formato
        and t[2] == funil_pref
    ]

    # Relaxa funil se não tiver candidato
    if not candidates:
        candidates = [
            t for t in YOUR_TOPICS
            if t[0] not in used_ids and t[4] == formato
        ]

    # Relaxa formato se ainda não tiver
    if not candidates:
        candidates = [t for t in YOUR_TOPICS if t[0] not in used_ids]

    if candidates:
        # Prioriza persona menos usada
        pref = [c for c in candidates if c[1] == persona_pref]
        pool = pref if pref else candidates

        # Evita repetir os 2 últimos hook_types
        avoid_hooks = set(hooks_used[-2:])
        varied = [c for c in pool if c[6] not in avoid_hooks]
        final_pool = varied if varied else pool

        chosen = random.choice(final_pool)
        return {
            "id":       chosen[0],
            "persona":  chosen[1],
            "funil":    chosen[2],
            "tema":     chosen[3],
            "formato":  chosen[4],
            "redes":    chosen[5],
            "hook":     chosen[6],
            "gancho":   GANCHO_MAP.get(chosen[0], ""),
            "source":   "pool",
        }

    # Pool esgotado — usa Claude para sugerir
    log.info("[Planner] Pool esgotado para esse slot. Pedindo sugestão ao Claude...")
    used_themes = [t[3] for t in YOUR_TOPICS if t[0] in used_ids]
    suggestion = suggest_topic_via_claude(persona_pref, funil_pref, formato, used_themes)
    return {
        "id":      f"AI-{datetime.now(BRT).strftime('%y%m%d%H%M')}",
        "persona":  persona_pref,
        "funil":    funil_pref,
        "tema":     suggestion["tema"],
        "formato":  formato,
        "redes":    ["instagram", "tiktok"] if formato != "carrossel" else ["instagram"],
        "hook":     suggestion["hook_type"],
        "gancho":   suggestion["gancho"],
        "source":   "claude",
    }

# ─── Montagem da semana ───────────────────────────────────────────────────────

def get_next_monday() -> datetime:
    """Retorna a próxima segunda-feira (ou hoje se já for segunda)."""
    now = datetime.now(BRT)
    days_ahead = (0 - now.weekday()) % 7  # 0 = segunda
    if days_ahead == 0 and now.hour >= 10:
        days_ahead = 7  # já passou da janela de hoje, planeja próxima semana
    return (now + timedelta(days=days_ahead)).replace(hour=0, minute=0, second=0, microsecond=0)


def build_week(used_ids: set[str]) -> list[dict]:
    """
    Monta os 6–7 posts da semana respeitando todas as regras de rotação.
    Retorna lista de dicts prontos para criar no ClickUp.
    """
    monday = get_next_monday()
    week_str = monday.strftime("%d/%m/%Y")
    log.info(f"[Planner] Planejando semana de {week_str}")

    personas_count = {"jessica": 0, "carla": 0, "leo": 0}
    funils_count   = {"tofu": 0, "mofu": 0, "bofu": 0}
    hooks_used     = []
    selected_ids   = set()
    posts          = []

    for slot in WEEK_PLAN:
        dia_offset = slot["dia"]
        formato    = slot["formato"]
        funil_pref = slot["funil_pref"]

        topic = select_topic(
            formato=formato,
            funil_pref=funil_pref,
            used_ids=used_ids | selected_ids,
            personas_count=personas_count,
            funils_count=funils_count,
            hooks_used=hooks_used,
        )

        pub_date = monday + timedelta(days=dia_offset)
        pub_date_str = pub_date.strftime("%Y-%m-%d")
        horario = SCHEDULE.get((dia_offset, formato), "10:00")

        post = {
            **topic,
            "pub_date":  pub_date_str,
            "horario":   horario,
            "semana":    week_str,
            "titulo_cu": f"[{topic['formato'].upper()}] {topic['tema']} · {pub_date.strftime('%d/%m')}",
        }
        posts.append(post)

        personas_count[topic["persona"]] += 1
        funils_count[topic["funil"]] += 1
        hooks_used.append(topic["hook"])
        selected_ids.add(topic["id"])

        log.info(
            f"  [{dia_offset}] {formato:10} | {topic['persona']:8} | "
            f"{topic['funil']:4} | {topic['hook']:8} | {topic['tema'][:50]}"
        )

    # Valida distribuição mínima
    assert funils_count["tofu"] >= 2,  f"TOFU insuficiente: {funils_count}"
    assert funils_count["mofu"] >= 2,  f"MOFU insuficiente: {funils_count}"
    assert funils_count["bofu"] >= 1,  f"BOFU ausente: {funils_count}"
    assert all(v >= 1 for v in personas_count.values()), f"Persona faltando: {personas_count}"

    log.info(f"[Planner] Semana OK — personas: {personas_count} | funil: {funils_count}")
    return posts

# ─── Criação das tasks no ClickUp ─────────────────────────────────────────────

def build_description(post: dict) -> str:
    redes_str = ", ".join(post["redes"]).upper()
    return (
        f"**Tema:** {post['tema']}\n"
        f"**Persona:** {post['persona'].capitalize()}\n"
        f"**Funil:** {post['funil'].upper()}\n"
        f"**Formato:** {post['formato']}\n"
        f"**Redes:** {redes_str}\n"
        f"**Data de publicação:** {post['pub_date']} às {post['horario']} BRT\n\n"
        f"**Gancho de abertura:**\n> {post['gancho']}\n\n"
        f"---\n"
        f"_Card gerado automaticamente pelo Social Agent em {datetime.now(BRT).strftime('%d/%m/%Y %H:%M')} BRT_\n"
        f"_Tema ID: {post['id']} | Fonte: {post['source']}_"
    )


def build_custom_fields(post: dict) -> list[dict]:
    """
    Monta o payload de custom_fields para a API do ClickUp.
    Valores de dropdown precisam ser os option_ids reais — ajustar após configuração.
    """
    # Para dropdowns, o ClickUp aceita o valor string diretamente se a opção existir.
    # Para labels/multi-select, passar lista de option_ids ou strings.
    fields = [
        {"id": CF_PERSONA,  "value": post["persona"]},
        {"id": CF_FUNIL,    "value": post["funil"]},
        {"id": CF_FORMATO,  "value": post["formato"]},
        {"id": CF_REDES,    "value": ", ".join(post["redes"])},
        {"id": CF_HOOK,     "value": post["hook"]},
        {"id": CF_HORARIO,  "value": post["horario"]},
        {"id": CF_GANCHO,   "value": post["gancho"]},
        {"id": CF_TEMA_ID,  "value": post["id"]},
    ]
    # Remove campos não configurados (ainda com valor "PREENCHER")
    return [
        f for f in fields
        if f["id"] not in ("PREENCHER", "", None)
    ]


def publish_week_to_clickup(posts: list[dict]) -> list[str]:
    """Cria todas as tasks no ClickUp. Retorna lista de task_ids criados."""
    task_ids = []
    for post in posts:
        desc = build_description(post)
        cfs  = build_custom_fields(post)
        task = create_task(
            title=post["titulo_cu"],
            description=desc,
            due_date_str=post["pub_date"],
            custom_fields=cfs,
        )
        task_ids.append(task["id"])
    return task_ids

# ─── Entry point ─────────────────────────────────────────────────────────────

def run_planner() -> dict:
    """
    Pipeline principal do planejador.
    Chamado pelo scheduler.py toda segunda-feira às 09h BRT.
    Retorna resumo da execução.
    """
    log.info("═" * 60)
    log.info("[Planner] Iniciando planejamento semanal")

    # 1. Busca tasks recentes para deduplicação
    try:
        recent_tasks = get_recent_tasks(days=28)
        used_ids = extract_used_ids(recent_tasks)
        log.info(f"[Planner] {len(used_ids)} IDs de temas já usados nas últimas 4 semanas")
    except Exception as e:
        log.warning(f"[Planner] Não foi possível buscar tasks recentes: {e}. Continuando sem dedup.")
        used_ids = set()

    # 2. Monta a semana
    posts = build_week(used_ids)

    # 3. Cria cards no ClickUp
    task_ids = publish_week_to_clickup(posts)

    summary = {
        "semana":    posts[0]["semana"] if posts else "?",
        "total":     len(task_ids),
        "task_ids":  task_ids,
        "posts":     [
            {
                "id":       p["id"],
                "persona":  p["persona"],
                "funil":    p["funil"],
                "formato":  p["formato"],
                "tema":     p["tema"],
                "pub_date": p["pub_date"],
                "horario":  p["horario"],
            }
            for p in posts
        ],
    }

    log.info(f"[Planner] ✓ {len(task_ids)} cards criados no ClickUp para semana {summary['semana']}")
    log.info("═" * 60)
    return summary


# ─── Execução direta (teste local) ───────────────────────────────────────────

if __name__ == "__main__":
    result = run_planner()
    print("\n── Resumo ──")
    print(json.dumps(result, ensure_ascii=False, indent=2))
