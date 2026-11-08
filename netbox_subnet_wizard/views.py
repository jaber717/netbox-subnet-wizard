import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from core.signals import clear_events
from ipam.choices import PrefixStatusChoices
from ipam.models import Prefix, Role
from tenancy.models import Tenant

from . import utils
from .allocator import AllocationError, Plan, allocate, validate_selection
from .forms import AllocateForm, ParentPickForm
from .utils import cfg

logger = logging.getLogger('netbox.plugins.netbox_subnet_wizard')


def error_messages(exc):
    """Human-readable lines for any refusal raised by allocate()."""
    if isinstance(exc, AllocationError):
        return exc.messages
    if isinstance(exc, ValidationError):
        if hasattr(exc, 'error_dict'):
            return [m if f == '__all__' else f'{f}: {m}'
                    for f, msgs in exc.message_dict.items() for m in msgs]
        return exc.messages
    return [str(exc) or 'Permission denied.']


class WizardMixin(LoginRequiredMixin, PermissionRequiredMixin):
    permission_required = 'ipam.add_prefix'

    def get_parent(self, request, pk):
        return get_object_or_404(Prefix.objects.restrict(request.user, 'view'), pk=pk)


class PickerView(WizardMixin, View):
    """Entry point from the menu: choose which block to split."""
    template_name = 'netbox_subnet_wizard/picker.html'

    def get(self, request):
        if request.GET.get('parent'):
            form = ParentPickForm(request.GET, user=request.user)
            if form.is_valid():
                return redirect('plugins:netbox_subnet_wizard:allocate', pk=form.cleaned_data['parent'].pk)
        else:
            form = ParentPickForm(user=request.user)
        return render(request, self.template_name, {'form': form})


class AllocateView(WizardMixin, View):
    """Free-space overview, size dropdown, selectable preview. Never writes."""
    template_name = 'netbox_subnet_wizard/allocate.html'

    def get(self, request, pk):
        parent = self.get_parent(request, pk)
        available = utils.available_space(parent)
        form = AllocateForm(parent=parent, available=available, user=request.user)
        return self.render_page(request, parent, available, form)

    def post(self, request, pk):
        parent = self.get_parent(request, pk)
        return self.handle(request, parent, request.POST, request.POST.get('action', 'preview'))

    def handle(self, request, parent, data, action):
        available = utils.available_space(parent)
        form = AllocateForm(data, parent=parent, available=available, user=request.user)
        if utils.parent_problems(parent) or not form.is_valid():
            return self.render_page(request, parent, available, form)

        d = form.cleaned_data
        length = d['prefix_length']
        candidates, total = utils.list_candidates(available, length, cfg('max_preview'))
        conflicts = utils.find_conflicts(parent, candidates)

        if action == 'select_all':
            selection = [c for c in candidates if c not in conflicts][:cfg('max_subnets')]
        elif action == 'clear':
            selection = []
        elif action == 'preview' and d['count'] and (d['length_changed'] or not d['selected']):
            pool = utils.pick(available, length, d['count'] + len(conflicts), d['strategy'])
            selection = [s for s in pool if s not in conflicts][:d['count']]
        elif action == 'review':
            selection = d['selected']        # validated below; taken blocks are reported by name
        else:
            selection = [s for s in d['selected'] if s in available]

        if action == 'review':
            try:
                validate_selection(parent, length, selection, available)
            except AllocationError as e:
                for m in e.messages:
                    form.add_error(None, m)
            else:
                sel_conflicts = utils.find_conflicts(parent, selection)
                plan = Plan(
                    user=request.user.pk, parent=parent.pk, length=length,
                    selected=[str(s) for s in selection], status=d['status'],
                    role=d['role'].pk if d['role'] else None,
                    tenant=d['tenant'].pk if d['tenant'] else None,
                    description=d['description'] or '',
                    gateway=bool(d['create_gateway']),
                    conflicts=[str(s) for s in sorted(sel_conflicts)],
                )
                return ConfirmView.render_confirm(request, parent, plan, sel_conflicts)

        # Re-bind with the computed selection and the length the table now shows
        post = data.copy()
        post.setlist('selected', [str(s) for s in selection])
        post['shown_length'] = str(length)
        bound = AllocateForm(post, parent=parent, available=available, user=request.user)
        bound.is_valid()
        for err in form.non_field_errors():
            if err not in bound.non_field_errors():
                bound.add_error(None, err)

        chosen = set(selection)
        numbers = {s: i for i, s in enumerate(sorted(chosen), start=1)}
        show_gw = d['create_gateway'] and utils.gateway_allowed(length, parent.family)
        rows = [{
            'prefix': c,
            'checked': c in chosen,
            'conflicts': conflicts.get(c, []),
            'description': utils.render_description(d['description'], numbers[c], c) if c in chosen else '',
            'gateway': utils.gateway_for(c) if show_gw else None,
        } for c in sorted(set(candidates) | chosen)]

        return self.render_page(request, parent, available, bound, {
            'rows': rows,
            'length': length,
            'total_free': total,
            'selected_count': len(chosen),
            'conflict_count': len(conflicts),
            'truncated': total > len(candidates),
            'show_gateway': show_gw,
        })

    def render_page(self, request, parent, available, form, preview=None):
        summary = utils.space_summary(parent, available)
        return render(request, self.template_name, {
            'object': parent,
            'parent': parent,
            'form': form,
            'problems': utils.parent_problems(parent),
            'policy': utils.policy_label(parent),
            'summary': summary,
            'free_blocks': summary['free_blocks'][:64],
            'free_blocks_more': max(len(summary['free_blocks']) - 64, 0),
            'vrf_query': self.vrf_query(parent),
            'max_subnets': cfg('max_subnets'),
            'preview': preview,
        })

    @staticmethod
    def vrf_query(parent):
        if parent.vrf_id:
            return f'&vrf_id={parent.vrf_id}'
        return '' if cfg('global_parent_policy') == 'all_vrfs' else '&vrf_id=null'


class ConfirmView(WizardMixin, View):
    """Shows exactly what will be written; only a signed plan from the review step is accepted."""
    template_name = 'netbox_subnet_wizard/confirm.html'

    @classmethod
    def render_confirm(cls, request, parent, plan, conflicts, errors=None, status=200):
        n = len(plan.selected)
        can_convert = (
            parent.status == PrefixStatusChoices.STATUS_RESERVED
            and Prefix.objects.restrict(request.user, 'change').filter(pk=parent.pk).exists()
        )
        gateway = plan.gateway and utils.gateway_allowed(plan.length, parent.family)
        return render(request, cls.template_name, {
            'object': parent,
            'parent': parent,
            'plan': plan,
            'token': plan.sign(),
            'count': n,
            'role': Role.objects.filter(pk=plan.role).first() if plan.role else None,
            'tenant': Tenant.objects.filter(pk=plan.tenant).first() if plan.tenant else None,
            'status_label': utils.status_label(plan.status),
            'gateway': gateway,
            'items': [{
                'prefix': net,
                'description': utils.render_description(plan.description, i, net),
                'gateway': utils.gateway_for(net) if gateway else None,
                'conflicts': conflicts.get(net, []),
            } for i, net in enumerate(plan.networks, start=1)],
            'has_conflicts': bool(conflicts),
            'needs_typed': n >= cfg('mass_confirm_threshold'),
            'can_convert': can_convert,
            'errors': errors or [],
        }, status=status)

    def post(self, request, pk):
        parent = self.get_parent(request, pk)
        try:
            plan = Plan.load(request.POST.get('token', ''), request.user)
            if plan.parent != parent.pk:
                raise AllocationError('This review was made for a different parent prefix.')
        except AllocationError as e:
            for m in e.messages:
                messages.error(request, m)
            return redirect('plugins:netbox_subnet_wizard:allocate', pk=parent.pk)

        if request.POST.get('action') == 'back':
            data = QueryDict(mutable=True)
            data.update({
                'prefix_length': str(plan.length), 'status': plan.status,
                'role': plan.role or '', 'tenant': plan.tenant or '',
                'description': plan.description, 'strategy': 'first_fit',
                'shown_length': str(plan.length),
            })
            if plan.gateway:
                data['create_gateway'] = 'on'
            data.setlist('selected', plan.selected)
            return AllocateView().handle(request, parent, data, 'preview')

        conflicts = utils.find_conflicts(parent, plan.networks)
        n = len(plan.selected)
        errors = []
        if n >= cfg('mass_confirm_threshold') and request.POST.get('typed_count', '').strip() != str(n):
            errors.append(f'Type {n} to confirm creating {n} prefixes.')
        ack = request.POST.get('acknowledge_conflicts') == 'on'
        if conflicts and not ack:
            errors.append('Tick the acknowledgement for prefixes that contain existing IPs or ranges.')
        if errors:
            return self.render_confirm(request, parent, plan, conflicts, errors, status=400)

        try:
            result = allocate(
                request.user, plan,
                acknowledge_conflicts=ack,
                convert_parent=request.POST.get('convert_parent') == 'on',
            )
        except (AllocationError, ValidationError, PermissionDenied) as e:
            # The transaction rolled back; drop the events queued by the provisional saves so
            # no webhook or event rule fires for objects that do not exist.
            clear_events.send(sender=self)
            parent.refresh_from_db()
            return self.render_confirm(
                request, parent, plan, utils.find_conflicts(parent, plan.networks),
                error_messages(e), status=409,
            )
        except Exception:
            clear_events.send(sender=self)
            raise

        msg = f'Created {len(result.prefixes)} prefix(es) in {parent.prefix}'
        if result.addresses:
            msg += f' and {len(result.addresses)} gateway IP(s)'
        if result.parent_converted:
            msg += f'; {parent.prefix} is now a container'
        messages.success(request, msg + '.')
        if result.late_overlaps:
            listed = ', '.join(f'{p.prefix} (#{p.pk})' for p in result.late_overlaps)
            logger.warning('Overlap detected after commit in %s: %s', parent.prefix, listed)
            messages.error(request, (
                'Created, but a change made outside the wizard at the same moment overlaps: '
                f'{listed}. Review and remove the duplicate.'
            ))
        return redirect(reverse('ipam:prefix_prefixes', kwargs={'pk': parent.pk}))
