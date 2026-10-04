# JobTracker AI Production Launch Checklist

## Custom Domain

Target public hostname: `www.jobtrackerai.com`

Current live fallback: `https://swara-jobtracker-live-api.azurewebsites.net`

Add these DNS records at the active DNS host for `jobtrackerai.com`:

| Host | Type | Value |
| --- | --- | --- |
| `www` | `CNAME` | `swara-jobtracker-live-api.azurewebsites.net` |
| `asuid.www` | `TXT` | `42AEAB6389967E07CCCDB7AE541B4EE6D73C2B2DFA954042DD17779348314BCF` |

After DNS propagates:

1. Add `www.jobtrackerai.com` as a custom domain on the Azure App Service.
2. Create and bind an Azure-managed TLS certificate for `www.jobtrackerai.com`.
3. Set `JOBTRACKER_PUBLIC_BASE_URL=https://www.jobtrackerai.com` in App Service settings.
4. Smoke test `/landing`, `/digest-signup`, `/privacy`, `/terms`, `/login`, `/jobs`, `/operations`, and a subscriber manage link.

Do not replace production email links with the custom domain until the Azure hostname and HTTPS binding are verified.

## Email Domain Authentication

The current app can send with SMTP, but broad public launch should use a sender address on the app domain, such as `matches@jobtrackerai.com`.

Required records before broad launch:

| Record | Purpose | Notes |
| --- | --- | --- |
| SPF | Authorizes the email provider to send for `jobtrackerai.com` | Include the chosen provider, for example Google Workspace, SES, SendGrid, or Postmark. |
| DKIM | Cryptographically signs mail | Generate provider-specific DKIM records in the email provider console. |
| DMARC | Defines alignment policy and reporting | Start with monitoring/quarantine, then tighten after successful beta delivery. |

If sending continues from `jobtrackerai28@gmail.com`, SPF/DKIM/DMARC for `jobtrackerai.com` will not align with that sender. Use a domain sender before collecting subscribers broadly.

## Beta Test

Run a 5–10 person beta before public launch:

1. Invite students/users across at least three different majors or job fields.
2. Ask each beta user to select role preferences, upload a resume, and subscribe.
3. Verify each user receives exactly one daily email around the configured send window.
4. Confirm each email includes ten best available verified-open jobs, match score, score-tier explanation when below 90, apply link, manage-preferences link, and unsubscribe link.
5. Collect feedback on relevance, freshness, UI clarity, email usefulness, and unsubscribe/manage-preference flow.

## Monitoring Alerts

Owner alerts should be enabled with `JOBTRACKER_ALERT_EMAIL`.

Alert sources:

- Public-board collection failure.
- Gemini recent-job scoring failure.
- Gemini batch submission failure.
- Daily digest delivery failure or no-sendable-match stop.
- Automation worker crash.
- Azure App Service HTTP 5xx metric alert.

## Final Go/No-Go

- All tests pass.
- Production readiness has zero blocking items.
- New jobs from primary boards appear in PostgreSQL.
- New described jobs receive Gemini scores before digest sending.
- Daily digest sends with verified-open jobs only.
- Privacy Policy, Terms, unsubscribe, and manage-preferences links are live.
- Custom domain and HTTPS are live.
- SPF, DKIM, and DMARC pass for the domain sender.
