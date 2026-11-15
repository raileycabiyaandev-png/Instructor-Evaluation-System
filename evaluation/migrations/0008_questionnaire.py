# STEP 1 of 3 for the new questionnaire: only ADD the new tables (nothing is deleted here).
#   0008 (this file)  new tables + the questionnaire link on the period
#   0009              create questionnaire v1 with the 15 statements, remove the old test evaluations
#   0010              drop the 5 old rating columns

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("evaluation", "0007_remove_qrcode_created_at_qrcode_updated_at"),
    ]

    operations = [
        migrations.CreateModel(
            name="EvaluationCategory",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "key",
                    models.SlugField(
                        help_text="Fixed code name used by reports and AI. Do not rename.",
                        max_length=40,
                    ),
                ),
                ("name", models.CharField(max_length=80)),
                ("description", models.CharField(blank=True, max_length=200)),
                ("order", models.PositiveSmallIntegerField(default=0)),
                (
                    "weight",
                    models.DecimalField(
                        decimal_places=2,
                        default=1,
                        help_text="Share of this dimension in the overall score. All 1 = every dimension counts equally.",
                        max_digits=4,
                        validators=[django.core.validators.MinValueValidator(0)],
                    ),
                ),
            ],
            options={
                "verbose_name_plural": "evaluation categories",
                "ordering": ["questionnaire", "order"],
            },
        ),
        migrations.CreateModel(
            name="Questionnaire",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("version", models.PositiveIntegerField(unique=True)),
                ("name", models.CharField(max_length=100)),
                (
                    "is_active",
                    models.BooleanField(
                        default=True,
                        help_text="New evaluation periods use the newest active questionnaire.",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["-version"],
            },
        ),
        migrations.CreateModel(
            name="EvaluationQuestion",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("text", models.CharField(max_length=300)),
                ("order", models.PositiveSmallIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "category",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="questions",
                        to="evaluation.evaluationcategory",
                    ),
                ),
            ],
            options={
                "ordering": ["category__order", "order", "pk"],
            },
        ),
        migrations.AddField(
            model_name="evaluationcategory",
            name="questionnaire",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="categories",
                to="evaluation.questionnaire",
            ),
        ),
        migrations.AddField(
            model_name="evaluationperiod",
            name="questionnaire",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="periods",
                to="evaluation.questionnaire",
            ),
        ),
        migrations.CreateModel(
            name="EvaluationAnswer",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "rating",
                    models.PositiveSmallIntegerField(
                        validators=[
                            django.core.validators.MinValueValidator(1),
                            django.core.validators.MaxValueValidator(5),
                        ]
                    ),
                ),
                (
                    "evaluation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="answers",
                        to="evaluation.evaluation",
                    ),
                ),
                (
                    "question",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="answers",
                        to="evaluation.evaluationquestion",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("rating__gte", 1), ("rating__lte", 5)),
                        name="answer_rating_1_to_5",
                    )
                ],
                "unique_together": {("evaluation", "question")},
            },
        ),
        migrations.AlterUniqueTogether(
            name="evaluationcategory",
            unique_together={("questionnaire", "key")},
        ),
    ]