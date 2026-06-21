"""
agent.py — Motor de geração de conteúdo do Social Agent · Salão 365°
Chamado pelo scheduler.py para cada card em estado GERANDO ou REVISAO_COPY.

Funções públicas (contrato com scheduler.py):
  generate_content(task: dict)                     — gera os 3 entregáveis e salva no ClickUp
  regenerate_item(task: dict, feedback: str)       — regera só o entregável pedido
"""

import os
import json
import logging
import re
from zoneinfo import ZoneInfo

import anthropic
import requests

# ─── Config ──────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
CLICKUP_API_TOKEN = os.environ["CLICKUP_API_TOKEN"]

BRT = ZoneInfo("America/Sao_Paulo")
CU_BASE = "https://api.clickup.com/api/v2"

log = logging.getLogger("agent")

# ─── IDs dos custom fields do ClickUp (lista 901327625285) ──────────────────

# Campos de entrada (lidos pelo agent)
CF_PERSONA  = "9148d75d-f32b-41e8-8f13-2f0e482f4a0b"   # dropdown
CF_FUNIL    = "25ce1c35-4269-459e-be3e-51e83e55c1c5"   # dropdown
CF_FORMATO  = "71aa0dfb-5753-4129-a7a0-d69d44aba40e"   # dropdown
CF_REDES    = "3f39ca1a-7ed1-43cb-8595-47a79d9cd014"   # short_text
CF_HOOK     = "06fe7b07-4a8f-43b1-b543-9bed1d18ef6b"   # dropdown
CF_GANCHO   = "ee3deffc-2c4d-4eac-bd4f-87cbd9e9db36"   # text
CF_TEMA_ID  = "79d2e971-1bbf-405c-819e-659083d7835d"   # short_text

# Campos de saída (escritos pelo agent)
CF_ROTEIRO  = "9277b94b-8aaa-40f6-961c-7f28ba5e3598"   # text
CF_COPY_IG  = "63144279-d7af-4ebf-a946-25958853b0e0"   # text
CF_COPY_TK  = "6ed06fa3-05cc-4a08-9b08-fd9d18800bcb"   # text
CF_COPY_YT  = "bdddb02e-2c6c-4388-b785-3f041d8cb85c"   # text
CF_BRIEFING = "1bc6c51d-ad67-4a37-8a59-e3e79bf59c32"   # text

# ─── Claude API ──────────────────────────────────────────────────────────────

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def ask_claude(prompt: str, max_tokens: int = 3000) -> str:
    msg = _client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


# ─── parse_json — GLOBAL, nunca mover para dentro de função ──────────────────

def parse_json(raw: str) -> dict:
    """Remove markdown fences e faz parse seguro do JSON."""
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip().rstrip("`").strip()
    return json.loads(raw)

# ─── Helpers de leitura do card ──────────────────────────────────────────────

def get_cf(task: dict, field_id: str) -> str:
    """Lê valor de um custom field pelo ID. Para dropdowns, retorna o nome da opção."""
    for cf in task.get("custom_fields", []):
        if cf.get("id") == field_id:
            val = cf.get("value")
            if val is None:
                return ""
            if cf.get("type") == "drop_down":
                _aliases = {"carrosel": "carrossel"}
                for opt in cf.get("type_config", {}).get("options", []):
                    if opt.get("orderindex") == val:
                        name = opt.get("name", "")
                        return _aliases.get(name, name)
                return ""
            return str(val).strip()
    return ""


def extract_task_context(task: dict) -> dict:
    """Extrai todos os campos relevantes do card para usar nos prompts."""
    return {
        "task_id": task["id"],
        "titulo":  task["name"],
        "persona": get_cf(task, CF_PERSONA)  or _infer_persona(task["name"]),
        "funil":   get_cf(task, CF_FUNIL)    or "tofu",
        "formato": get_cf(task, CF_FORMATO)  or "reels",
        "redes":   _parse_redes(get_cf(task, CF_REDES)),
        "gancho":  get_cf(task, CF_GANCHO),
        "tema_id": get_cf(task, CF_TEMA_ID),
        "tema":    _extract_tema_from_title(task["name"]),
    }


def _infer_persona(title: str) -> str:
    t = title.lower()
    if "barbearia" in t or "barbeiro" in t:
        return "leo"
    if "salão" in t or "cabeleireiro" in t:
        return "carla"
    return "jessica"


def _parse_redes(raw: str) -> list[str]:
    if not raw:
        return ["instagram", "tiktok"]
    return [r.strip().lower() for r in re.split(r"[,\s]+", raw) if r.strip()]


def _extract_tema_from_title(title: str) -> str:
    # Remove prefixo "[REELS] ", "[CARD] " etc se existir
    return re.sub(r"^\[[^\]]+\]\s*", "", title).split("·")[0].strip()

# ─── ClickUp: salvar entregáveis no card ─────────────────────────────────────

def cu_headers() -> dict:
    return {"Authorization": CLICKUP_API_TOKEN, "Content-Type": "application/json"}


def save_to_task(task_id: str, custom_fields: list[dict], comment: str = ""):
    """Salva entregáveis como custom fields e (opcionalmente) como comentário."""
    # Atualiza custom fields
    active_fields = [f for f in custom_fields if f["id"] not in ("PREENCHER", "", None)]
    if active_fields:
        url = f"{CU_BASE}/task/{task_id}"
        resp = requests.put(
            url,
            headers=cu_headers(),
            json={"custom_fields": active_fields},
            timeout=20,
        )
        resp.raise_for_status()
        log.info(f"[ClickUp] {len(active_fields)} campos salvos na task {task_id}")

    # Salva como comentário (sempre — fallback se campos não configurados)
    if comment:
        url = f"{CU_BASE}/task/{task_id}/comment"
        requests.post(
            url,
            headers=cu_headers(),
            json={"comment_text": comment},
            timeout=15,
        )

# ─── Mapa de personas e funis (reutilizado nos 3 prompts) ────────────────────

PERSONA_MAP = {
    "jessica": (
        "Jéssica — manicure/nail designer autônoma, 22–38 anos, atende em casa ou studio, "
        "agenda pelo WhatsApp, sem sistema, só celular"
    ),
    "carla": (
        "Carla — dona de salão pequeno, 28–45 anos, 2–4 cadeiras, "
        "trabalha 10–12h/dia, mistura conta pessoal com a do salão"
    ),
    "leo": (
        "Léo — dono de barbearia, 25–40 anos, 1–3 barbeiros, "
        "tech-friendly, já pesquisou outros apps do mercado"
    ),
}

FUNIL_MAP = {
    "tofu": (
        "TOPO DE FUNIL — ainda não percebeu que precisa de sistema. "
        "Conteúdo educativo e de identificação. NÃO citar o Salão 365° diretamente. "
        "Mencionar app de gestão só no final, 1x, como possibilidade natural."
    ),
    "mofu": (
        "MEIO DE FUNIL — consciente do problema, buscando solução. "
        "Introduzir o Salão 365° no meio e no CTA final. Máx 2 menções."
    ),
    "bofu": (
        "FUNDO DE FUNIL — considerando um app. CTA forte para app.salao365.com. "
        "Mencionar funcionalidades reais. Máx 3 menções."
    ),
}

FORMATO_MAP = {
    "reels": (
        "Reels/TikTok/Shorts — vídeo vertical 9:16, 30–60s. "
        "Estrutura: GANCHO (0–3s) → PROBLEMA (3–15s) → SOLUÇÃO/INSIGHT (15–45s) → CTA (45–60s)."
    ),
    "story": (
        "Story — vertical 9:16, sequência de 3–5 frames. "
        "Cada frame: 1 ideia. Último frame: CTA com link."
    ),
    "card": (
        "Card estático — legenda é o conteúdo principal. "
        "Roteiro = texto do visual: título forte + 3–5 bullets curtos."
    ),
    "carrossel": (
        "Carrossel — 5 a 8 slides. Slide 1: gancho/promessa. "
        "Slides 2–6: 1 ideia por slide, texto curto. Último: CTA."
    ),
}

DIMENSOES_MAP = {
    "reels":     "1080×1920px (9:16)",
    "story":     "1080×1920px (9:16) — cada frame",
    "card":      "1080×1080px (1:1) ou 1080×1350px (4:5)",
    "carrossel": "1080×1080px (1:1) — cada slide",
}

REGRAS_BASE = """
## REGRAS EDITORIAIS OBRIGATÓRIAS

### VOZ E TOM
- 2ª pessoa, colega experiente. Contrações naturais: pra, pro, num, numa, tô, tá, né, a gente.
- PROIBIDO: "é de suma importância", "no contexto atual", "de forma eficiente".
- PROIBIDO: travessão longo (—). Use vírgula ou ponto. Única exceção: seta de CTA →
- PROIBIDO usar mais de 1x: "não é X, é Y". PROIBIDO sempre: "é aqui que entra", "vamos entender".
- PROIBIDO vocabulário de LLM: desmistificar, otimizar, implementar, ecossistema,
  alavancar, potencializar, robusto, solução completa.

### SUBSTÂNCIA MÍNIMA
Obrigatório 2 dos 4 elementos:
  ✅ Cenário numérico fechado ("salão com 3 cadeiras perde R$ 480/mês")
  ✅ Erro nº1 com custo concreto ("confirmar pelo WhatsApp gera 30% de no-show")
  ✅ Passo a passo com ação imediata (verbo imperativo + detalhe específico)
  ✅ Dado de mercado com contexto

### CTA — SÓ APÓS DOR
CTA só aparece APÓS momento de dor: perda financeira, erro, cálculo de prejuízo.
NUNCA no começo ou no meio de explicação.
Formato máximo: 1 linha. "→ O Salão 365° resolve isso. Teste grátis."
TOFU: mencionar app só 1x, no final, como possibilidade natural.

### ANTI-CONCORRENTE
Nunca citar: Booksy, Trinks, iSalon, SetaDigital, Neon, AgendaOnline.
Se necessário comparar: "outros apps do mercado".
"""

# ─── Prompt 1: Roteiro ───────────────────────────────────────────────────────

def build_prompt_script(ctx: dict) -> str:
    persona   = ctx["persona"]
    funil     = ctx["funil"]
    formato   = ctx["formato"]
    tema      = ctx["tema"]
    gancho    = ctx["gancho"]
    redes_str = ", ".join(ctx["redes"]).upper()

    duracao = "60s" if formato == "reels" else "30s" if formato == "story" else "N/A"

    gancho_instrucao = (
        f'\nGancho pré-definido (use como abertura exata): "{gancho}"'
        if gancho else
        "\nCrie um gancho original seguindo a Regra dos 3 Segundos abaixo."
    )

    return f"""Você é roteirista de conteúdo para redes sociais especializado no nicho de beleza brasileiro.
Escreva com a voz de um colega experiente — direto, sem enrolação.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BRIEFING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Persona:  {PERSONA_MAP[persona]}
Funil:    {FUNIL_MAP[funil]}
Tema:     {tema}
Redes:    {redes_str}
Formato:  {FORMATO_MAP[formato]}
Duração:  {duracao}{gancho_instrucao}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REGRAS ESPECÍFICAS DE ROTEIRO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 1. GANCHO — REGRA DOS 3 SEGUNDOS
Primeiro texto falado/exibido deve ser um dos três tipos:
  a) Número impactante: "Você pode estar perdendo R$ 400 por mês sem saber"
  b) Erro comum:       "O maior erro de manicure na hora de confirmar cliente"
  c) Promessa direta:  "3 passos pra nunca mais ter furo na agenda"
PROIBIDO começar com: "Oi gente", "Hoje vou falar", apresentação pessoal,
"Você sabia que", pergunta genérica sem tensão.

## 2. RITMO — CADA FRASE TRABALHA
Cada linha do roteiro deve ensinar, provocar ou avançar.
Elimine qualquer frase que só "preenche tempo".
Teste: se tirar essa frase, o conteúdo fica melhor? Se sim, tire.

## 3. ESTRUTURA POR FORMATO
Reels/TikTok/Shorts: GANCHO → PROBLEMA → SOLUÇÃO/INSIGHT → CTA
Carrossel: Slide 1 gancho → Slides 2-N um insight por slide → Slide final CTA
Story:     Frame 1 gancho → Frames 2-4 desenvolvimento → Frame final CTA
Card:      Título impactante → 3–5 bullets curtos e diretos

{REGRAS_BASE}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SAÍDA — JSON VÁLIDO, SEM MARKDOWN
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{{
  "titulo_conteudo": "título interno para identificação no ClickUp",
  "gancho": "texto exato dos primeiros 3 segundos ou slide 1",
  "roteiro_completo": "roteiro linha a linha com marcações [0s], [15s] ou [Slide 1] etc",
  "notas_edicao": "instruções para o editor: cortes, texto na tela, emojis, música sugerida",
  "duracao_estimada": "{duracao}",
  "palavras_chave_visuais": ["elemento visual 1", "elemento visual 2", "elemento visual 3"]
}}"""


# ─── Prompt 2: Copy / Legenda ────────────────────────────────────────────────

def build_prompt_copy(ctx: dict, roteiro_completo: str, gancho: str) -> str:
    persona   = ctx["persona"]
    funil     = ctx["funil"]
    tema      = ctx["tema"]
    redes     = ctx["redes"]

    # Gera só as chaves das redes solicitadas
    redes_instrucao = "\n".join(
        f'- {r.capitalize()}: {_copy_rede_desc(r)}' for r in redes
    )

    return f"""Você é copywriter especializado em redes sociais para o nicho de beleza brasileiro.
Com base no roteiro abaixo, escreva as legendas para cada rede solicitada.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CONTEXTO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Persona:  {PERSONA_MAP[persona]}
Funil:    {FUNIL_MAP[funil]}
Tema:     {tema}
Gancho do roteiro: {gancho}

Roteiro gerado:
{roteiro_completo}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REDES SOLICITADAS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{redes_instrucao}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REGRAS ESPECÍFICAS DE COPY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 1. PRIMEIRA LINHA = GANCHO
A primeira linha (antes do "ver mais") deve ser o motivo para parar o scroll.
PROIBIDO começar com o nome do produto, saudação ou pergunta genérica.

## 2. COERÊNCIA SEM REPETIÇÃO
A legenda complementa o vídeo, não repete. Se o vídeo tem os 3 passos,
a legenda pode aprofundar 1 passo ou trazer contexto extra.

## 3. CTA ÚNICO E ESPECÍFICO — escolha o mais adequado ao funil:
  TOFU: "Salva esse post pra não esquecer"
  MOFU: "Comenta aqui se isso acontece com você"
  BOFU: "→ Link na bio pra testar grátis"
PROIBIDO ter 2 CTAs na mesma legenda.

## 4. HASHTAGS ESTRATÉGICAS
Mix obrigatório por rede:
  Instagram: 5–8 tags | 30% nicho pequeno (#manicureemcasa) + 40% médio (#manicurebrasil) + 30% alcance (#dicasdebeleza)
  TikTok:    3–5 tags de tendência do nicho beleza BR
  YouTube:   3–5 tags no final da descrição
PROIBIDO: #love #life #instagood ou qualquer tag genérica fora do nicho.

{REGRAS_BASE}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SAÍDA — JSON VÁLIDO, SEM MARKDOWN
Gere APENAS as chaves das redes solicitadas: {redes}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{{
  "instagram": {{
    "legenda": "texto completo com emojis e quebras de linha (se instagram em redes)",
    "hashtags": ["#tag1", "#tag2"],
    "primeira_linha": "só o gancho, sem emoji"
  }},
  "tiktok": {{
    "legenda": "texto curto até 150 chars (se tiktok em redes)",
    "hashtags": ["#tag1", "#tag2", "#tag3"]
  }},
  "youtube": {{
    "titulo": "título até 60 chars (se youtube em redes)",
    "descricao": "descrição até 200 chars",
    "hashtags": ["#tag1", "#tag2", "#tag3"]
  }}
}}"""


def _copy_rede_desc(rede: str) -> str:
    return {
        "instagram": "legenda até 2200 chars, primeira linha é o gancho, 5–8 hashtags no final",
        "tiktok":    "legenda curta até 150 chars visíveis, 3–5 hashtags de tendência",
        "youtube":   "título até 60 chars + descrição até 200 chars + 3–5 hashtags",
    }.get(rede, "legenda adaptada para a rede")


# ─── Prompt 3: Briefing de Design ────────────────────────────────────────────

def build_prompt_brief(ctx: dict, gancho: str, primeira_linha: str) -> str:
    persona   = ctx["persona"]
    funil     = ctx["funil"]
    formato   = ctx["formato"]
    tema      = ctx["tema"]
    dimensoes = DIMENSOES_MAP.get(formato, "1080×1080px")

    num_slides = {
        "reels":     "1 (capa)",
        "story":     "3–5 frames",
        "card":      "1",
        "carrossel": "5–8 slides",
    }.get(formato, "1")

    return f"""Você é diretor de arte para conteúdo de redes sociais no nicho de beleza.
Gere um briefing COMPLETO para o designer executar sem fazer nenhuma pergunta.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CONTEXTO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Persona:         {PERSONA_MAP[persona]}
Funil:           {FUNIL_MAP[funil]}
Tema:            {tema}
Formato:         {formato} — {num_slides}
Dimensões:       {dimensoes}
Gancho roteiro:  {gancho}
Primeira linha:  {primeira_linha}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REGRAS DO BRIEFING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ Todo texto que vai no visual deve estar ESCRITO no briefing (não "algum título")
✅ Referência visual precisa ser descrita com precisão
   Ex: "foto de manicure vista de cima, unhas em gel, fundo branco, iluminação natural pela esquerda"
✅ Hierarquia clara: o que o olho vê primeiro, segundo, terceiro
✅ Consistência de identidade: mesmo estilo nos cards da semana
✅ Máximo 7 palavras em qualquer texto de slide de carrossel
❌ PROIBIDO: "use uma cor bonita", "escolha uma fonte legal", "imagem do tema"
❌ PROIBIDO: mais de 7 palavras em textos de slide

Logo Salão 365°: incluir apenas em posts MOFU e BOFU, posição inferior direita, tamanho discreto.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SAÍDA — JSON VÁLIDO, SEM MARKDOWN
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{{
  "formato": "{formato}",
  "dimensoes": "{dimensoes}",
  "paleta": {{
    "primaria":   "#hex",
    "secundaria": "#hex",
    "texto":      "#hex",
    "fundo":      "#hex"
  }},
  "tipografia": {{
    "headline": "fonte + peso + tamanho orientativo",
    "corpo":    "fonte + peso + tamanho orientativo"
  }},
  "mood": ["palavra1", "palavra2", "palavra3"],
  "estilo_visual": "descrição em 1 frase",
  "slides": [
    {{
      "numero": 1,
      "texto_principal": "exatamente o que vai escrito",
      "subtexto": "se houver, senão null",
      "visual": "descrição precisa do elemento visual",
      "notas": "instrução extra se necessário"
    }}
  ],
  "logo_salao365": {str(funil in ("mofu", "bofu")).lower()},
  "proibidos": ["elemento 1", "elemento 2"],
  "nota_designer": "instrução global que não se encaixa acima"
}}"""


# ─── Geração dos 3 entregáveis ───────────────────────────────────────────────

def generate_content(task: dict):
    """
    Pipeline principal: gera roteiro → copy → briefing e salva tudo no card.
    Chamado pelo scheduler quando card entra em GERANDO.
    """
    ctx = extract_task_context(task)
    task_id = ctx["task_id"]

    log.info(f"[Agent] Gerando conteúdo: {ctx['tema'][:60]}")
    log.info(f"[Agent] Persona: {ctx['persona']} | Funil: {ctx['funil']} | Formato: {ctx['formato']}")

    # ── Chamada 1: Roteiro ────────────────────────────────────────────────────
    log.info("[Agent] Chamada 1/3 — Roteiro...")
    raw_script = ask_claude(build_prompt_script(ctx), max_tokens=2000)
    try:
        script = parse_json(raw_script)
    except Exception as e:
        log.error(f"[Agent] Falha no parse do roteiro: {e}")
        raise

    gancho           = script.get("gancho", ctx.get("gancho", ""))
    roteiro_completo = script.get("roteiro_completo", "")
    log.info(f"[Agent] Roteiro OK. Gancho: {gancho[:60]}")

    # ── Chamada 2: Copy / Legenda ─────────────────────────────────────────────
    log.info("[Agent] Chamada 2/3 — Copy...")
    raw_copy = ask_claude(
        build_prompt_copy(ctx, roteiro_completo, gancho),
        max_tokens=2000,
    )
    try:
        copy = parse_json(raw_copy)
    except Exception as e:
        log.error(f"[Agent] Falha no parse da copy: {e}")
        raise

    primeira_linha = (
        copy.get("instagram", {}).get("primeira_linha")
        or copy.get("tiktok",   {}).get("legenda", "")[:80]
        or gancho
    )
    log.info("[Agent] Copy OK.")

    # ── Chamada 3: Briefing de Design ─────────────────────────────────────────
    log.info("[Agent] Chamada 3/3 — Briefing de design...")
    raw_brief = ask_claude(
        build_prompt_brief(ctx, gancho, primeira_linha),
        max_tokens=2500,
    )
    try:
        brief = parse_json(raw_brief)
    except Exception as e:
        log.error(f"[Agent] Falha no parse do briefing: {e}")
        raise

    log.info("[Agent] Briefing OK.")

    # ── Salva no ClickUp ──────────────────────────────────────────────────────
    _save_deliverables(task_id, script, copy, brief, ctx)
    log.info(f"[Agent] ✓ Todos os entregáveis salvos na task {task_id}")


def _save_deliverables(
    task_id: str,
    script: dict,
    copy: dict,
    brief: dict,
    ctx: dict,
):
    """Salva os 3 entregáveis como custom fields + comentário formatado."""

    # Custom fields (apenas os configurados)
    fields = [
        {"id": CF_ROTEIRO,  "value": json.dumps(script,  ensure_ascii=False)},
        {"id": CF_COPY_IG,  "value": json.dumps(copy.get("instagram", {}),  ensure_ascii=False)},
        {"id": CF_COPY_TK,  "value": json.dumps(copy.get("tiktok", {}),     ensure_ascii=False)},
        {"id": CF_COPY_YT,  "value": json.dumps(copy.get("youtube", {}),    ensure_ascii=False)},
        {"id": CF_BRIEFING, "value": json.dumps(brief, ensure_ascii=False)},
    ]

    # Comentário formatado — sempre salvo como fallback legível
    comment = _format_comment(script, copy, brief, ctx)

    save_to_task(task_id, fields, comment=comment)


def _format_comment(script: dict, copy: dict, brief: dict, ctx: dict) -> str:
    """Formata os entregáveis como comentário Markdown legível no ClickUp."""
    redes = ctx["redes"]
    lines = [
        f"## 🎬 Roteiro — {script.get('titulo_conteudo', ctx['tema'])}",
        f"",
        f"**Gancho:** {script.get('gancho', '')}",
        f"**Duração estimada:** {script.get('duracao_estimada', 'N/A')}",
        f"",
        f"```",
        script.get("roteiro_completo", ""),
        f"```",
        f"",
        f"**Notas de edição:** {script.get('notas_edicao', '')}",
        f"",
        f"---",
        f"## ✍️ Copy / Legendas",
        f"",
    ]

    if "instagram" in redes and copy.get("instagram"):
        ig = copy["instagram"]
        lines += [
            f"**Instagram**",
            f"> {ig.get('primeira_linha', '')}",
            f"",
            ig.get("legenda", ""),
            f"",
            f"Hashtags: {' '.join(ig.get('hashtags', []))}",
            f"",
        ]

    if "tiktok" in redes and copy.get("tiktok"):
        tk = copy["tiktok"]
        lines += [
            f"**TikTok**",
            tk.get("legenda", ""),
            f"",
            f"Hashtags: {' '.join(tk.get('hashtags', []))}",
            f"",
        ]

    if "youtube" in redes and copy.get("youtube"):
        yt = copy["youtube"]
        lines += [
            f"**YouTube**",
            f"Título: {yt.get('titulo', '')}",
            f"Descrição: {yt.get('descricao', '')}",
            f"Hashtags: {' '.join(yt.get('hashtags', []))}",
            f"",
        ]

    lines += [
        f"---",
        f"## 🎨 Briefing de Design",
        f"",
        f"**Formato:** {brief.get('formato', '')} | **Dimensões:** {brief.get('dimensoes', '')}",
        f"**Mood:** {', '.join(brief.get('mood', []))}",
        f"**Estilo:** {brief.get('estilo_visual', '')}",
        f"",
        f"**Paleta:**",
        f"- Primária:   {brief.get('paleta', {}).get('primaria', '')}",
        f"- Secundária: {brief.get('paleta', {}).get('secundaria', '')}",
        f"- Texto:      {brief.get('paleta', {}).get('texto', '')}",
        f"- Fundo:      {brief.get('paleta', {}).get('fundo', '')}",
        f"",
        f"**Tipografia:**",
        f"- Headline: {brief.get('tipografia', {}).get('headline', '')}",
        f"- Corpo:    {brief.get('tipografia', {}).get('corpo', '')}",
        f"",
        f"**Slides:**",
    ]

    for slide in brief.get("slides", []):
        lines += [
            f"",
            f"**[Slide {slide.get('numero', '?')}]** {slide.get('texto_principal', '')}",
            f"Subtexto: {slide.get('subtexto') or '—'}",
            f"Visual: {slide.get('visual', '')}",
            f"Notas: {slide.get('notas') or '—'}",
        ]

    lines += [
        f"",
        f"**Proibidos:** {', '.join(brief.get('proibidos', []))}",
        f"**Nota ao designer:** {brief.get('nota_designer', '')}",
        f"",
        f"---",
        f"_Gerado pelo Social Agent · Salão 365°_",
    ]

    return "\n".join(lines)


# ─── Regeneração parcial (revisão de copy) ───────────────────────────────────

def regenerate_item(task: dict, feedback: str):
    """
    Regera apenas o entregável pedido no comentário de revisão.
    Detecta automaticamente qual entregável revisar pelo conteúdo do feedback.
    """
    ctx     = extract_task_context(task)
    task_id = ctx["task_id"]

    log.info(f"[Agent] Revisão solicitada: {feedback[:80]}")

    item = _detect_item(feedback)
    log.info(f"[Agent] Entregável detectado para revisão: {item}")

    if item == "roteiro":
        prompt = build_prompt_script(ctx)
        prompt += f"\n\n## FEEDBACK DE REVISÃO\n{feedback}\nAjuste o roteiro conforme solicitado."
        raw = ask_claude(prompt, max_tokens=2000)
        script = parse_json(raw)
        gancho = script.get("gancho", ctx.get("gancho", ""))
        fields = [{"id": CF_ROTEIRO, "value": json.dumps(script, ensure_ascii=False)}]
        comment = f"## 🔄 Roteiro revisado\n\n**Feedback:** {feedback}\n\n**Gancho:** {gancho}\n\n```\n{script.get('roteiro_completo','')}\n```"
        save_to_task(task_id, fields, comment=comment)

    elif item == "copy":
        # Busca roteiro existente do card para contexto
        roteiro_str = _get_existing_roteiro(task)
        prompt = build_prompt_copy(ctx, roteiro_str, ctx.get("gancho", ""))
        prompt += f"\n\n## FEEDBACK DE REVISÃO\n{feedback}\nAjuste a copy conforme solicitado."
        raw = ask_claude(prompt, max_tokens=2000)
        copy = parse_json(raw)
        fields = [
            {"id": CF_COPY_IG, "value": json.dumps(copy.get("instagram", {}), ensure_ascii=False)},
            {"id": CF_COPY_TK, "value": json.dumps(copy.get("tiktok", {}),    ensure_ascii=False)},
            {"id": CF_COPY_YT, "value": json.dumps(copy.get("youtube", {}),   ensure_ascii=False)},
        ]
        comment = f"## 🔄 Copy revisada\n\n**Feedback:** {feedback}\n\n{_format_copy_section(copy, ctx)}"
        save_to_task(task_id, fields, comment=comment)

    elif item == "briefing":
        gancho_atual = _get_existing_gancho(task)
        prompt = build_prompt_brief(ctx, gancho_atual, gancho_atual[:80])
        prompt += f"\n\n## FEEDBACK DE REVISÃO\n{feedback}\nAjuste o briefing conforme solicitado."
        raw = ask_claude(prompt, max_tokens=2500)
        brief = parse_json(raw)
        fields = [{"id": CF_BRIEFING, "value": json.dumps(brief, ensure_ascii=False)}]
        comment = f"## 🔄 Briefing revisado\n\n**Feedback:** {feedback}\n\n**Estilo:** {brief.get('estilo_visual','')}\n**Mood:** {', '.join(brief.get('mood',[]))}"
        save_to_task(task_id, fields, comment=comment)

    else:
        # Feedback genérico — regera os 3
        log.info("[Agent] Feedback genérico — regerando todos os entregáveis")
        generate_content(task)
        return

    log.info(f"[Agent] ✓ {item.capitalize()} revisado e salvo na task {task_id}")


def _detect_item(feedback: str) -> str:
    """Detecta qual entregável o feedback pede para revisar."""
    f = feedback.lower()
    if any(w in f for w in ["roteiro", "script", "vídeo", "video", "narração", "narracao", "fala"]):
        return "roteiro"
    if any(w in f for w in ["legenda", "copy", "caption", "hashtag", "texto da legenda"]):
        return "copy"
    if any(w in f for w in ["briefing", "design", "visual", "cor", "fonte", "slide", "layout"]):
        return "briefing"
    return "todos"


def _get_existing_roteiro(task: dict) -> str:
    """Tenta recuperar roteiro existente do custom field."""
    raw = get_cf(task, CF_ROTEIRO)
    if not raw:
        return ""
    try:
        data = json.loads(raw)
        return data.get("roteiro_completo", "")
    except Exception:
        return raw


def _get_existing_gancho(task: dict) -> str:
    """Gancho do campo do card ou do custom field de roteiro."""
    gancho = get_cf(task, CF_GANCHO)
    if gancho:
        return gancho
    raw = get_cf(task, CF_ROTEIRO)
    if raw:
        try:
            return json.loads(raw).get("gancho", "")
        except Exception:
            pass
    return ""


def _format_copy_section(copy: dict, ctx: dict) -> str:
    redes = ctx["redes"]
    lines = []
    if "instagram" in redes and copy.get("instagram"):
        ig = copy["instagram"]
        lines += [f"**Instagram:** {ig.get('legenda','')[:200]}..."]
    if "tiktok" in redes and copy.get("tiktok"):
        tk = copy["tiktok"]
        lines += [f"**TikTok:** {tk.get('legenda','')}"]
    return "\n".join(lines)
