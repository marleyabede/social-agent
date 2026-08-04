"""
agent.py — Motor de geração de conteúdo do Social Agent · Salão 365
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
    if "lash" in t or "cílios" in t or "sobrancelha" in t or "brow" in t:
        return "ana"
    return "jessica"


def _parse_redes(raw: str) -> list[str]:
    if not raw:
        return ["instagram"]
    return [r.strip().lower() for r in re.split(r"[,\s]+", raw) if r.strip()]


def _extract_tema_from_title(title: str) -> str:
    # Remove prefixo "[REELS] ", "[CARD] " etc se existir
    return re.sub(r"^\[[^\]]+\]\s*", "", title).split("·")[0].strip()

# ─── ClickUp: salvar entregáveis no card ─────────────────────────────────────

def cu_headers() -> dict:
    return {"Authorization": CLICKUP_API_TOKEN, "Content-Type": "application/json"}


def save_to_task_description(task_id: str, description: str):
    """Salva entregáveis na descrição da task (evita limite de custom fields)."""
    url = f"{CU_BASE}/task/{task_id}"
    resp = requests.put(
        url,
        headers=cu_headers(),
        json={"description": description},
        timeout=20,
    )
    resp.raise_for_status()
    log.info(f"[ClickUp] Descrição salva na task {task_id}")

# ─── Mapa de personas e funis (reutilizado nos 3 prompts) ────────────────────

PERSONA_MAP = {
    "jessica": (
        "Jéssica — manicure/nail designer autônoma, 22–38 anos, atende em casa ou studio, "
        "agenda pelo WhatsApp, sem sistema, só celular. "
        "Dores: no-show sem aviso, agenda bagunçada no WhatsApp, dificuldade de cobrar sinal, não sabe se tem lucro real."
    ),
    "ana": (
        "Ana — lash/brow designer autônoma, 22–35 anos, atende em casa ou studio compartilhado, "
        "agenda pelo WhatsApp, quer parecer profissional sem studio fixo caro. "
        "Dores: clientes somem após 1–2 sessões, dificuldade de sair do WhatsApp, quer imagem profissional sem grande investimento."
    ),
    "carla": (
        "Carla — dona de salão pequeno, 28–45 anos, 2–4 cadeiras, "
        "trabalha 10–12h/dia, mistura conta pessoal com a do salão. "
        "Dores: gestão de equipe (comissão, escala), controle financeiro com múltiplos profissionais, recrutamento e retenção."
    ),
    "leo": (
        "Léo — dono de barbearia, 25–40 anos, 1–3 barbeiros, "
        "tech-friendly, já pesquisou outros apps do mercado. "
        "Dores: fidelização de clientes (alta concorrência), precificação e comissão, controle de caixa."
    ),
}

FUNIL_MAP = {
    "tofu": (
        "TOPO DE FUNIL — ainda não percebeu que precisa de sistema. "
        "Conteúdo educativo e de identificação. NÃO citar o Salão 365 diretamente. "
        "Mencionar 'app de gestão' só no final, 1x, como possibilidade natural. Nunca nomear o produto."
    ),
    "mofu": (
        "MEIO DE FUNIL — consciente do problema, buscando solução. "
        "Introduzir o Salão 365 no meio e no CTA final. Máx 2 menções. "
        "Ao mencionar, citar 1 funcionalidade real (ver PRODUTO abaixo) que resolve a dor do post."
    ),
    "bofu": (
        "FUNDO DE FUNIL — considerando um app. CTA forte para app.salao365.com. "
        "Citar 2–3 funcionalidades reais do produto (ver PRODUTO abaixo) que resolvem as dores da persona. "
        "Mencionar plano disponível se relevante. Máx 3 menções ao produto."
    ),
}

PRODUTO = """
## PRODUTO — SALÃO 365 (funcionalidades reais, atualizado 30/07/2026)

### PARA AUTÔNOMAS (jessica e ana)
- Agendamento online: link público para cliente agendar sozinha, sem WhatsApp, sem criar conta
- QR code e link únicos para compartilhar no Instagram ou imprimir
- Lembretes automáticos 24h e 1h antes (push grátis; WhatsApp no plano Profissional — 100/mês)
- Pacote de sessões pré-pago: cliente paga antes, sessões são debitadas a cada atendimento — reduz no-show e garante receita
- Cobranças pendentes: marcar agendamento com vencimento e acompanhar quem ainda não pagou
- Bloquear cliente inadimplente do agendamento online (mensagem personalizada)
- Histórico completo de atendimentos por cliente
- Importar clientes direto dos contatos do celular
- Ficha de anamnese digital (plano Profissional): perguntas antes do agendamento, campos condicionais, assinatura digital — LGPD
- Registro de despesas com categorias e recorrência (aluguel, produtos, etc.)

### PARA CARLA (salão com equipe)
- Comissões automáticas (plano Profissional): calcula, fecha por período, paga com recibo PDF enviável por WhatsApp
- Ajustes de comissão: bônus, desconto ou vale sobre o fechamento
- Relatórios: resumo diário/mensal, ranking de clientes (quem mais gastou), ranking de serviços (mais vendidos), resumo por profissional
- Exportar tudo em Excel: clientes, agendamentos, despesas, serviços
- Equipe com roles: owner, admin, manager, user — controla quem vê o quê
- Aniversários de clientes com filtro de período (plano Premium ou superior)
- Despesas recorrentes: aluguel, assinaturas, fornecedor fixo

### PARA LÉO (barbearia com barbeiros)
- Múltiplos profissionais: grátis 1, Profissional 2, Business 5
- Cada barbeiro tem agenda própria, horários personalizados, serviços habilitados individualmente
- Comissão por barbeiro: taxa configurável por serviço, fechamento mensal com recibo PDF
- Agendamento online com escolha de barbeiro pelo cliente
- Ranking de barbeiros por receita e atendimentos
- Pacote de sessões por barbeiro

### PLANOS (resumo para CTAs de BOFU)
- Grátis: 30 agendamentos/mês, 1 profissional, lembretes push
- Profissional: 500 agendamentos, 2 profissionais, 100 lembretes WhatsApp/mês, comissões, anamnese
- Business: 1000 agendamentos, 5 profissionais, 300 lembretes WhatsApp/mês
- Teste grátis disponível em app.salao365.com

### NUNCA INVENTAR
Não mencionar funcionalidades fora desta lista. Se o tema não tem match direto com o produto, mencionar apenas o benefício geral (ex: "ter tudo num só lugar") sem inventar recurso específico.
"""

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
        "Card estático — UMA ÚNICA IMAGEM. Não há slides, não há sequência, não há swipe. "
        "Todo o conteúdo do visual fica visível de uma vez, numa tela de celular. "
        "Roteiro = exatamente o que aparece nessa única imagem: "
        "TÍTULO (máx 7 palavras) + 3–4 bullets ABAIXO DO TÍTULO. "
        "Cada bullet: máx 6 palavras. Começa com verbo imperativo ou número. "
        "Se o título começa com 'Como', bullets = os passos numerados da resposta. "
        "Se começa com 'Quanto/Por que', bullets = os dados/razões da resposta. "
        "PROIBIDO bullet que não entrega a resposta prometida no título. "
        "PROIBIDO estrutura de slides (Slide 1, Slide 2...) — isso é formato carrossel, não card."
    ),
    "carrossel": (
        "Carrossel — 5 a 8 slides. Slide 1: gancho/promessa. "
        "Slides 2–6: 1 ideia por slide, texto curto. Último: CTA."
    ),
}

DIMENSOES_MAP = {
    "reels":     "1080×1920px (9:16)",
    "story":     "1080×1920px (9:16) — cada frame",
    "card":      "1080×1350px (3:4)",
    "carrossel": "1080×1350px (3:4) — cada slide",
}

BRANDBOOK = """
## IDENTIDADE VISUAL OBRIGATÓRIA — SALÃO 365

### PALETA DE CORES (usar EXATAMENTE estes HEX)
Cor principal (destaque, CTAs, elementos-chave): #5E4FD3 (Roxo Institucional)
Escuros: #393276, #453B95, #5145B4
Claros:  #9E95E5, #BFB9ED, #DFDCF6 (lavanda)
Neutros claros: #F2F2F2, #EBEBEB, #E0E0E0, #D6D6D6
Neutros escuros (ink/navy): #201E38, #37354C, #4D4B60, #636274
Sinalização (APENAS para status/alertas, nunca decoração):
  Verde: #00AF8D, #22E0A1 | Amarelo: #FECA03, #F0F16E | Vermelho: #EF3F58, #FF739B

Distribuição: 60% / 30% / 10% — combinações aprovadas:
  1) 60% ink escuro + 30% lavanda + 10% roxo institucional
  2) 60% lavanda + 30% ink escuro + 10% roxo institucional
  3) 60% roxo institucional + 30% lavanda + 10% ink escuro
  4) 60% roxo institucional + 30% ink escuro + 10% lavanda

### TIPOGRAFIA
Headline/títulos: Argent CF Light (serifada) — SEMPRE maior que o corpo
Corpo/texto/apoio: General Sans Regular ou SemiBold (sans-serif) — SEMPRE menor que headline
PROIBIDO usar outras fontes. PROIBIDO usar Argent CF em corpo de texto.

### LOGO
Nome: "Salão 365" (com ® em lockups). Altura fixa de 20px em posts sociais.
Posição: inferior, centralizado ou canto inferior direito.
Incluir APENAS em posts MOFU e BOFU. Presença discreta.
PROIBIDO: distorcer, rotacionar, aplicar sombra, gradiente, efeitos, trocar cores.

### GRID SOCIAL
Feed post: 1080×1350px (3:4), margens top/bottom 135px, left/right 35px
Stories:   1080×1920px (9:16), margens top/bottom 135px, left/right 35px

### ELEMENTO DE APOIO
Padrão ondulado derivado do símbolo S, em roxo institucional — para fundos.
Elemento squiggle (linha ondulada) — para sublinhar headlines e palavras-chave.
Ambos são COMPLEMENTARES, nunca competem com a informação principal.

### ÍCONES
Estilo outline ou filled, traços arredondados, peso uniforme.
Cores aprovadas: roxo sobre lavanda, branco sobre roxo, lavanda sobre ink, roxo sobre cinza claro.

### FOTOGRAFIA
Usar: iluminação natural/suave, ambientes organizados, profissionais em atividade,
      expressões genuínas, cenários modernos, profundidade de campo suave.
Evitar: bagunça, poses artificiais, stock genérico, filtros excessivos, cenários corporativos frios.
Roxo institucional deve estar presente (elementos gráficos, objetos, destaques).

### FORMAS E BOTÕES
Botões: pill arredondados. Cards: cantos arredondados (squircle). FAB: circular roxo com ícone branco.
"""

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
Formato máximo: 1 linha. "→ O Salão 365 resolve isso. Teste grátis."
TOFU: mencionar app só 1x, no final, como possibilidade natural.

### ANTI-CONCORRENTE
Nunca citar: Booksy, Trinks, iSalon, SetaDigital, Neon, AgendaOnline.
Se necessário comparar: "outros apps do mercado".
"""

# ─── Helpers format-specific ────────────────────────────────────────────────

def _secao_conteudo(formato: str) -> str:
    """Retorna a seção de regras de conteúdo específica para o formato."""
    if formato == "card":
        return """━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VISUAL DO CARD (card não é vídeo — não tem roteiro, não tem gancho, não tem slides)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Card = 1 imagem estática. Todo o conteúdo visual cabe numa única tela de celular de uma vez.

Estrutura obrigatória do visual (exatamente isso, nada mais):
  HEADLINE: [resposta direta ao tema — máx 7 palavras]
  • BULLET 1: [verbo imperativo ou número — máx 6 palavras]
  • BULLET 2: [verbo imperativo ou número — máx 6 palavras]
  • BULLET 3: [verbo imperativo ou número — máx 6 palavras]
  (• BULLET 4: opcional, só se absolutamente necessário)

Regras dos bullets:
- "Como X" → bullets são os PASSOS de X (numerados, em ordem)
- "Quanto X" → bullets são os NÚMEROS que respondem X
- "Por que X" → bullets são as RAZÕES de X
- Bullet que não responde a promessa do headline = errado, corte

PROIBIDO no visual do card: gancho, slides, notas de edição, cenas, texto de vídeo, duração
O GANCHO vai na primeira linha da LEGENDA (seção copy), não no visual."""

    if formato == "carrossel":
        return """━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REGRAS DE ROTEIRO — CARROSSEL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- COERÊNCIA TEMÁTICA: todos os slides respondem ao Tema. PROIBIDO misturar dois assuntos.
- Estrutura: [Slide 1] Capa/Gancho → [Slides 2–N] 1 insight por slide → [Último slide] CTA
- Capa: headline forte (máx 7 palavras) + gancho que tease o conteúdo
- Slides intermediários: 1 ideia por slide, máx 2 frases curtas
- Último slide: CTA direto, fundo roxo (#5E4FD3) para contraste
- GANCHO SERVE O TEMA: o gancho anuncia exatamente o que o carrossel entrega"""

    # reels / story
    return """━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REGRAS DE ROTEIRO — VÍDEO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- COERÊNCIA TEMÁTICA: gancho, roteiro e copy respondem ao mesmo Tema. PROIBIDO misturar assuntos.
- GANCHO (3s): número impactante, erro comum ou promessa direta. PROIBIDO: "Oi gente", "Hoje vou falar".
- GANCHO SERVE O TEMA: o gancho tease a resposta do Tema. Gancho desconectado = promessa falsa.
- Cada frase deve ensinar, provocar ou avançar. Elimine preenchimento.
- Estrutura Reels: [0s] GANCHO → [5s] PROBLEMA → [15s] SOLUÇÃO passo a passo → [50s] CTA
- Estrutura Story: 1 ideia por frame, 3–5 frames, CTA no último"""


def _schema_saida(formato: str, redes: list, duracao: str, dimensoes: str,
                  logo_val: str, logo_pos: str) -> str:
    """Retorna o schema JSON de saída específico para o formato."""
    redes_str = str(redes)

    copy_schema = f"""  "copy": {{
    "instagram": {{"legenda": "texto completo", "hashtags": ["#tag"], "primeira_linha": "gancho da legenda"}},
    "tiktok": {{"legenda": "até 150 chars", "hashtags": ["#tag"]}},
    "youtube": {{"titulo": "até 60 chars", "descricao": "até 200 chars", "hashtags": ["#tag"]}}
  }}"""

    briefing_base = f"""    "formato": "{formato}",
    "dimensoes": "{dimensoes}",
    "paleta": {{"primaria": "#5E4FD3", "secundaria": "#hex", "texto": "#hex", "fundo": "#hex"}},
    "distribuicao_cor": "60% X + 30% Y + 10% Z",
    "tipografia": {{"headline": "Argent CF Light — tamanho", "corpo": "General Sans Regular — tamanho"}},
    "mood": ["palavra1", "palavra2", "palavra3"],
    "estilo_visual": "descrição em 1 frase",
    "logo_salao365": {logo_val},
    "logo_posicao": {logo_pos},
    "elemento_apoio": "squiggle, padrão ondulado, ou nenhum",
    "margens": "top/bottom 135px, left/right 35px",
    "proibidos": ["elemento1", "elemento2"],
    "nota_designer": "instrução global para o designer" """

    if formato == "card":
        return f"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SAÍDA — JSON VÁLIDO ÚNICO, SEM MARKDOWN
Gere APENAS as chaves de redes solicitadas: {redes_str}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{{
  "visual_card": {{
    "headline": "máx 7 palavras — entrega a promessa do tema",
    "bullets": [
      "bullet 1 — verbo imperativo ou número — máx 6 palavras",
      "bullet 2 — máx 6 palavras",
      "bullet 3 — máx 6 palavras"
    ],
    "palavras_chave_visuais": ["visual1", "visual2", "visual3"]
  }},
{copy_schema},
  "briefing": {{
{briefing_base},
    "layout_card": {{
      "headline": "idêntica a visual_card.headline",
      "bullets_texto": "• bullet1\\n• bullet2\\n• bullet3",
      "visual": "descrição visual completa do card único",
      "notas": "instruções ao designer sobre hierarquia e espaçamento"
    }}
  }}
}}"""

    if formato == "carrossel":
        return f"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SAÍDA — JSON VÁLIDO ÚNICO, SEM MARKDOWN
Gere APENAS as chaves de redes solicitadas: {redes_str}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{{
  "roteiro": {{
    "titulo_conteudo": "título interno para ClickUp",
    "gancho": "texto exato da capa — máx 7 palavras",
    "slides": [
      {{"numero": 1, "texto_principal": "capa — máx 7 palavras", "subtexto": "opcional", "visual": "descrição"}},
      {{"numero": 2, "texto_principal": "insight — máx 7 palavras", "subtexto": null, "visual": "descrição"}}
    ],
    "palavras_chave_visuais": ["visual1", "visual2", "visual3"]
  }},
{copy_schema},
  "briefing": {{
{briefing_base},
    "slides": [
      {{"numero": 1, "texto_principal": "máx 7 palavras", "subtexto": null, "visual": "descrição precisa", "notas": null}}
    ]
  }}
}}"""

    # reels / story
    return f"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SAÍDA — JSON VÁLIDO ÚNICO, SEM MARKDOWN
Gere APENAS as chaves de redes solicitadas: {redes_str}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{{
  "roteiro": {{
    "titulo_conteudo": "título interno para ClickUp",
    "gancho": "texto exato dos primeiros 3s",
    "roteiro_completo": "roteiro linha a linha com marcação [0s] [5s] [15s]...",
    "notas_edicao": "instruções para editor: cortes, texto na tela, música",
    "duracao_estimada": "{duracao}",
    "palavras_chave_visuais": ["visual1", "visual2", "visual3"]
  }},
{copy_schema},
  "briefing": {{
{briefing_base},
    "slides": [
      {{"numero": 1, "texto_principal": "capa — máx 7 palavras", "subtexto": null, "visual": "descrição precisa", "notas": null}}
    ]
  }}
}}"""


# ─── Prompt único: Roteiro + Copy + Briefing ────────────────────────────────

def build_prompt_all(ctx: dict) -> str:
    persona   = ctx["persona"]
    funil     = ctx["funil"]
    formato   = ctx["formato"]
    tema      = ctx["tema"]
    gancho    = ctx["gancho"]
    redes     = ctx["redes"]
    redes_str = ", ".join(redes).upper()
    dimensoes = DIMENSOES_MAP.get(formato, "1080×1080px")

    duracao = "60s" if formato == "reels" else "30s" if formato == "story" else "N/A"
    num_slides = {"reels": "1 (capa)", "story": "3–5 frames", "card": "1", "carrossel": "5–8 slides"}.get(formato, "1")

    gancho_instrucao = (
        f'\nGancho sugerido (use como referência de tom e ângulo, mas adapte para que sirva o TEMA exato acima): "{gancho}"'
        f'\nATENÇÃO: se o gancho falar de um assunto diferente do Tema, reescreva-o pra que convirja pro Tema. O gancho é a entrada do post, não um post paralelo.'
        if gancho else
        "\nCrie um gancho original seguindo a Regra dos 3 Segundos, diretamente conectado ao Tema."
    )

    redes_copy = "\n".join(f'- {r.capitalize()}: {_copy_rede_desc(r)}' for r in redes)

    logo_val = str(funil in ("mofu", "bofu")).lower()
    logo_pos = '"inferior centralizado, 20px altura"' if funil in ("mofu", "bofu") else '"N/A"'

    produto_section = PRODUTO if funil in ("mofu", "bofu") else ""

    secao_conteudo = _secao_conteudo(formato)
    schema_saida   = _schema_saida(formato, redes, duracao, dimensoes, logo_val, logo_pos)

    # Para card: gancho_instrucao não vai no visual — só orienta a legenda
    if formato == "card":
        gancho_instrucao = (
            f'\nGancho sugerido para a LEGENDA (não vai no visual do card): "{gancho}"'
            if gancho else
            "\nCrie um gancho original para a primeira linha da LEGENDA, conectado ao Tema."
        )

    return f"""Você é estrategista de conteúdo para redes sociais da marca Salão 365.
Gere os 3 entregáveis abaixo de uma vez: ROTEIRO + COPY + BRIEFING DE DESIGN.
Escreva com a voz de um colega experiente — direto, sem enrolação.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BRIEFING DO POST
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Persona:  {PERSONA_MAP[persona]}
Funil:    {FUNIL_MAP[funil]}
Tema:     {tema}
Redes:    {redes_str}
Formato:  {FORMATO_MAP[formato]}
Duração:  {duracao}
Dimensões: {dimensoes}{gancho_instrucao}
{produto_section}

{REGRAS_BASE}

{secao_conteudo}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REGRAS DE COPY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Redes: {redes_copy}
- Primeira linha = gancho (motivo para parar o scroll)
- Legenda complementa, não repete o roteiro
- VOZ DIRETA: a PRIMEIRA FRASE da legenda deve ser em 2ª pessoa ou entrar direto num cenário numérico. PROIBIDO abrir com 3ª pessoa ("A maioria dos salões...", "Donos de salão..."), com pergunta retórica seguida de 3ª pessoa, ou com o nome da persona. Opções válidas: "Você cobra R$ X e no fim do mês não sobra nada?" / "3 cadeiras, 50 atendimentos por mês, R$ 480 de custo fixo ignorado." / direto no erro com "você".
- PROIBIDO travessão longo (—) na legenda. Use vírgula, ponto ou dois pontos. Única exceção: seta de CTA →
- CTA único: TOFU "Salva esse post" | MOFU "Comenta se acontece" | BOFU "→ Link na bio"
- Hashtags: IG 5–8 (30% nicho pequeno + 40% médio + 30% alcance) | TK 3–5 trend | YT 3–5

{BRANDBOOK}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REGRAS DE BRIEFING DE DESIGN
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Paleta: APENAS HEX do brandbook, usando uma das 4 combinações 60/30/10
- Tipografia: headline Argent CF Light, corpo General Sans Regular/SemiBold
- Grid: margens 135px top/bottom, 35px left/right
- Todo texto do visual deve estar ESCRITO (não "algum título")
- Máx 7 palavras por texto de slide | CARD: apenas 1 slide no array, com título + bullets no subtexto
- Elemento squiggle para sublinhar headline se houver espaço
- PROIBIDO inventar cores/fontes fora do brandbook
- PROIBIDO verde, amarelo ou vermelho em qualquer elemento visual — sem exceção, nem para ícones de check, status ou comparação. Use roxo (#5E4FD3) para positivo e ink (#201E38) para negativo. Ícones de check, seta ou confirmação: sempre #5E4FD3 ou #DFDCF6

{schema_saida}"""


def _copy_rede_desc(rede: str) -> str:
    return {
        "instagram": "legenda até 2200 chars, primeira linha é o gancho, 5–8 hashtags",
        "tiktok":    "legenda curta até 150 chars, 3–5 hashtags de tendência",
        "youtube":   "título até 60 chars + descrição até 200 chars + 3–5 hashtags",
    }.get(rede, "legenda adaptada para a rede")


# ─── Geração dos 3 entregáveis ───────────────────────────────────────────────

def generate_content(task: dict):
    """
    Pipeline principal: gera roteiro + copy + briefing em 1 chamada e salva no card.
    Chamado pelo scheduler quando card entra em GERANDO.
    """
    ctx = extract_task_context(task)
    task_id = ctx["task_id"]

    log.info(f"[Agent] Gerando conteúdo: {ctx['tema'][:60]}")
    log.info(f"[Agent] Persona: {ctx['persona']} | Funil: {ctx['funil']} | Formato: {ctx['formato']}")

    log.info("[Agent] Chamada única — Roteiro + Copy + Briefing...")
    raw = ask_claude(build_prompt_all(ctx), max_tokens=5000)
    try:
        result = parse_json(raw)
    except Exception as e:
        log.error(f"[Agent] Falha no parse: {e}")
        raise

    # card usa chave "visual_card"; outros formatos usam "roteiro"
    if ctx["formato"] == "card":
        script = result.get("visual_card", {})
    else:
        script = result.get("roteiro", {})
    copy   = result.get("copy", {})
    brief  = result.get("briefing", {})

    if ctx["formato"] == "card":
        log.info(f"[Agent] OK. Headline: {script.get('headline', '')[:60]}")
    else:
        log.info(f"[Agent] OK. Gancho: {script.get('gancho', '')[:60]}")

    _save_deliverables(task_id, script, copy, brief, ctx)
    log.info(f"[Agent] ✓ Todos os entregáveis salvos na task {task_id}")


def _save_deliverables(
    task_id: str,
    script: dict,
    copy: dict,
    brief: dict,
    ctx: dict,
):
    """Salva os 3 entregáveis na descrição da task."""
    desc = _format_description(script, copy, brief, ctx)
    save_to_task_description(task_id, desc)


def _format_description(script: dict, copy: dict, brief: dict, ctx: dict) -> str:
    """Formata os entregáveis como Markdown legível na descrição do ClickUp."""
    redes   = ctx["redes"]
    formato = ctx.get("formato", "reels")
    sections = []

    # ── Conteúdo principal (format-specific) ──
    if formato == "card":
        sections.append(f"## 🖼️ Visual do Card — {ctx['tema']}")
        headline = script.get("headline", "")
        bullets  = script.get("bullets", [])
        sections.append(f"**Headline:** {headline}")
        for i, b in enumerate(bullets, 1):
            sections.append(f"• {b}")
        if script.get("palavras_chave_visuais"):
            sections.append(f"\n**Palavras-chave visuais:** {', '.join(script['palavras_chave_visuais'])}")
    elif formato == "carrossel":
        sections.append(f"## 🎠 Carrossel — {script.get('titulo_conteudo', ctx['tema'])}")
        sections.append(f"**Gancho (capa):** {script.get('gancho', '')}")
        for slide in script.get("slides", []):
            n = slide.get("numero", "?")
            txt = slide.get("texto_principal", "")
            sub = slide.get("subtexto", "")
            sections.append(f"\n**[Slide {n}]** {txt}")
            if sub:
                sections.append(sub)
        if script.get("palavras_chave_visuais"):
            sections.append(f"\n**Palavras-chave visuais:** {', '.join(script['palavras_chave_visuais'])}")
    else:
        # reels / story
        sections.append(f"## 🎬 Roteiro — {script.get('titulo_conteudo', ctx['tema'])}")
        sections.append(f"**Gancho:** {script.get('gancho', '')}")
        sections.append(f"**Duração:** {script.get('duracao_estimada', 'N/A')}")
        sections.append(f"\n{script.get('roteiro_completo', '')}")
        sections.append(f"\n**Notas de edição:** {script.get('notas_edicao', '')}")
        if script.get("palavras_chave_visuais"):
            sections.append(f"**Palavras-chave visuais:** {', '.join(script['palavras_chave_visuais'])}")

    # ── Copy / Legendas ──
    sections.append("\n---\n## ✍️ Copy / Legendas")

    if "instagram" in redes and copy.get("instagram"):
        ig = copy["instagram"]
        sections.append(f"\n**Instagram**\n> {ig.get('primeira_linha', '')}")
        sections.append(ig.get("legenda", ""))
        sections.append(f"Hashtags: {' '.join(ig.get('hashtags', []))}")

    if "tiktok" in redes and copy.get("tiktok"):
        tk = copy["tiktok"]
        sections.append(f"\n**TikTok**\n{tk.get('legenda', '')}")
        sections.append(f"Hashtags: {' '.join(tk.get('hashtags', []))}")

    if "youtube" in redes and copy.get("youtube"):
        yt = copy["youtube"]
        sections.append(f"\n**YouTube**\nTítulo: {yt.get('titulo', '')}")
        sections.append(f"Descrição: {yt.get('descricao', '')}")
        sections.append(f"Hashtags: {' '.join(yt.get('hashtags', []))}")

    # ── Briefing de Design ──
    sections.append("\n---\n## 🎨 Briefing de Design")
    sections.append(f"**Formato:** {brief.get('formato', '')} | **Dimensões:** {brief.get('dimensoes', '')}")
    sections.append(f"**Mood:** {', '.join(brief.get('mood', []))}")
    sections.append(f"**Estilo:** {brief.get('estilo_visual', '')}")

    paleta = brief.get("paleta", {})
    sections.append(f"\n**Paleta:**")
    sections.append(f"- Primária: {paleta.get('primaria', '')}")
    sections.append(f"- Secundária: {paleta.get('secundaria', '')}")
    sections.append(f"- Texto: {paleta.get('texto', '')}")
    sections.append(f"- Fundo: {paleta.get('fundo', '')}")

    if brief.get("distribuicao_cor"):
        sections.append(f"**Distribuição 60/30/10:** {brief['distribuicao_cor']}")

    tipo = brief.get("tipografia", {})
    sections.append(f"\n**Tipografia:**")
    sections.append(f"- Headline: {tipo.get('headline', '')}")
    sections.append(f"- Corpo: {tipo.get('corpo', '')}")

    if brief.get("margens"):
        sections.append(f"**Margens:** {brief['margens']}")

    if formato == "card":
        layout = brief.get("layout_card", {})
        if layout:
            sections.append(f"\n**Layout do Card:**")
            sections.append(f"Headline: {layout.get('headline', '')}")
            sections.append(f"Bullets: {layout.get('bullets_texto', '')}")
            sections.append(f"Visual: {layout.get('visual', '')}")
            if layout.get("notas"):
                sections.append(f"Notas: {layout['notas']}")
    else:
        sections.append(f"\n**Slides:**")
        for slide in brief.get("slides", []):
            sections.append(f"\n**[Slide {slide.get('numero', '?')}]** {slide.get('texto_principal', '')}")
            if slide.get("subtexto"):
                sections.append(f"Subtexto: {slide['subtexto']}")
            sections.append(f"Visual: {slide.get('visual', '')}")
            if slide.get("notas"):
                sections.append(f"Notas: {slide['notas']}")

    if brief.get("elemento_apoio"):
        sections.append(f"\n**Elemento de apoio:** {brief['elemento_apoio']}")
    if brief.get("logo_salao365"):
        sections.append(f"**Logo Salão 365:** {brief.get('logo_posicao', 'inferior, 20px')}")
    sections.append(f"**Proibidos:** {', '.join(brief.get('proibidos', []))}")
    if brief.get("nota_designer"):
        sections.append(f"**Nota ao designer:** {brief['nota_designer']}")

    sections.append("\n---\n_Gerado pelo Social Agent · Salão 365_")

    return "\n".join(sections)




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

    log.info(f"[Agent] Regerando todos os entregáveis com feedback: {item}")
    generate_content(task)
    log.info(f"[Agent] ✓ Revisão concluída para task {task_id}")


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


