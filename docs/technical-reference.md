# Referencia técnica de Jira Relay

Complementa el [README](../README.md) con contratos de ejecución, acciones, datos y diagnóstico.
La [guía de Studio](studio.md) cubre la interfaz. Todo corresponde al código de este checkout;
no depende de un despliegue o una cuenta particular.

## Mapa del código y fuentes de verdad

| Archivo | Responsabilidad |
| --- | --- |
| `app/main.py` | Única API HTTP: modelos, autorización, límites, gestión, webhook firmado y probes. |
| `app/config.py` | Parámetros, resolución de secretos, rechazo de configuración legacy. |
| `app/auth.py`, `app/tokens.py` | Identidades/permisos, bootstrap, emisión, revocación y rotación en DB. |
| `app/dsl.py`, `app/paths.py` | Parseo YAML estricto, schema, filtros, referencias y bindings. |
| `app/safety.py` | Alcance positivo obligatorio, especificidad del cambio y destinos unitarios. |
| `app/store.py` | Transacciones, revisiones, entregas, evaluaciones, runs y auditoría. |
| `app/actors.py` | Identificación de actor y exclusión global de las cuentas del servicio. |
| `app/worker.py` | Cola durable, leases, pasos, presupuesto y conector Bitbucket. |
| `app/jira.py`, `app/transport.py` | API Jira, comprobaciones previas, ADF y respuestas HTTP acotadas. |
| `app/catalog.py` | Descubrimiento acotado de rutas, tipos y ejemplos observados. |
| `app/migrate.py`, `app/schema.py`, `app/migrations/` | Historial SQL, identidad de instalación y huella estructural. |
| `app/health.py`, `app/retention.py` | Probes y redacción explícita de datos antiguos. |
| `tools/studio_server.py`, `tools/studio_access.py` | Gateway de GUI y sesiones administrativas en memoria. |
| `frontend/src/model.ts`, `Forms.tsx`, `ActionHelp.tsx` | Modelo del editor, formularios y ayuda por acción. |
| `frontend/src/main.tsx`, `Canvas.tsx`, `Access.tsx` | Navegación, lienzo secuencial y administración de credenciales. |
| `tests/`, `frontend/src/*.test.*` | Pruebas del contrato, permisos, persistencia, errores y GUI. |
| `tools/selftest.py`, `smoke_container.py`, `smoke_studio.py` | Verificación reproducible sin proveedores externos. |
| `tools/e2e_live.py` | Pruebas opt-in con efectos reales, destinos explícitos y evidencia privada. |

Para desarrollar con una IA: leer README, esta referencia y el código afectado; consultar
`GET /v1/schema` y `/openapi.json` de la instancia objetivo. No inferir formatos de otro producto.
No editar migraciones aplicadas, revisiones guardadas, sus checksums ni el ledger para forzar arranque.
No leer/publicar `.env`, secretos, dumps ni `work/` como documentación. Agregar pruebas positivas,
negativas y de fallo ambiguo al cambiar un contrato. No activar un flujo como parte de guardarlo.

## Recorrido HTTP: validar, evaluar, guardar y activar

Las llamadas de YAML envían el documento UTF-8 directamente (`application/yaml`), no un wrapper
JSON. Evaluar/comparar reciben JSON que contiene YAML como cadena. Las respuestas son JSON salvo
`/metrics`. Para cuerpos y respuestas exactas, descargar OpenAPI autenticado de la versión desplegada.

Ejemplo completo de cliente con la biblioteca estándar de Python, ejecutado desde la raíz del repo.
Configurar `RELAY_API_URL` y `RELAY_TOKEN_FILE` fuera del script. Son variables **de este ejemplo**,
no parámetros nuevos del servidor. El archivo contiene sólo un token delegado con
`api:read,rules:write`; para activar necesita además `flows:operate`.

El script valida, evalúa un payload sintético y guarda un borrador. **No activa ni escribe en Jira**.
Guardar sí modifica la DB de reglas; usar una instancia propia/autorizada. El ejemplo conserva
el proyecto `KAN`: adaptar el YAML antes de ejecutarlo.

```python
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

base = os.environ["RELAY_API_URL"].rstrip("/")
url = urlsplit(base)
if (url.scheme not in {"http", "https"} or not url.hostname
        or url.username or url.password or url.query or url.fragment):
    raise ValueError("URL de API inválida")
if url.scheme != "https" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
    raise ValueError("Usar HTTPS fuera de loopback")
token = Path(os.environ["RELAY_TOKEN_FILE"]).read_text().rstrip("\r\n")

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # No reenviar la autorización a otro destino.

client = build_opener(ProxyHandler({}), NoRedirect())

def call(method, route, body=None, yaml_body=False):
    data = None if body is None else (
        body.encode("utf-8") if yaml_body else json.dumps(body).encode("utf-8")
    )
    request = Request(base + route, data=data, method=method, headers={
        "Authorization": "Bearer " + token,
        "Content-Type": "application/yaml" if yaml_body else "application/json",
    })
    try:
        with client.open(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        raise RuntimeError(f"API devolvió HTTP {error.code}; revisar la instancia") from None

yaml_text = Path("examples/jira-assignment-append.yaml").read_text()
validated = call("POST", "/v1/rules/validate", yaml_text, yaml_body=True)
evaluation = call("POST", "/v1/evaluate", {
    "yaml": yaml_text,
    "source": "jira",
    "event_type": "jira:issue_updated",
    "payload": {
        "issue": {"key": "KAN-123", "fields": {"project": {"key": "KAN"}}},
        "changelog": {"items": [{"fieldId": "assignee", "to": "example-account"}]},
    },
})
assert validated["valid"] is True
assert evaluation["executed"] is False
assert evaluation["results"][0]["trace"]["matched"] is True
saved = call("POST", "/v1/rules", yaml_text, yaml_body=True)
print({key: saved[key] for key in ("name", "version", "sha256", "unchanged")})

# Sólo tras revisar la versión guardada y autorizar efectos reales:
# call("POST", f"/v1/rules/{saved['name']}/activate", {
#     "version": saved["version"], "sha256": saved["sha256"],
# })
```

También probar payloads negativos: `to: null`, `to: ""`, ausencia de `to`, otro campo y otro
proyecto. Evaluar contra históricos usa `event_ids: ["<uuid>"]` (hasta 100), sin `payload`.
No confundir `matched:true` con un ensayo de acciones: no verifica existencia del ticket,
permisos remotos, bindings de pasos ni la política de exclusión del actor.

Para desactivar: `POST /v1/rules/{name}/disable`, sin body. Para rollback de **regla**, consultar
una versión anterior y activarla con su checksum; debe seguir cumpliendo las reglas de seguridad
actuales. No revierte efectos anteriores. Runs en curso conservan su revisión fijada; cambiar
la versión activa no los convierte a la nueva. Desactivar sí bloquea los pasos aún pendientes.

`POST /v1/events` exige `flows:operate`, `Idempotency-Key` y exactamente
`{"source":"jira","event_type":"jira:issue_updated","payload":{...}}` (también admite `source:test`).
No es simulación: puede encolar efectos de las reglas activas. `POST /v1/events/{id}/execute`
evalúa las versiones actualmente activas; no duplica un run del mismo evento/regla/versión,
pero una versión nueva puede producir nuevos efectos. No usar replay como reintento de una
escritura ambigua. Eventos excluidos o redactados no se pueden ejecutar.

## Acciones: entradas y resultados

Los nombres son los discriminadores YAML exactos. `fields` es un mapa de bindings de valores;
los destinos de tickets son bindings de texto restringidos a una única clave Jira al resolver.
Los IDs de tipo, estado y vínculo son cadenas de dígitos, no sus nombres visibles.

| `type` | Entradas | Resultado durable accesible al paso siguiente |
| --- | --- | --- |
| `jira.issue.get` | `issue`; opcional `require` sobre el ticket actual. | `key`, `fields`, `guard_matched`; guarda falsa: `key`, `guard_matched:false`, `stop:true`. |
| `jira.issue.create` | `project`, `issue_type_id`, `fields`; opcional `parent`. | `key`, `operation_id`. |
| `jira.issue.clone` | Lo anterior más `source`; `copy_fields` por defecto `[summary, description]`; `fields` sobreescribe lo copiado. | `key`, `operation_id`. |
| `jira.issue.edit` | `issue`; al menos `fields` o `append_description`; no combinar append con `fields.description`. | `key`, `updated_fields`, o `key`, `no_change:true`. |
| `jira.issue.transition` | `issue`, `from_status_id`, `to_status_id`; opcional `fields`. | `key`, `status_id`; puede incluir `no_change:true`. |
| `jira.comment.add` | `issue`, `text` (texto convertido a ADF). | `key`, `comment_id`, `operation_id`. |
| `jira.issue.link` | `inward`, `outward`, `link_type_id`. | `inward`, `outward`, `link_type_id`. |
| `bitbucket.pipeline` | `workspace`, `repository`, `pipeline`; `branch` por defecto `master`; opcional `variables`. | Al completar: `state: COMPLETED`, `result: SUCCESSFUL`. El UUID remoto está en el run. |

Un `get.require` falso **termina la secuencia sin error** y sin ejecutar los pasos restantes;
no crea un `else`. Su filtro parte de `fields.status.id`, no de `issue.fields.status.id`.
Create/clone no devuelven un ticket completo: para leer el nuevo ticket, agregar un get con
`issue: {path: '_steps["0"].key'}`. Clone no copia comentarios, adjuntos, vínculos ni historial.

Campos editables del DSL: `summary`, `description`, `labels`, `priority`, `assignee`, `duedate`
y `customfield_<número>`. El backend no descubre ni concede permisos Jira. Objetos tipados:
por ejemplo `assignee: {value: {accountId: "<id>"}}`,
`priority: {value: {id: "<id>"}}`, `labels: {value: [operaciones]}`.
Jira decide qué campos/valores están habilitados para el proyecto y tipo de ticket. No se puede
inyectar `project`, `issuetype`, `status`, `security` o `reporter` dentro de `fields`.

Cross-project está permitido si la cuenta tiene acceso. El filtro acota el **evento origen**;
el destino puede ser otro ticket/proyecto explícito. No hay búsquedas ni escritura masiva:
cada referencia debe resolver a una clave como `OPS-42`, nunca un ID numérico, lista, objeto,
URL o JQL. Parent debe pertenecer al proyecto de la creación/clonado.

`description` de texto se convierte a ADF; un documento ADF puede pasarse como valor JSON.
Append lee la descripción actual y agrega un párrafo preservando los nodos existentes, no el
snapshot del webhook. Es lectura + escritura, **no operación atómica ante ediciones concurrentes**.

### Conectar Bitbucket

El worker usa `BITBUCKET_API_TOKEN`/`_FILE` como Access Token de repositorio/proyecto/workspace mediante Bearer, sin email,
contra `https://api.bitbucket.org/2.0/repositories/{workspace}/{repository}/pipelines/`.
Cada regla define el workspace y repositorio destino. El servicio no restringe esos
destinos con una lista adicional; el token debe permitir lanzar y consultar pipelines
en los repositorios seleccionados. Validar permisos efectivos
con la prueba opt-in; no deducirlos de que el login general funciona.

El repositorio debe tener la rama y un pipeline `custom` del nombre seleccionado en
`bitbucket-pipelines.yml`. Jira Relay no crea el repo, no instala ese YAML y no verifica
la lógica interna del pipeline. `workspace`, `repository`, `branch` y `pipeline` son literales,
no templates dinámicos. Las variables admiten datos escalares, hasta 50 entradas; sus nombres
son mayúsculas/dígitos/guion bajo y no pueden usarse para pasar secretos. El conector envía
`secured:false`: guardar secretos directamente en la configuración segura de Bitbucket.

## Contexto y bindings

`when` lee el evento original. `some.where` lee cada elemento de su lista. `get.require` lee
el ticket consultado. Las entradas de acciones leen el evento más `_steps`, un objeto construido
por el worker desde resultados durables anteriores **de ese mismo run**. Un webhook no puede
inyectarlo y los pasos futuros no están disponibles.

El paso visible 1 ocupa la clave `"0"`: `_steps["0"].key`. No usar `_steps[0]` (eso exige una
lista), `_steps["0"].issue.key` (el resultado no tiene wrapper `issue`) ni asumir que una edición
actualiza `issue.fields`: ese snapshot permanece como llegó en el evento.

| Forma | Semántica |
| --- | --- |
| `"texto"` o `{value: "texto"}` | Literal; la forma explícita no interpola `${...}`. |
| `{path: issue.key}` | Referencia a un valor. |
| `'${issue.fields.labels}'` | Referencia completa; preserva el tipo JSON en campos que lo admiten. |
| `'Ticket ${issue.key}'` | Interpolación de escalares no nulos dentro de texto. |
| `'$${issue.key}'` | Texto literal `${issue.key}`. |
| `{value: {accountId: "..."}}` | Objeto literal; el wrapper evita confundirlo con un binding. |

`default` existe en bindings de texto `{path: ..., default: "..."}`, no en los valores JSON de
`fields` ni en templates. En Bitbucket cubre ausente/null; los bindings de texto Jira lo aplican
a ausencia y luego validan tipo. Un null existente no equivale a ausencia. Para destinos Jira
de reglas con efectos no se permiten fallbacks ni templates parciales: evita editar un ticket
alternativo cuando falta el dato esperado. Para formas exactas, consultar el schema de esa acción.

## Filtros y límites

`eq`/`ne` comparan tipo y valor (`true` no es `1`, `1` no es `1.0`); `in` busca coincidencia
tipada en una lista; `contains` busca elemento tipado en lista o substring en texto.
`exists` prueba presencia, incluso si el valor es null. `gt/gte/lt/lte` son comparaciones
numéricas y no comparan fechas textuales. Ausencia hace falsos los predicados salvo
`exists:false`; `not` invierte el resultado de su filtro, también sobre ausencia.
`all` exige todos, `any` alguno y `some` un elemento que cumpla todo su `where`.

| Recurso | Límite del contrato |
| --- | --- |
| YAML | 64 KiB UTF-8, sin aliases ni claves duplicadas; sólo valores representables en JSON. |
| Regla | `name` de 3–80 caracteres `[a-z][a-z0-9-]{2,79}`, descripción hasta 2.000, 1–10 acciones. |
| Filtro | Profundidad 12, 128 nodos, 1–32 hijos por all/any, hasta 100 valores en in; 10.000 evaluaciones internas. |
| Path | 500 caracteres; puntos, claves entre comillas JSON e índices no negativos. |
| Template | 4.096 caracteres y 50 referencias; texto interpolado también acotado. |
| Campos Jira | Hasta 30 bindings; literal JSON de cada valor hasta 16.000 bytes; campos resueltos hasta 32.000 bytes. |
| Jira | Summary de 1–255 caracteres útiles; hasta 30 labels contando las reservadas. Respuesta HTTP hasta 256.000 bytes. |
| Bitbucket | Hasta 50 variables; valor final hasta 4.096 caracteres; body del pipeline hasta 10.000 bytes. |
| HTTP entrante | `MAX_BODY_BYTES`: 10 MiB por defecto/máximo. No eleva el límite propio de YAML. |
| Lecturas API | Eventos/runs: hasta 100 por página; evaluate/compare: hasta 100 event IDs. |

El tamaño máximo no es una promesa de throughput. La API evalúa reglas activas en el ingreso
transaccional; no existe una cola separada para distribuir eventos. El coste depende del número
de reglas activas y sus filtros. Cada proceso runtime usa un pool DB de 1–8 conexiones, timeout
de conexión 10 s, statement 15 s y lock 10 s. Cada worker ejecuta un paso por tick, sin pool de
acciones paralelas interno; agregar workers aumenta concurrencia entre runs, no dentro de uno.
No se garantiza orden entre eventos de un ticket ni se serializan por ticket. Dimensionar y
ensayar conexiones, CPU/memoria, ingreso y cuotas antes de escalar réplicas.

## Datos, checksums y tenant

| Dato | Persistencia y finalidad |
| --- | --- |
| Instalación | `installation` fija un único tenant por base. No es multitenancy por header ni RLS por cliente. |
| Revisiones | `rules` apunta a `active_version`; `rule_versions` conserva YAML original, spec normalizada y checksum. |
| Entregas | `events` conserva raw bytes, payload JSON, actor/resultado de procesamiento e identidad única tenant/source/delivery. |
| Decisiones | `evaluations` guarda coincidencia y explicación por evento/regla/versión. |
| Ejecuciones | `runs` conserva versión fijada, paso, estado, intentos, lease y resultado/UUID externo. `run_log` conserva la secuencia. |
| Cuotas | `jira_write_attempts` y `connector_write_attempts` reservan intentos antes de escribir en una ventana móvil de 24 h. |
| Identidad | `auth_tokens`, `auth_bootstrap_state` guardan hashes/estado; `service_actors` y `service_actor_credentials` conservan cuentas conocidas. |
| Operación | `audit`, `worker_heartbeats`, `schema_migrations` registran cambios, salud e historial SQL. |

**Tres checksums distintos:**

- Regla: SHA-256 del JSON canónico de la especificación validada, con claves ordenadas y sin
  espacios superfluos. No es el hash del archivo YAML crudo: comentarios/estilo no crean una
  revisión si la spec coincide con la última. Cambiar contenido semántico crea una revisión;
  reenviar la última sin cambios responde `unchanged:true` y no reemplaza su YAML original.
- Entrega: SHA-256 de los bytes HTTP originales. Mismo delivery ID con distinto formato JSON
  también puede producir 409, aunque su significado sea equivalente. Preservar bytes en reintentos.
- Migración: SHA-256 de los bytes del archivo SQL, más una huella estructural separada de la DB.
  Cualquier edición de una migración aplicada, incluso comentarios, bloquea la verificación.
  No provoca reaplicación ni rollback automático; una modificación de esquema exige un archivo nuevo.

No confundir esos hashes con firmas HMAC de webhooks o hashes de credenciales. Un checksum
detecta diferencias, no concede autorización ni demuestra autenticidad de un archivo no confiable.
La [guía de migraciones](../README.md#migraciones-de-postgresql) detalla locks, transacciones,
rechazo de drift y restauración. Migraciones actuales: 001 baseline, 002 descubrimiento UI,
003 credenciales API, 004 actores de servicio, 005 índices de contexto del editor.

## Estados de ejecución

| Estado | Significado / acción del operador |
| --- | --- |
| `pending` | Paso en cola, o reintento seguro con `next_at`; revisar heartbeat, cuota y `last_error` si no avanza. |
| `dispatching` | Worker posee lease y puede estar enviando/leyendo; no lanzar otra copia del efecto. |
| `waiting` | Pipeline ya enviado; se espera y consulta su resultado, no se vuelve a disparar. |
| `succeeded` | Secuencia terminada, incluida una guarda get falsa que detiene sin error; revisar log si se esperaban más pasos. |
| `failed` | Fallo definitivo, bloqueo de seguridad o regla desactivada; revisar paso y error. No deshace pasos anteriores. |
| `needs_review` | Resultado ambiguo o presupuesto de seguimiento agotado; investigar antes de cualquier efecto nuevo. |

Lease de 120 s con token de propietario; una escritura cuyo lease expiró no se redespacha a
ciegas. Un resultado tardío no sobrescribe al nuevo propietario: se registra como `late_completion`.
Lecturas Jira transitorias y escrituras con 429 admiten reintento acotado (hasta 10 intentos del
paso); cada intento de escritura consume cuota aunque no termine con éxito. HTTP del proveedor
usa timeout 20 s y no sigue redirects. Bitbucket pasa de POST 201 a seguimiento GET, con
presupuesto de polling; sólo `COMPLETED/SUCCESSFUL` habilita avanzar. Un error o crash no
dispara compensaciones ni promete exactly-once en los proveedores.

`processed_at` del evento significa distribución/evaluación durable, no ejecución exitosa.
`ignored_service_actor` y `held_actor_unknown` son motivos del **evento**, no estados nuevos
del run. Pueden impedir que exista un run. `/v1/runs` es resumido: consultar
`/v1/runs/{id}` para `external_result` y `log` completos.

## Diagnóstico y recuperación

| Síntoma | Revisar antes de actuar |
| --- | --- |
| API no arranca | DB accesible, env del proceso, migraciones completas, tenant y huella correctos. Runtime no crea tablas. |
| `/healthz` OK pero nada funciona | `/readyz`, identidad autenticada, heartbeat y logs; liveness no prueba proveedores. |
| HTTP 401 | En API: token vencido/revocado/pendiente expirado. En webhook: secreto/firma sobre bytes exactos. No intercambiar secretos. |
| HTTP 403 | Token válido sin permiso de la operación; el admin o un delegado adecuado debe autorizarla. |
| HTTP 404 | Recurso/ruta inexistente o ruta bloqueada por proxy; gestión y webhooks comparten la API. |
| HTTP 409 | Checksum de activación, delivery ID reutilizado, evento excluido/redactado o conflicto de rotación. No quitar el control. |
| HTTP 413 | Body excede límite. El YAML tiene además su propio límite de 64 KiB. |
| HTTP 422 `unsafe_*` | Falta alcance positivo, especificidad del cambio o destino unitario; corregir filtro/YAML, no desactivar validación. |
| HTTP 503 | Persistencia no disponible o límite de concurrencia del servidor; el remitente debe reintentar la misma entrega. |
| Evento sin ejecución | Regla activa y trigger exacto, trace del filtro, actor excluido/ambiguo, payload redactado y revisión elegida. |
| `jira_service_identity_unverified` | `/myself`, scopes (incluido `read:jira-user`), vigencia del token, Cloud ID y red; un GET issue 200 no alcanza. |
| `jira_preflight_http_401/403/404` | Autenticación Bearer, permisos y existencia/visibilidad del ticket; no es un fallo de permisos de nuestra API. |
| `jira_issue_key_invalid` | Binding debe resolver a una clave Jira válida; no ID, objeto, null ni path de salida inventado. |
| `needs_review` | Revisar proveedor, operación, run y pasos anteriores. No asumir que la escritura falló porque no llegó respuesta. |
| Sugerencias vacías o sólo null | Eventos observados del source/tipo elegido; no es una consulta live a Jira ni un schema garantizado. |
| Studio 502/503 | Conectividad backend, token delegado y readiness; cambiar env/archivo y reiniciar gateway, no recompilar frontend. |
| Accesos pide desbloquear otra vez | Sesión administrativa de 15 min, reinicio o réplica distinta; usar afinidad si corresponde. |

**Reconciliación:** sólo Bitbucket dispone de `POST /v1/runs/{id}/reconcile`, con
`{"external_uuid":"<uuid>"}`. Requiere run `needs_review` y verificar que el pipeline remoto
coincide; adjunta identidad y reanuda observación, nunca repite el POST del pipeline.
Para Jira no existe endpoint de reconciliación: investigar la operación/provenance y resolver
el incidente explícitamente, sin editar estados DB o reejecutar a ciegas como procedimiento estándar.

**Backup:** proteger DB y fuentes de secretos; los payloads y resultados contienen datos del negocio.
El operador define pg_dump/PITR, cifrado, acceso, retención, RPO/RTO y ensayos de restore.
Restaurar en una DB nueva con el tenant e imagen compatibles, validar migraciones/permisos y
reconciliar efectos remotos posteriores al backup antes de reabrir el ingreso y el worker.
Restaurar PostgreSQL no restaura Jira/Bitbucket ni revierte sus cambios.

**Retención:** dry run `python -m app.retention --days 90 --limit 100`; agregar `--apply` sólo
tras revisar. Un lote redacta eventos elegibles y sus trazas/resultados, no eventos con runs
pendientes/en curso/`needs_review`. Conserva deduplicación, revisiones y auditoría. No elimina
todas las filas ni es una política automática de backups; el scheduler es externo.

**Acceso administrador perdido:** no hay master token oculto ni rebootstrap por cambiar la env.
Mantener al menos una credencial administrativa recuperable en el gestor de secretos, rotarla
con prueba del reemplazo y respaldar la DB. La API impide revocar el último administrador
confirmado activo, pero no puede impedir que el operador pierda su secreto o que venza.

## Qué verifican las pruebas

`python3 tools/selftest.py` ejecuta pytest con PostgreSQL descartable; `--full` suma build y
aceptación de contenedores, migraciones, permisos, API única y backup/restore.
No compila ni prueba por sí solo el frontend. Para éste:

```sh
npm --prefix frontend ci
npm --prefix frontend test
npm --prefix frontend run build
```

La aceptación conjunta usa imágenes construidas por el operador:

```sh
docker build -t jira-relay:verify .
docker build -f frontend/Dockerfile -t jira-relay-studio:verify .
uv run --frozen python tools/smoke_studio.py \
  --api-image jira-relay:verify --studio-image jira-relay-studio:verify
```

Esto prueba GUI/gateway/API/DB y administración de credenciales sin llamar a Jira/Bitbucket.
La prueba opt-in `tools/e2e_live.py` sí escribe recursos remotos autorizados, conserva evidencia
privada y no sustituye un webhook nativo. La [CI](../.github/workflows/ci.yml) ejecuta los checks
reproducibles; no despliega ni verifica credenciales de una instalación particular.
