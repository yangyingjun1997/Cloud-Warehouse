from functools import wraps
from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect, resolve_url


def role_required(test_func, *, message='当前账号没有访问该页面的权限。'):
    """区分未登录和已登录但无业务权限的访问请求。"""

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                login_url = resolve_url(settings.LOGIN_URL)
                next_url = quote(request.get_full_path(), safe='')
                separator = '&' if '?' in login_url else '?'
                return redirect(f'{login_url}{separator}next={next_url}')
            if not test_func(request.user):
                messages.error(request, message)
                return redirect('home')
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator
