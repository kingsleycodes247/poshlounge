from django.core.management.base import BaseCommand
from core.email_utils import send_daily_sales_report

class Command(BaseCommand):
    help = 'Send daily sales report to manager'

    def handle(self, *args, **kwargs):
        self.stdout.write('Sending daily sales report...')
        if send_daily_sales_report():
            self.stdout.write(self.style.SUCCESS('✓ Daily report sent successfully'))
        else:
            self.stdout.write(self.style.ERROR('✗ Failed to send daily report'))