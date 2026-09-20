# Publicación

## GitHub Pages
La web estática se publica mediante GitHub Actions desde la carpeta site/.

## Backend
Render puede ejecutar:
- Build: pip install -r requirements.txt
- Start: uvicorn main:app --host 0.0.0.0 --port $PORT

## Producción
La versión beta usa SQLite. En un servicio sin disco persistente, el historial local no es permanente. Para producción hay que migrar la evidencia a PostgreSQL o almacenamiento persistente.
