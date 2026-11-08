from netbox.plugins import PluginConfig

__version__ = '0.2.0rc1'


class SubnetWizardConfig(PluginConfig):
    name = 'netbox_subnet_wizard'
    verbose_name = 'Subnet Wizard'
    description = 'phpIPAM-style splitting and reservation of child prefixes inside a parent prefix'
    version = __version__
    author = 'Networks Team'
    base_url = 'subnet-wizard'
    min_version = '4.6.0'
    max_version = '4.6.99'
    default_settings = {
        # Most prefixes created in one run. 256 covers a /24 -> /31 (128) and a /23 -> /31.
        'max_subnets': 256,
        # Most candidate prefixes listed in the preview.
        'max_preview': 512,
        # Selections of this size or larger must be confirmed by typing the count.
        'mass_confirm_threshold': 16,
        # Status pre-selected for new prefixes. "reserved" = planned, not yet in service.
        'default_status': 'reserved',
        # Parent statuses the wizard may split. A "reserved" block can be split later and
        # optionally converted to a container in the same transaction.
        'allowed_parent_statuses': ['container', 'reserved'],
        # Refuse parents marked as fully utilised (NetBox's own available-prefix logic ignores the flag).
        'allow_mark_utilized_parent': False,
        # How a parent in the GLOBAL table counts children that live in VRFs:
        #   'all_vrfs'  - a global container is an umbrella; any VRF child occupies its space
        #                 (matches Prefix.get_child_prefixes() and the NetBox child-prefix tab)
        #   'same_vrf'  - only global children occupy space (matches Prefix.get_available_prefixes())
        # A parent inside a VRF only ever looks at its own VRF, so overlapping space across
        # isolated VRFs is always allowed.
        'global_parent_policy': 'all_vrfs',
        # Gateway IPs are only offered for prefixes up to this length (P2P /30-/31 and
        # /126-/127 have endpoints, not a gateway).
        'gateway_max_length_v4': 29,
        'gateway_max_length_v6': 125,
        'gateway_status': 'active',
        'gateway_description': 'Gateway',
        # How long a reviewed plan stays valid before it must be reviewed again (seconds).
        'plan_max_age': 900,
    }


config = SubnetWizardConfig
