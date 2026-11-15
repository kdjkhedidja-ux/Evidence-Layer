# Workspace invitation pilot — 2026-11-15

Status: **PASS.** Link-based one-time invitations work end to end in production.
Automatic email delivery is not enabled yet.

## Production sequence

1. Migration `20261115140000_workspace_invitations.sql` applied and verified
   (49 migrations, pending=0).
2. Evidence Service redeployed from `dd8ead8`
   (`85dd0395-2030-416e-b340-ac0fa33d8744`).
3. The pilot workspace owner created an invitation for a new email.
4. A new Auth user signed in and accepted the one-time token.
5. The accept response returned the pilot workspace and `member` role.
6. Database verification: `member|active`.

## Boundary

- Only the SHA-256 hash of the invitation token is stored.
- Tokens expire after seven days and are single-use.
- Acceptance requires an authenticated user.
- The owner shares the link manually; Supabase Auth invite/magic-link email
  delivery or a transactional provider is the next increment.
- Synthetic pilot workspace and users remain; no customer data.
