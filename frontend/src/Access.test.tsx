import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";
import { Access, credentialStatus, expirySeconds, permissionOptions, type Credential } from "./Access";
import { setLanguage } from "./i18n";

const row: Credential = { id: "public-id", name: "service", permissions: ["api:read"], created_at: "2026-01-01T00:00:00Z", expires_at: null, revoked_at: null, pending_until: null, rotation_parent_id: null };
afterEach(() => setLanguage("es"));
describe("Access management", () => {
  it("starts locked and never renders management controls or a backend credential", () => {
    const html = renderToStaticMarkup(<Access />);
    expect(html).toContain('type="password"');
    expect(html).toContain("Ingresar a administración");
    expect(html).not.toContain("Tokens emitidos");
    expect(html).not.toContain("Crear token");
    expect(html).not.toContain("localStorage");
  });
  it("translates the locked screen without translating permission identifiers", () => {
    setLanguage("en");
    expect(renderToStaticMarkup(<Access />)).toContain("Unlock administration");
    expect(permissionOptions.map(([permission]) => permission)).toEqual(["api:read", "rules:write", "flows:operate", "auth:admin"]);
  });
  it("validates expiration bounds instead of issuing non-expiring delegated tokens", () => {
    expect(expirySeconds(1, 60)).toBe(60);
    expect(expirySeconds(365, 86400)).toBe(31536000);
    for (const value of [0, -1, 366, NaN, Infinity]) expect(expirySeconds(value, 86400)).toBeNull();
    expect(expirySeconds(0.1, 60)).toBeNull();
  });
  it("distinguishes revoked, expired, pending and active credentials", () => {
    expect(credentialStatus(row)).toBe("Activo");
    expect(credentialStatus({...row, revoked_at: "2026-01-01"})).toBe("Revocado");
    expect(credentialStatus({...row, expires_at: "2026-01-01"})).toBe("Vencido");
    expect(credentialStatus({...row, pending_until: "2999-01-01"})).toBe("Pendiente de confirmar");
    expect(credentialStatus({...row, pending_until: "2026-01-01"})).toBe("Vencido");
  });
});
