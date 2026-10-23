# NetBox Subnet Wizard

A NetBox 4.6 plugin that allocates one or more child prefixes from a parent container prefix, with a preview before anything is saved.

## What it does

1. Pick a parent **Container** prefix, the size of the new prefixes (e.g. /24) and how many you need.
2. Choose an allocation strategy. It decides which prefixes are pre-selected in the preview:
   - **First fit**: lowest free addresses first.
   - **Best fit**: fills the smallest free gaps first, keeping large blocks intact.
3. Set the status, role, tenant and description for the new prefixes. In the description, `{n}` becomes a sequence number and `{prefix}` becomes the prefix itself.
4. Optionally create a gateway IP, the first usable address, in each prefix. This is skipped for /31, /32, /127 and /128.
5. Click **Preview** to list every free prefix of that size in the parent, with the strategy's picks ticked. Tick or untick any of them, then click **Create selected**. Click **Preview** again after changing ticks to refresh the `{n}` numbering.

New prefixes inherit the parent's VRF and scope (site, location, region and so on). All prefixes are created in one transaction: either all of them are created or none are.

Before creating, the wizard locks the parent prefix and checks that every ticked prefix is still free. If one was taken since your preview, nothing is created and the updated list is shown. Changing the parent, size, count or strategy after a preview also requires a new preview before creating.

Changes appear in the NetBox change log under your user, the same as any UI edit.

## Where to find it

- **Plugins → Subnet Wizard** in the navigation menu.
- A **Subnet Wizard** button on any prefix with status Container. It opens the wizard with that prefix pre-selected.

## Requirements

- NetBox 4.6.x
- Python 3.12+

## Installation (no internet on the NetBox server)

The plugin ships as a single file, a Python wheel, attached to each [GitHub release](../../releases). It has no dependencies beyond NetBox itself, so the server never needs to reach GitHub or PyPI.

1. **On your Windows PC**, open the [latest release](../../releases/latest) and download `netbox_subnet_wizard-<version>-py3-none-any.whl`.

2. **Copy the file to the NetBox server.** Either drag and drop it into `/tmp` with WinSCP, or use the `scp` built into PowerShell:

   ```powershell
   scp $HOME\Downloads\netbox_subnet_wizard-0.1.0-py3-none-any.whl you@netbox-server:/tmp/
   ```

3. **On the server**, keep the wheel somewhere permanent and install it into NetBox's virtual environment. `--no-index` stops pip from trying to reach the internet:

   ```bash
   sudo mkdir -p /opt/netbox/plugins
   sudo mv /tmp/netbox_subnet_wizard-0.1.0-py3-none-any.whl /opt/netbox/plugins/
   sudo /opt/netbox/venv/bin/pip install --no-index /opt/netbox/plugins/netbox_subnet_wizard-0.1.0-py3-none-any.whl
   ```

   So that NetBox's `upgrade.sh` reinstalls the plugin after a NetBox upgrade, add the same path to `local_requirements.txt`:

   ```bash
   echo /opt/netbox/plugins/netbox_subnet_wizard-0.1.0-py3-none-any.whl | sudo tee -a /opt/netbox/local_requirements.txt
   ```

4. **Enable the plugin** in `/opt/netbox/netbox/netbox/configuration.py`:

   ```python
   PLUGINS = ['netbox_subnet_wizard']

   PLUGINS_CONFIG = {
       'netbox_subnet_wizard': {
           'max_subnets': 64,          # most prefixes created in one run
           'max_preview': 256,         # most free prefixes listed in the preview
           'default_status': 'active', # pre-selected status for new prefixes
           'gateway_status': 'active',
           'gateway_description': 'Gateway',
       },
   }
   ```

   If `PLUGINS` already lists other plugins, add `'netbox_subnet_wizard'` to that list rather than adding a second `PLUGINS` line.

5. **Restart NetBox:**

   ```bash
   sudo /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py collectstatic --no-input
   sudo systemctl restart netbox netbox-rq
   ```

   The plugin has no models, so there are no migrations.

6. **Check it loaded:** **Admin → System → Plugins** should list *Subnet Wizard*, and container prefixes get a **Subnet Wizard** button.

### Upgrading

Download the new wheel and repeat steps 2–3 with the new file name, then update the path in `local_requirements.txt` and restart NetBox (step 5).

### Uninstalling

Remove `'netbox_subnet_wizard'` from `PLUGINS`, then run `sudo /opt/netbox/venv/bin/pip uninstall netbox-subnet-wizard`, remove its line from `local_requirements.txt` and restart NetBox.

### Building the wheel yourself

```bash
pip install build
python -m build --wheel   # writes dist/netbox_subnet_wizard-<version>-py3-none-any.whl
```

## Trying it locally

`demo/` has a throwaway NetBox in Docker with the plugin installed and sample prefixes. See [demo/README.md](demo/README.md).

## Permissions

- To use the wizard, users need `ipam.add_prefix`.
- To use the gateway option, users also need `ipam.add_ipaddress`.
- The parent picker only lists prefixes the user can view.
- NetBox object-level permission constraints are enforced. If a constraint would block any of the new objects, the whole run is rolled back.

## Notes and limitations

- Only prefixes are allocated. VLANs, IP ranges and DNS records are not created.
- Free space comes from NetBox's own `get_available_prefixes()`. If the parent is marked as fully utilised, it has no free space.
- The gateway IP has no role set (NetBox has no "gateway" role). Its status and description come from `PLUGINS_CONFIG`.

## Structure

```
demo/                    # Docker NetBox for local testing
netbox_subnet_wizard/
├── __init__.py          # PluginConfig and default settings
├── forms.py             # Wizard form and validation
├── navigation.py        # Plugins menu entry
├── template_content.py  # Button on container prefix pages
├── urls.py
├── utils.py             # Allocation logic (first fit / best fit, gateway)
├── views.py             # Preview and transactional create
└── templates/netbox_subnet_wizard/wizard.html
```
