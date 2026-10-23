from django.contrib.auth.backends import ModelBackend


class CustomAuthBackend(ModelBackend):

    def authenticate(self, request, username=None, password=None, **kwargs):
        from django.contrib.auth import get_user_model

        User = get_user_model()

        if username is None or password is None:
            return None

        try:
            user = User.objects.get(email=username)
        except User.DoesNotExist:
            try:
                user = User.objects.get(username=username, role='student')
            except User.DoesNotExist:
                return None

        if user.check_password(password):
            return user

        return None