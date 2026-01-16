from django.core.management.base import BaseCommand
from core.email_utils import send_low_stock_alert

class Command(BaseCommand):
    help = 'Send low stock alert emails to manager'

    def handle(self, *args, **kwargs):
        self.stdout.write('Sending low stock alerts...')
        if send_low_stock_alert():
            self.stdout.write(self.style.SUCCESS('✓ Low stock alert sent successfully'))
        else:
            self.stdout.write(self.style.ERROR('✗ Failed to send low stock alert'))