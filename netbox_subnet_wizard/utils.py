"""
Read-side IPAM logic: what is free inside a parent, what conflicts with a candidate.
Nothing in this module writes to the database.
"""
from django.db.models import Count, Q
from netaddr import IPNetwork, IPSet

from ipam.choices import PrefixStatusChoices
from ipam.models import IPAddress, IPRange, Prefix
from netbox.plugins import get_plugin_config

PLUGIN = 'netbox_subnet_wizard'

STRATEGY_FIRST_FIT = 'first_fit'
STRATEGY_BEST_FIT = 'best_fit'


def cfg(name):
    return get_plugin_config(PLUGIN, name)


def max_length(parent_or_net):
    version = parent_or_net.family if hasattr(parent_or_net, 'family') else parent_or_net.version
    return 32 if version == 4 else 128


# --------------------------------------------------------------------------- VRF policy

def vrf_filter(parent, field='vrf'):
    """
    Queryset kwargs selecting the objects that share IP space with `parent`.

    A VRF parent only sees its own VRF, so identical space in other (isolated) VRFs is never
    treated as occupied or conflicting. A global parent follows `global_parent_policy`.
    """
    if parent.vrf_id is None and cfg('global_parent_policy') == 'all_vrfs':
        return {}
    return {field: parent.vrf_id} if parent.vrf_id else {f'{field}__isnull': True}


def policy_label(parent):
    if parent.vrf_id:
        return f'VRF {parent.vrf}: only prefixes in this VRF occupy space'
    if cfg('global_parent_policy') == 'all_vrfs':
        return 'Global table (umbrella): prefixes in any VRF occupy space'
    return 'Global table: only global prefixes occupy space'


# --------------------------------------------------------------------------- parent checks

def parent_problems(parent):
    """Reasons the wizard must not allocate inside `parent` (empty list = OK)."""
    problems = []
    allowed = cfg('allowed_parent_statuses')
    if parent.status not in allowed:
        problems.append(
            f'{parent.prefix} has status "{parent.get_status_display()}". '
            f'Only {", ".join(allowed)} prefixes can be split.'
        )
    if parent.mark_utilized and not cfg('allow_mark_utilized_parent'):
        problems.append(f'{parent.prefix} is marked as fully utilised.')
    if parent.prefix.prefixlen >= max_length(parent):
        problems.append(f'{parent.prefix} is a host prefix and cannot be split.')
    return problems


# --------------------------------------------------------------------------- free space

def occupied_prefixes(parent):
    return Prefix.objects.filter(prefix__net_contained=str(parent.prefix), **vrf_filter(parent))


def available_space(parent):
    """IPSet of space inside `parent` not covered by any child prefix (per VRF policy)."""
    used = occupied_prefixes(parent).values_list('prefix', flat=True)
    return IPSet([parent.prefix]) - IPSet(used)


def free_blocks(available, length):
    return [b for b in available.iter_cidrs() if b.prefixlen <= length]


def count_free(available, length):
    return sum(2 ** (length - b.prefixlen) for b in free_blocks(available, length))


def length_choices(parent, available=None):
    """(value, label) for every valid child length, e.g. ('30', '/30 - 64 blocks, 60 free')."""
    if available is None:
        available = available_space(parent)
    plen = parent.prefix.prefixlen
    choices = []
    for length in range(plen + 1, max_length(parent) + 1):
        total = 2 ** (length - plen)
        free = count_free(available, length)
        choices.append((str(length), f'/{length} — {total:,} blocks, {free:,} free'))
    return choices


def list_candidates(available, length, limit):
    """(candidates, total): free /length blocks, lowest first, capped at `limit`."""
    blocks = free_blocks(available, length)
    total = sum(2 ** (length - b.prefixlen) for b in blocks)
    out = []
    for block in blocks:
        for subnet in block.subnet(length):
            if len(out) >= limit:
                return out, total
            out.append(subnet)
    return out, total


def pick(available, length, count, strategy=STRATEGY_FIRST_FIT):
    """Pre-selection helper: up to `count` free /length blocks."""
    blocks = free_blocks(available, length)
    if strategy == STRATEGY_BEST_FIT:
        blocks.sort(key=lambda b: (-b.prefixlen, b.first))
    out = []
    for block in blocks:
        for subnet in block.subnet(length):
            out.append(subnet)
            if len(out) >= count:
                return sorted(out)
    return sorted(out)


def space_summary(parent, available):
    """Numbers for the parent summary card."""
    children = occupied_prefixes(parent)
    by_status = {}
    for status in children.values_list('status', flat=True):
        by_status[status] = by_status.get(status, 0) + 1
    size = parent.prefix.size
    return {
        'free_addresses': available.size,
        'free_pct': round(available.size / size * 100, 1),
        'used_pct': round((size - available.size) / size * 100, 1),
        'children_total': sum(by_status.values()),
        'children_by_status': [
            (status, status_label(status), n)
            for status, n in sorted(by_status.items())
        ],
        'free_blocks': list(available.iter_cidrs()),
        'duplicates': duplicate_prefixes(parent),
    }


def duplicate_prefixes(parent):
    """
    Exact duplicates (same prefix, same VRF) inside `parent`. NetBox has no database constraint
    against them, so concurrent writers can create them; they are shown on every visit.
    """
    rows = (
        Prefix.objects.filter(prefix__net_contained=str(parent.prefix), **vrf_filter(parent))
        .values('vrf_id', 'prefix').annotate(n=Count('id')).filter(n__gt=1).order_by('prefix')
    )
    return [f"{r['prefix']} x{r['n']}" for r in rows]


# --------------------------------------------------------------------------- conflicts

def find_conflicts(parent, subnets):
    """
    {subnet: [description, ...]} for candidates that contain existing IP addresses or
    overlap existing IP ranges in the same IP space. NetBox allows this containment;
    for planned allocations it usually means undocumented use, so it is surfaced.
    """
    subnets = list(subnets)
    if not subnets:
        return {}
    span = IPSet(subnets)
    lo, hi = min(s.first for s in subnets), max(s.last for s in subnets)
    conflicts = {}

    ips = IPAddress.objects.filter(
        address__net_host_contained=str(parent.prefix), **vrf_filter(parent)
    ).values_list('address', 'description')
    for address, description in ips:
        if not lo <= address.ip.value <= hi or address.ip not in span:
            continue
        for s in subnets:
            if address.ip in s:
                conflicts.setdefault(s, []).append(
                    f'IP {address.ip}' + (f' ({description})' if description else '')
                )
                break

    ranges = IPRange.objects.filter(
        Q(start_address__net_host_contained=str(parent.prefix)) |
        Q(end_address__net_host_contained=str(parent.prefix)),
        **vrf_filter(parent),
    ).values_list('start_address', 'end_address')
    for start, end in ranges:
        r_lo, r_hi = start.ip.value, end.ip.value
        for s in subnets:
            if s.first <= r_hi and r_lo <= s.last:
                conflicts.setdefault(s, []).append(f'IP range {start.ip}-{end.ip}')
    return conflicts


def overlapping_prefixes(parent, networks, exclude_pks=()):
    """
    Prefixes in the parent's IP space that are equal to or inside any of `networks`,
    excluding `exclude_pks`. Used before and after commit to detect concurrent writers.
    """
    if not networks:
        return Prefix.objects.none()
    q = Q()
    for net in networks:
        q |= Q(prefix__net_contained_or_equal=str(net))
    return Prefix.objects.filter(q, **vrf_filter(parent)).exclude(pk__in=list(exclude_pks))


# --------------------------------------------------------------------------- gateway / naming

def gateway_allowed(length, version):
    limit = cfg('gateway_max_length_v4') if version == 4 else cfg('gateway_max_length_v6')
    return length <= limit


def gateway_for(subnet):
    """First usable address of `subnet` as an IPNetwork with the subnet's mask, or None."""
    if subnet.prefixlen >= max_length(subnet) - 1:
        return None
    return IPNetwork(f'{subnet[1]}/{subnet.prefixlen}')


def render_description(template, index, subnet):
    """Fill {n} (sequence number) and {prefix} placeholders."""
    return (template or '').replace('{n}', str(index)).replace('{prefix}', str(subnet))


def status_label(value):
    return next((str(c[1]) for c in PrefixStatusChoices.CHOICES if c[0] == value), value)
