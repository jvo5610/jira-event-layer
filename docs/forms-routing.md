# Jira Forms y delegación por recurso

Para un `jira:issue_created` de Jira Service Management, la API persiste el evento
y el worker consulta los formularios asociados. Las respuestas normalizadas se
guardan por `questionKey` en `enrichment.forms[n].answers`. No se conserva el
diseño original ni un bloque `raw`. El worker espera `submitted: true`; si agota
`JIRA_FORMS_EMPTY_RETRIES`, registra `jira_forms_not_submitted` sin ejecutar reglas.

## Un único documento de evento

El JSON de `GET /v1/events/{id}` es la entrada de filtros, simulación y acciones.
El webhook queda en `payload`, los formularios en `enrichment` y los resultados
de acciones anteriores en `_steps`. Por ejemplo:

- `payload.issue.key`
- `payload.changelog.items`
- `enrichment.forms[0].answers.s3_why`

`POST /v1/evaluate` recibe `yaml` y `event` con ese documento completo, o una lista
`event_ids`. Las evaluaciones y el motivo de bloqueo se consultan mediante
`GET /v1/events/{id}/diagnostics`. Las reglas antiguas con rutas como `issue.key`
deben actualizarse; el motor no mantiene alias ocultos.

## Ejemplos de delegación

[S3](../examples/forms/infra-s3.yaml) y [SQS](../examples/forms/infra-sqs.yaml)
crean una tarea por recurso y la vinculan al padre. Sus descripciones toman del
formulario la descripción global, línea, finalidad, nombre y cuenta del recurso,
accesos o productores/consumidores. Incluyen la clave, resumen y enlace del padre
en «Otra información». No agregan una finalidad inventada.

Los proyectos, IDs y URL de estos ejemplos son ficticios. Antes de usarlos hay
que adaptar el request type, tipos de issue, template, `questionKey`, link type y
destino Jira. El alcance del ejemplo es un S3 y una SQS, con el formulario esperado
en la primera posición; el filtro verifica ese template. No son reglas instaladas
ni activadas automáticamente. No provisionan recursos ni ejecutan pipelines.

Las credenciales Jira requieren `ALLOWED_JIRA_PROJECTS` con los proyectos
permitidos, por ejemplo `DEMO,OPS`. La cuenta del servicio sigue necesitando los
permisos correspondientes en Jira.

La prueba de aceptación consiste en activar las reglas antes de cargar un padre
nuevo desde el portal y observar la creación y vinculación automáticas de los
hijos. No requiere llamar a `/execute` ni corregir los hijos manualmente.
La deduplicación cubre evento/regla/versión: activar otra versión y reprocesar
un evento puede generar nuevos efectos. Los resultados de escritura ambiguos
se mantienen en `needs_review` para evitar duplicados.
