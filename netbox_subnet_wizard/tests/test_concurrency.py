"""
Concurrency with real, separate PostgreSQL connections (one per thread). Interleavings are
forced with gates so the results are deterministic, not timing luck.

What these prove (and do not prove) is spelled out per test; see README "Concurrency".
"""
import threading
import time
from unittest import mock

import netaddr
from django.db import connections
from django.test import Client, TransactionTestCase
from django_pglocks import advisory_lock

from ipam.models import VRF, Prefix
from netbox.constants import ADVISORY_LOCK_KEYS
from users.models import User

from .. import utils
from .base import WizardTestMixin


class ConcurrencyTests(WizardTestMixin, TransactionTestCase):

    def setUp(self):
        self.admin = User.objects.create_user('admin', is_superuser=True)
        self.client = Client(raise_request_exception=False)
        self.client.force_login(self.admin)

    def wizard_in_thread(self, parent, length, selected, name='wizard'):
        """Review in this thread, confirm in another (own DB connection). Returns (thread, result)."""
        _, token = self.review(parent, length, selected)
        assert token
        result = {}

        def run():
            try:
                c = Client(raise_request_exception=False)
                c.force_login(self.admin)
                r = c.post(f'/plugins/subnet-wizard/{parent.pk}/confirm/', {'token': token, 'action': 'confirm'},
                           follow=False)
                result['status'] = r.status_code
                result['body'] = r.content.decode(errors='replace')
                if r.status_code == 302:
                    page = c.get(r['Location'])
                    result['messages'] = [str(m) for m in page.context['messages']] if page.context else []
            finally:
                connections.close_all()

        t = threading.Thread(target=run, name=name)
        t.start()
        return t, result

    def test_wizard_waits_for_netbox_available_prefixes_lock(self):
        """The wizard and POST .../available-prefixes/ serialise on the same advisory lock."""
        parent = Prefix.objects.create(prefix='192.168.240.0/24', status='container')
        with advisory_lock(ADVISORY_LOCK_KEYS['available-prefixes']):
            t, res = self.wizard_in_thread(parent, 30, self.nets('192.168.240.0/24', 30, 1))
            t.join(timeout=2)
            self.assertTrue(t.is_alive(), 'wizard must block while the core allocator lock is held')
        t.join(timeout=20)
        self.assertEqual(res['status'], 302)

    def test_core_api_allocation_waits_for_wizard(self):
        """Reverse direction through the real REST endpoint."""
        from users.models import Token
        parent = Prefix.objects.create(prefix='192.168.240.0/24', status='container')
        token = Token.objects.create(user=self.admin)
        reached, release = threading.Event(), threading.Event()
        real = utils.overlapping_prefixes

        def gated(*a, **kw):
            if threading.current_thread().name == 'wizard' and not reached.is_set():
                reached.set()
                release.wait(20)
            return real(*a, **kw)

        with mock.patch.object(utils, 'overlapping_prefixes', gated):
            t, res = self.wizard_in_thread(parent, 30, self.nets('192.168.240.0/24', 30, 1))
            self.assertTrue(reached.wait(20))   # wizard holds the lock, rows written, not committed
            api = {}

            def call_api():
                try:
                    c = Client()
                    r = c.post(f'/api/ipam/prefixes/{parent.pk}/available-prefixes/',
                               {'prefix_length': 30}, content_type='application/json',
                               HTTP_AUTHORIZATION=f'{token.get_auth_header_prefix()}{token.token}')
                    api['status'], api['body'] = r.status_code, r.json()
                    api['done_at'] = time.monotonic()
                finally:
                    connections.close_all()

            a = threading.Thread(target=call_api)
            a.start()
            a.join(timeout=2)
            self.assertTrue(a.is_alive(), 'REST allocation must block while the wizard holds the lock')
            released_at = time.monotonic()
            release.set()
            t.join(20)
            a.join(20)
        self.assertEqual(res['status'], 302)
        self.assertEqual(api['status'], 201, api.get('body'))
        self.assertGreaterEqual(api['done_at'], released_at)
        self.assertEqual(api['body'][0]['prefix'] if isinstance(api['body'], list) else api['body']['prefix'],
                         '192.168.240.4/30', 'API saw the wizard allocation and took the next block')

    def test_unlocked_writer_that_commits_first_aborts_the_run(self):
        """
        Native writer (no lock) in another VRF under a global umbrella parent commits while the
        wizard is mid-transaction: the pre-commit overlap check aborts the run, nothing is created.
        """
        parent = Prefix.objects.create(prefix='192.168.240.0/24', status='container')
        vrf = VRF.objects.create(name='WAN')
        target = netaddr.IPNetwork('192.168.240.0/30')
        reached, release = threading.Event(), threading.Event()
        real_clean = Prefix.full_clean

        def gated_clean(self_, *a, **kw):
            if threading.current_thread().name == 'wizard' and self_.pk is None:
                reached.set()
                release.wait(20)
            return real_clean(self_, *a, **kw)

        with mock.patch.object(Prefix, 'full_clean', gated_clean):
            t, res = self.wizard_in_thread(parent, 30, [target])
            self.assertTrue(reached.wait(20))
            Prefix.objects.create(prefix=str(target), vrf=vrf, status='active', description='native writer')
            release.set()
            t.join(20)
        self.assertEqual(res['status'], 409)
        self.assertIn('concurrent change created overlapping prefixes', res['body'])
        self.assertEqual(Prefix.objects.filter(prefix=str(target)).count(), 1)

    def _gate_wizard_clean(self):
        reached, release = threading.Event(), threading.Event()
        real_clean = Prefix.full_clean

        def gated_clean(self_, *a, **kw):
            if threading.current_thread().name == 'wizard' and self_.pk is None and not reached.is_set():
                reached.set()
                release.wait(20)
            return real_clean(self_, *a, **kw)
        return reached, release, mock.patch.object(Prefix, 'full_clean', gated_clean)

    def test_committed_native_write_is_rejected_cleanly_by_enforce_unique(self):
        """
        A native write that COMMITS while the wizard holds its locks (here an autocommit ORM save)
        is seen by the wizard's duplicate check: clean 409 with the reason, nothing created.
        v0.1.0 returned HTTP 500 in this situation.
        """
        vrf = VRF.objects.create(name='WAN', enforce_unique=True)
        parent = Prefix.objects.create(prefix='10.60.0.0/24', vrf=vrf, status='container')
        target = netaddr.IPNetwork('10.60.0.0/30')
        reached, release, gate = self._gate_wizard_clean()
        native = {}

        def native_writer():
            try:
                Prefix.objects.create(prefix=target, vrf=vrf, status='active')   # INSERT commits at once
                native['done'] = True
            finally:
                connections.close_all()

        with gate:
            t, res = self.wizard_in_thread(parent, 30, [target])
            self.assertTrue(reached.wait(20))
            n = threading.Thread(target=native_writer, name='native')
            n.start()
            time.sleep(1)
            release.set()
            t.join(20)
            n.join(20)
        self.assertEqual(res['status'], 409)
        self.assertIn('Duplicate prefix found in VRF WAN: 10.60.0.0/30', res['body'])
        self.assertEqual(Prefix.objects.filter(prefix=str(target), vrf=vrf).count(), 1)

    def test_residual_race_with_native_rest_create_is_detected_not_prevented(self):
        """
        RESIDUAL RISK, pinned so it cannot be forgotten. A plain POST /api/ipam/prefixes/ (no lock,
        runs in a transaction) for the same prefix, in the parent's own VRF, overlapping the wizard
        run: its duplicate check runs before either INSERT is committed; it inserts, then blocks on
        the parent row (NetBox's post_save updates the parent's _children) until the wizard commits,
        then commits too. Result: a duplicate, even with enforce_unique=True. The wizard page then
        flags it. Two native writers have the same blind spot in NetBox core.
        """
        from users.models import Token
        vrf = VRF.objects.create(name='WAN', enforce_unique=True)
        parent = Prefix.objects.create(prefix='10.60.0.0/24', vrf=vrf, status='container')
        target = netaddr.IPNetwork('10.60.0.0/30')
        token = Token.objects.create(user=self.admin)
        reached, release, gate = self._gate_wizard_clean()
        api = {}

        def native_rest():
            try:
                r = Client().post('/api/ipam/prefixes/', {'prefix': str(target), 'vrf': vrf.pk, 'status': 'active'},
                                  content_type='application/json',
                                  HTTP_AUTHORIZATION=f'{token.get_auth_header_prefix()}{token.token}')
                api['status'] = r.status_code
            finally:
                connections.close_all()

        with gate:
            t, res = self.wizard_in_thread(parent, 30, [target])
            self.assertTrue(reached.wait(20))       # wizard holds advisory + parent row lock
            n = threading.Thread(target=native_rest, name='native')
            n.start()
            n.join(timeout=2)
            self.assertTrue(n.is_alive(), 'REST create blocks on the parent row after its INSERT')
            release.set()
            t.join(20)
            n.join(20)
        self.assertEqual(res['status'], 302, res.get('body', '')[res.get('body', '').find('wizard-errors'):][:600])
        self.assertEqual(api['status'], 201)
        self.assertEqual(Prefix.objects.filter(prefix=str(target), vrf=vrf).count(), 2)
        page = self.client.get(f'/plugins/subnet-wizard/{parent.pk}/')
        self.assertContains(page, 'Duplicate prefixes in this block')
        self.assertContains(page, '10.60.0.0/30 x2')

    def test_run_duration_for_128_prefixes(self):
        """Measures the exposure window for the residual race (printed, not asserted)."""
        parent = Prefix.objects.create(prefix='192.168.240.0/24', status='container')
        _, token = self.review(parent, 31, self.nets('192.168.240.0/24', 31))
        start = time.monotonic()
        r = self.confirm(parent, token, typed_count='128')
        elapsed = time.monotonic() - start
        self.assertEqual(r.status_code, 302)
        print(f'\n[measure] /24 -> 128 x /31 confirm request took {elapsed:.2f}s')
