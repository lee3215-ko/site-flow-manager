# SiteFlow private administrator

## Local Windows use

Run from the SiteFlow source directory:

```powershell
python -m pip install -r admin_server/requirements.txt
python -m admin_server.local
```

On first start, enter a new administrator password (14+ characters). Open
http://127.0.0.1:8787 and log in. Configuration is DPAPI encrypted under
`%LOCALAPPDATA%/SiteFlowAdmin/server.bin`. The database is stored alongside it.
Back up both together; encrypted tokens cannot be recovered without the vault key.
DPAPI configuration is bound to this Windows user, not portable to other computers.

In another terminal run `python -m admin_server.local --connection-key` to copy the
device key. In SiteFlow, open `중앙 관리자 연동`, enter the local URL, device name,
and key, then explicitly consent to sending records and Cloudflare credentials.
Disabled by default. Naver cookies/passwords and raw logs are never uploaded.
Offline state snapshots remain encrypted on the device and retry every 30 seconds.
Historical raw logs are not imported. First sync imports current project states;
subsequent saves retain changed snapshots. Deleted desktop rows remain in history.

## Central hosting (not provisioned automatically)

Localhost is accessible only on this computer. To connect other PCs, provision an
always-on server and HTTPS domain/reverse proxy. Do not expose the local HTTP mode
or port-forward it directly to the Internet. Use VPN/access controls as appropriate.
Install both root requirements.txt and admin_server/requirements.txt plus Node.js/npm
for Wrangler. Run `python -m admin_server.server` behind a reverse proxy at port 8787.
Set SITEFLOW_SESSION_KEY and SITEFLOW_DEVICE_KEY to independent random 32+ character
values, SITEFLOW_PASSWORD_HASH to a Werkzeug password hash, SITEFLOW_VAULT_KEY to a
Fernet key, SITEFLOW_ADMIN_DATA to a protected persistent folder. Do not set
SITEFLOW_LOCAL_HTTP in production. Set proxy upload limit 100MB and timeout >600s.
Back up database and vault key separately and restrict filesystem permissions.
No public hosting, DNS or subscription is configured by this change.

## Security and scope

Admin cookies are HttpOnly, SameSite Strict, secure in production; mutations use
CSRF headers. Device credentials can submit snapshots, not read records or deploy.
All own PCs share the ingestion key: rotate it on every PC if one is lost.
Token plaintext is returned only after password reauthentication and erased from
the page after 30 seconds; copied clipboard contents are not automatically erased.
API tokens are Fernet encrypted in SQLite. Use Cloudflare Pages minimum permissions.
Login/reveal failures throttle globally at 10 per five minutes (process lifetime).
Only one redeployment runs at a time; tasks are synchronous. Audit records show
started/success/failed. An interrupted started task requires manual Cloudflare review.
ZIPs are temporary, static-only, maximum 100MB compressed and 2GB extracted. Use a
fresh complete ZIP including ownership verification HTML; original site source is
not downloaded or archived. Account and existing project name must be explicitly
selected; no automatic creation/failover. Check your deployed site after completion.
