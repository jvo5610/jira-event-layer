# Jira Relay

[![CI](https://github.com/jvo5610/jira-event-layer/actions/workflows/ci.yml/badge.svg)](https://github.com/jvo5610/jira-event-layer/actions/workflows/ci.yml)

Motor de automatización para Jira Cloud: recibe webhooks, conserva los eventos en PostgreSQL,
evalúa reglas YAML versionadas y ejecuta acciones Jira o pipelines Bitbucket. **jira-relay-studio**
es su editor visual opcional. La API unifica gestión y webhooks; API, worker y Studio son procesos independientes:
pueden correr como servicios en Docker, Kubernetes, ECS o una VM, sin cambiar el código.
No requiere LLM, Redis, SQS ni un proveedor cloud específico.

**Contrato v1, versión 1.0.0 candidata; sin certificación general de producción.** Hay ocho acciones,
hasta diez pasos secuenciales por regla y filtros deterministas. No hay paridad completa con Jira
Automation/Make: no implementa scheduling, routers/paralelismo, fanout JQL, compensaciones ni JSM
específico. El [inventario de capacidades](use-cases.json) y los [pendientes de producción](#pendiente-para-producción)
separan implementación de aceptación operativa. No está afiliado a Atlassian. Licencia [MIT](LICENSE).

El nombre del repositorio y del paquete Python sigue siendo `jira-event-layer`; el producto se llama
Jira Relay, el frontend `jira-relay-studio` y el contrato YAML `automation/v1`. No son servicios distintos.

## Por dónde empezar

| Necesitás | Leer |
| --- | --- |
| Entender componentes y recorrido de un evento | [Modelo de ejecución](#modelo-de-ejecución) y [componentes](#componentes) |
| Probar un clon sin cuentas externas | [Quick start](#quick-start) |
| Instalar servicios separados | [Instalación sin Compose](#instalación-como-servicios-independientes-sin-compose) y [variables por servicio](#mapa-por-servicio) |
| Conectar Jira de verdad | [OAuth / API token](#autenticación-jira-oauth-y-api-token) y [webhook firmado](#conectar-el-webhook-real-de-jira) |
| Crear, probar y activar una regla | [Primer flujo](#primer-flujo-asignación-y-descripción) y [guía HTTP reproducible](docs/technical-reference.md#recorrido-http-validar-evaluar-guardar-y-activar) |
| Aprender todas las variantes YAML | [DSL y filtros](#yaml-y-filtros), [referencia de acciones](docs/technical-reference.md#acciones-entradas-y-resultados) y [catálogo comentado](examples/all-v1-variations.yaml) |
| Usar el editor y sus sugerencias | [Guía de Studio](docs/studio.md) |
| Emitir/rotar tokens | [Autenticación](#api) y [rotación](#emisión-revocación-y-rotación-de-credenciales) |
| Operar, migrar o recuperar una instalación | [Operación](#operación-y-actualización), [migraciones](#migraciones-de-postgresql) y [diagnóstico](docs/technical-reference.md#diagnóstico-y-recuperación) |
| Modificar código o trabajar con una IA | [Mapa del código](docs/technical-reference.md#mapa-del-código-y-fuentes-de-verdad), [pruebas](#desarrollo), [contribución](CONTRIBUTING.md) y [seguridad](SECURITY.md) |

Este README es la entrada operativa; los documentos enlazados son parte de la documentación del
mismo checkout, no instrucciones de un laboratorio externo. El schema y OpenAPI autenticados
de la instancia son el contrato ejecutable. No asumir capacidades por el aspecto del editor.

## Modelo de ejecución

1. Jira envía un webhook firmado a `/webhooks/jira` de la API. Alternativamente, un operador autorizado ingresa
   un evento por la API; esta segunda vía no autentica la identidad declarada del actor Jira.
2. La API verifica firma/identidad de entrega y persiste el evento. Se excluyen las cuentas
   conocidas del servicio; un evento Jira sin actor inequívoco queda retenido, no se ejecuta.
3. En la misma transacción se comparan las reglas activas, se guarda la explicación del filtro y
   se crean ejecuciones para las coincidencias. Un `202` confirma persistencia, **no éxito en Jira**.
4. El worker toma una ejecución durable, verifica identidad/permisos y controles de seguridad,
   resuelve sus datos y despacha un paso. Guarda resultados para los pasos siguientes.
5. Una acción Jira exitosa permite avanzar; un pipeline se sigue hasta conocer su resultado.
   Fallos ambiguos de escritura requieren revisión, no una repetición automática.
6. API y Studio permiten inspeccionar evento, decisión, versión exacta, pasos y errores.

Una **regla** tiene un `name` estable y revisiones inmutables; una **versión activa** recibe nuevos
eventos. Un **evento** es una entrega persistida; una **ejecución** (`run`) es el procesamiento de
ese evento por una regla/versión. Un **paso** es una acción de esa ejecución. Guardar un borrador
no lo activa. Activar una versión no reproduce el histórico automáticamente.

`TENANT_ID` identifica una instalación y su base dedicada: no se elige por request ni sirve para
compartir una DB entre empresas. No es el Jira Cloud ID. Los checksums de reglas, entregas y
migraciones tienen finalidades distintas: [detalle técnico](docs/technical-reference.md#datos-checksums-y-tenant).

## Quick start

### Configuración desde variables o un archivo montado

API, worker, migraciones y herramientas de operación aceptan variables de entorno
directas. También se puede definir `ENV_FILE` con la ruta de un archivo dotenv
montado, por ejemplo `ENV_FILE=/app/.env`. El código Python carga ese archivo antes
de validar la configuración; no hace falta un wrapper ni ejecutar `source`.

Las variables ya presentes en el proceso tienen prioridad por clave, incluso si
están vacías. Se conservan los secretos individuales mediante `DB_PASSWORD_FILE`,
`JIRA_WEBHOOK_SECRET_FILE` y los demás parámetros `_FILE`.
Sin `ENV_FILE` no se busca ni carga automáticamente ningún `.env`. Un archivo
configurado que no puede leerse impide el arranque. Se admite UTF-8 (hasta 1 MiB),
comentarios y valores entre comillas; no se ejecuta shell ni se expanden `$VAR`
o `${VAR}`, para preservar contraseñas literales. Los valores se cargan al iniciar
cada proceso: reiniciarlo después de cambiar el archivo montado.

For the optional, runtime-configured visual editor, see
[jira-relay-studio](docs/studio.md). The same image runs against any configured backend,
independently of Docker, Kubernetes, ECS or other deployment choices. It covers the v1 actions and filters,
maps observed event fields, and uses the same versioned API and worker.

Requisitos para la prueba reproducible: Git, Python 3.12+, [uv](https://docs.astral.sh/uv/getting-started/installation/),
Docker Engine con Compose v2. En Windows, usar WSL2 con integración Docker.
Son requisitos de esta prueba, no dependencias del motor dentro de una instalación.

```sh
git clone https://github.com/jvo5610/jira-event-layer.git
cd jira-event-layer
python3 tools/selftest.py --full
```

Instala dependencias fijadas, ejecuta tests con PostgreSQL descartable, construye el contenedor y
verifica instalación, migraciones, aislamiento, reinicio y backup/restauración del empaquetado Compose.
No usa credenciales externas ni llama a Jira/Bitbucket. Sólo elimina sus propios recursos temporales.

Para iniciar una instancia local de desarrollo:

```sh
python3 tools/local_config.py
docker compose -p jira-event-layer-dev -f compose.dev.yaml up -d --build
curl --fail http://127.0.0.1:8088/readyz
```

El `.env` generado contiene un `ADMIN_BOOTSTRAP_TOKEN` privado. La API usa
`Authorization: Bearer <token>`; comenzar por `GET /v1/schema` y `GET /openapi.json`.
No poner tokens en historial de shell, commits ni reglas. Para detener:
`docker compose -p jira-event-layer-dev -f compose.dev.yaml down` (conserva el volumen de DB).

Esto es desarrollo, no una instalación productiva. [Servicios independientes](#instalación-como-servicios-independientes-sin-compose)
es el contrato de despliegue; `compose.yaml` es sólo un empaquetado opcional con PostgreSQL del operador.
No publicar administración sin controles de acceso. Studio se instala por separado según
[su guía](docs/studio.md); no es necesario para usar la API.

## Probar desde un clon limpio

Requisitos: Python 3.12 o posterior, uv, Docker Engine y Docker Compose v2 funcionando.
Desde la raíz del checkout, sin crear .env ni proporcionar credenciales:

```sh
python3 tools/selftest.py --full
```

Este comando instala las dependencias fijadas, crea una base efímera propia, ejecuta todos los tests,
construye la imagen y prueba el Compose distribuido, migraciones, reinicio y backup/restauración.
El build usa una copia del código con directorios `0700` y archivos `0600` para comprobar
que el usuario no-root del contenedor puede leerlo, independientemente del `umask` del builder.
Elimina sólo los contenedores/redes/imágenes temporales que creó. No llama a Jira, Bitbucket ni AWS.
Sin --full ejecuta la suite con Postgres real pero omite build/aceptación de contenedores.

Para explorar la API local, `python3 tools/local_config.py` genera una configuración de desarrollo
sin sobrescribir archivos y `docker compose -p automation-dev -f compose.dev.yaml up -d --build` arranca
los servicios. Usar el ADMIN_BOOTSTRAP_TOKEN local para consultar /v1/schema. Esto es desarrollo, no producción.
Los destinos de los ejemplos no configuran la instalación. Adaptar proyecto, tipos, estados y
repositorios a los recursos autorizados del operador. La suite local usa dobles aislados y no llama proveedores.

## Pruebas con proveedores reales

Son opt-in y separadas de la suite reproducible. Los destinos se pasan explícitamente y las
credenciales tienen permisos mínimos. Crean evidencia real en Jira y ejecutan el pipeline real de Bitbucket.

```sh
uv run python tools/e2e_live.py --provider bitbucket --output work/e2e-bitbucket \
  --bitbucket-repository jvidelaolmos/jira-event-layer-lab \
  --bitbucket-branch main --bitbucket-pipeline setup-new-repository \
  --allow-remote-writes

uv run python tools/e2e_live.py --provider jira --output work/e2e-jira \
  --jira-project KAN --jira-issue-type-id 10003 \
  --jira-initial-status-id 10000 --jira-target-status-id 10003 --jira-link-type-id 10000 \
  --allow-remote-writes
```

Las únicas variables de entorno del proveedor son las conexiones normales:
BITBUCKET_API_TOKEN (Access Token de repositorio o workspace, sin email) y
JIRA_CLIENT_ID/JIRA_CLIENT_SECRET/JIRA_CLOUD_ID (OAuth de cuenta de servicio).
Como alternativa compatible, Jira admite JIRA_API_TOKEN/JIRA_CLOUD_ID sin OAuth; no combinar ambos modos.
La versión 1 no acepta los nombres anteriores; los tokens también admiten
BITBUCKET_API_TOKEN_FILE, JIRA_CLIENT_SECRET_FILE y JIRA_API_TOKEN_FILE.
Los destinos se pasan explícitamente como argumentos; no se leen variables E2E_* ni TEST_*.
La autorización para escribir también es un argumento obligatorio para ejecutar, no una variable persistente.
No pegar tokens en comandos ni en YAML: cargarlos mediante el gestor de secretos del operador.
Primero construir la imagen automation-api:1.0.0; --image permite probar otra imagen explícita.

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
- API única: incluye `/webhooks/jira` y gestión `/v1/*` en el mismo proceso y puerto.
- jira-relay-studio: React + gateway Python; editor visual opcional sobre el mismo contrato HTTP.

Dependencias Python fijadas en `uv.lock`, frontend en `frontend/package-lock.json`.
No hay código arbitrario dentro del YAML. Se han realizado pruebas reales de acciones Jira y
pipeline Bitbucket, además del recorrido webhook nativo → motor → Bitbucket. Esa evidencia
histórica no certifica todos los cambios posteriores: cada release y cada instalación deben
validar sus credenciales, permisos, recursos y recorrido efectivo. La suite reproducible usa
PostgreSQL real y respuestas de proveedor simuladas; las pruebas remotas son opt-in.
Ver [pruebas con proveedores reales](#pruebas-con-proveedores-reales),
[CI](.github/workflows/ci.yml) y [brechas conocidas](#pendiente-para-producción).

## API

Excepto health/ready y el webhook firmado, requiere Authorization: Bearer TOKEN.
OpenAPI en /openapi.json también está autenticado. /docs y /redoc no se publican;
una UI separada puede cargar el contrato autenticado.

Se configura `ADMIN_BOOTSTRAP_TOKEN` o `ADMIN_BOOTSTRAP_TOKEN_FILE` para la primera inicialización:
mínimo 32 caracteres ASCII imprimibles sin espacios, generado aleatoriamente por el operador o
por el instalador. La API registra sólo SHA-256 y un marcador de bootstrap en una transacción
protegida contra múltiples réplicas. Luego la DB es la fuente de verdad: se puede retirar la variable.
Aunque quede configurada, se cambie su valor o se rote el token, un reinicio no restaura la credencial
inicial. Borrar o vencer los tokens tampoco habilita otro bootstrap. El runtime no tiene permisos
para actualizar/borrar el marcador; no existe recuperación pública ni un token maestro oculto.

El administrador emite tokens aleatorios opacos (`jrl_...`), con nombre, permisos y vencimiento
obligatorio de 60 segundos a 365 días. El secreto se devuelve sólo en creación/rotación; las
consultas sólo devuelven metadatos, nunca el secreto ni su hash. Guardamos SHA-256 de tokens de
alta entropía, no contraseñas de usuario. La auditoría registra `token:<uuid>` y operaciones,
nunca el secreto. Autenticación consulta DB, sin caché de credenciales revocadas entre réplicas;
revocar bloquea solicitudes posteriores, no deshace acciones ya iniciadas en proveedores.

| Permiso | Capacidades |
| --- | --- |
| `api:read` | Consultar, validar, evaluar y comparar sin ejecutar acciones. |
| `rules:write` | Guardar borradores; no activar ni ejecutar flujos. |
| `flows:operate` | Activar/desactivar, ingresar/reprocesar eventos y reconciliar ejecuciones; puede escribir en proveedores. |
| `auth:admin` | Emitir, listar y revocar credenciales; incluye todos los permisos anteriores. |

No se conceden permisos por defecto al emitir tokens. La GUI debe usar un token delegado con
`api:read,rules:write,flows:operate`, o un subconjunto, mediante `STUDIO_BACKEND_TOKEN`/`_FILE`.
No montar el bootstrap administrador en la GUI: guardar el delegado en `gui_api_token`, que el
instalador deja vacío hasta emitirlo. El navegador no recibe el token configurado del gateway. El login Basic de
la GUI se mantiene; compartir un token de GUI no equivale a auditoría individual por usuario.
Jira, Bitbucket y la firma de webhooks mantienen secretos independientes.

Los antiguos `API_TOKEN`, `ADMIN_TOKEN`, `READER_TOKEN`, `WRITER_TOKEN`, `OPERATOR_TOKEN` y
sus variantes `_FILE` se rechazan explícitamente: no hay fallback legacy.

### Emisión, revocación y rotación de credenciales

| Endpoint | Autorización y resultado |
| --- | --- |
| `GET /v1/auth/me` | Cualquier token vigente; identidad, permisos efectivos y estado de rotación, sin secretos. |
| `POST /v1/auth/tokens` | Administrador confirmado; body `name`, `permissions`, `expires_in_seconds`. Devuelve el token una vez. |
| `GET /v1/auth/tokens?limit=100&offset=0` | Administrador confirmado; metadatos paginados, incluidos vencidos/revocados. |
| `POST /v1/auth/tokens/{id}/revoke` | Administrador confirmado; revoca también su reemplazo pendiente. No permite revocar el último administrador activo confirmado. |
| `POST /v1/auth/tokens/self/rotate` | Token local vigente; genera un reemplazo con exactamente sus permisos y vencimiento original. |
| `POST /v1/auth/tokens/self/confirm` | Usar **el nuevo token pendiente**; revoca el anterior y confirma el nuevo en una transacción. |

La ventana predeterminada es **una hora**, `AUTH_ROTATION_TTL_SECONDS=3600` (configurable entre
60 y 86400 segundos). Nunca supera el vencimiento original. Sólo se permite una rotación pendiente
por token. Durante la ventana ambos permiten sus operaciones normales; el reemplazo pendiente no
puede emitir/listar/revocar credenciales ni iniciar otra rotación. Confirmarlo habilita los permisos
administrativos que heredó. Si no se confirma a tiempo, el nuevo deja de autenticar y el anterior
sigue vigente hasta su propio vencimiento/revocación. Puede iniciarse otra rotación después.
Un administrador puede revocar un reemplazo pendiente cuyo secreto se haya perdido para reintentar.
El secreto viejo de env nunca se reactiva; la API no modifica variables de entorno ni archivos.

La GUI incluye **Accesos** para emitir, listar, revocar y rotar credenciales con un desbloqueo
administrativo independiente de 15 minutos. No necesita otra variable de administrador. Los tokens
nuevos se muestran una vez y deben guardarse; la rotación sigue teniendo una ventana de una hora.
Ver [operación y límites de la GUI](docs/studio.md#accesos-administración-desde-la-gui).

Ejemplo del body para emitir acceso a la GUI (llamada autenticada con el administrador):

```json
{"name":"gui-production","permissions":["api:read","rules:write","flows:operate"],"expires_in_seconds":2592000}
```

Guardar la respuesta secreta en el gestor de secretos, no en historial de shell, logs ni repositorio.
Para rotar, llamar a `self/rotate` con el vigente y probar el nuevo con `GET /v1/schema`.
Reemplazar la credencial del consumidor, reiniciarlo según su despliegue y verificar que ya usa el
nuevo. Finalmente llamar a `self/confirm` usando el nuevo antes de la hora límite. La API no requiere
reinicio para emitir, revocar o confirmar tokens. No usar el bootstrap como recuperación de acceso perdido.

`app/auth.py` separa validación de credenciales (`Authenticator`) de identidad/permisos (`Identity`).
Hoy implementa tokens locales persistidos; un futuro adaptador OAuth/OIDC para Ory Hydra podría devolver
identidades y permisos a esta misma interfaz sin cambiar endpoints, reglas o worker. **Hydra no
está implementado ni se ofrece un modo OAuth ficticio**; su integración requerirá configurar y
validar emisión, audience/issuer, permisos y revocación/expiración según el tipo de token elegido.

| Endpoint | Función |
|---|---|
| GET /v1/schema | JSON Schema del DSL, para agentes y futuros editores |
| GET /v1/catalog | Schema, eventos observados, configuración declarada de conectores y límites del editor |
| GET /v1/catalog/fields?source=jira&event_type=... | Rutas/tipos/ejemplos de los últimos 100 eventos del tipo elegido |
| POST /v1/rules/validate | Validar YAML sin guardar |
| POST /v1/rules | Guardar YAML como nueva revisión; no activar automáticamente |
| GET /v1/rules | Listar reglas y versión activa |
| GET /v1/rules/{name}/versions | Histórico de revisiones |
| GET /v1/rules/{name}/versions/{version} | Recuperar YAML y spec |
| GET /v1/rules/{name}/versions/{version}/context?before_step=N | Contexto histórico anterior al paso N; sólo lectura |
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

### Primer flujo: asignación y descripción

Este flujo agrega `Se asigno!` al final de la descripción del ticket que se asignó o reasignó.
No coincide al desasignar ni al modificar otro campo. Cambiar `KAN` por el proyecto autorizado;
el ejemplo no configura ni crea ese proyecto. No poner el account ID del servicio como actor
de una prueba real: sus eventos se ignoran por diseño.

```yaml
apiVersion: automation/v1
kind: Rule
name: al-asignar-descripcion
description: Agrega un párrafo cuando se asigna el ticket a una persona.
trigger:
  source: jira
  event: jira:issue_updated
when:
  all:
    - {path: issue.fields.project.key, op: eq, value: KAN}
    - some:
        path: changelog.items
        where:
          all:
            - {path: fieldId, op: eq, value: assignee}
            - {path: to, op: exists, value: true}
            - {path: to, op: ne, value: null}
            - {path: to, op: ne, value: ""}
actions:
  - type: jira.issue.edit
    issue: {path: issue.key}
    append_description: {value: "Se asigno!"}
```

En Studio: crear/importar el YAML, validar, guardar una revisión y probar las condiciones en
**Tests y versiones**. La evaluación sólo prueba trigger/filtro: no ejecuta acciones ni asegura
que Jira aceptará sus campos/permisos. Activar explícitamente la revisión y su checksum cuando
el operador esté listo para efectos reales. Luego asignar un ticket desde otra cuenta, inspeccionar
el evento y su ejecución, y comprobar en Jira el nuevo párrafo. La actualización generada por la
cuenta del servicio se conserva como ignorada y no vuelve a ejecutar el flujo.

Para editar el padre, cambiar solamente el destino a `issue: {path: issue.fields.parent.key}`.
Para tomar un valor de una consulta previa, usar por ejemplo
`fields: {summary: {path: '_steps["0"].fields.summary'}}` en un paso posterior al `jira.issue.get`.
La ruta del evento y la salida de un paso son fuentes distintas; ver
[contexto de ejecución y acciones](docs/technical-reference.md#contexto-y-bindings).

### Contrato general

Ver `examples/repository-request.yaml` para un flujo breve y
`examples/all-v1-variations.yaml` como catálogo comentado de todas las variantes v1.
DSL automation/v1 / Rule; name identifica una regla.
Trigger source+event y when con all/any/not/some. Predicados: eq/ne/in/contains/exists/gt/gte/lt/lte.
Paths son referencias relativas al objeto leído, sin prefijo `$`: `issue.key`, `changelog.items`.
`some` evalúa `where` sobre cada elemento de la lista, no sobre el evento completo.
Igualdad tipada: true no equivale a 1. Un campo ausente no satisface ne; usar exists explícitamente.
Las entradas de acciones aceptan literales normales y referencias `"${issue.key}"`.
Una referencia completa conserva el tipo JSON original; `"Ticket ${issue.key}"` produce texto.
`'$${issue.key}'` produce el texto literal `${issue.key}`. Claves literales e índices funcionan
dentro de referencias: `'${issue.fields["some.value"][0].name}'`.
Un campo inexistente falla; no se ejecutan expresiones ni se reinterpreta el contenido obtenido.
La interpolación dentro de texto solo admite escalares no nulos, con booleanos `true`/`false`;
objetos y listas deben usarse como referencia completa en campos Jira, no en variables Bitbucket.
Máximo 4096 caracteres por plantilla y 50 referencias; las acciones mantienen sus límites de salida.
La forma explícita sigue disponible: `{value: "${texto-literal}"}` no interpola y
`{path: issue.key, default: "..."}` permite un default. Objetos literales usan `{value: {...}}`.
Las referencias funcionan en campos/variables y argumentos de acciones que aceptan bindings;
no en nombres de repositorios, ramas, IDs de configuración o predicados `when`.
La versión 1 acepta únicamente esta sintaxis; los JSON Pointer históricos no son válidos.
No hay eval, shell, Jinja, Python en YAML, URLs arbitrarias ni resolución de secretos desde reglas.
64 KiB por YAML, sin aliases ni claves duplicadas, filtros con profundidad y presupuesto acotados.

### Referencias a campos (`path`)

```yaml
# Campos anidados: puntos entre identificadores.
path: issue.fields.summary

# Una clave literal puede contener puntos, espacios, barras o símbolos.
path: 'this["some.value"].is_literal'
path: 'issue.fields["Nombre del repositorio"]'

# Índices de listas: enteros no negativos, comenzando en cero.
path: issue.fields.components[0].name

# Una clave numérica de un objeto es una cadena, no un índice.
path: '_steps["0"].key'
```

Cada `path` parte del objeto que se está leyendo. Normalmente es el evento; en `some.where`
es el elemento actual y en `jira.issue.get.require` es el ticket consultado a Jira.
`this` no es una palabra especial: solo usarla si existe esa clave. `path: ""` selecciona
el objeto actual completo. Campos simples usan `[A-Za-z_][A-Za-z0-9_]*`; cualquier otra clave,
incluidas claves Unicode o vacías, usa `["..."]`. Las comillas dobles interiores siguen los
escapes de cadenas JSON (`\"`, `\\`, `\uXXXX`); las simples exteriores pertenecen a YAML.

No es JSONPath completo: no acepta `$`, funciones, comodines, slices, índices negativos,
filtros dentro de corchetes ni búsquedas recursivas. No usa `eval` ni acceso a atributos Python.
Un path tiene como máximo 500 caracteres y se valida antes de guardar/activar.
Una clave literal numérica (`["0"]`) solo accede a objetos; `[0]` solo accede a listas.
Una referencia válida pero ausente, fuera de rango o sobre un tipo incompatible se considera
ausente; un valor `null` existente sigue siendo presente para `exists`. Los bindings de
Bitbucket usan `default` para ausente o null; sin él fallan antes de enviar la solicitud.
Los bindings Jira conservan sus reglas de tipos y campos permitidos.

Las rutas JSON Pointer (`/issue/key`, `/_steps/0/key`) se rechazan antes de guardar la regla.
`GET /v1/schema` describe la sintaxis en los bindings, filtros y guardas (`x-path-syntax`).

Los ejemplos usan el proyecto `KAN`, los estados verificados `10001` → `10003`, el tipo `10003`,
el vínculo `10000` y `jvidelaolmos/jira-event-layer-lab`. Son datos ilustrativos, no recursos
requeridos por la aplicación. Verificar IDs y permisos propios antes de activar una regla.

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
- jira.issue.clone: copiar sólo campos seleccionados de un ticket accesible a la cuenta; no es un clon completo.
- jira.issue.edit: modificar campos autorizados, incluidos assignee, labels y customfields habilitados por el servidor.
- jira.issue.transition: consultar el estado actual y transiciones disponibles; destino por ID de estado.
- jira.comment.add: comentario de texto convertido a ADF.
- jira.issue.link: vincular dos tickets accesibles a la cuenta mediante un ID de tipo de vínculo.
- jira.issue.get: leer la tarjeta actual y, opcionalmente, detener la secuencia si no cumple require.

`jira.issue.edit` también admite `append_description: {value: "Se asigno!"}`:
lee la descripción actual de Jira y agrega un párrafo conservando su documento ADF,
en lugar de concatenar el snapshot del evento. Una descripción null se crea vacía;
si Jira omite el campo o devuelve un formato desconocido, falla sin escribir.
Puede combinarse con otros `fields`, pero no con `fields.description` (reemplazo).
El texto tiene el límite del conector y el documento final el límite de 32 KB.
Es una lectura seguida de una edición, no un append atómico de Jira: una edición
concurrente entre ambas puede competir con esta escritura, igual que los demás edits.
No se reintentan a ciegas escrituras ambiguas. Ver
[flujo de asignación](examples/jira-assignment-append.yaml), guardado sin activación automática.

Resultados de pasos previos: `_steps["0"].key`, `_steps["1"].fields.status.id`, etc. `_steps`
es un objeto de resultados por clave numérica, no una lista: el paso visible 1 usa la clave `"0"`.
El contexto lo construye el worker
desde Postgres; un evento entrante no puede sobrescribirlo. Las cadenas se reanudan desde el paso pendiente.
Ejemplo: examples/jira-create-from-source.yaml (borrador, nunca activado automáticamente).

En Studio, **Ticket a editar** permite escribir una clave Jira o elegir con `{ }` el dato
que la contiene, incluido `issue.fields.parent.key` para editar el padre. **Agregar campo**
abre directamente su editor de nuevo valor; no hay que elegir un modo de binding para
esta operación. Los controles técnicos se conservan en Opciones avanzadas.
Los selectores muestran datos del evento y resultados anidados de acciones anteriores,
con ejemplos históricos protegidos cuando existen. Ver [contexto y edición en Studio](docs/studio.md#contexto-y-ejemplos-de-pasos-anteriores).

`GET /v1/rules/{name}/versions/{version}/context?before_step=N` requiere `api:read`.
Devuelve campos y ejemplos de una sola ejecución de esa versión, nunca pasos `>= N`,
resultados fallidos, `_steps` inyectado en el webhook ni eventos redactados. La búsqueda
revisa como máximo diez ejecuciones, con índices de la migración 005; inspecciona hasta
100 campos y 2.000 nodos por resultado previo, previews escalares de 120 caracteres y
el catálogo acotado del evento de esa misma ejecución. No ejecuta acciones ni llama proveedores.
Sin resultados devuelve `sample:false`, no valores ficticios. El editor descarta muestras
si cambian el trigger, el filtro o los pasos anteriores; indica siempre versión y ejecución.

El conector requiere JIRA_CLIENT_ID, JIRA_CLIENT_SECRET y JIRA_CLOUD_ID para OAuth,
o JIRA_API_TOKEN y JIRA_CLOUD_ID como alternativa estática; no combinar ambos modos.
Usa exclusivamente https://api.atlassian.com/ex/jira/{cloudId}/rest/api/3.
No se exige ni agrega una etiqueta de alcance a los tickets. El proyecto real de cada tarjeta se verifica antes de actuar.
Los proyectos/campos se especifican en el YAML y Jira aplica los permisos de la cuenta.
Se conserva la validación del DSL: project/status/security/reporter no se pueden inyectar por fields.
JIRA_MAX_DAILY_WRITES (default 100) limita globalmente intentos de escritura en 24 horas mediante Postgres.
Cada intento reserva presupuesto antes del envío, incluso si luego queda ambiguo o recibe 429.

Los timeouts y 5xx de escrituras van a needs_review; sólo lecturas previas y 429 admiten reintento acotado.
Crear tickets y comentarios incluye una propiedad automation.operation para ayudar a correlacionar resultados.
Su formato nuevo es un objeto versionado: schema_version, id (operación), rule_name, rule_version,
rule_sha256, run_id y step. Lo construye el worker desde la revisión inmutable; el evento y las
variables YAML no pueden definirlo. Las propiedades históricas de texto/id siguen siendo evidencia
del formato anterior: no se reescriben ni se usan como autorización. Las propiedades de Jira son
visibles a usuarios/apps con acceso: no contienen secretos. El histórico completo permanece en Postgres.

### Autenticación Jira: OAuth y API token

La opción recomendada es OAuth 2.0 de cuenta de servicio: configurar `JIRA_CLIENT_ID`,
`JIRA_CLIENT_SECRET` (o `JIRA_CLIENT_SECRET_FILE`) y `JIRA_CLOUD_ID` (UUID del sitio).
Crear una credencial **OAuth 2.0** en **Directory → Service accounts → Create credentials**,
con los permisos de la cuenta y scopes indicados abajo. No es una aplicación OAuth 3LO:
no necesita email, callback ni refresh token.

El worker obtiene el token con `client_credentials` en
`https://auth.atlassian.com/oauth/token`, lo conserva sólo en memoria y lo renueva antes
del vencimiento. La identidad de servicio permanece estable entre renovaciones. Una lectura
con `401` puede refrescar y reintentarse una vez; las escrituras no se reenvían automáticamente.
No configurar `JIRA_API_TOKEN` junto con OAuth. `JIRA_WEBHOOK_SECRET` sigue siendo independiente.

```dotenv
JIRA_CLIENT_ID=<client-id-de-la-cuenta-de-servicio>
JIRA_CLIENT_SECRET_FILE=/run/secrets/jira_client_secret
JIRA_CLOUD_ID=<cloud-id-del-sitio-jira>
```

Referencia: [OAuth de cuentas de servicio (Atlassian)](https://support.atlassian.com/user-management/docs/create-oauth-2-0-credential-for-service-accounts/).

#### Alternativa compatible: API token estático

Jira Relay envía un API token de cuenta de servicio como `Authorization: Bearer <token>`.
Bearer es la forma de enviar el API token, no otra credencial ni un flujo OAuth.
El conector no requiere email. No asumir que un token personal anterior admite este mecanismo:
validar la nueva credencial con una consulta de sólo lectura antes de ponerla en servicio.

Para crear la credencial, un administrador de la organización debe:

1. Entrar a [Atlassian Administration](https://admin.atlassian.com/) y seleccionar la organización.
2. Abrir **Directory → Service accounts** y seleccionar o crear una cuenta dedicada a Jira Relay.
3. Darle acceso a Jira y únicamente a los proyectos que debe operar, con los permisos necesarios.
4. Elegir **Create credentials → API token → Next**; no seleccionar OAuth 2.0.
5. Indicar un nombre, por ejemplo `jira-relay-production`, y un vencimiento según la política
   de la empresa (Atlassian permite entre 1 y 365 días). Registrar responsable y fecha de renovación.
6. En **Select token scopes**, seleccionar estos tres scopes **Classic**:

   | Scope | Uso |
   | --- | --- |
   | `read:jira-work` | Consultar tickets y datos asociados para lecturas y comprobaciones previas. |
   | `write:jira-work` | Crear/clonar, editar, comentar, vincular y cambiar estados de tickets. |
   | `read:jira-user` | Identificar mediante `/myself` la cuenta del token y excluir globalmente sus eventos. |

7. Revisar y crear el token. Guardarlo inmediatamente en el gestor de secretos de la empresa:
   Atlassian lo muestra una sola vez. No copiarlo al chat, al repositorio ni a reglas YAML.

Los dos scopes `jira-work` se verificaron en el selector de Atlassian el 15 de octubre de 2026;
`read:jira-user` se agrega por el contrato documentado de [Get current user](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-myself/#api-rest-api-3-myself-get).
No agregar scopes de administración de Jira o usuarios, Confluence o gestión de webhooks para
las operaciones actuales. El webhook entrante usa su propio secreto de firma, no este token.

**Importante:** `write:jira-work` es amplio y también permite operaciones de borrado.
Aunque Jira Relay no implemente acciones de borrado, el scope por sí solo no las impide.
No conceder a la cuenta de servicio permisos de **Delete Issues** ni otros permisos de borrado
que no necesite. Los scopes no reemplazan los permisos de Jira: el acceso efectivo depende de
ambos. Los límites de escritura del motor no sustituyen los permisos del proveedor.

Configuración mínima del conector usando un archivo montado por el operador:

```dotenv
JIRA_API_TOKEN_FILE=/run/secrets/jira_api_token
JIRA_CLOUD_ID=<cloud-id-del-sitio-jira>
```

El archivo contiene solamente el token, sin comillas ni el prefijo `Bearer`; la aplicación
agrega el prefijo. `JIRA_CLOUD_ID` es el identificador del sitio Jira, no el de la organización.
También se puede inyectar `JIRA_API_TOKEN` directamente mediante el gestor de secretos.
Si está definida, esa variable tiene prioridad sobre `JIRA_API_TOKEN_FILE` sin abrir el archivo;
un valor directo vacío no activa el fallback. La configuración se carga al arrancar:
reiniciar los procesos consumidores al rotar el token.

Antes de desplegar, verificar una lectura de un ticket permitido y confirmar que los permisos
de proyecto cubren las acciones de los flujos. No revocar la credencial anterior hasta validar
la nueva. No confundir las pruebas locales con una validación real de autenticación o permisos.

Referencia: [API tokens de cuentas de servicio y autenticación Bearer (Atlassian)](https://support.atlassian.com/user-management/docs/manage-api-tokens-for-service-accounts/).

### Conectar el webhook real de Jira

El operador publica la API por HTTPS y registra en Jira la URL
`https://<host-api>/webhooks/jira`. Gestión y webhook comparten proceso/puerto; el WAF/proxy
decide qué rutas son públicas y cuáles restringir. Configurar en Jira el mismo secreto que `JIRA_WEBHOOK_SECRET`/`_FILE`
y seleccionar los eventos y el alcance que la instalación necesita. El motor no registra
webhooks automáticamente ni crea sitios, proyectos o cuentas Atlassian.

El contrato de ingreso exige:

- Body JSON nativo, sin envolverlo en `source/payload`; `webhookEvent` contiene el tipo de evento.
- `X-Hub-Signature: sha256=<hex>`: HMAC-SHA256 sobre los **bytes exactos** del body usando el
  secreto del webhook. No es el token Bearer de Jira ni el token de nuestra API.
- `X-Atlassian-Webhook-Identifier`: identidad estable de la entrega, de 1 a 250 caracteres.
  Reintentos deben conservar identificador y body; mismo ID con bytes distintos produce 409.

El proxy no debe transformar JSON ni descartar esos headers. Una modalidad de webhook que
no entregue firma/identificador compatibles **no se conecta quitando la validación**: hace falta
resolver esa integración antes. Validar desde Jira un evento humano y otro generado por el
servicio: el primero debe llegar y ejecutarse si coincide; el segundo debe quedar ignorado.
Consultar los [webhooks de Jira Cloud](https://developer.atlassian.com/cloud/jira/platform/webhooks/)
para el mecanismo de registro disponible en la organización. Firma válida no implica actor
inequívoco: los eventos sin actor admitido quedan retenidos por la política global.

### Identificación legible de tickets

Al crear o clonar, el motor agrega `automation-flujo-<name>` usando exactamente el `name` del YAML.
Ejemplo: `name: crear-repositorio` produce `automation-flujo-crear-repositorio`. La etiqueta identifica
el flujo creador, no la última regla que tocó el ticket. Revisiones del mismo flujo mantienen etiqueta;
renombrar `name` crea otra regla. No se agregan UUIDs ni timestamps a las etiquetas.

`automation-managed` no tiene tratamiento especial: no se exige, no se agrega y puede quitarse
como cualquier etiqueta de negocio. La actualización no recorre tickets existentes para borrarla.
Usar identidad dedicada y permisos de proyecto en Jira.

Las etiquetas de negocio se conservan al crear; duplicados se eliminan. Clonar conserva las etiquetas
seleccionadas salvo las del flujo de origen, que se reemplazan por la del flujo creador de la copia.
El prefijo `automation-flujo-` está reservado: el YAML no puede inventarlo al crear ni alterarlo al editar
o transicionar. Al reemplazar labels en un ticket existente se deben conservar
sus etiquetas de flujo; un cambio que las elimine se rechaza. Máximo 30 etiquetas incluyendo las reservadas:
si se excede, se rechaza antes de escribir, sin truncar ni borrar etiquetas de negocio.

La actualización no recorre ni reetiqueta tickets históricos automáticamente. Para migrarlos, inventariar
las claves exactas, verificar la regla original en el histórico y preservar sus etiquetas de negocio.

### Controles de alcance antes de escribir

La política es parte del backend; no depende de una confirmación en la GUI ni de variables de
despliegue. `POST /v1/rules/validate` comprueba seguridad incluso con `targets=false`. Guardar un
borrador sigue permitido, pero **activarlo exige superar estos controles**:

- Toda regla con efectos (incluidos pipelines) debe acotar el origen con `eq` o un `in` no vacío
  sobre `issue.key`, `issue.fields.project.key` o `issue.fields.project.id`. `exists`, `ne`, `not`,
  `contains` y listas vacías no cuentan como alcance. No se imponen proyectos por env var.
- En `any`, cada rama debe garantizar alcance. Un `all` exterior puede aportar una restricción
  común. El análisis es conservador: no intenta resolver equivalencias lógicas arbitrarias.
- Si el trigger es `jira:issue_updated`, debe existir un `some` positivo sobre `changelog.items`
  cuyo **mismo `where`** garantice `field`/`fieldId` y una comparación positiva `eq`/`in` sobre
  `from`, `to`, `fromString` o `toString`. La mera presencia del changelog o del campo no alcanza.
  El caso específico **asignar a cualquier persona** admite `fieldId`/`field = assignee`
  con `to exists true`, `to ne null` y `to ne ""`, todos garantizados en ese mismo `where`.
  Así no se hardcodean account IDs: asignar y reasignar coinciden, desasignar no.
- Una transición necesita `from_status_id` y `to_status_id` distintos. Se comprueba el estado
  actual antes del envío, aunque Jira puede cambiar concurrentemente después de esa lectura.
- Los destinos de acciones Jira deben ser una clave literal (incluido cross-project) o una
  referencia a un único valor: `issue.key`, `issue.fields.parent.key`, un custom field que
  contenga una clave, o un resultado anterior como `_steps["0"].fields.parent.key`.
  La gramática no permite búsquedas, comodines ni iteraciones. Antes de cualquier HTTP o
  reserva de escritura se valida que el resultado sea exactamente una clave Jira; listas,
  objetos, null, IDs numéricos y JQL fallan. No se aceptan templates parciales, pasos futuros
  ni fallbacks. Esto se aplica también a `source`, `parent`, `inward` y `outward` en reglas con escrituras.
- Las reglas exclusivamente `jira.issue.get` no necesitan alcance de escritura. En el origen
  autenticado `test` se permite además acotar mediante un `test_id` explícito con `eq`/`in`;
  no es una excepción general a las verificaciones de destinos ni transiciones.

Ejemplo mínimo de filtro de una escritura disparada por actualización:

```yaml
when:
  all:
    - {path: issue.fields.project.key, op: eq, value: SUPPORT}
    - some:
        path: changelog.items
        where:
          all:
            - {path: field, op: eq, value: status}
            - {path: fromString, op: eq, value: Open}
            - {path: toString, op: eq, value: Approved}
```

La API devuelve 422 con `unsafe_write_scope`, `unsafe_change_scope`, `unsafe_transition` o
`unsafe_action_target`. El control se repite al activar, distribuir eventos y antes de nuevas
acciones del worker. Revisiones inseguras antiguas dejan de producir ejecuciones; si ya había
una tarea encolada, se detiene con `unsafe_rule_execution_blocked`. Un pipeline ya enviado se
sigue consultando para conocer su resultado, pero no habilita nuevas escrituras inseguras.
No se reescriben YAML históricos ni sus checksums; hay que crear una revisión corregida.

Estos controles **no son una prueba de ausencia de bucles**, ni un límite de cantidad de tickets
afectados por una regla válida. Un proyecto grande aún puede generar muchos eventos coincidentes;
los presupuestos globales de Jira/Bitbucket siguen activos. La deduplicación cubre la misma entrega,
no la causalidad de nuevos webhooks ni ciclos A → B → A. No hay detector general de ciclos entre
reglas/automatizaciones externas en esta versión. Revisar simulaciones históricas y el alcance de
cada regla antes de activarla, y usar una cuenta de servicio con permisos mínimos.

### Exclusión global de eventos del servicio

No es una opción de cada regla ni una lista manual en env vars. Antes de ejecutar tareas con el
conector Jira configurado, el worker identifica la cuenta del token mediante `GET /rest/api/3/myself`
y persiste su `accountId` en DB. Si no puede verificarla, no despacha acciones; requiere un token
con `read:jira-user` además de los scopes de trabajo. La API no lo necesita para recibir webhooks.
Si una consulta de ticket devuelve 200 pero `/myself` devuelve 401 con `scope does not match`,
la credencial no habilita la identificación de su cuenta: emitir un token con `read:jira-user` y
actualizar su fuente de secreto. No desactivar la verificación ni inferir el sujeto desde un ticket.

La migración `004_service_actors.sql` agrega el registro de identidades y la asociación con un
SHA-256 de la credencial y el Cloud ID. No se guarda el token en esa tabla. Las identidades y
asociaciones son append-only para el runtime: volver a correr `deploy/runtime-grants.sql` después
de migrar. Las cuentas anteriores no se eliminan al rotar credenciales, para cubrir eventos demorados.
Todos los workers/servicios comparten este registro; no hay una consulta remota por evento.

Antes de evaluar reglas, el ingreso nativo y `POST /v1/events` aplican la misma política:

- Actor registrado: guardar payload e identidad de entrega, marcar `ignored_service_actor` y no
  evaluar reglas ni crear ejecuciones. Se aplica a creaciones/clonados, actualizaciones, borrados,
  comentarios, transiciones, vínculos, worklogs y otros eventos, independientemente del flujo/proyecto.
- Jira sin actor inequívoco: conservar como `held_actor_unknown`, sin ejecución ni replay. Algunos
  tipos de webhook pueden no identificar quién actuó; no se infiere desde el creador del ticket.
- Un evento posterior de una persona sobre un ticket creado por el servicio se procesa normalmente.
  Tampoco se confunde al autor original de un comentario/worklog con su editor o con quien lo borró.
- Replay manual devuelve 409. El worker reevalúa la exclusión para trabajos encolados antes de
  registrar la identidad, cancela pendientes y vuelve a comprobar el actor antes de reservar escrituras
  Jira o Bitbucket. Las llamadas ya enviadas no pueden deshacerse; se permite observar un pipeline
  ya iniciado, pero no continuar con nuevas acciones del evento excluido.

La GUI muestra el motivo y deshabilita ejecutar esos eventos; el backend lo impide aunque se llame
directamente a la API. `GET /v1/events/{id}` incluye `execution_blocked_reason` y `last_error`.
La exclusión funciona también para el origen autenticado `test` si su payload identifica una cuenta
del servicio. Los eventos sintéticos sin actor siguen siendo una función privilegiada del operador;
la API autenticada no prueba que un actor declarado por ese operador sea auténtico.

Usar una cuenta **dedicada**: cualquier acción manual u otra integración que use esa misma cuenta
también será ignorada. No usar un usuario personal. No se corrigen ni revierten efectos históricos.
Este control evita reentrada de las cuentas conocidas del servicio, no demuestra ausencia de ciclos
mediante otras cuentas, Jira Automation u otros sistemas. Los filtros de alcance y cuotas siguen activos.

### Límites de recuperación

No hay reconciliación automática Jira aún; /reconcile sólo acepta pipelines de Bitbucket.
La exclusión de cuentas del servicio es global y persistente, descrita arriba.
La comprobación de estado antes de transicionar no es atómica con cambios concurrentes en Jira.

La suite incluye Postgres real y proveedor HTTP simulado para fallos controlados.
El JSON Schema del DSL está publicado; no es un schema exhaustivo de los payloads Jira ni
garantiza todos los custom fields de una organización. Los gates operativos siguen pendientes;
más acciones no implica production-ready.

## Desarrollo

1. uv sync
2. python3 tools/local_config.py (genera .env local, ignorado por Git, sin mostrar secretos).
3. docker compose -p automation-dev -f compose.dev.yaml up -d --build
4. API en http://127.0.0.1:8088; /v1/schema y /openapi.json requieren bearer.

compose.dev.yaml tiene credenciales de prueba; nunca usarlo como plantilla de producción.
El nombre de proyecto separado evita reutilizar accidentalmente volúmenes del laboratorio.

Pruebas unitarias: uv run pytest tests/test_dsl.py -q.
Integración: crear la base dedicada automation_test en Postgres local y ejecutar:

    TEST_DB_HOST=127.0.0.1 TEST_DB_PORT=55439 TEST_DB_NAME=automation_test TEST_DB_USER=automation TEST_DB_PASSWORD=local-test-only uv run pytest -q

Las pruebas rechazan bases con otro nombre; nunca ejecutarlas contra la base del despliegue.
Las pruebas de migraciones crean bases y un rol temporales en ese servidor: usar PostgreSQL
descartable con permisos CREATEDB/CREATEROLE (o el superusuario de `tools/selftest.py`).
Usan Postgres real y mock HTTP del proveedor para fallos controlados. Eso no sustituye la prueba E2E con Jira/Bitbucket reales.

## Instalación portable

### Instalación como servicios independientes (sin Compose)

Requisitos de ejecución: PostgreSQL (CI usa 17), red de cada proceso hacia la DB y HTTPS saliente
del worker hacia los proveedores habilitados. Python 3.12+ si se ejecuta desde fuentes; la imagen
incluye Python y dependencias. Node 22 se usa para construir Studio, no en su runtime.
No se necesita instalar Docker CLI, AWS SDK ni kubectl dentro de los servicios.

1. Crear una base **dedicada** con esquema `public` vacío y dos roles: propietario migrador y
   runtime sin superusuario/DDL. No usar el mismo usuario para ambos en producción.
2. Construir imágenes desde el mismo commit: `docker build -t jira-relay:<release> .` y,
   si se usa GUI, `docker build -f frontend/Dockerfile -t jira-relay-studio:<release> .`.
   Publicarlas en el registro del operador y desplegar por digest. Desde fuentes, instalar
   `uv sync --frozen --no-dev`; los comandos siguientes corren dentro del entorno instalado.
3. Inyectar los [parámetros de cada servicio](#mapa-por-servicio), no un `.env` completo en todos.
   Usar el mismo `TENANT_ID` y DB en API y worker. Nunca montar la credencial del
   migrador en procesos runtime. Sólo se carga un dotenv si se configura `ENV_FILE`;
   no se consulta un gestor cloud desde el motor.
4. Con variables del **migrador**, ejecutar `python -m app.migrate --status` y
   `python -m app.migrate`. No iniciar runtime antes de completar este trabajo.
5. Conectar `psql` como propietario y ejecutar:

   ```sh
   psql -v ON_ERROR_STOP=1 -v runtime_role=jira_relay_runtime -f deploy/runtime-grants.sql
   ```

   La conexión de `psql` se configura fuera del producto (por ejemplo `PGHOST`, `PGPORT`,
   `PGDATABASE`, `PGUSER` y un `PGPASSFILE` privado). `DB_*` son parámetros de la aplicación,
   **no** variables que `psql` lea automáticamente. El archivo SQL está en el checkout, no
   dentro de la imagen mínima del backend. Volver a conceder permisos tras nuevas migraciones.
6. Iniciar la **API** con su bloque de variables y el comando documentado abajo. En el primer
   arranque registra el administrador de `ADMIN_BOOTSTRAP_TOKEN`/`_FILE`; conservarlo en un
   gestor de secretos. Verificar `/readyz` y una consulta autenticada a `/v1/schema`.
7. Iniciar el **worker** con sus credenciales de proveedor; validar `/myself`, heartbeat y
   permisos efectivos. La **API** ya recibe webhooks con su secreto HMAC. Sobrescribir el healthcheck
   de la imagen al ejecutar worker con `python -m app.health worker`; migración es un job,
   sin probe de servidor. En API, `/healthz` y `/readyz` usan el puerto elegido.
8. Publicar la API mediante HTTPS con límites de tasa/conexiones/tiempos. Restringir gestión
   por WAF/proxy/red según la política del operador; mantener DB privada. Registrar el webhook
   y probar su recorrido según la sección anterior. La aplicación conserva Bearer y HMAC independientes.
9. Emitir un token delegado, instalar Studio según [su guía](docs/studio.md) y configurar su
   backend/origen público al arrancar. No darle acceso a DB ni credenciales Jira/Bitbucket.
10. Guardar, validar y simular reglas antes de activarlas. Ensayar backup/restauración, alertas,
    rotación de credenciales y carga de la instalación antes de habilitar uso compartido real.

Cada comando es un proceso separado con su propio entorno. La API escucha en 8080 por defecto
dentro de la imagen; los otros servicios pueden usar el mismo puerto en sus propias redes.
Cambiar `--port` exige adaptar la probe (la probe empaquetada `app.health api` consulta 8080).
Dar al backend un cierre ordenado de 150 segundos; Studio tiene su propio shutdown de 30 segundos.
El operador controla exposición, TLS, recursos, despliegue, secretos y agenda de mantenimiento.

### Empaquetado opcional con Compose

La misma imagen ejecuta API (comando por defecto), worker (`python -m app.worker`), migraciones
(`python -m app.migrate`) y mantenimiento (`python -m app.retention`). Sólo necesita PostgreSQL y
acceso HTTPS saliente a los proveedores habilitados. No invoca AWS, kubectl, SSM ni Docker desde el motor.
Usar una base dedicada por instalación. TENANT_ID es obligatorio e inmutable para esa base.

Para Compose con PostgreSQL administrado por el operador:

1. Preparar una base y dos roles: propietario para migraciones, runtime sin permisos DDL/superuser.
2. Crear un archivo de configuración a partir de .env.example en una ruta nueva, sin sobrescribir .env existente.
3. Configurar `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` (runtime) y `DB_MIGRATION_USER` (migrador)
   en el archivo de instalación. Ejecutar `uv run python tools/init_installation.py`: pide ambas
   contraseñas sin mostrarlas y genera los archivos `db_runtime_password` y `db_migrator_password`.
   Se niega a sobrescribir un directorio existente. Genera un único archivo `admin_bootstrap_token`;
   los tokens de conectores opcionales empiezan vacíos.
4. Configurar cloud ID y credenciales OAuth de servicio Jira, y Access Token Bitbucket sin email. Cargar credenciales de conectores en los
   archivos privados, nunca en YAML de reglas. Los secretos se resuelven primero desde NAME;
   sólo si no está definida se lee el archivo indicado por NAME_FILE. Si ambas existen gana NAME,
   sin abrir el archivo. Un valor directo vacío no activa el fallback y los secretos obligatorios fallan.
5. Construir `docker build -t automation-api:1.0.0 .` o usar una imagen de un registro privado validado.
6. Ejecutar primero `docker compose --env-file install.env run --rm migrate` con el archivo elegido.
7. Aplicar deploy/runtime-grants.sql como propietario, pasando runtime_role al cliente psql.
   Otorga DML en datos y lectura de metadatos de instalación/migración; no otorga CREATE ni propiedad.
   La aplicación no crea tablas al arrancar. Usar este script sólo en una base dedicada al producto.
8. Ejecutar `docker compose --env-file install.env up -d`. La migración idempotente debe finalizar antes de los servicios.
9. Con el administrador inicial, emitir un token delegado para la GUI mediante `POST /v1/auth/tokens`,
   guardarlo en `gui_api_token` y preparar la contraseña de login antes de habilitar el perfil `studio`.
   Retirar el bootstrap del proceso si se desea; no usarlo como credencial de la GUI.

El Compose principal no incluye una base con password prefijado: puede apuntar a PostgreSQL externo,
otro contenedor o un servicio administrado. `DB_HOST` se interpreta desde los contenedores; localhost
no es el host ni otro contenedor. El driver recibe parámetros separados, no una URL construida
manualmente: las contraseñas no requieren URL-encoding. `DATABASE_URL` y `DATABASE_URL_FILE`
se rechazan explícitamente, sin compatibilidad legacy. Esta configuración no agrega controles SSL
ni certificados; tampoco garantiza por sí sola que la conexión esté cifrada o sin cifrar.

La carpeta secrets tiene modo 0700; los archivos 0444 permiten lectura al UID 10001 dentro de los
contenedores a los que Compose los monta. El directorio privado impide acceso de otros usuarios del host.
No mover esos archivos a directorios públicos. En Linux validar propietarios/montajes; en plataformas
administradas usar su mecanismo de secretos. Cambiar variables/archivos requiere reiniciar el consumidor
afectado. Los tokens de nuestra API se emiten, revocan y rotan en DB sin reiniciar la API; si cambia el
token del gateway, actualizar su secreto y reiniciar el gateway antes de confirmar. No hay recarga en
caliente de archivos ni período de doble clave de webhook en esta versión.

Compose publica una única API en 127.0.0.1:8088 (`API_PORT`). Poner delante un proxy/WAF HTTPS
con límites de conexión, tiempo y tasa, y reglas de acceso por ruta. `app.main:app` registra
gestión y webhook; no existe otro punto de entrada ni una env var `API_SURFACE` para separarlos.
No montar el socket Docker en ningún componente. El frontend no es necesario para operar.

| Plataforma | Mapeo del mismo contrato de ejecución |
|---|---|
| Compose | compose.yaml; servicios separados, migración previa y secretos por archivo |
| Kubernetes | Deployments para API/worker, Job previo para migrar, Secret/volumen, probes; ingress/WAF con política por ruta |
| ECS | Services para API/worker, tarea puntual previa para migrar, secretos inyectados; balanceador/WAF con política por ruta |

No hay manifiestos K8s/ECS genéricos certificados en esta release. No requieren cambios de código,
pero deben probarse en la plataforma elegida. Si se agregan hostnames en Kubernetes, usar external-dns
en los manifests como fuente de verdad. Los despliegues particulares no deben copiarse como producto universal.

## Parámetros de configuración (inventario completo)

API, worker, migrador y mantenimiento aceptan `ENV_FILE` explícito o variables inyectadas
por el operador. No se busca `.env` implícitamente. Studio usa su configuración independiente
y no carga `ENV_FILE`; el operador debe inyectar sus variables o secretos `_FILE`.
Los parámetros no secretos se configuran directamente. Los secretos marcados con **†** admiten
el nombre directo o el mismo nombre terminado en `_FILE`: gana el directo si está definido,
incluso si está vacío; sólo si no existe se lee el archivo UTF-8. No hay fallback a una credencial
anterior cuando el valor directo está vacío. Reiniciar los procesos afectados después de cambios.

### Mapa por servicio

Plantillas independientes para copiar/adaptar fuera de Git:
[api](deploy/env/api.env.example), [worker](deploy/env/worker.env.example),
[migrate](deploy/env/migrate.env.example) y [studio](deploy/env/studio.env.example).
Usan archivos de secretos montados; cada secreto admite también su env var directa.
No son archivos Compose ni se cargan automáticamente: el gestor de procesos inyecta cada uno
en el servicio correspondiente. La plantilla de worker deja conectores deshabilitados hasta configurarlos.

Cada bloque es completo e independiente; no requiere Compose ni heredar otros bloques.
El operador inyecta estas variables con su plataforma de servicios. Los valores entre `<...>`
deben reemplazarse; los secretos pueden entregarse directamente o con la alternativa `_FILE`.
Las rutas de archivos las define el despliegue, no la aplicación. No guardar secretos reales en Git.

#### `api`: gestión y webhooks

Arranque: `uvicorn app.main:app --host 0.0.0.0 --port 8080 --no-access-log --no-proxy-headers --limit-concurrency 100 --timeout-graceful-shutdown 120`.
Único punto de entrada HTTP: gestión `/v1/*`, webhook `/webhooks/jira` y probes.

```dotenv
TENANT_ID=mi-empresa
DB_HOST=<host-postgresql>
DB_PORT=5432
DB_NAME=jira_relay
DB_USER=jira_relay_runtime
DB_PASSWORD=<secreto-runtime>
ADMIN_BOOTSTRAP_TOKEN=<secreto-inicial-minimo-32-caracteres>
JIRA_WEBHOOK_SECRET=<secreto-firma-minimo-32-caracteres-distinto-del-bootstrap>
AUTH_ROTATION_TTL_SECONDS=3600
MAX_BODY_BYTES=10485760
# Opcionales: permiten mostrar el estado del conector en el catálogo.
JIRA_CLOUD_ID=<cloud-id-jira>
JIRA_CLIENT_ID=<client-id-jira>
JIRA_CLIENT_SECRET=<client-secret-jira>
# Opcional: indica credenciales declaradas aquí; la autorización remota la hace Bitbucket.
BITBUCKET_API_TOKEN=<token-bitbucket>
```

Bootstrap sólo es necesario para registrar el primer administrador en una DB no inicializada.
Después no se vuelve a usar para autenticar y puede retirarse. El secreto de firma se mantiene
en la API; una credencial Bearer válida no reemplaza la firma HMAC del webhook, ni viceversa.
Los proyectos y campos Jira se definen en las reglas; no hay allowlists Jira en env vars.
La API no necesita credenciales Jira para recibir eventos o gestionar reglas. El catálogo
informa configuración declarada en este proceso, **no salud ni credenciales del worker**:
Jira sin credenciales locales devuelve `configured:null` (sin verificar), no “deshabilitado”.
No copiar secretos del worker a la API sólo para cambiar un indicador de la GUI.

#### `worker`: ejecutor

Arranque: `python -m app.worker`.

```dotenv
TENANT_ID=mi-empresa
DB_HOST=<host-postgresql>
DB_PORT=5432
DB_NAME=jira_relay
DB_USER=jira_relay_runtime
DB_PASSWORD=<secreto-runtime>
JIRA_CLOUD_ID=<cloud-id-jira>
JIRA_CLIENT_ID=<client-id-jira>
JIRA_CLIENT_SECRET=<client-secret-jira>
JIRA_MAX_DAILY_WRITES=100
# Sólo si se utiliza Bitbucket.
BITBUCKET_API_TOKEN=<token-bitbucket>
BITBUCKET_MAX_DAILY_WRITES=100
# Omitir para usar el hostname; si se configura, debe ser único por worker.
# WORKER_ID=<identidad-unica-del-worker>
```

No necesita bootstrap, secreto de webhook ni configuración Studio. No expone un servidor HTTP.
No necesita `MAX_BODY_BYTES` ni `AUTH_ROTATION_TTL_SECONDS`.
Para deshabilitar Jira, omitir sus credenciales. Para despachar pipelines Bitbucket, el worker
necesita un Access Token con Pipelines Read/Write sobre el repositorio de la regla.
Se envía como Bearer sin email; no requiere permisos de webhook para lanzar o consultar runs.

#### `migrate`: trabajo de migración previo al arranque

Arranque: `python -m app.migrate`. Es un trabajo previo, no un servicio permanente.

```dotenv
TENANT_ID=mi-empresa
DB_HOST=<host-postgresql>
DB_PORT=5432
DB_NAME=jira_relay
DB_USER=jira_relay_migrator
DB_PASSWORD=<secreto-migrador>
```

No recibe credenciales de proveedores, bootstrap ni variables Studio. Se ejecuta con
`python -m app.migrate` y termina. `DB_MIGRATION_USER` no es una variable leída por Python:
es una entrada de Compose que éste transforma en `DB_USER` exclusivamente para este servicio.

#### `studio`: jira-relay-studio

Arranque: `python -m tools.studio_server` (assets compilados incluidos en la imagen).
No recibe configuración de DB, tenant ni proveedores:

```dotenv
STUDIO_BACKEND_URL=https://<host-api>
STUDIO_PUBLIC_ORIGIN=https://relay.<dominio-de-tu-empresa>
STUDIO_BACKEND_TOKEN=<token-delegado-de-nuestra-api>
STUDIO_AUTH_MODE=basic
STUDIO_LOGIN_USERNAME=operator
STUDIO_LOGIN_PASSWORD=<secreto-minimo-16-caracteres>
STUDIO_HOST=0.0.0.0
STUDIO_PORT=8080
# Opcional: normalmente se deja el directorio incluido en la imagen.
# STUDIO_STATIC_DIR=/app/frontend/dist
```

`gui_api_token` contiene un token delegado de nuestra API, normalmente con
`api:read,rules:write,flows:operate`, **sin `auth:admin`**. Para administrar credenciales se ingresa
el token administrador en Accesos; no se configura otra env var ni se monta el bootstrap en Studio.
Para una consola local se puede usar `STUDIO_PUBLIC_ORIGIN=http://127.0.0.1:8093` conservando
el login Basic. Los límites generales del gateway son fijos: 10 MiB por solicitud y 32 MiB por
respuesta; no hay un `MAX_BODY_BYTES` propio de Studio.

#### Resumen de secretos por servicio (Compose principal)

| Servicio | Archivos montados en `/run/secrets/` |
| --- | --- |
| `api` | `db_runtime_password`, `admin_bootstrap_token`, `jira_webhook_secret`, `jira_client_secret`, `jira_api_token`, `bitbucket_api_token` |
| `worker` | `db_runtime_password`, `jira_client_secret`, `jira_api_token`, `bitbucket_api_token` |
| `migrate` | `db_migrator_password` |
| `studio` | `gui_api_token`, `studio_login_password` |

Cada secreto puede sustituirse por su env var directa, sin `_FILE`, en otro empaquetado.
El Compose principal utiliza archivos deliberadamente; exportar un secreto en el host no lo
inyecta automáticamente si no está mapeado en el manifest. No configurar ambas variantes.

`AUTOMATION_IMAGE`, `STUDIO_IMAGE`, `AUTOMATION_SECRETS_DIR`, `DB_MIGRATION_USER`,
`API_PORT`, `STUDIO_BIND_ADDRESS` y `STUDIO_PUBLISHED_PORT` son entradas
de **Compose**, no variables que deban agregarse a todos los contenedores. Ver su inventario abajo.
El archivo [.env.example](.env.example) concentra las entradas del Compose principal, incluido
el perfil opcional Studio; no es un archivo para inyectar entero en cada servicio.

La tarea opcional de retención recibe `TENANT_ID`, `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`
y `DB_PASSWORD`/`DB_PASSWORD_FILE`, sin credenciales de proveedores.

### Base de datos e instalación

| Parámetro | Obligatorio / valor predeterminado | Uso |
| --- | --- | --- |
| `TENANT_ID` | Obligatorio; 1–100 caracteres seguros | Identidad inmutable de esta instalación en su DB. No es el Cloud ID de Jira. |
| `ENV_FILE` | Opcional; sin búsqueda automática | Ruta de dotenv UTF-8 montado de hasta 1 MiB, cargado al iniciar; variables del proceso ganan por clave, incluso vacías. No es configuración de Studio. |
| `DB_HOST` | Obligatorio | Host de PostgreSQL visto desde el proceso/contenedor. |
| `DB_PORT` | `5432`; rango 1–65535 | Puerto de PostgreSQL. |
| `DB_NAME` | Obligatorio | Base dedicada a la instalación. |
| `DB_USER` | Obligatorio | Usuario de este proceso: runtime o migrador, según el servicio. |
| `DB_PASSWORD` **†** | Obligatorio, sin valor predeterminado | Contraseña del usuario indicado; alternativa `DB_PASSWORD_FILE`. |

API, worker, migraciones, retención y probe del worker usan estos mismos nombres.
Sólo el migrador recibe credenciales de propietario/DDL. No hay parámetros SSL propios en este cambio.
`DATABASE_URL` y `DATABASE_URL_FILE` se rechazan, aunque también estén configurados los parámetros nuevos.

### API, eventos y worker

| Parámetro | Obligatorio / valor predeterminado | Uso |
| --- | --- | --- |
| `ADMIN_BOOTSTRAP_TOKEN` **†** | Sólo en la primera inicialización de la API de gestión | Registra el primer administrador; después se ignora y puede retirarse. No es el token de Jira. |
| `AUTH_ROTATION_TTL_SECONDS` | `3600`; rango 60–86400 | Ventana para confirmar una rotación con el nuevo token. |
| `JIRA_WEBHOOK_SECRET` **†** | Obligatorio en la API | Firma HMAC del webhook entrante, independiente del API token. |
| `MAX_BODY_BYTES` | `10485760` (10 MiB); rango 1024–10485760 | Límite del cuerpo HTTP entrante. GUI admite hasta 10 MiB por solicitud; respuestas hasta 32 MiB. No implica capacidad de carga ilimitada. |
| `WORKER_ID` | Hostname del proceso | Identidad de heartbeat. Configurar una identidad única por worker si comparten hostname. |

Bootstrap y secreto de webhook: mínimo 32 caracteres ASCII; valores distintos al inicializar.
El worker no necesita el bootstrap ni el secreto del webhook. Configurar un token para nuestra API
no otorga privilegios nuevos en Jira. Los tokens delegados y su rotación se administran en DB/API,
sin una env var nueva por token y sin RBAC individual de usuarios de GUI por ahora.

### Conector Jira

| Parámetro | Obligatorio / valor predeterminado | Uso |
| --- | --- | --- |
| `JIRA_CLIENT_ID` | Para Jira mediante OAuth | Client ID de cuenta de servicio, flujo `client_credentials`. |
| `JIRA_CLIENT_SECRET` **†** | Junto con `JIRA_CLIENT_ID` | Client secret; token temporal obtenido y renovado sólo en memoria. |
| `JIRA_API_TOKEN` **†** | Alternativa sin OAuth | API token estático Bearer; no combinar con las credenciales OAuth. |
| `JIRA_CLOUD_ID` | Para ejecutar acciones Jira | UUID del sitio Jira Cloud. |
| `JIRA_MAX_DAILY_WRITES` | `100`; rango 1–100000 | Intentos de escritura por ventana móvil de 24 h, compartidos en DB. |

### Conector Bitbucket

| Parámetro | Obligatorio / valor predeterminado | Uso |
| --- | --- | --- |
| `BITBUCKET_API_TOKEN` **†** | Para ejecutar pipelines | Access Token de repositorio, proyecto o workspace con Pipelines Read/Write, enviado como Bearer sin email. |
| `BITBUCKET_MAX_DAILY_WRITES` | `100`; rango 1–100000 | Presupuesto compartido de intentos de despacho por 24 h. |

Los Workspace Access Tokens requieren un plan Bitbucket Premium. Verificar su disponibilidad
antes de migrar: un API token personal anterior que usa Basic con email no se convierte en
Workspace Access Token por enviarlo como Bearer. Esta release cambia ese contrato; no desplegar
el conector nuevo con la credencial anterior sin validarla. Ver
[tokens de workspace](https://support.atlassian.com/bitbucket-cloud/docs/workspace-access-tokens/).
Para una instalación que opera un único repositorio, se puede usar un Repository Access Token:
el conector usa el mismo Bearer y Bitbucket limita su alcance a ese repo. No requiere Premium
ni permisos de escritura sobre código, webhooks o variables: conceder sólo Pipelines Read/Write.
No habilita otros repositorios por agregarlos al YAML. Si se necesita operar varios repositorios,
evaluar el token de proyecto/workspace y su plan, sin ampliar el alcance silenciosamente.
Ver [tipos y alcance de Access Tokens](https://support.atlassian.com/bitbucket-cloud/docs/access-tokens/).
`BITBUCKET_ACCOUNT_EMAIL` y `ALLOWED_REPOSITORIES` se rechazan al arrancar: eliminarlos
de la configuración anterior. No hay conversión silenciosa de Basic a Bearer ni una allowlist
que parezca activa cuando ya no se usa; el alcance efectivo lo autoriza el token de Bitbucket.

### GUI (proceso opcional)

| Parámetro | Obligatorio / valor predeterminado | Uso |
| --- | --- | --- |
| `STUDIO_BACKEND_URL` | Obligatorio | URL de nuestra API vista desde la GUI; no de Jira. |
| `STUDIO_PUBLIC_ORIGIN` | Obligatorio | Origen del navegador; exige HTTPS fuera de loopback. |
| `STUDIO_BACKEND_TOKEN` **†** | Obligatorio | Token de nuestra API mantenido del lado servidor. |
| `STUDIO_AUTH_MODE` | `basic` | `local` sólo como excepción explícita, con bind y origen loopback. |
| `STUDIO_LOGIN_USERNAME` | Obligatorio en modo `basic` | Usuario de ingreso a la GUI. |
| `STUDIO_LOGIN_PASSWORD` **†** | Obligatorio en modo `basic`, mínimo 16 caracteres | Contraseña de ingreso, distinta de la credencial del backend. |
| `STUDIO_HOST` | `127.0.0.1` en proceso; `0.0.0.0` dentro de la imagen | Dirección donde escucha la GUI. |
| `STUDIO_PORT` | `8080`; rango 1–65535 | Puerto del proceso GUI. |
| `STUDIO_STATIC_DIR` | `frontend/dist` dentro de la instalación | Directorio de assets compilados; normalmente no se cambia. |

### Parámetros adicionales del empaquetado Compose

Estos nombres los consume Compose, no el código del motor:

| Parámetro | Valor predeterminado / requisito | Uso |
| --- | --- | --- |
| `DB_MIGRATION_USER` | Obligatorio en Compose principal | Se inyecta como `DB_USER` sólo en `migrate`. No lo recibe el runtime. |
| `AUTOMATION_IMAGE` | `automation-api:1.0.0` | Imagen de API, worker y migrador. |
| `AUTOMATION_SECRETS_DIR` | `./secrets` | Directorio privado con los archivos de secretos. |
| `API_PORT` | `8088` | Publicación en loopback de la API única; el operador agrega HTTPS/WAF. |
| `STUDIO_IMAGE` | `jira-relay-studio:1.0.0` | Imagen de GUI. |
| `STUDIO_BIND_ADDRESS` | `127.0.0.1` | Dirección de publicación de la GUI en el host. |
| `STUDIO_PUBLISHED_PORT` | `8093` | Puerto de publicación de la GUI en el host. |

El Compose principal monta `db_runtime_password` en API/worker y `db_migrator_password`
sólo en `migrate`; no se guardan host/base/usuario dentro del archivo de contraseña.
El perfil `studio` es opcional. En el Compose de GUI independiente, `STUDIO_BACKEND_TOKEN_FILE`
y `STUDIO_LOGIN_PASSWORD_FILE` indican archivos del host que se montan en el contenedor;
consultar [la guía de GUI](docs/studio.md) para las diferencias de empaquetado.

Las pruebas usan exclusivamente `TEST_DB_HOST`, `TEST_DB_PORT`, `TEST_DB_NAME=automation_test`,
`TEST_DB_USER` y `TEST_DB_PASSWORD`/`TEST_DB_PASSWORD_FILE`, con roles de laboratorio capaces de
crear DB/roles para las pruebas de migración. No apuntarlas a una base operativa. `tools/selftest.py`
los configura automáticamente en recursos descartables; no son parámetros de producción.
Retención utiliza argumentos `--days` (90), `--limit` (100) y `--apply` (desactivado), no variables nuevas.

## Operación y actualización

### Actualizar una instalación con receptor separado

La topología actual tiene **API + worker + Studio opcional**, más el job de migración y la tarea
de retención. Se eliminó `app.webhooks:app`; no desplegar esta imagen con ese comando antiguo.
Para pasar de una instalación anterior a esta:

1. Montar `JIRA_WEBHOOK_SECRET`/`_FILE` en la API, conservando exactamente el secreto registrado
   en Jira. Mantener DB, tenant y credenciales API actuales; no hacer rebootstrap.
2. Arrancar la nueva API y verificar gestión autenticada y webhook firmado en su mismo puerto.
3. Apuntar la ruta pública `/webhooks/jira` del proxy/WAF al servicio API. Conservar URL, bytes,
   firma e identificador de entrega. Restringir `/v1/*`, `/metrics` y `/openapi.json` según política.
4. Retirar el servicio/contenedor exclusivo de webhooks cuando la nueva ruta esté verificada.
   En Compose se usa ahora `API_PORT`, no `ADMIN_PORT`/`WEBHOOK_PORT`.

Este cambio de topología no agrega una migración SQL. No cambia por sí solo versiones activas,
payloads, tokens ni checksums históricos. Actualizar manifests y configuración externos también
es necesario: hacer `git pull` o pushear el código no modifica una instalación en funcionamiento.

### Salud, retención y cierre ordenado

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

### Migraciones de PostgreSQL

V1 parte de una única base inicial: `app/migrations/001_initial.sql`. No adopta tablas
preexistentes ni historiales del prototipo. La primera instalación exige un esquema `public`
vacío; no se borran ni convierten bases anteriores automáticamente.

Cada cambio posterior agrega un archivo SQL consecutivo: `002_<nombre>.sql`, `003_<nombre>.sql`, etc.
Los nombres usan tres dígitos y letras minúsculas/guiones bajos. No modificar archivos ya aplicados,
ni insertar migraciones antes de otras publicadas. No se permiten huecos o números repetidos.

```bash
# Consulta de solo lectura: valida y lista aplicadas/pendientes.
python -m app.migrate --status

# Aplica únicamente las pendientes con DB_USER/DB_PASSWORD del migrador.
python -m app.migrate
```

Ambos comandos leen `DB_HOST`, `DB_PORT` (5432 por defecto), `DB_NAME`, `DB_USER`,
`DB_PASSWORD` o `DB_PASSWORD_FILE`, y `TENANT_ID`. Salud del worker y mantenimiento usan
el mismo contrato. El servicio `migrate` de Compose recibe `DB_MIGRATION_USER` como su
`DB_USER` y monta sólo `db_migrator_password`; API/worker montan `db_runtime_password`.
No hay una contraseña de migración accesible al runtime. El Compose de desarrollo es una excepción
local descartable: usa un único usuario de laboratorio, no es el modelo de permisos de producción.

Antes de ejecutar SQL nuevo, el migrador verifica todo el historial: debe ser un prefijo exacto
de la release y cada SHA-256 debe coincidir con los bytes del archivo original. Un checksum
diferente detiene el proceso: no reaplica SQL ni modifica el historial para ocultar diferencias.
Se comprueba también el tenant y la huella estructural registrada por la última migración.
Esta segunda huella cubre tablas/vistas, columnas/tipos/defaults/nullability, restricciones,
índices, configuración de secuencias, triggers y políticas RLS del esquema `public`.
Detecta, por ejemplo, una restricción de deduplicación eliminada manualmente. No es una auditoría
de roles/permisos, funciones o extensiones. Datos, valores actuales de secuencias, propietarios y
GRANTs no forman parte de la huella. Cambios manuales de índices también requieren investigación.

El migrador toma un advisory lock transaccional. Todo el lote pendiente, incluyendo sus registros
y huellas, se confirma junto. Ante un error se revierte ese lote; las migraciones ya confirmadas
permanecen intactas. API/worker validan historial y estructura al arrancar, sin aplicar DDL.
La huella usa definiciones de los catálogos PostgreSQL: ensayar upgrades de versión mayor y
comparar definiciones si cambia; no sobrescribir automáticamente la huella almacenada.

Las migraciones son para una ventana de mantenimiento. SQL que exige ejecutarse fuera de una
transacción, como `CREATE INDEX CONCURRENTLY`, no es compatible con este ejecutor.

Actualización: fijar digest de imagen, detener ingreso y drenar/detener workers, tomar backup verificado,
ejecutar migraciones con el propietario y volver a conceder permisos sobre objetos nuevos; arrancar la
imagen correspondiente y verificar probes. No mezclar workers de releases incompatibles. Los scripts SQL
aplicados son inmutables y su checksum se valida al arrancar. Una versión anterior rechaza una DB más nueva;
no hay downgrade destructivo automático. Recuperación: restaurar backup en una base nueva y usar la imagen
correspondiente, conservando tenant y claves. Antes de reanudar escrituras reconciliar efectos posteriores
al backup: restaurar estado local NO revierte acciones que ya ocurrieron en Jira/Bitbucket.

Prueba reproducible sin proveedores: `uv run python tools/smoke_container.py --image automation-api:1.0.0`.
Crea infraestructura Docker efímera propia, prueba migración/rechazo de esquemas ajenos/roles/reinicio/backup y la elimina al terminar.
No usa .env, credenciales personales ni bases existentes. Los tests de conectores no equivalen a certificación cloud.
También levanta el Compose distribuido con secretos montados y verifica la API única y sus dos mecanismos de autenticación.
GitHub Actions ejecuta la suite Python 3.12/3.13 con PostgreSQL, pruebas/build de frontend,
auditoría de dependencias y aceptación Docker/Studio en pushes y pull requests.
No publica ni despliega imágenes; ver [workflow](.github/workflows/ci.yml).
El escaneo de paquetes Python no cubre paquetes del sistema operativo ni equivale a una auditoría de seguridad.

Referencias operativas: [secretos de Compose](https://docs.docker.com/compose/how-tos/use-secrets/),
[orden de arranque](https://docs.docker.com/compose/how-tos/startup-order/),
[verificación TLS de PostgreSQL](https://www.postgresql.org/docs/current/libpq-ssl.html).

## Pendiente para producción

E2E nativo de cada acción Jira; cuenta de servicio y rotación ensayada; mapeo definitivo JSM/campos;
reconciliación Jira y causalidad entre sistemas; conflictos de orden/concurrencia en el proveedor;
metadatos exhaustivos de campos/payloads de proveedor; carga/fault injection prolongadas; auth por usuario si la instalación
lo necesita; límites/alertas de ingreso en el proxy; backup/PITR y RPO/RTO de la instalación real;
registro privado/digests, escaneo completo y firma/SBOM de imagen; aceptación de K8s/ECS si se ofrecen.
Las migraciones, credenciales de servicio, probes y retención ya están implementados; esto no cierra esos gates.
Por ahora las revisiones y auditorías son append-only mediante API, no un almacén legal inmutable.
La aplicación no ofrece parámetros propios de CA/SSL de PostgreSQL en esta versión. No suponer
que `DB_*` configura `verify-full`: el operador debe evaluar y validar el transporte real antes de
usar una DB remota. Si la política exige TLS verificable, debe resolverse explícitamente antes de desplegar.
