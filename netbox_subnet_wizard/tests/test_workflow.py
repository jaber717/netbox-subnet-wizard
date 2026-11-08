"""End-to-end UI workflow, persistence, change log, plan integrity, parent validation."""
from unittest import mock

import netaddr
from django.core import signing

from ipam.models import IPAddress, Prefix
from users.models import User

from ..allocator import PLAN_SALT
from .base import WizardTestCase, allocate_url, confirm_url, grant, plugin_settings


class SplitPageTests(WizardTestCase):

    def test_page_shows_free_space_and_length_dropdown_with_counts(self):
        Prefix.objects.create(prefix='192.168.240.0/26', status='active', scope=self.site)
        r = self.client.get(allocate_url(self.parent))
        self.assertEqual(r.status_code, 200)
        # dropdown, not a free-text number
        self.assertContains(r, '<select name="prefix_length"')
        self.assertContains(r, '/30 — 64 blocks, 48 free')
        self.assertContains(r, '/31 — 128 blocks, 96 free')
        self.assertContains(r, '/25 — 2 blocks, 1 free')
        # what is available
        self.assertContains(r, '192.168.240.64/26')
        self.assertContains(r, '192.168.240.128/25')
        self.assertContains(r, '192 addresses free')
        # defaults: /30 for a /24, status reserved, role inherited
        html = r.content.decode()
        self.assertRegex(html, r'<option value="30"\s+selected')
        self.assertRegex(html, r'<option value="reserved"\s+selected')

    def test_split_button_on_container_and_reserved_only(self):
        reserved = Prefix.objects.create(prefix='10.1.0.0/24', status='reserved')
        active = Prefix.objects.create(prefix='10.2.0.0/24', status='active')
        for obj, shown in ((self.parent, True), (reserved, True), (active, False)):
            r = self.client.get(obj.get_absolute_url())
            (self.assertContains if shown else self.assertNotContains)(r, allocate_url(obj))

    def test_select_all_ticks_every_free_block_and_skips_existing(self):
        Prefix.objects.create(prefix='192.168.240.0/30', status='active', scope=self.site)
        r = self.preview(self.parent, 30, action='select_all')
        self.assertEqual(r.context['preview']['selected_count'], 63)
        self.assertNotContains(r, 'value="192.168.240.0/30"')
        r = self.preview(self.parent, 30, action='clear')
        self.assertEqual(r.context['preview']['selected_count'], 0)

    def test_quick_select_count_and_length_change_resets_ticks(self):
        r = self.preview(self.parent, 30, count='3')
        self.assertEqual(r.context['preview']['selected_count'], 3)
        r = self.preview(self.parent, 29, selected=self.nets('192.168.240.0/24', 30, 3), shown_length='30')
        self.assertEqual(r.context['preview']['selected_count'], 0)
        self.assertEqual(r.context['preview']['length'], 29)

    def test_preview_never_writes(self):
        for action in ('preview', 'select_all', 'clear', 'review', 'create'):
            self.preview(self.parent, 30, action=action, selected=self.nets('192.168.240.0/24', 30, 2))
        self.assertFalse(self.children(self.parent).exists())


class PersistenceTests(WizardTestCase):

    def test_24_into_64_x_30_persists_with_changelog(self):
        nets = self.nets('192.168.240.0/24', 30)
        r = self.run_full(self.parent, 30, nets, role=str(self.role.pk), description='WAN-P2P-{n}')
        self.assertEqual(r.status_code, 302, r.content[:2000])
        kids = self.children(self.parent)
        self.assertEqual(kids.count(), 64)
        self.assertEqual(set(kids.values_list('status', flat=True)), {'reserved'})
        self.assertTrue(all(k.scope == self.site and k.vrf is None and k.role == self.role for k in kids))
        self.assertEqual(kids.get(prefix='192.168.240.252/30').description, 'WAN-P2P-64')
        oc = self.prefix_changes()
        self.assertEqual(oc.count(), 64)
        self.assertEqual(set(oc.values_list('user_name', flat=True)), {'admin'})
        self.assertEqual(len(set(oc.values_list('request_id', flat=True))), 1)
        self.assertFalse(IPAddress.objects.exists())

    def test_24_into_128_x_31_persists_with_changelog(self):
        nets = self.nets('192.168.240.0/24', 31)
        r = self.run_full(self.parent, 31, nets, create_gateway='on')
        self.assertEqual(r.status_code, 302, r.content[:2000])
        self.assertEqual(self.children(self.parent, prefix__net_mask_length=31).count(), 128)
        self.assertEqual(self.prefix_changes().count(), 128)
        self.assertFalse(IPAddress.objects.exists(), 'no gateway on /31')

    def test_create_selected_subset_without_typed_confirmation(self):
        pick = [netaddr.IPNetwork(s) for s in ('192.168.240.8/30', '192.168.240.100/30', '192.168.240.200/30')]
        r = self.run_full(self.parent, 30, pick, typed=False)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(sorted(str(p.prefix) for p in self.children(self.parent)), sorted(str(p) for p in pick))

    def test_confirm_page_lists_exact_plan(self):
        r, token = self.review(self.parent, 30, self.nets('192.168.240.0/24', 30, 20), description='L{n}')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.context['needs_typed'])
        self.assertContains(r, '20 &times; /30')
        self.assertContains(r, '192.168.240.76/30')
        self.assertContains(r, 'L20')

    def test_mass_allocation_requires_typed_count(self):
        _, token = self.review(self.parent, 30, self.nets('192.168.240.0/24', 30))
        for typed in (None, '', '63', '128'):
            kw = {} if typed is None else {'typed_count': typed}
            r = self.confirm(self.parent, token, **kw)
            self.assertEqual(r.status_code, 400)
        self.assertFalse(self.children(self.parent).exists())
        self.assertEqual(self.confirm(self.parent, token, typed_count='64').status_code, 302)

    def test_back_returns_to_selection(self):
        sel = self.nets('192.168.240.0/24', 30, 3)
        _, token = self.review(self.parent, 30, sel)
        r = self.confirm(self.parent, token, action='back')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['preview']['selected_count'], 3)
        self.assertFalse(self.children(self.parent).exists())

    @plugin_settings(max_subnets=64)
    def test_max_subnets_enforced(self):
        r, token = self.review(self.parent, 31, self.nets('192.168.240.0/24', 31))
        self.assertIsNone(token)
        self.assertContains(r, 'At most 64')


class PlanIntegrityTests(WizardTestCase):
    """The confirm step only accepts a server-signed plan; availability is still re-checked."""

    def test_v010_style_direct_create_is_ignored(self):
        data = self.form_data(30, 'create', self.nets('192.168.240.0/24', 30, 4))
        data['previewed'] = f'{self.parent.pk}:30:4:first_fit'
        self.client.post(allocate_url(self.parent), data)
        self.client.post(confirm_url(self.parent), data)
        self.assertFalse(self.children(self.parent).exists())

    def test_missing_or_tampered_token_rejected(self):
        _, token = self.review(self.parent, 30, self.nets('192.168.240.0/24', 30, 2))
        payload = signing.loads(token, salt=PLAN_SALT)
        payload['selected'] = ['192.168.240.0/25']
        forged_unsigned = signing.dumps(payload, salt='wrong-salt', compress=True)
        for bad in ('', 'garbage', token[:-3] + 'AAA', forged_unsigned):
            r = self.confirm(self.parent, bad)
            self.assertEqual(r.status_code, 302)
        self.assertFalse(self.children(self.parent).exists())

    def test_token_bound_to_user(self):
        _, token = self.review(self.parent, 30, self.nets('192.168.240.0/24', 30, 2))
        other = User.objects.create_user('other', is_superuser=True)
        self.client.force_login(other)
        self.confirm(self.parent, token)
        self.assertFalse(self.children(self.parent).exists())

    def test_token_bound_to_parent(self):
        other = Prefix.objects.create(prefix='10.0.0.0/24', status='container')
        _, token = self.review(self.parent, 30, self.nets('192.168.240.0/24', 30, 2))
        self.confirm(other, token)
        self.assertFalse(Prefix.objects.filter(prefix__net_contained='10.0.0.0/24').exists())
        self.assertFalse(self.children(self.parent).exists())

    def test_expired_token_rejected(self):
        _, token = self.review(self.parent, 30, self.nets('192.168.240.0/24', 30, 2))
        with plugin_settings(plan_max_age=-1):
            r = self.confirm(self.parent, token)
        self.assertEqual(r.status_code, 302)
        self.assertFalse(self.children(self.parent).exists())

    def test_replay_after_success_is_refused(self):
        _, token = self.review(self.parent, 30, self.nets('192.168.240.0/24', 30, 2))
        self.assertEqual(self.confirm(self.parent, token).status_code, 302)
        r = self.confirm(self.parent, token)
        self.assertEqual(r.status_code, 409)
        self.assertContains(r, 'No longer available', status_code=409)
        self.assertEqual(self.children(self.parent).count(), 2)

    def test_space_taken_between_review_and_confirm(self):
        _, token = self.review(self.parent, 30, self.nets('192.168.240.0/24', 30, 3))
        Prefix.objects.create(prefix='192.168.240.4/30', status='active', scope=self.site)
        r = self.confirm(self.parent, token)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.children(self.parent).count(), 1, 'all-or-nothing')


class ParentValidationTests(WizardTestCase):

    def test_active_parent_refused_on_page_and_at_confirm(self):
        _, token = self.review(self.parent, 30, self.nets('192.168.240.0/24', 30, 1))
        self.parent.status = 'active'
        self.parent.save()
        r = self.client.get(allocate_url(self.parent))
        self.assertContains(r, 'Only container, reserved prefixes can be split')
        self.assertNotContains(r, 'name="prefix_length"')
        r = self.confirm(self.parent, token)
        self.assertEqual(r.status_code, 409)
        self.assertFalse(self.children(self.parent).exists())

    def test_mark_utilized_parent_refused(self):
        self.parent.mark_utilized = True
        self.parent.save()
        r = self.client.get(allocate_url(self.parent))
        self.assertContains(r, 'marked as fully utilised')
        r, token = self.review(self.parent, 30, self.nets('192.168.240.0/24', 30, 1))
        self.assertIsNone(token)
        self.assertFalse(self.children(self.parent).exists())

    def test_reserved_block_split_and_converted_to_container(self):
        future = Prefix.objects.create(prefix='10.200.0.0/26', status='reserved', description='future DMZ')
        r = self.run_full(future, 30, self.nets('10.200.0.0/26', 30, 4), typed=False, confirm={'convert_parent': 'on'})
        self.assertEqual(r.status_code, 302)
        future.refresh_from_db()
        self.assertEqual(future.status, 'container')
        upd = self.prefix_changes('update').get(changed_object_id=future.pk)
        self.assertEqual(upd.prechange_data['status'], 'reserved')
        self.assertEqual(upd.postchange_data['status'], 'container')
        self.assertEqual(self.children(future).count(), 4)

    def test_reserved_block_split_without_conversion(self):
        future = Prefix.objects.create(prefix='10.200.0.0/26', status='reserved')
        self.run_full(future, 30, self.nets('10.200.0.0/26', 30, 2), typed=False)
        future.refresh_from_db()
        self.assertEqual(future.status, 'reserved')
        self.assertEqual(self.children(future).count(), 2)

    def test_conversion_needs_change_permission(self):
        future = Prefix.objects.create(prefix='10.200.0.0/26', status='reserved')
        user = User.objects.create_user('planner')
        grant(user, Prefix, ['view', 'add'])
        self.client.force_login(user)
        r, token = self.review(future, 30, self.nets('10.200.0.0/26', 30, 2))
        self.assertFalse(r.context['can_convert'])
        r = self.confirm(future, token, convert_parent='on')   # forged checkbox
        self.assertEqual(r.status_code, 409)
        self.assertFalse(self.children(future).exists())
        future.refresh_from_db()
        self.assertEqual(future.status, 'reserved')


class ModelValidationTests(WizardTestCase):

    def test_model_validation_error_is_shown_not_500(self):
        from django.core.exceptions import ValidationError
        with mock.patch.object(Prefix, 'clean', side_effect=ValidationError({'prefix': 'Injected failure'})):
            _, token = self.review(self.parent, 30, self.nets('192.168.240.0/24', 30, 2))
            r = self.confirm(self.parent, token)
        self.assertEqual(r.status_code, 409)
        self.assertContains(r, 'prefix: Injected failure', status_code=409)
        self.assertFalse(self.children(self.parent).exists())
