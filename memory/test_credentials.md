# Test Credentials — Overall Tracking

## Admin (administrador)
- Email: carlos.mesia.22@gmail.com
- Password: Admin123!
- Role: admin  (puede actualizar el estado de las solicitudes y gestionar activos)

## Demo Admin
- Email: maria.admin@empresa.com
- Password: Demo123!
- Role: admin

## Cliente (demo)
- Email: cliente@demo.com
- Password: Cliente123!
- Role: cliente  (crea solicitudes y ve solo las suyas)

## Auth endpoints
- POST /api/auth/register  {name,email,password}  -> crea rol "cliente", devuelve access_token
- POST /api/auth/login     {email,password}        -> devuelve access_token (Bearer)
- POST /api/auth/logout
- GET  /api/auth/me

## Notas
- Auth JWT por header Authorization: Bearer (token en localStorage 'ot_token').
- Solicitudes de servicio: POST /api/requests (autenticado). Cliente ve solo owner/propias; admin ve todas.
- PATCH /api/requests/{id}/status  -> solo admin (8 estados de flujo).
- Rutas públicas (sin login): /a/{qr_token}, reporte QR, /seguimiento/{track_token}.
