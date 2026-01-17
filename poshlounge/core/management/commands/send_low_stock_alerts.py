from django.core.management.base import BaseCommand
from core.email_utils import send_low_stock_alert


class Command(BaseCommand):
    help = 'Send low stock alert emails to manager'

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE('Checking for low stock products...'))

        result = send_low_stock_alert()

        if result is True:
            self.stdout.write(
                self.style.SUCCESS('✓ Low stock alert email sent successfully')
            )
        elif result is False:
            self.stdout.write(
                self.style.ERROR('✗ Failed to send low stock alert email')
            )
        else:
            # No low-stock products found
            self.stdout.write(
                self.style.WARNING('No low stock products detected. No email sent.')
            )
