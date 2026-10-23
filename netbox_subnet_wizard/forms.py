import netaddr
from django import forms
from django.utils.translation import gettext_lazy as _

from ipam.choices import PrefixStatusChoices
from ipam.models import Prefix, Role
from netbox.plugins import get_plugin_config
from tenancy.models import Tenant
from utilities.forms.fields import DynamicModelChoiceField

from .utils import STRATEGY_BEST_FIT, STRATEGY_FIRST_FIT

PLUGIN = 'netbox_subnet_wizard'


class SubnetWizardForm(forms.Form):
    parent = DynamicModelChoiceField(
        queryset=Prefix.objects.all(),
        label=_('Parent prefix'),
        query_params={'status': 'container'},
        help_text=_('Container prefix to allocate from'),
    )
    prefix_length = forms.IntegerField(
        label=_('Prefix length'),
        min_value=1,
        max_value=128,
        help_text=_('Size of each new prefix, e.g. 24 for a /24'),
    )
    count = forms.IntegerField(
        label=_('Number of prefixes'),
        min_value=1,
        initial=1,
        help_text=_('How many to pre-select in the preview; you can change the selection there'),
    )
    strategy = forms.ChoiceField(
        label=_('Allocation strategy'),
        choices=(
            (STRATEGY_FIRST_FIT, _('First fit (lowest addresses first)')),
            (STRATEGY_BEST_FIT, _('Best fit (use smallest free gaps first)')),
        ),
        initial=STRATEGY_FIRST_FIT,
    )
    status = forms.ChoiceField(
        label=_('Status'),
        choices=PrefixStatusChoices,
    )
    role = DynamicModelChoiceField(
        queryset=Role.objects.all(),
        required=False,
        label=_('Role'),
    )
    tenant = DynamicModelChoiceField(
        queryset=Tenant.objects.all(),
        required=False,
        label=_('Tenant'),
    )
    description = forms.CharField(
        max_length=200,
        required=False,
        label=_('Description'),
        help_text=_('Use {n} for a sequence number and {prefix} for the prefix itself'),
    )
    create_gateway = forms.BooleanField(
        required=False,
        label=_('Create gateway IP'),
        help_text=_('Add the first usable address of each prefix as an IP address'),
    )
    # Settings the current preview was built from (see SubnetWizardView.preview_key);
    # a mismatch means the ticked prefixes no longer match the form.
    previewed = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields['status'].initial = get_plugin_config(PLUGIN, 'default_status')
        if user is not None:
            self.fields['parent'].queryset = Prefix.objects.restrict(user, 'view')

    def clean(self):
        cleaned = super().clean()
        parent = cleaned.get('parent')
        length = cleaned.get('prefix_length')
        count = cleaned.get('count')

        if parent and length:
            max_len = 32 if parent.family == 4 else 128
            if length <= parent.prefix.prefixlen:
                self.add_error('prefix_length', _(
                    'Must be longer than the parent prefix (/{len}).'
                ).format(len=parent.prefix.prefixlen))
            elif length > max_len:
                self.add_error('prefix_length', _(
                    'Must be /{max} or shorter for IPv{family}.'
                ).format(max=max_len, family=parent.family))

        max_subnets = get_plugin_config(PLUGIN, 'max_subnets')
        if count and count > max_subnets:
            self.add_error('count', _(
                'You can create at most {max} prefixes at once.'
            ).format(max=max_subnets))

        if cleaned.get('create_gateway') and self.user is not None \
                and not self.user.has_perm('ipam.add_ipaddress'):
            self.add_error('create_gateway', _(
                'You do not have permission to create IP addresses.'
            ))

        # Prefixes ticked in the preview, rendered as plain checkboxes named "selected"
        selected = set()
        raw = self.data.getlist('selected') if hasattr(self.data, 'getlist') else []
        for value in raw:
            try:
                net = netaddr.IPNetwork(value)
            except (netaddr.AddrFormatError, ValueError, TypeError):
                self.add_error(None, _('{value} is not a valid prefix.').format(value=value))
                continue
            if parent and length and (
                net.prefixlen != length or net.ip != net.network or net not in parent.prefix
            ):
                self.add_error(None, _(
                    '{value} is not a /{len} inside {parent}.'
                ).format(value=value, len=length, parent=parent.prefix))
                continue
            selected.add(net)
        cleaned['selected'] = sorted(selected)

        return cleaned
