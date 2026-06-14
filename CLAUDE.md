# Social Agent — Salão 365°

## O que é este projeto
Agente Python que planeja, gera e publica conteúdo de redes sociais automaticamente.
Roda no Railway como web service. Cadência moderada: 5–7 posts/semana.
Instagram, TikTok e YouTube Shorts.

## Como rodar localmente
```
pip install -r requirements.txt
python scheduler.py
```

## Processo principal no Railway
`scheduler.py` — inicia o web server (porta 8080) + dois loops em background.
Procfile: `web: python scheduler.py`

## Arquivos principais
```
scheduler.py   — processo principal (web server + loop_planner + loop_executor)
planner.py     — planejador semanal (toda segunda 09h BRT, cria cards no ClickUp)
agent.py       — geração de conteúdo (roteiro + copy + briefing de design)
publisher.py   — publicação nas redes (Instagram Graph API, TikTok, YouTube)
notify.py      — notificações por e-mail (Gmail SMTP)
CLAUDE.md      — este arquivo
.env.example   — template de variáveis de ambiente
```

## Variáveis de ambiente (Railway)
Consulte `.env.example` para a lista completa e comentada.
Obrigatórias para funcionar:
- ANTHROPIC_API_KEY
- CLICKUP_API_TOKEN + CLICKUP_LIST_ID
- AGENT_TOKEN
- SMTP_USER + SMTP_PASS + NOTIFY_EMAIL

APIs de redes (necessárias apenas para publicação):
- IG_ACCESS_TOKEN + IG_BUSINESS_ID
- TIKTOK_ACCESS_TOKEN
- YOUTUBE_CLIENT_ID + YOUTUBE_CLIENT_SECRET + YOUTUBE_REFRESH_TOKEN

## Fluxo da state machine (ClickUp)
```
backlog → gerando → aguarda_ap1 → [revisao_copy →] aprovado_copy
       → design → design_pronto → aguarda_ap2
       → [revisao_design →] pronto_publicar → publicado
```

Estados de pausa (agente não age, aguarda mudança manual):
- aguarda_ap1  — você revisa copy + briefing
- design       — designer produz os assets
- aguarda_ap2  — você revisa o visual final

## Custom fields do ClickUp
Criar manualmente na lista e preencher os CF_* no Railway.
IDs disponíveis via: GET https://api.clickup.com/api/v2/list/{CLICKUP_LIST_ID}/field

Campos de entrada (criados pelo planner):
  CF_PERSONA, CF_FUNIL, CF_FORMATO, CF_REDES, CF_HOOK, CF_HORARIO, CF_GANCHO, CF_TEMA_ID

Campos de saída (salvos pelo agent):
  CF_ROTEIRO, CF_COPY_IG, CF_COPY_TK, CF_COPY_YT, CF_BRIEFING

Campo do designer (preenchido manualmente antes da publicação):
  CF_MEDIA_URL — URL pública do asset aprovado (vídeo ou imagem)

## Endpoints do web server
```
GET  /health        — health check Railway
GET  /status        — estado dos loops (último planner, ciclos executor)
POST /run/planner   — dispara planejamento manual (requer Bearer AGENT_TOKEN)
POST /run/execute   — dispara ciclo de execução manual (requer Bearer AGENT_TOKEN)
```

## Personas
- jessica — manicure/nail designer autônoma, 22–38 anos, só celular, WhatsApp como agenda
- carla   — dona de salão pequeno, 28–45 anos, 2–4 cadeiras, 10–12h/dia
- leo     — dono de barbearia, 25–40 anos, 1–3 barbeiros, tech-friendly

## Funil
- tofu — topo: educativo, não cita Salão 365° diretamente
- mofu — meio: consciente do problema, introduz Salão 365° (máx 2x)
- bofu — fundo: considerando app, CTA direto para app.salao365.com

## Formatos e redes
- reels     → Instagram + TikTok + YouTube Shorts
- carrossel → Instagram only
- card      → Instagram + TikTok
- story     → Instagram only

## YOUR_TOPICS — 60 temas (atualizado em 11/06/2026)
Jessica: 20 (TOFU:8, MOFU:7, BOFU:5)
Carla:   20 (TOFU:8, MOFU:7, BOFU:5)
Léo:     20 (TOFU:8, MOFU:7, BOFU:5)
Total: 60. Atualizar aqui sempre que YOUR_TOPICS mudar no planner.py.

## Padrão editorial (resumo)
- Zero travessão longo (—) — único permitido: setas → de CTA
- Zero palavras: desmistificar, otimizar, implementar, ecossistema, alavancar
- Zero estruturas de LLM: "não é X, é Y" (máx 1x), "é aqui que entra", "vamos entender"
- Voz: 2ª pessoa, tom de colega, contrações pra/pro/num/numa/tô
- Substância: cenário numérico fechado + erro nº1 com custo (mínimo 2 de 4)
- CTA: só após momento de dor, máximo 1 linha por entregável
- Gancho: primeiros 3 segundos obrigatórios (número, dor ou promessa)
- Anti-concorrente: nunca citar Booksy, Trinks, iSalon, SetaDigital

## Regras técnicas inegociáveis
1. parse_json deve ser global no agent.py — nunca mover para dentro de função
2. Nunca publicar sem CF_MEDIA_URL preenchido — publisher.py valida isso
3. Sempre validar sintaxe com ast.parse() antes de alterar qualquer .py
4. Ao adicionar dependência nova, atualizar requirements.txt
5. Nunca usar schedule ou time.sleep no planner — ele é chamado pelo loop_planner
6. CF_MEDIA_URL é responsabilidade do designer — o agente não faz upload de mídia
