---
schedule: every 5m
---

# AutoTech Europe — desarrollo técnico seguro

## Goal

Mejorar AutoTech Europe de forma incremental y verificable, priorizando la resolución correcta de variantes técnicas, la trazabilidad de evidencia y fuentes, la cobertura de pruebas y la separación estricta entre vehículos y variantes. Cada iteración debe producir un avance técnico real, pequeño y reversible.

## Target

Se pueden modificar únicamente:
- `main.py`
- `app.js`
- `tests/**`
- `sources/**`
- `README.md`

No modificar:
- `.github/workflows/**`
- configuración de despliegue
- secretos
- base de datos generada en producción
- otras rutas fuera de la lista anterior

## Evaluation

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
```

La evaluación debe terminar correctamente. El agente debe añadir o fortalecer pruebas significativas cuando corresponda. **Higher is better** para el número de casos de prueba descubiertos, pero nunca se considera mejora añadir pruebas triviales, duplicadas o que no cubran comportamiento real. Los cambios de código solo se aceptan si mantienen toda la suite en verde y aportan una mejora técnica demostrable.
