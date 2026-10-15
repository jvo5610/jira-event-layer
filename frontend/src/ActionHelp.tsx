import { t } from "./i18n";
import { actionLabels, actionOutputKeys, type Obj } from "./model";

export function ActionHelpContent({ actions, index }: { actions: Obj[]; index: number }) {
  const action = actions[index];
  const prior = actions.slice(0, index);
  const readIndex = prior.map(a => a.type).lastIndexOf("jira.issue.get");
  const previous = prior.map(a => Boolean(actionOutputKeys[a.type]?.includes("key"))).lastIndexOf(true);
  return <div className="action-help-content">
    <p>{t("Cada acción guarda su resultado al terminar. Los pasos siguientes pueden leerlo; los anteriores no.")}</p>
    <h4>{t("Resultado de este paso")}</h4>
    <code>_steps["{index}"]</code>
    <ul>{(actionOutputKeys[action.type] ?? []).map(key => <li key={key}><code>{key}</code></li>)}</ul>
    <p className="muted">{t("Son campos posibles: algunos aparecen solo si hubo cambios. Crear o clonar devuelve la key, no todos los campos del ticket; agregá Consultar ticket para leerlos.")}</p>
    <h4>{t("Usar un resultado anterior")}</h4>
    {index === 0 ? <p>{t("Este es el primer paso: todavía no hay resultados anteriores. Podés usar datos del evento, como issue.key.")}</p> : <>
      <p>{t("Elegí Datos y abrí { } para seleccionar un resultado de un paso anterior. Se conserva el tipo original del valor.")}</p>
      {previous >= 0 && <p>{t("Ticket del paso {step}", { step: previous + 1 })}: <code>{`_steps["${previous}"].key`}</code></p>}
      {readIndex >= 0 && <p>{t("Resumen consultado en el paso {step}", { step: readIndex + 1 })}: <code>{`_steps["${readIndex}"].fields.summary`}</code></p>}
      <p>{t("Para mezclar texto y datos, elegí Plantilla. Por ejemplo:")}</p>
      <pre>{previous >= 0 ? `Ticket: ${'${'}_steps["${previous}"].key}` : `Resultado: ${'${'}_steps["${index - 1}"].${actionOutputKeys[actions[index - 1].type]?.[0] ?? "result"}}`}</pre>
    </>}
    <p>{t("Paso 1 corresponde a _steps[\"0\"], paso 2 a _steps[\"1\"]. issue.fields siempre lee el evento original, no los cambios de una acción anterior.")}</p>
    <p className="muted">{t("La descripción de Jira puede ser un objeto ADF, no texto. No la concatènes en una plantilla: eso no conserva su formato ni agrega un párrafo.")}</p>
  </div>;
}

export function ActionHelp({ actions, index }: { actions: Obj[]; index: number }) {
  return <details className="action-help" key={`${index}:${actions[index].type}`}>
    <summary aria-label={t("Ayuda de la acción")}>? <span>{t("Cómo usar esta acción")}: {t(actionLabels[actions[index].type])}</span></summary>
    <ActionHelpContent actions={actions} index={index} />
  </details>;
}
