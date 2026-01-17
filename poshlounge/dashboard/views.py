from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.db.models import Sum, Count, Q, F
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal

from core.models import (
    User, Product, Category, Order, OrderItem, Payment,
    Table, StockMovement, AuditLog, Shift
)
from dashboard.forms import ProductForm, CategoryForm, InventoryAdjustmentForm


# ============================================================================
# ADMIN DASHBOARD - Overview & Analytics
# ============================================================================

@login_required
def admin_dashboard(request):
    """
    Main admin dashboard with key metrics and recent activity.
    """
    if request.user.role != 'admin':
        messages.error(request, 'Access denied')
        return redirect('core:dashboard_router')
    
    # Date ranges
    today = timezone.now().date()
    week_ago = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)
    
    # Today's statistics
    today_orders = Order.objects.filter(created_at__date=today)
    today_revenue = Payment.objects.filter(
        processed_at__date=today
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
    
    # Weekly statistics
    week_orders = Order.objects.filter(created_at__date__gte=week_ago)
    week_revenue = Payment.objects.filter(
        processed_at__date__gte=week_ago
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
    
    # Monthly statistics
    month_revenue = Payment.objects.filter(
        processed_at__date__gte=month_ago
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
    
    # Active orders
    active_orders = Order.objects.filter(
        status__in=['pending', 'preparing', 'ready', 'served']
    ).count()
    
    # Low stock products
    low_stock_products = Product.objects.filter(
        is_active=True,
        stock_quantity__lte=F('min_stock_level')
    ).order_by('stock_quantity')[:5]
    
    # Recent payments
    recent_payments = Payment.objects.select_related(
        'order', 'processed_by'
    ).order_by('-processed_at')[:5]
    
    # Payment method breakdown (today)
    payment_breakdown = Payment.objects.filter(
        processed_at__date=today
    ).values('payment_method').annotate(
        total=Sum('amount'),
        count=Count('id')
    )
    
    # Top selling products (this week)
    top_products = OrderItem.objects.filter(
        order__created_at__date__gte=week_ago,
        order__status='completed'
    ).values('product__name').annotate(
        quantity_sold=Sum('quantity'),
        revenue=Sum(F('quantity') * F('unit_price'))
    ).order_by('-quantity_sold')[:5]
    
    context = {
        'today_orders': today_orders.count(),
        'today_revenue': today_revenue,
        'week_orders': week_orders.count(),
        'week_revenue': week_revenue,
        'month_revenue': month_revenue,
        'active_orders': active_orders,
        'low_stock_products': low_stock_products,
        'recent_payments': recent_payments,
        'payment_breakdown': payment_breakdown,
        'top_products': top_products,
    }
    
    return render(request, 'admin/dashboard.html', context)


# ============================================================================
# PRODUCT MANAGEMENT
# ============================================================================

@login_required
def product_list(request):
    """List all products with filtering and search options"""
    if request.user.role != 'admin':
        messages.error(request, 'Access denied')
        return redirect('core:dashboard_router')
    
    products = Product.objects.select_related('category').all()
    
    # Search functionality
    search_query = request.GET.get('search', '').strip()
    if search_query:
        from django.db.models import Q
        products = products.filter(
            Q(name__icontains=search_query) |
            Q(sku__icontains=search_query) |
            Q(description__icontains=search_query)
        )
    
    # Category filtering
    category_id = request.GET.get('category')
    if category_id:
        products = products.filter(category_id=category_id)
    
    # Status filtering
    status = request.GET.get('status')
    if status == 'available':
        products = products.filter(is_active=True, is_available=True)
    elif status == 'unavailable':
        products = products.filter(is_available=False)
    elif status == 'low_stock':
        products = products.filter(stock_quantity__lte=F('min_stock_level'))
    
    categories = Category.objects.all()
    
    context = {
        'products': products,
        'categories': categories,
    }
    
    return render(request, 'admin/products/list.html', context)


@login_required
def product_create(request):
    """Create new product"""
    if request.user.role != 'admin':
        messages.error(request, 'Access denied')
        return redirect('core:dashboard_router')
    
    if request.method == 'POST':
        form = ProductForm(request.POST)
        if form.is_valid():
            product = form.save(commit=False)
            product.base_price = product.current_price
            product.save()
            
            messages.success(request, f'Product "{product.name}" created successfully')
            return redirect('dashboard:product_list')
    else:
        form = ProductForm()
    
    return render(request, 'admin/products/form.html', {'form': form})


@login_required
def product_edit(request, pk):
    """Edit existing product"""
    if request.user.role != 'admin':
        messages.error(request, 'Access denied')
        return redirect('core:dashboard_router')
    
    product = get_object_or_404(Product, pk=pk)
    
    if request.method == 'POST':
        form = ProductForm(request.POST, instance=product)
        if form.is_valid():
            form.save()
            messages.success(request, f'Product "{product.name}" updated successfully')
            return redirect('dashboard:product_list')
    else:
        form = ProductForm(instance=product)
    
    return render(request, 'admin/products/form.html', {
        'form': form,
        'product': product
    })


@login_required
def product_delete(request, pk):
    """Soft delete product"""
    if request.user.role != 'admin':
        messages.error(request, 'Access denied')
        return redirect('core:dashboard_router')
    
    product = get_object_or_404(Product, pk=pk)
    
    if request.method == 'POST':
        # Soft delete - mark as inactive
        product.is_active = False
        product.is_available = False
        product.save(update_fields=['is_active', 'is_available'])
        
        # Log the deletion
        try:
            AuditLog.objects.create(
                user=request.user,
                action_type='user_action',
                table_name='products',
                record_id=str(product.pk),
                description=f'Product "{product.name}" deactivated (soft delete)',
                device_id=request.session.get('device_id', ''),
                ip_address=get_client_ip(request),
                metadata={'product_name': product.name, 'sku': product.sku}
            )
        except Exception:
            pass  # Don't block deletion if audit log fails
        
        messages.success(request, f'Product "{product.name}" has been deactivated')
        return redirect('dashboard:product_list')
    
    return render(request, 'admin/products/delete.html', {'product': product})


# Helper function
def get_client_ip(request):
    """Extract client IP from request"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


# ============================================================================
# INVENTORY MANAGEMENT
# ============================================================================

@login_required
def inventory_dashboard(request):
    """Inventory overview and management"""
    if request.user.role != 'admin':
        messages.error(request, 'Access denied')
        return redirect('core:dashboard_router')
    
    products = Product.objects.filter(is_active=True).select_related('category')
    
    # Statistics
    total_products = products.count()
    low_stock = products.filter(stock_quantity__lte=F('min_stock_level')).count()
    out_of_stock = products.filter(stock_quantity=0).count()
    total_value = sum(
        p.stock_quantity * p.current_price for p in products
    )
    
    context = {
        'products': products,
        'total_products': total_products,
        'low_stock_count': low_stock,
        'out_of_stock_count': out_of_stock,
        'total_inventory_value': total_value,
    }
    
    return render(request, 'admin/inventory/dashboard.html', context)


@login_required
@transaction.atomic
def inventory_adjust(request, pk):
    """Manually adjust inventory levels"""
    if request.user.role != 'admin':
        messages.error(request, 'Access denied')
        return redirect('core:dashboard_router')
    
    product = get_object_or_404(Product, pk=pk)
    
    if request.method == 'POST':
        form = InventoryAdjustmentForm(request.POST)
        if form.is_valid():
            quantity = form.cleaned_data['quantity']
            adjustment_type = form.cleaned_data['adjustment_type']
            notes = form.cleaned_data['notes']
            
            # Record previous quantity
            previous_quantity = product.stock_quantity
            
            # Adjust stock
            if adjustment_type == 'purchase':
                product.stock_quantity += quantity
            elif adjustment_type == 'wastage':
                product.stock_quantity -= quantity
            else:  # adjustment
                # For manual adjustments, quantity is the new total
                product.stock_quantity = quantity
            
            product.save()
            
            # Create stock movement record
            StockMovement.objects.create(
                product=product,
                movement_type=adjustment_type,
                quantity=quantity if adjustment_type == 'purchase' else -quantity,
                previous_quantity=previous_quantity,
                new_quantity=product.stock_quantity,
                notes=notes,
                created_by=request.user
            )
            
            messages.success(
                request,
                f'Inventory adjusted for {product.name}. New stock: {product.stock_quantity}'
            )
            return redirect('dashboard:inventory_dashboard')
    else:
        form = InventoryAdjustmentForm()
    
    return render(request, 'admin/inventory/adjust.html', {
        'form': form,
        'product': product
    })


@login_required
def stock_movements(request):
    """View stock movement history"""
    if request.user.role != 'admin':
        messages.error(request, 'Access denied')
        return redirect('core:dashboard_router')
    
    movements = StockMovement.objects.select_related(
        'product', 'created_by'
    ).order_by('-created_at')
    
    # Filtering
    product_id = request.GET.get('product')
    if product_id:
        movements = movements.filter(product_id=product_id)
    
    movement_type = request.GET.get('type')
    if movement_type:
        movements = movements.filter(movement_type=movement_type)
    
    # Pagination (limit to 100 recent)
    movements = movements[:100]
    
    context = {
        'movements': movements,
        'products': Product.objects.filter(is_active=True),
    }
    
    return render(request, 'admin/inventory/movements.html', context)


@login_required
def low_stock_alerts(request):
    """View low stock alerts"""
    if request.user.role != 'admin':
        messages.error(request, 'Access denied')
        return redirect('core:dashboard_router')
    
    low_stock_products = Product.objects.filter(
        is_active=True,
        stock_quantity__lte=F('min_stock_level')
    ).select_related('category').order_by('stock_quantity')
    
    context = {
        'low_stock_products': low_stock_products,
    }
    
    return render(request, 'admin/inventory/alerts.html', context)


# ============================================================================
# REPORTS
# ============================================================================

@login_required
def reports_dashboard(request):
    """Main reports dashboard"""
    if request.user.role != 'admin':
        messages.error(request, 'Access denied')
        return redirect('core:dashboard_router')
    
    return render(request, 'admin/reports/dashboard.html')


@login_required
def sales_report(request):
    """Sales report with date filtering"""
    if request.user.role != 'admin':
        messages.error(request, 'Access denied')
        return redirect('core:dashboard_router')
    
    # Date filtering
    start_date = request.GET.get('start_date', timezone.now().date())
    end_date = request.GET.get('end_date', timezone.now().date())
    
    # Sales data
    payments = Payment.objects.filter(
        processed_at__date__range=[start_date, end_date]
    ).select_related('order', 'processed_by')
    
    # Statistics
    total_revenue = payments.aggregate(total=Sum('amount'))['total'] or Decimal('0')
    total_transactions = payments.count()
    
    # Payment method breakdown
    payment_methods = payments.values('payment_method').annotate(
        total=Sum('amount'),
        count=Count('id')
    )
    
    # Daily breakdown
    daily_sales = payments.extra(
        select={'day': 'DATE(processed_at)'}
    ).values('day').annotate(
        revenue=Sum('amount'),
        transactions=Count('id')
    ).order_by('day')
    
    context = {
        'payments': payments,
        'total_revenue': total_revenue,
        'total_transactions': total_transactions,
        'payment_methods': payment_methods,
        'daily_sales': daily_sales,
        'start_date': start_date,
        'end_date': end_date,
    }
    
    return render(request, 'admin/reports/sales.html', context)


@login_required
def inventory_report(request):
    """Inventory valuation and status report"""
    if request.user.role != 'admin':
        messages.error(request, 'Access denied')
        return redirect('core:dashboard_router')
    
    products = Product.objects.filter(is_active=True).select_related('category')
    
    # Calculate values
    inventory_data = []
    total_value = Decimal('0')
    
    for product in products:
        value = product.stock_quantity * product.current_price
        total_value += value
        
        inventory_data.append({
            'product': product,
            'value': value,
            'status': 'Low Stock' if product.is_low_stock else 'OK'
        })
    
    context = {
        'inventory_data': inventory_data,
        'total_value': total_value,
    }
    
    return render(request, 'admin/reports/inventory.html', context)


@login_required
def audit_trail(request):
    """View complete audit trail"""
    if request.user.role != 'admin':
        messages.error(request, 'Access denied')
        return redirect('core:dashboard_router')
    
    logs = AuditLog.objects.select_related('user').order_by('-created_at')
    
    # Filtering
    user_id = request.GET.get('user')
    if user_id:
        logs = logs.filter(user_id=user_id)
    
    action_type = request.GET.get('action')
    if action_type:
        logs = logs.filter(action_type=action_type)
    
    # Handle bulk deletion
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'delete_old':
            # Delete logs older than 90 days
            from datetime import timedelta
            from django.utils import timezone
            cutoff_date = timezone.now() - timedelta(days=90)
            deleted_count = AuditLog.objects.filter(created_at__lt=cutoff_date).delete()[0]
            messages.success(request, f'Deleted {deleted_count} old audit logs (older than 90 days)')
            return redirect('dashboard:audit_trail')
    
    # Limit to recent 200
    logs = logs[:200]
    
    context = {
        'logs': logs,
        'users': User.objects.filter(is_active=True),
    }
    
    return render(request, 'admin/reports/audit_trail.html', context)


# ============================================================================
# USER MANAGEMENT
# ============================================================================

@login_required
def user_list(request):
    """List all users"""
    if request.user.role != 'admin':
        messages.error(request, 'Access denied')
        return redirect('core:dashboard_router')
    
    users = User.objects.all().order_by('username')
    
    context = {'users': users}
    return render(request, 'admin/users/list.html', context)


@login_required
def user_create(request):
    """Create new user"""
    if request.user.role != 'admin':
        messages.error(request, 'Access denied')
        return redirect('core:dashboard_router')
    
    if request.method == 'POST':
        # Handle user creation
        username = request.POST.get('username')
        password = request.POST.get('password')
        role = request.POST.get('role')
        pin_code = request.POST.get('pin_code')
        
        try:
            user = User.objects.create_user(
                username=username,
                password=password,
                role=role,
                pin_code=pin_code
            )
            messages.success(request, f'User "{username}" created successfully')
            return redirect('dashboard:user_list')
        except Exception as e:
            messages.error(request, f'Error creating user: {str(e)}')
    
    return render(request, 'admin/users/form.html')


# ============================================================================
# TABLE MANAGEMENT
# ============================================================================

@login_required
def table_list(request):
    """List all tables"""
    if request.user.role != 'admin':
        messages.error(request, 'Access denied')
        return redirect('core:dashboard_router')
    
    tables = Table.objects.all().order_by('number')
    
    context = {'tables': tables}
    return render(request, 'admin/tables/list.html', context)


@login_required
def table_create(request):
    """Create new table"""
    if request.user.role != 'admin':
        messages.error(request, 'Access denied')
        return redirect('core:dashboard_router')
    
    if request.method == 'POST':
        number = request.POST.get('number')
        capacity = request.POST.get('capacity')
        
        Table.objects.create(number=number, capacity=capacity)
        messages.success(request, f'Table {number} created')
        return redirect('dashboard:table_list')
    
    return render(request, 'admin/tables/form.html')


# ============================================================================
# CATEGORY MANAGEMENT
# ============================================================================

@login_required
def category_list(request):
    """List all categories"""
    if request.user.role != 'admin':
        messages.error(request, 'Access denied')
        return redirect('core:dashboard_router')
    
    categories = Category.objects.all()
    context = {'categories': categories}
    return render(request, 'admin/categories/list.html', context)


@login_required
def category_create(request):
    """Create new category"""
    if request.user.role != 'admin':
        messages.error(request, 'Access denied')
        return redirect('core:dashboard_router')
    
    if request.method == 'POST':
        form = CategoryForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Category created successfully')
            return redirect('dashboard:category_list')
    else:
        form = CategoryForm()
    
    return render(request, 'admin/categories/form.html', {'form': form})


# Placeholder views (implement as needed)
def device_list(request):
    return render(request, 'admin/devices/list.html')

def device_register(request):
    return render(request, 'admin/devices/register.html')

def system_settings(request):
    return render(request, 'admin/settings.html')

@login_required
def user_edit(request, pk):
    if request.user.role != 'admin':
        messages.error(request, 'Access denied')
        return redirect('core:dashboard_router')
    
    user = get_object_or_404(User, pk=pk)
    # Implement edit logic
    return render(request, 'admin/users/form.html', {'edit_user': user})