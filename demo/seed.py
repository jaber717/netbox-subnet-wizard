# Sample data for exercising the wizard. Safe to run more than once.
#   See README.md for how to run it (piping into nbshell breaks on the function below)
from dcim.models import Site
from ipam.models import VRF, Prefix, Role
from tenancy.models import Tenant

site, _ = Site.objects.get_or_create(name='Demo DC', slug='demo-dc')
vrf, _ = VRF.objects.get_or_create(name='Corp')
Role.objects.get_or_create(name='Servers', slug='servers')
Role.objects.get_or_create(name='Users', slug='users')
Tenant.objects.get_or_create(name='Acme', slug='acme')


def prefix(cidr, status, vrf=None, scope=None, description=''):
    p, created = Prefix.objects.get_or_create(
        prefix=cidr, vrf=vrf,
        defaults={'status': status, 'scope': scope, 'description': description},
    )
    print(('created ' if created else 'exists  ') + str(p))


# Empty container: simple first-fit tests
prefix('10.10.0.0/16', 'container', scope=site, description='Empty container')

# Fragmented container: gaps of different sizes, to compare first fit vs best fit
prefix('10.20.0.0/16', 'container', scope=site, description='Fragmented container')
prefix('10.20.0.0/24', 'active')
prefix('10.20.2.0/23', 'active')
prefix('10.20.5.0/24', 'active')
prefix('10.20.8.0/21', 'active')

# Container inside a VRF: new prefixes should inherit the VRF
prefix('172.16.0.0/20', 'container', vrf=vrf, description='Corp VRF container')

# Nearly full container
prefix('192.168.100.0/24', 'container', description='Almost full')
prefix('192.168.100.0/25', 'active')
prefix('192.168.100.128/26', 'active')

# IPv6
prefix('2001:db8::/48', 'container', scope=site, description='IPv6 container')

# Non-container: should not show the wizard button
prefix('10.99.0.0/24', 'active', description='Not a container')
