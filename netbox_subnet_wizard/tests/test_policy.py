"""Global-vs-VRF space policy, conflicts with existing IPs/ranges, gateway handling, permissions."""
import netaddr

from ipam.models import VRF, IPAddress, IPRange, Prefix
from tenancy.models import Tenant
from users.models import User

from .base import WizardTestCase, allocate_url, grant, plugin_settings


class VrfPolicyTests(WizardTestCase):

    def test_global_umbrella_counts_vrf_children_by_default(self):
        vrf = VRF.objects.create(name='WAN')
        Prefix.objects.create(prefix='192.168.240.0/26', vrf=vrf, status='active')
        r = self.client.get(allocate_url(self.parent))
        self.assertContains(r, 'prefixes in any VRF occupy space')
        self.assertContains(r, '/30 — 64 blocks, 48 free')
        r, token = self.review(self.parent, 30, self.nets('192.168.240.0/24', 30, 1))
        self.assertIsNone(token)
        self.assertContains(r, 'No longer available')

    @plugin_settings(global_parent_policy='same_vrf')
    def test_global_same_vrf_policy_allows_overlap_with_vrf_children(self):
        vrf = VRF.objects.create(name='WAN')
        Prefix.objects.create(prefix='192.168.240.0/26', vrf=vrf, status='active')
        r = self.run_full(self.parent, 30, self.nets('192.168.240.0/24', 30, 1), typed=False)
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Prefix.objects.filter(prefix='192.168.240.0/30', vrf=None).exists())

    def test_vrf_parent_ignores_identical_space_in_other_vrfs_and_global(self):
        a, b = VRF.objects.create(name='A'), VRF.objects.create(name='B')
        parent_a = Prefix.objects.create(prefix='10.0.0.0/24', vrf=a, status='container')
        Prefix.objects.create(prefix='10.0.0.0/30', vrf=b, status='active')
        Prefix.objects.create(prefix='10.0.0.4/30', vrf=None, status='active')
        IPAddress.objects.create(address=netaddr.IPNetwork('10.0.0.9/24'), vrf=b)
        r = self.preview(parent_a, 30, action='select_all')
        self.assertEqual(r.context['preview']['selected_count'], 64)
        self.assertEqual(r.context['preview']['conflict_count'], 0)
        r = self.run_full(parent_a, 30, self.nets('10.0.0.0/24', 30, 3), typed=False)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Prefix.objects.filter(vrf=a, prefix__net_contained='10.0.0.0/24').count(), 3)

    def test_children_inherit_parent_vrf(self):
        vrf = VRF.objects.create(name='DMZ')
        parent = Prefix.objects.create(prefix='172.16.0.0/24', vrf=vrf, status='container')
        self.run_full(parent, 28, self.nets('172.16.0.0/24', 28, 2), typed=False)
        self.assertEqual(set(Prefix.objects.filter(prefix__net_contained='172.16.0.0/24')
                             .values_list('vrf__name', flat=True)), {'DMZ'})


class ConflictTests(WizardTestCase):

    def setUp(self):
        super().setUp()
        IPAddress.objects.create(address=netaddr.IPNetwork('192.168.240.1/24'), description='legacy router')
        IPRange.objects.create(start_address=netaddr.IPNetwork('192.168.240.9/24'),
                               end_address=netaddr.IPNetwork('192.168.240.14/24'))

    def test_conflicts_flagged_and_excluded_from_select_all(self):
        r = self.preview(self.parent, 30, action='select_all')
        p = r.context['preview']
        self.assertEqual(p['conflict_count'], 3)   # .0/30, .8/30, .12/30
        self.assertEqual(p['selected_count'], 61)
        self.assertContains(r, 'IP 192.168.240.1 (legacy router)')
        self.assertContains(r, 'IP range 192.168.240.9-192.168.240.14')

    def test_conflicting_selection_requires_acknowledgement(self):
        sel = [netaddr.IPNetwork('192.168.240.0/30'), netaddr.IPNetwork('192.168.240.16/30')]
        r, token = self.review(self.parent, 30, sel)
        self.assertTrue(r.context['has_conflicts'])
        self.assertEqual(self.confirm(self.parent, token).status_code, 400)
        self.assertFalse(self.children(self.parent).exists())
        self.assertEqual(self.confirm(self.parent, token, acknowledge_conflicts='on').status_code, 302)
        self.assertEqual(self.children(self.parent).count(), 2)


class GatewayTests(WizardTestCase):

    def test_gateway_created_for_29(self):
        r = self.run_full(self.parent, 29, self.nets('192.168.240.0/24', 29, 2), typed=False, create_gateway='on')
        self.assertEqual(r.status_code, 302)
        self.assertEqual(sorted(str(a.address) for a in IPAddress.objects.all()),
                         ['192.168.240.1/29', '192.168.240.9/29'])

    def test_gateway_ignored_for_p2p_30(self):
        r = self.run_full(self.parent, 30, self.nets('192.168.240.0/24', 30, 2), typed=False, create_gateway='on')
        self.assertEqual(r.status_code, 302)
        self.assertFalse(IPAddress.objects.exists())

    def test_duplicate_gateway_refused_even_without_enforce_unique(self):
        vrf = VRF.objects.create(name='LAB', enforce_unique=False)
        parent = Prefix.objects.create(prefix='10.50.0.0/24', vrf=vrf, status='container')
        IPAddress.objects.create(address=netaddr.IPNetwork('10.50.0.9/32'), vrf=vrf)  # gateway of .8/29, other mask
        _, token = self.review(parent, 29, self.nets('10.50.0.0/24', 29, 2), create_gateway='on')
        r = self.confirm(parent, token, acknowledge_conflicts='on')
        self.assertEqual(r.status_code, 409)
        self.assertContains(r, 'Gateway 10.50.0.9 already exists', status_code=409)
        self.assertFalse(Prefix.objects.filter(prefix__net_contained='10.50.0.0/24').exists())
        self.assertEqual(IPAddress.objects.count(), 1)


class PermissionTests(WizardTestCase):

    def test_no_add_permission_no_access(self):
        user = User.objects.create_user('viewer')
        grant(user, Prefix, ['view'])
        self.client.force_login(user)
        self.assertEqual(self.client.get(allocate_url(self.parent)).status_code, 403)

    def test_object_constraint_denial_rolls_back_with_message(self):
        user = User.objects.create_user('ops')
        good, bad = Tenant.objects.create(name='Good', slug='good'), Tenant.objects.create(name='Bad', slug='bad')
        grant(user, Prefix, ['view'])
        grant(user, Prefix, ['add'], constraints={'tenant__slug': 'good'})
        grant(user, Tenant, ['view'])
        self.client.force_login(user)
        _, token = self.review(self.parent, 30, self.nets('192.168.240.0/24', 30, 2), tenant=str(bad.pk))
        r = self.confirm(self.parent, token)
        self.assertEqual(r.status_code, 409)
        self.assertContains(r, 'permission constraint', status_code=409)
        self.assertFalse(self.children(self.parent).exists())
        _, token = self.review(self.parent, 30, self.nets('192.168.240.0/24', 30, 2), tenant=str(good.pk))
        self.assertEqual(self.confirm(self.parent, token).status_code, 302)

    def test_parent_not_viewable_is_404(self):
        user = User.objects.create_user('scoped')
        grant(user, Prefix, ['view', 'add'], constraints={'prefix': '10.0.0.0/8'})
        self.client.force_login(user)
        self.assertEqual(self.client.get(allocate_url(self.parent)).status_code, 404)
