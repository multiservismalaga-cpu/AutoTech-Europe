# AutoTech Europe

Aplicación web para identificación de vehículos y localización de evidencia técnica pública, con trazabilidad de fuentes.

## Principios
- La identidad del vehículo y la evidencia técnica están separadas.
- No se presentan datos de terceros como documentación oficial.
- Los resultados de búsqueda web se marcan como **PENDIENTES DE CONTRASTE**.
- La base de modelos usa VehiclesDB y conserva su atribución CC BY 4.0.

## Ejecutar
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8000

## Publicación
El backend puede desplegarse en Render usando render.yaml. GitHub Pages se usa para la web pública estática.

## Limitación actual
Esta versión es una beta técnica: todavía no sustituye documentación OEM ni sistemas profesionales de información de reparación. La persistencia pública debe migrarse de SQLite local a PostgreSQL o almacenamiento persistente antes de considerarla producción.

© 2026 MZ · AutoTech Europe. Todos los derechos reservados.
