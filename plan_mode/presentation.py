"""Optional native work-presentation bridge; no raw plan content is published."""
import json


def service_for(ctx):
    getter = getattr(ctx, 'get_work_presentation', None)
    if getattr(ctx, 'work_presentation_capability', None) != 1 or not callable(getter):
        return None
    return getter()


def clear_mode(ctx):
    """A revoked presentation route must not prevent native off/reset semantics."""
    try:
        service = service_for(ctx)
        if service is not None:
            service.set_plan_mode(False)
    except (ValueError, PermissionError):
        # Revocation already denies presentation; native state must still clear.
        pass


def remember_publication(service, state, args, result):
    """Trust the current host ref, not the model or a result-shaped string alone."""
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (ValueError, TypeError):
            return False
    if not isinstance(result, dict) or result.get('ok') is not True:
        return False
    ref = service.current_proposal()
    if ref is None or any(result.get(key) != getattr(ref, key) for key in
                          ('proposal_id', 'incarnation', 'revision', 'source_sha256')):
        return False
    state['published_proposal'] = {key: getattr(ref, key) for key in
                                   ('proposal_id', 'incarnation', 'revision', 'source_sha256')}
    state['published_source_path'] = args['source_path']
    state['presentation_bound'] = True
    return True


def transition(service, state, status, *, source_path=None):
    receipt = state.get('published_proposal')
    if not isinstance(receipt, dict) or service is None:
        raise ValueError('Publish the current plan brief before approving it.')
    ref = service.current_proposal()
    if ref is None or any(receipt.get(key) != getattr(ref, key) for key in
                          ('proposal_id', 'incarnation', 'revision', 'source_sha256')):
        raise ValueError('The displayed plan changed. Review and publish its current revision first.')
    updated = service.transition_proposal(
        ref, expected_revision=ref.revision, status=status,
        source_path=source_path or state['published_source_path'],
        source_sha256=ref.source_sha256)
    state['published_proposal'] = {key: getattr(updated, key) for key in
                                   ('proposal_id', 'incarnation', 'revision', 'source_sha256')}
    return state['published_source_path']
