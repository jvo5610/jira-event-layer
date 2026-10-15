import { useEffect, useRef, useState } from "react";
import { t, locale, useLanguage } from "./i18n";

export const permissionOptions = [
  ["api:read", "Consultar y simular"],
  ["rules:write", "Guardar borradores"],
  ["flows:operate", "Activar y ejecutar flujos"],
  ["auth:admin", "Administrar accesos (acceso completo)"],
] as const;
export type Credential = { id: string; name: string; permissions: string[]; created_at: string;
  expires_at: string | null; revoked_at: string | null; pending_until: string | null; rotation_parent_id: string | null };
type Identity = { token_id: string; permissions: string[]; pending: boolean };
type Session = { identity: Identity; pending: Credential | null; session_seconds: number };
type Revealed = Credential & { token: string; kind: "created" | "admin" | "backend" };

export function credentialStatus(row: Credential, now = Date.now()) {
  if (row.revoked_at) return "Revocado";
  if ((row.expires_at && Date.parse(row.expires_at) <= now) ||
      (row.pending_until && Date.parse(row.pending_until) <= now)) return "Vencido";
  return row.pending_until ? "Pendiente de confirmar" : "Activo";
}
export function expirySeconds(amount: number, unit: number) {
  const value = amount * unit;
  return Number.isSafeInteger(value) && value >= 60 && value <= 31536000 ? value : null;
}
class AccessError extends Error {
  constructor(message: string, public status: number) { super(message); }
}
export async function accessRequest(path: string, method = "GET", body?: unknown) {
  const response = await fetch("/access" + path, { method, credentials: "same-origin", cache: "no-store",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(20000) });
  const value = await response.json();
  if (!response.ok) throw new AccessError(typeof value.detail === "string" ? t(value.detail) : t("No se pudo completar la operación."), response.status);
  return value;
}
const date = (value: string | null) => value ? new Date(value).toLocaleString(locale()) : t("Sin vencimiento");

export function Access({ request = accessRequest }: { request?: typeof accessRequest }) {
  useLanguage();
  const [session, setSession] = useState<Session | null>(null);
  const [backend, setBackend] = useState<Identity | null>(null);
  const [rows, setRows] = useState<Credential[]>([]);
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [revealed, setRevealed] = useState<Revealed | null>(null);
  const [saved, setSaved] = useState(false);
  const [name, setName] = useState("");
  const [permissions, setPermissions] = useState<string[]>(["api:read"]);
  const [duration, setDuration] = useState(30);
  const [unit, setUnit] = useState(86400);
  const [adminGrant, setAdminGrant] = useState(false);
  const [resumeRotation, setResumeRotation] = useState(false);
  const [revoking, setRevoking] = useState<Credential | null>(null);
  const input = useRef<HTMLInputElement>(null);
  async function load(nextOffset = offset) {
    const current = await request("/session");
    setSession(current);
    const [tokens, gateway] = await Promise.all([request("/tokens?offset=" + nextOffset), request("/backend").catch(() => null)]);
    setRows(tokens); setBackend(gateway); setOffset(nextOffset);
  }
  async function action(fn: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await fn(); }
    catch (failure) {
      if (failure instanceof AccessError && failure.status === 403) { setSession(null); setRevealed(null); }
      setError(failure instanceof Error ? t(failure.message) : t("No se pudo completar la operación."));
    } finally { setBusy(false); }
  }
  useEffect(() => { let active = true;
    request("/session").then(value => { if (active) { setSession(value); void action(() => load(0)); } })
      .catch(failure => { if (active && !(failure instanceof AccessError && failure.status === 403)) setError(t("No se pudo verificar el acceso.")); });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    if (!session) return;
    const timer = setInterval(() => { void request("/session").then(setSession).catch(() => {
      setSession(null); setRevealed(null); setError(t("La sesión de administración terminó. Volvé a ingresar."));
    }); }, 30000);
    return () => clearInterval(timer);
  }, [!!session]);
  function show(value: Credential & { token: string }, kind: Revealed["kind"]) {
    setRevealed({ ...value, kind }); setSaved(false);
  }
  return <div className="access-page">
    {error && <p className="access-message error" role="alert">{error}</p>}
    {notice && <p className="access-message" role="status">{notice}</p>}
    {!session ? <section className="card access-unlock">
      <h2>{t("Administración de accesos")}</h2>
      <p>{t("Ingresá un token administrador. No se guarda en el navegador; la sesión dura 15 minutos.")}</p>
      <form onSubmit={event => { event.preventDefault(); const token = input.current?.value ?? "";
        if (input.current) input.current.value = "";
        void action(async () => { await request("/session", "POST", { token, ...(resumeRotation ? {confirm_rotation: true} : {}) }); setResumeRotation(false); await load(0); }); }}>
        <label>{t("Token administrador")}<input ref={input} type="password" required autoComplete="off" spellCheck={false} /></label>
        <label className="access-check"><input type="checkbox" checked={resumeRotation} onChange={event => setResumeRotation(event.target.checked)} />{t("Es el nuevo token de una rotación pendiente: confirmar y revocar el anterior.")}</label>
        <button className="primary" disabled={busy}>{t("Ingresar a administración")}</button>
      </form>
    </section> : <>
      <div className="access-toolbar"><p>{t("Sesión temporal de administración. Los tokens de uso normal no pueden gestionar accesos.")}</p>
        <button disabled={busy} onClick={() => void action(async () => { await request("/logout", "POST"); setSession(null); setRevealed(null); setRows([]); })}>{t("Cerrar administración")}</button></div>
      {revealed && <section className="card access-secret" aria-label={t("Token creado")}>
        <h3>{t("Guardalo ahora: se muestra una sola vez")}</h3>
        <p>{t("Guardá el token en tu gestor de secretos. No se puede recuperar después de cerrar esta pantalla.")}</p>
        <textarea aria-label={t("Nuevo token")} value={revealed.token} readOnly spellCheck={false} rows={3} />
        <div className="access-actions"><button onClick={() => void action(async () => { await navigator.clipboard.writeText(revealed.token); setNotice(t("Token copiado.")); })}>{t("Copiar token")}</button>
          <button onClick={() => setRevealed(null)}>{t("Ocultar token")}</button></div>
        {revealed.pending_until && <p>{t("Confirmar antes de")}: {date(revealed.pending_until)}</p>}
        {revealed.kind === "backend" && <p>{t("Actualizá STUDIO_BACKEND_TOKEN o su archivo en el despliegue y reiniciá la GUI. Después ingresá a administración y confirmá la credencial de la GUI.")}</p>}
      </section>}
      <div className="access-columns">
        <section className="card"><h3>{t("Crear token")}</h3>
          <form onSubmit={event => { event.preventDefault(); void action(async () => {
            const value = await request("/tokens", "POST", { name: name.trim(), permissions, expires_in_seconds: expirySeconds(duration, unit) });
            show(value, "created"); setName(""); setAdminGrant(false); await load(0);
          }); }}>
            <label>{t("Nombre")}<input value={name} onChange={event => setName(event.target.value)} required maxLength={100} placeholder="ci-production" /></label>
            <fieldset><legend>{t("Permisos")}</legend>{permissionOptions.map(([value, label]) =>
              <label className="access-check" key={value}><input type="checkbox" checked={permissions.includes(value)} onChange={event => {
                setPermissions(old => event.target.checked ? [...old, value] : old.filter(item => item !== value)); setAdminGrant(false);
              }} />{t(label)}<code>{value}</code></label>)}</fieldset>
            <p>{t("Activar y ejecutar flujos puede modificar Jira o disparar pipelines.")}</p>
            {permissions.includes("auth:admin") && <label className="access-check access-warning"><input type="checkbox" checked={adminGrant} onChange={event => setAdminGrant(event.target.checked)} />{t("Entiendo que este token tendrá acceso completo, incluida la administración.")}</label>}
            <label>{t("Vigencia")}<div className="access-duration"><input type="number" min={1} max={31536000} required value={duration} onChange={event => setDuration(Number(event.target.value))} />
              <select aria-label={t("Unidad de vigencia")} value={unit} onChange={event => setUnit(Number(event.target.value))}>
                <option value={60}>{t("Minutos")}</option><option value={3600}>{t("Horas")}</option><option value={86400}>{t("Días")}</option></select></div></label>
            <small>{t("Entre un minuto y 365 días. El vencimiento es obligatorio.")}</small>
            <button className="primary" disabled={busy || !name.trim() || !permissions.length || !expirySeconds(duration, unit) || (permissions.includes("auth:admin") && !adminGrant)}>{t("Crear token")}</button>
          </form>
        </section>
        <section className="card"><h3>{t("Rotar mi token administrador")}</h3>
          <p>{t("El token actual sigue vigente hasta confirmar con el nuevo. Sin confirmación, vence el reemplazo; no se reactiva ningún token viejo.")}</p>
          {session.pending ? <><p>{t("Confirmar antes de")}: {date(session.pending.pending_until)}</p>
            <label className="access-check"><input type="checkbox" checked={saved} onChange={event => setSaved(event.target.checked)} />{t("Guardé el nuevo token y actualicé sus consumidores.")}</label>
            <button className="primary" disabled={busy || !saved} onClick={() => void action(async () => {
              await request("/rotation/confirm", "POST"); setRevealed(null); setSaved(false); await load(); setNotice(t("Rotación confirmada. El token anterior fue revocado."));
            })}>{t("Confirmar y revocar el anterior")}</button></> :
            <button disabled={busy} onClick={() => void action(async () => { show(await request("/rotation/start", "POST"), "admin"); await load(); })}>{t("Generar reemplazo")}</button>}
          <hr /><h3>{t("Credencial de la GUI")}</h3>
          <p>{t("La credencial actual se mantiene sólo en el servidor. Su reemplazo debe guardarse en el despliegue antes de confirmar.")}</p>
          <code>{backend?.token_id ?? "—"}</code>
          {backend?.pending ? <button className="primary" disabled={busy} onClick={() => void action(async () => {
            await request("/backend/confirm", "POST"); await load(); setNotice(t("Rotación confirmada. El token anterior fue revocado."));
          })}>{t("Confirmar credencial de la GUI")}</button> :
            <button disabled={busy || !backend || backend.permissions.includes("auth:admin")} onClick={() => void action(async () => { show(await request("/backend/rotate", "POST"), "backend"); await load(); })}>{t("Rotar credencial de la GUI")}</button>}
          {backend?.permissions.includes("auth:admin") && <p className="access-warning">{t("Configurá un token delegado para la GUI, sin auth:admin.")}</p>}
        </section>
      </div>
      <section className="card access-list"><div className="access-actions"><h3>{t("Tokens emitidos")}</h3><button disabled={busy} onClick={() => void action(() => load())}>{t("Actualizar")}</button></div>
        <div className="access-table-scroll"><table><thead><tr>{["Nombre", "Permisos", "Estado", "Vencimiento", "Acciones"].map(label => <th key={label}>{t(label)}</th>)}</tr></thead>
          <tbody>{rows.map(row => <tr key={row.id}><td><strong>{row.name}</strong><small>{row.id}</small>{row.id === backend?.token_id && <small>GUI</small>}</td>
            <td>{row.permissions.map(permission => <code key={permission}>{permission} </code>)}</td><td>{t(credentialStatus(row))}</td>
            <td>{date(row.expires_at)}{row.pending_until && <small>{t("Confirmar antes de")}: {date(row.pending_until)}</small>}</td>
            <td><button className="danger" disabled={busy || !!row.revoked_at} onClick={() => setRevoking(row)}>{t("Revocar")}</button></td></tr>)}</tbody></table></div>
        <div className="access-actions"><button disabled={busy || !offset} onClick={() => void action(() => load(Math.max(0, offset - 50)))}>{t("Anterior")}</button>
          <span>{offset + 1}–{offset + rows.length}</span><button disabled={busy || rows.length < 50} onClick={() => void action(() => load(offset + 50))}>{t("Siguiente")}</button></div>
      </section>
      {revoking && <div className="modal-backdrop"><section className="modal" role="dialog" aria-modal="true" aria-labelledby="revoke-title">
        <h3 id="revoke-title">{t("¿Revocar este token?")}</h3><p><strong>{revoking.name}</strong></p><p>{t("Dejará de funcionar inmediatamente para nuevas solicitudes. También se revoca su reemplazo pendiente.")}</p>
        {revoking.id === backend?.token_id && <p className="access-warning">{t("Este token lo usa la GUI. Revocarlo interrumpirá su conexión hasta configurar otro.")}</p>}
        <div className="modal-actions"><button disabled={busy} onClick={() => setRevoking(null)}>{t("Cancelar")}</button><button className="danger" disabled={busy} onClick={() => void action(async () => {
          await request(`/tokens/${revoking.id}/revoke`, "POST"); setRevoking(null); await load(); setNotice(t("Token revocado."));
        })}>{t("Revocar")}</button></div></section></div>}
    </>}
  </div>;
}
