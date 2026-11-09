# contrib — server-side helpers (no Python typing needed)

Run from the cloned repo on the NetBox server. Pass your NetBox path if it is not `/opt/netbox`.

## Enable the plugin in configuration.py

```bash
sudo bash contrib/apply-config.sh /opt/netbox
sudo systemctl restart netbox netbox-rq
```

Appends a marked block that adds `netbox_subnet_wizard` to your existing `PLUGINS` (nothing else is
touched). Takes a timestamped backup, runs `manage.py check`, and restores the backup automatically
if the check fails. Re-running replaces the block instead of duplicating it.

## "IP Function" field on IP addresses

```bash
sudo bash contrib/setup-ip-function.sh /opt/netbox
```

NetBox 4.6 does not allow extending the built-in IP **role** list (`FIELD_CHOICES` only covers
`IPAddress.status`). This creates a select custom field **IP Function** on IP addresses instead:
gateway, p2p, nat-pool, public-service, vpn-peer, tunnel, ha-link, mgmt, oob, vtep.

- No configuration edit, no restart. Visible on the IP add/edit form, filterable, in the REST API
  (`custom_fields.ip_function`) and in netbox-subnet-map's native quick-add form.
- One transaction: either everything is applied or nothing.
- To change the list, edit `CHOICES` in `contrib/ip_function_field.py`, push, `git pull` on the
  server and re-run. Values still used by IP addresses are never removed.
- Changes made by this script are not attributed to a user in the change log (it runs outside a
  web request).
