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
| Conectar Jira de verdad | [Token Bearer](#api-token-de-cuenta-de-servicio-bearer) y [webhook firmado](#conectar-el-webhook-real-de-jira) |
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
BITBUCKET_ACCOUNT_EMAIL/BITBUCKET_API_TOKEN y
JIRA_API_TOKEN/JIRA_CLOUD_ID. Jira utiliza Bearer con un API token de cuenta de servicio compatible; no requiere email.
La versión 1 no acepta los nombres anteriores; los tokens también admiten
BITBUCKET_API_TOKEN_FILE y JIRA_API_TOKEN_FILE.
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

El conector requiere JIRA_API_TOKEN y JIRA_CLOUD_ID.
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

### API token de cuenta de servicio (Bearer)

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
- Una transición necesita `from_status_id` y `to_status_id` distintos. Se comprueba el esta…19425 tokens truncated…d813ea9e97c294af3ead63d02b7b203502c66380336c50065e441/psycopg_binary-3.3.6-cp315-cp315-manylinux2014_ppc64le.manylinux_2_17_ppc64le.whl", hash = "sha256:b8ece331509f7a975b90501f41e83ad905e4141753fedf3f2711b2bc70a8efbc", size = 5566111, upload-time = "2026-09-18T13:21:58.089Z" },
    { url = "https://files.pythonhosted.org/packages/68/16/47194e002007c27337b11e49bf459c4b19727463f9aff2e1a90917bcc806/psycopg_binary-3.3.6-cp315-cp315-manylinux2014_x86_64.manylinux_2_17_x86_64.whl", hash = "sha256:c61617eaae0112ca154da87ffb99b73af2c74067acac28dfb9a4455b019dff2e", size = 5249963, upload-time = "2026-09-18T13:22:06.695Z" },
    { url = "https://files.pythonhosted.org/packages/53/84/5dcf9f310b11f0675cd860c6b2c70f58ce61798a3ee3f6f962b53fa358ca/psycopg_binary-3.3.6-cp315-cp315-manylinux_2_27_aarch64.manylinux_2_28_aarch64.whl", hash = "sha256:c6d19cb4999d03231e8730a5f66c8f5068bc3b532677eb39dab0f600bff3e312", size = 6847925, upload-time = "2026-09-18T13:22:13.088Z" },
    { url = "https://files.pythonhosted.org/packages/f3/06/1957a06dc22963c418c27b284929579de84f29c37ad1abe6dc6ee9e8cf25/psycopg_binary-3.3.6-cp315-cp315-manylinux_2_38_riscv64.manylinux_2_39_riscv64.whl", hash = "sha256:e8cbb54454dbf1bbf2ff08dd7693e8d94ac94b1a20f70f4b3b813d52ecb5cbc1", size = 5087720, upload-time = "2026-09-18T13:22:17.959Z" },
    { url = "https://files.pythonhosted.org/packages/21/43/ac07d042bae99b57bf123bb473632f29af544008094da0ffd285ab8011e2/psycopg_binary-3.3.6-cp315-cp315-musllinux_1_2_aarch64.whl", hash = "sha256:dc75da5a20951049f7b773145f998f69d181adad9c58a0ff36e0cf1d73c10e10", size = 4613412, upload-time = "2026-09-18T13:22:26.719Z" },
    { url = "https://files.pythonhosted.org/packages/aa/b1/019156fbeafcefb4cccc9d109de4699493bceb8313c7545c8349e089dfbc/psycopg_binary-3.3.6-cp315-cp315-musllinux_1_2_ppc64le.whl", hash = "sha256:955e3dd94da361e052d2e49acf591017158dc8f8ed2c8a42c2e3943403c39dc2", size = 4292618, upload-time = "2026-09-18T13:22:33.042Z" },
    { url = "https://files.pythonhosted.org/packages/5d/0f/62113dc6b1df65983a1f2fc816c04b1edfa22f2ae9d4abee74ed267f4a96/psycopg_binary-3.3.6-cp315-cp315-musllinux_1_2_riscv64.whl", hash = "sha256:c7753871eb57e6a5f4646f6168590c6653073dea5e9e720b201c8875332df4c8", size = 4027121, upload-time = "2026-09-18T13:22:38.334Z" },
    { url = "https://files.pythonhosted.org/packages/5d/d5/cf0cbd1ea5a7d8167fe2c6953efde19101f7b193bd61a23e6d622ad6854c/psycopg_binary-3.3.6-cp315-cp315-musllinux_1_2_x86_64.whl", hash = "sha256:303732e798fe6729f8e12021b9c96107df8e95ecec4dd487c67b98ec2a59435e", size = 4336388, upload-time = "2026-09-18T13:22:45.576Z" },
    { url = "https://files.pythonhosted.org/packages/98/33/e2a5b36edf8aa422f6fa4b894756eb33dc93b36df5f65121280bb8b929c4/psycopg_binary-3.3.6-cp315-cp315-win_amd64.whl", hash = "sha256:2f122603f36050937982abf9668d8bc4769a79f7c93a65013b1c49f1cab7b56b", size = 3756154, upload-time = "2026-09-18T13:22:51.283Z" },
]

[[package]]
name = "psycopg-pool"
version = "3.3.3"
source = { registry = "https://pypi.org/simple" }
dependencies = [
    { name = "typing-extensions" },
]
sdist = { url = "https://files.pythonhosted.org/packages/74/5e/c0664b968b102ff68b811d999c728546c48d5c1eec03e3bbaf88c0cb4472/psycopg_pool-3.3.3.tar.gz", hash = "sha256:df87b5d9d0ad7db37f6cdad4fa8ce113d250f5997f6db38e9a99192fb67f9e1d", size = 32006, upload-time = "2026-09-22T15:53:24.947Z" }
wheels = [
    { url = "https://files.pythonhosted.org/packages/5d/b4/452c6607a0f479465cd8a9b0d9956919fcb150050c1f83f9f11e6b8ee8dc/psycopg_pool-3.3.3-py3-none-any.whl", hash = "sha256:9b9cd6a4fcec47a410f7e82d408540e7f77b478509e91b44c1a5457a13e5ff37", size = 40304, upload-time = "2026-09-22T15:53:23.712Z" },
]

[[package]]
name = "pydantic"
version = "2.13.5"
source = { registry = "https://pypi.org/simple" }
dependencies = [
    { name = "annotated-types" },
    { name = "pydantic-core" },
    { name = "typing-extensions" },
    { name = "typing-inspection" },
]
sdist = { url = "https://files.pythonhosted.org/packages/53/ef/fc4f868f4e2cee79f863883abffceff107875f569b848507319842d2a681/pydantic-2.13.5.tar.gz", hash = "sha256:51a9c5f7b2f8e636f04c6cada605d9b6a3bf1348fdf945a3d8869b19bba0ee08", size = 845750, upload-time = "2026-08-28T14:04:00.916Z" }
wheels = [
    { url = "https://files.pythonhosted.org/packages/eb/47/c95ffc2009878c7aac0c5e08528022dcb885933252a88b5f170058014464/pydantic-2.13.5-py3-none-any.whl", hash = "sha256:346a034f080da3755d8e9cb5e00e8b07de1d39e4f6e2c87d8ab7cafa0b269a73", size = 472589, upload-time = "2026-08-28T14:03:59.136Z" },
]

[[package]]
name = "pydantic-core"
version = "2.46.5"
source = { registry = "https://pypi.org/simple" }
dependencies = [
    { name = "typing-extensions" },
]
sdist = { url = "https://files.pythonhosted.org/packages/af/f9/8a06bea35ef8daf588f707784c973a7046e0034c8d8cfb08828eeffb8b75/pydantic_core-2.46.5.tar.gz", hash = "sha256:10416c15b8839ecc4ef4d0885da76da6fd0f67333a0eb8aff6d93c4b8f2910fc", size = 472262, upload-time = "2026-08-28T10:01:31.677Z" }
wheels = [
    { url = "https://files.pythonhosted.org/packages/82/3f/76358795aa7a8c6d4f36e2cb828ad1c90ee118e1393a9281664f5aade9d4/pydantic_core-2.46.5-cp312-cp312-macosx_10_12_x86_64.whl", hash = "sha256:b9fe6fb92520e3fd61f2e49000b6911b188824f089b75973ea06d6267f0b476d", size = 2076516, upload-time = "2026-08-28T09:58:21.576Z" },
    { url = "https://files.pythonhosted.org/packages/db/50/26b091836076ce4cb2fac264186936acc069e0595772cfd02a563bc4761a/pydantic_core-2.46.5-cp312-cp312-macosx_11_0_arm64.whl", hash = "sha256:a39ac25a9a2fa4072efdb429833c4a4c8009a51ff9eea3eeae131713cd27991e", size = 1922874, upload-time = "2026-08-28T09:58:23.766Z" },
    { url = "https://files.pythonhosted.org/packages/09/f0/2a8ce3849e299d44e2d2c196b6082643a3235565a735cb51db7a6261f614/pydantic_core-2.46.5-cp312-cp312-manylinux_2_17_aarch64.manylinux2014_aarch64.whl", hash = "sha256:4fdc8b93a41521988916eeaa271173fcca7fa0803d62f87675aac8dcec1c8e29", size = 1951772, upload-time = "2026-08-28T09:58:25.435Z" },
    { url = "https://files.pythonhosted.org/packages/87/46/ac0dc8bdd9e6048183a14eb127764e7ad9240021c17513074a4711b0e31e/pydantic_core-2.46.5-cp312-cp312-manylinux_2_17_armv7l.manylinux2014_armv7l.whl", hash = "sha256:b98134087d9de723658d17a42c7d0da8d6e2ef08015dee7dc93889047315f5e4", size = 2031832, upload-time = "2026-08-28T09:58:27.102Z" },
    { url = "https://files.pythonhosted.org/packages/c4/c2/339de5bef7be36301a2231eaa52e62163742c2281f11b5f4892bc79785cd/pydantic_core-2.46.5-cp312-cp312-manylinux_2_17_ppc64le.manylinux2014_ppc64le.whl", hash = "sha256:e652ab17569c94bff5475520f907b7148b8c24036a8ebbe5cf7cf7493d28579a", size = 2208645, upload-time = "2026-08-28T09:58:28.948Z" },
    { url = "https://files.pythonhosted.org/packages/7b/a0/9ff22b797724262da14427abaed4dd1d864a139693fc5e7809114376a716/pydantic_core-2.46.5-cp312-cp312-manylinux_2_17_s390x.manylinux2014_s390x.whl", hash = "sha256:d925f3d9afd05a8c0fb3a1031463a8d59ebe5e2afad297e29c78be19e13b4e62", size = 2265935, upload-time = "2026-08-28T09:58:30.625Z" },
    { url = "https://files.pythonhosted.org/packages/c0/a4/eb9409ec0736e50aa70a412f16c204ed149516846912f7e6724d4c73ee53/pydantic_core-2.46.5-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", hash = "sha256:0fc5be0abd4a407e200d844b404e33639a554e7bd0d448e7b9ae181be4789ac2", size = 2066284, upload-time = "2026-08-28T09:58:32.289Z" },
    { url = "https://files.pythonhosted.org/packages/c0/02/7f6156ffc926857f1c37c07d9a388682865a81830ab6a1b637082c25e399/pydantic_core-2.46.5-cp312-cp312-manylinux_2_31_riscv64.whl", hash = "sha256:816ff0a6550ffc06c098ccd2e0698600f9aa7da192a79eaa6f9af504a35db869", size = 2105889, upload-time = "2026-08-28T09:58:33.986Z" },
    { url = "https://files.pythonhosted.org/packages/92/b1/e781d357ebe09fc929f995700f1b3503e8897f1cece183ecb1300d4d67e9/pydantic_core-2.46.5-cp312-cp312-manylinux_2_5_i686.manylinux1_i686.whl", hash = "sha256:c7ea57fc63aa7da93a1bd2d644e6577befae10c52c4e36377635eea1056a74f5", size = 2158006, upload-time = "2026-08-28T09:58:35.647Z" },
    { url = "https://files.pythonhosted.org/packages/70/0a/644597d84ab400e50609c192120b85c9681c22d3a20461b9060a79be0a7a/pydantic_core-2.46.5-cp312-cp312-musllinux_1_1_aarch64.whl", hash = "sha256:efd62a42486f1bda5d24cb4f63d15a3c7768375fe83d36f9417b4ad7a2fb20b3", size = 2158408, upload-time = "2026-08-28T09:58:37.38Z" },
    { url = "https://files.pythonhosted.org/packages/1e/ee/ca3b7b3a4b3769ffe9ce9432a7c9be755de9593a46d3b0d54d0409323e44/pydantic_core-2.46.5-cp312-cp312-musllinux_1_1_armv7l.whl", hash = "sha256:2bc9419666990c06d7397831f2126a1ecc3594aaa3ff7de5bf2d066802f4e07b", size = 2309609, upload-time = "2026-08-28T09:58:39.22Z" },
    { url = "https://files.pythonhosted.org/packages/ce/52/39fa1f451486019524ca685020390e7ca351832fd874530ba30c8628e6dc/pydantic_core-2.46.5-cp312-cp312-musllinux_1_1_x86_64.whl", hash = "sha256:18a09e1e1011b462f2e32774f25859ef1223d5c2b0546a633cf56654710721e0", size = 2342618, upload-time = "2026-08-28T09:58:40.89Z" },
    { url = "https://files.pythonhosted.org/packages/81/5e/468fc630568c61dcef3cd47ad32ffbeed9af643f49208d1ea86ab4f890c4/pydantic_core-2.46.5-cp312-cp312-win32.whl", hash = "sha256:5cb482e9e84c851f4e623fe4acc1ced89168cf1fe18f7089db4548c8f5bbb65b", size = 1939475, upload-time = "2026-08-28T09:58:42.591Z" },
    { url = "https://files.pythonhosted.org/packages/cf/c9/4c19f41b84cf6b622a72fbeed7665b25d47a187d68d47d0d430c07f23268/pydantic_core-2.46.5-cp312-cp312-win_amd64.whl", hash = "sha256:5e81740c09e310f5aa5cbd3e434a01c154d4bef93241c7877b39f211d2b78ba8", size = 2043140, upload-time = "2026-08-28T09:58:44.272Z" },
    { url = "https://files.pythonhosted.org/packages/af/dd/0c1a050299147c746e5256db16d645ab5efd4f78c59937d581a0524e74a2/pydantic_core-2.46.5-cp312-cp312-win_arm64.whl", hash = "sha256:f7b0ec93a2893de856652154d73b7ba622f26fa97726487dcac373de5f4c6084", size = 1997729, upload-time = "2026-08-28T09:58:46.13Z" },
    { url = "https://files.pythonhosted.org/packages/f5/37/5abe39a8372a61d3dc3c1338fc504281c01b32fdb3169cd7187153b56d3e/pydantic_core-2.46.5-cp313-cp313-macosx_10_12_x86_64.whl", hash = "sha256:b7ca9034437b6022f941f4857459562ee00a560b97e7cce8a0ec5a74fc6766e0", size = 2075885, upload-time = "2026-08-28T09:58:47.856Z" },
    { url = "https://files.pythonhosted.org/packages/21/43/6323b1f8b217780454c61304bcd2b38ae4762f50754414124603ccc90bb2/pydantic_core-2.46.5-cp313-cp313-macosx_11_0_arm64.whl", hash = "sha256:f332f0e72a5a0400141f830744e141bf9f97917878dbe968669e8a7fefea78ff", size = 1922768, upload-time = "2026-08-28T09:58:49.58Z" },
    { url = "https://files.pythonhosted.org/packages/0f/a3/c05ca796e1197618a774b01e596aeedfefc2f7d8c01ae3054e910b120e8a/pydantic_core-2.46.5-cp313-cp313-manylinux_2_17_aarch64.manylinux2014_aarch64.whl", hash = "sha256:193375f3548919d3f0b60936ca113ada3e38f264f91b9b8e0508efaad57be931", size = 1951241, upload-time = "2026-08-28T09:58:51.511Z" },
    { url = "https://files.pythonhosted.org/packages/68/32/33bc39ac705c52cffc908e8389f9754fdb208aea5c69cceddf4eb3ce99af/pydantic_core-2.46.5-cp313-cp313-manylinux_2_17_armv7l.manylinux2014_armv7l.whl", hash = "sha256:79bdfa52f843137045b2d081cc05c120ba6665d29b7559c2c47690906f39279f", size = 2031975, upload-time = "2026-08-28T09:58:53.166Z" },
    { url = "https://files.pythonhosted.org/packages/b0/70/2333e885c0f6a67bc105c5916965dac9b57f2718ee20d81d1a06a4ebdc13/pydantic_core-2.46.5-cp313-cp313-manylinux_2_17_ppc64le.manylinux2014_ppc64le.whl", hash = "sha256:24922243639cbdac66c75fcb6fd6495a9cb52b213d62f9a0d16f0310b1ff8038", size = 2208542, upload-time = "2026-08-28T09:58:55.017Z" },
    { url = "https://files.pythonhosted.org/packages/f7/ea/296debfb4264207bbda5936133892e027c0a58875ad53ebd512fba8ec3a2/pydantic_core-2.46.5-cp313-cp313-manylinux_2_17_s390x.manylinux2014_s390x.whl", hash = "sha256:c76fe65e607be28c7fd4d56fc3c42b1583aa058ce3408b7ad0fd540171d31f9f", size = 2264692, upload-time = "2026-08-28T09:58:56.767Z" },
    { url = "https://files.pythonhosted.org/packages/d3/f2/9e4de77a6271e07a76d2d58b11c091a979c191ed2939bf80067568b369d2/pydantic_core-2.46.5-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", hash = "sha256:6f7b393a8b3da82f5c1fc0751e6d01ac6c55b93c18226a60bdfba4a724efafd1", size = 2066633, upload-time = "2026-08-28T09:58:58.531Z" },
    { url = "https://files.pythonhosted.org/packages/8d/db/f9e9d0c97445987b2084823d5c240de88087338f04fc2cfaa2df186b8049/pydantic_core-2.46.5-cp313-cp313-manylinux_2_31_riscv64.whl", hash = "sha256:7ac031912d54f3d83ef3b3eb98dfabc1608802e2202263d25957eeed40b94761", size = 2105235, upload-time = "2026-08-28T09:59:00.421Z" },
    { url = "https://files.pythonhosted.org/packages/07/c5/79169b047b3b2c3e99e04bc76372af9637e0bf6db638274fa927df96369e/pydantic_core-2.46.5-cp313-cp313-manylinux_2_5_i686.manylinux1_i686.whl", hash = "sha256:837b396ca3d7b74091ca623f6cbd8351bd42d670a79c2683e79fb089f06a2de5", size = 2157367, upload-time = "2026-08-28T09:59:02.442Z" },
    { url = "https://files.pythonhosted.org/packages/26/b5/ba6057afb7c291bd449f51b867f95aef2072941c4ce4e5c31d6ffd132d3b/pydantic_core-2.46.5-cp313-cp313-musllinux_1_1_aarch64.whl", hash = "sha256:5ee239d575f80b08eca11f6e20f90c4c695de7825c67eefe6091fbf20dda648e", size = 2158420, upload-time = "2026-08-28T09:59:04.2Z" },
    { url = "https://files.pythonhosted.org/packages/6e/28/2057abecaafdc22912afa819603a51f0a62d40643b7c4871c51721fea9be/pydantic_core-2.46.5-cp313-cp313-musllinux_1_1_armv7l.whl", hash = "sha256:e80675d75ae2cd14372cb65cad5400d9347a3d3f6c13000183f22dfd027283ed", size = 2309588, upload-time = "2026-08-28T09:59:06.048Z" },
    { url = "https://files.pythonhosted.org/packages/71/9d/881156dc404e27479c4246128d73538464cab4a239bec61995e227644c30/pydantic_core-2.46.5-cp313-cp313-musllinux_1_1_x86_64.whl", hash = "sha256:9c4b71f10dd532fb7a5cbc8f58707779e64f03a258c2bf8bfbaecfcd9970b519", size = 2341866, upload-time = "2026-08-28T09:59:08.539Z" },
    { url = "https://files.pythonhosted.org/packages/5a/38/d66f443a259f84d13babdceae568e572b0ed26da17ca5d0a649ebb110a67/pydantic_core-2.46.5-cp313-cp313-win32.whl", hash = "sha256:97bf8de4d541598c94a59344eeb988a94c08ff76b5723c41f6567ec18c7892ea", size = 1938580, upload-time = "2026-08-28T09:59:10.402Z" },
    { url = "https://files.pythonhosted.org/packages/2c/1e/1d5371213f4cc9a7ed70c0bfcc7911de22311ee99a662a56077d7292d2ac/pydantic_core-2.46.5-cp313-cp313-win_amd64.whl", hash = "sha256:15f4a94963c95accac15b7b657bb177d3ad82bb90b0d0526d9a9b85079925db5", size = 2041980, upload-time = "2026-08-28T09:59:12.396Z" },
    { url = "https://files.pythonhosted.org/packages/5a/48/4222d90b1c67568bace4dec6dca6271449c66de3595d72b6d098f5fde597/pydantic_core-2.46.5-cp313-cp313-win_arm64.whl", hash = "sha256:d22a945598fb91236b4dd793a6e42e4f3dd7740bb5aace5ebd7d4c08d13bb575", size = 1997213, upload-time = "2026-08-28T09:59:14.245Z" },
    { url = "https://files.pythonhosted.org/packages/8e/8a/14596f2a8367da50cf7cbac48169ee5d9c8e11d486a3b527082384630c72/pydantic_core-2.46.5-cp314-cp314-macosx_10_12_x86_64.whl", hash = "sha256:c1c43ad4339643d70ebb8124e1305a7dab423001eff58bb41a0f731adbc98355", size = 2074081, upload-time = "2026-08-28T09:59:16.141Z" },
    { url = "https://files.pythonhosted.org/packages/ae/d5/d8a4eb6d6c7f66b91dd37c576d76e9e60fba900caf5372c17bcf949febc2/pydantic_core-2.46.5-cp314-cp314-macosx_11_0_arm64.whl", hash = "sha256:1a353f84de772f423b5ffb11d7ae352fbbef0f446f3c0b0af0f8236d7233606e", size = 1920497, upload-time = "2026-08-28T09:59:18.065Z" },
    { url = "https://files.pythonhosted.org/packages/8e/26/092079428f86e927e030b2c0ced87df69dbb1c875cdeaa67bf42ea2be746/pydantic_core-2.46.5-cp314-cp314-manylinux_2_17_aarch64.manylinux2014_aarch64.whl", hash = "sha256:5086029a57366b8cf81b130a43908738095c270c21a8d7f0e8bdfdb89718e2f3", size = 1952130, upload-time = "2026-08-28T09:59:20.476Z" },
    { url = "https://files.pythonhosted.org/packages/08/c3/8ec0e290a9ebaebd64047bf5fda94be835c6b1551b02437e4b76778fbcd7/pydantic_core-2.46.5-cp314-cp314-manylinux_2_17_armv7l.manylinux2014_armv7l.whl", hash = "sha256:46c25dda9d092a06c08db76ffe0a197107904d0dfac653f7d5306bbcd6d6119c", size = 2026371, upload-time = "2026-08-28T09:59:22.227Z" },
    { url = "https://files.pythonhosted.org/packages/01/72/4fd20ad520fb8da0157f95b27a7eb05a72790ef08138e7701ac972c342ea/pydantic_core-2.46.5-cp314-cp314-manylinux_2_17_ppc64le.manylinux2014_ppc64le.whl", hash = "sha256:37ea7b83c935e5b0d68c9449b82651accf78a10828b2c02b2f2d9e9496446c21", size = 2202822, upload-time = "2026-08-28T09:59:24.277Z" },
    { url = "https://files.pythonhosted.org/packages/31/b0/d16e0771206b29314f0d52198b720be21e8a99ab2bf11e3bc0d7c9cebdff/pydantic_core-2.46.5-cp314-cp314-manylinux_2_17_s390x.manylinux2014_s390x.whl", hash = "sha256:e64e88d5585bea9ce95861079de72006c7fa6d3df4e3a3b65ba31eb979c15c9f", size = 2262756, upload-time = "2026-08-28T09:59:26.608Z" },
    { url = "https://files.pythonhosted.org/packages/2c/9b/59634b7ac631c63b2a37760eb6943af3e29573d6b59a4abc5e7f019d4cee/pydantic_core-2.46.5-cp314-cp314-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", hash = "sha256:54d510bac3ee52247af28ed4bb18a1e799f040ac60fd2bf5ccd4c92f1fbe786f", size = 2068352, upload-time = "2026-08-28T09:59:29.044Z" },
    { url = "https://files.pythonhosted.org/packages/08/7c/570abb1ad2155348dc754ea91be22e5aaa18eb6d69a6068f7c6f2679a6ed/pydantic_core-2.46.5-cp314-cp314-manylinux_2_31_riscv64.whl", hash = "sha256:a2a5e1d0ff29adddc9f6d6821a66302e4493f8ca898b715b6b1182c2c201ea0a", size = 2104777, upload-time = "2026-08-28T09:59:30.95Z" },
    { url = "https://files.pythonhosted.org/packages/8e/25/5bf74adc65a1ac5b7be3f6cb0bcb5433615c1598a801c19d830d84c98ded/pydantic_core-2.46.5-cp314-cp314-manylinux_2_5_i686.manylinux1_i686.whl", hash = "sha256:03b9666e41e35d8909852ba191a0607520f81b74eaf12ccf8737005dbb313821", size = 2156312, upload-time = "2026-08-28T09:59:32.604Z" },
    { url = "https://files.pythonhosted.org/packages/90/6a/2ef38830675e050121040618135564ed56b860b45433b02d9b4ebece46f3/pydantic_core-2.46.5-cp314-cp314-musllinux_1_1_aarch64.whl", hash = "sha256:a91c17edf6eea2402cb5457b4c89e99bc5ed1004aa34c4adf1d4258c1a5c22c2", size = 2150067, upload-time = "2026-08-28T09:59:34.453Z" },
    { url = "https://files.pythonhosted.org/packages/90/ef/a7dbb03a14a64c2a4621f989c615ed9a892535a6cad938fc27079f919d80/pydantic_core-2.46.5-cp314-cp314-musllinux_1_1_armv7l.whl", hash = "sha256:b49924c73a235e969511bf2aabdff3beebf9820931f646c80274d5d780010c47", size = 2304516, upload-time = "2026-08-28T09:59:36.194Z" },
    { url = "https://files.pythonhosted.org/packages/68/f8/6bb4c4b80e8a6fde1904c64a51c62a1d04fcdfa3ea521a66b2ddefa1d885/pydantic_core-2.46.5-cp314-cp314-musllinux_1_1_x86_64.whl", hash = "sha256:2cbd9a5eff05e51c447c34dfa4632145b26b09120cf04bd0c871e44c1a5e1c9a", size = 2335223, upload-time = "2026-08-28T09:59:37.931Z" },
    { url = "https://files.pythonhosted.org/packages/2a/80/f46b8c681195190b2c1f1c7c0a81abce60663e987613e09ef64d433dd96b/pydantic_core-2.46.5-cp314-cp314-win32.whl", hash = "sha256:2d5d76654becf5efd62c9e51c3756c67b49498b0c9a40884934c40807adbd074", size = 1934827, upload-time = "2026-08-28T09:59:39.836Z" },
    { url = "https://files.pythonhosted.org/packages/f7/3c/60674207246bc0a4009d2391b7c7251c7159f279c8d2ab8aae8ef46f3dee/pydantic_core-2.46.5-cp314-cp314-win_amd64.whl", hash = "sha256:fa10ef4112775900e7a0661068635eb67b2ab824fbde764de6e0e21982a93db0", size = 2042648, upload-time = "2026-08-28T09:59:41.792Z" },
    { url = "https://files.pythonhosted.org/packages/69/0c/117c562c7c1babdf44576b72a5e496906506c93690387ecfbca7c729ae2e/pydantic_core-2.46.5-cp314-cp314-win_arm64.whl", hash = "sha256:045ab3b6d308439e32b81cc173bba5b9018bc6ed896afd0c65b3b009b1699af5", size = 1989652, upload-time = "2026-08-28T09:59:43.702Z" },
    { url = "https://files.pythonhosted.org/packages/e8/66/9336ae58f9eb68c41d121894e52c4c89eccb07eb8f602a04ee9c3f37736a/pydantic_core-2.46.5-cp314-cp314t-macosx_10_12_x86_64.whl", hash = "sha256:8816f3d218beb4b787de5c9759c259b8fa61f9dec42dc7811f320a33771778b7", size = 2065829, upload-time = "2026-08-28T09:59:45.364Z" },
    { url = "https://files.pythonhosted.org/packages/c5/02/bc19b47a96c2d3109760711acf22369e56bd7e405ca52f7ade164d2ead57/pydantic_core-2.46.5-cp314-cp314t-macosx_11_0_arm64.whl", hash = "sha256:bce57638e08ac148e5778cce7feb968307a727d66f8e2274a543d0cf0c9ad6a3", size = 1905716, upload-time = "2026-08-28T09:59:47.18Z" },
    { url = "https://files.pythonhosted.org/packages/52/a4/70b47c0509923dd98ccfed04fb3e32ea3849c82a0ff2205bb41009b43c00/pydantic_core-2.46.5-cp314-cp314t-manylinux_2_17_aarch64.manylinux2014_aarch64.whl", hash = "sha256:976e1128455aa595ea04c79ccfedff1aaeab96ee013fcc916bed120c4f0ad94f", size = 1934216, upload-time = "2026-08-28T09:59:49.241Z" },
    { url = "https://files.pythonhosted.org/packages/52/ab/aa03b65f7bb198585edf806b906c3223ecf1795543e39e23aec4cce27ad2/pydantic_core-2.46.5-cp314-cp314t-manylinux_2_17_armv7l.manylinux2014_armv7l.whl", hash = "sha256:e7b891faeedeafba41b2983e5001a81b6a915b69544c7e7570d1989ce1c36ac7", size = 2010635, upload-time = "2026-08-28T09:59:51.692Z" },
    { url = "https://files.pythonhosted.org/packages/3c/8b/0da06343f30b84ec549aafd309c6456223d5dc8bd36af504c573faad561d/pydantic_core-2.46.5-cp314-cp314t-manylinux_2_17_ppc64le.manylinux2014_ppc64le.whl", hash = "sha256:5f194189415698233dd1114a093a9b56e61e2c57e11b469be3b0506f46f0771c", size = 2209369, upload-time = "2026-08-28T09:59:53.582Z" },
    { url = "https://files.pythonhosted.org/packages/d6/5b/844c4defaa34a3df66eb9257087d121d70c201298b96abdf9f492fc2f1bf/pydantic_core-2.46.5-cp314-cp314t-manylinux_2_17_s390x.manylinux2014_s390x.whl", hash = "sha256:82a36973cf8a2ef5406f4fe2edbf8ed0c99629535d959e0b100c76a32535a111", size = 2253238, upload-time = "2026-08-28T09:59:55.484Z" },
    { url = "https://files.pythonhosted.org/packages/f4/64/a4e536cb16d7f61a7fd3120b46c577fc7fa7325992f69c4f52bc786d77d8/pydantic_core-2.46.5-cp314-cp314t-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", hash = "sha256:cdbb78909f52b981d3b2d56b97328d71eb0b974c36bd77c920123a7ebb192829", size = 2065740, upload-time = "2026-08-28T09:59:58.038Z" },
    { url = "https://files.pythonhosted.org/packages/5f/75/aaa38c6bc2d085f6605b34eabdc6a8a4e0b2e61fc9c8e6e52b28e97b3125/pydantic_core-2.46.5-cp314-cp314t-manylinux_2_31_riscv64.whl", hash = "sha256:52e24eacdb536cade636aa90fb851835222becff8484b7001fdc78cb0290f2aa", size = 2087425, upload-time = "2026-08-28T09:59:59.898Z" },
    { url = "https://files.pythonhosted.org/packages/55/ae/fcab4cfc39aba3689e1d20c8b5250ad280957022c09af2ed9cd585602a5e/pydantic_core-2.46.5-cp314-cp314t-manylinux_2_5_i686.manylinux1_i686.whl", hash = "sha256:37ae34309d7bd8c0d61ab839668058f2a7962ea1fc51d105d2db228fe0618034", size = 2139306, upload-time = "2026-08-28T10:00:03.057Z" },
    { url = "https://files.pythonhosted.org/packages/2d/f4/f1d03a4bc9d9acbc62f4d742b8a319af52f71885079868b2ff8e48a651ee/pydantic_core-2.46.5-cp314-cp314t-musllinux_1_1_aarch64.whl", hash = "sha256:0cdbada856a1c69a7624a64d3d9aefe79300bd6ef827b43a4f265010b9b55184", size = 2144589, upload-time = "2026-08-28T10:00:05.645Z" },
    { url = "https://files.pythonhosted.org/packages/83/f3/7a53bb1356de514a4cd295f25b6ac39237895620c0462d2592b76c16e114/pydantic_core-2.46.5-cp314-cp314t-musllinux_1_1_armv7l.whl", hash = "sha256:545f26c504b27c3758439a5e6d9349931f0a04f855668d5fe323c89e82300a38", size = 2288882, upload-time = "2026-08-28T10:00:07.931Z" },
    { url = "https://files.pythonhosted.org/packages/cd/94/5a81583660c175c59d49ffb09f4b3a44debeaf86a19fca664ae1cdd9ee32/pydantic_core-2.46.5-cp314-cp314t-musllinux_1_1_x86_64.whl", hash = "sha256:ff218293c9c806138dca139765e3b067621be52bcd93cdc14c7711be7ddc90a9", size = 2335210, upload-time = "2026-08-28T10:00:10.177Z" },
    { url = "https://files.pythonhosted.org/packages/5a/9f/5d685c2693b972d1a59c998586e8823712b66603aeff47ee60a4bdaafd37/pydantic_core-2.46.5-cp314-cp314t-win32.whl", hash = "sha256:97cf3eb53a8cccacf9d46686a0926186c9bfb5574f2ed66d3639d5fe117cd3a9", size = 1921180, upload-time = "2026-08-28T10:00:12.35Z" },
    { url = "https://files.pythonhosted.org/packages/70/12/5c94ee16d65a37a15f9e869f5e6256df111154491173801a4c5e800ab548/pydantic_core-2.46.5-cp314-cp314t-win_amd64.whl", hash = "sha256:d2f9fc07a8042a8f95925b35c4f04f469707c981fc33245b6ca187cf5d2dd290", size = 2020515, upload-time = "2026-08-28T10:00:14.774Z" },
    { url = "https://files.pythonhosted.org/packages/63/19/67830dda664e6bdf9285ee2e40f355d0d7d6b92aa0c42e8d217bb8d33d36/pydantic_core-2.46.5-cp314-cp314t-win_arm64.whl", hash = "sha256:acf8a67ba51f4ca9ddbd0e6b3000a65ac51ab734661778b3e7ba64d99a710f2f", size = 1989276, upload-time = "2026-08-28T10:00:16.984Z" },
    { url = "https://files.pythonhosted.org/packages/df/dd/053c2e4303f791f3b8f8a14ab0b22008e8eb21d868c0c90b4f9be705b76a/pydantic_core-2.46.5-graalpy312-graalpy250_312_native-macosx_10_12_x86_64.whl", hash = "sha256:013d6f3483d81e02e7c328831808f336c8596ee33b4bd4026b9ffb1e960b8942", size = 2062540, upload-time = "2026-08-28T10:01:00.318Z" },
    { url = "https://files.pythonhosted.org/packages/d7/dd/a18df751a5e37dd51bfad7f68e766999125bebe68c9e1d10a493ad01bd63/pydantic_core-2.46.5-graalpy312-graalpy250_312_native-macosx_11_0_arm64.whl", hash = "sha256:e9c134bb666dd54b778b9fc0d2b50cbb7f979b9e3716f26a88c9ab3b6fc1dd0f", size = 1902040, upload-time = "2026-08-28T10:01:02.529Z" },
    { url = "https://files.pythonhosted.org/packages/b7/13/01d40f9d07ce8a779fd6e0bd8ad4fba91309500dd67b869e2e219d261a6d/pydantic_core-2.46.5-graalpy312-graalpy250_312_native-manylinux_2_17_aarch64.manylinux2014_aarch64.whl", hash = "sha256:347ec774390c87326a2e4929d58d3f7e8763a104d5d35f4cd595a4c952366433", size = 1967479, upload-time = "2026-08-28T10:01:05.004Z" },
    { url = "https://files.pythonhosted.org/packages/fa/04/c81d4841331c2178b6fb09ae225425e110ed72d990c9fe556c4ec03d1013/pydantic_core-2.46.5-graalpy312-graalpy250_312_native-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", hash = "sha256:8e24d8f05fa2d28513d94e877e9c75ad66175376209b3977f916e240e623193c", size = 2111034, upload-time = "2026-08-28T10:01:07.345Z" },
]

[[package]]
name = "pygments"
version = "2.21.0"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "https://files.pythonhosted.org/packages/49/2e/ced460408999b33da6b31b0021b0f37d329e202d4169aeb164493778f25b/pygments-2.21.0.tar.gz", hash = "sha256:610ca751c9bc2492b38eb9a38a7fbc93edbbb2d7182edaf34e66ae493dee5c8c", size = 5005329, upload-time = "2026-08-17T08:02:48.824Z" }
wheels = [
    { url = "https://files.pythonhosted.org/packages/71/46/17f022dd3e953bf20a04a028a21ec746d942f8d2af30fa0f124fa0e6a684/pygments-2.21.0-py3-none-any.whl", hash = "sha256:2363c69b61c4a97c838da3b130dcd6468f4848992b21a82f2a63ec34377137d9", size = 1250147, upload-time = "2026-08-17T08:02:44.912Z" },
]

[[package]]
name = "pytest"
version = "9.1.1"
source = { registry = "https://pypi.org/simple" }
dependencies = [
    { name = "colorama", marker = "sys_platform == 'win32'" },
    { name = "iniconfig" },
    { name = "packaging" },
    { name = "pluggy" },
    { name = "pygments" },
]
sdist = { url = "https://files.pythonhosted.org/packages/e4/47/b9efed96c114afcfa3c9d3fe98a76a1d14c74a9e266d397cf6eb64be5e01/pytest-9.1.1.tar.gz", hash = "sha256:1088fbde8f2b49d95a549a195707afa7a76a3ce9bcadc26b6d71f0ffda5fe313", size = 1636369, upload-time = "2026-06-19T10:58:32.857Z" }
wheels = [
    { url = "https://files.pythonhosted.org/packages/24/25/1de2678b631f5a49215c6c96fff41ba892b0a34df68d6d80292b1b48aa7f/pytest-9.1.1-py3-none-any.whl", hash = "sha256:37a86b45efb9a47a61a36449063e8e18d0cab3161329fc099eb21783169c4f0c", size = 386536, upload-time = "2026-06-19T10:58:31.347Z" },
]

[[package]]
name = "python-dotenv"
version = "1.2.4"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "https://files.pythonhosted.org/packages/74/26/2fbeedb218a787a5eea551c7532cac4e009f83d689dd2faa0d0353473f86/python_dotenv-1.2.4.tar.gz", hash = "sha256:f0d53e69935a851c0dcc78f3ab7aaccd8cabef0b92382b576b824212902873c0", size = 60824, upload-time = "2026-10-01T05:36:10Z" }
wheels = [
    { url = "https://files.pythonhosted.org/packages/60/d1/38f3a3405989a89ac18390803e70c6ad7c7760da4f9b83cbeca0c44a0c72/python_dotenv-1.2.4-py3-none-any.whl", hash = "sha256:42269a8a5b3fd54ffa6f3d84b18abed50064717576b4ecf03dc4a55d8aa04fdc", size = 23266, upload-time = "2026-10-01T05:36:08.633Z" },
]

[[package]]
name = "pyyaml"
version = "6.0.3"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "https://files.pythonhosted.org/packages/05/8e/961c0007c59b8dd7729d542c61a4d537767a59645b82a0b521206e1e25c2/pyyaml-6.0.3.tar.gz", hash = "sha256:d76623373421df22fb4cf8817020cbb7ef15c725b9d5e45f17e189bfc384190f", size = 130960, upload-time = "2025-09-25T21:33:16.546Z" }
wheels = [
    { url = "https://files.pythonhosted.org/packages/d1/33/422b98d2195232ca1826284a76852ad5a86fe23e31b009c9886b2d0fb8b2/pyyaml-6.0.3-cp312-cp312-macosx_10_13_x86_64.whl", hash = "sha256:7f047e29dcae44602496db43be01ad42fc6f1cc0d8cd6c83d342306c32270196", size = 182063, upload-time = "2025-09-25T21:32:11.445Z" },
    { url = "https://files.pythonhosted.org/packages/89/a0/6cf41a19a1f2f3feab0e9c0b74134aa2ce6849093d5517a0c550fe37a648/pyyaml-6.0.3-cp312-cp312-macosx_11_0_arm64.whl", hash = "sha256:fc09d0aa354569bc501d4e787133afc08552722d3ab34836a80547331bb5d4a0", size = 173973, upload-time = "2025-09-25T21:32:12.492Z" },
    { url = "https://files.pythonhosted.org/packages/ed/23/7a778b6bd0b9a8039df8b1b1d80e2e2ad78aa04171592c8a5c43a56a6af4/pyyaml-6.0.3-cp312-cp312-manylinux2014_aarch64.manylinux_2_17_aarch64.manylinux_2_28_aarch64.whl", hash = "sha256:9149cad251584d5fb4981be1ecde53a1ca46c891a79788c0df828d2f166bda28", size = 775116, upload-time = "2025-09-25T21:32:13.652Z" },
    { url = "https://files.pythonhosted.org/packages/65/30/d7353c338e12baef4ecc1b09e877c1970bd3382789c159b4f89d6a70dc09/pyyaml-6.0.3-cp312-cp312-manylinux2014_s390x.manylinux_2_17_s390x.manylinux_2_28_s390x.whl", hash = "sha256:5fdec68f91a0c6739b380c83b951e2c72ac0197ace422360e6d5a959d8d97b2c", size = 844011, upload-time = "2025-09-25T21:32:15.21Z" },
    { url = "https://files.pythonhosted.org/packages/8b/9d/b3589d3877982d4f2329302ef98a8026e7f4443c765c46cfecc8858c6b4b/pyyaml-6.0.3-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl", hash = "sha256:ba1cc08a7ccde2d2ec775841541641e4548226580ab850948cbfda66a1befcdc", size = 807870, upload-time = "2025-09-25T21:32:16.431Z" },
    { url = "https://files.pythonhosted.org/packages/05/c0/b3be26a015601b822b97d9149ff8cb5ead58c66f981e04fedf4e762f4bd4/pyyaml-6.0.3-cp312-cp312-musllinux_1_2_aarch64.whl", hash = "sha256:8dc52c23056b9ddd46818a57b78404882310fb473d63f17b07d5c40421e47f8e", size = 761089, upload-time = "2025-09-25T21:32:17.56Z" },
    { url = "https://files.pythonhosted.org/packages/be/8e/98435a21d1d4b46590d5459a22d88128103f8da4c2d4cb8f14f2a96504e1/pyyaml-6.0.3-cp312-cp312-musllinux_1_2_x86_64.whl", hash = "sha256:41715c910c881bc081f1e8872880d3c650acf13dfa8214bad49ed4cede7c34ea", size = 790181, upload-time = "2025-09-25T21:32:18.834Z" },
    { url = "https://files.pythonhosted.org/packages/74/93/7baea19427dcfbe1e5a372d81473250b379f04b1bd3c4c5ff825e2327202/pyyaml-6.0.3-cp312-cp312-win32.whl", hash = "sha256:96b533f0e99f6579b3d4d4995707cf36df9100d67e0c8303a0c55b27b5f99bc5", size = 137658, upload-time = "2025-09-25T21:32:20.209Z" },
    { url = "https://files.pythonhosted.org/packages/86/bf/899e81e4cce32febab4fb42bb97dcdf66bc135272882d1987881a4b519e9/pyyaml-6.0.3-cp312-cp312-win_amd64.whl", hash = "sha256:5fcd34e47f6e0b794d17de1b4ff496c00986e1c83f7ab2fb8fcfe9616ff7477b", size = 154003, upload-time = "2025-09-25T21:32:21.167Z" },
    { url = "https://files.pythonhosted.org/packages/1a/08/67bd04656199bbb51dbed1439b7f27601dfb576fb864099c7ef0c3e55531/pyyaml-6.0.3-cp312-cp312-win_arm64.whl", hash = "sha256:64386e5e707d03a7e172c0701abfb7e10f0fb753ee1d773128192742712a98fd", size = 140344, upload-time = "2025-09-25T21:32:22.617Z" },
    { url = "https://files.pythonhosted.org/packages/d1/11/0fd08f8192109f7169db964b5707a2f1e8b745d4e239b784a5a1dd80d1db/pyyaml-6.0.3-cp313-cp313-macosx_10_13_x86_64.whl", hash = "sha256:8da9669d359f02c0b91ccc01cac4a67f16afec0dac22c2ad09f46bee0697eba8", size = 181669, upload-time = "2025-09-25T21:32:23.673Z" },
    { url = "https://files.pythonhosted.org/packages/b1/16/95309993f1d3748cd644e02e38b75d50cbc0d9561d21f390a76242ce073f/pyyaml-6.0.3-cp313-cp313-macosx_11_0_arm64.whl", hash = "sha256:2283a07e2c21a2aa78d9c4442724ec1eb15f5e42a723b99cb3d822d48f5f7ad1", size = 173252, upload-time = "2025-09-25T21:32:25.149Z" },
    { url = "https://files.pythonhosted.org/packages/50/31/b20f376d3f810b9b2371e72ef5adb33879b25edb7a6d072cb7ca0c486398/pyyaml-6.0.3-cp313-cp313-manylinux2014_aarch64.manylinux_2_17_aarch64.manylinux_2_28_aarch64.whl", hash = "sha256:ee2922902c45ae8ccada2c5b501ab86c36525b883eff4255313a253a3160861c", size = 767081, upload-time = "2025-09-25T21:32:26.575Z" },
    { url = "https://files.pythonhosted.org/packages/49/1e/a55ca81e949270d5d4432fbbd19dfea5321eda7c41a849d443dc92fd1ff7/pyyaml-6.0.3-cp313-cp313-manylinux2014_s390x.manylinux_2_17_s390x.manylinux_2_28_s390x.whl", hash = "sha256:a33284e20b78bd4a18c8c2282d549d10bc8408a2a7ff57653c0cf0b9be0afce5", size = 841159, upload-time = "2025-09-25T21:32:27.727Z" },
    { url = "https://files.pythonhosted.org/packages/74/27/e5b8f34d02d9995b80abcef563ea1f8b56d20134d8f4e5e81733b1feceb2/pyyaml-6.0.3-cp313-cp313-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl", hash = "sha256:0f29edc409a6392443abf94b9cf89ce99889a1dd5376d94316ae5145dfedd5d6", size = 801626, upload-time = "2025-09-25T21:32:28.878Z" },
    { url = "https://files.pythonhosted.org/packages/f9/11/ba845c23988798f40e52ba45f34849aa8a1f2d4af4b798588010792ebad6/pyyaml-6.0.3-cp313-cp313-musllinux_1_2_aarch64.whl", hash = "sha256:f7057c9a337546edc7973c0d3ba84ddcdf0daa14533c2065749c9075001090e6", size = 753613, upload-time = "2025-09-25T21:32:30.178Z" },
    { url = "https://files.pythonhosted.org/packages/3d/e0/7966e1a7bfc0a45bf0a7fb6b98ea03fc9b8d84fa7f2229e9659680b69ee3/pyyaml-6.0.3-cp313-cp313-musllinux_1_2_x86_64.whl", hash = "sha256:eda16858a3cab07b80edaf74336ece1f986ba330fdb8ee0d6c0d68fe82bc96be", size = 794115, upload-time = "2025-09-25T21:32:31.353Z" },
    { url = "https://files.pythonhosted.org/packages/de/94/980b50a6531b3019e45ddeada0626d45fa85cbe22300844a7983285bed3b/pyyaml-6.0.3-cp313-cp313-win32.whl", hash = "sha256:d0eae10f8159e8fdad514efdc92d74fd8d682c933a6dd088030f3834bc8e6b26", size = 137427, upload-time = "2025-09-25T21:32:32.58Z" },
    { url = "https://files.pythonhosted.org/packages/97/c9/39d5b874e8b28845e4ec2202b5da735d0199dbe5b8fb85f91398814a9a46/pyyaml-6.0.3-cp313-cp313-win_amd64.whl", hash = "sha256:79005a0d97d5ddabfeeea4cf676af11e647e41d81c9a7722a193022accdb6b7c", size = 154090, upload-time = "2025-09-25T21:32:33.659Z" },
    { url = "https://files.pythonhosted.org/packages/73/e8/2bdf3ca2090f68bb3d75b44da7bbc71843b19c9f2b9cb9b0f4ab7a5a4329/pyyaml-6.0.3-cp313-cp313-win_arm64.whl", hash = "sha256:5498cd1645aa724a7c71c8f378eb29ebe23da2fc0d7a08071d89469bf1d2defb", size = 140246, upload-time = "2025-09-25T21:32:34.663Z" },
    { url = "https://files.pythonhosted.org/packages/9d/8c/f4bd7f6465179953d3ac9bc44ac1a8a3e6122cf8ada906b4f96c60172d43/pyyaml-6.0.3-cp314-cp314-macosx_10_13_x86_64.whl", hash = "sha256:8d1fab6bb153a416f9aeb4b8763bc0f22a5586065f86f7664fc23339fc1c1fac", size = 181814, upload-time = "2025-09-25T21:32:35.712Z" },
    { url = "https://files.pythonhosted.org/packages/bd/9c/4d95bb87eb2063d20db7b60faa3840c1b18025517ae857371c4dd55a6b3a/pyyaml-6.0.3-cp314-cp314-macosx_11_0_arm64.whl", hash = "sha256:34d5fcd24b8445fadc33f9cf348c1047101756fd760b4dacb5c3e99755703310", size = 173809, upload-time = "2025-09-25T21:32:36.789Z" },
    { url = "https://files.pythonhosted.org/packages/92/b5/47e807c2623074914e29dabd16cbbdd4bf5e9b2db9f8090fa64411fc5382/pyyaml-6.0.3-cp314-cp314-manylinux2014_aarch64.manylinux_2_17_aarch64.manylinux_2_28_aarch64.whl", hash = "sha256:501a031947e3a9025ed4405a168e6ef5ae3126c59f90ce0cd6f2bfc477be31b7", size = 766454, upload-time = "2025-09-25T21:32:37.966Z" },
    { url = "https://files.pythonhosted.org/packages/02/9e/e5e9b168be58564121efb3de6859c452fccde0ab093d8438905899a3a483/pyyaml-6.0.3-cp314-cp314-manylinux2014_s390x.manylinux_2_17_s390x.manylinux_2_28_s390x.whl", hash = "sha256:b3bc83488de33889877a0f2543ade9f70c67d66d9ebb4ac959502e12de895788", size = 836355, upload-time = "2025-09-25T21:32:39.178Z" },
    { url = "https://files.pythonhosted.org/packages/88/f9/16491d7ed2a919954993e48aa941b200f38040928474c9e85ea9e64222c3/pyyaml-6.0.3-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl", hash = "sha256:c458b6d084f9b935061bc36216e8a69a7e293a2f1e68bf956dcd9e6cbcd143f5", size = 794175, upload-time = "2025-09-25T21:32:40.865Z" },
    { url = "https://files.pythonhosted.org/packages/dd/3f/5989debef34dc6397317802b527dbbafb2b4760878a53d4166579111411e/pyyaml-6.0.3-cp314-cp314-musllinux_1_2_aarch64.whl", hash = "sha256:7c6610def4f163542a622a73fb39f534f8c101d690126992300bf3207eab9764", size = 755228, upload-time = "2025-09-25T21:32:42.084Z" },
    { url = "https://files.pythonhosted.org/packages/d7/ce/af88a49043cd2e265be63d083fc75b27b6ed062f5f9fd6cdc223ad62f03e/pyyaml-6.0.3-cp314-cp314-musllinux_1_2_x86_64.whl", hash = "sha256:5190d403f121660ce8d1d2c1bb2ef1bd05b5f68533fc5c2ea899bd15f4399b35", size = 789194, upload-time = "2025-09-25T21:32:43.362Z" },
    { url = "https://files.pythonhosted.org/packages/23/20/bb6982b26a40bb43951265ba29d4c246ef0ff59c9fdcdf0ed04e0687de4d/pyyaml-6.0.3-cp314-cp314-win_amd64.whl", hash = "sha256:4a2e8cebe2ff6ab7d1050ecd59c25d4c8bd7e6f400f5f82b96557ac0abafd0ac", size = 156429, upload-time = "2025-09-25T21:32:57.844Z" },
    { url = "https://files.pythonhosted.org/packages/f4/f4/a4541072bb9422c8a883ab55255f918fa378ecf083f5b85e87fc2b4eda1b/pyyaml-6.0.3-cp314-cp314-win_arm64.whl", hash = "sha256:93dda82c9c22deb0a405ea4dc5f2d0cda384168e466364dec6255b293923b2f3", size = 143912, upload-time = "2025-09-25T21:32:59.247Z" },
    { url = "https://files.pythonhosted.org/packages/7c/f9/07dd09ae774e4616edf6cda684ee78f97777bdd15847253637a6f052a62f/pyyaml-6.0.3-cp314-cp314t-macosx_10_13_x86_64.whl", hash = "sha256:02893d100e99e03eda1c8fd5c441d8c60103fd175728e23e431db1b589cf5ab3", size = 189108, upload-time = "2025-09-25T21:32:44.377Z" },
    { url = "https://files.pythonhosted.org/packages/4e/78/8d08c9fb7ce09ad8c38ad533c1191cf27f7ae1effe5bb9400a46d9437fcf/pyyaml-6.0.3-cp314-cp314t-macosx_11_0_arm64.whl", hash = "sha256:c1ff362665ae507275af2853520967820d9124984e0f7466736aea23d8611fba", size = 183641, upload-time = "2025-09-25T21:32:45.407Z" },
    { url = "https://files.pythonhosted.org/packages/7b/5b/3babb19104a46945cf816d047db2788bcaf8c94527a805610b0289a01c6b/pyyaml-6.0.3-cp314-cp314t-manylinux2014_aarch64.manylinux_2_17_aarch64.manylinux_2_28_aarch64.whl", hash = "sha256:6adc77889b628398debc7b65c073bcb99c4a0237b248cacaf3fe8a557563ef6c", size = 831901, upload-time = "2025-09-25T21:32:48.83Z" },
    { url = "https://files.pythonhosted.org/packages/8b/cc/dff0684d8dc44da4d22a13f35f073d558c268780ce3c6ba1b87055bb0b87/pyyaml-6.0.3-cp314-cp314t-manylinux2014_s390x.manylinux_2_17_s390x.manylinux_2_28_s390x.whl", hash = "sha256:a80cb027f6b349846a3bf6d73b5e95e782175e52f22108cfa17876aaeff93702", size = 861132, upload-time = "2025-09-25T21:32:50.149Z" },
    { url = "https://files.pythonhosted.org/packages/b1/5e/f77dc6b9036943e285ba76b49e118d9ea929885becb0a29ba8a7c75e29fe/pyyaml-6.0.3-cp314-cp314t-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl", hash = "sha256:00c4bdeba853cc34e7dd471f16b4114f4162dc03e6b7afcc2128711f0eca823c", size = 839261, upload-time = "2025-09-25T21:32:51.808Z" },
    { url = "https://files.pythonhosted.org/packages/ce/88/a9db1376aa2a228197c58b37302f284b5617f56a5d959fd1763fb1675ce6/pyyaml-6.0.3-cp314-cp314t-musllinux_1_2_aarch64.whl", hash = "sha256:66e1674c3ef6f541c35191caae2d429b967b99e02040f5ba928632d9a7f0f065", size = 805272, upload-time = "2025-09-25T21:32:52.941Z" },
    { url = "https://files.pythonhosted.org/packages/da/92/1446574745d74df0c92e6aa4a7b0b3130706a4142b2d1a5869f2eaa423c6/pyyaml-6.0.3-cp314-cp314t-musllinux_1_2_x86_64.whl", hash = "sha256:16249ee61e95f858e83976573de0f5b2893b3677ba71c9dd36b9cf8be9ac6d65", size = 829923, upload-time = "2025-09-25T21:32:54.537Z" },
    { url = "https://files.pythonhosted.org/packages/f0/7a/1c7270340330e575b92f397352af856a8c06f230aa3e76f86b39d01b416a/pyyaml-6.0.3-cp314-cp314t-win_amd64.whl", hash = "sha256:4ad1906908f2f5ae4e5a8ddfce73c320c2a1429ec52eafd27138b7f1cbe341c9", size = 174062, upload-time = "2025-09-25T21:32:55.767Z" },
    { url = "https://files.pythonhosted.org/packages/f1/12/de94a39c2ef588c7e6455cfbe7343d3b2dc9d6b6b2f40c4c6565744c873d/pyyaml-6.0.3-cp314-cp314t-win_arm64.whl", hash = "sha256:ebc55a14a21cb14062aa4162f906cd962b28e2e9ea38f9b4391244cd8de4ae0b", size = 149341, upload-time = "2025-09-25T21:32:56.828Z" },
]

[[package]]
name = "starlette"
version = "1.7.0"
source = { registry = "https://pypi.org/simple" }
dependencies = [
    { name = "anyio" },
    { name = "typing-extensions", marker = "python_full_version < '3.13'" },
]
sdist = { url = "https://files.pythonhosted.org/packages/7b/2b/3850dc6bf7ef71b088962eba31dafc6cffd2f96e577ebb0bb316df96da3e/starlette-1.7.0.tar.gz", hash = "sha256:c79f74ea63cff761804fbbfb182f1e0b440c2d07b164d24700c5a1bab5d6ff5d", size = 2736246, upload-time = "2026-09-23T07:30:26.35Z" }
wheels = [
    { url = "https://files.pythonhosted.org/packages/4e/d6/1ec1b290f9e0fb067899b61e1d37a30c923068bad260b216dbe37a7d2967/starlette-1.7.0-py3-none-any.whl", hash = "sha256:67f8e99895493dd2911a03f11314af6ceebeae4e704bb9f43dfc6a9db151c93e", size = 78980, upload-time = "2026-09-23T07:30:24.567Z" },
]

[[package]]
name = "typing-extensions"
version = "4.16.0"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "https://files.pythonhosted.org/packages/f6/cc/6253133b5bb138fc3306cebfbda2c520f545d36b5be2c7255cc528bb45d6/typing_extensions-4.16.0.tar.gz", hash = "sha256:dc983d19a509c94dba722ee6abd33940f7c05a89e243c47e907eb4db6f1a43e5", size = 113555, upload-time = "2026-07-02T08:40:05.92Z" }
wheels = [
    { url = "https://files.pythonhosted.org/packages/49/d3/b8441a820a491ddfc024b0b0cf0393375b75ea13866d9c66727e54c2fc80/typing_extensions-4.16.0-py3-none-any.whl", hash = "sha256:481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8", size = 45571, upload-time = "2026-07-02T08:40:04.659Z" },
]

[[package]]
name = "typing-inspection"
version = "0.4.4"
source = { registry = "https://pypi.org/simple" }
dependencies = [
    { name = "typing-extensions" },
]
sdist = { url = "https://files.pythonhosted.org/packages/a3/26/b09b8010994eccc3c09092e6b34058f36a460eea2d4c3e8b910c695975a0/typing_inspection-0.4.4.tar.gz", hash = "sha256:547274fa6b0a561ccf549cc9524b999a578e737d015d8709d021f9d0d13bea47", size = 76928, upload-time = "2026-08-12T12:37:25.997Z" }
wheels = [
    { url = "https://files.pythonhosted.org/packages/67/81/4add07e5172b7ac40d8ed5ff580409a7801a4fe26d529bdd915401dabfbe/typing_inspection-0.4.4-py3-none-any.whl", hash = "sha256:65b8397ba37ccbce054456aaccddfc91e6e3083c92824df348d96ca832f3f147", size = 14750, upload-time = "2026-08-12T12:37:24.648Z" },
]

[[package]]
name = "tzdata"
version = "2026.5"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "https://files.pythonhosted.org/packages/d9/68/f1b440335057bfce71b6e50a9d09445aa2ecbd08359a337976627b8409e7/tzdata-2026.5.tar.gz", hash = "sha256:8cc73c0a0bfca7dbfa59235d60b2eff82231dee33f53d206db1acd9173cfc0a7", size = 200404, upload-time = "2026-10-03T09:23:14.143Z" }
wheels = [
    { url = "https://files.pythonhosted.org/packages/94/21/1e5995a1c920cce14e4bffae20c665ec10e7ed03ab25e006cd741092b718/tzdata-2026.5-py2.py3-none-any.whl", hash = "sha256:b683bd1b6659ddcd810ff02ad09ba821d4bf1065072805063eb35c49617905ac", size = 347996, upload-time = "2026-10-03T09:23:12.535Z" },
]

[[package]]
name = "uvicorn"
version = "0.54.0"
source = { registry = "https://pypi.org/simple" }
dependencies = [
    { name = "click" },
    { name = "h11" },
]
sdist = { url = "https://files.pythonhosted.org/packages/da/34/30e9280707135d2cfc589dfff3cb796bd07a3aeb1a3e415ba09dd89d7bb4/uvicorn-0.54.0.tar.gz", hash = "sha256:a2e33cbfaa0306f8e6b0c13e0cb89d7d7a2da3e62b90c66e18c33d9807b28620", size = 112283, upload-time = "2026-09-25T06:52:37.601Z" }
wheels = [
    { url = "https://files.pythonhosted.org/packages/38/0c/b54a4fdd7f90a3af8b02ebc9ce6712c2c208b7926a2f7bad95c33ebbe943/uvicorn-0.54.0-py3-none-any.whl", hash = "sha256:505bdb0f318731d45f1f712071fc781a8981f6847a31c902c9f5e652d4f67faf", size = 87427, upload-time = "2026-09-25T06:52:35.829Z" },
]
