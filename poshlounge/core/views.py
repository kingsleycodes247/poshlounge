from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.db import transaction
from django.utils import timezone
from django.core.cache import cache
from decimal import Decimal, InvalidOperation


from core.models import (
    User, Order, OrderItem, Product, Table, Category,
    Payment, Shift, AuditLog
)
import uuid
import logging

logger = logging.getLogger(__name__)


# ============================================================================
# AUTHENTICATION VIEWS
# ============================================================================

def login_view(request):
    """
    Unified login for all user roles with PIN support.
    Implements device binding on first login.
    """
    if request.user.is_authenticated:
        return redirect('core:dashboard_router')
    
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        pin = request.POST.get('pin')
        
        # Authenticate with either password or PIN
        user = None
        if pin:
            try:
                user = User.objects.get(username=username, pin_code=pin)
                # For PIN login, we trust the PIN matches
                login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            except User.DoesNotExist:
                messages.error(request, 'Invalid username or PIN')
        else:
            user = authenticate(request, username=username, password=password)
            if user:
                login(request, user)
            else:
                messages.error(request, 'Invalid username or password')
        
        if user:
            # Generate device ID if not exists
            if not request.session.get('device_id'):
                request.session['device_id'] = str(uuid.uuid4())
            
            # Log successful login (non-blocking)
            try:
                AuditLog.objects.create(
                    user=user,
                    action_type='login',
                    description=f'{user.username} logged in successfully',
                    device_id=request.session.get('device_id'),
                    ip_address=get_client_ip(request)
                )
            except Exception as e:
                # Don't block login if audit logging fails
                logger.error(f"Failed to create login audit log: {str(e)}")
            
            logger.info(f"User {user.username} logged in from device {request.session.get('device_id')}")
            
            return redirect('core:dashboard_router')
    
    return render(request, 'core/login.html')


@login_required
def logout_view(request):
    """Logout user and clear session"""
    username = request.user.username
    device_id = request.session.get('device_id')
    
    # Log logout (non-blocking)
    try:
        AuditLog.objects.create(
            user=request.user,
            action_type='logout',
            description=f'{username} logged out',
            device_id=device_id,
            ip_address=get_client_ip(request)
        )
    except Exception as e:
        logger.error(f"Failed to create logout audit log: {str(e)}")
    
    logout(request)
    messages.success(request, 'You have been logged out successfully')
    return redirect('core:login')


@login_required
def dashboard_router(request):
    """Route users to their role-specific dashboard"""
    role_routes = {
        'admin': 'dashboard:admin_dashboard',
        'cashier': 'core:cashier_dashboard',
        'waiter': 'core:waiter_dashboard',
        'kitchen': 'core:kitchen_display',
    }
    
    route = role_routes.get(request.user.role, 'core:login')
    return redirect(route)


# ============================================================================
# WAITER VIEWS - Table & Order Management
# ============================================================================

@login_required
def waiter_dashboard(request):
    """
    Waiter main dashboard showing active tables and orders.
    """
    if request.user.role != 'waiter':
        messages.error(request, 'Access denied')
        return redirect('core:dashboard_router')
    
    # Get waiter's active orders
    active_orders = Order.objects.filter(
        waiter=request.user,
        status__in=['pending', 'preparing', 'ready']
    ).select_related('table').prefetch_related('items__product')
    
    context = {
        'active_orders': active_orders,
        'total_orders': active_orders.count(),
    }
    
    return render(request, 'waiter/dashboard.html', context)


@login_required
def waiter_tables(request):
    """
    Display all restaurant tables with occupancy status.
    Allows waiter to select table to create order.
    """
    if request.user.role != 'waiter':
        messages.error(request, 'Access denied')
        return redirect('core:dashboard_router')
    
    tables = Table.objects.filter(is_active=True).order_by('number')
    
    context = {
        'tables': tables,
    }
    
    return render(request, 'waiter/tables.html', context)


@login_required
@transaction.atomic
def waiter_create_order(request, table_id):
    """
    Create new order for a table.
    Table gets marked as occupied.
    """
    if request.user.role != 'waiter':
        messages.error(request, 'Access denied')
        return redirect('core:dashboard_router')
    
    table = get_object_or_404(Table, id=table_id, is_active=True)
    
    # Check if table already has pending order
    existing_order = Order.objects.filter(
        table=table,
        status__in=['pending', 'preparing', 'ready']
    ).first()
    
    if existing_order:
        # Redirect to add items to existing order
        return redirect('core:waiter_add_items', order_id=existing_order.id)
    
    if request.method == 'POST':
        # Create new order
        order = Order.objects.create(
            table=table,
            waiter=request.user,
            status='pending',
            device_id=request.session.get('device_id', '')
        )
        
        # Mark table as occupied
        table.is_occupied = True
        table.save(update_fields=['is_occupied'])
        
        messages.success(request, f'Order #{order.order_number} created for Table {table.number}')
        
        return redirect('core:waiter_add_items', order_id=order.id)
    
    context = {
        'table': table,
    }
    
    return render(request, 'waiter/create_order.html', context)


@login_required
def waiter_add_items(request, order_id):
    """
    Add items to an order.
    Real-time menu with categories and availability.
    """
    if request.user.role != 'waiter':
        messages.error(request, 'Access denied')
        return redirect('core:dashboard_router')
    
    order = get_object_or_404(Order, id=order_id, waiter=request.user)
    
    if order.status not in ['pending', 'preparing']:
        messages.error(request, 'Cannot modify this order')
        return redirect('core:waiter_dashboard')
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        # Handle item deletion
        if action == 'delete_item':
            item_id = request.POST.get('item_id')
            try:
                item = OrderItem.objects.get(id=item_id, order=order)
                if item.is_confirmed:
                    messages.error(request, 'Cannot delete confirmed items')
                else:
                    product_name = item.product.name
                    # Return stock before deleting
                    product = item.product
                    product.stock_quantity += item.quantity
                    product.save(update_fields=['stock_quantity'])
                    
                    # Delete the item
                    item.delete()
                    
                    # Recalculate order totals
                    order.refresh_from_db()
                    all_items = order.items.all()
                    if all_items.exists():
                        subtotal = sum(i.subtotal for i in all_items)
                        tax = subtotal * Decimal('0.0')
                        total = subtotal + tax
                        Order.objects.filter(pk=order.pk).update(
                            subtotal=subtotal,
                            tax_amount=tax,
                            total_amount=total
                        )
                    else:
                        Order.objects.filter(pk=order.pk).update(
                            subtotal=0,
                            tax_amount=0,
                            total_amount=0
                        )
                    
                    messages.success(request, f'Removed {product_name} from order')
                return redirect('core:waiter_add_items', order_id=order.id)
            except OrderItem.DoesNotExist:
                messages.error(request, 'Item not found')
                return redirect('core:waiter_add_items', order_id=order.id)
        
        # Handle adding items
        product_id = request.POST.get('product_id')
        quantity = request.POST.get('quantity', 1)
        special_instructions = request.POST.get('special_instructions', '')
        
        try:
            product = Product.objects.get(id=product_id, is_active=True, is_available=True)
            quantity = Decimal(str(quantity))
            
            if quantity <= 0:
                messages.error(request, 'Invalid quantity')
                return redirect('core:waiter_add_items', order_id=order.id)
            
            # Check stock availability
            if product.stock_quantity < quantity:
                messages.warning(
                    request,
                    f'Low stock for {product.name}. Available: {product.stock_quantity}'
                )
            
            # Create order item
            with transaction.atomic():
                order_item = OrderItem.objects.create(
                    order=order,
                    product=product,
                    quantity=quantity,
                    unit_price=product.current_price,
                    special_instructions=special_instructions
                )
                
                # Update order status to preparing if it was pending
                if order.status == 'pending':
                    order.status = 'preparing'
                    order.save(update_fields=['status'])
                
                # Manually recalculate totals to ensure accuracy
                order.refresh_from_db()
                all_items = order.items.all()
                subtotal = sum(item.subtotal for item in all_items)
                tax = subtotal * Decimal('0.0')  # No tax
                total = subtotal + tax
                
                Order.objects.filter(pk=order.pk).update(
                    subtotal=subtotal,
                    tax_amount=tax,
                    total_amount=total
                )
                
                messages.success(request, f'Added {product.name} to order')
            
        except Product.DoesNotExist:
            messages.error(request, 'Product not found or unavailable')
        except Exception as e:
            logger.error(f"Error adding item to order: {str(e)}")
            messages.error(request, 'Failed to add item to order')
    
    # Refresh order from database to get latest totals
    order.refresh_from_db()
    
    # Get products by category
    categories = Category.objects.filter(
        is_active=True,
        products__is_active=True,
        products__is_available=True
    ).distinct().prefetch_related('products')
    
    # Get current order items
    order_items = order.items.select_related('product').all()
    
    context = {
        'order': order,
        'order_items': order_items,
        'categories': categories,
    }
    
    return render(request, 'waiter/add_items.html', context)


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def get_client_ip(request):
    """Extract client IP address from request"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip



# ============================================================================
# CASHIER VIEWS - Payment Processing & Shift Management
# ============================================================================

@login_required
def cashier_dashboard(request):
    """
    Cashier dashboard showing pending payments and shift status.
    """
    if request.user.role != 'cashier':
        messages.error(request, 'Access denied')
        return redirect('core:dashboard_router')
    
    # Check if cashier has active shift
    active_shift = Shift.objects.filter(
        user=request.user,
        ended_at__isnull=True
    ).first()
    
    if not active_shift:
        # Redirect to start shift
        return redirect('core:cashier_start_shift')
    
    # Get orders ready for payment
    pending_orders = Order.objects.filter(
        status__in=['ready', 'served']
    ).select_related('table', 'waiter').prefetch_related('items__product', 'payments')
    
    # Calculate shift statistics
    shift_payments = Payment.objects.filter(
        processed_by=request.user,
        processed_at__gte=active_shift.started_at
    )
    
    shift_total = sum(p.amount for p in shift_payments)
    cash_total = sum(p.amount for p in shift_payments if p.payment_method == 'cash')
    mobile_total = sum(p.amount for p in shift_payments if p.payment_method == 'mobile_money')
    orange_total = sum(p.amount for p in shift_payments if p.payment_method == 'orange_money')
    
    context = {
        'active_shift': active_shift,
        'pending_orders': pending_orders,
        'shift_total': shift_total,
        'cash_total': cash_total,
        'mobile_total': mobile_total,
        'orange_total': orange_total,
        'transaction_count': shift_payments.count(),
    }
    
    return render(request, 'cashier/dashboard.html', context)


@login_required
@require_http_methods(['GET', 'POST'])
def cashier_start_shift(request):
    """
    Start cashier shift with opening cash count.
    Required before processing any payments.
    """
    if request.user.role != 'cashier':
        messages.error(request, 'Access denied')
        return redirect('core:dashboard_router')
    
    # Check for existing active shift
    existing_shift = Shift.objects.filter(
        user=request.user,
        ended_at__isnull=True
    ).first()
    
    if existing_shift:
        messages.info(request, 'You already have an active shift')
        return redirect('core:cashier_dashboard')
    
    if request.method == 'POST':
        opening_cash = request.POST.get('opening_cash', 0)
        
        try:
            opening_cash = Decimal(str(opening_cash))
            
            if opening_cash < 0:
                messages.error(request, 'Opening cash cannot be negative')
                return render(request, 'cashier/start_shift.html')
            
            # Create shift
            shift = Shift.objects.create(
                user=request.user,
                device_id=request.session.get('device_id', ''),
                opening_cash=opening_cash
            )
            
            # Mark user as in active shift
            request.user.is_active_shift = True
            request.user.save(update_fields=['is_active_shift'])
            
            # Log shift start
            AuditLog.objects.create(
                user=request.user,
                action_type='user_action',
                description=f'Shift started with opening cash: {opening_cash}',
                device_id=request.session.get('device_id'),
                metadata={'opening_cash': float(opening_cash)}
            )
            
            messages.success(request, 'Shift started successfully')
            return redirect('core:cashier_dashboard')
            
        except ValueError:
            messages.error(request, 'Invalid opening cash amount')
    
    return render(request, 'cashier/start_shift.html')


@login_required
@require_http_methods(['GET', 'POST'])
def cashier_end_shift(request):
    """
    End cashier shift with closing cash count and reconciliation.
    """
    if request.user.role != 'cashier':
        messages.error(request, 'Access denied')
        return redirect('core:dashboard_router')
    
    active_shift = Shift.objects.filter(
        user=request.user,
        ended_at__isnull=True
    ).first()
    
    if not active_shift:
        messages.error(request, 'No active shift found')
        return redirect('core:cashier_dashboard')
    
    if request.method == 'POST':
        closing_cash = request.POST.get('closing_cash', 0)
        
        try:
            closing_cash = Decimal(str(closing_cash))
            
            # Calculate expected cash
            cash_payments = Payment.objects.filter(
                processed_by=request.user,
                payment_method='cash',
                processed_at__gte=active_shift.started_at
            )
            expected_cash = active_shift.opening_cash + sum(p.amount for p in cash_payments)
            
            variance = closing_cash - expected_cash
            
            # End shift
            active_shift.ended_at = timezone.now()
            active_shift.closing_cash = closing_cash
            active_shift.save(update_fields=['ended_at', 'closing_cash'])
            
            # Mark user as not in shift
            request.user.is_active_shift = False
            request.user.save(update_fields=['is_active_shift'])
            
            # Log shift end
            AuditLog.objects.create(
                user=request.user,
                action_type='user_action',
                description=f'Shift ended - Closing cash: {closing_cash}, Variance: {variance}',
                device_id=request.session.get('device_id'),
                metadata={
                    'opening_cash': float(active_shift.opening_cash),
                    'closing_cash': float(closing_cash),
                    'expected_cash': float(expected_cash),
                    'variance': float(variance)
                }
            )
            
            if abs(variance) > Decimal('10'):  # Alert if variance > 10 FCFA
                messages.warning(
                    request,
                    f'Cash variance detected: {variance} FCFA. Please inform management.'
                )
            
            messages.success(request, 'Shift ended successfully')
            return redirect('core:login')
            
        except ValueError:
            messages.error(request, 'Invalid closing cash amount')
    
    # Calculate shift summary
    shift_payments = Payment.objects.filter(
        processed_by=request.user,
        processed_at__gte=active_shift.started_at
    )
    
    cash_payments = shift_payments.filter(payment_method='cash')
    expected_cash = active_shift.opening_cash + sum(p.amount for p in cash_payments)
    
    context = {
        'active_shift': active_shift,
        'expected_cash': expected_cash,
        'total_transactions': shift_payments.count(),
    }
    
    return render(request, 'cashier/end_shift.html', context)


@login_required
def cashier_orders(request):
    """View all orders ready for payment"""
    if request.user.role != 'cashier':
        messages.error(request, 'Access denied')
        return redirect('core:dashboard_router')
    
    orders = Order.objects.filter(
        status__in=['ready', 'served']
    ).select_related('table', 'waiter').prefetch_related('items__product')
    
    context = {'orders': orders}
    return render(request, 'cashier/orders.html', context)


@login_required
@transaction.atomic
def cashier_process_payment(request, order_id):
    """
    Process payment for an order.
    Supports Cash, Mobile Money, and Orange Money.
    Triggers receipt printing and cash drawer.
    """
    if request.user.role != 'cashier':
        messages.error(request, 'Access denied')
        return redirect('core:dashboard_router')
    
    # Check active shift
    active_shift = Shift.objects.filter(
        user=request.user,
        ended_at__isnull=True
    ).first()
    
    if not active_shift:
        messages.error(request, 'You must start a shift before processing payments')
        return redirect('core:cashier_start_shift')
    
    order = get_object_or_404(Order, id=order_id)
    
    if order.status == 'completed':
        messages.error(request, 'This order has already been paid')
        return redirect('core:cashier_dashboard')
    
    if request.method == 'POST':
        payment_method = request.POST.get('payment_method')
        amount_str = request.POST.get('amount', '')
        transaction_ref = request.POST.get('transaction_reference', '')
        
        # Debug logging
        logger.info(f"Payment request - Method: {payment_method}, Amount string: '{amount_str}', Ref: {transaction_ref}")
        
        try:
            # Clean the amount string - remove spaces, commas
            amount_str = amount_str.strip().replace(',', '').replace(' ', '')
            
            # Check if amount is empty
            if not amount_str:
                messages.error(request, 'Amount cannot be empty')
                return redirect('core:cashier_process_payment', order_id=order.id)
            
            # Convert to Decimal
            try:
                amount = Decimal(amount_str)
            except (ValueError, InvalidOperation) as e:
                logger.error(f"Invalid amount format: '{amount_str}' - Error: {str(e)}")
                messages.error(request, f'Invalid amount format: {amount_str}')
                return redirect('core:cashier_process_payment', order_id=order.id)
            
            logger.info(f"Converted amount: {amount} (type: {type(amount)})")
            
            # Validate amount
            if amount <= 0:
                logger.warning(f"Amount is zero or negative: {amount}")
                messages.error(request, f'Amount must be greater than 0 (received: {amount})')
                return redirect('core:cashier_process_payment', order_id=order.id)
            
            # Validate payment method
            valid_methods = ['cash', 'mobile_money', 'orange_money']
            if payment_method not in valid_methods:
                messages.error(request, 'Invalid payment method')
                return redirect('core:cashier_process_payment', order_id=order.id)
            
            # Require transaction reference for mobile payments
            if payment_method in ['mobile_money', 'orange_money'] and not transaction_ref:
                messages.error(request, 'Transaction reference required for mobile payments')
                return redirect('core:cashier_process_payment', order_id=order.id)
            
            # Calculate remaining amount
            total_paid = sum(p.amount for p in order.payments.all())
            remaining = order.total_amount - total_paid
            
            logger.info(f"Order total: {order.total_amount}, Paid: {total_paid}, Remaining: {remaining}")
            
            if amount > remaining:
                logger.warning(f"Amount {amount} exceeds remaining {remaining}, capping to remaining")
                amount = remaining  # Don't overpay
            
            # Create payment record
            payment = Payment.objects.create(
                order=order,
                amount=amount,
                payment_method=payment_method,
                transaction_reference=transaction_ref,
                processed_by=request.user,
                device_id=request.session.get('device_id', '')
            )

            logger.info(f"Payment created successfully: {payment.payment_number}")
            
            messages.success(
                request,
                f'Payment of {amount} FCFA processed successfully via {payment.get_payment_method_display()}'
            )
     
            return redirect('core:cashier_dashboard')
        
            
        except Exception as e:
            logger.error(f"Payment processing error: {type(e).__name__}: {str(e)}")
            messages.error(request, f'Payment processing failed: {str(e)}')
    
    # Calculate order summary
    total_paid = sum(p.amount for p in order.payments.all())
    remaining = order.total_amount - total_paid
    
    logger.info(f"Rendering payment form - Order: {order.order_number}, Remaining: {remaining}")
    
    context = {
        'order': order,
        'total_paid': total_paid,
        'remaining': remaining,
    }
    
    return render(request, 'cashier/process_payment.html', context)

    
# ============================================================================
# KITCHEN VIEWS - Order Display & Confirmation
# ============================================================================

@login_required
def kitchen_display(request):
    """
    Kitchen display system showing all pending orders.
    Real-time updates via polling.
    """
    if request.user.role != 'kitchen':
        messages.error(request, 'Access denied')
        return redirect('core:dashboard_router')
    
    # Get all orders being prepared
    orders = Order.objects.filter(
        status__in=['preparing', 'pending']
    ).select_related('table', 'waiter').prefetch_related(
        'items__product'
    ).order_by('created_at')
    
    # Organize items by order
    kitchen_orders = []
    for order in orders:
        items = order.items.filter(is_confirmed=False, product__requires_kitchen=True)
        if items.exists():
            kitchen_orders.append({
                'order': order,
                'items': items
            })
    
    context = {
        'kitchen_orders': kitchen_orders,
    }
    
    return render(request, 'kitchen/display.html', context)


@login_required
@require_http_methods(['POST'])
def kitchen_confirm_item(request, item_id):
    """
    Mark order item as confirmed/ready.
    Updates order status when all items confirmed.
    """
    if request.user.role != 'kitchen':
        return JsonResponse({'error': 'Access denied'}, status=403)
    
    try:
        item = OrderItem.objects.get(id=item_id)
        
        # Mark as confirmed
        item.is_confirmed = True
        item.confirmed_at = timezone.now()
        item.save(update_fields=['is_confirmed', 'confirmed_at'])
        
        # Check if all order items are confirmed
        order = item.order
        all_confirmed = not order.items.filter(
            is_confirmed=False,
            product__requires_kitchen=True
        ).exists()
        
        if all_confirmed and order.status == 'preparing':
            order.status = 'ready'
            order.save(update_fields=['status'])
        
        return JsonResponse({
            'success': True,
            'item_id': str(item_id),
            'order_status': order.status
        })
        
    except OrderItem.DoesNotExist:
        return JsonResponse({'error': 'Item not found'}, status=404)
    except Exception as e:
        logger.error(f"Kitchen confirmation error: {str(e)}")
        return JsonResponse({'error': 'Server error'}, status=500)
    


# ============================================================================
# API ENDPOINTS - Real-time Data for Frontend
# ============================================================================

from django.views.decorators.csrf import csrf_exempt
import json

@login_required
def api_pending_orders(request):
    """
    API endpoint for cashier to get pending orders.
    Returns JSON for real-time updates.
    """
    if request.user.role not in ['cashier', 'admin']:
        return JsonResponse({'error': 'Access denied'}, status=403)
    
    orders = Order.objects.filter(
        status__in=['ready', 'served']
    ).select_related('table', 'waiter').prefetch_related('items__product', 'payments')
    
    orders_data = []
    for order in orders:
        total_paid = sum(p.amount for p in order.payments.all())
        
        orders_data.append({
            'id': str(order.id),
            'order_number': order.order_number,
            'table': order.table.number if order.table else 'Takeout',
            'waiter': order.waiter.username,
            'total_amount': float(order.total_amount),
            'total_paid': float(total_paid),
            'remaining': float(order.total_amount - total_paid),
            'status': order.status,
            'created_at': order.created_at.isoformat(),
            'items_count': order.items.count()
        })
    
    return JsonResponse({'orders': orders_data})


@login_required
def api_kitchen_orders(request):
    """
    API endpoint for kitchen display real-time updates.
    Polls for new orders every few seconds.
    """
    if request.user.role not in ['kitchen', 'admin']:
        return JsonResponse({'error': 'Access denied'}, status=403)
    
    # Check for new orders flag in cache
    has_new = cache.get('kitchen_new_orders', False)
    
    orders = Order.objects.filter(
        status__in=['preparing', 'pending']
    ).select_related('table', 'waiter').prefetch_related('items__product')
    
    orders_data = []
    for order in orders:
        unconfirmed_items = order.items.filter(
            is_confirmed=False,
            product__requires_kitchen=True
        )
        
        if unconfirmed_items.exists():
            items_data = []
            for item in unconfirmed_items:
                items_data.append({
                    'id': str(item.id),
                    'product_name': item.product.name,
                    'quantity': float(item.quantity),
                    'special_instructions': item.special_instructions,
                    'created_at': item.created_at.isoformat()
                })
            
            orders_data.append({
                'id': str(order.id),
                'order_number': order.order_number,
                'table': order.table.number if order.table else 'Takeout',
                'waiter': order.waiter.username,
                'items': items_data,
                'created_at': order.created_at.isoformat()
            })
    
    # Clear new orders flag
    if has_new:
        cache.delete('kitchen_new_orders')
    
    return JsonResponse({
        'orders': orders_data,
        'has_new': has_new,
        'timestamp': timezone.now().isoformat()
    })


@login_required
def api_products(request):
    """
    API endpoint for getting available products.
    Used by waiter interface for menu.
    """
    category_id = request.GET.get('category')
    
    products = Product.objects.filter(
        is_active=True,
        is_available=True
    )
    
    if category_id:
        products = products.filter(category_id=category_id)
    
    products = products.select_related('category')
    
    products_data = []
    for product in products:
        products_data.append({
            'id': product.id,
            'name': product.name,
            'sku': product.sku,
            'price': float(product.current_price),
            'category': product.category.name,
            'stock': float(product.stock_quantity),
            'is_low_stock': product.is_low_stock,
            'description': product.description
        })
    
    return JsonResponse({'products': products_data})


@login_required
def api_tables(request):
    """
    API endpoint for getting table status.
    Used by waiter interface.
    """
    tables = Table.objects.filter(is_active=True).order_by('number')
    
    tables_data = []
    for table in tables:
        # Check for active orders
        active_order = Order.objects.filter(
            table=table,
            status__in=['pending', 'preparing', 'ready', 'served']
        ).first()
        
        tables_data.append({
            'id': table.id,
            'number': table.number,
            'capacity': table.capacity,
            'is_occupied': table.is_occupied,
            'has_active_order': active_order is not None,
            'order_number': active_order.order_number if active_order else None
        })
    
    return JsonResponse({'tables': tables_data})


# ============================================================================
# RECEIPT PRINTING - ESC/POS Integration
# ============================================================================

@login_required
def api_print_receipt(request, payment_id):
    """
    Generate and send receipt to ESC/POS thermal printer.
    Also returns receipt data for browser printing as fallback.
    """
    if request.user.role not in ['cashier', 'admin']:
        return JsonResponse({'error': 'Access denied'}, status=403)
    
    try:
        payment = Payment.objects.select_related(
            'order__table',
            'order__waiter',
            'processed_by'
        ).prefetch_related(
            'order__items__product'
        ).get(id=payment_id)
        
        # Build receipt data
        receipt_data = {
            'business_name': 'POSH LOUNGE',
            'address': 'New Deido, Douala',
            'tax_id': 'TIN: XXXXXXXXX',
            'receipt_number': payment.payment_number,
            'order_number': payment.order.order_number,
            'date': payment.processed_at.strftime('%Y-%m-%d %H:%M:%S'),
            'table': payment.order.table.number if payment.order.table else 'Takeout',
            'waiter': payment.order.waiter.username,
            'cashier': payment.processed_by.username,
            'items': [],
            'subtotal': float(payment.order.subtotal),
            'tax': float(payment.order.tax_amount),
            'total': float(payment.order.total_amount),
            'payment_method': payment.get_payment_method_display(),
            'amount_paid': float(payment.amount),
            'transaction_ref': payment.transaction_reference
        }
        
        # Add items
        for item in payment.order.items.all():
            receipt_data['items'].append({
                'name': item.product.name,
                'quantity': float(item.quantity),
                'unit_price': float(item.unit_price),
                'subtotal': float(item.subtotal)
            })
        
        # Generate ESC/POS commands
        escpos_commands = generate_escpos_receipt(receipt_data)
        
        # Try to send to printer
        printer_success = False
        try:
            printer_success = send_to_printer(escpos_commands)
        except Exception as e:
            logger.error(f"Printer error: {str(e)}")
        
        # Mark receipt as printed
        if printer_success:
            payment.receipt_printed = True
            payment.receipt_printed_at = timezone.now()
            payment.save(update_fields=['receipt_printed', 'receipt_printed_at'])
        
        return JsonResponse({
            'success': True,
            'receipt_data': receipt_data,
            'printer_success': printer_success
        })
        
    except Payment.DoesNotExist:
        return JsonResponse({'error': 'Payment not found'}, status=404)
    except Exception as e:
        logger.error(f"Receipt generation error: {str(e)}")
        return JsonResponse({'error': 'Failed to generate receipt'}, status=500)


def generate_escpos_receipt(data):
    """
    Generate ESC/POS commands for thermal printer.
    Returns byte array of printer commands.
    """
    # ESC/POS command constants
    ESC = b'\x1b'
    GS = b'\x1d'
    
    commands = []
    
    # Initialize printer
    commands.append(ESC + b'@')  # Initialize
    
    # Set alignment center
    commands.append(ESC + b'a' + b'\x01')
    
    # Business name (double height, bold)
    commands.append(ESC + b'!' + b'\x38')  # Double height + bold
    commands.append(data['business_name'].encode('utf-8') + b'\n')
    
    # Reset formatting
    commands.append(ESC + b'!' + b'\x00')
    
    # Address and tax ID
    commands.append(data['address'].encode('utf-8') + b'\n')
    commands.append(data['tax_id'].encode('utf-8') + b'\n')
    
    # Separator
    commands.append(b'-' * 42 + b'\n')
    
    # Receipt details (left aligned)
    commands.append(ESC + b'a' + b'\x00')  # Left align
    
    commands.append(f"Receipt #: {data['receipt_number']}\n".encode('utf-8'))
    commands.append(f"Order #: {data['order_number']}\n".encode('utf-8'))
    commands.append(f"Date: {data['date']}\n".encode('utf-8'))
    commands.append(f"Table: {data['table']}\n".encode('utf-8'))
    commands.append(f"Waiter: {data['waiter']}\n".encode('utf-8'))
    
    commands.append(b'-' * 42 + b'\n')
    
    # Items header
    commands.append(ESC + b'!' + b'\x08')  # Bold
    commands.append(b'Item                 Qty    Price   Total\n')
    commands.append(ESC + b'!' + b'\x00')  # Reset
    
    # Items
    for item in data['items']:
        name = item['name'][:20].ljust(20)
        qty = f"{item['quantity']:.1f}".rjust(6)
        price = f"{item['unit_price']:.0f}".rjust(7)
        total = f"{item['subtotal']:.0f}".rjust(7)
        
        line = f"{name}{qty}{price}{total}\n"
        commands.append(line.encode('utf-8'))
    
    commands.append(b'-' * 42 + b'\n')
    
    # Totals (right aligned)
    commands.append(ESC + b'a' + b'\x02')  # Right align
    
    commands.append(f"Subtotal:    {data['subtotal']:.2f} FCFA\n".encode('utf-8'))
    commands.append(f"Tax (19.25%): {data['tax']:.2f} FCFA\n".encode('utf-8'))
    
    # Total (bold, larger)
    commands.append(ESC + b'!' + b'\x18')  # Bold + double height
    commands.append(f"TOTAL:       {data['total']:.2f} FCFA\n".encode('utf-8'))
    commands.append(ESC + b'!' + b'\x00')  # Reset
    
    # Payment info
    commands.append(f"\nPaid ({data['payment_method']}): {data['amount_paid']:.2f} FCFA\n".encode('utf-8'))
    
    if data['transaction_ref']:
        commands.append(f"Ref: {data['transaction_ref']}\n".encode('utf-8'))
    
    # Footer (center)
    commands.append(ESC + b'a' + b'\x01')  # Center
    commands.append(b'\n')
    commands.append(b'Thank you for dining with us!\n')
    commands.append(b'Visit us again soon\n')
    
    # Cut paper
    commands.append(b'\n\n\n')
    commands.append(GS + b'V' + b'\x00')  # Full cut
    
    # Open cash drawer (for cash payments)
    if data['payment_method'] == 'Cash':
        commands.append(ESC + b'p' + b'\x00' + b'\x19' + b'\xfa')
    
    return b''.join(commands)


def send_to_printer(commands):
    """
    Send ESC/POS commands to network printer.
    Returns True if successful, False otherwise.
    """
    from django.conf import settings
    import socket
    
    try:
        # Create socket connection to printer
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        
        # Connect to printer
        sock.connect((settings.ESC_POS_PRINTER_IP, settings.ESC_POS_PRINTER_PORT))
        
        # Send commands
        sock.sendall(commands)
        
        # Close connection
        sock.close()
        
        logger.info("Receipt sent to printer successfully")
        return True
        
    except socket.timeout:
        logger.error("Printer connection timeout")
        return False
    except socket.error as e:
        logger.error(f"Printer socket error: {str(e)}")
        return False
    except Exception as e:
        logger.error(f"Printer error: {str(e)}")
        return False