# PRD — ActivoQR (Gestión de activos por QR + Solicitudes de mantenimiento)

## Problema original
App web responsive y sencilla en español para gestionar activos mediante códigos QR y solicitudes de mantenimiento. 4 módulos: Activos, Usuarios, Códigos QR, Solicitudes. Flujo: Activo → QR → Reporte → Solicitud → Estado → Seguimiento.

## Arquitectura
- Backend: FastAPI + MongoDB (motor). Todas las rutas con prefijo `/api`.
- Frontend: React 19 + React Router 7 + Tailwind + shadcn/ui + sonner.
- Auth: JWT en cookie httpOnly (secure, samesite=none), `withCredentials`. Sin auto-registro; los admins crean usuarios.
- QR: generado en backend con `qrcode` (PNG base64), codifica `{FRONTEND_URL}/a/{qr_token}` (token aleatorio, no IDs consecutivos).
- Fotos: object storage de Emergent (máx 2 por entidad), servidas vía `/api/files/{path}`.

## Personas
- ADMINISTRADOR: CRUD de activos, administra usuarios, ve y actualiza el estado de solicitudes.
- USUARIO: visualiza activos y solicitudes (sin crear/editar).
- Público (sin login): ve página del activo por QR, crea solicitud, consulta seguimiento por token.

## Requisitos core (estáticos)
- Activos: código único, nombre, descripción, categoría, ubicación, estado (Operativo/Con incidencia/En mantenimiento/Inactivo), fotos, fecha. Listado, búsqueda, filtros, crear, editar, detalle.
- QR por activo: ver, descargar, imprimir. Página pública responsive con código/nombre/ubicación/estado + REPORTAR PROBLEMA.
- Solicitudes: número correlativo SOL-000001, activo (bloqueado en form público), solicitante, email, descripción, foto, fecha, estado, historial. Estados: NUEVA/EN REVISIÓN/EN PROCESO/FINALIZADA. Link público de seguimiento por token.
- Dashboard: total activos + conteos por estado + tabla de últimas solicitudes.
- Seguridad: links públicos por token aleatorio; administración requiere auth.

## Implementado (2026-06-11)
- Auth JWT (login/logout/me) + seed de admin y 2 usuarios demo.
- Módulos Activos, Usuarios, Solicitudes, QR completos + páginas públicas (activo, reporte, seguimiento).
- Dashboard con métricas y tabla.
- Datos demo: 10 activos, 3 usuarios, 8 solicitudes con estados variados.
- Verificado por testing agent: backend 100% (17/17), frontend 100%. Role enforcement OK (403 backend + UI oculta).

## Backlog priorizado
- P1: Paginación en listados de activos/solicitudes.
- P2: Bloqueo por fuerza bruta en login; notificación por email al solicitante al cambiar estado.
- P2: Split de server.py en módulos si crece.

## Credenciales
Ver `/app/memory/test_credentials.md`.
