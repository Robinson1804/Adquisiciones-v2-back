"""Sincronización manual Exchange/EWS para la bandeja de ingesta.

Diseño MVP:
- credenciales efímeras: llegan en la petición y no se guardan;
- solo persiste correos candidatos a adquisiciones;
- los adjuntos se descargan a staging solo si el correo supera el filtro;
- reutiliza ingesta_service.ingestar_correo para dedupe, archivos y auto-link.
"""
from __future__ import annotations

import base64
import hashlib
import mimetypes
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from html import unescape
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.proceso import Proceso
from app.schemas.ingesta import (
    CorreoIngestaIn,
    DocumentoExtraidoIn,
    ExchangeCredencialesIn,
    ExchangeFolderOut,
    ExchangeFoldersOut,
    ExchangeSyncIn,
    ExchangeSyncOut,
    ExchangeTestOut,
)
from app.services import ingesta_service
from app.services.etapas_catalogo import COD_A_FASE, ETAPAS_CATALOGO, FASES
from app.services.ingesta_normalizers import (
    evaluar_similitud_nombre,
    normalizar_nombre_servicio,
    normalizar_oficio,
)


@dataclass
class _Candidate:
    score: float
    motivos: list[str]
    etapa: str | None
    fase: str | None
    proceso_id: int | None
    nombre_servicio: str | None
    numero_oficio: str | None
    tipo: str | None


_KEYWORDS: tuple[tuple[str, float, str], ...] = (
    ("adquisicion", 0.20, "adquisición"),
    ("adquisicion tic", 0.25, "adquisición TIC"),
    ("contratacion", 0.18, "contratación"),
    ("requerimiento", 0.18, "requerimiento"),
    ("tdr", 0.22, "TDR"),
    ("terminos de referencia", 0.22, "términos de referencia"),
    ("indagacion de mercado", 0.24, "indagación de mercado"),
    ("cotizacion", 0.18, "cotización"),
    ("cuadro comparativo", 0.22, "cuadro comparativo"),
    ("certificacion presupuestal", 0.24, "certificación presupuestal"),
    ("siaf", 0.18, "SIAF"),
    ("orden de servicio", 0.24, "orden de servicio"),
    ("orden de compra", 0.24, "orden de compra"),
    ("ocs", 0.20, "OCS"),
    ("conformidad", 0.22, "conformidad"),
    ("otin", 0.12, "OTIN"),
    ("oeas", 0.10, "OEAS"),
    ("otpp", 0.10, "OTPP"),
    ("cmn", 0.12, "CMN"),
    ("siga", 0.12, "SIGA"),
)

_ETAPA_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("conformidad final", "E25"),
    ("requiere conformidad", "E23"),
    ("solicita conformidad", "E23"),
    ("conformidad tecnica", "E23"),
    ("inicio de servicio", "E22"),
    ("entrega del bien", "E22"),
    ("confirmacion recepcion", "E21"),
    ("confirmacion de recepcion", "E21"),
    ("notificacion al proveedor", "E20"),
    ("notificacion orden de servicio", "E20"),
    ("notificacion orden de compra", "E20"),
    ("orden de servicio", "E19"),
    ("orden de compra", "E19"),
    ("ocs", "E19"),
    ("certificacion presupuestal", "E16"),
    ("siaf", "E16"),
    ("secretaria general", "E14"),
    ("cuadro comparativo", "E09"),
    ("evaluacion tecnica", "E07"),
    ("observaciones", "E05"),
    ("indagacion de mercado", "E03"),
    ("cotizacion", "E03"),
    ("visto bueno", "E02b"),
    ("v°b°", "E02b"),
    ("tdr", "E02"),
    ("requerimiento", "E01a"),
)

_DOC_TIPO_BY_EXT: dict[str, str] = {
    ".pdf": "OFICIO",
    ".doc": "TDR",
    ".docx": "TDR",
    ".xls": "COTIZACION",
    ".xlsx": "COTIZACION",
    ".xlsm": "COTIZACION",
    ".jpg": "OTRO",
    ".jpeg": "OTRO",
    ".png": "OTRO",
    ".gif": "OTRO",
    ".tif": "OTRO",
    ".tiff": "OTRO",
    ".webp": "OTRO",
}

_CONTENT_TYPE_BY_EXT: dict[str, str] = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
    ".zip": "application/zip",
    ".rar": "application/vnd.rar",
    ".7z": "application/x-7z-compressed",
}


def probar_conexion(body: ExchangeCredencialesIn) -> ExchangeTestOut:
    account = _connect(body)
    try:
        total = int(account.inbox.total_count)
    except Exception as exc:
        raise _exchange_unavailable(exc) from exc
    return ExchangeTestOut(
        conectado=True,
        cuenta=str(getattr(account, "primary_smtp_address", body.email)),
        total_bandeja=total,
        mensaje="Conexión Exchange/EWS correcta.",
    )


def listar_carpetas(body: ExchangeCredencialesIn) -> ExchangeFoldersOut:
    account = _connect(body)
    try:
        folders = [
            ExchangeFolderOut(
                nombre=display,
                total=_safe_int(getattr(folder, "total_count", None)),
                no_leidos=_safe_int(getattr(folder, "unread_count", None)),
            )
            for display, folder in _known_folders(account)
            if folder is not None
        ]
    except Exception as exc:
        raise _exchange_unavailable(exc) from exc

    return ExchangeFoldersOut(items=folders, total=len(folders))


def sincronizar_exchange(db: Session, body: ExchangeSyncIn) -> ExchangeSyncOut:
    account = _connect(body)
    try:
        folder = _folder(account, body.carpeta)
        query = folder.all().order_by("-datetime_received")
        if body.solo_no_leidos:
            query = query.filter(is_read=False)

        fetch_limit = max(body.limite, 1) * (4 if body.remitente else 1)
        messages = list(query[:fetch_limit])
    except Exception as exc:
        raise _exchange_unavailable(exc) from exc
    if body.remitente:
        needle = body.remitente.lower().strip()
        messages = [
            msg for msg in messages
            if needle in _sender_email(msg).lower()
            or needle in str(getattr(msg, "sender", "") or "").lower()
        ][: body.limite]
    else:
        messages = messages[: body.limite]

    revisados = len(messages)
    candidatos = creados = duplicados = auto_vinculados = descartados = 0
    errores: list[str] = []

    procesos = db.execute(
        select(Proceso).where(Proceso.eliminado_en.is_(None))
    ).scalars().all()

    for msg in messages:
        try:
            subject = msg.subject or ""
            body_clean = _clean_body(msg)
            text = " ".join([subject, body_clean])
            candidate = _evaluar_candidato(text, procesos)
            if candidate.score < body.umbral_relevancia:
                descartados += 1
                continue

            candidatos += 1
            documentos = (
                _documentos_from_message(msg)
                if body.descargar_adjuntos and bool(getattr(msg, "has_attachments", False))
                else []
            )
            fecha_detectada = _extract_date(text) or _message_date(msg)
            payload = CorreoIngestaIn(
                entry_id=_entry_id(msg),
                subject=subject or None,
                sender_name=_sender_name(msg),
                sender_email=_sender_email(msg),
                received_at=_message_datetime(msg),
                body_clean=body_clean[:12000] if body_clean else None,
                nombre_servicio=candidate.nombre_servicio,
                nombre_servicio_normalizado=(
                    normalizar_nombre_servicio(candidate.nombre_servicio)
                    if candidate.nombre_servicio else None
                ),
                numero_oficio_raw=candidate.numero_oficio,
                numero_oficio=candidate.numero_oficio,
                tipo=candidate.tipo,  # type: ignore[arg-type]
                fecha_documento=fecha_detectada,
                fecha_recepcion=_message_date(msg),
                relevancia_score=candidate.score,
                relevancia_motivos="; ".join(candidate.motivos),
                proceso_sugerido_id=candidate.proceso_id,
                etapa_sugerida=candidate.etapa,
                fase_sugerida=candidate.fase,
                resumen_sugerido=_resumen_basico(subject, body_clean),
                documentos=documentos,
            )
            result = ingesta_service.ingestar_correo(db, payload)
            if result.status == "already_ingested":
                duplicados += 1
            elif result.status == "auto_linked":
                auto_vinculados += 1
                creados += 1
            else:
                creados += 1
        except Exception as exc:
            db.rollback()
            errores.append(f"{msg.subject or '(sin asunto)'}: {exc}")

    return ExchangeSyncOut(
        carpeta=body.carpeta,
        revisados=revisados,
        candidatos=candidatos,
        creados=creados,
        duplicados=duplicados,
        auto_vinculados=auto_vinculados,
        descartados=descartados,
        errores=errores[:10],
    )


def _connect(settings: ExchangeCredencialesIn):
    _prepare_proxy_env(settings.servidor)
    try:
        from exchangelib import (
            Account, BASIC, Build, Configuration, Credentials, DELEGATE,
            FailFast, NTLM, Version,
        )
        from exchangelib.errors import UnauthorizedError
    except ImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Falta la dependencia 'exchangelib'. Instalá backend/requirements.txt "
                "o ejecutá: pip install exchangelib"
            ),
        ) from exc

    version = Version(build=Build(14, 3, 123, 2))
    user = (settings.usuario or "").strip()
    domain = os.getenv("EWS_DOMAIN", "INEI").strip()
    # En este Exchange 2010 (IIS 7.5) la cuenta autentica por EWS con
    # Basic + DOMINIO\usuario; NTLM y el usuario pelado son rechazados (401),
    # aunque OWA (auth de formulario) sí los acepte. Probamos una cadena de
    # candidatos y nos quedamos con el primero que autentique de verdad.
    user_dom = user if ("\\" in user or "@" in user or not domain) else f"{domain}\\{user}"
    candidates: list[tuple[Any, str]] = []
    for uname in (user_dom, user):
        for auth in (BASIC, NTLM):
            if (auth, uname) not in candidates:
                candidates.append((auth, uname))

    last_exc: Exception | None = None
    for auth, uname in candidates:
        try:
            credentials = Credentials(username=uname, password=settings.password)
            config = Configuration(
                server=settings.servidor,
                credentials=credentials,
                auth_type=auth,
                version=version,
                retry_policy=FailFast(),
            )
            account = Account(
                primary_smtp_address=settings.email,
                config=config,
                autodiscover=False,
                access_type=DELEGATE,
            )
            # Forzar autenticación real (Account es perezoso): si las
            # credenciales/auth no sirven, esto lanza UnauthorizedError.
            _ = account.inbox.total_count
            return account
        except UnauthorizedError as exc:
            last_exc = exc
            continue
        except Exception as exc:
            raise _exchange_unavailable(exc) from exc

    raise _exchange_unavailable(
        last_exc or Exception("credenciales rechazadas por EWS")
    )


def _prepare_proxy_env(ews_server: str) -> None:
    """Normaliza proxies y evita proxy para el host EWS institucional.

    requests/exchangelib respetan HTTP(S)_PROXY y NO_PROXY. Si el servidor EWS
    es interno, pasar por proxy suele terminar en "Tunnel connection failed".
    """
    host = _host_from_server(ews_server)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        value = os.environ.get(key)
        if value and "://" not in value:
            os.environ[key] = f"http://{value}"

    no_proxy_values = []
    for key in ("NO_PROXY", "no_proxy"):
        current = os.environ.get(key, "")
        no_proxy_values.extend(
            item.strip() for item in current.split(",") if item.strip()
        )

    for value in (host, ".inei.gob.pe", "inei.gob.pe", "localhost", "127.0.0.1"):
        if value and value not in no_proxy_values:
            no_proxy_values.append(value)

    merged = ",".join(no_proxy_values)
    os.environ["NO_PROXY"] = merged
    os.environ["no_proxy"] = merged


def _host_from_server(ews_server: str) -> str:
    server = (ews_server or "").strip()
    server = re.sub(r"^https?://", "", server, flags=re.I)
    server = server.split("/", 1)[0]
    return server.split(":", 1)[0]


def _exchange_unavailable(exc: Exception) -> HTTPException:
    detail = (
        "No se pudo conectar a Exchange/EWS. Revisa proxy/NO_PROXY para el "
        "servidor EWS y confirma que el backend tenga salida a "
        "https://economicas2.inei.gob.pe/EWS/Exchange.asmx. "
        f"Detalle: {exc}"
    )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=detail,
    )


def _folder(account, folder_name: str):
    name = (folder_name or "Bandeja de entrada").lower().strip()
    folders = {
        "inbox": account.inbox,
        "bandeja de entrada": account.inbox,
        "drafts": account.drafts,
        "borradores": account.drafts,
        "sent": account.sent,
        "sent items": account.sent,
        "elementos enviados": account.sent,
        "trash": account.trash,
        "deleted": account.trash,
        "elementos eliminados": account.trash,
        "junk": _find_folder(account, "Correo no deseado") or getattr(account, "junk", None),
        "junk email": _find_folder(account, "Correo no deseado") or getattr(account, "junk", None),
        "correo no deseado": _find_folder(account, "Correo no deseado") or getattr(account, "junk", None),
    }
    if name in folders and folders[name] is not None:
        return folders[name]
    for folder in account.root.walk():
        if folder.name and folder.name.lower() == name:
            return folder
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=f"No se encontró la carpeta Exchange: {folder_name}",
    )


def _known_folders(account):
    """Devuelve solo las carpetas principales que se muestran en la UI."""
    return [
        ("Bandeja de entrada", account.inbox),
        ("Borradores", account.drafts),
        ("Elementos enviados", account.sent),
        ("Elementos eliminados", account.trash),
        ("Correo no deseado", _find_folder(account, "Correo no deseado") or getattr(account, "junk", None)),
    ]


def _find_folder(account, folder_name: str):
    target = folder_name.lower()
    try:
        for folder in account.root.walk():
            name = getattr(folder, "name", None)
            if name and str(name).lower() == target:
                return folder
    except Exception:
        return None
    return None


def _evaluar_candidato(text: str, procesos: list[Proceso]) -> _Candidate:
    normalized = _norm_text(text)
    motivos: list[str] = []
    score = 0.0

    for keyword, weight, label in _KEYWORDS:
        if keyword in normalized:
            score += weight
            motivos.append(label)

    oficio = _extract_oficio(text)
    if oficio:
        score += 0.18
        motivos.append(f"oficio {oficio}")

    if _extract_ocs(text):
        score += 0.18
        motivos.append("número OCS")

    if re.search(r"\bSIAF\b|\bsiaf\b", text):
        score += 0.12
        motivos.append("SIAF")

    etapa = _infer_etapa(normalized)
    fase = _fase_label(etapa) if etapa else None
    if etapa:
        score += 0.08
        motivos.append(f"etapa sugerida {etapa}")

    proceso_id, sim = _suggest_proceso(text, normalized, oficio, procesos)
    if proceso_id:
        score += 0.22 if sim >= 0.90 else 0.14
        motivos.append(f"proceso sugerido por similitud {sim:.0%}")

    tipo = _infer_tipo(normalized)
    nombre_servicio = _infer_nombre_servicio(text)
    score = min(score, 1.0)
    if not motivos:
        motivos.append("sin señales suficientes")

    return _Candidate(
        score=round(score, 3),
        motivos=motivos,
        etapa=etapa,
        fase=fase,
        proceso_id=proceso_id,
        nombre_servicio=nombre_servicio,
        numero_oficio=oficio,
        tipo=tipo,
    )


def _suggest_proceso(
    raw_text: str,
    normalized_text: str,
    oficio: str | None,
    procesos: list[Proceso],
) -> tuple[int | None, float]:
    for p in procesos:
        if p.numero_oficio and oficio and p.numero_oficio == oficio:
            return p.id, 1.0
        if p.id_proceso and p.id_proceso.lower() in raw_text.lower():
            return p.id, 1.0

    best_id: int | None = None
    best_ratio = 0.0
    comparable_text = normalizar_nombre_servicio(raw_text)
    for p in procesos:
        ratio = evaluar_similitud_nombre(
            comparable_text[:600],
            normalizar_nombre_servicio(p.requerimiento),
        )
        if ratio > best_ratio:
            best_id = p.id
            best_ratio = ratio

    if best_ratio >= 0.55:
        return best_id, best_ratio
    return None, best_ratio


def _norm_text(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text or "")
    sin_acentos = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sin_acentos.lower()).strip()


def _infer_etapa(normalized_text: str) -> str | None:
    if (
        ("orden de servicio" in normalized_text or "orden de compra" in normalized_text)
        and "notificacion" in normalized_text
    ):
        return "E21" if "confirmacion" in normalized_text else "E20"

    for keyword, etapa in _ETAPA_KEYWORDS:
        if keyword in normalized_text and etapa in ETAPAS_CATALOGO:
            return etapa
    return None


def _fase_label(etapa: str | None) -> str | None:
    if not etapa:
        return None
    fase_key = COD_A_FASE.get(etapa)
    if not fase_key:
        return None
    return str(FASES[fase_key]["label"])


def _infer_tipo(normalized_text: str) -> str | None:
    if re.search(r"\bbien(es)?\b", normalized_text):
        return "BIEN"
    if "servicio" in normalized_text or "suscripcion" in normalized_text:
        return "SERVICIO"
    return None


def _infer_nombre_servicio(text: str) -> str | None:
    subject = (text.splitlines()[0] if text else "").strip()
    subject = re.sub(r"^(rv|re|fw|fwd)\s*:\s*", "", subject, flags=re.I)
    return subject[:500] or None


def _extract_oficio(text: str) -> str | None:
    patterns = [
        r"(?:oficio\s*)?(?:n[°ºo.]?\s*)?([0-9]{1,6}\s*-\s*20[0-9]{2}[A-Za-z0-9/_.-]*)",
        r"(?:oficio\s*)?(?:n[°ºo.]?\s*)?([0-9]{3,6}[A-Za-z0-9/_.-]*)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return normalizar_oficio(match.group(1))
    return None


def _extract_ocs(text: str) -> str | None:
    match = re.search(r"\b(?:OCS|OS|OC)[-:\s]*([0-9]{3,}(?:-[0-9]{2,4})?)", text, flags=re.I)
    return match.group(0) if match else None


def _extract_date(text: str) -> date | None:
    patterns = (
        r"\b([0-3]?\d)[/-]([01]?\d)[/-](20\d{2})\b",
        r"\b(20\d{2})[/-]([01]?\d)[/-]([0-3]?\d)\b",
    )
    for idx, pattern in enumerate(patterns):
        match = re.search(pattern, text)
        if not match:
            continue
        try:
            if idx == 0:
                day, month, year = map(int, match.groups())
            else:
                year, month, day = map(int, match.groups())
            return date(year, month, day)
        except ValueError:
            continue
    return None


def _documentos_from_message(message) -> list[DocumentoExtraidoIn]:
    try:
        from exchangelib import FileAttachment
    except ImportError:
        return []

    documentos: list[DocumentoExtraidoIn] = []
    for attachment in getattr(message, "attachments", []) or []:
        if not isinstance(attachment, FileAttachment):
            continue
        filename = attachment.name or "archivo"
        data = attachment.content or b""
        if _is_inline_noise_attachment(attachment, filename, len(data)):
            continue
        ext = _suffix(filename)
        content_type = _safe_content_type(filename, getattr(attachment, "content_type", None))
        if content_type is None:
            continue
        documentos.append(
            DocumentoExtraidoIn(
                nombre_original=filename,
                content_type=content_type,
                tamano_bytes=len(data),
                contenido_b64=base64.b64encode(data).decode("ascii"),
                tipo_clasificado=_tipo_documento(filename),
                confianza=0.75,
            )
        )
    return documentos


def _is_inline_noise_attachment(attachment, filename: str, size: int) -> bool:
    """Filtra imagenes embebidas de firmas/logos que Exchange expone como adjuntos."""
    name = filename.lower().strip()
    content_id = str(getattr(attachment, "content_id", "") or "")
    is_inline = bool(getattr(attachment, "is_inline", False) or content_id)
    is_image = _suffix(filename) in {".png", ".jpg", ".jpeg", ".gif", ".webp"}
    looks_generated = bool(re.fullmatch(r"image\d{3,}\.(png|jpg|jpeg|gif|webp)", name))
    return is_image and (is_inline or looks_generated) and size <= 250_000


def _tipo_documento(filename: str) -> str:
    normalized = normalizar_nombre_servicio(filename)
    if "orden" in normalized or "ocs" in normalized:
        return "ORDEN_SERVICIO"
    if "conformidad" in normalized:
        return "CONFORMIDAD"
    if "cronograma" in normalized:
        return "CRONOGRAMA"
    if "siaf" in normalized or "certificacion" in normalized:
        return "SIAF"
    if "cotizacion" in normalized or "cuadro" in normalized:
        return "COTIZACION"
    if "tdr" in normalized or "terminos" in normalized:
        return "TDR"
    if "oficio" in normalized:
        return "OFICIO"
    return _DOC_TIPO_BY_EXT.get(_suffix(filename), "OTRO")


def _safe_content_type(filename: str, content_type: str | None) -> str | None:
    ext = _suffix(filename)
    if ext in _CONTENT_TYPE_BY_EXT:
        return _CONTENT_TYPE_BY_EXT[ext]
    guessed = mimetypes.guess_type(filename)[0]
    return guessed if guessed in set(_CONTENT_TYPE_BY_EXT.values()) else None


def _suffix(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot >= 0 else ""


def _clean_body(message) -> str:
    value = getattr(message, "text_body", None) or getattr(message, "body", None) or ""
    text = str(value)
    text = re.sub(r"<(script|style).*?</\1>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\bClean\s+false\b.*?\bMicrosoftInternetExplorer\d+\b", " ", text, flags=re.I)
    text = re.sub(r"\bClean\s+false\s+\d+\s*", " ", text, flags=re.I)
    text = re.sub(r"\bES-PE\s+X-NONE\s+X-NONE\b", " ", text, flags=re.I)
    text = re.sub(r"\b(?:false\s+){2,}", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _resumen_basico(subject: str, body_clean: str) -> str:
    text = body_clean.strip()
    if not text:
        return subject[:500]
    return text[:700]


def _entry_id(message) -> str:
    raw = str(getattr(message, "id", "") or "")
    digest = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()
    return f"ews:{digest}"


def _sender_email(message) -> str:
    sender = getattr(message, "sender", None)
    return str(getattr(sender, "email_address", None) or sender or "")


def _sender_name(message) -> str | None:
    sender = getattr(message, "sender", None)
    name = getattr(sender, "name", None)
    return str(name) if name else None


def _message_datetime(message) -> datetime | None:
    value: Any = getattr(message, "datetime_received", None) or getattr(message, "datetime_sent", None)
    return value if isinstance(value, datetime) else None


def _message_date(message) -> date | None:
    value = _message_datetime(message)
    return value.date() if value else None


def _safe_int(value) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None
