from netbox.plugins import PluginMenuItem

menu_items = (
    PluginMenuItem(
        link='plugins:netbox_subnet_wizard:wizard',
        link_text='Split / Reserve Prefix',
        permissions=['ipam.add_prefix'],
    ),
)
