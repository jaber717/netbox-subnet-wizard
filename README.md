# NetBox Subnet Wizard

phpIPAM-style subnetting for NetBox 4.6: open a block, see what is free, split it into equal
child prefixes, tick the ones you want, review the exact list, confirm. Blocks can be reserved for
the future and split again later.

> **0.2.0rc1 is a release candidate.** Do not deploy to production until it has passed your own
> acceptance on a copy of production data (see [Testing](#testing)).

## Workflow

1. Open any **Container** or **Reserved** prefix and click **Split / Reserve**
   (or *Plugins → Split / Reserve Prefix*).
2. The left side shows **what is available**: free addresses, free blocks, and how many child
   prefixes already exist per status (each links to the filtered prefix list).
3. **Split into**: a dropdown of every valid size with live counts, e.g.
   `/30 — 64 blocks, 60 free`, `/31 — 128 blocks, 120 free`.
4. Choose status (default **Reserved**), role and tenant (pre-filled from the parent), and an
   optional description template (`WAN-P2P-{n}`, `{prefix}`).
5. **Show free blocks** lists every free block with a checkbox. **Select all free**, **Clear**,
   or *Quick-select N* (lowest first, or smallest gaps first to keep big blocks intact).
6. **Review selected** shows exactly what will be written. Selections of 16 or more require
   typing the count. Blocks that contain existing IPs or IP ranges are flagged and need an
   explicit acknowledgement.
7. **Create** writes everything in one transaction and returns to the parent's child prefixes.

### Planning ahead

Split a /22 into four /24s with status **Reserved** and roles *WAN*, *DMZ*, *Inner*, *Public*.
Later, open the reserved DMZ /24 and split it into /28s. The confirm page offers to change that
/24 from Reserved to **Container** in the same transaction (default on, logged in the change log).

## What changed from 0.1.0

See [CHANGELOG.md](CHANGELOG.md). Short version: no more HTTP 500 on model validation errors,
no webhooks for rolled-back objects, the core allocator lock is honoured, server-side parent
checks, duplicate-gateway protection, a signed review step, and the new UI.

## Safety model

**All-or-nothing.** Prefixes, gateway IPs and the optional parent conversion are one
transaction. On any refusal (validation, permission constraint, concurrent change) nothing is
written and queued events are discarded (`core.signals.clear_events`), so no webhook or event
rule fires for objects that never existed. Verified with a real webhook receiver in
`tests/test_events.py`.

**Signed review.** The confirm step accepts only a plan signed by the server
(`django.core.signing`, bound to user, parent and selection, valid 15 minutes). Hand-crafted or
replayed POSTs cannot skip the review; availability is still re-checked under lock at confirm.

**Parent checks (server side).** Only statuses in `allowed_parent_statuses`; parents marked
*fully utilised* are refused (NetBox's own `get_available_prefixes()` ignores that flag).

**Permissions.** `ipam.add_prefix` to use the wizard, `ipam.add_ipaddress` for gateways,
`ipam.change_prefix` on the parent to convert it. Object-level constraints are enforced; a
violation rolls back the whole run and is shown as a message.

### Global vs VRF space (`global_parent_policy`)

| Parent | Counts as occupied | Overlap with other VRFs |
|---|---|---|
| In a VRF | prefixes in **that VRF only** | always allowed (isolated) |
| Global, `all_vrfs` (default) | prefixes in **any VRF** (umbrella, matches NetBox's child list) | prevented |
| Global, `same_vrf` | global prefixes only (matches `get_available_prefixes()`) | allowed |

New prefixes always inherit the parent's VRF and scope.

### Concurrency

| Other writer | Result |
|---|---|
| Another wizard run | serialised (advisory lock + parent row lock) |
| `POST /api/ipam/prefixes/<id>/available-prefixes/` | serialised: same advisory lock `100100` |
| Native create already **committed** before the wizard finishes | detected: duplicate check or pre-commit overlap check → clean refusal, nothing written |
| Native create (UI form, `POST /api/ipam/prefixes/`) **in flight at the same moment, same VRF, same prefix** | **not prevented** — see below |

**Residual risk.** NetBox has no database constraint against duplicate prefixes. A native
create in the same VRF whose transaction overlaps a wizard run checks for duplicates before
either side has committed, then commits right after the wizard: a duplicate results, even with
`enforce_unique`. NetBox core has the same blind spot between two native writers. The window
is the duration of a wizard run (about 2 s for 128 prefixes on the test system). The wizard
page lists exact duplicates in the block on every visit, and a post-commit check reports what
it can see. Eliminating it requires a unique index on `ipam_prefix`, which this plugin does not
add. Operational mitigation: allocate P2P and planned space only through the wizard or the
`available-prefixes` API. Pinned by
`test_residual_race_with_native_rest_create_is_detected_not_prevented`.

## Configuration

```python
PLUGINS_CONFIG = {
    'netbox_subnet_wizard': {
        'max_subnets': 256,                 # per run; /24 -> /31 needs 128
        'max_preview': 512,                 # rows in the preview table
        'mass_confirm_threshold': 16,       # type the count at or above this size
        'default_status': 'reserved',
        'allowed_parent_statuses': ['container', 'reserved'],
        'allow_mark_utilized_parent': False,
        'global_parent_policy': 'all_vrfs', # or 'same_vrf'
        'gateway_max_length_v4': 29,        # no gateway for /30, /31, /32
        'gateway_max_length_v6': 125,
        'gateway_status': 'active',
        'gateway_description': 'Gateway',
        'plan_max_age': 900,                # seconds a review stays valid
    },
}
```

## Installation from GitHub (on the NetBox server)

No runtime dependencies beyond NetBox. Pin a tag; never install a moving branch.

Python 3.12 venvs (NetBox's included) do not contain `setuptools`, which builds this package.
Pick one:

```bash
# A) Let pip fetch the build backend through your proxy (PyPI must be reachable):
sudo /opt/netbox/venv/bin/pip install \
  "git+https://github.com/<your-org>/netbox-subnet-wizard.git@v0.2.0rc1"

# B) No PyPI at install time: add the build backend once, then build without isolation.
sudo /opt/netbox/venv/bin/pip install "setuptools>=68" wheel      # once, via proxy or a mirror
sudo /opt/netbox/venv/bin/pip install --no-build-isolation \
  "git+https://github.com/<your-org>/netbox-subnet-wizard.git@v0.2.0rc1"
```

Record the same `git+https://...@v0.2.0rc1` line in `/opt/netbox/local_requirements.txt` so
`upgrade.sh` reinstalls it. Behind an SSL-inspecting proxy, point git and pip at the corporate CA
(`git config --global http.sslCAInfo <ca.pem>`, `PIP_CERT=<ca.pem>`) and export `https_proxy`.

Add `'netbox_subnet_wizard'` to `PLUGINS`, then:

```bash
sudo /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py collectstatic --no-input
sudo systemctl restart netbox netbox-rq
```

No models, no migrations. Uninstall: remove from `PLUGINS`, `pip uninstall netbox-subnet-wizard`.

## Testing

Tests live in `netbox_subnet_wizard/tests/` (excluded from the wheel) and need a NetBox 4.6
checkout with PostgreSQL and Redis:

```bash
cd /path/to/netbox/netbox
pip install -e /path/to/netbox-subnet-wizard        # editable, so tests are importable
python manage.py test netbox_subnet_wizard -v 2
```

Coverage: UI workflow, persistence and change log for /24→64×/30 and /24→128×/31, signed-plan
tampering/replay/expiry, parent validation, VRF policy, IP/range conflicts, gateways,
permissions, event queue, **real webhook delivery** through an RQ worker to an HTTP receiver,
and concurrency with separate PostgreSQL connections (core API lock in both directions,
committed native writer, and the residual race).

## Limitations

- Prefixes only (and optional gateway IPs). No VLANs, IP ranges or DNS.
- New prefixes inherit the parent VRF; no per-run VRF override.
- No merge of adjacent prefixes.
- Preview shows at most `max_preview` rows; *Select all free* selects those rows only.
