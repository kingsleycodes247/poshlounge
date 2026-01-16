from django.contrib import admin
from core.models import (
    User, Category, Product, Table, Order, OrderItem,
    Payment, Shift, StockMovement, AuditLog, DeviceRegistration
)

@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ['username', 'role', 'is_active', 'is_active_shift']
    list_filter = ['role', 'is_active']
    search_fields = ['username', 'email']

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'icon', 'is_active']
    search_fields = ['name']

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['name', 'sku', 'category', 'current_price', 'stock_quantity', 'is_available']
    list_filter = ['category', 'is_available', 'is_active']
    search_fields = ['name', 'sku']
    readonly_fields = ['created_at', 'updated_at']

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ['order_number', 'table', 'waiter', 'status', 'total_amount', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['order_number']
    readonly_fields = ['id', 'order_number', 'created_at']

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ['payment_number', 'order', 'amount', 'payment_method', 'processed_by', 'processed_at']
    list_filter = ['payment_method', 'processed_at']
    search_fields = ['payment_number', 'transaction_reference']
    readonly_fields = ['id', 'payment_number', 'processed_at']
    
    def has_delete_permission(self, request, obj=None):
        return False  # Prevent deletion

@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ['product', 'movement_type', 'quantity', 'created_by', 'created_at']
    list_filter = ['movement_type', 'created_at']
    search_fields = ['product__name', 'reference_number']
    readonly_fields = ['id', 'created_at']
    
    def has_delete_permission(self, request, obj=None):
        return False  # Immutable

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ['user', 'action_type', 'description', 'created_at']
    list_filter = ['action_type', 'created_at']
    search_fields = ['user__username', 'description']
    readonly_fields = ['id', 'created_at']
    
    def has_add_permission(self, request):
        return False  # Only created programmatically
    
    def has_delete_permission(self, request, obj=None):
        return False  # Immutable

admin.site.register(Table)
admin.site.register(Shift)
admin.site.register(OrderItem)
admin.site.register(DeviceRegistration)