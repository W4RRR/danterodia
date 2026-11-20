# legacy/ — código original de Dante's Gates Minimal Version

Aquí está el código original de Quantika14 / Jorge Coronado, **solo como referencia**.
No se mantiene ni se ejecuta: muchas de sus dependencias y fuentes ya no existen
(ver `docs/AUDITORIA.md`). La herramienta nueva está en `src/osintsuite/` y se usa
con el comando `osint`.

Se han retirado las herramientas obsoletas (Python 2, servicios desaparecidos,
copias duplicadas). Todo lo retirado sigue disponible en el historial de git:

```bash
git show baseline:ruta/al/fichero   # o: git checkout cfc048a -- ruta
```

> ⚠️ Algunas funciones de este código (pruebas de login con contraseña `123456`,
> generador de spoofing de email) **no se han portado** a la nueva versión por
> motivos legales. Ver `docs/AUDITORIA.md` §7.
