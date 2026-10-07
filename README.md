# Jira Event Layer

[![CI](https://github.com/jvo5610/jira-event-layer/actions/workflows/ci.yml/badge.svg)](https://github.com/jvo5610/jira-event-layer/actions/workflows/ci.yml)

An open-source, API-first event layer for Jira Cloud. Receive native webhooks, persist events in
PostgreSQL, evaluate versioned YAML rules, and execute deterministic Jira or Bitbucket actions.
The frontend is optional. No LLM, Redis, SQS or specific cloud is required.

**Experimental (0.2.0 candidate), not production-certified.** This is not a complete replacement for
Jira Automation. Jira create/clone/edit/transition/comment/link/get and Bitbucket pipeline actions are
implemented. Scheduling, action branching, broad JQL fanout, JSM features and other providers are not.
See [capabilities and production gates](use-cases.json). Not affiliated with Atlassian.

## Quick start

Prerequisites: Git, Python 3.12+, [uv](https://docs.astral.sh/uv/getting-started/installation/),
Docker Engine with Compose v2. macOS or Linux; Windows users can use WSL2 with Docker integration.

```sh
git clone https://github.com/jvo5610/jira-event-layer.git
cd jira-event-layer
python3 tools/selftest.py --full
```

This installs locked dependencies, runs tests against disposable PostgreSQL, builds the container,
and verifies the shipped Compose installation, migrations, isolation, restart and backup/restore.
It needs no cloud credentials and makes no Jira/Bitbucket calls. Only its own temporary resources are removed.

To start a local development instance:

```sh
python3 tools/local_config.py
docker compose -p jira-event-layer-dev -f compose.dev.yaml up -d --build
curl --fail http://127.0.0.1:8088/readyz
```

The generated `.env` contains a private `ADMIN_TOKEN`. Management endpoints require
`Authorization: Bearer <ADMIN_TOKEN>`; start with `GET /v1/schema` and `GET /openapi.json`.
Keep tokens out of shell history, commits and rules. Stop development services with
`docker compose -p jira-event-layer-dev -f compose.dev.yaml down` (database volume is preserved).

For self-hosted installations, use `compose.yaml` with operator-supplied PostgreSQL and mounted secrets;
the same image runs separate API, webhook receiver and worker processes. Do not expose management APIs
publicly. Kubernetes/ECS deployments are operator-managed, not bundled or certified.

Rules are saved as inactive drafts, evaluated without effects, and activated explicitly with version
and checksum. Connectors are disabled until their allowlists and credentials are configured.
See [examples](examples), [contributing](CONTRIBUTING.md), [security](SECURITY.md), and the operational
reference below (Spanish). Licensed under [MIT](LICENSE).

## Referencia operativa (español)

Backend independiente del front para **guardar jobs/reglas YAML, comparar filtros y ejecutar sobre eventos**.
Motor single-tenant portable. Cada instalación tiene su propia base, identidad y credenciales.
No necesita un LLM para ejecutar. Las instalaciones de laboratorio no son dependencias del producto.

Versión 0.2.0 en preparación: mejoras de operación y seguridad implementadas y verificadas localmente.
No está certificada para producción ni publicada como release. Ver los gates pendientes al final.

## Probar desde un clon limpio

Requisitos: Python 3.12 o posterior, uv, Docker Engine y Docker Compose v2 funcionando.
Desde la raíz del checkout, sin crear .env ni proporcionar credenciales:

```sh
python3 tools/selftest.py --full
```

Este comando instala las dependencias fijadas, crea una base efímera propia, ejecuta todos los tests,
construye la imagen y prueba el Compose distribuido, migraciones, reinicio y backup/restauración.
Elimina sólo los contenedores/redes/imágenes temporales que creó. No llama a Jira, Bitbucket ni AWS.
Sin --full ejecuta la suite con Postgres real pero omite build/aceptación de contenedores.

Para explorar la API local, `python3 tools/local_config.py` genera una configuración de desarrollo
sin sobrescribir archivos y `docker compose -p automation-dev -f compose.dev.yaml up -d --build` arranca
los servicios. Usar el ADMIN_TOKEN local para consultar /v1/schema. Esto es desarrollo, no producción.
Los ejemplos usan destinos ficticios; reemplazarlos y habilitarlos explícitamente antes de activar reglas.

## Pruebas con proveedores reales

Son opt-in y separadas de la suite reproducible. Crear un proyecto/repositorio desechable y una
credencial con permisos mínimos; nunca usar recursos productivos. El pipeline elegido debe ser NO-OP.

```sh
export E2E_ALLOW_WRITES=yes
uv run python tools/e2e_live.py --provider bitbucket --output work/e2e-bitbucket
uv run python tools/e2e_live.py --provider jira --output work/e2e-jira
```

Bitbucket requiere BITBUCKET_EMAIL, BITBUCKET_TOKEN, E2E_BITBUCKET_REPOSITORY (workspace/repo),
E2E_BITBUCKET_BRANCH y E2E_BITBUCKET_PIPELINE. Jira requiere JIRA_EMAIL, JIRA_TOKEN, JIRA_CLOUD_ID,
E2E_JIRA_PROJECT, E2E_JIRA_ISSUE_TYPE_ID, E2E_JIRA_TO_STATUS_ID y E2E_JIRA_LINK_TYPE_ID.
No pegar tokens en comandos ni en YAML: cargarlos mediante el gestor de secretos del operador.
Primero construir la imagen automation-api:0.2.0; --image permite probar otra imagen explícita.

La prueba usa la API HTTP, Postgres y worker reales en contenedores, valida el resultado con lecturas
independientes al proveedor y comprueba deduplicación/filtro negativo. Jira crea tarjetas etiquetadas;
verifica creación, comentario, edición, clonación, vínculo y transición; además transiciona la copia
según el estado actual del original, verifica no-op de transición repetida y bloqueo por guarda falsa.
no modifica tarjetas preexistentes. Conserva recursos remotos y una copia privada de la DB como evidencia.
No reintentar una ejecución fallida sin revisar esa evidencia. Esta prueba entra por /v1/events:
NO sustituye el E2E de un webhook nativo Jira, que requiere un receptor HTTPS y su registro en Jira.

## Preparar una distribución de código

`python3 tools/export_source.py /ruta/nueva/automation-api` exporta una lista explícita de archivos de
código, Dockerfiles, ejemplos, tests y documentación. No incluye historial Git, secretos, laboratorios
privados, .env, dumps, resultados reales ni entornos virtuales. Genera checksums en SOURCE-MANIFEST.json.
Ejecutar selftest --full desde esa copia es la prueba del recorrido de un tercero.

La distribución incluye LICENSE (MIT). La exportación no publica nada ni incluye historial Git.
No cambiar a público un repositorio privado anterior sin revisar también todo su historial.

## Componentes

- API FastAPI: validación, revisiones inmutables, activación/rollback, evaluación, comparación e histórico.
- Postgres: YAML original + especificación normalizada + checksum, raw events, decisiones, ejecuciones y auditoría.
- Worker independiente: cola transaccional en la misma base, Bitbucket Pipelines y seguimiento hasta su resultado.
- Front: no implementado ni requerido. Cualquier UI/CLI/agente consume este mismo contrato HTTP.

Dependencias fijadas en uv.lock. Sin Redis, SQS, editor visual ni código arbitrario dentro del YAML.
No se promete paridad completa con Jira Automation. Bitbucket y las siete acciones Jira están
verificadas contra proveedores reales desde la API autenticada. El flujo nativo Jira hacia Bitbucket
se verificó en el laboratorio anterior; falta repetir el webhook nativo con esta revisión desplegada.
Subtareas y asignación todavía tienen pruebas simuladas, no aceptación contra Jira real.
El inventario de capacidades y brechas está en use-cases.json.
Admite hasta diez acciones secuenciales por regla. No hay branching de acciones ni compensaciones aún.

## API

Excepto health/ready y el webhook firmado, requiere Authorization: Bearer TOKEN.
OpenAPI en /openapi.json también está autenticado. /docs y /redoc no se publican;
una UI separada puede cargar el contrato autenticado. Permisos de servicio:

| Credencial | Permisos |
|---|---|
| ADMIN_TOKEN | Todas las operaciones |
| READER_TOKEN | Consultar, validar, evaluar y comparar sin ejecutar |
| WRITER_TOKEN | Lo anterior y guardar borradores; no activar ni ejecutar |
| OPERATOR_TOKEN | Consultar/simular, activar/desactivar, ingresar eventos, ejecutar y reconciliar; no editar reglas |

Los tres tokens limitados son opcionales; vacío significa deshabilitado. Deben ser distintos,
de al menos 32 caracteres ASCII. Son identidades de servicio, no RBAC por usuario ni multi-tenancy.
La auditoría de mutaciones de reglas/replay/reconciliación identifica el rol, nunca el token.

| Endpoint | Función |
|---|---|
| GET /v1/schema | JSON Schema del DSL, para agentes y futuros editores |
| POST /v1/rules/validate | Validar YAML sin guardar |
| POST /v1/rules | Guardar YAML como nueva revisión; no activar automáticamente |
| GET /v1/rules | Listar reglas y versión activa |
| GET /v1/rules/{name}/versions | Histórico de revisiones |
| GET /v1/rules/{name}/versions/{version} | Recuperar YAML y spec |
| POST /v1/rules/{name}/activate | Activar/rollback con version y sha256 explícitos |
| POST /v1/rules/{name}/disable | Desactivar nuevos eventos y frenar pasos pendientes; no revierte llamadas ya enviadas |
| POST /v1/evaluate | YAML + payload o event_ids; devuelve explicación, sin ejecutar |
| POST /v1/compare | left_yaml + right_yaml + event_ids; diferencias de coincidencia, sin ejecutar |
| POST /webhooks/jira | Webhook nativo firmado HMAC; persistir antes de 202 |
| POST /v1/events | Ingesta autenticada con Idempotency-Key |
| GET /v1/events, /v1/events/{id} | Histórico y decisiones por regla |
| POST /v1/events/{id}/execute | Evaluar versiones activas; no duplica la misma versión/evento |
| GET /v1/runs, /v1/runs/{id} | Estado y log de ejecución |
| POST /v1/runs/{id}/reconcile | Adjuntar UUID verificado de pipeline a un resultado ambiguo; no repite POST |
| GET /v1/audit, /metrics | Auditoría y métricas mínimas |

Los POST de YAML aceptan el documento crudo, con Content-Type: application/yaml.
Activación: {"version":1,"sha256":"checksum devuelto al guardar"}.
Evaluación: {"yaml":"...","source":"jira","event_type":"jira:issue_updated","payload":{...}}.
Comparación: {"left_yaml":"...","right_yaml":"...","event_ids":["uuid"]}.
Ingesta: {"source":"jira","event_type":"jira:issue_updated","payload":{...}}.

## YAML y filtros

Ver examples/repository-request.yaml. DSL automation/v1 / Rule; name identifica una regla.
Trigger source+event y when con all/any/not/some. Predicados: eq/ne/in/contains/exists/gt/gte/lt/lte.
Paths son JSON pointers (/issue/key, /changelog/items). some evalúa where sobre elementos de una lista.
Igualdad tipada: true no equivale a 1. Un campo ausente no satisface ne; usar exists explícitamente.
Las variables de acciones son literales {value: "..."} o referencias {path: /issue/key, default: "..."}.
No hay eval, shell, Jinja, Python en YAML, URLs arbitrarias ni resolución de secretos desde reglas.
64 KiB por YAML, sin aliases ni claves duplicadas, filtros con profundidad y presupuesto acotados.

Los ejemplos usan DEMO y example-workspace como destinos ficticios. Los nombres de transición son
In Progress → Done; mapearlos a los valores reales antes de activar. El ejemplo por ID requiere IDs
verificados en la instalación destino. El operador debe crear su propio pipeline de prueba NO-OP;
no hay dependencia de un workspace privado ni se incluye provisioning AWS.

## Durabilidad e idempotencia

- Evento + evaluaciones + runs se escriben en una transacción antes de responder 202.
- Identidad de entrega: tenant/source/delivery_id. Misma ID con otro cuerpo retorna 409.
- Cada run es único por evento/regla/versión. Revisiones nuevas pueden ejecutarse sobre el mismo evento mediante execute explícito.
- processed_at significa que la evaluación/encolado durable finalizó, no que Bitbucket terminó.
- Workers usan FOR UPDATE SKIP LOCKED y leases. Guardan dispatching ANTES del POST externo.
- 201 de Bitbucket sólo inicia el seguimiento: succeeded llega al observar COMPLETED/SUCCESSFUL.
- Timeout/5xx de un POST o crash durante dispatch se consideran ambiguos: needs_review, sin reintento ciego.
- 429 permite retry acotado respetando Retry-After. GET de estado es reintentable y tiene presupuesto.
- El orden entre eventos distintos no está garantizado. No hay exactamente-una-vez para efectos externos.
- Desactivar una regla falla sus pasos pendientes con rule_disabled. Una llamada ya enviada puede completar;
  los pipelines en marcha siguen consultándose, pero no se inician pasos posteriores de una regla desactivada.
- Leases con token de propietario: un worker atrasado no puede sobrescribir el estado de otro; su resultado
  se conserva como late_completion para investigación. Esto no garantiza exactly-once en Jira.

## Acciones Jira — implementación local, validación de proveedores separada

- jira.issue.create: crear un ticket con campos literales o tomados del evento; parent permite subtareas.
- jira.issue.clone: copiar sólo campos seleccionados de una tarjeta administrada; no es un clon completo.
- jira.issue.edit: modificar campos autorizados, incluidos assignee, labels y customfields habilitados por el servidor.
- jira.issue.transition: consultar el estado actual y transiciones disponibles; destino por ID de estado.
- jira.comment.add: comentario de texto convertido a ADF.
- jira.issue.link: vincular dos tarjetas administradas mediante un ID de tipo de vínculo.
- jira.issue.get: leer la tarjeta actual y, opcionalmente, detener la secuencia si no cumple require.

Resultados de pasos previos: /_steps/0/key, /_steps/1/fields/status/id, etc. El contexto lo construye el worker
desde Postgres; un evento entrante no puede sobrescribirlo. Las cadenas se reanudan desde el paso pendiente.
Ejemplo: examples/jira-create-from-source.yaml (borrador, nunca activado automáticamente).

El conector queda deshabilitado por defecto. Requiere JIRA_EMAIL, JIRA_TOKEN, JIRA_CLOUD_ID y
ALLOWED_JIRA_PROJECTS. Usa exclusivamente https://api.atlassian.com/ex/jira/{cloudId}/rest/api/3.
JIRA_REQUIRED_LABEL (default automation-managed) es obligatoria en todas las tarjetas leídas/modificadas;
las tarjetas creadas reciben esa etiqueta. El proyecto real de cada tarjeta se verifica antes de actuar.
ALLOWED_JIRA_FIELDS controla los campos; project/status/security/reporter no se pueden inyectar por fields.
JIRA_MAX_DAILY_WRITES (default 100) limita globalmente intentos de escritura en 24 horas mediante Postgres.
Cada intento reserva presupuesto antes del envío, incluso si luego queda ambiguo o recibe 429.

Los timeouts y 5xx de escrituras van a needs_review; sólo lecturas previas y 429 admiten reintento acotado.
Crear tickets y comentarios incluye una propiedad automation.operation para ayudar a correlacionar resultados.
Su formato nuevo es un objeto versionado: schema_version, id (operación), rule_name, rule_version,
rule_sha256, run_id y step. Lo construye el worker desde la revisión inmutable; el evento y las
variables YAML no pueden definirlo. Las propiedades históricas de texto/id siguen siendo evidencia
del formato anterior: no se reescriben ni se usan como autorización. Las propiedades de Jira son
visibles a usuarios/apps con acceso: no contienen secretos. El histórico completo permanece en Postgres.

### Identificación legible de tickets

Al crear o clonar, el motor agrega `automation-flujo-<name>` usando exactamente el `name` del YAML.
Ejemplo: `name: crear-repositorio` produce `automation-flujo-crear-repositorio`. La etiqueta identifica
el flujo creador, no la última regla que tocó el ticket. Revisiones del mismo flujo mantienen etiqueta;
renombrar `name` crea otra regla. No se agregan UUIDs ni timestamps a las etiquetas.

`automation-managed` (o JIRA_REQUIRED_LABEL existente) sigue siendo la marca de alcance operativo.
No se cambia por defecto al actualizar y no se sustituye por la etiqueta del flujo. Es una guarda de
la aplicación, no una frontera de permisos Jira: usar además identidad dedicada y permisos de proyecto.
Los tests usan únicamente `automation-prueba` como etiqueta de negocio, además de las dos anteriores.

Las etiquetas de negocio se conservan al crear; duplicados se eliminan. Clonar conserva las etiquetas
seleccionadas salvo las del flujo de origen, que se reemplazan por la del flujo creador de la copia.
El prefijo `automation-flujo-` está reservado: el YAML no puede inventarlo al crear ni alterarlo al editar
o transicionar. Al reemplazar labels en un ticket existente se deben conservar la marca de seguridad y
sus etiquetas de flujo; un cambio que las elimine se rechaza. Máximo 30 etiquetas incluyendo las reservadas:
si se excede, se rechaza antes de escribir, sin truncar ni borrar etiquetas de negocio.

La actualización no recorre ni reetiqueta tickets históricos automáticamente. Para migrarlos, inventariar
las claves exactas, verificar la regla original en el histórico y preservar sus etiquetas de negocio.
No cambiar JIRA_REQUIRED_LABEL de una instalación activa sin migrar sus tickets y configuración juntos.

### Límites de recuperación

No hay reconciliación automática Jira aún; /reconcile sólo acepta pipelines de Bitbucket.
JIRA_IGNORED_ACTOR_IDS acepta account IDs separados por coma de una cuenta de servicio dedicada.
Sus webhooks se conservan pero no disparan reglas, y se rechaza su replay con execute.
No usar la cuenta personal del operador para este filtro. El límite diario contiene un bucle;
ignorar un actor conocido no sustituye seguimiento general de causalidad entre sistemas.
La comprobación de estado antes de transicionar no es atómica con cambios concurrentes en Jira.

La suite incluye Postgres real y proveedor HTTP simulado para las nuevas acciones.
Los esquemas descriptivos completos y los gates de producción siguen pendientes; más acciones no implica production-ready.

## Desarrollo

1. uv sync
2. python3 tools/local_config.py (genera .env local, ignorado por Git, sin mostrar secretos).
3. docker compose -p automation-dev -f compose.dev.yaml up -d --build
4. API en http://127.0.0.1:8088; /v1/schema y /openapi.json requieren bearer.

compose.dev.yaml tiene credenciales de prueba; nunca usarlo como plantilla de producción.
El nombre de proyecto separado evita reutilizar accidentalmente volúmenes del laboratorio.

Pruebas unitarias: uv run pytest tests/test_dsl.py -q.
Integración: crear la base dedicada automation_test en Postgres local y ejecutar:

    TEST_DATABASE_URL=postgresql://automation:local-test-only@127.0.0.1:55439/automation_test uv run pytest -q

Las pruebas rechazan bases con otro nombre; nunca ejecutarlas contra la base del despliegue.
Usan Postgres real y mock HTTP del proveedor para fallos controlados. Eso no sustituye la prueba E2E con Jira/Bitbucket reales.

## Instalación portable

La misma imagen ejecuta API (comando por defecto), worker (`python -m app.worker`), migraciones
(`python -m app.migrate`) y mantenimiento (`python -m app.retention`). Sólo necesita PostgreSQL y
acceso HTTPS saliente a los proveedores habilitados. No invoca AWS, kubectl, SSM ni Docker desde el motor.
Usar una base dedicada por instalación. TENANT_ID es obligatorio e inmutable para esa base.

Para Compose con PostgreSQL administrado por el operador:

1. Preparar una base y dos roles: propietario para migraciones, runtime sin permisos DDL/superuser.
2. Crear un archivo de configuración a partir de .env.example en una ruta nueva, sin sobrescribir .env existente.
3. Ejecutar `uv run python tools/init_installation.py`; pide ambos DSN sin mostrarlos y genera secretos.
   Se niega a sobrescribir un directorio existente. Los tokens de conectores/roles opcionales empiezan vacíos.
4. Configurar allowlists, email/cloud ID e identidad de servicio. Cargar credenciales de conectores en los
   archivos privados, nunca en YAML de reglas. El runtime soporta NAME o NAME_FILE, no ambos.
5. Construir `docker build -t automation-api:0.2.0 .` o usar una imagen de un registro privado validado.
6. Ejecutar primero `docker compose --env-file install.env run --rm migrate` con el archivo elegido.
7. Aplicar deploy/runtime-grants.sql como propietario, pasando runtime_role al cliente psql.
   Otorga DML en datos y lectura de metadatos de instalación/migración; no otorga CREATE ni propiedad.
   La aplicación no crea tablas al arrancar. Usar este script sólo en una base dedicada al producto.
8. Ejecutar `docker compose --env-file install.env up -d`. La migración idempotente debe finalizar antes de los servicios.

El Compose principal no incluye una base con password prefijado: puede apuntar a PostgreSQL externo,
otro contenedor o un servicio administrado. Los DSN se interpretan desde los contenedores; localhost
no es el host ni otro contenedor. Usar sslmode=verify-full y CA montada para conexiones remotas.
Agregar el montaje de CA al Compose si el proveedor no usa una autoridad presente en la imagen.

La carpeta secrets tiene modo 0700; los archivos 0444 permiten lectura al UID 10001 dentro de los
contenedores a los que Compose los monta. El directorio privado impide acceso de otros usuarios del host.
No mover esos archivos a directorios públicos. En Linux validar propietarios/montajes; en plataformas
administradas usar su mecanismo de secretos. La configuración se carga al arrancar: rotar exige reinicio
controlado. No hay recarga en caliente ni período de doble clave de webhook en esta versión.

Compose publica administración sólo en 127.0.0.1:8088 y webhooks sólo en 127.0.0.1:8089. Poner delante
del segundo un proxy HTTPS con límites de conexión, tiempo y tasa. Nunca publicar el primero sin una
red privada/control de acceso. API_SURFACE=webhooks rechaza rutas administrativas aun si el proxy falla;
API_SURFACE=management rechaza webhooks. API_SURFACE=all es para instalaciones privadas simples.
No montar el socket Docker en ningún componente. El frontend no es necesario para operar.

| Plataforma | Mapeo del mismo contrato de ejecución |
|---|---|
| Compose | compose.yaml; servicios separados, migración previa y secretos por archivo |
| Kubernetes | Deployments para API/worker, Job previo para migrar, Secret/volumen, probes; ingress sólo al receptor |
| ECS | Services para API/worker, tarea puntual previa para migrar, secretos inyectados; balanceador sólo al receptor |

No hay manifiestos K8s/ECS genéricos certificados en esta release. No requieren cambios de código,
pero deben probarse en la plataforma elegida. Si se agregan hostnames en Kubernetes, usar external-dns
en los manifests como fuente de verdad. Los despliegues particulares no deben copiarse como producto universal.

## Operación y actualización

- /healthz confirma proceso; /readyz confirma DB. Worker: `python -m app.health worker`, con heartbeat
  por contenedor. WORKER_ID debe ser único si se ejecutan varios workers en el mismo hostname.
- /metrics incluye runs por estado, workers sanos y edad del pendiente más antiguo. Alertar ante cero
  workers sanos, crecimiento de cola, needs_review, fallas de firma y fallas del proceso.
- SIGTERM deja terminar el paso actual; dar 150 segundos al contenedor. Si muere durante una escritura,
  el lease vencido pasa a needs_review: investigar el proveedor, no repetir la llamada a ciegas.
- La cuota diaria de Jira y Bitbucket se reserva en Postgres antes de escribir, compartida entre workers.
- Logs de proceso a stdout/stderr; detalles de ejecución en Postgres. Evitar registrar cuerpos de webhook,
  credenciales o encabezados. Configurar retención/cifrado/acceso de logs y backups en la instalación.
- Retención: `python -m app.retention --days 90 --limit 100` es dry run. Agregar --apply redacta cuerpos,
  trazas y resultados de eventos antiguos terminados; NO toca pendientes ni needs_review. Es irreversible
  salvo backup. Conserva identificadores/checksums para deduplicar y bloquea ejecución de payloads borrados.
  YAML de reglas y auditoría se conservan; no incluir secretos en literales. El operador agenda el comando
  con su scheduler, no se agrega un daemon ni cron interno.

Actualización: fijar digest de imagen, detener ingreso y drenar/detener workers, tomar backup verificado,
ejecutar migraciones con el propietario y volver a conceder permisos sobre objetos nuevos; arrancar la
imagen correspondiente y verificar probes. No mezclar workers de releases incompatibles. Los scripts SQL
aplicados son inmutables y su checksum se valida al arrancar. Una versión anterior rechaza una DB más nueva;
no hay downgrade destructivo automático. Recuperación: restaurar backup en una base nueva y usar la imagen
correspondiente, conservando tenant y claves. Antes de reanudar escrituras reconciliar efectos posteriores
al backup: restaurar estado local NO revierte acciones que ya ocurrieron en Jira/Bitbucket.

Prueba reproducible sin proveedores: `uv run python tools/smoke_container.py --image automation-api:0.2.0`.
Crea infraestructura Docker efímera propia, prueba migración/upgrade/roles/reinicio/backup y la elimina al terminar.
No usa .env, credenciales personales ni bases existentes. Los tests de conectores no equivalen a certificación cloud.
También levanta el Compose distribuido con secretos montados y verifica la separación de rutas.
Bitbucket Pipelines ejecuta la suite y pip-audit en pushes y pull requests; no publica ni despliega imágenes.
El escaneo de paquetes Python no cubre paquetes del sistema operativo ni equivale a una auditoría de seguridad.

Referencias operativas: [secretos de Compose](https://docs.docker.com/compose/how-tos/use-secrets/),
[orden de arranque](https://docs.docker.com/compose/how-tos/startup-order/),
[verificación TLS de PostgreSQL](https://www.postgresql.org/docs/current/libpq-ssl.html).

## Pendiente para producción

E2E nativo de cada acción Jira; cuenta de servicio y rotación ensayada; mapeo definitivo JSM/campos;
reconciliación Jira y causalidad entre sistemas; conflictos de orden/concurrencia en el proveedor;
schemas descriptivos completos; carga/fault injection prolongadas; auth por usuario si la instalación
lo necesita; límites/alertas de ingreso en el proxy; backup/PITR y RPO/RTO de la instalación real;
registro privado/digests, escaneo completo y firma/SBOM de imagen; aceptación de K8s/ECS si se ofrecen.
Las migraciones, roles de servicio, probes y retención ya están implementados; esto no cierra esos gates.
Por ahora las revisiones y auditorías son append-only mediante API, no un almacén legal inmutable.
Para PostgreSQL remoto, usar verify-full con CA/nombre adecuados; require cifra pero no verifica la identidad del servidor.
