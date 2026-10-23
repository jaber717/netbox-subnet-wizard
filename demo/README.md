# Demo NetBox for testing the Subnet Wizard

A throwaway NetBox 4.6 (Postgres + Valkey) with the plugin installed. Run all commands from this `demo` folder.

```powershell
docker compose up -d --build          # first start takes a few minutes (migrations)
docker compose ps                     # wait until netbox shows "healthy"
docker compose cp seed.py netbox:/tmp/seed.py
docker compose exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py nbshell --command "exec(open('/tmp/seed.py', encoding='utf-8-sig').read())"
```

Open http://localhost:8000 and log in as `admin` / `admin`.

The plugin source is bind-mounted, so after editing code just run `docker compose restart netbox`.

## Delete everything

```powershell
docker compose down -v --rmi local
```

This removes the containers, the database volume and the built image.
