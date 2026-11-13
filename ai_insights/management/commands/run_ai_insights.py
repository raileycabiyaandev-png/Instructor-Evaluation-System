from django.core.management.base import BaseCommand, CommandError

from ai_insights.models import AnalysisRun
from ai_insights.services import analysis
from ai_insights.services.data import finished_periods, period_label
from evaluation.models import EvaluationPeriod
from users.models import User


class Command(BaseCommand):
    help = (
        "Run the OFFICIAL AI analysis (the same as the admin's 'Generate AI Analysis' button).\n"
        "  python manage.py run_ai_insights                  newest finished period that has responses\n"
        "  python manage.py run_ai_insights --period 12      one chosen period (id)\n"
        "  python manage.py run_ai_insights --regenerate     run again even if nothing changed (new version)\n"
        "  python manage.py run_ai_insights --with-llm       also let Ollama write the texts\n"
        "  python manage.py run_ai_insights --auto           for a scheduler: every finished period that has\n"
        "                                                    responses but no finished analysis yet\n"
        "  python manage.py run_ai_insights --instructor 5   TEST ONE instructor only (no official run record)\n"
        "Safe to run again: unchanged data is skipped."
    )

    def add_arguments(self, parser):
        parser.add_argument('--period', type=int, help="Evaluation period id to analyse.")
        parser.add_argument('--auto', action='store_true', help="Analyse every finished period that has no analysis yet.")
        parser.add_argument('--regenerate', '--force', action='store_true', dest='regenerate',
                            help="Process again even if the data did not change (makes a new version).")
        parser.add_argument('--with-llm', action='store_true', help="Also ask Ollama to write the summary and coaching note.")
        parser.add_argument('--no-sentiment', action='store_true', help="Skip the Hugging Face sentiment step.")
        parser.add_argument('--instructor', type=int, help="Only this instructor (user id). Not an official run.")

    def handle(self, *args, **opts):
        use_sentiment = not opts['no_sentiment']

        if opts['instructor']:
            return self._one_instructor(opts, use_sentiment)

        if opts['auto']:
            targets = analysis.unanalysed_periods()
            if not targets:
                self.stdout.write("Nothing to do: every finished period with responses already has an analysis.")
                return
        elif opts['period']:
            try:
                targets = [EvaluationPeriod.objects.get(pk=opts['period'])]
            except EvaluationPeriod.DoesNotExist:
                raise CommandError("No evaluation period with that id.")
        else:
            with_data = [p for p in finished_periods() if p.evaluations.exists()]
            if not with_data:
                raise CommandError("No finished evaluation period has any response yet.")
            targets = [with_data[-1]]

        bad = 0
        for period in targets:
            try:
                run, created = analysis.start_run(period, trigger='auto' if opts['auto'] else 'command',
                                                  regenerate=opts['regenerate'], use_llm=opts['with_llm'])
            except (analysis.AnalysisBusy, analysis.AnalysisNotAllowed) as exc:
                self.stdout.write(self.style.WARNING(f"{period_label(period)}: {exc}"))
                bad += 1
                continue
            if not created:
                self.stdout.write(f"{period_label(period)}: already analysed with the same data "
                                  f"(version {run.version}). Use --regenerate to run it again.")
                continue
            run = analysis.launch(run, use_sentiment=use_sentiment, background=False)
            style = self.style.SUCCESS if run.status == AnalysisRun.STATUS_COMPLETED else self.style.ERROR
            self.stdout.write(style(f"{period_label(period)} (version {run.version}): {run.get_status_display()}. {run.message}"))
            if not run.is_released:
                bad += 1
        if bad:
            raise CommandError(f"{bad} analysis run(s) did not finish. See the messages above.")

    def _one_instructor(self, opts, use_sentiment):
        from ai_insights.services import pipeline
        try:
            teacher = User.objects.get(pk=opts['instructor'], role='instructor')
        except User.DoesNotExist:
            raise CommandError("No instructor with that id.")
        periods = finished_periods()
        with_data = [p for p in periods if teacher.received_evaluations.filter(period=p).exists()]
        target = with_data[-1] if with_data else None
        report = pipeline.run_for_instructor(teacher, use_sentiment=use_sentiment, use_llm=opts['with_llm'],
                                             force=opts['regenerate'], periods=periods, target_period=target)
        feedback = ", ".join(sorted(set(report['feedback'].values()))) or "no comments"
        self.stdout.write(f"{report['instructor']}: forecast={report['forecast']}, feedback={feedback}, "
                          f"suggestions={report['suggestions']}")
        self.stdout.write(self.style.WARNING("Single-instructor test: this is NOT an official analysis, "
                                             "so instructors do not see it."))