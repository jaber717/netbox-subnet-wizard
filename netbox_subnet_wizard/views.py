from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View

from ipam.models import IPAddress, Prefix
from netbox.plugins import get_plugin_config

from .forms import PLUGIN, SubnetWizardForm
from .utils import find_available_subnets, gateway_for, list_available_subnets, render_description


class _PreviewChanged(Exception):
    """Raised inside the transaction when ticked prefixes were taken after the preview."""

    def __init__(self, taken):
        super().__init__()
        self.taken = taken


class SubnetWizardView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'ipam.add_prefix'
    template_name = 'netbox_subnet_wizard/wizard.html'

    @staticmethod
    def preview_key(data):
        """Identifies the settings a preview was built from."""
        return f"{data['parent'].pk}:{data['prefix_length']}:{data['count']}:{data['strategy']}"

    def get(self, request):
        initial = {}
        if request.GET.get('parent'):
            initial['parent'] = request.GET['parent']
        form = SubnetWizardForm(initial=initial, user=request.user)
        return render(request, self.template_name, {'form': form})

    def post(self, request):
        form = SubnetWizardForm(request.POST, user=request.user)
        if not form.is_valid():
            return render(request, self.template_name, {'form': form})

        data = form.cleaned_data
        parent = data['parent']
        selection = None

        if request.POST.get('action') == 'create':
            max_subnets = get_plugin_config(PLUGIN, 'max_subnets')
            if data['previewed'] != self.preview_key(data):
                form.add_error(None, (
                    'The settings changed since the preview. '
                    'Review the updated list before creating.'
                ))
            elif not data['selected']:
                form.add_error(None, 'Select at least one prefix to create.')
                selection = []
            elif len(data['selected']) > max_subnets:
                form.add_error(None, f'You can create at most {max_subnets} prefixes at once.')
                selection = data['selected']
            else:
                try:
                    created = self._create(request, data, parent)
                except _PreviewChanged as e:
                    messages.warning(request, (
                        f"{', '.join(str(s) for s in e.taken)} is no longer available. "
                        'Review the updated list before creating.'
                    ))
                    selection = data['selected']
                except ValidationError as e:
                    form.add_error(None, e)
                    selection = data['selected']
                else:
                    messages.success(
                        request,
                        f'Created {len(created)} prefix(es) in {parent.prefix}.',
                    )
                    return redirect(reverse('ipam:prefix_prefixes', kwargs={'pk': parent.pk}))

        return self._render_preview(request, form, selection)

    def _render_preview(self, request, form, selection=None):
        """
        List the free prefixes in the parent with checkboxes. `selection` is what to
        tick; by default the user's ticks are kept while the settings are unchanged,
        and the strategy's picks are used otherwise.
        """
        data = form.cleaned_data
        parent = data['parent']
        length = data['prefix_length']
        key = self.preview_key(data)

        if selection is None:
            if data['previewed'] == key:
                selection = data['selected']
            else:
                selection = find_available_subnets(parent, length, data['count'], data['strategy'])

        listing, total = list_available_subnets(
            parent, length, get_plugin_config(PLUGIN, 'max_preview')
        )
        # Drop ticks that were taken since the last preview
        available = parent.get_available_prefixes()
        selection = sorted(s for s in selection if s in available)

        if total < data['count']:
            form.add_error(None, (
                f"Only {total} free /{length} block(s) are available in {parent.prefix}."
            ))

        numbers = {subnet: i for i, subnet in enumerate(selection, start=1)}
        preview = [
            {
                'prefix': subnet,
                'checked': subnet in numbers,
                'description': render_description(
                    data.get('description') or '', numbers[subnet], subnet
                ) if subnet in numbers else '',
                'gateway': gateway_for(subnet) if data.get('create_gateway') else None,
            }
            for subnet in sorted(set(listing) | set(selection))
        ]

        post = request.POST.copy()
        post['previewed'] = key
        bound = SubnetWizardForm(post, user=request.user)
        bound.is_valid()
        for error in form.non_field_errors():
            if error not in bound.non_field_errors():
                bound.add_error(None, error)

        return render(request, self.template_name, {
            'form': bound,
            'preview': preview,
            'preview_total': total,
            'selected_count': len(selection),
            'allow_create': bool(preview),
        })

    def _create(self, request, data, parent):
        gateway_status = get_plugin_config(PLUGIN, 'gateway_status')
        gateway_description = get_plugin_config(PLUGIN, 'gateway_description')

        with transaction.atomic():
            # Serialise concurrent wizard runs against the same parent
            parent = Prefix.objects.select_for_update().get(pk=parent.pk)
            available = parent.get_available_prefixes()
            taken = [s for s in data['selected'] if s not in available]
            if taken:
                raise _PreviewChanged(taken)

            prefixes, addresses = [], []
            for i, subnet in enumerate(data['selected'], start=1):
                prefix = Prefix(
                    prefix=subnet,
                    vrf=parent.vrf,
                    scope_type=parent.scope_type,
                    scope_id=parent.scope_id,
                    status=data['status'],
                    role=data['role'],
                    tenant=data['tenant'],
                    description=render_description(data['description'] or '', i, subnet),
                )
                prefix.full_clean()
                prefix.save()
                prefixes.append(prefix)

                gateway = gateway_for(subnet) if data['create_gateway'] else None
                if gateway is not None:
                    ip = IPAddress(
                        address=gateway,
                        vrf=parent.vrf,
                        tenant=data['tenant'],
                        status=gateway_status,
                        description=gateway_description,
                    )
                    ip.full_clean()
                    ip.save()
                    addresses.append(ip)

            # Enforce NetBox object-level permission constraints, as core views do
            if Prefix.objects.restrict(request.user, 'add').filter(
                pk__in=[p.pk for p in prefixes]
            ).count() != len(prefixes):
                raise PermissionDenied('A permission constraint prevents creating these prefixes.')
            if addresses and IPAddress.objects.restrict(request.user, 'add').filter(
                pk__in=[a.pk for a in addresses]
            ).count() != len(addresses):
                raise PermissionDenied('A permission constraint prevents creating these IP addresses.')

        return prefixes
