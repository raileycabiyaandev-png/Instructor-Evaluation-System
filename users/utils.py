from django.core.mail import send_mail
from django.conf import settings

def send_activation_email(user, request):
    activation_link = f"http://127.0.0.1:8000/activate/{user.activation_token}/"

    subject = "Activate your account"
    message = f"""
Hi {user.first_name},

Click the link below to activate your account:

{activation_link}

If you did not request this, ignore this email.
"""

    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
        fail_silently=False,
    )