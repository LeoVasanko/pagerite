# Production setup

From a local demo to a real site: run Pagerite as a systemd service behind
a reverse proxy that terminates HTTPS, with Paskia guarding the editing API.

The moving parts:

- **Pagerite** — serves the public site on `localhost:8100` and the editing
  API under `/_api`.
- **Paskia** — the SSO server; owns `/auth/` and answers forward-auth
  subrequests.
- **A reverse proxy** — Caddy below, but nginx or anything with
  forward-auth support works the same way.

## Pagerite as a systemd service

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) on the
system, create a user, and add a template unit:

```sh
sudo useradd --system --home-dir /srv/pagerite --create-home pagerite
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh
sudo systemctl edit --force --full pagerite.service
```

```ini
[Unit]
Description=Pagerite CMS

[Service]
Type=simple
User=pagerite
SyslogIdentifier=pagerite
WorkingDirectory=/srv/pagerite
ExecStart=uvx pagerite example.com --dbip

[Install]
WantedBy=multi-user.target
```

Replace `example.com` with your actual domain name. `--dbip` keeps the local GeoIP database up to date: leave out if you don't want DBIP data for analytics.

```sh
sudo systemctl enable --now pagerite
sudo journalctl -ocat -fu pagerite
```

## Running it on internet

We recommend Caddy for making your site publicly visible on the Internet. Presumably you already have some proxy, perhaps Nginx, but our setup is not much different of any other service you might already be running. ChatGPT and the likes can also help with the configuration because online documentation is limited. Note that Paskia also has extensive documentation on [running on various proxy servers](https://git.zi.fi/LeoVasanko/paskia/src/branch/main/docs/proxy/index.md)

Install [Caddy](https://caddyserver.com/) and follow the [Paskia setup guide](https://git.zi.fi/LeoVasanko/paskia) to get the SSO server running and its `auth` snippets copied to `/etc/caddy/auth` — that guide covers Paskia's own configuration and admin registration in detail.

Then the site config. Only the editing API needs gating; the site itself is public:

```caddyfile
example.com {
    import auth/setup

    reverse_proxy /auth/* localhost:4401

    @api path /_api/*
    handle @api {
        import auth/require perm=pagerite:admin
        reverse_proxy localhost:8100
    }

    handle {
        reverse_proxy localhost:8100
    }
}
```

Reload Caddy, then create a permission with scope `pagerite:admin` in the
Paskia admin panel (`/auth/admin/`) and assign it to yourself, as the Paskia
guide describes. Anonymous visitors now get 401 from `/_api`, logged-in
users without the permission get 403, and admins get the editing pens.

## nginx or another proxy

The shape is identical everywhere:

- `/auth/` proxies to Paskia (`localhost:4401`).
- `/_api` requires a forward-auth subrequest against Paskia — on nginx that
  is `auth_request` against Paskia's verify endpoint — before proxying to
  Pagerite (`localhost:8100`).
- Everything else proxies straight to Pagerite.

Paskia ships per-proxy forward-auth guides covering
[Caddy, nginx and others](https://git.zi.fi/LeoVasanko/paskia/src/branch/main/docs/proxy/index.md);
adapt the matcher to `/auth/` and `/_api` as above and leave the rest public.
