import netaddr
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.models import ObjectChange, ObjectType
from dcim.models import Site
from ipam.models import Prefix, Role
from users.models import ObjectPermission, User

PLUGIN = 'netbox_subnet_wizard'


def plugin_settings(**overrides):
    """override_settings that keeps the plugin's other defaults."""
    merged = {**settings.PLUGINS_CONFIG.get(PLUGIN, {}), **overrides}
    return override_settings(PLUGINS_CONFIG={**settings.PLUGINS_CONFIG, PLUGIN: merged})


def allocate_url(parent):
    return reverse('plugins:netbox_subnet_wizard:allocate', kwargs={'pk': parent.pk})


def confirm_url(parent):
    return reverse('plugins:netbox_subnet_wizard:confirm', kwargs={'pk': parent.pk})


def grant(user, model, actions, constraints=None, name=None):
    p = ObjectPermission.objects.create(
        name=name or f'{model._meta.model_name}-{"-".join(actions)}-{ObjectPermission.objects.count()}',
        actions=actions, constraints=constraints,
    )
    p.object_types.add(ObjectType.objects.get_for_model(model))
    p.users.add(user)
    return p


class WizardTestMixin:
    """Drives the real UI flow: preview -> select -> review -> confirm."""

    def form_data(self, length, action='preview', selected=(), **extra):
        data = {'prefix_length': str(length), 'status': extra.pop('status', 'reserved'),
                'strategy': 'first_fit', 'action': action, 'shown_length': str(length),
                'selected': [str(s) for s in selected]}
        data.update(extra)
        return data

    def preview(self, parent, length, action='preview', client=None, **kw):
        return (client or self.client).post(allocate_url(parent), self.form_data(length, action, **kw))

    def review(self, parent, length, selected, client=None, **kw):
        r = (client or self.client).post(allocate_url(parent), self.form_data(length, 'review', selected, **kw))
        return r, (r.context.get('token') if r.context else None)

    def confirm(self, parent, token, client=None, **kw):
        data = {'token': token, 'action': 'confirm'}
        data.update(kw)
        return (client or self.client).post(confirm_url(parent), data)

    def run_full(self, parent, length, selected, client=None, typed=True, confirm=None, **kw):
        r, token = self.review(parent, length, selected, client=client, **kw)
        assert token, getattr(r, 'content', b'')[:3000]
        extra = {'typed_count': str(len(selected))} if typed else {}
        extra.update(confirm or {})
        return self.confirm(parent, token, client=client, **extra)

    def children(self, parent, **kw):
        return Prefix.objects.filter(prefix__net_contained=str(parent.prefix), **kw)

    @staticmethod
    def nets(cidr, length, n=None):
        s = list(netaddr.IPNetwork(cidr).subnet(length))
        return s[:n] if n else s


class WizardTestCase(WizardTestMixin, TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user('admin', password='x', is_superuser=True)
        cls.site = Site.objects.create(name='DC1', slug='dc1')
        cls.role = Role.objects.create(name='WAN P2P', slug='wan-p2p')
        cls.parent = Prefix.objects.create(prefix='192.168.240.0/24', status='container',
                                           scope=cls.site, role=cls.role)

    def setUp(self):
        self.client = Client(raise_request_exception=False)
        self.client.force_login(self.admin)

    def prefix_changes(self, action='create'):
        return ObjectChange.objects.filter(changed_object_type=ContentType.objects.get_for_model(Prefix),
                                           action=action)
