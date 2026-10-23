from netbox.plugins import PluginConfig


class SubnetWizardConfig(PluginConfig):
    name = 'netbox_subnet_wizard'
    verbose_name = 'Subnet Wizard'
    description = 'Guided allocation of child prefixes from a parent container prefix'
    version = '0.1.0'
    author = 'Networks Team'
    base_url = 'subnet-wizard'
    min_version = '4.6.0'
    max_version = '4.6.99'
    default_settings = {
        # Upper limit on how many prefixes can be created in one run
        'max_subnets': 64,
        # Upper limit on how many available prefixes the preview lists
        'max_preview': 256,
        # Status pre-selected for new prefixes
        'default_status': 'active',
        # Status and description used for the optional gateway IP
        'gateway_status': 'active',
        'gateway_description': 'Gateway',
    }


config = SubnetWizardConfig
