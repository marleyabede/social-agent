"""
notify.py — Notificações por e-mail do Social Agent · Salão 365°
Usa Resend (API HTTP) — sem dependência de porta SMTP.

Funções públicas:
  notify_success(subject, body)     — envia notificação de sucesso
  notify_failure(task_name, error)  — envia alerta de falha
"""

import os
import logging
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger("notify")
BRT = ZoneInfo("America/Sao_Paulo")

# ─── Env vars ────────────────────────────────────────────────────────────────

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
NOTIFY_EMAIL   = os.environ.get("NOTIFY_EMAIL",   "")
FROM_EMAIL     = os.environ.get("FROM_EMAIL",     "Social Agent <onboarding@resend.dev>")

# ─── Core ─────────────────────────────────────────────────────────────────────

def send_email(subject: str, body: str) -> bool:
    """
    Envia e-mail via Resend API (HTTP).
    Retorna True se enviado, False se falhar (nunca levanta exceção).
    """
    if not all([RESEND_API_KEY, NOTIFY_EMAIL]):
        log.warning("[Notify] RESEND_API_KEY ou NOTIFY_EMAIL não configurados.")
        return False

    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from":    FROM_EMAIL,
                "to":      [NOTIFY_EMAIL],
                "subject": subject,
                "text":    body,
                "html":    _to_html(subject, body),
            },
            timeout=15,
        )
        resp.raise_for_status()
        log.info(f"[Notify] E-mail enviado: {subject[:60]}")
        return True

    except Exception as e:
        log.error(f"[Notify] Falha ao enviar e-mail: {e}")
        return False


def _to_html(subject: str, body: str) -> str:
    lines_html = []
    for line in body.splitlines():
        line_escaped = (
            line
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        if line.startswith("##"):
            lines_html.append(f"<h3 style='color:#7b6ef6;margin:16px 0 4px'>{line_escaped[2:].strip()}</h3>")
        elif line.startswith("•") or line.startswith("-"):
            lines_html.append(f"<li style='margin:2px 0'>{line_escaped[1:].strip()}</li>")
        elif line.strip() == "---":
            lines_html.append("<hr style='border:none;border-top:1px solid #e0e0e0;margin:12px 0'>")
        elif line.strip() == "":
            lines_html.append("<br>")
        else:
            lines_html.append(f"<p style='margin:4px 0'>{line_escaped}</p>")

    now_str = datetime.now(BRT).strftime("%d/%m/%Y %H:%M")

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:Inter,Arial,sans-serif;font-size:14px;color:#222;max-width:600px;margin:0 auto;padding:24px">
  <div style="background:#11111c;border-radius:8px;padding:16px 20px;margin-bottom:20px">
    <span style="color:#7b6ef6;font-weight:700;font-size:16px">Social Agent</span>
    <span style="color:#6a6a85;font-size:12px;margin-left:12px">Salão 365°</span>
  </div>
  <h2 style="color:#11111c;font-size:18px;margin:0 0 16px">{subject}</h2>
  <div style="line-height:1.6">
    {"".join(lines_html)}
  </div>
  <div style="margin-top:24px;padding-top:16px;border-top:1px solid #e0e0e0;color:#999;font-size:12px">
    Enviado em {now_str} BRT · Social Agent · Salão 365°
  </div>
</body>
</html>"""


# ─── Notificações específicas ─────────────────────────────────────────────────

def notify_success(subject: str, body: str) -> bool:
    full_subject = f"✓ {subject}" if not subject.startswith("✓") else subject
    return send_email(full_subject, body)


def notify_failure(task_name: str, error: str) -> bool:
    now_str = datetime.now(BRT).strftime("%d/%m/%Y %H:%M")
    subject = f"⚠️ [Social Agent] Falha — {task_name[:50]}"
    body = (
        f"## Erro no Social Agent\n\n"
        f"Task:  {task_name}\n"
        f"Hora:  {now_str} BRT\n"
        f"Erro:  {error}\n\n"
        f"---\n"
        f"Acesse o Railway para ver os logs completos."
    )
    return send_email(subject, body)
