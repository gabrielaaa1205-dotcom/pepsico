# Actualización automática desde DreamHost — CIANSE Kardex

## Qué hace
* Un **Render Cron Job** separado consulta diariamente el buzón `jesus.gutierrez@cianse.com` mediante IMAP SSL, puerto 993.
* Solo procesa Excel `.xlsx` del remitente exacto `laura.guzman@cianse.net`.
* Reconoce las hojas de movimientos, selección y stock del formato actual. Añade registros nuevos; no elimina registros previos. Si un día o guía ya existe con valores distintos, preserva el dato anterior y registra una discrepancia para revisión.
* Controla duplicados por SHA-256 del adjunto y por clave de movimiento; deja trazabilidad en `mail_ingestions` y auditoría en `audit`.
* **No modifica ni borra mensajes del buzón.**

## Publicación
1. Actualizar el repositorio privado de GitHub que usa el Web Service `pepsico` con este paquete (archivos `server.py`, `mail_sync.py`, `mail_import.py`, `templates/`, etc.). El Web Service sigue usando el mismo Build/Start Command. **No crear otro Web Service.**
2. En Render, crear **New > Cron Job**, conectar **el mismo repositorio y rama**. Si el proyecto guarda los archivos en una subcarpeta, indicar esa carpeta como Root Directory; si están en raíz, dejarla vacía.
3. Build Command: `pip install -r requirements.txt`
4. Command: `python mail_sync.py`
5. Schedule (UTC, ejemplo cada mañana a las 7:00 a. m. de Perú = 12:00 UTC): `0 12 * * *`. Ajustar si Laura suele enviar después. Render cobra/limita según el plan elegido.
6. Agregar **en el Cron Job** variables seguras:
   - `DATABASE_URL`: **la Internal Database URL** de la **misma** PostgreSQL que usa el Web Service `pepsico`.
   - `MAIL_USERNAME`: `jesus.gutierrez@cianse.com`
   - `MAIL_PASSWORD`: contraseña del buzón (definir como secret directamente en Render, **no escribirla en GitHub ni enviarla por chat**).
   - `MAIL_IMAP_HOST`: `imap.dreamhost.com`
   - `MAIL_IMAP_PORT`: `993`
   - `MAIL_ALLOWED_SENDER`: `laura.guzman@cianse.net`
   - `MAIL_LOOKBACK_DAYS`: `14`
7. Ejecutar una primera prueba con **Run Now** en el Cron Job y revisar Logs, el tablero y `mail_ingestions` antes de utilizarlo sin supervisión. Conservar una copia de PostgreSQL y los Excel aprobados.

## Consideraciones
- IMAP accede al correo receptor; se necesita su contraseña en **Render** con acceso restringido. Si DreamHost permite una credencial exclusiva para IMAP, preferirla. Si una clave quedó expuesta anteriormente, rotarla.
- **No asumir que los Excel de Laura siempre tienen el formato ya probado.** Un adjunto con formato nuevo se rechaza y se registra el error.
- Las colisiones por fecha en producción/stock se registran, pero no se reescribe un registro anterior. Si Laura envía un Excel corregido, un responsable debe conciliar las diferencias.
- El Web Service usa almacenamiento `kx-data` como bloque JSON. El Cron Job preserva las secciones y eleva la revisión, pero **aún no es una base de datos transaccional por cada guía**. Evita editar manualmente mientras se ejecuta la importación, hasta completar pruebas en Render.
- La app web lee su estado al iniciar sesión; recarga la página para ver las novedades del cron.
- La actualización inicial de grandes historiales y libros con hojas incompletas debe validarse manualmente. Para producción definitiva se recomienda transacciones por guía y alertas por email para fallos.
