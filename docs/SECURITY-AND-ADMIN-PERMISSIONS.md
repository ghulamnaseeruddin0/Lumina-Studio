# Security and Admin Permissions

## Trust boundaries and setup

The browser is untrusted. All administrative operations are authorized in the Python API; hiding the Admin button is only a usability feature. With Supabase enabled, the server validates the JWT signature, issuer, audience, and expiry before using its `sub` as the account ID.

To establish the one owner:

1. Create/sign up the intended account through the configured Supabase project.
2. Obtain its Supabase Auth UUID from the trusted Supabase dashboard.
3. Set `LUMINA_SUPER_ADMIN_USER_ID` as a Railway backend secret/environment variable.
4. Restart the backend. On first configured startup, Lumina binds that UUID to the single row in `super_admin_identity`.
5. Keep the same value configured on every restart. A conflicting UUID fails startup. Removing the variable fails closed by leaving the application with no recognized owner; it does not promote the first visitor.

There is no public admin registration/bootstrap endpoint. Never set the owner UUID from a browser form or a user-editable profile field. There must be a Supabase identity for the owner before binding.

## Global roles and permissions

The singleton owner is identified only by the server-side UUID binding and receives the complete permission set. It cannot be deleted, suspended, demoted, or reassigned using ordinary admin routes.

| Role | Intended permissions |
|---|---|
| `member` | No global admin capabilities; ordinary product use remains subject to account status |
| `viewer` | Read dashboard, users, agents, analytics, audit, health, logs and notifications |
| `support` | Read users, audit, health, logs and sessions; change account status through the role grant |
| `operator` | Read operational data; toggle optional agents/features, update allow-listed settings, maintenance, retry notifications, create backups, change status |
| Super Admin | All global admin permissions, owner-only user invitations/deletions and role/status/permission controls |

Roles cannot grant the exclusive owner identity. Stored permission lists are intersected with the selected role's server-defined allow-list. A regular user cannot change their own global role or permissions; user control updates are restricted to the configured owner.

## Sensitive operation controls

- Account creation is an owner-initiated Supabase email invitation; no public admin registration or password creation route is provided.
- Account deletion requires typing the target account's exact email. It deletes the Supabase Auth identity, but Lumina application data is retained. Data erasure is not represented as complete.
- Deactivation and suspension are enforced on each authenticated API request. Status changes also invoke Supabase Auth ban/unban, so newly issued tokens are blocked at the provider too.
- In Supabase multi-user mode, shared API-key/output setting changes require the admin settings grant, shared-token rotation is owner-only, and backup creation requires the backup grant.
- Roles/permissions cannot target the owner account or add grants outside the chosen role.
- Agent disablement is limited to non-critical registered agents; critical pipeline stages are protected.
- Maintenance/feature controls are server-enforced. Health check, admin endpoints, OAuth callbacks, and time-limited publishing media remain available during maintenance.
- Backup creation is audited. Restore is deliberately manual; stop writes, verify the backup and database, and follow the operator recovery process.
- Every admin user, role, agent, feature, maintenance, notification retry, and backup action writes an audit event.

## Secrets and private data

- `SUPABASE_SERVICE_ROLE_KEY`, database URLs, OAuth client secrets, Resend credentials, and token encryption/signing keys belong only in the backend host's secret manager.
- Browser-exposed values are limited to `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` (publishable), and `VITE_API_URL`.
- The admin screen reports configured/not configured state only.
- Provider OAuth tokens are encrypted using `LUMINA_OAUTH_ENCRYPTION_KEY`; preserve this key securely or connected accounts must reconnect.
- Authentication session records contain hashed session identifiers, a keyed hash of source IP (when a salt is set), user agent and timestamps. They do not store bearer tokens.
- Error log output is bounded and passed through Lumina's redaction helper. Treat logs as operationally sensitive.

## Security checklist

- Enforce HTTPS, exact CORS origins, strong Supabase account recovery, and owner MFA in Supabase.
- Use a unique long random `LUMINA_TOKEN` for local-token compatibility; do not use it as multi-user identity.
- Restrict the PostgreSQL network endpoint and Railway project access; rotate provider credentials if exposed.
- Keep backups off the same failure domain, encrypt them, and test restore in a non-production environment.
- Review account invitations and admin audit events; grant only the narrowest support/operator role.
- Update providers and dependencies, run project tests/audit workflows, and verify CORS/JWT behavior after auth configuration changes.

## Remaining risks and limitations

The application depends on external provider availability and scopes. It has not been live-tested against the owner's Supabase, OAuth apps, Resend account, or Railway production data as part of this source change. The UI cannot prove external API permissions. IP/session monitoring is best-effort and not a SIEM; there is no built-in MFA enforcement beyond Supabase configuration. Backup restore and environment-secret rotation need host-console/manual operations. A security review does not replace a deployment-specific penetration test.
