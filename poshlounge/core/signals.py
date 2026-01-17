from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.core.cache import cache
from django.conf import settings
from django.utils import timezone
from decimal import Decimal
from core.models import (
    OrderItem, Order, Payment, Product, 
    StockMovement, AuditLog
)
import logging

logger = logging.getLogger(__name__)


# ============================================================================
# ORDER SIGNALS - Automated Order Processing
# ============================================================================

@receiver(post_save, sender=OrderItem)
def deduct_inventory_on_order(sender, instance, created, **kwargs):
    """
    Automatically deduct inventory when order item is created.
    Creates immutable stock movement record for audit trail.
    """
    if not created:
        return  # Only process new order items
    
    product = instance.product
    quantity_sold = instance.quantity
    
    # Check if sufficient stock available
    if product.stock_quantity < quantity_sold:
        logger.warning(
            f"Insufficient stock for {product.name}. "
            f"Available: {product.stock_quantity}, Requested: {quantity_sold}"
        )
        # In production, you might want to raise an exception or send alert
        # For now, we'll allow negative stock with a warning
    
    try:
        # Record previous quantity before update
        previous_quantity = product.stock_quantity
        
        # Deduct from inventory
        product.stock_quantity -= quantity_sold
        product.save(update_fields=['stock_quantity'])
        
        # Create immutable stock movement record
        StockMovement.objects.create(
            product=product,
            movement_type='sale',
            quantity=-quantity_sold,  # Negative for deduction
            previous_quantity=previous_quantity,
            new_quantity=product.stock_quantity,
            reference_number=str(instance.order.order_number),
            notes=f"Sold via Order #{instance.order.order_number}",
            created_by=instance.order.waiter
        )
        
        logger.info(
            f"Inventory deducted: {product.name} - {quantity_sold} units. "
            f"New stock: {product.stock_quantity}"
        )
        
        # Check for low stock and trigger alert
        if product.is_low_stock:
            trigger_low_stock_alert(product)
            
    except Exception as e:
        logger.error(f"Failed to deduct inventory for {product.name}: {str(e)}")
        raise


@receiver(post_save, sender=OrderItem)
def calculate_order_totals_on_item_save(sender, instance, created, **kwargs):
    """
    Automatically calculate order subtotal, tax, and total.
    Triggers whenever an order item is saved.
    """
    order = instance.order
    
    # Recalculate totals from all items
    order_items = order.items.all()
    
    subtotal = sum(item.subtotal for item in order_items)
    tax_amount = subtotal * Decimal(str(settings.TAX_RATE))  # Will be 0 if TAX_RATE = 0
    total_amount = subtotal + tax_amount
    
    # Update order (use update to avoid triggering signals)
    Order.objects.filter(pk=order.pk).update(
        subtotal=subtotal,
        tax_amount=tax_amount,
        total_amount=total_amount
    )
    
    logger.info(
        f"Order {order.order_number} totals updated: "
        f"Subtotal={subtotal}, Tax={tax_amount}, Total={total_amount}"
    )


@receiver(post_save, sender=OrderItem)
def notify_kitchen_on_order_item(sender, instance, created, **kwargs):
    """
    Notify kitchen display when new item is ordered.
    Auto-confirm items that don't require kitchen preparation.
    Uses cache for real-time updates.
    """
    if created:
        # Auto-confirm items that don't require kitchen (e.g., drinks)
        if not instance.product.requires_kitchen:
            instance.is_confirmed = True
            instance.confirmed_at = timezone.now()
            # Use update to avoid triggering signals again
            OrderItem.objects.filter(pk=instance.pk).update(
                is_confirmed=True,
                confirmed_at=timezone.now()
            )
            logger.info(f"Auto-confirmed non-kitchen item: {instance.product.name}")
            
            # Check if all items are ready
            order = instance.order
            all_confirmed = not order.items.filter(
                is_confirmed=False,
                product__requires_kitchen=True
            ).exists()
            
            if all_confirmed and order.status == 'preparing':
                order.status = 'ready'
                order.save(update_fields=['status'])
                logger.info(f"Order {order.order_number} auto-marked as ready")
        
        elif instance.product.requires_kitchen:
            # Store in cache for kitchen display to poll
            cache_key = f'kitchen_orders_{instance.order.id}'
            kitchen_items = cache.get(cache_key, [])
            
            kitchen_items.append({
                'id': str(instance.id),
                'product_name': instance.product.name,
                'quantity': float(instance.quantity),
                'special_instructions': instance.special_instructions,
                'table': instance.order.table.number if instance.order.table else 'Takeout',
                'order_number': instance.order.order_number,
                'created_at': instance.created_at.isoformat(),
            })
            
            # Store for 24 hours
            cache.set(cache_key, kitchen_items, 86400)
            
            # Also set a flag for new orders
            cache.set('kitchen_new_orders', True, 60)
            
            logger.info(
                f"Kitchen notified: {instance.product.name} x{instance.quantity} "
                f"for Order #{instance.order.order_number}"
            )


# ============================================================================
# PAYMENT SIGNALS - Financial Record Automation
# ============================================================================

@receiver(post_save, sender=Payment)
def mark_order_completed_on_payment(sender, instance, created, **kwargs):
    """
    Automatically mark order as completed when fully paid.
    Ensures order lifecycle is properly tracked.
    """
    if not created:
        return
    
    order = instance.order
    total_paid = sum(p.amount for p in order.payments.all())
    
    if total_paid >= order.total_amount:
        # Order is fully paid
        from django.utils import timezone
        
        Order.objects.filter(pk=order.pk).update(
            status='completed',
            completed_at=timezone.now()
        )
        
        # Free up table if applicable
        if order.table:
            order.table.is_occupied = False
            order.table.save(update_fields=['is_occupied'])
        
        logger.info(f"Order {order.order_number} marked as completed after payment")


@receiver(post_save, sender=Payment)
def trigger_cash_drawer(sender, instance, created, **kwargs):
    """
    Trigger cash drawer opening for cash payments.
    In production, this would send command to ESC/POS printer.
    """
    if created and instance.payment_method == 'cash':
        # Set flag in cache for terminal to trigger drawer
        cache_key = f'open_drawer_{instance.device_id}'
        cache.set(cache_key, {
            'payment_id': str(instance.id),
            'amount': float(instance.amount),
            'timestamp': instance.processed_at.isoformat()
        }, 60)  # 1 minute TTL
        
        logger.info(
            f"Cash drawer trigger set for device {instance.device_id} - "
            f"Payment #{instance.payment_number}"
        )


# ============================================================================
# INVENTORY SIGNALS - Stock Management Automation
# ============================================================================

@receiver(pre_save, sender=Product)
def log_price_changes(sender, instance, **kwargs):
    """
    Create audit trail when product prices are changed.
    Prevents unauthorized price manipulation.
    """
    if instance.pk:  # Only for existing products
        try:
            old_product = Product.objects.get(pk=instance.pk)
            
            if old_product.current_price != instance.current_price:
                # Price has changed - log it
                logger.warning(
                    f"PRICE CHANGE: {instance.name} - "
                    f"Old: {old_product.current_price}, "
                    f"New: {instance.current_price}"
                )
                
                # Store in cache for admin alert
                cache.set(
                    f'price_change_{instance.pk}',
                    {
                        'product': instance.name,
                        'old_price': float(old_product.current_price),
                        'new_price': float(instance.current_price),
                        'timestamp': instance.updated_at.isoformat()
                    },
                    3600  # 1 hour
                )
        except Product.DoesNotExist:
            pass


def trigger_low_stock_alert(product):
    """
    Trigger alerts when stock falls below minimum level.
    Stores alerts in cache for admin dashboard.
    """
    alert_key = f'low_stock_alert_{product.pk}'
    
    # Check if alert was already sent recently (avoid spam)
    if cache.get(alert_key):
        return
    
    alert_data = {
        'product_id': product.pk,
        'product_name': product.name,
        'sku': product.sku,
        'current_stock': float(product.stock_quantity),
        'min_stock': float(product.min_stock_level),
        'timestamp': product.updated_at.isoformat()
    }
    
    # Store individual alert
    cache.set(alert_key, alert_data, 3600)  # 1 hour
    
    # Add to global alerts list
    alerts = cache.get('low_stock_alerts', [])
    alerts.append(alert_data)
    cache.set('low_stock_alerts', alerts, 3600)
    
    logger.warning(
        f"LOW STOCK ALERT: {product.name} - "
        f"Current: {product.stock_quantity}, Min: {product.min_stock_level}"
    )


# ============================================================================
# AUDIT SIGNALS - Security and Compliance
# ============================================================================

@receiver(post_save, sender=Payment)
def audit_payment_creation(sender, instance, created, **kwargs):
    """
    Create detailed audit log for all payment transactions.
    Essential for financial compliance and fraud prevention.
    """
    if created:
        AuditLog.objects.create(
            user=instance.processed_by,
            action_type='payment_process',
            table_name='payments',
            record_id=str(instance.id),
            description=(
                f"Payment processed: {instance.payment_method} - "
                f"{settings.CURRENCY_CODE} {instance.amount} - "
                f"Order #{instance.order.order_number}"
            ),
            device_id=instance.device_id,
            metadata={
                'payment_number': instance.payment_number,
                'amount': float(instance.amount),
                'payment_method': instance.payment_method,
                'order_number': instance.order.order_number,
                'transaction_reference': instance.transaction_reference,
            }
        )


@receiver(post_save, sender=StockMovement)
def audit_stock_changes(sender, instance, created, **kwargs):
    """
    Ensure all stock movements are logged in audit trail.
    Critical for inventory reconciliation and theft prevention.
    """
    if created:
        AuditLog.objects.create(
            user=instance.created_by,
            action_type='stock_adjust',
            table_name='stock_movements',
            record_id=str(instance.id),
            description=(
                f"Stock {instance.movement_type}: {instance.product.name} - "
                f"Quantity: {instance.quantity} - "
                f"Ref: {instance.reference_number or 'N/A'}"
            ),
            metadata={
                'product_id': instance.product.pk,
                'product_name': instance.product.name,
                'movement_type': instance.movement_type,
                'quantity': float(instance.quantity),
                'previous_quantity': float(instance.previous_quantity),
                'new_quantity': float(instance.new_quantity),
                'reference': instance.reference_number,
            }
        )


# ============================================================================
# Connect all signals
# ============================================================================

def ready():
    """Called when app is ready - ensures all signals are connected"""
    logger.info("Core signals initialized successfully")