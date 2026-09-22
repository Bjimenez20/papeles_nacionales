# Gantt System - PRD

## Problem Statement
Sistema para registrar actividades de Gantt con duraciones y predecesores (como en la página 1 del Excel), más registro de incidencias con días de retraso que generan un Gantt modificado (como en la página 2).

## Architecture
- **Backend**: FastAPI + MongoDB (Motor), routes under `/api`
- **Frontend**: React 19 + React Router + Tailwind + shadcn/ui
- **Auth**: None (open system)
- **Scheduling**: FS dependencies, start = max(pred.end) + 1 day, end = start + duration - 1
- **Delay propagation**: Incidents sum per activity; modified schedule cascades to successors

## User Personas
- Project Managers que gestionan cronogramas con dependencias
- Coordinadores que documentan incidencias y necesitan ver impacto en el plan

## Core Requirements (static)
1. CRUD de Proyectos (multi-proyecto)
2. CRUD de Actividades con múltiples predecesores
3. CRUD de Incidencias con días de retraso
4. Cálculo automático de fechas original + modificado
5. Visualización Gantt: Original, Modificado, Comparación
6. Dashboard con estadísticas (actividades, incidencias, retraso, duración)

## Implemented (2026-02-06)
- Backend endpoints: /api/projects, /api/activities, /api/incidents, /api/projects/{id}/gantt
- Topological sort + FS scheduling with delay cascade
- Frontend: Dashboard (proyectos), ProjectView (Tabs: Actividades, Incidencias, Gantt Original, Modificado, Comparación)
- Custom Gantt chart with Tailwind flexbox bars (IKB blue + Signal red)
- Stats cards + responsive layout
- Swiss design (Cabinet Grotesk + IBM Plex Sans, #002FA7 primary, #FF2A00 accent)

## Implemented (2026-02-27)
- **Sticky Gantt Header**: Restructured `GanttChart.jsx` so the date header (timeline ticks) + "Actividad" column header remain visible (sticky top-0) when scrolling vertically through long activity lists. Implemented via synced horizontal scroll between an outer sticky header row (`overflow-x-hidden`) and the body scroll container (`overflow-x-auto`) using refs. Today's red line + "HOY" badge also propagated to the sticky header. Applies to all 3 modes (Original, Actual, Comparación).
- **Inline editing of activities**: New reusable `EditableCell` component activated by double click; supports text/number/date. Inline-editable fields in `ProjectView.jsx` activities table: Nombre, Categoría, Duración, Inicio (`start_date`), Fin (`end_date`), Responsable. Estado y Progreso already inline. Save with Enter/blur, cancel with Escape. Generic `handleFieldUpdate` validates duration ≥1 and ISO date format.
- **Dashboard – Pending Activities section**: New cross-project table on the home page (`PendingActivities.jsx`) listing activities with their project column. Four filter pills with counts: Pendientes (default, status ≠ Finalizado), Completadas, Pendientes de esta semana (activities overlapping current ISO week), Pendientes de la siguiente semana (activities starting next ISO week). Each row links to the project. Uses existing `/api/gantt-general` endpoint.
- **Moved Pending Activities to own page + Project Director**: Pending Activities now lives at `/pending-activities` (`PendingActivitiesPage.jsx`); Dashboard hero restored with "Tus proyectos" as the main section. New "Actividades pendientes" button in the hero action bar links to it. New `director` field on Project model + `PUT /api/projects/{id}` endpoint + `updateProject` API helper. Director shown in every project card and editable from "Nuevo proyecto" dialog. Seeded directors: Carlos Mesia → Fintech, SSO, HRM Go Colombia, Logística Colombia; Carlos Gutarra → all other 9 projects.

## Test Coverage
- 15/15 backend pytest cases pass
- Frontend E2E Playwright run passes (canonical scenario A→B(+3d)→C verified)

## Backlog
### P1
- Edit activity dialog (currently only create + delete)
- Export Gantt to Excel/CSV (mimic original spreadsheet)
- Editable project (rename, change start date)

### P2
- Drag & drop predecessor editing on Gantt
- Filter/search in activities and incidents tables
- Resource allocation per activity
- Milestone markers on Gantt
- Print-friendly Gantt view
- Authentication + multi-user collaboration
- Undo / version history of incidents
