# jira-relay-studio

Consola opcional del motor `automation/v1`. Usa la misma API, reglas y worker que cualquier otro cliente. No crea tenants ni bases, no descubre infraestructura y no obtiene secretos desde proveedores cloud. Docker, Kubernetes, ECS, una VM o un proceso local son decisiones del operador.

El backend tiene una única API para gestión y webhooks. Studio apunta a esa API;
el operador puede restringir sus rutas de gestión mediante proxy/WAF sin desplegar otro receptor.
Para ejecutar Studio como servicio independiente, partir de
[deploy/env/studio.env.example](../deploy/env/studio.env.example). El archivo
[.env.studio.example](../.env.studio.example) es para el empaquetado Compose, no para inyectarlo entero en el proceso.

## Contrato de ejecución

El navegador consume `/api` en su propio origen. El gateway reenvía esas solicitudes al backend configurado, agrega su token del lado servidor y sirve los assets compilados. No se envían el token configurado del backend ni credenciales Jira/Bitbucket al navegador. La sección Accesos permite ingresar un administrador y muestra las nuevas credenciales una sola vez a ese administrador. Cambiar la configuración requiere reiniciar el gateway, **no recompilar la imagen**. No existe una configuración incorporada del entorno actual.

| Variable | Uso |
| --- | --- |
| `STUDIO_BACKEND_URL` | URL HTTP(S) de la API de gestión accesible desde el gateway. Obligatoria; admite un prefijo de ruta. Sin credenciales, query ni fragmento. |
| `STUDIO_BACKEND_TOKEN` / `STUDIO_BACKEND_TOKEN_FILE` | Token delegado emitido por el administrador, con `api:read,rules:write,flows:operate` o un subconjunto. No usar el bootstrap. El valor directo tiene prioridad sobre el archivo. Se mantiene sólo en el servidor. |
| `STUDIO_PUBLIC_ORIGIN` | Origen exacto visible en el navegador, con puerto si corresponde. Obligatorio. Sin ruta. |
| `STUDIO_HOST` | Dirección de escucha. Proceso: `127.0.0.1`; imagen: `0.0.0.0`. |
| `STUDIO_PORT` | Puerto de escucha del proceso; por defecto `8080`. |
| `STUDIO_AUTH_MODE` | `basic` por defecto. `local` es una excepción explícita que exige bind y origen loopback. No funciona dentro del contenedor escuchando en todas las interfaces. |
| `STUDIO_LOGIN_USERNAME` | Usuario de la consola, obligatorio en modo `basic`. |
| `STUDIO_LOGIN_PASSWORD` / `STUDIO_LOGIN_PASSWORD_FILE` | Contraseña de la consola, mínimo 16 caracteres; el valor directo tiene prioridad sobre el archivo. Un valor vacío se rechaza sin fallback. No es el token del backend. |
| `STUDIO_STATIC_DIR` | Directorio de assets; normalmente no hace falta cambiarlo. |

Los secretos pueden ser archivos montados por cualquier plataforma. La aplicación no llama servicios de secretos ni comandos de despliegue. No acepta secretos en URLs. Una configuración incompleta o insegura impide arrancar, sin imprimir sus valores.

Configuración muestra la URL efectiva del backend y la variable que la controla. El encabezado no contiene nombres de cluster, plataforma ni etiquetas ambientales inventadas. El idioma Español/English se guarda por navegador; no se traducen payloads, DSL ni datos del proveedor.

El selector de datos muestra y busca rutas raw del payload, como `issue.fields.duedate` o `comment.body`, sin aliases ni traducciones inferidas. Los tipos son los identificadores JSON observados (`null`, `string`, `number`, etc.). Los ejemplos escalares usan representación JSON para distinguir `"0"` de `0` y conservar `false`. Las descripciones de la interfaz sí se traducen; la protección de datos sensibles y los resúmenes acotados de objetos/listas se mantienen. No se consulta Jira para reemplazar los nombres de las rutas.

La API Jira [`GET /rest/api/3/field`](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-fields/#api-rest-api-3-field-get) permite obtener metadatos de campos de issues, no el schema completo de cualquier webhook por nombre de evento. Para comentarios, Atlassian documenta que el objeto del webhook tiene la forma de la respuesta de [Get comment](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-comments/#api-rest-api-3-issue-issueidorkey-comment-id-get); esa consulta requiere identificar el issue y el comentario. [Los campos enviados dependen del tipo de webhook](https://developer.atlassian.com/cloud/jira/platform/webhooks/#webhook-payload). Metadatos de issues, schema documentado y campos realmente observados no son la misma fuente y no deben mezclarse como si fueran una garantía de presencia.

## Backend existente, sin contenedores

Construir el frontend e iniciar el gateway con las dependencias fijadas del proyecto:

```sh
npm --prefix frontend ci
npm --prefix frontend run build
export STUDIO_BACKEND_URL=http://127.0.0.1:8088
export STUDIO_PUBLIC_ORIGIN=http://127.0.0.1:8093
export STUDIO_PORT=8093
export STUDIO_AUTH_MODE=local
export STUDIO_BACKEND_TOKEN_FILE=/ruta/privada/backend_token
uv run --frozen python -m tools.studio_server
```

El backend debe existir y ser accesible en esa URL. Si la red requiere VPN, túnel o proxy, se configura **fuera** de la aplicación. El gateway no abre ni controla túneles. El modo local equivale a los permisos del token y solo sirve para una máquina de confianza; otros procesos de esa máquina pueden usarlo.

## Contenedor independiente

El contexto de build es la raíz del repositorio:

```sh
docker build -f frontend/Dockerfile -t jira-relay-studio:1.0.0 .
docker compose --env-file .env.studio -f compose.studio.yaml up -d
```

Preparar `.env.studio` desde `.env.studio.example`: indicar URL accesible desde el contenedor, origen del navegador y archivos existentes para el token y la contraseña. El archivo no se carga automáticamente al ejecutar Python. Las rutas de archivos de Compose se montan como secretos; deben ser legibles por UID `10001`, no estar en Git y tener permisos mínimos.

`compose.studio.yaml` contiene solo la consola. No incluye una base, credenciales por defecto ni proveedores falsos. `STUDIO_BIND_ADDRESS` y `STUDIO_PUBLISHED_PORT` controlan la publicación del puerto en el host; no son configuración del motor.

Para instalar la consola junto al backend portable de `compose.yaml`, preparar además `studio_login_password` en `AUTOMATION_SECRETS_DIR` y ejecutar:

```sh
docker compose --profile studio up -d --build
```

Ese ejemplo conecta servicios por el DNS de Compose `http://api:8080`. Se puede sobrescribir con `STUDIO_BACKEND_URL`; no es un destino compilado ni una dependencia de la aplicación.

## Accesos: administración desde la GUI

El login de la consola y el permiso de administrar credenciales son independientes. Entrar a **Accesos** e ingresar un token confirmado con `auth:admin`; no hay una variable de administrador adicional en el gateway. El token delegado usado para editar flujos no habilita esta sección.

Desde allí se pueden listar credenciales, emitir una con nombre, permisos y vencimiento, revocar accesos y rotar el administrador o el token delegado de la GUI. Otorgar `auth:admin` requiere una elección explícita. `flows:operate` permite efectos reales sobre proveedores. La API impide revocar el último administrador confirmado vigente.

Guardar cada token nuevo en el gestor de secretos antes de ocultarlo: no se puede recuperarlo desde el listado. El navegador lo mantiene temporalmente en memoria, no en almacenamiento local ni en URLs. No compartir capturas con el secreto visible.

### Rotación administrativa

1. Iniciar la rotación: se muestra el reemplazo una vez y su fecha límite.
2. Guardarlo y actualizar los consumidores que usaban el anterior. La API no cambia archivos ni variables.
3. Confirmar desde la GUI antes del plazo: se revoca el anterior y la sesión usa el nuevo.

La ventana predeterminada de rotación es **una hora**. La sesión administrativa de la GUI dura **15 minutos absolutos**, no una hora. Si vence o se reinicia el gateway, ingresar el nuevo token y marcar la opción para confirmar una rotación pendiente. Esa acción revoca el anterior; no es una recuperación sin credencial. Si se perdió el reemplazo, un administrador vigente puede revocarlo e iniciar otra rotación.

### Rotación del token de la GUI

Iniciar la rotación del delegado desde Accesos, guardar el nuevo en la fuente de `STUDIO_BACKEND_TOKEN` o `STUDIO_BACKEND_TOKEN_FILE` y reiniciar el gateway según la plataforma. Volver a desbloquear Accesos y confirmar **después** de verificar que el gateway utiliza el nuevo. La GUI rechaza confirmar con la configuración anterior. No escribe secretos de Docker, Kubernetes, ECS ni del host.

Si el delegado ya venció o fue revocado, un administrador puede entrar a Accesos, emitir otro y actualizar la configuración. Las demás pantallas no funcionarán hasta hacerlo. No montar un administrador como token de uso normal para resolverlo.

### Sesión y despliegue

El gateway conserva la credencial administrativa sólo en memoria del proceso; el navegador recibe una cookie opaca `HttpOnly`, `SameSite=Strict`, restringida a `/access`, con `Secure` en HTTPS. Revalida permisos contra la API en cada operación. Logout, vencimiento o reinicio eliminan la sesión. Hay un máximo de 32 sesiones por proceso, solicitudes administrativas de 32 KiB y respuestas de 1 MiB; los secretos no se registran en logs de acceso.

Estas sesiones no tienen almacenamiento distribuido: usar un gateway o afinidad de sesión si hay réplicas; un reinicio sigue requiriendo desbloquear de nuevo. No equivalen a SSO ni a usuarios individuales. La ruta general `/api` bloquea la administración de credenciales, salvo la consulta de identidad; administrar requiere `/access` y su sesión independiente. Mantener controles de acceso y rate limiting en el ingreso.

## Kubernetes, ECS y otros orquestadores

Usar la misma imagen con las variables anteriores y archivos de secretos montados. Configurar fuera del producto:

- Endpoint/DNS del backend accesible desde el gateway.
- TLS y enrutamiento al puerto configurado. El proxy debe preservar el `Host` público exacto. No se confía en `X-Forwarded-Host`, ni se elige un destino a partir de headers del cliente.
- Secretos, acceso de red restringido al backend y políticas de egreso.
- Probes: `GET /healthz` verifica el proceso; `GET /readyz` verifica la conectividad/readiness del backend. Ambos responden sin login y no exponen credenciales.
- Arranque como UID/GID `10001`, filesystem de solo lectura, `/tmp` efímero y límites de recursos.
- Reinicios al rotar secretos/cambiar parámetros; no hay detección automática del cloud.

No hace falta instalar AWS SDK, Docker CLI o Kubernetes CLI en la imagen.

## Seguridad y alcance de producción

El modo predeterminado solicita autenticación HTTP Basic para la consola completa, configuración y API. Para un origen no loopback exige HTTPS declarado; el operador debe realmente terminar TLS y bloquear acceso directo al puerto interno. No alcanza con escribir una URL HTTPS si se expone además el puerto HTTP al público. Usar contraseña aleatoria y controles de acceso/rate limiting en la entrada. No se publicará automáticamente ningún servicio.

Este es un acceso de operador único, no un sistema multiusuario con SSO o sesiones individuales. El backend permite revocar/rotar el token delegado de la GUI y audita su UUID, no la identidad individual del navegador. Si SSO o auditoría por usuario son obligatorios para una instalación, **no queda habilitada para producción compartida hasta implementarlos**. No presentar el editor como un SaaS multiusuario terminado.

El gateway valida Host/Origin, bloquea peticiones cross-site, limita el cuerpo de la API general a 10 MiB y la respuesta a 32 MiB, usa tiempos de espera y pool acotado, no sigue redirects del backend, no reenvía cookies ni credenciales del navegador, no confía en proxies ambientales y no registra acceso con payloads/tokens. La API debe configurarse con `MAX_BODY_BYTES=10485760` para aceptar el mismo máximo; puede reducirse para instalaciones más restrictivas. Los límites administrativos de `/access` siguen siendo menores. Estos máximos no sustituyen una prueba de carga ni los límites de memoria/concurrencia del despliegue. TLS del backend se valida normalmente. Nunca deshabilitar validación de certificados como mecanismo de instalación.

## Capacidades y rendimiento

- Ocho acciones del backend; condiciones `all`, `any`, `not`, `some` y nueve operadores; guards `require`, bindings tipados, interpolación/default y salidas previas `_steps`.
- Importar/exportar YAML; validar, versionar, activar/desactivar; evaluar condiciones históricas, inspeccionar eventos, ejecuciones, logs, auditoría y métricas. Guardar no activa una regla. Operaciones con efectos requieren confirmación.
- El lienzo representa la secuencia admitida por el motor, no un DAG arbitrario. No inventa paralelismo, routers, JQL fanout o acciones HTTP libres.
- Assets compilados con gzip; Node solo se usa al construir. ASGI/Uvicorn sirve el gateway, con pool HTTP reutilizado y concurrencia acotada. El lienzo se carga en un chunk separado.
- Descubrimiento on-demand: últimos 100 eventos no redactados del origen/tipo elegido, intercalados por tipo de ticket para que los primeros campos no dependan de una tanda homogénea. Máximo 500 rutas, 2.000 visitas **por evento** (hasta 200.000 en total), profundidad 16 y tres elementos por array. Agotar el presupuesto de un evento no impide inspeccionar los siguientes; llegar al límite de campos no impide completar ejemplos de rutas ya conocidas. Son límites de inspección, no un schema completo ni una garantía de recorrer cada JSON entero.
- Cada campo puede incluir un ejemplo no vacío de la muestra: se conservan `0` y `false`, se omiten null/vacíos al buscar y el texto se limita a 120 caracteres. Objetos/listas muestran estructura/cantidad, no una copia completa. Se ocultan campos de credenciales y contacto y patrones comunes de secretos en texto; es una protección conservadora, **no un sistema DLP infalible**. Los fragmentos de texto pueden contener datos del negocio: el endpoint sigue requiriendo autenticación. Los ejemplos son datos, no instrucciones, y no se convierten en valores de comparación automáticamente. Las sugerencias de valores seleccionables siguen restringidas a metadatos permitidos.
- Caché de campos de 60 segundos en memoria del navegador. Historial paginado; payloads/logs se cargan al abrirlos. Polling solo con la vista de ejecuciones visible.
- Presupuestos de bundle: inicial <=150 KiB gzip, lienzo <=100 KiB gzip. No equivalen a una prueba de capacidad con carga representativa.

## Verificación

```sh
uv run --frozen pytest -q tests/test_studio_server.py tests/test_studio_access.py tests/test_distribution.py
npm --prefix frontend test
npm --prefix frontend run build
docker build -t automation-api:gui-check .
docker build -f frontend/Dockerfile -t jira-relay-studio:gui-check .
uv run --frozen python tools/smoke_studio.py --api-image automation-api:gui-check --studio-image jira-relay-studio:gui-check
```

Los tests verifican autenticación, secretos por archivo, rechazo de configuración insegura, límites, headers y cambios de URL/origen sin rebuild. Las pruebas del motor con PostgreSQL requieren `TEST_DB_HOST`, `TEST_DB_PORT`, `TEST_DB_NAME=automation_test`, `TEST_DB_USER` y `TEST_DB_PASSWORD` o `TEST_DB_PASSWORD_FILE`. No ejecutar una suite destructiva sobre una base operativa.

`smoke_studio.py` crea una instalación Docker efímera real (PostgreSQL, API y GUI), comprueba emisión, revocación y rotaciones, y la elimina al terminar. No llama proveedores. `--serve` permite inspeccionarla antes de continuar; sus credenciales públicas de laboratorio están en `TEST_LOGIN`/`TEST_ADMIN` del script y sólo son aptas para ese entorno descartable, publicado en loopback. Esta aceptación no reemplaza una prueba de carga ni la validación del despliegue elegido.

## Editar un ticket

**Ticket a editar** es un único campo: escribí una clave Jira (`KAN-45`) o abrí
`{ }` para elegir el dato que contiene esa clave. No se elige una categoría
"resultado de otro paso": todos los datos se seleccionan desde el mismo contexto.
Por ejemplo, `issue.fields.parent.key` apunta al padre del ticket que disparó el
evento y `_steps["0"].fields.parent.key` al padre obtenido por una consulta anterior.
La selección utiliza el valor de la ruta al ejecutar, no el texto de la ruta.
En este selector se priorizan valores con formato de clave Jira y salidas de claves
de pasos anteriores; no se ofrecen resultados estructurados como si fueran tickets.
Para volver a escribir una clave fija, quitá el dato seleccionado con `×`.

En **Campos a modificar**, elegí `description`, `summary`, etc. desde **Agregar
campo**: su editor de valor aparece inmediatamente. El nuevo valor reemplaza el
actual. Podés escribir texto o usar `{ }` para tomar un dato; los valores JSON y
controles técnicos siguen disponibles en **Opciones avanzadas**. El destino debe
resolver a una única clave Jira; no acepta listas, JQL, IDs numéricos, referencias
futuras ni un fallback que pueda editar otro ticket si falta el dato esperado.

Para **sumar** texto, usá **Agregar al final de la descripción** en la acción Editar
ticket. Por ejemplo `Se asigno!` crea un nuevo párrafo y conserva el contenido/formato
actual de Jira; no requiere cargar un `fields.description` ni una acción de consulta
previa. Funciona con descripciones vacías. No combines agregado y reemplazo del mismo
campo. Como es lectura seguida de escritura, no garantiza un append atómico frente
a ediciones concurrentes de otras personas o integraciones.

Para dispararlo al asignar a alguien, el filtro de `changelog.items` debe exigir en
un mismo elemento `fieldId = assignee`, `to` existente, distinto de null y distinto de
texto vacío. No exige una lista fija de personas. La desasignación no coincide; las
reasignaciones sí. Los eventos de la cuenta del servicio siguen excluidos globalmente.

## Contexto y ejemplos de pasos anteriores

Los selectores separan **Acciones anteriores** y **Datos del evento** y muestran
tipos y ejemplos protegidos. Buscan por ruta raw y por el nombre del paso.
Cuando existen ejecuciones, **Contexto disponible** identifica una ejecución de
la misma versión con resultados anteriores al paso seleccionado: por ejemplo,
`_steps["0"].fields.summary` con su valor observado. Se usa una sola ejecución
coherente; nunca se mezclan salidas de distintas ejecuciones o versiones. Son
ejemplos históricos, no una ejecución en vivo ni una simulación del flujo editado.

Al modificar el trigger, los filtros o un paso previo se descarta ese contexto.
Sin resultados, se muestran las salidas declaradas como todavía no observadas;
no se inventan valores. **Actualizar contexto** vuelve a consultar sin ejecutar
acciones. La consulta es bajo demanda, revisa como máximo diez ejecuciones y solo
resultados durables `pending`/`succeeded` de pasos anteriores. Respeta redacción,
protección de datos sensibles y límites de inspección del catálogo. No consulta
Jira/Bitbucket ni persiste nuevas copias de payloads. La migración 005 agrega los
índices de búsqueda; no cambia datos ni migraciones anteriores.

## Ayuda de acciones y resultados

Al seleccionar una acción, abrí **? Cómo usar esta acción**. La ayuda enumera sus
resultados y muestra rutas reales de los pasos anteriores del flujo actual.
El paso visible 1 corresponde a `_steps["0"]`: para usar el ticket consultado
allí, abrí `{ }` y elegí `_steps["0"].key`; para su resumen,
`_steps["0"].fields.summary`. Para mezclar texto y datos, escribí por ejemplo
`Ticket: ${_steps["0"].key}`. `issue.fields` sigue siendo el evento original.
Los resultados solo están disponibles después de ejecutar el paso correspondiente.
Una descripción ADF es un objeto: interpolarla como texto no implementa un append.
Usá la operación explícita **Agregar al final de la descripción** para conservarla.
