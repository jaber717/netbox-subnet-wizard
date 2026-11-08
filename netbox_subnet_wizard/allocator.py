"""
Write-side logic. Everything that changes the database goes through `allocate()`.

Concurrency model (see README "Concurrency" for the residual risk):
  1. PostgreSQL advisory lock ADVISORY_LOCK_KEYS['available-prefixes'] - the same lock NetBox's
     REST endpoint /api/ipam/prefixes/<id>/available-prefixes/ takes, so wizard runs and core
     API allocations are serialised against each other.
  2. SELECT ... FOR UPDATE on the parent row.
  3. Availability re-check after both locks are held.
  4. Overlap re-check of everything just written, immediately before commit. A writer that
     takes neither lock (native prefix form, POST /api/ipam/prefixes/, another plugin) and
     already COMMITTED an overlapping prefix aborts the run.
  5. Overlap check after commit, reported to the user.

Residual risk (not preventable from a plugin): a native writer in the SAME VRF whose
transaction overlaps ours runs its duplicate check before our rows are committed, inserts,
then blocks on the parent row (NetBox's post_save updates the parent's _children counter,
which we hold locked) and commits right after us. Neither side saw the other's uncommitted
row, so a duplicate results - NetBox's own enforce_unique check has the same blind spot
between two native writers. Steps 4-5 cannot see it; the wizard page lists exact duplicates
in the block on every visit (utils.duplicate_prefixes). Eliminating it needs a database
unique constraint on ipam_prefix, which NetBox core does not have.
"""
from dataclasses import dataclass, field

from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django_pglocks import advisory_lock
from netaddr import IPNetwork

from ipam.choices import PrefixStatusChoices
from ipam.models import IPAddress, Prefix, Role
from netbox.constants import ADVISORY_LOCK_KEYS
from tenancy.models import Tenant

from . import utils
from .utils import cfg

PLAN_SALT = 'netbox_subnet_wizard.plan.v1'


class AllocationError(Exception):
    """A refusal the user can act on. Nothing was written."""

    def __init__(self, *messages):
        super().__init__(' '.join(messages))
        self.messages = list(messages)


# --------------------------------------------------------------------------- plan

@dataclass
class Plan:
    """Exactly what will be created. Signed into the confirm form so it cannot be altered."""
    user: int
    parent: int
    length: int
    selected: list
    status: str
    role: int | None = None
    tenant: int | None = None
    description: str = ''
    gateway: bool = False
    conflicts: list = field(default_factory=list)

    @property
    def networks(self):
        return [IPNetwork(s) for s in self.selected]

    def sign(self):
        return signing.dumps(self.__dict__, salt=PLAN_SALT, compress=True)

    @classmethod
    def load(cls, token, user):
        try:
            data = signing.loads(token, salt=PLAN_SALT, max_age=cfg('plan_max_age'))
        except signing.SignatureExpired:
            raise AllocationError('This review has expired. Review the selection again.')
        except signing.BadSignature:
            raise AllocationError('The confirmation data is invalid. Review the selection again.')
        plan = cls(**data)
        if plan.user != user.pk:
            raise AllocationError('This review belongs to another user.')
        return plan


# --------------------------------------------------------------------------- validation

def validate_selection(parent, length, networks, available=None):
    """Raise AllocationError unless every network is a free, aligned /length inside parent."""
    problems = utils.parent_problems(parent)
    if problems:
        raise AllocationError(*problems)
    if not parent.prefix.prefixlen < length <= utils.max_length(parent):
        raise AllocationError(f'/{length} is not a valid child size for {parent.prefix}.')
    if not networks:
        raise AllocationError('Select at least one prefix.')
    if len(networks) > cfg('max_subnets'):
        raise AllocationError(f'At most {cfg("max_subnets")} prefixes can be created at once.')
    for net in networks:
        if net.prefixlen != length or net.ip != net.network or net not in parent.prefix:
            raise AllocationError(f'{net} is not a /{length} inside {parent.prefix}.')
    if available is None:
        available = utils.available_space(parent)
    taken = [str(n) for n in networks if n not in available]
    if taken:
        raise AllocationError(
            f'No longer available: {", ".join(taken)}. Another allocation happened since the preview.'
        )


# --------------------------------------------------------------------------- allocate

@dataclass
class Result:
    prefixes: list
    addresses: list
    parent_converted: bool
    late_overlaps: list   # detected after commit (residual race, see module docstring)


def allocate(user, plan, *, acknowledge_conflicts=False, convert_parent=False):
    """
    Create the planned prefixes (and gateway IPs) atomically. Raises AllocationError,
    ValidationError or PermissionDenied with nothing written. Callers must clear queued
    events when this raises (see views.ConfirmView).
    """
    networks = sorted(plan.networks)
    role = Role.objects.filter(pk=plan.role).first() if plan.role else None
    tenant = Tenant.objects.filter(pk=plan.tenant).first() if plan.tenant else None
    if plan.status not in PrefixStatusChoices.values():
        raise AllocationError(f'Unknown status {plan.status}.')

    with advisory_lock(ADVISORY_LOCK_KEYS['available-prefixes']):
        with transaction.atomic():
            parent = Prefix.objects.select_for_update().get(pk=plan.parent)
            if not Prefix.objects.restrict(user, 'view').filter(pk=parent.pk).exists():
                raise PermissionDenied('You cannot view the parent prefix.')
            validate_selection(parent, plan.length, networks)

            conflicts = utils.find_conflicts(parent, networks)
            if conflicts and not acknowledge_conflicts:
                raise AllocationError(
                    'Some selected prefixes contain existing IP addresses or ranges: '
                    + ', '.join(str(s) for s in sorted(conflicts))
                    + '. Acknowledge them on the confirmation page or remove them.'
                )

            gateway = plan.gateway and utils.gateway_allowed(plan.length, parent.family)

            prefixes, addresses = [], []
            for i, net in enumerate(networks, start=1):
                prefix = Prefix(
                    prefix=net,
                    vrf=parent.vrf,
                    scope_type=parent.scope_type,
                    scope_id=parent.scope_id,
                    status=plan.status,
                    role=role,
                    tenant=tenant,
                    description=utils.render_description(plan.description, i, net),
                )
                prefix.full_clean()
                prefix.save()
                prefixes.append(prefix)

                gw = utils.gateway_for(net) if gateway else None
                if gw is not None:
                    # Duplicate check independent of VRF.enforce_unique / ENFORCE_GLOBAL_UNIQUE
                    if IPAddress.objects.filter(vrf=parent.vrf, address__net_host=str(gw.ip)).exists():
                        raise AllocationError(
                            f'Gateway {gw.ip} already exists in {parent.vrf or "the global table"}. '
                            'Untick that prefix or disable gateway creation.'
                        )
                    ip = IPAddress(
                        address=gw,
                        vrf=parent.vrf,
                        tenant=tenant,
                        status=cfg('gateway_status'),
                        description=cfg('gateway_description'),
                    )
                    ip.full_clean()
                    ip.save()
                    addresses.append(ip)

            converted = False
            if convert_parent and parent.status == PrefixStatusChoices.STATUS_RESERVED:
                if not Prefix.objects.restrict(user, 'change').filter(pk=parent.pk).exists():
                    raise PermissionDenied('You cannot change the parent prefix.')
                parent.snapshot()
                parent.status = PrefixStatusChoices.STATUS_CONTAINER
                parent.full_clean()
                parent.save()
                converted = True

            # Object-level permission constraints, as NetBox's own edit views enforce them
            created = [p.pk for p in prefixes]
            if Prefix.objects.restrict(user, 'add').filter(pk__in=created).count() != len(created):
                raise PermissionDenied('A permission constraint prevents creating these prefixes.')
            if addresses and IPAddress.objects.restrict(user, 'add').filter(
                pk__in=[a.pk for a in addresses]
            ).count() != len(addresses):
                raise PermissionDenied('A permission constraint prevents creating these IP addresses.')

            # Pre-commit: did a writer that ignores our locks commit an overlapping prefix?
            clash = list(utils.overlapping_prefixes(parent, networks, exclude_pks=created))
            if clash:
                raise AllocationError(
                    'A concurrent change created overlapping prefixes while this run was in '
                    f'progress ({", ".join(str(p.prefix) for p in clash)}). Nothing was created.'
                )

    # Post-commit: residual window between the check above and COMMIT
    late = list(utils.overlapping_prefixes(parent, networks, exclude_pks=created))
    return Result(prefixes, addresses, converted, late)
