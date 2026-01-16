from django.core.mail import send_mail
from django.conf import settings
from django.template.loader import render_to_string
from core.models import Product, Payment, Order
from decimal import Decimal
from datetime import datetime, timedelta
import logging

from poshlounge.core import models

logger = logging.getLogger(__name__)


def send_low_stock_alert():
    """Send email alert for low stock products"""
    low_stock_products = Product.objects.filter(
        is_active=True,
        stock_quantity__lte=models.F('min_stock_level')
    )
    
    if not low_stock_products.exists():
        return
    
    subject = f'🚨 Low Stock Alert - {low_stock_products.count()} Products Need Restocking'
    
    message = f"""
Low Stock Alert - Posh Lounge POS

The following products are running low on stock:

"""
    for product in low_stock_products:
        message += f"""
Product: {product.name}
SKU: {product.sku}
Current Stock: {product.stock_quantity} {product.unit_of_measure}
Minimum Level: {product.min_stock_level} {product.unit_of_measure}
Status: {'OUT OF STOCK' if product.stock_quantity == 0 else 'LOW STOCK'}

---
"""
    
    message += f"""
Please restock these items as soon as possible.

View full details: https://www.poshlounge.com/dashboard/inventory/alerts/

Sent automatically by Posh Lounge POS
{datetime.now().strftime('%Y-%m-%d %H:%M')}
    """
    
    try:
        send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            [settings.MANAGER_EMAIL],
            fail_silently=False,
        )
        logger.info(f"Low stock alert sent to {settings.MANAGER_EMAIL}")
        return True
    except Exception as e:
        logger.error(f"Failed to send low stock alert: {str(e)}")
        return False


def send_daily_sales_report():
    """Send end-of-day sales report"""
    today = datetime.now().date()
    
    # Get today's data
    payments = Payment.objects.filter(processed_at__date=today)
    orders = Order.objects.filter(created_at__date=today)
    
    total_revenue = sum(p.amount for p in payments)
    total_transactions = payments.count()
    total_orders = orders.count()
    
    # Payment method breakdown
    cash_total = sum(p.amount for p in payments if p.payment_method == 'cash')
    mobile_total = sum(p.amount for p in payments if p.payment_method == 'mobile_money')
    orange_total = sum(p.amount for p in payments if p.payment_method == 'orange_money')
    
    subject = f'📊 Daily Sales Report - {today.strftime("%B %d, %Y")}'
    
    message = f"""
Daily Sales Report - Posh Lounge
{today.strftime('%A, %B %d, %Y')}

═══════════════════════════════════

SUMMARY
-------
Total Revenue: {total_revenue:,.0f} FCFA
Total Transactions: {total_transactions}
Total Orders: {total_orders}
Average Order Value: {(total_revenue / total_orders if total_orders > 0 else 0):,.0f} FCFA

PAYMENT METHODS
---------------
Cash: {cash_total:,.0f} FCFA ({(cash_total / total_revenue * 100 if total_revenue > 0 else 0):.1f}%)
Mobile Money: {mobile_total:,.0f} FCFA ({(mobile_total / total_revenue * 100 if total_revenue > 0 else 0):.1f}%)
Orange Money: {orange_total:,.0f} FCFA ({(orange_total / total_revenue * 100 if total_revenue > 0 else 0):.1f}%)

═══════════════════════════════════

View detailed report: https://www.poshlounge.com/dashboard/reports/sales/

Sent automatically by Posh Lounge POS
{datetime.now().strftime('%Y-%m-%d %H:%M')}
    """
    
    try:
        send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            [settings.MANAGER_EMAIL],
            fail_silently=False,
        )
        logger.info(f"Daily sales report sent to {settings.MANAGER_EMAIL}")
        return True
    except Exception as e:
        logger.error(f"Failed to send daily sales report: {str(e)}")
        return False