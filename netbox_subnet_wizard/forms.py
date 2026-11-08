import netaddr
from django import forms
from django.utils.translation import gettext_lazy as _

from ipam.choices import PrefixStatusChoices
from ipam.models import Prefix, Role
from tenancy.models import Tenant
from utilities.forms.fields import DynamicModelChoiceField

from . import utils
from .utils import STRATEGY_BEST_FIT, STRATEGY_FIRST_FIT, cfg


class ParentPickForm(forms.Form):
    parent = DynamicModelChoiceField(
        queryset=Prefix.objects.all(),
        label=_('Parent prefix'),
        help_text=_('The block to split: a container or reserved prefix.'),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None:
            self.fields['parent'].queryset = Prefix.objects.restrict(user, 'view')


def default_length(parent):
    plen = parent.prefix.prefixlen
    if parent.family == 4:
        return 30 if plen >= 22 else min(plen + 8, 32)
    return 64 if plen < 64 else min(plen + 8, 128)


class AllocateForm(forms.Form):
    """Settings for one split of one parent. The parent comes from the URL, never the form."""
    prefix_length = forms.TypedChoiceField(
        label=_('Split into'), coerce=int,
        help_text=_('Size of each new prefix. Counts are for this parent.'),
    )
    status = forms.ChoiceField(
        label=_('Status'), choices=PrefixStatusChoices,
        help_text=_('Reserved = planned for the future, not yet in service.'),
    )
    role = DynamicModelChoiceField(queryset=Role.objects.all(), required=False, label=_('Role'))
    tenant = DynamicModelChoiceField(queryset=Tenant.objects.all(), required=False, label=_('Tenant'))
    description = forms.CharField(
        max_length=200, required=False, label=_('Description'),
        help_text=_('{n} = sequence number, {prefix} = the prefix. Example: WAN-P2P-{n}'),
    )
    create_gateway = forms.BooleanField(
        required=False, label=_('Create gateway IP'),
        help_text=_('First usable address of each prefix. Ignored for P2P sizes (/30, /31, /126, /127).'),
    )
    count = forms.IntegerField(
        required=False, min_value=1, label=_('Quick-select'),
        help_text=_('Optional: tick this many free blocks automatically when you preview.'),
    )
    strategy = forms.ChoiceField(
        label=_('Quick-select order'),
        choices=(
            (STRATEGY_FIRST_FIT, _('Lowest addresses first')),
            (STRATEGY_BEST_FIT, _('Smallest free gaps first (keeps big blocks intact)')),
        ),
        initial=STRATEGY_FIRST_FIT,
    )
    # Length the visible table was built for; ticks are dropped when the length changes.
    shown_length = forms.IntegerField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, parent, available, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.parent = parent
        self.available = available
        self.user = user
        self.fields['prefix_length'].choices = utils.length_choices(parent, available)
        self.fields['prefix_length'].initial = str(default_length(parent))
        self.fields['status'].initial = cfg('default_status')
        self.fields['role'].initial = parent.role_id
        self.fields['tenant'].initial = parent.tenant_id

    def clean(self):
        cleaned = super().clean()
        length = cleaned.get('prefix_length')
        if cleaned.get('count') and cleaned['count'] > cfg('max_subnets'):
            self.add_error('count', _('At most {n} prefixes can be created at once.').format(n=cfg('max_subnets')))
        if cleaned.get('create_gateway') and length and not utils.gateway_allowed(length, self.parent.family):
            cleaned['create_gateway'] = False
        if cleaned.get('create_gateway') and self.user is not None and not self.user.has_perm('ipam.add_ipaddress'):
            self.add_error('create_gateway', _('You do not have permission to create IP addresses.'))

        selected, bad = set(), []
        raw = self.data.getlist('selected') if hasattr(self.data, 'getlist') else []
        shown = cleaned.get('shown_length')
        cleaned['length_changed'] = bool(shown and length and shown != length)
        if cleaned['length_changed']:
            raw = []    # ticks belong to the previous size; start fresh
        for value in raw:
            try:
                net = netaddr.IPNetwork(value)
            except (netaddr.AddrFormatError, ValueError, TypeError):
                bad.append(value)
                continue
            if length and (net.prefixlen != length or net.ip != net.network or net not in self.parent.prefix):
                bad.append(value)
                continue
            selected.add(net)
        if bad:
            self.add_error(None, _('Invalid selections: {v}').format(v=', '.join(str(b) for b in bad[:5])))
        cleaned['selected'] = sorted(selected)
        return cleaned
