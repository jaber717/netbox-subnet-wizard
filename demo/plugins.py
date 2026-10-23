# Loaded by the netbox-docker image alongside its own configuration.py
PLUGINS = ['netbox_subnet_wizard']

PLUGINS_CONFIG = {
    'netbox_subnet_wizard': {
        'max_subnets': 64,
        'default_status': 'active',
        'gateway_status': 'active',
        'gateway_description': 'Gateway',
    },
}
