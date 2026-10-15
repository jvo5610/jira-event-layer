# Automation Studio local

Editor visual para el motor real `automation/v1`: no hay otro ejecutor ni un backend simulado. El lienzo representa la secuencia que admite el motor, no un DAG arbitrario. No se agregan routers, bucles, paralelismo ni HTTP libre.

## Conectar el editor a Fett

La interfaz puede ejecutarse en esta máquina usando directamente la API, el worker y la base de Fett. No se copian eventos a otra base ni se usa un backend de ejemplo.

```sh
cd frontend
npm ci
npm run build
cd ..
uv run --with boto3 python tools/studio_server.py --port 8093
```

Abrir <http://127.0.0.1:8093>. El servidor abre un túnel privado a `infra/jira-event-layer-api` con el contexto Kubernetes `fett`. Lee la credencial administradora desde SSM `/k8s/jira-event-layer`, en memoria, y la agrega únicamente del lado servidor. Requiere acceso local a ese contexto y al parámetro AWS; no se ingresan tokens en el navegador. Al detener el servidor se cierra su túnel. La API de gestión no se publica en Internet.

**Los cambios desde esta conexión afectan Fett.** El indicador de la cabecera identifica la conexión. Guardar una regla no la activa; activar, ejecutar o desactivar requiere una confirmación explícita.

En Configuración se puede elegir Español o English. La preferencia se guarda en este navegador. Se traducen controles, mensajes, fechas y estados; no se traducen payloads, nombres de campos del DSL, nombres de tickets ni datos de Jira.

## Iniciar una instalación Docker independiente

Desde la raíz del repositorio, copiar `.env.studio.example` a `.env.studio` y completar destinos permitidos. Por ejemplo, para los recursos de laboratorio existentes:

```dotenv
STUDIO_PORT=8091
STUDIO_JIRA_PROJECTS=KAN
STUDIO_REPOSITORIES=jvidelaolmos/jira-event-layer-lab
```

```sh
docker compose --env-file .env.studio -f compose.studio.yaml up -d --build
```

Abrir <http://127.0.0.1:8091>. Se crean un proyecto Compose `jira-automation-studio`, volumen PostgreSQL, migración, API, worker y frontend propios. No se lee el `.env` de la aplicación ni se heredan secretos de Fett. No se expone PostgreSQL ni la API directamente.

Para detener sin perder datos:

```sh
docker compose --env-file .env.studio -f compose.studio.yaml stop
```

Para volver a abrir, usar el mismo comando `up`. No ejecutar `down -v` si se quieren conservar los escenarios y eventos.

## Cobertura

- Disparadores Jira y test; nombre de evento libre, con sugerencias de eventos observados.
- Ocho acciones: consultar, crear, clonar, editar, transicionar, comentar, vincular y ejecutar pipeline. Biblioteca derivada de `/v1/schema` (a través de `/v1/catalog`).
- Condiciones anidadas `all`, `any`, `not`, `some`; nueve operadores `eq`, `ne`, `in`, `contains`, `exists`, `gt`, `gte`, `lt`, `lte`. Los valores de comparación aceptan cualquier tipo JSON válido para el operador.
- Guardas `require` sobre la respuesta actual de Jira; dentro de `some.where` las rutas son relativas al elemento de la lista.
- Bindings de valor, ruta, plantilla, escape y default donde lo admite el contrato. Campos JSON tipados, campos personalizados, parent, copy_fields, estado de origen opcional, variables de pipeline.
- Referencias manuales arbitrarias compatibles con el DSL, incluidas claves literales y arrays. Sugerencias de resultados previos tanto para Jira como para Bitbucket. El contexto `_steps` se construye exclusivamente con resultados persistidos de esta ejecución y de pasos anteriores, nunca desde datos que aporte un webhook.
- Importar/exportar YAML y edición JSON de la acción completa, sin descartar propiedades ni convertir silenciosamente tipos. Se conserva la semántica, no los comentarios o formato originales.
- Validar, guardar revisiones, abrir versiones anteriores, activar y desactivar. Guardar no activa automáticamente. Activar usa la versión y checksum devueltos por el servidor.
- Evaluar condiciones sobre eventos históricos y comparar coincidencias entre versiones. No se simulan acciones, resultados externos, permisos ni mapeos.
- Ingresar un payload con `Idempotency-Key`, inspeccionar eventos, solicitar ejecución, inspeccionar runs/logs y reconciliar un UUID de pipeline existente cuando corresponde. Confirmaciones explícitas antes de operaciones con efectos.
- Auditoría, métricas, readiness y consulta del JSON Schema real.

## Credenciales y ejecución real

Sin tokens configurados, se pueden crear reglas para destinos permitidos, validarlas, guardarlas y evaluar condiciones. Si se activa una regla y se ingresa un evento coincidente, el worker real intentará ejecutarla y registrará el error de conector si faltan credenciales. No hay respuesta simulada de éxito.

Para operaciones reales, completar las variables `STUDIO_JIRA_*` y `STUDIO_BITBUCKET_*` de `.env.studio`, y recrear servicios con el comando `up`. Usar tokens propios y destinos autorizados. Los secretos permanecen en servicios del lado servidor, nunca en el bundle del navegador. `.env.studio` está ignorado por Git. La pantalla Instalación indica configuración presente, no certifica permisos remotos.

Este Compose es una instalación de gestión local; los webhooks públicos permanecen fuera de alcance. La ingesta manual usa el endpoint real de gestión. Para recibir webhooks nativos se requiere el servicio separado `webhooks` y una entrada HTTPS con firma, como en la instalación portable existente, no publicar la API administradora.

**No publicar este gateway:** equivale a un administrador local sin login. Escucha únicamente en loopback; comprueba Host y Origin para bloquear rebinding y peticiones web de otros orígenes. No protege contra otros procesos o usuarios de la misma máquina. Una instalación compartida necesita autenticación por usuario, sesión segura, permisos y atribución individual. No se modificó el modelo de roles existente del backend.

## Límites y rendimiento

- React compilado y servido por Nginx; no hay servidor Node en ejecución.
- Lienzo React Flow en chunk separado, cargado al entrar al editor; componentes memorizados.
- Solo 10 acciones por regla, como el backend. Las posiciones del lienzo se guardan localmente en el navegador y no alteran reglas ni checksums.
- Descubrimiento on-demand: se consideran como máximo 100 eventos recientes no redactados del disparador y se eligen hasta 20, repartidos entre los tipos de ticket observados. Así una tanda de subtareas no oculta los campos de las épicas. No se recorre el historial completo ni se persiste una copia de datos.
- Máximo 500 rutas, 10.000 visitas, profundidad 16, tres elementos por array y ejemplos solo de metadatos expresamente permitidos. Los tipos observados no se interpretan como schema completo.
- Caché del catálogo de campos en memoria del navegador durante 60 segundos; nunca localStorage para payloads. Los ejemplos no incluyen summary, description, emails ni comentarios.
- Historial de eventos/runs paginado por cursor; payloads y logs solo al inspeccionarlos. Búsqueda de texto limitada a la página cargada, identificada en la interfaz.
- Polling de runs cada 10 segundos únicamente con la pantalla visible y en la primera página. No WebSockets ni polling global.
- Migración nueva `002_ui_discovery.sql` con índices B-tree de filtros y orden. No se modifica la migración 001 ni se agrega un GIN global sobre JSON.
- Presupuestos orientativos de bundle inicial <=150 KiB gzip y chunk de lienzo <=100 KiB gzip; verificar salida de `npm run build` al cambiar dependencias. Los tiempos de API deben medirse con volumen representativo; el dataset local pequeño no certifica escalabilidad.

## Verificación de desarrollo

```sh
cd frontend
npm ci
npm test
npm run build
```

Los tests de frontend verifican round-trip del catálogo YAML completo, descubrimiento de acciones desde schema, bindings, salidas previas y contrato de ingesta. Los tests Python `tests/test_catalog.py` cubren rutas, tipos, límites, exclusión de datos personales, autenticación, retención y paginación con PostgreSQL cuando `TEST_DATABASE_URL` apunta a una base dedicada `automation_test`.

La instalación Docker independiente no despliega en Fett. El modo conectado opera directamente sobre Fett; no es una simulación. El listener puede recibir todos los eventos del sitio Jira, sin JQL, conservando firma y deduplicación. Las reglas deciden qué disparadores y condiciones coinciden.
