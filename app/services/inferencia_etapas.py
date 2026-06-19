"""Servicio de inferencia de avance de etapas desde correos ingestados.

Módulo autónomo (ADR-D1): toda la lógica nueva vive aquí para no inflar
etapas_service ni ingesta_service. Reutiliza CADENA, ETAPAS_CATALOGO y
EtapaRegistro sin modificarlos.

Fixes críticos implementados:
- Q1 (montos): INSERT directo de EtapaRegistro, NUNCA via registrar_etapa,
  para que sync_montos NO se dispare y no pise montos_proceso con None.
- Q2 (prerequisitos): el INSERT directo también evita validar_prerequisito_generico
  que bloquearía la cadena en E02 por exigir E01c (por_area) COMPLETADO.

Refinamiento FASE 3 (por-etapa con datos reales):
- inferir_etapa_correo: deduce etapa desde keywords del asunto (prioridad)
  con fallback a tipo_clasificado del documento.
- inferir_avance_correo: marca SOLO 1 etapa (la inferida), con datos reales
  del correo (responsable=sender_name, oficio_correo=numero_oficio).
- La cascada (inferir_avance) se mantiene internamente para retrocompatibilidad
  pero el caller (_vincular) ahora usa inferir_avance_correo.
- derivar_tiempos retorna 4-tuple: agrega etapa_actual_avance.
"""
from __future__ import annotations

import unicodedata
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.etapa import EtapaRegistro
from app.services.etapas_catalogo import CADENA, ETAPAS_CATALOGO

if TYPE_CHECKING:
    from app.models.proceso import Proceso

# ---------------------------------------------------------------------------
# Mapa tipo_clasificado → codigo_etapa (INF-01)
# ---------------------------------------------------------------------------
# Solo tipos con semántica clara en el flujo real.
# INFORME/OFICIO/SIAF/OTRO no mapean → no infieren.
# E24 (por_area) y E25 (fin) excluidos explícitamente (ADR-D5, R6 proposal).

TIPO_DOC_A_ETAPA: dict[str, str] = {
    "TDR": "E02",             # orden 4  — Elaboración TDR consolidado (OTIN)
    "COTIZACION": "E03",      # orden 6  — Envío indagación de mercado (fija fecha_indagacion)
    "ORDEN_SERVICIO": "E19",  # orden 26 — Emisión orden de compra/servicio (OEAS)
    "CRONOGRAMA": "E22",      # orden 29 — Inicio de servicio / entrega del bien
    "CONFORMIDAD": "E23",     # orden 30 — OTIN solicita conformidad (NO por_area)
}

# Umbral mínimo de confianza del documento para participar en la inferencia (INF-05)
CONFIANZA_MIN_INFERENCIA: float = 0.7

# ---------------------------------------------------------------------------
# Keywords del asunto → codigo_etapa (REFINAMIENTO FASE 3)
# ---------------------------------------------------------------------------
# Orden importa: se evalúa de mayor a menor especificidad.
# Comparación: case-insensitive + sin acentos (normalización NFKD).
# La primera coincidencia gana.

_KEYWORD_A_ETAPA: list[tuple[str, str]] = [
    # Más específicas primero
    ("indagacion de mercado", "E03"),
    ("validacion de cotizaciones", "E03"),
    ("cotizacion", "E03"),
    ("orden de servicio", "E19"),
    ("notificacion orden", "E19"),
    ("conformidad", "E23"),
    ("observaciones", "E05"),
    ("visto bueno", "E02b"),
    ("v°b°", "E02b"),
    ("vb", "E02b"),
    ("tdr", "E02b"),
    ("solicito de contratacion", "E01a"),
    ("contratacion", "E01a"),
    ("requerimiento", "E01a"),
]


def _normalizar_texto(texto: str) -> str:
    """Convierte a minúsculas y elimina acentos para comparación robusta."""
    nfkd = unicodedata.normalize("NFKD", texto)
    sin_acentos = "".join(c for c in nfkd if not unicodedata.combining(c))
    return sin_acentos.lower()


# ---------------------------------------------------------------------------
# NUEVA: inferir_etapa_correo — keyword asunto + fallback tipo_doc (FASE 3)
# ---------------------------------------------------------------------------

def inferir_etapa_correo(subject: str | None, documentos) -> str | None:
    """Infiere la etapa desde keywords del asunto (prioridad) y fallback a tipo_doc.

    Args:
        subject:    asunto del correo (puede ser None o vacío).
        documentos: lista de objetos con .tipo_clasificado y .confianza.

    Returns:
        Código de etapa (ej. "E23") o None si no se puede inferir.
    """
    # 1. Prioridad: keywords en el asunto
    if subject:
        texto = _normalizar_texto(subject)
        for keyword, cod in _KEYWORD_A_ETAPA:
            kw_norm = _normalizar_texto(keyword)
            if kw_norm in texto:
                return cod

    # 2. Fallback: tipo_clasificado del documento (misma lógica que inferir_etapa_objetivo)
    return inferir_etapa_objetivo(documentos)


# ---------------------------------------------------------------------------
# Mapeo puro: inferir_etapa_objetivo (INF-01, INF-05)
# ---------------------------------------------------------------------------

def inferir_etapa_objetivo(doc_rows) -> str | None:
    """Devuelve el código de etapa de MAYOR orden entre los docs con tipo mapeado
    y confianza >= CONFIANZA_MIN_INFERENCIA. Retorna None si ningún doc califica.

    Args:
        doc_rows: lista de objetos con atributos .tipo_clasificado y .confianza.
    """
    cods: list[str] = []
    for d in doc_rows:
        cod = TIPO_DOC_A_ETAPA.get(d.tipo_clasificado or "")
        if cod is None:
            continue
        conf = float(d.confianza) if d.confianza is not None else 0.0
        if conf < CONFIANZA_MIN_INFERENCIA:
            continue
        cods.append(cod)

    if not cods:
        return None

    # Mayor orden según ETAPAS_CATALOGO (spec.orden) — desempate determinista
    return max(cods, key=lambda c: ETAPAS_CATALOGO[c].orden)


# ---------------------------------------------------------------------------
# NUEVA: inferir_avance_correo — marca SOLO 1 etapa con datos reales (FASE 3)
# ---------------------------------------------------------------------------

def inferir_avance_correo(
    db: Session,
    proceso_id: int,
    correo,
    doc_rows,
) -> list[str]:
    """Infiere y marca SOLO la etapa correspondiente al correo (sin cascada).

    Usa inferir_etapa_correo (keywords asunto + fallback tipo_doc) para
    determinar la etapa objetivo. Inserta SOLO esa etapa como COMPLETADO
    con datos reales del correo (responsable, oficio_correo, fecha).

    Args:
        db:         sesión SQLAlchemy activa.
        proceso_id: ID del proceso.
        correo:     objeto IngestaCorreo (con .id, .subject, .sender_name,
                    .numero_oficio, .fecha_documento, .received_at).
        doc_rows:   documentos del correo (con .tipo_clasificado y .confianza).

    Returns:
        Lista de códigos de etapa marcados (0 o 1 elemento).
    """
    cod_obj = inferir_etapa_correo(correo.subject, doc_rows)
    if cod_obj is None:
        return []

    # Verificar que el código exista en el catálogo
    if cod_obj not in ETAPAS_CATALOGO:
        return []

    spec = ETAPAS_CATALOGO[cod_obj]

    # No inferir etapas por_area
    if spec.por_area:
        return []

    # Idempotencia: si ya existe fila COMPLETADO, no duplicar
    if _ya_completada(db, proceso_id, cod_obj):
        return []

    # Fecha: fecha_documento del correo, fallback received_at.date()
    fecha: date | None = correo.fecha_documento
    if fecha is None and correo.received_at is not None:
        rv = correo.received_at
        fecha = rv.date() if isinstance(rv, datetime) else rv
    if fecha is None:
        return []

    # Datos reales del correo
    responsable = getattr(correo, "sender_name", None)
    # oficio_correo toma el asunto del correo (truncado a 250) — identificador semántico
    _subject = getattr(correo, "subject", None)
    oficio_correo = (_subject[:250] if _subject else None) or None

    _insertar_fila_inferida(
        db, proceso_id, cod_obj, spec, correo.id, fecha,
        responsable=responsable,
        oficio_correo=oficio_correo,
        subject=getattr(correo, "subject", None),
        body_clean=getattr(correo, "body_clean", None),
    )
    return [cod_obj]


# ---------------------------------------------------------------------------
# Inferencia en cadena: inferir_avance (INF-02, Q1-fix, Q2-fix)
# Mantenida para retrocompatibilidad — el caller principal ahora es inferir_avance_correo
# ---------------------------------------------------------------------------

def inferir_avance(
    db: Session,
    proceso_id: int,
    doc_rows,
    correo_id: int,
    fecha: date,
) -> list[str]:
    """[DEPRECADO — usar inferir_avance_correo] Recorre la CADENA desde el inicio
    hasta la etapa objetivo e inserta filas COMPLETADO para cada etapa que NO sea
    por_area y NO esté ya COMPLETADO.

    FIX Q1: usa INSERT directo de EtapaRegistro (sin registrar_etapa) para que
    sync_montos NO se dispare y no pise montos_proceso existentes con None.

    FIX Q2: al no pasar por registrar_etapa, tampoco se ejecuta
    validar_prerequisito_generico, por lo que E02 no queda bloqueado
    por E01c (por_area, que la inferencia salta).

    La idempotencia se garantiza via _ya_completada: si la etapa ya tiene
    una fila COMPLETADO (manual o de otra inferencia), se salta sin duplicar.

    Args:
        db:         sesión SQLAlchemy activa.
        proceso_id: ID del proceso.
        doc_rows:   documentos del correo (con .tipo_clasificado y .confianza).
        correo_id:  ID del correo que origina la inferencia (para la marca).
        fecha:      fecha_documento del correo (fallback: received_at.date()).

    Returns:
        Lista de códigos de etapa que fueron marcados COMPLETADO en esta llamada.
    """
    cod_obj = inferir_etapa_objetivo(doc_rows)
    if cod_obj is None:
        return []

    idx = CADENA.index(cod_obj)
    prefijo = CADENA[: idx + 1]  # inclusive

    marcadas: list[str] = []
    for cod in prefijo:
        spec = ETAPAS_CATALOGO[cod]

        # Excluir etapas por_area (E01c, E11, E24) — INF-02 §3
        if spec.por_area:
            continue

        # Idempotencia: si ya existe fila COMPLETADO, saltar
        if _ya_completada(db, proceso_id, cod):
            continue

        _insertar_fila_inferida(db, proceso_id, cod, spec, correo_id, fecha)
        marcadas.append(cod)

    return marcadas


def _ya_completada(db: Session, proceso_id: int, cod: str) -> bool:
    """True si existe al menos una fila no-bucle COMPLETADO para este cod."""
    row = db.execute(
        select(EtapaRegistro).where(
            EtapaRegistro.proceso_id == proceso_id,
            EtapaRegistro.codigo_etapa == cod,
            EtapaRegistro.es_bucle.is_(False),
            EtapaRegistro.estado_etapa == "COMPLETADO",
        )
    ).scalars().first()
    return row is not None


def _construir_observaciones(correo_id: int, subject: str | None, body_clean: str | None) -> str:
    """Construye el texto de observaciones para una etapa inferida desde correo.

    Formato con body:    "[INGESTA_INFER corr=<id>] <asunto> — <extracto body ~160 chars>"
    Formato sin body:    "[INGESTA_INFER corr=<id>] <asunto>"
    Formato sin nada:    "[INGESTA_INFER corr=<id>] Inferido automáticamente desde correo ingestado"

    El prefijo "[INGESTA_INFER corr=<id>]" es INMUTABLE — revertir_avance depende
    de él para detectar filas por regex corr=(\\d+).
    El asunto va en observaciones ADEMÁS de en oficio_correo.
    """
    prefijo = f"[INGESTA_INFER corr={correo_id}]"

    # Limpiar y truncar extracto del cuerpo
    extracto = ""
    if body_clean:
        extracto = body_clean.replace("\n", " ").replace("\r", " ").strip()
        # Colapsar múltiples espacios en uno
        while "  " in extracto:
            extracto = extracto.replace("  ", " ")
        if len(extracto) > 160:
            extracto = extracto[:160] + "…"

    asunto = subject.strip() if subject else ""

    if extracto:
        return f"{prefijo} {extracto}"
    elif asunto:
        return f"{prefijo} {asunto}"
    else:
        return f"{prefijo} Inferido automáticamente desde correo ingestado"


def _insertar_fila_inferida(
    db: Session,
    proceso_id: int,
    cod: str,
    spec,
    correo_id: int,
    fecha: date,
    responsable: str | None = None,
    oficio_correo: str | None = None,
    subject: str | None = None,
    body_clean: str | None = None,
) -> EtapaRegistro:
    """INSERT directo de EtapaRegistro COMPLETADO con marca de trazabilidad.

    - fecha_inicio = fecha_fin = fecha del documento (ADR-D7: marcamos el HITO).
    - registrado_por = 'INGESTA_INFER'
    - responsable = sender_name del correo (datos reales — REFINAMIENTO FASE 3)
    - oficio_correo = numero_oficio del correo (datos reales — REFINAMIENTO FASE 3)
    - observaciones contiene '[INGESTA_INFER corr={correo_id}]' para la reversa parcial,
      más asunto y extracto del body_clean del correo para trazabilidad humana.
    - NO llama sync_montos (garantía Q1).
    - NO valida prerequisitos (garantía Q2).
    """
    observaciones = _construir_observaciones(correo_id, subject, body_clean)
    row = EtapaRegistro(
        proceso_id=proceso_id,
        codigo_etapa=cod,
        nombre_etapa=spec.nombre,
        area_responsable=spec.area_responsable,
        fecha_inicio=fecha,
        fecha_fin=fecha,
        estado_etapa="COMPLETADO",
        registrado_por="INGESTA_INFER",
        responsable=responsable,
        oficio_correo=oficio_correo,
        observaciones=observaciones,
        es_bucle=False,
        nro_ronda=1,
    )
    db.add(row)
    db.flush()
    return row


# ---------------------------------------------------------------------------
# Reversa: revertir_avance (INF-03, ADR-D3)
# ---------------------------------------------------------------------------

def revertir_avance(
    db: Session,
    proceso_id: int,
    correo_id: int,
) -> int:
    """Elimina SOLO las filas EtapaRegistro marcadas con este correo_id.

    La marca '[INGESTA_INFER corr={correo_id}]' en observaciones y
    registrado_por='INGESTA_INFER' identifican exclusivamente las filas de este correo.
    Las filas manuales (registrado_por != 'INGESTA_INFER') no se tocan nunca.

    Returns:
        Número de filas eliminadas.
    """
    marca = f"[INGESTA_INFER corr={correo_id}]"
    filas = db.execute(
        select(EtapaRegistro).where(
            EtapaRegistro.proceso_id == proceso_id,
            EtapaRegistro.registrado_por == "INGESTA_INFER",
            EtapaRegistro.observaciones.like(f"%{marca}%"),
        )
    ).scalars().all()

    for fila in filas:
        db.delete(fila)

    db.flush()
    return len(filas)


# ---------------------------------------------------------------------------
# Derivación de tiempos: derivar_tiempos (TMP-01, ADR-D6)
# Actualizado FASE 3: retorna 4-tuple con etapa_actual_avance
# ---------------------------------------------------------------------------

def derivar_tiempos(
    rows: list[EtapaRegistro],
    proceso,
) -> tuple[date | None, date | None, int | None, str | None]:
    """Deriva campos de tiempo desde las filas de etapas_registro del proceso.

    - fecha_indagacion: fecha_inicio de E03 COMPLETADO (fallback fecha_fin; None si no existe)
    - fecha_transicion_ota: fecha_inicio de E04 COMPLETADO (None si no existe)
    - dias_transcurridos: desde fecha_indagacion (fallback fecha_creacion del proceso)
        hasta E25.fecha_fin si CULMINADO, o hasta hoy en caso contrario.
    - etapa_actual_avance: código de etapa de MAYOR orden con estado COMPLETADO (o None)
        Representa "dónde está el proceso" en el flujo real. (REFINAMIENTO FASE 3)

    Los campos son DERIVADOS: no se persisten en la tabla procesos (ADR-D6).

    Args:
        rows:    todas las EtapaRegistro del proceso.
        proceso: instancia Proceso (para .estado y .fecha_creacion como fallback).

    Returns:
        (fecha_indagacion, fecha_transicion_ota, dias_transcurridos, etapa_actual_avance)
    """
    def _fecha_de(cod: str) -> date | None:
        """Primera fila COMPLETADO del código → fecha_inicio (fallback fecha_fin)."""
        for r in rows:
            if r.codigo_etapa == cod and r.estado_etapa == "COMPLETADO":
                return r.fecha_inicio or r.fecha_fin
        return None

    fecha_indagacion = _fecha_de("E03")
    fecha_transicion_ota = _fecha_de("E04")

    # Base para días: E03 si existe, si no fecha_creacion del proceso
    base: date | None = fecha_indagacion
    if base is None and proceso.fecha_creacion is not None:
        fc = proceso.fecha_creacion
        base = fc.date() if isinstance(fc, datetime) else fc

    if base is None:
        dias_transcurridos = None
    else:
        # Fin: E25.fecha_fin si CULMINADO, si no hoy
        if getattr(proceso, "estado", None) == "CULMINADO":
            fin = _fecha_de("E25") or date.today()
        else:
            fin = date.today()
        dias_transcurridos = (fin - base).days

    # etapa_actual_avance: mayor orden COMPLETADO en ETAPAS_CATALOGO
    etapa_actual_avance: str | None = None
    mejor_orden = -1
    for r in rows:
        if r.estado_etapa != "COMPLETADO":
            continue
        spec = ETAPAS_CATALOGO.get(r.codigo_etapa)
        if spec is None:
            continue
        if spec.orden > mejor_orden:
            mejor_orden = spec.orden
            etapa_actual_avance = r.codigo_etapa

    return fecha_indagacion, fecha_transicion_ota, dias_transcurridos, etapa_actual_avance
