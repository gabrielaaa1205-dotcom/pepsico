# CIANSE Kardex — Importar correos con GitHub Actions

Esta carpeta actualiza el proyecto existente `pepsico`. No crear otro servicio Render ni contratar un Cron Job.

## 1. Subir al mismo repositorio
Subir **el contenido de esta carpeta** a la misma ubicación que `server.py` y `requirements.txt` en el repositorio privado. Es imprescindible preservar `.github/workflows/kardex-correo.yml` (la carpeta `.github` puede estar oculta en Windows). Si el proyecto original tiene `server.py` en una subcarpeta, ajustar el workflow a esa ubicación antes de activar.

## 2. Configurar dos secretos en GitHub
En el repositorio: Settings > Secrets and variables > Actions > New repository secret.

- `CIANSE_MAIL_PASSWORD`: contraseña del buzón DreamHost de `jesus.gutierrez@cianse.com`. No escribir en GitHub como archivo ni en un chat.
- `CIANSE_EXTERNAL_DATABASE_URL`: **External Database URL** de la base `cianse-kardex-db` en Render (panel PostgreSQL > Connect). No usar `Internal Database URL` porque el runner de GitHub está fuera de Render. Confirmar que Render permite conexiones externas y usar TLS (preferir `sslmode=require` si la URL no lo incluye; respetar la documentación de Render).

**La aplicación web `pepsico` en Render debe seguir usando su `DATABASE_URL` interno**; no reemplazar ese valor. Si esas claves estuvieron expuestas, rotarlas.

## 3. Ejecutar primera prueba
En GitHub: Actions > CIANSE - Importar kardex desde DreamHost > Run workflow. Abrir el log del job `importar`. Antes, respaldar la base y confirmar que los archivos históricos en el correo no producen importaciones no deseadas.

## 4. Ejecución programada
Todos los días a las 12:00 UTC (07:00 Perú). GitHub Actions puede retrasar o, excepcionalmente, omitir una ejecución programada. Se puede activar manualmente en Actions.

## 5. Validaciones y límites
- Solo archivos `.xlsx` del remitente exacto `laura.guzman@cianse.net`.
- No borra correos ni reemplaza automáticamente los registros que no coinciden.
- Detecta duplicados por hash del Excel y detecta diferencias por fecha o guía; revisarlas en `/admin/mail-imports`.
- El script busca correos de los últimos 14 días y hasta 200 mensajes por ejecución.
- El importador fue preparado con formatos de prueba: una importación real debe verificarse en PostgreSQL y en el tablero.
- En la versión actual algunas diferencias pueden coexistir con registros nuevos válidos del mismo Excel; es importante revisar el historial.
- Antes de habilitar importación automática en producción, guardar un respaldo de PostgreSQL y realizar una primera ejecución supervisada.

## Costos
GitHub Actions suele tener cuota de minutos incluida; la disponibilidad depende del plan y consumo del repositorio. La base PostgreSQL y alojamiento Render mantienen sus propias condiciones y costos.
