from django.db import models
from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator
from django.utils import timezone
from decimal import Decimal
import uuid

# ============================================================================
# USER & AUTHENTICATION MODELS - Role-Based Access Control
# ============================================================================

class User(AbstractUser):
    """Extended user model with role-based permissions and device binding"""
    ROLE_CHOICES = [
        ('admin', 'Administrator'),
        ('cashier', 'Cashier'),
        ('waiter', 'Waiter'),
        ('kitchen', 'Kitchen Staff'),
    ]
    
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    device_id = models.CharField(max_length=100, blank=True, null=True, 
                                 help_text="Binds user to specific device for fraud prevention")
    pin_code = models.CharField(max_length=6, blank=True, 
                                help_text="Quick PIN for POS login")
    is_active_shift = models.BooleanField(default=False)
    
    class Meta:
        db_table = 'users'
    
    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"


class Shift(models.Model):
    """Tracks employee shifts for accountability"""
    user = models.ForeignKey(User, on_delete=models.PROTECT)
    device_id = models.CharField(max_length=100)
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    opening_cash = models.DecimalField(max_digits=10, decimal_places=2, 
                                       validators=[MinValueValidator(0)])
    closing_cash = models.DecimalField(max_digits=10, decimal_places=2, 
                                       null=True, blank=True)
    
    class Meta:
        db_table = 'shifts'
        ordering = ['-started_at']
    
    def __str__(self):
        return f"{self.user.username} - {self.started_at.strftime('%Y-%m-%d %H:%M')}"


# ============================================================================
# INVENTORY MODELS - Stock Management with Audit Trails
# ============================================================================

class Category(models.Model):
    """Product categories for organization"""
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=50, default='utensils')
    is_active = models.BooleanField(default=True)
    
    class Meta:
        db_table = 'categories'
        verbose_name_plural = 'Categories'
    
    def __str__(self):
        return self.name


class Product(models.Model):
    """Menu items and inventory products"""
    name = models.CharField(max_length=200)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name='products')
    sku = models.CharField(max_length=50, unique=True, 
                          help_text="Stock Keeping Unit - unique identifier")
    description = models.TextField(blank=True)
    
    # Pricing - IMMUTABLE once product is in use
    base_price = models.DecimalField(max_digits=10, decimal_places=2, 
                                     validators=[MinValueValidator(0)])
    current_price = models.DecimalField(max_digits=10, decimal_places=2,
                                        validators=[MinValueValidator(0)])
    
    # Inventory tracking
    stock_quantity = models.DecimalField(max_digits=10, decimal_places=2, 
                                         default=0, validators=[MinValueValidator(0)])
    unit_of_measure = models.CharField(max_length=20, default='unit',
                                       help_text="e.g., unit, kg, liter")
    min_stock_level = models.DecimalField(max_digits=10, decimal_places=2, 
                                          default=10)
    
    # Status flags
    is_available = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    requires_kitchen = models.BooleanField(default=True, 
                                          help_text="If true, order goes to kitchen display")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'products'
        ordering = ['category', 'name']
    
    def __str__(self):
        return f"{self.name} - {self.sku}"
    
    @property
    def is_low_stock(self):
        """Check if product needs replenishment"""
        return self.stock_quantity <= self.min_stock_level


class StockMovement(models.Model):
    """Immutable audit trail for all inventory changes"""
    MOVEMENT_TYPES = [
        ('purchase', 'Purchase/Restock'),
        ('sale', 'Sale'),
        ('adjustment', 'Manual Adjustment'),
        ('wastage', 'Wastage/Loss'),
        ('return', 'Return'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='movements')
    movement_type = models.CharField(max_length=20, choices=MOVEMENT_TYPES)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    previous_quantity = models.DecimalField(max_digits=10, decimal_places=2)
    new_quantity = models.DecimalField(max_digits=10, decimal_places=2)
    
    reference_number = models.CharField(max_length=100, blank=True,
                                        help_text="Order ID, Invoice number, etc.")
    notes = models.TextField(blank=True)
    
    created_by = models.ForeignKey(User, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'stock_movements'
        ordering = ['-created_at']
        # Immutable: prevent updates/deletes at DB level
        permissions = [
            ('view_audit_trail', 'Can view inventory audit trails'),
        ]
    
    def __str__(self):
        return f"{self.product.name} - {self.movement_type} - {self.quantity}"
    
    def save(self, *args, **kwargs):
        # Prevent updates (but allow initial creation)
        if self.pk and self._state.adding is False:
            raise ValueError("Stock movements cannot be modified after creation")
        super().save(*args, **kwargs)


# ============================================================================
# ORDER MANAGEMENT MODELS - Transaction Tracking
# ============================================================================

class Table(models.Model):
    """Restaurant tables"""
    number = models.CharField(max_length=10, unique=True)
    capacity = models.PositiveIntegerField()
    is_occupied = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        db_table = 'tables'
        ordering = ['number']
    
    def __str__(self):
        return f"Table {self.number}"


class Order(models.Model):
    """Customer orders - central transaction record"""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('preparing', 'Preparing'),
        ('ready', 'Ready'),
        ('served', 'Served'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order_number = models.CharField(max_length=20, unique=True, editable=False)
    
    table = models.ForeignKey(Table, on_delete=models.PROTECT, null=True, blank=True)
    waiter = models.ForeignKey(User, on_delete=models.PROTECT, related_name='orders_taken',
                               limit_choices_to={'role': 'waiter'})
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    
    # Financial tracking
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    
    # Timestamps for tracking
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    # Fraud prevention
    device_id = models.CharField(max_length=100)
    
    class Meta:
        db_table = 'orders'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Order #{self.order_number}"
    
    def save(self, *args, **kwargs):
        if not self.order_number:
            # Generate unique order number: ORD-YYYYMMDD-XXXX
            today = timezone.now().strftime('%Y%m%d')
            last_order = Order.objects.filter(
                order_number__startswith=f'ORD-{today}'
            ).order_by('-order_number').first()
            
            if last_order:
                last_num = int(last_order.order_number.split('-')[-1])
                new_num = last_num + 1
            else:
                new_num = 1
            
            self.order_number = f'ORD-{today}-{new_num:04d}'
        
        super().save(*args, **kwargs)


class OrderItem(models.Model):
    """Individual items in an order - IMMUTABLE after kitchen confirmation"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    
    # Price locking - captures price at time of order
    quantity = models.DecimalField(max_digits=10, decimal_places=2,
                                   validators=[MinValueValidator(0.01)])
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    
    special_instructions = models.TextField(blank=True)
    
    # Kitchen tracking
    is_confirmed = models.BooleanField(default=False)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'order_items'
    
    def __str__(self):
        return f"{self.product.name} x{self.quantity}"
    
    def save(self, *args, **kwargs):
        # Price locking: capture current price if not set
        if not self.unit_price:
            self.unit_price = self.product.current_price
        
        # Calculate subtotal
        self.subtotal = self.quantity * self.unit_price
        
        # Allow updates for kitchen confirmation (is_confirmed, confirmed_at)
        # but prevent modification of quantity, product, price after confirmation
        if self.pk and self.is_confirmed:
            try:
                old_item = OrderItem.objects.get(pk=self.pk)
                # Check if critical fields were changed
                if (old_item.quantity != self.quantity or 
                    old_item.product_id != self.product_id or 
                    old_item.unit_price != self.unit_price):
                    raise ValueError("Cannot modify confirmed order items")
            except OrderItem.DoesNotExist:
                pass  # New item, allow save
        
        super().save(*args, **kwargs)


# ============================================================================
# PAYMENT MODELS - Financial Records (IMMUTABLE)
# ============================================================================

class Payment(models.Model):
    """Payment records - completely immutable for audit compliance"""
    PAYMENT_METHODS = [
        ('cash', 'Cash'),
        ('mobile_money', 'Mobile Money'),
        ('orange_money', 'Orange Money'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payment_number = models.CharField(max_length=20, unique=True, editable=False)
    
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='payments')
    
    amount = models.DecimalField(max_digits=10, decimal_places=2,
                                 validators=[MinValueValidator(0.01)])
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHODS)
    
    # Mobile money details
    transaction_reference = models.CharField(max_length=100, blank=True,
                                            help_text="Mobile/Orange Money transaction ID")
    
    # Tracking
    processed_by = models.ForeignKey(User, on_delete=models.PROTECT,
                                     limit_choices_to={'role': 'cashier'})
    processed_at = models.DateTimeField(auto_now_add=True)
    device_id = models.CharField(max_length=100)
    
    # Receipt printing
    receipt_printed = models.BooleanField(default=False)
    receipt_printed_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'payments'
        ordering = ['-processed_at']
    
    def __str__(self):
        return f"Payment #{self.payment_number} - {self.amount}"
    
    def save(self, *args, **kwargs):
        # Prevent any updates (but allow initial creation)
        if self.pk and self._state.adding is False:
            raise ValueError("Payment records cannot be modified after creation")
        
        if not self.payment_number:
            today = timezone.now().strftime('%Y%m%d')
            last_payment = Payment.objects.filter(
                payment_number__startswith=f'PAY-{today}'
            ).order_by('-payment_number').first()
            
            if last_payment:
                last_num = int(last_payment.payment_number.split('-')[-1])
                new_num = last_num + 1
            else:
                new_num = 1
            
            self.payment_number = f'PAY-{today}-{new_num:04d}'
        
        super().save(*args, **kwargs)
    
    def delete(self, *args, **kwargs):
        # Prevent deletion
        raise ValueError("Payment records cannot be deleted")


# ============================================================================
# AUDIT & SECURITY MODELS
# ============================================================================

class AuditLog(models.Model):
    """Complete audit trail of all system actions"""
    ACTION_TYPES = [
        ('login', 'User Login'),
        ('logout', 'User Logout'),
        ('order_create', 'Order Created'),
        ('order_modify', 'Order Modified'),
        ('order_cancel', 'Order Cancelled'),
        ('payment_process', 'Payment Processed'),
        ('stock_adjust', 'Stock Adjusted'),
        ('price_change', 'Price Changed'),
        ('user_action', 'User Action'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.PROTECT, null=True)
    action_type = models.CharField(max_length=30, choices=ACTION_TYPES)
    
    # What was affected
    table_name = models.CharField(max_length=50, blank=True)
    record_id = models.CharField(max_length=100, blank=True)
    
    # Details
    description = models.TextField()
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    device_id = models.CharField(max_length=100, blank=True)
    
    # Additional context
    metadata = models.JSONField(default=dict, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'audit_logs'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['action_type', '-created_at']),
        ]
    
    def __str__(self):
        return f"{self.action_type} by {self.user} at {self.created_at}"
    
    def save(self, *args, **kwargs):
        # Prevent updates (but allow initial creation)
        if self.pk and self._state.adding is False:
            raise ValueError("Audit logs cannot be modified")
        super().save(*args, **kwargs)


class DeviceRegistration(models.Model):
    """Tracks authorized devices for fraud prevention"""
    DEVICE_TYPES = [
        ('waiter_tablet', 'Waiter Tablet'),
        ('cashier_terminal', 'Cashier Terminal'),
        ('kitchen_display', 'Kitchen Display'),
        ('admin_device', 'Admin Device'),
    ]
    
    device_id = models.CharField(max_length=100, unique=True)
    device_type = models.CharField(max_length=20, choices=DEVICE_TYPES)
    device_name = models.CharField(max_length=100)
    
    assigned_user = models.ForeignKey(User, on_delete=models.SET_NULL, 
                                     null=True, blank=True)
    
    is_active = models.BooleanField(default=True)
    last_seen = models.DateTimeField(auto_now=True)
    
    registered_at = models.DateTimeField(auto_now_add=True)
    registered_by = models.ForeignKey(User, on_delete=models.PROTECT,
                                      related_name='registered_devices')
    
    class Meta:
        db_table = 'device_registrations'
    
    def __str__(self):
        return f"{self.device_name} ({self.device_type})"