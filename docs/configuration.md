# Configuration

Unity Helpdesk adds a few settings on top of stock Frappe Helpdesk. They live on the
single **HD Settings** doctype (Desk → HD Settings) and are surfaced to the SPA through
`helpdesk.api.unity_helpdesk.get_profile` (under `settings`).

## Unity HD Settings fields

| Field | Type | Default | Purpose |
| --- | --- | --- | --- |
| `helpdesk_ui` | Select | `Default Helpdesk` | Which front-end to serve. |
| `enable_unity_ticket_reminders` | Check | `0` | Daily reminder for open tickets. |
| `unity_reminder_after_days` | Int | `3` | Age threshold for reminders. |
| `unity_email_thread_layout` | Select | `Classic` | Ticket thread layout in the SPA. |
| `unity_bulk_email_default_recipients` | Small Text | *(blank)* | Comma/newline-separated addresses BCC'd on **every** bulk email (e.g. an audit mailbox). **Leave blank to disable.** |

### `unity_bulk_email_default_recipients`

Replaces a previously hardcoded audit address. When set, every bulk email is also BCC'd
to these addresses, and the audit ticket records the full recipient list so the mailbox
owner can see who was emailed. When blank, no audit copy is sent. See
[Bulk Email](bulk-email.md).

The address is parsed leniently (comma, semicolon, or newline separated) and each entry
is validated; invalid entries are ignored.

## Migrating from the old hardcoded address

Older deployments hardcoded a single audit address. The open-source code ships **no**
site-specific address — the field defaults blank. To preserve the old behaviour on an
existing site without putting an address in code, set a site-config key before migrating:

```bash
bench --site <site> set-config bulk_email_default_recipients feedback@example.com
bench --site <site> migrate
```

The patch `helpdesk.patches.unity_bulk_email_default_recipients_backfill` copies that key
into `unity_bulk_email_default_recipients` on existing sites (only if the field hasn't
already been set in the UI). Fresh installs skip the patch and stay blank. You can also
just type the address into HD Settings directly — that always takes precedence.

## Outgoing email

Bulk email and agent replies require a default **outgoing** Email Account. Bulk send
fails fast with a clear message if none is configured.
