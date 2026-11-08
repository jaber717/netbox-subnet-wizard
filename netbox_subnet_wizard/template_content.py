from django.urls import reverse
from django.utils.html import format_html

from netbox.plugins import PluginTemplateExtension

from .utils import cfg


class PrefixWizardButton(PluginTemplateExtension):
    """Adds a 'Split / Reserve' button to prefixes the wizard is allowed to split."""
    models = ['ipam.prefix']

    def buttons(self):
        obj = self.context['object']
        user = self.context['request'].user
        if obj.status not in cfg('allowed_parent_statuses') or not user.has_perm('ipam.add_prefix'):
            return ''
        return format_html(
            '<a href="{}" class="btn btn-primary">'
            '<i class="mdi mdi-call-split"></i> Split / Reserve</a>',
            reverse('plugins:netbox_subnet_wizard:allocate', kwargs={'pk': obj.pk}),
        )


template_extensions = [PrefixWizardButton]
