import { useSyncExternalStore } from "react";

export type Language = "es" | "en";
let language: Language = "es";
try {
  language = localStorage.getItem("automation-language") === "en" ? "en" : "es";
} catch {
  /* Private storage may be unavailable. */
}
const listeners = new Set<() => void>();
export function setLanguage(next: Language) {
  language = next;
  try {
    localStorage.setItem("automation-language", next);
  } catch {}
  if (typeof document !== "undefined") document.documentElement.lang = next;
  for (const listener of listeners) listener();
}
export function useLanguage() {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    () => language,
    () => "es" as Language,
  );
}
export function locale() {
  return language === "es" ? "es-AR" : "en-GB";
}
export function t(text: string, values: Record<string, string | number> = {}) {
  if (!text) return text;
  let result = messages[text]?.[language === "es" ? 0 : 1] ?? text;
  for (const [key, value] of Object.entries(values))
    result = result.replaceAll("{" + key + "}", String(value));
  return result;
}

// The source key remains stable. Copy is intentionally short; data and DSL identifiers are not translated.
export const messages: Record<string, [string, string]> = {
  "Clave del ticket": ["Clave del ticket", "Issue key"],
  "ID del ticket": ["ID del ticket", "Issue ID"],
  Título: ["Título", "Summary"],
  Etiquetas: ["Etiquetas", "Labels"],
  "Nombre del proyecto": ["Nombre del proyecto", "Project name"],
  "Tipo de ticket": ["Tipo de ticket", "Issue type"],
  "ID del estado": ["ID del estado", "Status ID"],
  Prioridad: ["Prioridad", "Priority"],
  "ID de prioridad": ["ID de prioridad", "Priority ID"],
  "Tipo del ticket padre": ["Tipo del ticket padre", "Parent issue type"],
  "Fecha de vencimiento": ["Fecha de vencimiento", "Due date"],
  "Asignado a": ["Asignado a", "Assignee"],
  "Fecha de creación": ["Fecha de creación", "Created at"],
  "Última modificación": ["Última modificación", "Last updated"],
  "Campos modificados": ["Campos modificados", "Changed fields"],
  "Contenido del comentario": ["Contenido del comentario", "Comment body"],
  "ID del comentario": ["ID del comentario", "Comment ID"],
  "Campo modificado": ["Campo modificado", "Changed field"],
  "Valor anterior": ["Valor anterior", "Previous value"],
  "Valor nuevo": ["Valor nuevo", "New value"],
  Proyectos: ["Proyectos", "Projects"],
  "Campos habilitados": ["Campos habilitados", "Enabled fields"],
  "Etiqueta requerida": ["Etiqueta requerida", "Required label"],
  Repositorios: ["Repositorios", "Repositories"],
  "Automation Studio inicio": ["Inicio", "Home"],
  WORKSPACE: ["ESPACIO", "WORKSPACE"],
  Workspace: ["Espacio", "Workspace"],
  Automatizaciones: ["Automatizaciones", "Automation"],
  "Editor de reglas": ["Editor de reglas", "Rule editor"],
  Backend: ["Backend", "Backend"],
  "Sin verificar": ["Sin verificar", "Not verified"],
  Objeto: ["Objeto", "Object"],
  Lista: ["Lista", "List"],
  "Sin valor observado": ["Sin valor observado", "No value observed"],
  "Sin ejemplo disponible": ["Sin ejemplo disponible", "No example available"],
  "Sin datos en la muestra": ["Sin datos en la muestra", "No data in the sample"],
  "Valor protegido": ["Valor protegido", "Protected value"],
  "Objeto con {count} campos": ["Objeto con {count} campos", "Object with {count} fields"],
  "Lista con {count} elementos": ["Lista con {count} elementos", "List with {count} items"],
  "Ejemplo: {value}": ["Ejemplo: {value}", "Example: {value}"],
  " · puede venir vacío": [" · puede venir vacío", " · may be empty"],
  "Acceso a la consola": ["Acceso a la consola", "Console access"],
  Autenticado: ["Autenticado", "Authenticated"],
  "Acceso local": ["Acceso local", "Local access"],
  Escenarios: ["Reglas", "Rules"],
  Eventos: ["Eventos", "Events"],
  Ejecuciones: ["Ejecuciones", "Runs"],
  Auditoría: ["Auditoría", "Audit log"],
  Instalación: ["Configuración", "Settings"],
  Configuración: ["Configuración", "Settings"],
  "Solo en esta máquina": [
    "Acceso desde esta máquina",
    "Access from this computer",
  ],
  "Contrato y ayuda": ["Ayuda técnica", "Technical help"],
  "Cerrar aviso": ["Cerrar aviso", "Dismiss"],
  Volver: ["Volver", "Back"],
  "Cambios sin guardar": ["Cambios sin guardar", "Unsaved changes"],
  "Nuevo escenario": ["Nueva regla", "New rule"],
  Activo: ["Activo", "Active"],
  Inactivo: ["Inactivo", "Inactive"],
  activa: ["activa", "active"],
  activo: ["activo", "active"],
  inactivo: ["inactivo", "inactive"],
  "Activa v": ["Activa v", "Active v"],
  YAML: ["YAML", "YAML"],
  "Exportar YAML": ["Exportar YAML", "Export YAML"],
  Validar: ["Validar", "Validate"],
  Guardar: ["Guardar", "Save"],
  Activar: ["Activar", "Enable"],
  "Regla válida · ": ["Regla válida · ", "Valid rule · "],
  "Flujo secuencial": ["Secuencia de pasos", "Step sequence"],
  "/ 10 acciones": ["/ 10 acciones", "/ 10 actions"],
  "Cargando editor…": ["Cargando editor…", "Loading editor…"],
  Escenario: ["Regla", "Rule"],
  "Elegí un módulo para configurarlo · Arrastrá para organizar": [
    "Seleccioná un paso para editarlo",
    "Select a step to edit it",
  ],
  "Probar condiciones": ["Probar condiciones", "Test conditions"],
  "CONFIGURACIÓN DEL MÓDULO": ["PASO", "STEP"],
  "Agregar una acción": ["Agregar acción", "Add action"],
  "Agregar: {action}": ["Agregar: {action}", "Add: {action}"],
  Acción: ["Acción", "Action"],
  Disparador: ["Disparador", "Trigger"],
  Condiciones: ["Condiciones", "Conditions"],
  "Prueba e historial": ["Pruebas y versiones", "Tests and versions"],
  "Evaluación sin efectos externos": [
    "Prueba de condiciones",
    "Condition test",
  ],
  "Comprueba disparador y condiciones sobre eventos guardados. No ejecuta ni simula acciones, bindings o permisos del proveedor.":
    [
      "Usa eventos guardados. No ejecuta acciones ni verifica permisos de Jira o Bitbucket.",
      "Uses stored events. Does not run actions or check Jira or Bitbucket permissions.",
    ],
  "Elegí eventos reales recibidos": ["Eventos recibidos", "Received events"],
  "Inspeccionar evento": ["Ver evento", "View event"],
  "Todavía no hay eventos de este disparador. Ingresalos desde Eventos o conectá el origen real.":
    ["No hay eventos de este tipo todavía.", "No events of this type yet."],
  "Evaluar condiciones": ["Evaluar condiciones", "Evaluate conditions"],
  "Comparar con versión guardada": [
    "Comparar con otra versión",
    "Compare with another version",
  ],
  "Seleccionar versión": ["Seleccionar versión", "Select version"],
  Versión: ["Versión", "Version"],
  "Comparar coincidencias": ["Comparar resultados", "Compare results"],
  "Resultado · sin ejecución": [
    "Resultado · sin ejecutar acciones",
    "Result · no actions executed",
  ],
  Versiones: ["Versiones", "Versions"],
  "¿Descartar cambios y abrir esta versión?": [
    "¿Descartar los cambios y abrir esta versión?",
    "Discard changes and open this version?",
  ],
  "· activa": ["· activa", "· active"],
  "Nombre estable del escenario": ["Nombre de la regla", "Rule name"],
  "Cambiar el nombre crea otra regla; no renombra la existente.": [
    "Cambiar el nombre crea una regla nueva.",
    "Changing the name creates a new rule.",
  ],
  Descripción: ["Descripción", "Description"],
  Origen: ["Origen", "Source"],
  Evento: ["Evento", "Event"],
  "Test (origen soportado por el backend)": ["Test", "Test"],
  "Podés escribir cualquier evento soportado por tu origen, incluso antes de recibirlo.":
    [
      "Elegí un evento recibido o escribí su nombre.",
      "Choose a received event or enter its name.",
    ],
  "campos observados": ["campos encontrados", "fields found"],
  Sobre: ["En", "In"],
  "eventos recientes, máximo 100. Los campos faltantes no se inventan.": [
    "eventos recientes.",
    "recent events.",
  ],
  "Desactivar escenario": ["Desactivar regla", "Disable rule"],
  "El evento avanza solo cuando se cumple este filtro. Las rutas se evalúan sobre el payload original.":
    [
      "La regla se ejecuta cuando el evento cumple estas condiciones.",
      "The rule runs when the event meets these conditions.",
    ],
  "Todos los módulos ejecutables del contrato automation/v1. Sin routers o conexiones que el motor no soporte.":
    [
      "Elegí qué querés hacer en este paso.",
      "Choose what this step should do.",
    ],
  Paso: ["Paso", "Step"],
  "Conector configurado; permisos y destinos se verifican al ejecutar.": [
    "Conector configurado.",
    "Connector configured.",
  ],
  "Sin credenciales del proveedor. Podés editar; las acciones externas no podrán completarse.":
    [
      "El servicio de gestión no informa credenciales. El worker las verifica al ejecutar.",
      "The management API does not report credentials. The worker checks them when running.",
    ],
  "← Antes": ["← Antes", "← Earlier"],
  "Después →": ["Después →", "Later →"],
  "Quitar acción": ["Quitar acción", "Remove action"],
  "Seleccioná un módulo del lienzo.": ["Seleccioná un paso.", "Select a step."],
  "AUTOMATION WORKSPACE": ["", ""],
  "Tus escenarios": ["Reglas", "Rules"],
  "Explorador de eventos": ["Eventos", "Events"],
  "Registro de auditoría": ["Auditoría", "Audit log"],
  "Tu instalación": ["Configuración", "Settings"],
  "Conectá eventos, aplicá condiciones y automatizá el siguiente paso.": [
    "Administrá las reglas de automatización.",
    "Manage automation rules.",
  ],
  "Los datos que recibe el motor. La fuente de tus próximas automatizaciones.":
    [
      "Consultá los eventos recibidos de Jira.",
      "Browse events received from Jira.",
    ],
  "Seguí cada paso, inspeccioná resultados y entendé los errores.": [
    "Consultá el resultado de cada ejecución.",
    "Review the result of each run.",
  ],
  "Cambios de configuración y acciones de operación registradas.": [
    "Cambios realizados en las reglas y ejecuciones.",
    "Changes made to rules and runs.",
  ],
  "Idioma, conexión y conectores.": [
    "Idioma, conexión y conectores.",
    "Language, connection and connectors.",
  ],
  "Importar YAML": ["Importar YAML", "Import YAML"],
  "Crear escenario": ["Crear regla", "Create rule"],
  "Ingresar evento": ["Ingresar evento", "Submit event"],
  "Escenarios guardados": ["Reglas guardadas", "Saved rules"],
  "Escenarios activos": ["Reglas activas", "Active rules"],
  "Tipos de acción disponibles": ["Acciones disponibles", "Available actions"],
  "La URL del backend se configura al iniciar el servicio. Guardar una regla no la activa.":
    [
      "La URL del backend se configura al iniciar el servicio. Guardar una regla no la activa.",
      "The backend URL is configured at startup. Saving a rule does not enable it.",
    ],
  "Herramientas técnicas": ["Información técnica", "Technical information"],
  "JSON Schema real del backend": ["Esquema de reglas", "Rule schema"],
  "Contrato completo": ["Ver esquema", "View schema"],
  "Estado y métricas": ["Estado y métricas", "Status and metrics"],
  "Las credenciales del backend permanecen en el servidor. Los permisos dependen del token configurado.":
    [
      "Las credenciales del backend permanecen en el servidor. Los permisos dependen del token configurado.",
      "Backend credentials stay on the server. Permissions depend on the configured token.",
    ],
  "Buscar en la lista": ["Buscar en la lista", "Search list"],
  "Buscar escenarios…": ["Buscar reglas…", "Search rules…"],
  "Buscar en esta página…": [
    "Buscar en los registros cargados…",
    "Search loaded records…",
  ],
  "Filtrar tipo de evento": ["Tipo de evento", "Event type"],
  "Todos los tipos": ["Todos los tipos", "All types"],
  elementos: ["registros", "records"],
  "Abrir escenario": ["Abrir regla", "Open rule"],
  "Un nuevo escenario": ["Nueva regla", "New rule"],
  "Empezá con un evento.": [
    "Elegí un evento y agregá acciones.",
    "Choose an event and add actions.",
  ],
  "Conectá lo que sigue.": ["", ""],
  "Versión / paso": ["Versión / paso", "Version / step"],
  Estado: ["Estado", "Status"],
  Recibido: ["Recibido", "Received"],
  Creado: ["Creado", "Created"],
  Fecha: ["Fecha", "Date"],
  Destino: ["Destino", "Target"],
  Detalle: ["Detalle", "Details"],
  Operación: ["Operación", "Operation"],
  Inspeccionar: ["Ver evento", "View event"],
  "· índice": ["· índice", "· index"],
  "Ver pasos": ["Ver pasos", "View steps"],
  "Entrada de auditoría": ["Registro de auditoría", "Audit entry"],
  "Ver detalle": ["Ver detalle", "View details"],
  "Cargando…": ["Cargando…", "Loading…"],
  "Todavía no hay registros": ["No hay registros", "No records yet"],
  "Los eventos aparecerán cuando los ingreses a esta instalación.": [
    "Los eventos recibidos aparecerán acá.",
    "Received events will appear here.",
  ],
  "Los resultados del motor aparecerán acá.": [
    "Las ejecuciones aparecerán acá.",
    "Runs will appear here.",
  ],
  "Cargar 50 anteriores": ["Cargar anteriores", "Load older records"],
  "Definiciones versionadas · Ejecución durable · Sin datos de producción en este entorno":
    ["", ""],
  "El archivo supera 64 KiB.": [
    "El archivo supera el límite de 64 KiB.",
    "The file exceeds the 64 KiB limit.",
  ],
  "¿Reemplazar los cambios del editor?": [
    "¿Reemplazar los cambios del editor?",
    "Replace the changes in the editor?",
  ],
  "Archivo importado al editor. Aún no se guardó ni activó.": [
    "Archivo importado. Falta guardar la regla.",
    "File imported. Save the rule to keep it.",
  ],
  "Definición YAML": ["Definición YAML", "YAML definition"],
  "Importá, editá o exportá el contrato completo. No se modifica el motor.": [
    "Editá la definición de la regla.",
    "Edit the rule definition.",
  ],
  Cancelar: ["Cancelar", "Cancel"],
  "YAML válido aplicado al editor. Guardá para crear una versión.": [
    "Definición válida. Falta guardar los cambios.",
    "Valid definition. Save your changes.",
  ],
  "Validar y aplicar al editor": ["Aplicar cambios", "Apply changes"],
  "Información del backend": ["Información de la API", "API information"],
  "Cerrar detalle": ["Cerrar detalle", "Close details"],
  "Detalle del evento": ["Detalle del evento", "Event details"],
  "Detalle de la ejecución": ["Detalle de la ejecución", "Run details"],
  "El payload fue eliminado por retención y no puede ejecutarse.": [
    "El contenido del evento ya no está disponible por la política de retención.",
    "The event content is no longer available due to the retention policy.",
  ],
  "Ejecutar evalúa las reglas activas para este evento; puede escribir en sistemas externos.":
    [
      "Ejecutar aplica las reglas activas y puede modificar Jira o Bitbucket.",
      "Running applies active rules and may modify Jira or Bitbucket.",
    ],
  "¿Ejecutar este evento contra las reglas activas? Puede generar acciones reales.":
    [
      "¿Ejecutar este evento? Puede modificar Jira o Bitbucket.",
      "Run this event? It may modify Jira or Bitbucket.",
    ],
  "Ejecutar evento": ["Ejecutar evento", "Run event"],
  "Reconciliación de pipeline": [
    "Vincular ejecución de pipeline",
    "Link pipeline run",
  ],
  "Adjuntá el UUID de un pipeline existente. Esto verifica su identidad; no vuelve a lanzarlo.":
    [
      "Ingresá el UUID del pipeline que ya se ejecutó. No se iniciará otro.",
      "Enter the UUID of the pipeline that already ran. This will not start another one.",
    ],
  "UUID del pipeline existente (sin llaves):": [
    "UUID del pipeline (sin llaves):",
    "Pipeline UUID (without braces):",
  ],
  "Pipeline reconciliado.": ["Ejecución vinculada.", "Run linked."],
  "Reconciliar pipeline": ["Vincular pipeline", "Link pipeline"],
  "Ingresar un evento": ["Ingresar evento", "Submit event"],
  "Se guarda en el backend configurado y se procesa con sus reglas activas.": [
    "Se guardará en la API conectada y se procesará con las reglas activas.",
    "It will be saved to the connected API and processed by active rules.",
  ],
  "Tipo de evento": ["Tipo de evento", "Event type"],
  "Identificador de entrega (idempotencia)": [
    "Identificador único de entrega",
    "Unique delivery identifier",
  ],
  "Payload original (objeto JSON)": [
    "Contenido del evento (JSON)",
    "Event content (JSON)",
  ],
  "¿Guardar y procesar este evento? Si coincide con una regla activa se ejecutarán sus acciones.":
    [
      "¿Guardar y procesar el evento? Puede ejecutar acciones de las reglas activas.",
      "Save and process this event? It may execute actions from active rules.",
    ],
  "Evento recibido por el motor.": [
    "Evento recibido.",
    "Event received.",
  ],
  "Guardar y procesar evento": ["Guardar y procesar", "Save and process"],
  "Corregí los valores JSON inválidos antes de continuar.": [
    "Corregí el JSON marcado antes de continuar.",
    "Fix the highlighted JSON before continuing.",
  ],
  "Hay cambios sin guardar. ¿Querés salir del editor?": [
    "Hay cambios sin guardar. ¿Querés salir?",
    "You have unsaved changes. Leave the editor?",
  ],
  "¿Descartar los cambios sin guardar?": [
    "¿Descartar los cambios sin guardar?",
    "Discard unsaved changes?",
  ],
  "La definición ya estaba guardada.": [
    "No hay cambios para guardar.",
    "No changes to save.",
  ],
  "Guardá y validá la versión antes de activarla.": [
    "Guardá la regla antes de activarla.",
    "Save the rule before enabling it.",
  ],
  "Versión activada.": ["Regla activada.", "Rule enabled."],
  "Desactivar impide nuevas ejecuciones y marca como fallidas las pendientes. ¿Continuar?":
    [
      "¿Desactivar la regla? No iniciará nuevas ejecuciones y cancelará las pendientes.",
      "Disable this rule? It will stop new runs and cancel pending ones.",
    ],
  "Regla desactivada.": ["Regla desactivada.", "Rule disabled."],
  "Seleccioná al menos un evento guardado.": [
    "Seleccioná al menos un evento.",
    "Select at least one event.",
  ],
  "Elegí una versión para comparar.": [
    "Elegí una versión para comparar.",
    "Select a version to compare.",
  ],
  "Hay referencias _steps por índice. Quitar este paso cambia los índices siguientes; deberás revisar esas referencias. ¿Continuar?":
    [
      "Otros pasos usan resultados anteriores. Si quitás este paso, revisá sus referencias. ¿Continuar?",
      "Other steps use previous results. Review their references after removing this step. Continue?",
    ],
  "Reordenar cambia los índices _steps. Revisá las referencias antes de activar. ¿Continuar?":
    [
      "Cambiar el orden afecta las referencias a resultados anteriores. ¿Continuar?",
      "Changing the order affects references to previous results. Continue?",
    ],
  "Valor JSON": ["Valor JSON", "JSON value"],
  "JSON incompleto o inválido; aún no se aplicó.": [
    "JSON inválido. El cambio todavía no se aplicó.",
    "Invalid JSON. The change has not been applied.",
  ],
  "Campo / ruta": ["Campo", "Field"],
  "Elegir dato": ["Elegir dato", "Choose data"],
  "Datos disponibles": ["Datos disponibles", "Available data"],
  "Cerrar selector": ["Cerrar selector", "Close picker"],
  "Buscar campo": ["Buscar campo", "Search fields"],
  "Buscar un campo…": ["Buscar campo…", "Search fields…"],
  "Sin muestras para este campo. Podés escribir la ruta.": [
    "No hay campos que coincidan. También podés escribir la ruta.",
    "No matching fields. You can also enter a path.",
  ],
  "Campos observados, no un esquema obligatorio. Solo se cargan 60 coincidencias.":
    [
      "Campos encontrados en eventos recibidos.",
      "Fields found in received events.",
    ],
  "Quitar valor opcional": ["Quitar campo", "Remove field"],
  "Valor fijo": ["Valor fijo", "Fixed value"],
  Dato: ["Dato", "Data"],
  Plantilla: ["Plantilla", "Template"],
  "Ruta del dato": ["Campo de origen", "Source field"],
  "Usar valor si falta el dato": [
    "Usar valor si falta el dato",
    "Use a fallback when data is missing",
  ],
  "Valor alternativo": ["Valor alternativo", "Fallback value"],
  "Insertar referencia": ["Insertar dato", "Insert data"],
  "Una referencia completa conserva el tipo JSON. $$": [
    "Una referencia completa conserva el tipo. $$",
    "A complete reference preserves its type. $$",
  ],
  "escapa una referencia literal.": [
    "permite escribir una referencia literal.",
    "writes a literal reference.",
  ],
  "Tipo del valor": ["Tipo de valor", "Value type"],
  Texto: ["Texto", "Text"],
  Número: ["Número", "Number"],
  Booleano: ["Sí / no", "Boolean"],
  "Lista JSON": ["Lista", "List"],
  "Objeto JSON": ["Objeto JSON", "JSON object"],
  Nulo: ["Nulo", "Null"],
  "Texto literal": ["Texto", "Text"],
  "Valor booleano": ["Valor booleano", "Boolean value"],
  'Se enviará null, no el texto "null".': [
    "Se enviará un valor nulo.",
    "A null value will be sent.",
  ],
  "es igual a": ["es igual a", "equals"],
  "es distinto de": ["es distinto de", "does not equal"],
  "está en la lista": ["está en la lista", "is in the list"],
  contiene: ["contiene", "contains"],
  existe: ["existe", "exists"],
  "mayor que": ["mayor que", "greater than"],
  "mayor o igual": ["mayor o igual", "greater than or equal"],
  "menor que": ["menor que", "less than"],
  "menor o igual": ["menor o igual", "less than or equal"],
  "El filtro supera la profundidad permitida.": [
    "El filtro tiene demasiados niveles.",
    "The filter has too many levels.",
  ],
  "Tipo de filtro": ["Tipo de condición", "Condition type"],
  "Comparar un campo": ["Comparar un campo", "Compare a field"],
  "TODAS las condiciones (all)": ["Se cumplen todas", "All conditions match"],
  "ALGUNA condición (any)": ["Se cumple alguna", "Any condition matches"],
  "NO se cumple (not)": ["No se cumple", "Does not match"],
  "ALGÚN elemento de una lista (some)": [
    "Algún elemento de la lista cumple",
    "Any list item matches",
  ],
  "Quitar condición": ["Quitar condición", "Remove condition"],
  "Agregar condición": ["Agregar condición", "Add condition"],
  "Ruta de la lista": ["Campo que contiene la lista", "List field"],
  "Las rutas siguientes son relativas a cada elemento, no al evento.": [
    "Las condiciones siguientes se aplican a cada elemento de la lista.",
    "The following conditions apply to each list item.",
  ],
  "Debe existir": ["Debe existir", "Must exist"],
  "No debe existir": ["No debe existir", "Must not exist"],
  "Valor de comparación (JSON)": ["Comparar con (JSON)", "Compare with (JSON)"],
  "Nuevo ": ["Nuevo ", "New "],
  "Agregar ": ["Agregar ", "Add "],
  "summary, description, labels, priority, assignee, duedate o customfield_N. Los campos deben estar habilitados en la instalación.":
    [
      "Solo se pueden usar campos habilitados en la instalación.",
      "Only fields enabled in this installation can be used.",
    ],
  "Campos Jira": ["Campos del ticket", "Issue fields"],
  "Variables del pipeline": ["Variables del pipeline", "Pipeline variables"],
  "Verificar el ticket consultado": [
    "Agregar condiciones al ticket consultado",
    "Add conditions for the fetched issue",
  ],
  "Este filtro se aplica a la respuesta actual de Jira, no al evento original.":
    [
      "Estas condiciones usan el estado actual del ticket en Jira.",
      "These conditions use the current issue in Jira.",
    ],
  "Identificador numérico de Jira": ["ID numérico de Jira", "Numeric Jira ID"],
  "(opcional)": ["(opcional)", "(optional)"],
  "Configuración JSON completa": ["Editar JSON", "Edit JSON"],
  "Acción completa (edición avanzada)": [
    "Definición de la acción (JSON)",
    "Action definition (JSON)",
  ],
  "Evento de prueba": ["Evento de prueba", "Test event"],
  "Filtrar los eventos": ["Condiciones de entrada", "Entry conditions"],
  "Agregar módulo": ["Agregar paso", "Add step"],
  "Elegí una acción": ["Elegí una acción", "Choose an action"],
  "Consultar ticket": ["Consultar ticket", "Get issue"],
  "Crear ticket": ["Crear ticket", "Create issue"],
  "Clonar ticket": ["Clonar ticket", "Clone issue"],
  "Editar ticket": ["Editar ticket", "Edit issue"],
  "Cambiar estado": ["Cambiar estado", "Change status"],
  "Agregar comentario": ["Agregar comentario", "Add comment"],
  "Vincular tickets": ["Vincular tickets", "Link issues"],
  "Ejecutar pipeline": ["Ejecutar pipeline", "Run pipeline"],
  Ticket: ["Ticket", "Issue"],
  "Ticket de origen": ["Ticket de origen", "Source issue"],
  Proyecto: ["Proyecto", "Project"],
  "ID del tipo de ticket": ["ID del tipo de ticket", "Issue type ID"],
  "Ticket padre": ["Ticket padre", "Parent issue"],
  "ID del estado destino": ["ID del estado destino", "Target status ID"],
  "ID del estado de origen (opcional)": [
    "ID del estado de origen (opcional)",
    "Source status ID (optional)",
  ],
  Comentario: ["Comentario", "Comment"],
  "Ticket de entrada": ["Ticket de entrada", "Inward issue"],
  "Ticket de salida": ["Ticket de salida", "Outward issue"],
  "ID del tipo de vínculo": ["ID del tipo de vínculo", "Link type ID"],
  Repositorio: ["Repositorio", "Repository"],
  Rama: ["Rama", "Branch"],
  "Pipeline personalizado": ["Pipeline personalizado", "Custom pipeline"],
  "Campos a copiar": ["Campos a copiar", "Fields to copy"],
  Idioma: ["Idioma", "Language"],
  Actualizar: ["Actualizar", "Refresh"],
  Operador: ["Operador", "Operator"],
  Existencia: ["Existencia", "Existence"],
  pending: ["Pendiente", "Pending"],
  dispatching: ["Enviando", "Dispatching"],
  waiting: ["En espera", "Waiting"],
  succeeded: ["Completada", "Succeeded"],
  failed: ["Falló", "Failed"],
  needs_review: ["Requiere revisión", "Needs review"],
  processed: ["Procesado", "Processed"],
  configured: ["Configurado", "Configured"],
  not_configured: ["Sin configurar", "Not configured"],
  unknown: ["No verificado", "Not verified"],
  "Versión {version} guardada. No se activó automáticamente.": [
    "Versión {version} guardada. La activación no cambió.",
    "Version {version} saved. Activation was not changed.",
  ],
  "Versión {version} guardada; hay cambios posteriores sin guardar.": [
    "Versión {version} guardada. Quedan cambios sin guardar.",
    "Version {version} saved. There are newer unsaved changes.",
  ],
  "¿Activar {name}, versión {version}? Los próximos eventos coincidentes ejecutarán sus acciones.":
    [
      "¿Activar {name}, versión {version}? Los próximos eventos pueden ejecutar sus acciones.",
      "Enable {name}, version {version}? Future matching events may execute its actions.",
    ],
  "No se pudo cargar la configuración de conexión.": [
    "No se pudo cargar la configuración de conexión.",
    "Could not load connection configuration.",
  ],
};
