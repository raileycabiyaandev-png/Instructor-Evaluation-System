from django.contrib import admin
from .models import Evaluation, EvaluationPeriod, QRCode

admin.site.register(EvaluationPeriod)
admin.site.register(QRCode)