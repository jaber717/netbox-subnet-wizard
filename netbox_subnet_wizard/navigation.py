from netbox.plugins import PluginMenuItem

menu_items = (
    PluginMenuItem(
        link='plugins:netbox_subnet_wizard:wizard',
        link_text='Subnet Wizard',
        permissions=['ipam.add_prefix'],
    ),
)
