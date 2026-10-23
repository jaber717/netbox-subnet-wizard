from django.urls import reverse
from django.utils.html import format_html

from netbox.plugins import PluginTemplateExtension


class PrefixWizardButton(PluginTemplateExtension):
    """Adds a 'Subnet Wizard' button to container prefix pages."""
    models = ['ipam.prefix']

    def buttons(self):
        obj = self.context['object']
        user = self.context['request'].user
        if obj.status != 'container' or not user.has_perm('ipam.add_prefix'):
            return ''
        url = reverse('plugins:netbox_subnet_wizard:wizard')
        return format_html(
            '<a href="{}?parent={}" class="btn btn-primary">'
            '<i class="mdi mdi-auto-fix"></i> Subnet Wizard</a>',
            url, obj.pk,
        )


template_extensions = [PrefixWizardButton]
