from django.utils.functional import SimpleLazyObject

from .access import current_membership


class CurrentBusinessMiddleware:
    """Makes the signed-in person's current business membership available to
    templates. Views still re-check permissions through `merchant_view`."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.membership = SimpleLazyObject(lambda: current_membership(request))
        return self.get_response(request)
