from venv import logger
from django.shortcuts import redirect
from django.contrib import messages
from django.utils.deprecation import MiddlewareMixin
from core.models import AuditLog, DeviceRegistration
import uuid


class DeviceBindingMiddleware(MiddlewareMixin):
    """
    Enforces device binding for fraud prevention.
    Each user must be logged in from their assigned device only.
    """
    
    EXEMPT_PATHS = ['/login/', '/logout/', '/admin/', '/static/', '/media/']
    
    def process_request(self, request):
        # Skip for exempt paths
        if any(request.path.startswith(path) for path in self.EXEMPT_PATHS):
            return None
        
        # Skip for unauthenticated users
        if not request.user.is_authenticated:
            return None
        
        # Admin users can use any device
        if request.user.role == 'admin':
            return None
        
        # Get or create device ID from session
        device_id = request.session.get('device_id')
        if not device_id:
            # Generate new device ID
            device_id = str(uuid.uuid4())
            request.session['device_id'] = device_id
        
        # Check if user has a device binding
        if request.user.device_id:
            # Verify this is the correct device
            if request.user.device_id != device_id:
                messages.error(request, 
                    'Access denied: You must login from your assigned device.')
                return redirect('core:login')
        else:
            # First login - bind user to this device
            request.user.device_id = device_id
            request.user.save(update_fields=['device_id'])
            
            # Log device registration (skip if fails - don't block login)
            try:
                AuditLog.objects.create(
                    user=request.user,
                    action_type='user_action',
                    description=f'Device binding created for {request.user.username}',
                    device_id=device_id,
                    ip_address=self.get_client_ip(request),
                    metadata={'device_id': device_id}
                )
            except Exception as e:
                logger.error(f"Failed to create audit log: {str(e)}")
        
        # Store device_id in request for easy access
        request.device_id = device_id
        
        return None
    
    @staticmethod
    def get_client_ip(request):
        """Extract client IP address"""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip


class AuditLogMiddleware(MiddlewareMixin):
    """
    Automatically logs all significant user actions for audit trail.
    Creates immutable records of all system interactions.
    """
    
    EXEMPT_PATHS = ['/static/', '/media/']
    
    # Actions that should always be logged
    LOGGED_METHODS = ['POST', 'PUT', 'PATCH', 'DELETE']
    
    def process_request(self, request):
        # Store request start time
        import time
        request._audit_start_time = time.time()
        return None
    
    def process_response(self, request, response):
        # Skip for exempt paths
        if any(request.path.startswith(path) for path in self.EXEMPT_PATHS):
            return response
        
        # Skip for non-authenticated users on GET requests
        if not request.user.is_authenticated and request.method == 'GET':
            return response
        
        # Only log significant actions
        if request.method not in self.LOGGED_METHODS and request.method != 'GET':
            return response
        
        # Skip logging for failed requests (4xx, 5xx) unless it's authentication
        if response.status_code >= 400 and 'login' not in request.path:
            return response
        
        # Determine action type based on path and method
        action_type = self._determine_action_type(request)
        
        if action_type and request.user.is_authenticated:
            device_id = getattr(request, 'device_id', 
                               request.session.get('device_id', ''))
            
            # Extract response time
            import time
            response_time = None
            if hasattr(request, '_audit_start_time'):
                response_time = time.time() - request._audit_start_time
            
            # Create audit log entry
            try:
                AuditLog.objects.create(
                    user=request.user,
                    action_type=action_type,
                    description=self._generate_description(request, action_type),
                    ip_address=DeviceBindingMiddleware.get_client_ip(request),
                    device_id=device_id,
                    metadata={
                        'path': request.path,
                        'method': request.method,
                        'status_code': response.status_code,
                        'response_time': response_time,
                    }
                )
            except Exception as e:
                # Don't break the request if audit logging fails
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Failed to create audit log: {str(e)}")
        
        return response
    
    def _determine_action_type(self, request):
        """Determine the action type based on the request"""
        path = request.path.lower()
        method = request.method
        
        if 'login' in path and method == 'POST':
            return 'login'
        elif 'logout' in path:
            return 'logout'
        elif 'order' in path:
            if method == 'POST':
                return 'order_create'
            elif method in ['PUT', 'PATCH']:
                return 'order_modify'
            elif method == 'DELETE':
                return 'order_cancel'
        elif 'payment' in path and method == 'POST':
            return 'payment_process'
        elif 'stock' in path and method in ['POST', 'PUT', 'PATCH']:
            return 'stock_adjust'
        elif 'product' in path and 'price' in path:
            return 'price_change'
        elif method in self.LOGGED_METHODS:
            return 'user_action'
        
        return None
    
    def _generate_description(self, request, action_type):
        """Generate human-readable description of the action"""
        descriptions = {
            'login': f'User {request.user.username} logged in',
            'logout': f'User {request.user.username} logged out',
            'order_create': f'Created new order from {request.path}',
            'order_modify': f'Modified order from {request.path}',
            'order_cancel': f'Cancelled order from {request.path}',
            'payment_process': f'Processed payment from {request.path}',
            'stock_adjust': f'Adjusted stock from {request.path}',
            'price_change': f'Changed product price from {request.path}',
            'user_action': f'User action: {request.method} {request.path}',
        }
        return descriptions.get(action_type, f'{request.method} {request.path}')


class RoleBasedAccessMiddleware(MiddlewareMixin):
    """
    Enforces role-based access control at the middleware level.
    Prevents unauthorized access to different system modules.
    """
    
    ROLE_ACCESS_RULES = {
        'waiter': ['/waiter/', '/api/orders/', '/api/tables/'],
        'cashier': ['/cashier/', '/api/payments/', '/api/orders/'],
        'kitchen': ['/kitchen/', '/api/orders/'],
        'admin': ['*'],  # Admin has access to everything
    }
    
    EXEMPT_PATHS = ['/login/', '/logout/', '/static/', '/media/']
    
    def process_request(self, request):
        # Skip for exempt paths
        if any(request.path.startswith(path) for path in self.EXEMPT_PATHS):
            return None
        
        # Skip for unauthenticated users (let auth middleware handle)
        if not request.user.is_authenticated:
            return None
        
        # Admin has access to everything
        if request.user.role == 'admin':
            return None
        
        # Check if user's role has access to this path
        user_role = request.user.role
        allowed_paths = self.ROLE_ACCESS_RULES.get(user_role, [])
        
        # Check if current path is allowed for this role
        path_allowed = any(
            request.path.startswith(allowed_path) 
            for allowed_path in allowed_paths
        )
        
        if not path_allowed:
            messages.error(request, 
                'Access denied: You do not have permission to access this area.')
            
            # Redirect to appropriate dashboard based on role
            role_redirects = {
                'waiter': '/waiter/',
                'cashier': '/cashier/',
                'kitchen': '/kitchen/',
            }
            return redirect(role_redirects.get(user_role, 'core:login'))
        
        return None