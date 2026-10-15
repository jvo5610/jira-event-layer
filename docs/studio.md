# Automation Studio

Consola opcional del motor `automation/v1`. Usa la misma API, reglas y worker que cualquier otro cliente. No crea tenants ni bases, no descubre infraestructura y no obtiene secretos desde proveedores cloud. Docker, Kubernetes, ECS, una VM o un proceso local son decisiones del operador.

## Contrato de ejecución

El navegador consume `/api` en su propio origen. El gateway reenvía esas solicitudes al backend configurado, agrega su token del lado servidor y sirve los assets compilados. No se envían tokens de backend ni credenciales Jira/Bitbucket al navegador. Cambiar la configuración requiere reiniciar el gateway, **no recompilar la imagen**. No existe una configuración incorporada del entorno actual.

| Variable | Uso |
| --- | --- |
| `STUDIO_BACKEND_URL` | URL HTTP(S) de la API de gestión accesible desde el gateway. Obligatoria; admite un prefijo de ruta. Sin credenciales, query ni fragmento. |
| `STUDIO_BACKEND_TOKEN` / `STUDIO_BACKEND_TOKEN_FILE` | Token del backend, uno solo de los dos. Usar el rol mínimo necesario. |
| `STUDIO_PUBLIC_ORIGIN` | Origen exacto visible en el navegador, con puerto si corresponde. Obligatorio. Sin ruta. |
| `STUDIO_HOST` | Dirección de escucha. Proceso: `127.0.0.1`; imagen: `0.0.0.0`. |
| `STUDIO_PORT` | Puerto de escucha del proceso; por defecto `8080`. |
| `STUDIO_AUTH_MODE` | `basic` por defecto. `local` es una excepción explícita que exige bind y origen loopback. No funciona dentro del contenedor escuchando en todas las interfaces. |
| `STUDIO_LOGIN_USERNAME` | Usuario de la consola, obligatorio en modo `basic`. |
| `STUDIO_LOGIN_PASSWORD` / `STUDIO_LOGIN_PASSWORD_FILE` | Contraseña de la consola, mínimo 16 caracteres; elegir uno. No es el token del backend. |
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
docker build -f frontend/Dockerfile -t automation-studio:1.0.0 .
docker compose --env-file .env.studio -f compose.studio.yaml up -d
```

Preparar `.env.studio` desde `.env.studio.example`: indicar URL accesible desde el contenedor, origen del navegador y archivos existentes para el token y la contraseña. El archivo no se carga automáticamente al ejecutar Python. Las rutas de archivos de Compose se montan como secretos; deben ser legibles por UID `10001`, no estar en Git y tener permisos mínimos.

`compose.studio.yaml` contiene solo la consola. No incluye una base, credenciales por defecto ni proveedores falsos. `STUDIO_BIND_ADDRESS` y `STUDIO_PUBLISHED_PORT` controlan la publicación del puerto en el host; no son configuración del motor.

Para instalar la consola junto al backend portable de `compose.yaml`, preparar además `studio_login_password` en `AUTOMATION_SECRETS_DIR` y ejecutar:

```sh
docker compose --profile studio up -d --build
```

Ese ejemplo conecta servicios por el DNS de Compose `http://api:8080`. Se puede sobrescribir con `STUDIO_BACKEND_URL`; no es un destino compilado ni una dependencia de la aplicación.

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

Este es un acceso de operador único, no un sistema multiusuario con SSO, sesiones revocables o permisos individuales. La auditoría del backend identifica el rol de su token, no una identidad individual del navegador. Si esos requisitos son obligatorios para una instalación, **no queda habilitada para producción compartida hasta implementarlos**. No presentar el editor como un SaaS multiusuario terminado.

El gateway valida Host/Origin, bloquea peticiones cross-site, limita el cuerpo a 1 MiB y la respuesta a 8 MiB, usa tiempos de espera y pool acotado, no sigue redirects del backend, no reenvía cookies ni credenciales del navegador, no confía en proxies ambientales y no registra acceso con payloads/tokens. TLS del backend se valida normalmente. Nunca deshabilitar validación de certificados como mecanismo de instalación.

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
uv run --frozen pytest -q tests/test_studio_server.py tests/test_distribution.py
npm --prefix frontend test
npm --prefix frontend run build
```

Los tests verifican autenticación, secretos por archivo, rechazo de configuración insegura, límites, headers y cambios de URL/origen sin rebuild. Las pruebas del motor con PostgreSQL requieren `TEST_DATABASE_URL` hacia una base de pruebas dedicada. No ejecutar una suite destructiva sobre una base operativa.
