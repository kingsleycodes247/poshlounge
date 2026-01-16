from django.urls import path
from dashboard import views

app_name = 'dashboard'

urlpatterns = [
    # Main Dashboard
    path('', views.admin_dashboard, name='admin_dashboard'),
    
    # Product Management
    path('products/', views.product_list, name='product_list'),
    path('products/create/', views.product_create, name='product_create'),
    path('products/<int:pk>/edit/', views.product_edit, name='product_edit'),
    path('products/<int:pk>/delete/', views.product_delete, name='product_delete'),
    
    # Category Management
    path('categories/', views.category_list, name='category_list'),
    path('categories/create/', views.category_create, name='category_create'),
    
    # Inventory Management
    path('inventory/', views.inventory_dashboard, name='inventory_dashboard'),
    path('inventory/adjust/<int:pk>/', views.inventory_adjust, name='inventory_adjust'),
    path('inventory/movements/', views.stock_movements, name='stock_movements'),
    path('inventory/alerts/', views.low_stock_alerts, name='low_stock_alerts'),
    
    # Reports
    path('reports/', views.reports_dashboard, name='reports_dashboard'),
    path('reports/sales/', views.sales_report, name='sales_report'),
    path('reports/inventory/', views.inventory_report, name='inventory_report'),
    path('reports/audit-trail/', views.audit_trail, name='audit_trail'),
    
    # User Management
    path('users/', views.user_list, name='user_list'),
    path('users/create/', views.user_create, name='user_create'),
    path('users/<int:pk>/edit/', views.user_edit, name='user_edit'),
    
    # Table Management
    path('tables/', views.table_list, name='table_list'),
    path('tables/create/', views.table_create, name='table_create'),
    
    # Device Management
    path('devices/', views.device_list, name='device_list'),
    path('devices/register/', views.device_register, name='device_register'),
    
    # Settings
    path('settings/', views.system_settings, name='system_settings'),
]