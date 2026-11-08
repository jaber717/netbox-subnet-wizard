# Loaded by the netbox-docker image alongside its own configuration.py
PLUGINS = ['netbox_subnet_wizard']

PLUGINS_CONFIG = {
    'netbox_subnet_wizard': {
        'max_subnets': 256,
        'default_status': 'reserved',
        'global_parent_policy': 'all_vrfs',
    },
}
