"""
publisher.py — Motor de publicação do Social Agent · Salão 365°
Chamado pelo scheduler.py quando card entra em PRONTO_PUBLICAR.

Função pública (contrato com scheduler.py):
  publish_post(task: dict) -> dict
    Recebe o card do ClickUp com os entregáveis já salvos.
    Publica nas redes configuradas.
    Retorna {"post_url": "...", "redes": "instagram, tiktok", "resultados": {...}}

APIs utilizadas:
  Instagram — Graph API v18+ (container → publish)
  TikTok    — Content Posting API v2
  YouTube   — Data API v3 (upload multipart)

IMPORTANTE: Este arquivo publica apenas metadados (copy + link de mídia).
Os assets de vídeo/imagem devem estar hospedados em URL pública antes da publicação.
O campo CF_MEDIA_URL do ClickUp deve conter a URL do arquivo após o designer fazer upload.
"""

import os
import json
import time
import logging
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger("publisher")
BRT = ZoneInfo("America/Sao_Paulo")

# ─── Env vars ────────────────────────────────────────────────────────────────

# Instagram Graph API
IG_ACCESS_TOKEN  = os.environ.get("IG_ACCESS_TOKEN",  "")
IG_BUSINESS_ID   = os.environ.get("IG_BUSINESS_ID",   "")

# TikTok Content Posting API v2
TIKTOK_ACCESS_TOKEN = os.environ.get("TIKTOK_ACCESS_TOKEN", "")

# YouTube Data API v3
YOUTUBE_CLIENT_ID       = os.environ.get("YOUTUBE_CLIENT_ID",       "")
YOUTUBE_CLIENT_SECRET   = os.environ.get("YOUTUBE_CLIENT_SECRET",   "")
YOUTUBE_REFRESH_TOKEN   = os.environ.get("YOUTUBE_REFRESH_TOKEN",   "")

# ClickUp (para ler entregáveis do card)
CLICKUP_API_TOKEN = os.environ["CLICKUP_API_TOKEN"]
CU_BASE = "https://api.clickup.com/api/v2"

# Custom fields — mesmos do agent.py
CF_COPY_IG   = os.environ.get("CF_COPY_IG",   "PREENCHER")
CF_COPY_TK   = os.environ.get("CF_COPY_TK",   "PREENCHER")
CF_COPY_YT   = os.environ.get("CF_COPY_YT",   "PREENCHER")
CF_FORMATO   = os.environ.get("CF_FORMATO",   "PREENCHER")
CF_REDES     = os.environ.get("CF_REDES",     "PREENCHER")
CF_MEDIA_URL = os.environ.get("CF_MEDIA_URL", "PREENCHER")   # URL do asset aprovado

# ─── Helpers gerais ───────────────────────────────────────────────────────────

def cu_headers() -> dict:
    return {"Authorization": CLICKUP_API_TOKEN, "Content-Type": "application/json"}


def get_cf(task: dict, field_id: str) -> str:
    for cf in task.get("custom_fields", []):
        if cf.get("id") == field_id:
            val = cf.get("value")
            return str(val).strip() if val is not None else ""
    return ""


def parse_cf_json(task: dict, field_id: str) -> dict:
    raw = get_cf(task, field_id)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def parse_redes(raw: str) -> list[str]:
    import re
    return [r.strip().lower() for r in re.split(r"[,\s]+", raw) if r.strip()]


# ─── Extração de contexto do card ────────────────────────────────────────────

def extract_publish_context(task: dict) -> dict:
    """
    Lê do card tudo que o publisher precisa:
    copy por rede, formato, URL da mídia aprovada.
    """
    redes_raw = get_cf(task, CF_REDES)
    redes     = parse_redes(redes_raw) if redes_raw else ["instagram"]
    formato   = get_cf(task, CF_FORMATO) or "reels"
    media_url = get_cf(task, CF_MEDIA_URL)

    copy_ig = parse_cf_json(task, CF_COPY_IG)
    copy_tk = parse_cf_json(task, CF_COPY_TK)
    copy_yt = parse_cf_json(task, CF_COPY_YT)

    return {
        "task_id":   task["id"],
        "titulo":    task["name"],
        "redes":     redes,
        "formato":   formato,
        "media_url": media_url,
        "copy": {
            "instagram": copy_ig,
            "tiktok":    copy_tk,
            "youtube":   copy_yt,
        },
    }


# ─── INSTAGRAM — Graph API ────────────────────────────────────────────────────
#
# Fluxo: criar container → aguardar processamento → publicar
# Docs: https://developers.facebook.com/docs/instagram-api/guides/content-publishing

IG_BASE = "https://graph.facebook.com/v18.0"

FORMATO_IG_MAP = {
    "reels":     "REELS",
    "carrossel": "CAROUSEL",   # requer sub-containers por item
    "card":      "IMAGE",
    "story":     "STORIES",    # via media_type IMAGE ou VIDEO
}


def publish_instagram(copy: dict, media_url: str, formato: str) -> dict:
    """
    Publica no Instagram via Graph API.
    Retorna {"url": "...", "id": "..."}
    """
    if not IG_ACCESS_TOKEN or not IG_BUSINESS_ID:
        raise EnvironmentError("IG_ACCESS_TOKEN e IG_BUSINESS_ID são obrigatórios")

    legenda    = copy.get("legenda", "")
    hashtags   = " ".join(copy.get("hashtags", []))
    caption    = f"{legenda}\n\n{hashtags}".strip()
    media_type = FORMATO_IG_MAP.get(formato, "REELS")

    log.info(f"[Instagram] Criando container ({media_type})...")

    # 1. Criar container de mídia
    container_params: dict = {
        "access_token": IG_ACCESS_TOKEN,
        "caption":      caption,
    }

    is_video = formato in ("reels", "story")

    if is_video:
        container_params["media_type"] = media_type
        container_params["video_url"]  = media_url
        if formato == "reels":
            container_params["share_to_feed"] = "true"
    else:
        container_params["image_url"]  = media_url
        container_params["media_type"] = media_type

    resp = requests.post(
        f"{IG_BASE}/{IG_BUSINESS_ID}/media",
        params=container_params,
        timeout=30,
    )
    _check_ig_response(resp, "criar container")
    container_id = resp.json()["id"]
    log.info(f"[Instagram] Container criado: {container_id}")

    # 2. Aguardar processamento (vídeos precisam de mais tempo)
    if is_video:
        _wait_ig_container(container_id)

    # 3. Publicar
    log.info("[Instagram] Publicando...")
    pub_resp = requests.post(
        f"{IG_BASE}/{IG_BUSINESS_ID}/media_publish",
        params={"creation_id": container_id, "access_token": IG_ACCESS_TOKEN},
        timeout=30,
    )
    _check_ig_response(pub_resp, "publicar")
    media_id = pub_resp.json()["id"]

    # 4. Busca permalink
    permalink = _get_ig_permalink(media_id)
    log.info(f"[Instagram] ✓ Publicado: {permalink}")
    return {"url": permalink, "id": media_id}


def _wait_ig_container(container_id: str, max_wait: int = 120):
    """Aguarda processamento do vídeo no Instagram (status FINISHED)."""
    for attempt in range(max_wait // 10):
        resp = requests.get(
            f"{IG_BASE}/{container_id}",
            params={"fields": "status_code", "access_token": IG_ACCESS_TOKEN},
            timeout=15,
        )
        status = resp.json().get("status_code", "")
        log.info(f"[Instagram] Container status: {status} (tentativa {attempt + 1})")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise RuntimeError(f"Instagram rejeitou o vídeo: {resp.json()}")
        time.sleep(10)
    raise TimeoutError("Instagram não processou o vídeo a tempo")


def _get_ig_permalink(media_id: str) -> str:
    resp = requests.get(
        f"{IG_BASE}/{media_id}",
        params={"fields": "permalink", "access_token": IG_ACCESS_TOKEN},
        timeout=15,
    )
    return resp.json().get("permalink", f"https://www.instagram.com/p/{media_id}/")


def _check_ig_response(resp: requests.Response, acao: str):
    if not resp.ok:
        err = resp.json().get("error", {})
        raise RuntimeError(
            f"Instagram API erro ao {acao}: "
            f"[{err.get('code')}] {err.get('message')} — {err.get('error_user_msg','')}"
        )


# ─── TIKTOK — Content Posting API v2 ─────────────────────────────────────────
#
# Fluxo: initialize upload → upload chunk → publish
# Docs: https://developers.tiktok.com/doc/content-posting-api-get-started

TK_BASE = "https://open.tiktokapis.com/v2"

FORMATO_TK_MAP = {
    "reels": "VIDEO",
    "story": "VIDEO",
    "card":  "PHOTO",
    "carrossel": "PHOTO",
}


def publish_tiktok(copy: dict, media_url: str, formato: str) -> dict:
    """
    Publica no TikTok via Content Posting API v2.
    Retorna {"url": "...", "id": "..."}
    """
    if not TIKTOK_ACCESS_TOKEN:
        raise EnvironmentError("TIKTOK_ACCESS_TOKEN é obrigatório")

    legenda  = copy.get("legenda", "")
    hashtags = " ".join(copy.get("hashtags", []))
    title    = f"{legenda} {hashtags}".strip()[:150]  # TikTok limita a 150 chars

    media_type = FORMATO_TK_MAP.get(formato, "VIDEO")
    log.info(f"[TikTok] Iniciando publicação ({media_type})...")

    headers = {
        "Authorization": f"Bearer {TIKTOK_ACCESS_TOKEN}",
        "Content-Type":  "application/json; charset=UTF-8",
    }

    if media_type == "VIDEO":
        return _publish_tiktok_video(headers, title, media_url)
    else:
        return _publish_tiktok_photo(headers, title, media_url)


def _publish_tiktok_video(headers: dict, title: str, video_url: str) -> dict:
    # 1. Initialize upload
    init_resp = requests.post(
        f"{TK_BASE}/post/publish/video/init/",
        headers=headers,
        json={
            "post_info": {
                "title":            title,
                "privacy_level":    "PUBLIC_TO_EVERYONE",
                "disable_duet":     False,
                "disable_comment":  False,
                "disable_stitch":   False,
            },
            "source_info": {
                "source":    "PULL_FROM_URL",
                "video_url": video_url,
            },
        },
        timeout=30,
    )
    _check_tk_response(init_resp, "initialize video")
    publish_id = init_resp.json()["data"]["publish_id"]
    log.info(f"[TikTok] publish_id: {publish_id}")

    # 2. Aguarda processamento
    post_url = _wait_tiktok_publish(headers, publish_id)
    log.info(f"[TikTok] ✓ Publicado: {post_url}")
    return {"url": post_url, "id": publish_id}


def _publish_tiktok_photo(headers: dict, title: str, image_url: str) -> dict:
    init_resp = requests.post(
        f"{TK_BASE}/post/publish/content/init/",
        headers=headers,
        json={
            "post_info": {
                "title":         title,
                "privacy_level": "PUBLIC_TO_EVERYONE",
                "post_mode":     "DIRECT_POST",
                "media_type":    "PHOTO",
            },
            "source_info": {
                "source":     "PULL_FROM_URL",
                "photo_urls": [image_url],
            },
        },
        timeout=30,
    )
    _check_tk_response(init_resp, "initialize photo")
    publish_id = init_resp.json()["data"]["publish_id"]
    post_url   = _wait_tiktok_publish(headers, publish_id)
    log.info(f"[TikTok] ✓ Publicado: {post_url}")
    return {"url": post_url, "id": publish_id}


def _wait_tiktok_publish(headers: dict, publish_id: str, max_wait: int = 120) -> str:
    """Aguarda processamento e retorna a URL do post."""
    for attempt in range(max_wait // 10):
        resp = requests.post(
            f"{TK_BASE}/post/publish/status/fetch/",
            headers=headers,
            json={"publish_id": publish_id},
            timeout=15,
        )
        data   = resp.json().get("data", {})
        status = data.get("status", "")
        log.info(f"[TikTok] Status: {status} (tentativa {attempt + 1})")
        if status == "PUBLISH_COMPLETE":
            pub_info = data.get("publicaly_available_post_id", [])
            post_id  = pub_info[0] if pub_info else publish_id
            return f"https://www.tiktok.com/@me/video/{post_id}"
        if status in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"TikTok publicação falhou: {data}")
        time.sleep(10)
    raise TimeoutError("TikTok não processou o post a tempo")


def _check_tk_response(resp: requests.Response, acao: str):
    body = resp.json()
    err  = body.get("error", {})
    if err.get("code", "ok") != "ok":
        raise RuntimeError(
            f"TikTok API erro ao {acao}: "
            f"[{err.get('code')}] {err.get('message')} — {err.get('log_id','')}"
        )


# ─── YOUTUBE — Data API v3 ────────────────────────────────────────────────────
#
# Fluxo: refresh access token → resumable upload → set metadata
# Docs: https://developers.google.com/youtube/v3/guides/uploading_a_video

YT_TOKEN_URL  = "https://oauth2.googleapis.com/token"
YT_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
YT_VIDEO_URL  = "https://www.googleapis.com/youtube/v3/videos"


def _get_yt_access_token() -> str:
    """Troca refresh token por access token."""
    resp = requests.post(
        YT_TOKEN_URL,
        data={
            "client_id":     YOUTUBE_CLIENT_ID,
            "client_secret": YOUTUBE_CLIENT_SECRET,
            "refresh_token": YOUTUBE_REFRESH_TOKEN,
            "grant_type":    "refresh_token",
        },
        timeout=15,
    )
    resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError(f"YouTube OAuth falhou: {resp.json()}")
    return token


def publish_youtube(copy: dict, media_url: str, formato: str) -> dict:
    """
    Publica no YouTube via Data API v3.
    Retorna {"url": "...", "id": "..."}
    """
    if not all([YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN]):
        raise EnvironmentError(
            "YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET e YOUTUBE_REFRESH_TOKEN são obrigatórios"
        )

    titulo     = copy.get("titulo", "")[:100]
    descricao  = copy.get("descricao", "")[:5000]
    hashtags   = copy.get("hashtags", [])

    # YouTube aceita hashtags na descrição
    desc_final = f"{descricao}\n\n{' '.join(hashtags)}".strip()

    log.info(f"[YouTube] Publicando: {titulo[:60]}")

    access_token = _get_yt_access_token()
    headers_auth = {"Authorization": f"Bearer {access_token}"}

    # 1. Baixa o arquivo de vídeo para upload
    log.info(f"[YouTube] Baixando mídia de {media_url[:80]}...")
    video_bytes = _download_media(media_url)

    # 2. Inicia upload resumable
    init_headers = {
        **headers_auth,
        "Content-Type":           "application/json; charset=UTF-8",
        "X-Upload-Content-Type":  "video/mp4",
        "X-Upload-Content-Length": str(len(video_bytes)),
    }
    metadata = {
        "snippet": {
            "title":       titulo,
            "description": desc_final,
            "tags":        [t.lstrip("#") for t in hashtags],
            "categoryId":  "26",   # Howto & Style
        },
        "status": {
            "privacyStatus":           "public",
            "selfDeclaredMadeForKids": False,
        },
    }

    init_resp = requests.post(
        f"{YT_UPLOAD_URL}?uploadType=resumable&part=snippet,status",
        headers=init_headers,
        json=metadata,
        timeout=30,
    )
    init_resp.raise_for_status()
    upload_url = init_resp.headers.get("Location")
    if not upload_url:
        raise RuntimeError("YouTube não retornou URL de upload")

    log.info("[YouTube] Upload URL obtida. Enviando vídeo...")

    # 3. Upload do arquivo
    upload_resp = requests.put(
        upload_url,
        headers={
            **headers_auth,
            "Content-Type": "video/mp4",
        },
        data=video_bytes,
        timeout=300,   # vídeos podem demorar
    )
    upload_resp.raise_for_status()

    video_id = upload_resp.json().get("id")
    if not video_id:
        raise RuntimeError(f"YouTube upload falhou: {upload_resp.text[:200]}")

    yt_url = f"https://www.youtube.com/shorts/{video_id}"
    log.info(f"[YouTube] ✓ Publicado: {yt_url}")
    return {"url": yt_url, "id": video_id}


def _download_media(url: str) -> bytes:
    """Baixa mídia de URL pública. Levanta erro se > 500MB."""
    resp = requests.get(url, timeout=120, stream=True)
    resp.raise_for_status()

    chunks = []
    total  = 0
    limit  = 500 * 1024 * 1024   # 500 MB

    for chunk in resp.iter_content(chunk_size=1024 * 1024):
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            raise ValueError(f"Arquivo muito grande para upload ({total / 1e6:.1f} MB > 500 MB)")

    return b"".join(chunks)


# ─── Função pública: publish_post ─────────────────────────────────────────────

def publish_post(task: dict) -> dict:
    """
    Pipeline principal de publicação.
    Contrato com scheduler.py:
      Recebe: task dict do ClickUp (com entregáveis nos custom fields)
      Retorna: {"post_url": str, "redes": str, "resultados": dict}

    Lógica:
      1. Extrai contexto do card (copy por rede, formato, media_url)
      2. Valida que media_url está preenchida
      3. Publica em cada rede configurada
      4. Retorna URLs de cada publicação
    """
    ctx = extract_publish_context(task)

    log.info(f"[Publisher] Iniciando publicação: {ctx['titulo'][:60]}")
    log.info(f"[Publisher] Redes: {ctx['redes']} | Formato: {ctx['formato']}")

    # Validação crítica — sem mídia aprovada não publica
    if not ctx["media_url"]:
        raise ValueError(
            f"Campo CF_MEDIA_URL vazio na task {ctx['task_id']}. "
            "O designer deve fazer upload da mídia e preencher o campo antes da publicação."
        )

    resultados  = {}
    urls        = []
    erros       = []

    for rede in ctx["redes"]:
        copy = ctx["copy"].get(rede, {})

        if not copy:
            log.warning(f"[Publisher] Copy ausente para {rede}. Pulando.")
            erros.append(f"{rede}: copy não encontrada no card")
            continue

        try:
            if rede == "instagram":
                result = publish_instagram(copy, ctx["media_url"], ctx["formato"])
            elif rede == "tiktok":
                result = publish_tiktok(copy, ctx["media_url"], ctx["formato"])
            elif rede == "youtube":
                result = publish_youtube(copy, ctx["media_url"], ctx["formato"])
            else:
                log.warning(f"[Publisher] Rede desconhecida: {rede}. Pulando.")
                continue

            resultados[rede] = result
            urls.append(result["url"])
            log.info(f"[Publisher] ✓ {rede.capitalize()}: {result['url']}")

        except Exception as e:
            log.error(f"[Publisher] ✗ {rede.capitalize()}: {e}")
            erros.append(f"{rede}: {str(e)}")

    # Pelo menos uma rede deve ter publicado
    if not resultados:
        raise RuntimeError(
            f"Publicação falhou em todas as redes. Erros: {'; '.join(erros)}"
        )

    # Avisa sobre erros parciais mas não interrompe
    if erros:
        log.warning(f"[Publisher] Erros parciais: {'; '.join(erros)}")

    redes_str   = ", ".join(resultados.keys())
    post_url    = urls[0] if urls else ""

    log.info(f"[Publisher] ✓ Publicado em {len(resultados)} rede(s): {redes_str}")

    return {
        "post_url":   post_url,
        "redes":      redes_str,
        "resultados": resultados,
        "erros":      erros,
    }
