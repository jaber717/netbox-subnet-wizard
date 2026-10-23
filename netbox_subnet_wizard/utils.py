import netaddr

STRATEGY_FIRST_FIT = 'first_fit'
STRATEGY_BEST_FIT = 'best_fit'


def find_available_subnets(parent, prefix_length, count, strategy=STRATEGY_FIRST_FIT):
    """
    Return up to `count` netaddr.IPNetwork objects of size /prefix_length
    carved from the free space inside `parent`.

    first_fit: lowest addresses first.
    best_fit:  fill the smallest free blocks first, keeping large blocks intact.
    """
    available = parent.get_available_prefixes()  # netaddr.IPSet
    blocks = [b for b in available.iter_cidrs() if b.prefixlen <= prefix_length]

    if strategy == STRATEGY_BEST_FIT:
        blocks.sort(key=lambda b: (-b.prefixlen, b.first))

    results = []
    for block in blocks:
        for subnet in block.subnet(prefix_length):
            results.append(subnet)
            if len(results) >= count:
                return sorted(results)
    return sorted(results)


def list_available_subnets(parent, prefix_length, limit):
    """
    Return (subnets, total): the free /prefix_length subnets inside `parent`,
    lowest first and capped at `limit`, plus how many exist in total.
    """
    available = parent.get_available_prefixes()  # netaddr.IPSet
    blocks = [b for b in available.iter_cidrs() if b.prefixlen <= prefix_length]
    total = sum(2 ** (prefix_length - b.prefixlen) for b in blocks)

    subnets = []
    for block in blocks:
        for subnet in block.subnet(prefix_length):
            if len(subnets) >= limit:
                return subnets, total
            subnets.append(subnet)
    return subnets, total


def gateway_for(subnet):
    """First usable address of a subnet, or None for /31, /32, /127, /128."""
    max_len = 32 if subnet.version == 4 else 128
    if subnet.prefixlen >= max_len - 1:
        return None
    return netaddr.IPNetwork(f'{subnet[1]}/{subnet.prefixlen}')


def render_description(template, index, subnet):
    """Fill {n} (sequence number) and {prefix} placeholders."""
    return template.replace('{n}', str(index)).replace('{prefix}', str(subnet))
