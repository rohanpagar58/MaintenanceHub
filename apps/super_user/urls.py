from django.urls import path
from . import views

app_name = 'super_user'

urlpatterns = [
    path('',                                              views.login_page,              name='login_page'),
    path('login/',                                        views.login_view,              name='login'),
    path('logout/',                                       views.logout_view,             name='logout'),
    path('register-apartment/',                           views.register_apartment_page, name='register_apartment_page'),
    path('register-apartment/save/',                      views.register_apartment_view, name='register_apartment'),
    path('register-apartment/list/',                      views.get_apartments_list_view, name='get_apartments_list'),
    path('register-apartment/check-duplicate/',           views.check_duplicate_view,    name='check_duplicate'),
    path('register-apartment/delete/<str:society_id>/',   views.delete_apartment_view,   name='delete_apartment'),
]

