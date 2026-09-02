# Jianada License Admin

Minimal admin portal for customer licenses and account whitelist publishing.

This project is intentionally separate from the existing EXE code. The first
version keeps the current EXE compatible by continuing to publish whitelist JSON
files to:

```text
account-whitelist/<machine_id>.json
```

## What is included

- Admin login backed by Worker secrets.
- Customer and machine management.
- License code generation using the same RSA signature format as
  `tools/gen_license.py`.
- D1 tables for customers, machines, and generated license events.
- R2 whitelist publishing with the existing JSON shape.
- Static admin UI in `web/` with a demo mode for local preview.

## Local preview

The UI can be previewed without Cloudflare credentials:

```powershell
cd license-admin
python -m http.server 5174 -d web
```

Open `http://127.0.0.1:5174`. If the API is not running, the UI uses demo data.


## Current Cloudflare deployment

Deployed on 2026-08-18.

- Worker: `jianada-license-admin`
- URL: `https://jianada-license-admin.soft-api-7mskfl.workers.dev`
- D1 database: `jianada-license-admin-db`
- D1 database id: `5e91378f-1ee5-4a0e-8e0a-d83dce7faa01`
- R2 bucket: `pro-downloads`
- R2 key pattern: `account-whitelist/<machine_id>.json`
- Secrets configured: `ADMIN_PASSWORD_SHA256`, `SESSION_SECRET`, `LICENSE_PRIVATE_KEY_PEM`

The initial admin login is stored locally in `.admin-password.local.txt`, which is ignored by Git. The RSA private key remains in Cloudflare Secrets and the existing local `tools/private_key.pem`; it is not committed.

Smoke test data currently exists for machine `ADMINTEST0000001`, with public R2 JSON at:

```text
https://pub-465f078b4f484662b30eb39d27ae5155.r2.dev/account-whitelist/ADMINTEST0000001.json
```
## Customer renewal flow

The desktop app uses an offline signed license code. Editing the expiry date in
this admin portal is not enough by itself: after changing or extending the
expiry, generate a new license code and send it to the customer so they can
activate it in the desktop app.

Recommended steps:

1. Open the admin URL and log in.
2. Select the customer and the bound machine ID.
3. Use `延长 7 天`, `延长 30 天`, `延长 90 天`, or fill `延期天数` and click
   `按填写天数延期并生成授权码`.
4. Click `复制授权码` and send the code to the customer.
5. If account whitelist accounts were changed, click `保存机器码`; that save also
   syncs the whitelist JSON to R2.

The `按到期日生成授权码` button now uses the date currently shown in the card, so
an unsaved expiry date will not accidentally generate a code for the old stored
date.

## Cloudflare setup outline

1. Copy `wrangler.toml.example` to `wrangler.toml`.
2. Create a D1 database and put its id into `wrangler.toml`.
3. Apply `schema.sql` to D1.
4. Bind the existing R2 bucket `pro-downloads`.
5. Set secrets:

```powershell
npx wrangler secret put ADMIN_PASSWORD_SHA256
npx wrangler secret put SESSION_SECRET
npx wrangler secret put LICENSE_PRIVATE_KEY_PEM
```

`ADMIN_PASSWORD_SHA256` is the lowercase SHA-256 hex digest of the admin
password. `LICENSE_PRIVATE_KEY_PEM` must be a PKCS#8 PEM private key
(`-----BEGIN PRIVATE KEY-----`). Do not commit it.

## API compatibility

The R2 record produced by this portal matches the current desktop client:

```json
{
  "machine_id": "E67F37167AD7B0BF",
  "customer_id": "cus_xxx",
  "enabled": true,
  "accounts": ["ccff11", "ccff22"],
  "remark": "customer note",
  "updated_at": "2026-08-17T00:00:00.000Z"
}
```

