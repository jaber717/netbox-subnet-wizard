"""
Event pipeline. Two layers:
  * EventQueueTests  - what the request hands to NetBox's flush_events() (fast, in-process).
  * WebhookDeliveryTests - real delivery: EventRule -> Webhook -> RQ worker -> HTTP POST to a
    local receiver. Proves rolled-back runs send nothing over the wire and committed runs do.
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

import netaddr
from django.test import Client, TransactionTestCase

from core.models import ObjectType
from dcim.models import Site
from extras.models import EventRule, Webhook
from ipam.models import VRF, IPAddress, Prefix
from users.models import User

from .base import WizardTestCase, WizardTestMixin


class EventQueueTests(WizardTestCase):

    def test_committed_run_flushes_one_event_per_object(self):
        with mock.patch('netbox.context_managers.flush_events') as flush:
            r = self.run_full(self.parent, 29, self.nets('192.168.240.0/24', 29, 3), typed=False,
                              create_gateway='on')
        self.assertEqual(r.status_code, 302)
        models = sorted(e['object_type'].model for e in flush.call_args[0][0])
        self.assertEqual(models, ['ipaddress'] * 3 + ['prefix'] * 3)

    def test_rolled_back_run_flushes_nothing(self):
        vrf = VRF.objects.create(name='LAB', enforce_unique=False)
        parent = Prefix.objects.create(prefix='10.50.0.0/24', vrf=vrf, status='container')
        # Second gateway collides -> first prefix + first IP were provisionally saved, then rolled back
        IPAddress.objects.create(address=netaddr.IPNetwork('10.50.0.9/32'), vrf=vrf)
        _, token = self.review(parent, 29, self.nets('10.50.0.0/24', 29, 2), create_gateway='on')
        with mock.patch('netbox.context_managers.flush_events') as flush:
            r = self.confirm(parent, token, acknowledge_conflicts='on')
        self.assertEqual(r.status_code, 409)
        self.assertFalse(flush.called)


class _Receiver(BaseHTTPRequestHandler):
    hits = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        _Receiver.hits.append(json.loads(body or b'{}'))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


class WebhookDeliveryTests(WizardTestMixin, TransactionTestCase):

    def setUp(self):
        _Receiver.hits = []
        self.server = HTTPServer(('127.0.0.1', 0), _Receiver)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        env = mock.patch.dict(os.environ, {'NO_PROXY': '127.0.0.1,localhost', 'no_proxy': '127.0.0.1,localhost'})
        env.start()
        self.addCleanup(env.stop)

        self.admin = User.objects.create_user('admin', is_superuser=True)
        self.client = Client(raise_request_exception=False)
        self.client.force_login(self.admin)
        site = Site.objects.create(name='DC1', slug='dc1')
        self.parent = Prefix.objects.create(prefix='192.168.240.0/24', status='container', scope=site)
        hook = Webhook.objects.create(
            name='receiver', payload_url=f'http://127.0.0.1:{self.server.server_port}/hook',
            http_method='POST', http_content_type='application/json', ssl_verification=False,
        )
        rule = EventRule.objects.create(
            name='prefix+ip created', event_types=['object_created'],
            action_type='webhook', action_object_type=ObjectType.objects.get_for_model(Webhook),
            action_object_id=hook.pk,
        )
        rule.object_types.set([ObjectType.objects.get_for_model(Prefix), ObjectType.objects.get_for_model(IPAddress)])

        from django_rq import get_queue
        for name in ('high', 'default', 'low'):
            get_queue(name).empty()

    def drain(self):
        from django_rq import get_worker
        from rq import SimpleWorker
        get_worker('high', 'default', 'low', worker_class=SimpleWorker).work(burst=True)

    def test_committed_run_delivers_webhooks(self):
        r = self.run_full(self.parent, 30, self.nets('192.168.240.0/24', 30, 4), typed=False)
        self.assertEqual(r.status_code, 302)
        self.drain()
        prefixes = sorted(
            (h['data']['prefix'] for h in _Receiver.hits if h.get('object_type') == 'ipam.prefix'),
            key=netaddr.IPNetwork,
        )
        self.assertEqual(prefixes, [str(n) for n in self.nets('192.168.240.0/24', 30, 4)])
        self.assertTrue(all(h['event'] == 'created' and h['username'] == 'admin' for h in _Receiver.hits))

    def test_rolled_back_run_delivers_nothing(self):
        vrf = VRF.objects.create(name='LAB', enforce_unique=False)
        parent = Prefix.objects.create(prefix='10.50.0.0/24', vrf=vrf, status='container')
        IPAddress.objects.create(address=netaddr.IPNetwork('10.50.0.9/32'), vrf=vrf)
        self.drain()
        _Receiver.hits = []   # discard the setup objects' own events
        _, token = self.review(parent, 29, self.nets('10.50.0.0/24', 29, 2), create_gateway='on')
        r = self.confirm(parent, token, acknowledge_conflicts='on')
        self.assertEqual(r.status_code, 409)
        self.drain()
        self.assertEqual(_Receiver.hits, [])
        self.assertFalse(Prefix.objects.filter(prefix__net_contained='10.50.0.0/24').exists())
