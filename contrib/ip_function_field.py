"""
Creates/updates the "IP Function" custom field on IP addresses (select, with colours).

NetBox 4.6 does not allow extending the built-in IP *role* list (FIELD_CHOICES only covers
IPAddress.status), so the function of an address is recorded in this custom field instead.
Built-in roles (loopback, secondary, anycast, vip, vrrp, hsrp, glbp, carp) stay as they are.

Run through contrib/setup-ip-function.sh. Idempotent: re-running adds new choices and updates
labels/colours; it never deletes a value that IP addresses still use.
"""
from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from extras.models import CustomField, CustomFieldChoiceSet
from ipam.models import IPAddress

FIELD_NAME = 'ip_function'
CHOICES = [
    # value (stored, never change)  label (safe to change)  colour
    ('gateway',        'Gateway',        'orange'),
    ('p2p',            'P2P Link',       'teal'),
    ('nat-pool',       'NAT Pool',       'pink'),
    ('public-service', 'Public Service', 'red'),
    ('vpn-peer',       'VPN Peer',       'indigo'),
    ('tunnel',         'Tunnel',         'cyan'),
    ('ha-link',        'HA Link',        'gray'),
    ('mgmt',           'Management',     'blue'),
    ('oob',            'Out-of-Band',    'purple'),
    ('vtep',           'VTEP',           'yellow'),
]

def apply():
    wanted = [[v, label] for v, label, _ in CHOICES]
    colours = {v: c for v, _, c in CHOICES}

    cs = CustomFieldChoiceSet.objects.filter(name='IP Function').first()
    if cs is None:
        cs = CustomFieldChoiceSet(name='IP Function', description='Function of an IP address')
        existing = []
    else:
        existing = [list(c) for c in (cs.extra_choices or [])]

    # Keep values that are in use but no longer listed, so no IP is left with an invalid value
    in_use = set(
        IPAddress.objects.exclude(**{f'custom_field_data__{FIELD_NAME}__isnull': True})
        .values_list(f'custom_field_data__{FIELD_NAME}', flat=True)
    ) if CustomField.objects.filter(name=FIELD_NAME).exists() else set()
    wanted_values = {v for v, _ in wanted}
    kept = [c for c in existing if c[0] in in_use and c[0] not in wanted_values]

    cs.extra_choices = wanted + kept
    cs.choice_colors = {**colours, **{c[0]: cs.choice_colors.get(c[0], 'gray') for c in kept}} if cs.pk else colours
    cs.full_clean()
    cs.save()

    cf = CustomField.objects.filter(name=FIELD_NAME).first() or CustomField(name=FIELD_NAME)
    cf.type = 'select'
    cf.label = 'IP Function'
    cf.group_name = 'Addressing'
    cf.description = 'What this address is used for (gateway, P2P, NAT pool, ...)'
    cf.choice_set = cs
    cf.filter_logic = 'exact'
    cf.required = False
    cf.weight = 50
    cf.full_clean()
    cf.save()
    cf.object_types.add(ContentType.objects.get_for_model(IPAddress))

    print('IP Function field ready. Choices:', ', '.join(v for v, _ in cs.extra_choices))
    if kept:
        print('Kept (still used by IPs):', ', '.join(c[0] for c in kept))


with transaction.atomic():   # all or nothing
    apply()
