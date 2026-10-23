# users/middleware.py
from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse


class ForcePasswordChangeMiddleware:
    """
    Students and instructors who still have their first (temporary)
    password are sent to the "Account Setup" page on EVERY page,
    not only right after login. They cannot open any other page
    (even by typing the address) until they change their credentials.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user

        if (
            user.is_authenticated
            and user.role in ('student', 'instructor')
            and user.must_change_password
        ):
            allowed_paths = (reverse('force_update'), reverse('logout'))
            is_asset = request.path.startswith((settings.STATIC_URL, settings.MEDIA_URL))

            if request.path not in allowed_paths and not is_asset:
                return redirect('force_update')

        return self.get_response(request)
