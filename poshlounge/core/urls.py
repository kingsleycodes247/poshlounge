from django.urls import path
from core import views

app_name = 'core'

urlpatterns = [
    # Authentication
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('', views.dashboard_router, name='dashboard_router'),
    
    # Waiter Interface
    path('waiter/', views.waiter_dashboard, name='waiter_dashboard'),
    path('waiter/tables/', views.waiter_tables, name='waiter_tables'),
    path('waiter/order/<int:table_id>/', views.waiter_create_order, name='waiter_create_order'),
    path('waiter/order/<uuid:order_id>/add-items/', views.waiter_add_items, name='waiter_add_items'),
    
    # Cashier Interface
    path('cashier/', views.cashier_dashboard, name='cashier_dashboard'),
    path('cashier/orders/', views.cashier_orders, name='cashier_orders'),
    path('cashier/process-payment/<uuid:order_id>/', views.cashier_process_payment, name='cashier_process_payment'),
    path('cashier/shift-start/', views.cashier_start_shift, name='cashier_start_shift'),
    path('cashier/shift-end/', views.cashier_end_shift, name='cashier_end_shift'),
    
    # Kitchen Interface
    path('kitchen/', views.kitchen_display, name='kitchen_display'),
    path('kitchen/confirm-item/<uuid:item_id>/', views.kitchen_confirm_item, name='kitchen_confirm_item'),
    
    # API Endpoints for Real-time Updates
    path('api/orders/pending/', views.api_pending_orders, name='api_pending_orders'),
    path('api/kitchen/orders/', views.api_kitchen_orders, name='api_kitchen_orders'),
    path('api/products/', views.api_products, name='api_products'),
    path('api/tables/', views.api_tables, name='api_tables'),
    
    # Receipt Printing
    path('api/print-receipt/<uuid:payment_id>/', views.api_print_receipt, name='api_print_receipt'),
]