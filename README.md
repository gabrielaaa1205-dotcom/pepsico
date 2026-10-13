# CIANSE Kardex — despliegue con cuentas y PostgreSQL

## Qué incluye
- Interfaz original y cálculos de tarifas: buenas S/0.143, rechazo S/0.093, merma S/0.070.
- Base de datos PostgreSQL para configuraciones, cargas de datos y ajustes.
- Acceso HTTPS servido por Render, contraseñas almacenadas como hash, roles, protección CSRF y registro de escrituras.
- Protección frente a sobrescritura entre usuarios: ante conflicto, bloquea las escrituras y exige recargar.
- Panel del administrador: `/admin/users` para crear usuarios y cambiar claves.

## Publicar en Render
1. Crea un repositorio **privado** en GitHub. Sube el contenido de esta carpeta a la raíz, **no subas Excel privados al repositorio**.
2. En https://dashboard.render.com entra en **New > Blueprint** y conecta ese repositorio (Render leerá `render.yaml`).
3. Configura `INITIAL_ADMIN_USERNAME` con un usuario de tu elección y `INITIAL_ADMIN_PASSWORD` con una clave larga y única (mínimo 12 caracteres). No los compartas por chat ni los subas al repositorio. 
4. Verifica y acepta el precio mostrado por Render: Blueprint solicita servicio web y PostgreSQL de pago. Entra a la URL HTTPS que genere Render.
5. Inicia sesión solo una vez con el usuario del servidor. El segundo login ha sido retirado. Si la base central está vacía, importa los kardex Excel originales desde el panel.
6. En `/admin/users` crea las cuentas de tu personal. Consulta la guía de respaldo de PostgreSQL de Render antes de operar.
7. Carga y revisa datos en un entorno de prueba antes de usarlo en producción. Conserva los originales aprobados.

## Importante antes del uso operativo
- Este es un **prototipo desplegable para validación**, no una plataforma totalmente auditada o certificada.
- El guardado central usa un JSON por sección (`kx-data`, `kx-cfg`, `kx-edits`). Cuando alguien importa un kardex completo, reemplaza el conjunto de datos central, sujeto a control de versión. No hay edición transaccional individual de guías todavía.
- Si otra persona modificó una sección, la escritura falla, se advierte y se bloquean escrituras hasta recargar. No sustituye un flujo multiusuario por movimientos.
- El rol `consulta` no puede guardar, `operario` no cambia las tarifas y `admin` administra usuarios. Aún no existe un rol diferenciado por cada transacción.
- El snapshot histórico cifrado no está incluido en esta versión porque requería la clave anterior. Los registros que ya están en PostgreSQL permanecen intactos; para recuperar registros que solo existían en el snapshot, importa los Excel originales aprobados. Conserva la versión ZIP anterior como respaldo.
- El servidor requiere PostgreSQL y no tiene modo de producción sin base de datos.
- Verifica las facturas de Render y configura copias de seguridad y restauración.

## Comprobar localmente (requiere PostgreSQL)
`export DATABASE_URL='postgresql://...'`
`export INITIAL_ADMIN_USERNAME='admin_cianse'`
`export INITIAL_ADMIN_PASSWORD='<clave_segura_de_12_o_mas_caracteres>'`
`pip install -r requirements.txt`
`python server.py`
Visita `http://localhost:8000`.

## Actualización del servicio existente (pepsico)
Reemplaza los archivos del repositorio ya conectado a Render, conserva `DATABASE_URL` y los usuarios existentes, y despliega. No necesitas crear otra base de datos. Para comprobar que está aplicado, la aplicación debe abrir directamente el tablero después de iniciar sesión en `/login`.
