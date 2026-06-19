# Adquisiciones v2 — Backend

API del sistema de Adquisiciones TIC (OTIN / INEI). Índice documental por proceso, máquina de etapas, ingesta de correos e inteligencia de tiempos (lead-time / cuellos de botella).

## Stack
FastAPI · SQLAlchemy 2.0 · Alembic · Pydantic v2 · PostgreSQL · pytest

## Desarrollo
```bash
python -m venv .venv && . .venv/Scripts/activate
pip install -r requirements.txt
cp .env.example .env          # completar valores
alembic upgrade head          # migraciones
python -m app.seed            # usuario admin inicial
uvicorn app.main:app --reload
```

## Tests
```bash
pytest
```
Los tests corren contra una base **cuyo nombre termina en `_test`** (override con `TEST_DATABASE_URL`). El conftest se niega a correr contra una DB que no sea de test, para no tocar datos reales.

## Deploy (Railway)
- Agregar el plugin **PostgreSQL** → inyecta `DATABASE_URL`.
- Variables: `SECRET_KEY` fuerte, `ACCESS_TOKEN_EXPIRE_MINUTES`, etc.
- Ejecutar `alembic upgrade head` en el release.
- Montar un **Volume** para `UPLOAD_DIR` (el filesystem de Railway es efímero).

> La ingesta de correos (lectura de Outlook + IA) corre en un repo aparte (`Adquisiciones-v2-ingesta`), en una PC local. El backend solo recibe el JSON ya extraído.
