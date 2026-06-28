"""
scheduler.py — Processo principal do Social Agent · Salão 365°
Railway: web: python scheduler.py

Dois loops em background:
  loop_planner  — toda segunda 09h BRT: monta a semana e cria cards no ClickUp
  loop_executor — a cada 5 min: avança cards pelo fluxo (state machine)

Web server principal (porta 8080):
  POST /run/planner  — dispara planejamento manual
  POST /run/execute  — dispara um ciclo de execução manual
  GET  /health       — health check do Railway
  GET  /status       — resumo do estado atual
"""

import os
import json
import time
import threading
import logging
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

import requests

from planner import run_planner
from notify  import notify_success, notify_failure   # mesmo padrão do blog agent

# ─── Config ──────────────────────────────────────────────────────────────────

PORT        = int(os.environ.get("PORT", 8080))
AGENT_TOKEN = os.environ["AGENT_TOKEN"]
BRT         = ZoneInfo("America/Sao_Paulo")

CLICKUP_API_TOKEN = os.environ["CLICKUP_API_TOKEN"]
CLICKUP_LIST_ID   = os.environ["CLICKUP_LIST_ID"]

EXECUTOR_INTERVAL_SEC = 5 * 60   # polling a cada 5 minutos
PLANNER_HOUR_BRT      = 9        # segunda-feira às 09h BRT

# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [scheduler] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("scheduler")


def banner(msg: str):
    log.info("=" * 60)
    log.info(f"  {msg}")
    log.info("=" * 60)

# ─── Estado global (thread-safe) ─────────────────────────────────────────────

_lock          = threading.Lock()
_last_planner  = None   # datetime da última execução do planner
_last_executor = None   # datetime do último ciclo do executor
_executor_runs = 0      # total de ciclos do executor
_is_executing  = False  # lock para evitar ciclos simultâneos

# ─── ClickUp helpers ─────────────────────────────────────────────────────────

CU_BASE = "https://api.clickup.com/api/v2"

def cu_headers() -> dict:
    return {"Authorization": CLICKUP_API_TOKEN, "Content-Type": "application/json"}


def get_tasks_by_status(status: str) -> list[dict]:
    url = f"{CU_BASE}/list/{CLICKUP_LIST_ID}/task"
    params = {"statuses[]": status, "include_closed": "false"}
    resp = requests.get(url, headers=cu_headers(), params=params, timeout=15)
    resp.raise_for_status()
    return resp.json().get("tasks", [])


def update_task_status(task_id: str, new_status: str):
    url = f"{CU_BASE}/task/{task_id}"
    resp = requests.put(
        url,
        headers=cu_headers(),
        json={"status": new_status},
        timeout=15,
    )
    resp.raise_for_status()
    log.info(f"[ClickUp] Task {task_id} → {new_status}")


def get_task_comments(task_id: str) -> list[dict]:
    url = f"{CU_BASE}/task/{task_id}/comment"
    resp = requests.get(url, headers=cu_headers(), timeout=15)
    resp.raise_for_status()
    return resp.json().get("comments", [])


def get_custom_field(task: dict, field_id: str) -> str | None:
    for cf in task.get("custom_fields", []):
        if cf.get("id") == field_id:
            val = cf.get("value")
            return str(val) if val is not None else None
    return None


def get_due_date(task: dict) -> datetime | None:
    due_ms = task.get("due_date")
    if not due_ms:
        return None
    return datetime.fromtimestamp(int(due_ms) / 1000, tz=BRT)

# ─── State machine — estados do ClickUp ──────────────────────────────────────
#
#  backlog          → agente detecta, move para gerando
#  gerando          → agente gera conteúdo, move para aguarda_ap1
#  aguarda_ap1      → pausa. age só se mudar para aprovado_copy ou revisao_copy
#  revisao_copy     → agente lê comentário, regera, volta para aguarda_ap1
#  aprovado_copy    → agente notifica designer, move para design
#  design           → pausa. age só se mudar para design_pronto
#  aguarda_ap2      → pausa. age só se mudar para pronto_publicar ou revisao_design
#  revisao_design   → notifica designer com novo briefing, volta para design
#  pronto_publicar  → agente verifica data/hora, agenda ou publica
#  publicado        → terminal. salva link do post no card.
#
# Nomenclatura lowercase com underscore — exatamente como ficará no ClickUp.

STATUS_BACKLOG         = "backlog"
STATUS_GERANDO         = "gerando"
STATUS_AGUARDA_AP1     = "aguarda_ap1"
STATUS_REVISAO_COPY    = "revisao_copy"
STATUS_APROVADO_COPY   = "aprovado_copy"
STATUS_DESIGN          = "design"
STATUS_AGUARDA_AP2     = "aguarda_ap2"
STATUS_REVISAO_DESIGN  = "revisao_design"
STATUS_PRONTO_PUBLICAR = "pronto_publicar"
STATUS_PUBLICADO       = "publicado"

# ─── Importações lazy do agent.py (evita import circular) ────────────────────
# agent.py será criado na próxima etapa. Aqui definimos o contrato esperado.
#
# from agent import generate_content, regenerate_item
# from publisher import publish_post

def _import_agent():
    from agent import generate_content, regenerate_item
    return generate_content, regenerate_item

def _import_publisher():
    from publisher import publish_post
    return publish_post

# ─── Executor: processa um card por vez ──────────────────────────────────────

def process_backlog(task: dict):
    """BACKLOG → GERANDO: marca que o agente pegou o card."""
    task_id = task["id"]
    log.info(f"[Executor] Processando backlog: {task['name'][:60]}")
    update_task_status(task_id, STATUS_GERANDO)


_retry_count: dict[str, int] = {}
MAX_RETRIES = 2

def process_gerando(task: dict):
    """GERANDO → AGUARDA_AP1: gera roteiro, copy e briefing, salva no card."""
    task_id = task["id"]
    retries = _retry_count.get(task_id, 0)

    if retries >= MAX_RETRIES:
        log.error(f"[Executor] Task {task_id} falhou {MAX_RETRIES}x. Movendo para backlog.")
        update_task_status(task_id, STATUS_BACKLOG)
        _retry_count.pop(task_id, None)
        notify_failure(task["name"], f"Falhou {MAX_RETRIES}x seguidas. Movido para backlog.")
        return

    log.info(f"[Executor] Gerando conteúdo: {task['name'][:60]} (tentativa {retries + 1}/{MAX_RETRIES})")

    try:
        generate_content, _ = _import_agent()
        generate_content(task)
        update_task_status(task_id, STATUS_AGUARDA_AP1)
        _retry_count.pop(task_id, None)
        log.info(f"[Executor] Conteúdo gerado. Card em aguarda_ap1.")
        notify_success(
            subject=f"[Social Agent] Conteúdo gerado — aguardando aprovação",
            body=f"Card: {task['name']}\nRevisar em: https://app.clickup.com/t/{task_id}",
        )
    except Exception as e:
        _retry_count[task_id] = retries + 1
        log.error(f"[Executor] Erro ao gerar conteúdo (tentativa {retries + 1}): {e}")
        notify_failure(task["name"], str(e))


def process_revisao_copy(task: dict):
    """REVISAO_COPY: lê último comentário, regera entregável pedido, volta para AGUARDA_AP1."""
    task_id = task["id"]
    log.info(f"[Executor] Processando revisão de copy: {task['name'][:60]}")

    try:
        comments = get_task_comments(task_id)
        if not comments:
            log.warning(f"[Executor] Revisão sem comentário. Ignorando.")
            return

        last_comment = comments[-1]["comment_text"]
        log.info(f"[Executor] Comentário de revisão: {last_comment[:100]}")

        _, regenerate_item = _import_agent()
        regenerate_item(task, feedback=last_comment)
        update_task_status(task_id, STATUS_AGUARDA_AP1)
        log.info(f"[Executor] Revisão aplicada. Card voltou para aguarda_ap1.")
    except Exception as e:
        log.error(f"[Executor] Erro na revisão de copy: {e}")
        notify_failure(task["name"], str(e))


def process_aprovado_copy(task: dict):
    """APROVADO_COPY → DESIGN: notifica designer e move o card."""
    task_id = task["id"]
    log.info(f"[Executor] Copy aprovada. Movendo para design: {task['name'][:60]}")

    # Notifica designer por e-mail com link do card
    notify_success(
        subject=f"[Social Agent] Briefing aprovado — pronto para design",
        body=(
            f"Card: {task['name']}\n"
            f"O briefing foi aprovado. Acesse o card para ver os detalhes:\n"
            f"https://app.clickup.com/t/{task_id}\n\n"
            f"Ao finalizar, mude o status para 'design_pronto'."
        ),
    )
    update_task_status(task_id, STATUS_DESIGN)


def process_revisao_design(task: dict):
    """REVISAO_DESIGN → DESIGN: notifica designer com pedido de ajuste."""
    task_id = task["id"]
    log.info(f"[Executor] Revisão de design solicitada: {task['name'][:60]}")

    try:
        comments = get_task_comments(task_id)
        last_comment = comments[-1]["comment_text"] if comments else "(sem comentário)"

        notify_success(
            subject=f"[Social Agent] Ajuste de design solicitado",
            body=(
                f"Card: {task['name']}\n"
                f"Pedido de ajuste:\n> {last_comment}\n\n"
                f"Acesse o card: https://app.clickup.com/t/{task_id}\n"
                f"Ao finalizar, mude o status para 'design_pronto'."
            ),
        )
        update_task_status(task_id, STATUS_DESIGN)
    except Exception as e:
        log.error(f"[Executor] Erro na revisão de design: {e}")


def process_aguarda_ap2(task: dict):
    """
    AGUARDA_AP2: card chegou aqui vindo de design_pronto.
    Apenas notifica Marley para revisar. Estado de pausa — só age se
    status mudar para pronto_publicar ou revisao_design.
    Esta função só roda uma vez por card (na transição de design_pronto).
    """
    task_id = task["id"]
    log.info(f"[Executor] Design pronto. Notificando para aprovação 2: {task['name'][:60]}")

    notify_success(
        subject=f"[Social Agent] Design pronto — aguardando aprovação final",
        body=(
            f"Card: {task['name']}\n"
            f"O design foi entregue. Revise os assets no card:\n"
            f"https://app.clickup.com/t/{task_id}\n\n"
            f"Para publicar: mude o status para 'pronto_publicar'.\n"
            f"Para ajuste:   mude o status para 'revisao_design' e comente o que mudar."
        ),
    )


def process_pronto_publicar(task: dict):
    """PRONTO_PUBLICAR → PUBLICADO: verifica data/hora e publica."""
    task_id = task["id"]
    log.info(f"[Executor] Verificando publicação: {task['name'][:60]}")

    due = get_due_date(task)
    now = datetime.now(BRT)

    if due and due > now:
        mins_left = int((due - now).total_seconds() / 60)
        log.info(f"[Executor] Publicação agendada para {due.strftime('%d/%m %H:%M')} ({mins_left}min restantes)")
        return  # ainda não é hora — próximo ciclo verifica novamente

    # É hora de publicar
    log.info(f"[Executor] Publicando agora: {task['name'][:60]}")
    try:
        publish_post = _import_publisher()
        result = publish_post(task)

        # Salva link do post como comentário no card
        if result.get("post_url"):
            _add_comment(task_id, f"✅ Publicado: {result['post_url']}")

        update_task_status(task_id, STATUS_PUBLICADO)
        log.info(f"[Executor] Publicado com sucesso: {result.get('post_url', '?')}")
        notify_success(
            subject=f"[Social Agent] Post publicado",
            body=(
                f"Card: {task['name']}\n"
                f"URL: {result.get('post_url', 'não disponível')}\n"
                f"Redes: {result.get('redes', '?')}\n"
                f"Card: https://app.clickup.com/t/{task_id}"
            ),
        )
    except Exception as e:
        log.error(f"[Executor] Erro ao publicar: {e}")
        notify_failure(task["name"], str(e))


def _add_comment(task_id: str, text: str):
    url = f"{CU_BASE}/task/{task_id}/comment"
    requests.post(url, headers=cu_headers(), json={"comment_text": text}, timeout=10)

# ─── Transição automática de design_pronto → aguarda_ap2 ─────────────────────
# O designer muda para "design_pronto" (status intermediário não listado acima).
# O executor detecta e faz a transição + notificação.

STATUS_DESIGN_PRONTO = "design_pronto"   # status que o designer usa para sinalizar


def process_design_pronto(task: dict):
    """DESIGN_PRONTO → AGUARDA_AP2: agente detecta entrega do designer."""
    update_task_status(task["id"], STATUS_AGUARDA_AP2)
    process_aguarda_ap2(task)  # notifica imediatamente

# ─── Ciclo do executor ───────────────────────────────────────────────────────

# Mapa: status → função de processamento
# Apenas status que o agente DEVE agir. Estados de pausa não entram aqui.
PROCESSOR = {
    STATUS_BACKLOG:        process_backlog,
    STATUS_GERANDO:        process_gerando,
    STATUS_REVISAO_COPY:   process_revisao_copy,
    STATUS_APROVADO_COPY:  process_aprovado_copy,
    STATUS_DESIGN_PRONTO:  process_design_pronto,
    STATUS_REVISAO_DESIGN: process_revisao_design,
    STATUS_PRONTO_PUBLICAR:process_pronto_publicar,
}


def run_executor_cycle():
    """
    Percorre todos os status acionáveis e processa um card por vez.
    Cards em AGUARDA_AP1, AGUARDA_AP2 e DESIGN são ignorados (aguardam humano).
    """
    global _last_executor, _executor_runs, _is_executing

    with _lock:
        if _is_executing:
            log.info("[Executor] Ciclo anterior ainda em andamento. Pulando.")
            return
        _is_executing = True

    try:
        log.info(f"[Executor] Ciclo #{_executor_runs + 1} iniciado")
        total_processed = 0

        for status, processor_fn in PROCESSOR.items():
            try:
                tasks = get_tasks_by_status(status)
                if not tasks:
                    continue
                log.info(f"[Executor] {len(tasks)} card(s) em '{status}'")
                for task in tasks:
                    processor_fn(task)
                    total_processed += 1
                    time.sleep(2)  # gentleza com a API do ClickUp
            except Exception as e:
                log.error(f"[Executor] Erro ao processar status '{status}': {e}")

        with _lock:
            _last_executor = datetime.now(BRT)
            _executor_runs += 1

        log.info(f"[Executor] Ciclo concluído. {total_processed} card(s) processado(s).")

    finally:
        with _lock:
            _is_executing = False

# ─── Loop do executor ────────────────────────────────────────────────────────

def loop_executor():
    log.info(f"[Executor] Loop iniciado (intervalo: {EXECUTOR_INTERVAL_SEC // 60}min)")
    while True:
        try:
            run_executor_cycle()
        except Exception as e:
            log.error(f"[Executor] Erro inesperado no ciclo: {e}")
        time.sleep(EXECUTOR_INTERVAL_SEC)

# ─── Loop do planner ─────────────────────────────────────────────────────────

def loop_planner():
    global _last_planner
    log.info(f"[Planner] Loop iniciado (toda segunda às {PLANNER_HOUR_BRT:02d}h BRT)")

    while True:
        now = datetime.now(BRT)

        is_monday    = now.weekday() == 0
        is_right_hour = now.hour == PLANNER_HOUR_BRT

        # Evita rodar mais de uma vez na mesma hora
        already_ran_today = (
            _last_planner is not None
            and _last_planner.date() == now.date()
        )

        if is_monday and is_right_hour and not already_ran_today:
            banner(f"Social Agent — Planner | {now.strftime('%d/%m/%Y %H:%M')} BRT")
            try:
                summary = run_planner()
                with _lock:
                    _last_planner = now
                log.info(
                    f"[Planner] ✓ {summary['total']} cards criados "
                    f"para semana {summary['semana']}"
                )
                notify_success(
                    subject=f"[Social Agent] Semana planejada — {summary['total']} cards criados",
                    body=(
                        f"Semana: {summary['semana']}\n"
                        f"Total de cards: {summary['total']}\n\n"
                        + "\n".join(
                            f"• [{p['formato'].upper()}] {p['persona'].capitalize()} "
                            f"({p['funil'].upper()}) — {p['pub_date']} {p['horario']}\n"
                            f"  {p['tema']}"
                            for p in summary["posts"]
                        )
                    ),
                )
            except Exception as e:
                log.error(f"[Planner] Erro: {e}")
                notify_failure("Planejamento semanal", str(e))

        time.sleep(60)  # verifica a cada minuto

# ─── Web server ──────────────────────────────────────────────────────────────

class AgentHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass  # silencia logs padrão do HTTPServer

    # ── POST ──────────────────────────────────────────────────────────────────

    def do_POST(self):
        auth = self.headers.get("Authorization", "")
        if auth != f"Bearer {AGENT_TOKEN}":
            log.warning("[Web] Token inválido.")
            return self._respond(401, "Unauthorized")

        if self.path == "/run/planner":
            self._respond(200, "Planejamento manual acionado.")
            log.info("[Web] Planejamento manual acionado via /run/planner")
            threading.Thread(target=self._run_planner_safe, daemon=True).start()

        elif self.path == "/run/execute":
            self._respond(200, "Ciclo de execução manual acionado.")
            log.info("[Web] Ciclo manual acionado via /run/execute")
            threading.Thread(target=run_executor_cycle, daemon=True).start()

        else:
            self._respond(404, "Not found")

    # ── GET ───────────────────────────────────────────────────────────────────

    def do_GET(self):
        if self.path == "/health":
            self._respond(200, "OK")

        elif self.path == "/status":
            now = datetime.now(BRT)
            status = {
                "service":         "social-agent",
                "time_brt":        now.strftime("%d/%m/%Y %H:%M"),
                "last_planner":    _last_planner.strftime("%d/%m/%Y %H:%M") if _last_planner else "nunca",
                "last_executor":   _last_executor.strftime("%d/%m/%Y %H:%M") if _last_executor else "nunca",
                "executor_cycles": _executor_runs,
                "is_executing":    _is_executing,
                "next_planner":    _next_monday_str(),
            }
            self._respond(200, json.dumps(status, ensure_ascii=False, indent=2),
                          content_type="application/json")
        else:
            self._respond(404, "Not found")

    # ── helpers ───────────────────────────────────────────────────────────────

    def _respond(self, code: int, body: str, content_type: str = "text/plain"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body.encode())

    @staticmethod
    def _run_planner_safe():
        try:
            run_planner()
        except Exception as e:
            log.error(f"[Web] Erro no planner manual: {e}")
            notify_failure("Planejamento manual via /run/planner", str(e))


def _next_monday_str() -> str:
    now = datetime.now(BRT)
    days = (7 - now.weekday()) % 7 or 7
    nxt = now + timedelta(days=days)
    return nxt.strftime("%d/%m/%Y")

# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    banner(f"Social Agent · Salão 365°  |  {datetime.now(BRT).strftime('%d/%m/%Y %H:%M')} BRT")

    # Inicia loop do planner em background
    threading.Thread(target=loop_planner, daemon=True, name="loop_planner").start()
    log.info("[Main] Thread loop_planner iniciada")

    # Inicia loop do executor em background
    threading.Thread(target=loop_executor, daemon=True, name="loop_executor").start()
    log.info("[Main] Thread loop_executor iniciada")

    # Web server como processo principal (Railway detecta a porta)
    log.info(f"[Main] Web server iniciando na porta {PORT}...")
    server = HTTPServer(("0.0.0.0", PORT), AgentHandler)
    log.info(f"[Main] Web server pronto. Endpoints:")
    log.info(f"  GET  /health        — health check Railway")
    log.info(f"  GET  /status        — estado dos loops")
    log.info(f"  POST /run/planner   — planejamento manual")
    log.info(f"  POST /run/execute   — ciclo de execução manual")
    server.serve_forever()
